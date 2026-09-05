"""Scheduled routines: a prompt JARVIS runs on its own at set times.

A routine is a named prompt with a schedule, either daily at a local
clock time ("her gün 09:00") or every N minutes. Routines live in their
own SQLite store, survive restarts, and are claimed atomically when due:
the claim moves ``next_run_at`` to the following occurrence before the
routine is handed out, so a run happens once even if the desktop restarts
mid-run or two watches overlap. The desktop runs the prompt through the
same core, permission engine and approvals as a typed command and
delivers the outcome to the notification centre; nothing here executes
anything itself.
"""

from __future__ import annotations

import sqlite3
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from app.core.models import (
    RiskLevel,
    ToolDefinition,
    ToolExecutionStatus,
    ToolResult,
)

MAX_ROUTINES = 20
MAX_NAME_LENGTH = 60
MAX_PROMPT_LENGTH = 500
MIN_INTERVAL_MINUTES = 5
MAX_INTERVAL_MINUTES = 60 * 24 * 7
DEFER_MAX_SECONDS = 3600.0

_SCHEMA = """
CREATE TABLE IF NOT EXISTS routines (
    routine_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    prompt TEXT NOT NULL,
    schedule_kind TEXT NOT NULL,
    schedule_value TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1,
    conversation_id TEXT,
    created_at TEXT NOT NULL,
    next_run_at TEXT NOT NULL,
    last_run_at TEXT,
    last_outcome TEXT,
    last_summary TEXT,
    run_count INTEGER NOT NULL DEFAULT 0
)
"""


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _local(value: str | None) -> str | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value).astimezone().strftime("%d.%m %H:%M")
    except ValueError:
        return None


def parse_clock(value: str) -> tuple[int, int] | None:
    """``"9:05"`` / ``"09.05"`` -> ``(9, 5)``; ``None`` when not a clock time."""
    text = value.strip().replace(".", ":")
    hour, sep, minute = text.partition(":")
    if not sep or not hour.isdigit() or not minute.isdigit():
        return None
    hours, minutes = int(hour), int(minute)
    if not (0 <= hours <= 23 and 0 <= minutes <= 59):
        return None
    return hours, minutes


def next_daily_run(hours: int, minutes: int, *, now: datetime | None = None) -> datetime:
    """The next local occurrence of HH:MM, as an aware UTC datetime."""
    local_now = (now or _utc_now()).astimezone()
    candidate = local_now.replace(hour=hours, minute=minutes, second=0, microsecond=0)
    if candidate <= local_now:
        candidate += timedelta(days=1)
    return candidate.astimezone(timezone.utc)


def describe_schedule(kind: str, value: str) -> str:
    """User-facing Turkish description of a schedule."""
    if kind == "daily":
        return f"her gün {value}"
    if kind == "interval":
        minutes = int(value)
        if minutes % 60 == 0:
            hours = minutes // 60
            return "her saat" if hours == 1 else f"her {hours} saatte"
        return f"her {minutes} dakikada"
    return kind


class RoutineService:
    """Persistent, bounded routine store with atomic due claims."""

    def __init__(self, database_path: str | Path) -> None:
        self._path = Path(database_path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.execute(_SCHEMA)

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self._path)
        connection.row_factory = sqlite3.Row
        return connection

    # ------------------------------------------------------------------
    # schedules
    # ------------------------------------------------------------------
    @staticmethod
    def _next_run(kind: str, value: str, *, now: datetime | None = None) -> datetime:
        if kind == "daily":
            clock = parse_clock(value)
            if clock is None:
                raise ValueError("Invalid daily schedule.")
            return next_daily_run(*clock, now=now)
        if kind == "interval":
            return (now or _utc_now()) + timedelta(minutes=int(value))
        raise ValueError(f"Unknown schedule kind: {kind}")

    @staticmethod
    def _parse_schedule(at: str, every_minutes: int) -> tuple[str, str] | str:
        clock_text = (at or "").strip()
        if clock_text:
            clock = parse_clock(clock_text)
            if clock is None:
                return "Saat biçimi SS:DD olmalı, örn. 09:00."
            return "daily", f"{clock[0]:02d}:{clock[1]:02d}"
        try:
            minutes = int(every_minutes)
        except (TypeError, ValueError):
            minutes = 0
        if minutes <= 0:
            return "Saat ('at', SS:DD) veya aralık ('every_minutes') vermelisin."
        if minutes < MIN_INTERVAL_MINUTES:
            return f"Aralık en az {MIN_INTERVAL_MINUTES} dakika olmalı."
        if minutes > MAX_INTERVAL_MINUTES:
            return "Aralık en fazla bir hafta olabilir."
        return "interval", str(minutes)

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------
    def create(
        self, name: str, prompt: str, *, at: str = "", every_minutes: int = 0
    ) -> ToolResult:
        clean_name = " ".join(str(name or "").split())[:MAX_NAME_LENGTH]
        clean_prompt = " ".join(str(prompt or "").split())
        if not clean_name:
            return self._failed("create_routine", "Rutin adı boş olamaz.", "empty_name")
        if not clean_prompt:
            return self._failed("create_routine", "Rutin komutu boş olamaz.", "empty_prompt")
        if len(clean_prompt) > MAX_PROMPT_LENGTH:
            return self._failed(
                "create_routine",
                f"Rutin komutu en fazla {MAX_PROMPT_LENGTH} karakter olabilir.",
                "prompt_too_long",
            )
        schedule = self._parse_schedule(at, every_minutes)
        if isinstance(schedule, str):
            return self._failed("create_routine", schedule, "invalid_schedule")
        kind, value = schedule
        with self._connect() as connection:
            count = connection.execute(
                "SELECT COUNT(*) FROM routines WHERE enabled = 1"
            ).fetchone()[0]
            if count >= MAX_ROUTINES:
                return self._failed(
                    "create_routine",
                    f"En fazla {MAX_ROUTINES} rutin tanımlanabilir.",
                    "too_many_routines",
                )
            routine_id = uuid.uuid4().hex[:10]
            next_run = self._next_run(kind, value)
            connection.execute(
                "INSERT INTO routines (routine_id, name, prompt, schedule_kind, "
                "schedule_value, created_at, next_run_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    routine_id,
                    clean_name,
                    clean_prompt,
                    kind,
                    value,
                    _utc_now().isoformat(),
                    next_run.isoformat(),
                ),
            )
        stored = self.get(routine_id)
        described = describe_schedule(kind, value)
        return ToolResult(
            ToolExecutionStatus.SUCCESS,
            "create_routine",
            message=(
                f"Rutin kuruldu: {clean_name} ({described}); ilk çalışma "
                f"{_local(next_run.isoformat())}."
            ),
            data={
                "routine_id": routine_id,
                "schedule": described,
                "next_run_local": _local(next_run.isoformat()),
            },
            verified=stored is not None,
        )

    def get(self, routine_id: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM routines WHERE routine_id = ?", (str(routine_id).strip(),)
            ).fetchone()
        return self._row_to_dict(row) if row is not None else None

    def list_active(self) -> ToolResult:
        items = self.list()
        return ToolResult(
            ToolExecutionStatus.SUCCESS,
            "list_routines",
            message=(f"{len(items)} rutin tanımlı." if items else "Tanımlı rutin yok."),
            data={"routines": items},
            verified=True,
        )

    def list(self) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM routines WHERE enabled = 1 ORDER BY next_run_at LIMIT ?",
                (MAX_ROUTINES,),
            ).fetchall()
        return [self._row_to_dict(row) for row in rows]

    def delete(self, routine_id: str) -> ToolResult:
        with self._connect() as connection:
            changed = connection.execute(
                "DELETE FROM routines WHERE routine_id = ?", (str(routine_id).strip(),)
            ).rowcount
        if not changed:
            return self._failed("delete_routine", "Bu kimlikte bir rutin yok.", "not_found")
        return ToolResult(
            ToolExecutionStatus.SUCCESS,
            "delete_routine",
            message="Rutin silindi.",
            verified=True,
        )

    # ------------------------------------------------------------------
    # running
    # ------------------------------------------------------------------
    def claim_due(self, *, now: datetime | None = None) -> list[dict[str, Any]]:
        """Return the routines due now, each already moved to its next slot."""
        moment = now or _utc_now()
        claimed: list[dict[str, Any]] = []
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM routines WHERE enabled = 1 AND next_run_at <= ? "
                "ORDER BY next_run_at",
                (moment.isoformat(),),
            ).fetchall()
            for row in rows:
                following = self._next_run(
                    row["schedule_kind"], row["schedule_value"], now=moment
                )
                moved = connection.execute(
                    "UPDATE routines SET next_run_at = ? WHERE routine_id = ? "
                    "AND next_run_at = ?",
                    (following.isoformat(), row["routine_id"], row["next_run_at"]),
                ).rowcount
                if moved:
                    claimed.append(self._row_to_dict(row))
        return claimed

    def defer(self, routine_id: str, seconds: float) -> bool:
        """Push a claimed routine's next run closer (the desktop was busy)."""
        pause = max(1.0, min(float(seconds), DEFER_MAX_SECONDS))
        soon = _utc_now() + timedelta(seconds=pause)
        with self._connect() as connection:
            row = connection.execute(
                "SELECT next_run_at FROM routines WHERE routine_id = ?",
                (str(routine_id).strip(),),
            ).fetchone()
            if row is None:
                return False
            current = datetime.fromisoformat(row["next_run_at"])
            if current <= soon:
                return True
            connection.execute(
                "UPDATE routines SET next_run_at = ? WHERE routine_id = ?",
                (soon.isoformat(), str(routine_id).strip()),
            )
        return True

    def record_run(
        self,
        routine_id: str,
        *,
        outcome: str,
        summary: str,
        conversation_id: str | None = None,
    ) -> bool:
        with self._connect() as connection:
            changed = connection.execute(
                "UPDATE routines SET last_run_at = ?, last_outcome = ?, last_summary = ?, "
                "run_count = run_count + 1, conversation_id = COALESCE(?, conversation_id) "
                "WHERE routine_id = ?",
                (
                    _utc_now().isoformat(),
                    str(outcome)[:40],
                    " ".join(str(summary or "").split())[:240],
                    conversation_id,
                    str(routine_id).strip(),
                ),
            ).rowcount
        return bool(changed)

    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------
    @staticmethod
    def _failed(tool: str, message: str, error: str) -> ToolResult:
        return ToolResult(ToolExecutionStatus.FAILED, tool, message=message, error=error)

    @staticmethod
    def _row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "routine_id": row["routine_id"],
            "name": row["name"],
            "prompt": row["prompt"],
            "schedule": describe_schedule(row["schedule_kind"], row["schedule_value"]),
            "schedule_kind": row["schedule_kind"],
            "schedule_value": row["schedule_value"],
            "conversation_id": row["conversation_id"],
            "next_run_at": row["next_run_at"],
            "next_run_local": _local(row["next_run_at"]),
            "last_run_at": row["last_run_at"],
            "last_run_local": _local(row["last_run_at"]),
            "last_outcome": row["last_outcome"],
            "last_summary": row["last_summary"],
            "run_count": int(row["run_count"] or 0),
        }

    # ------------------------------------------------------------------
    # tools
    # ------------------------------------------------------------------
    def register_tools(self, executor: Any) -> None:
        def define(
            name: str, description: str, *, risk: RiskLevel = RiskLevel.READ_ONLY
        ) -> ToolDefinition:
            return ToolDefinition(
                name=name,
                description=description,
                risk_level=risk,
                version="1.0.0",
                capabilities=frozenset({"routines", "schedule"}),
                tags=frozenset({"integration", "routines"}),
                timeout_seconds=10.0,
                metadata={"verification_strategy": "store_readback"},
            )

        def create_routine(
            name: str, prompt: str, at: str = "", every_minutes: int = 0
        ) -> ToolResult:
            return self.create(name, prompt, at=at, every_minutes=every_minutes)

        def list_routines() -> ToolResult:
            return self.list_active()

        def delete_routine(routine_id: str) -> ToolResult:
            return self.delete(routine_id)

        executor.register(
            define(
                "create_routine",
                "Rutin kur: JARVIS verilen komutu ('prompt') her gün belirli "
                "saatte ('at', SS:DD) veya her N dakikada ('every_minutes') "
                "kendiliğinden çalıştırır ve sonucu bildirir.",
                risk=RiskLevel.LOW,
            ),
            create_routine,
            source="integration:routines",
        )
        executor.register(
            define("list_routines", "Tanımlı rutinleri listele."),
            list_routines,
            source="integration:routines",
        )
        executor.register(
            define(
                "delete_routine",
                "Kimliği verilen rutini sil.",
                risk=RiskLevel.LOW,
            ),
            delete_routine,
            source="integration:routines",
        )


__all__ = [
    "MAX_ROUTINES",
    "RoutineService",
    "describe_schedule",
    "next_daily_run",
    "parse_clock",
]
