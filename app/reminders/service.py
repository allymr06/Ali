"""Persistent reminders with a claim, deliver, acknowledge lifecycle.

Reminders survive restarts in their own SQLite store. A poller (the Nova
shell's reminder watch, or the classic desktop's delivery loop) *claims*
the reminders that are due, hands each to a delivery callback, and then
*acknowledges* the claim when the callback returned or *releases* it with
the error when the callback raised. A released reminder comes back after
a bounded, growing delay; after ``MAX_ATTEMPTS`` failures it stays in the
active list marked undeliverable instead of vanishing. A claim nobody
settles — the process died mid-delivery — expires with its lease and is
handed out again.

What "delivered" means is decided by the caller of the callback: the Nova
shell counts a reminder delivered when the notification centre accepted
it, whatever the native toast did. The guarantee here is therefore
at-least-once delivery to the callback, exactly-once acknowledgment per
claim token; the callback keeps a repeated hand-out from becoming a
second entry by the reminder's id (see ``NovaBridge._deliver_reminder``).
"""

from __future__ import annotations

import asyncio
import sqlite3
import subprocess
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

from app.core.models import (
    RiskLevel,
    ToolDefinition,
    ToolExecutionStatus,
    ToolResult,
)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS reminders (
    reminder_id TEXT PRIMARY KEY,
    text TEXT NOT NULL,
    due_at TEXT NOT NULL,
    created_at TEXT NOT NULL,
    delivered INTEGER NOT NULL DEFAULT 0,
    cancelled INTEGER NOT NULL DEFAULT 0
)
"""

# Columns the lifecycle added after the first release; ``ALTER TABLE`` is
# applied only for the ones a database lacks, so opening an old store
# migrates it in place and opening it again is a no-op.
_LIFECYCLE_COLUMNS: tuple[tuple[str, str], ...] = (
    ("attempts", "INTEGER NOT NULL DEFAULT 0"),
    ("claim_token", "TEXT"),
    ("claimed_at", "TEXT"),
    ("next_attempt_at", "TEXT"),
    ("delivered_at", "TEXT"),
    ("last_error", "TEXT"),
)

LEASE_SECONDS = 120.0
MAX_ATTEMPTS = 5
RETRY_BASE_SECONDS = 30.0
RETRY_MAX_SECONDS = 15 * 60.0


def retry_delay_seconds(attempt: int) -> float:
    """Wait before the next try after ``attempt`` failures: 30 s doubling,
    capped at fifteen minutes."""
    exponent = max(0, int(attempt) - 1)
    return float(min(RETRY_BASE_SECONDS * (2**exponent), RETRY_MAX_SECONDS))


def show_windows_toast(title: str, body: str) -> bool:
    """Fire a native toast through WinRT; best effort, never raises."""
    if sys.platform != "win32":
        return False
    safe_title = title.replace("<", "").replace(">", "")[:60]
    safe_body = body.replace("<", "").replace(">", "")[:180]
    script = (
        "$null=[Windows.UI.Notifications.ToastNotificationManager,"
        "Windows.UI.Notifications,ContentType=WindowsRuntime];"
        "$null=[Windows.Data.Xml.Dom.XmlDocument,Windows.Data.Xml.Dom"
        ",ContentType=WindowsRuntime];"
        "$xml=New-Object Windows.Data.Xml.Dom.XmlDocument;"
        "$xml.LoadXml('<toast><visual><binding template="
        f"\"ToastGeneric\"><text>{safe_title}</text>"
        f"<text>{safe_body}</text></binding></visual></toast>');"
        "$toast=New-Object Windows.UI.Notifications.ToastNotification "
        "$xml;"
        "[Windows.UI.Notifications.ToastNotificationManager]::"
        "CreateToastNotifier("
        "'Microsoft.Windows.PowerShell').Show($toast)"
    )
    try:
        completed = subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-NonInteractive",
                "-Command",
                script,
            ],
            capture_output=True,
            timeout=15,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        return completed.returncode == 0
    except Exception:
        return False


def _describe_error(error: BaseException | str | None) -> str:
    if error is None:
        return ""
    if isinstance(error, BaseException):
        text = f"{type(error).__name__}: {error}"
    else:
        text = str(error)
    return " ".join(text.split())[:200]


class ReminderService:
    POLL_SECONDS = 10.0

    def __init__(
        self,
        database_path: str | Path,
        *,
        clock: Callable[[], datetime] | None = None,
        lease_seconds: float = LEASE_SECONDS,
        max_attempts: int = MAX_ATTEMPTS,
    ) -> None:
        self._path = Path(database_path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._lease = max(1.0, float(lease_seconds))
        self._max_attempts = max(1, int(max_attempts))
        with self._connect() as connection:
            connection.execute(_SCHEMA)
            self._migrate(connection)

    @property
    def max_attempts(self) -> int:
        return self._max_attempts

    @property
    def lease_seconds(self) -> float:
        return self._lease

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self._path, timeout=5.0)
        connection.row_factory = sqlite3.Row
        return connection

    @staticmethod
    def _migrate(connection: sqlite3.Connection) -> list[str]:
        """Add the lifecycle columns an older database lacks; repeatable."""
        # Column name is the second field of PRAGMA table_info, whatever the
        # connection's row factory.
        present = {
            row[1] for row in connection.execute("PRAGMA table_info(reminders)").fetchall()
        }
        added: list[str] = []
        for column, definition in _LIFECYCLE_COLUMNS:
            if column in present:
                continue
            connection.execute(f"ALTER TABLE reminders ADD COLUMN {column} {definition}")
            added.append(column)
        return added

    def _now(self) -> datetime:
        moment = self._clock()
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=timezone.utc)
        return moment.astimezone(timezone.utc)

    # ------------------------------------------------------------------

    def _parse_due(
        self, minutes: int, at: str
    ) -> tuple[datetime | None, str | None]:
        now = self._now()
        clock = at.strip()
        if clock:
            try:
                hour, _, minute = clock.partition(":")
                local_now = now.astimezone()
                due_local = local_now.replace(
                    hour=int(hour),
                    minute=int(minute or 0),
                    second=0,
                    microsecond=0,
                )
                if due_local <= local_now:
                    due_local += timedelta(days=1)
                return due_local.astimezone(timezone.utc), None
            except (ValueError, TypeError):
                return None, "Saat biçimi SS:DD olmalı, örn. 18:30."
        if minutes and int(minutes) > 0:
            bounded = min(int(minutes), 60 * 24 * 30)
            return now + timedelta(minutes=bounded), None
        return None, (
            "Süre ('minutes') veya saat ('at', SS:DD) vermelisin."
        )

    def create(
        self, text: str, *, minutes: int = 0, at: str = ""
    ) -> ToolResult:
        body = text.strip()
        if not body:
            return ToolResult(
                ToolExecutionStatus.FAILED,
                "create_reminder",
                message="Hatırlatıcı metni boş olamaz.",
                error="empty_text",
            )
        due, problem = self._parse_due(minutes, at)
        if due is None:
            return ToolResult(
                ToolExecutionStatus.FAILED,
                "create_reminder",
                message=problem or "Zaman anlaşılamadı.",
                error="invalid_time",
            )
        reminder_id = uuid.uuid4().hex[:10]
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO reminders (reminder_id, text, due_at, "
                "created_at) VALUES (?, ?, ?, ?)",
                (
                    reminder_id,
                    body,
                    due.isoformat(),
                    self._now().isoformat(),
                ),
            )
        local = due.astimezone().strftime("%d.%m %H:%M")
        stored = self._get(reminder_id) is not None
        return ToolResult(
            ToolExecutionStatus.SUCCESS,
            "create_reminder",
            message=f"Hatırlatıcı kuruldu: {local} — {body}",
            data={"reminder_id": reminder_id, "due_local": local},
            verified=stored,
        )

    def _get(self, reminder_id: str) -> sqlite3.Row | None:
        with self._connect() as connection:
            return connection.execute(
                "SELECT * FROM reminders WHERE reminder_id = ?",
                (reminder_id,),
            ).fetchone()

    def get(self, reminder_id: str) -> dict[str, Any] | None:
        """One reminder with its lifecycle fields, for inspection."""
        row = self._get(str(reminder_id).strip())
        return self._row_to_dict(row) if row is not None else None

    def _status(self, row: sqlite3.Row) -> str:
        if row["delivered"]:
            return "teslim edildi"
        if row["cancelled"]:
            return "iptal edildi"
        attempts = int(row["attempts"] or 0)
        if attempts >= self._max_attempts:
            return f"teslim edilemedi ({attempts} deneme)"
        if row["claim_token"]:
            return "teslim ediliyor"
        if attempts:
            return f"yeniden denenecek ({attempts}/{self._max_attempts})"
        return "bekliyor"

    def _row_to_dict(self, row: sqlite3.Row) -> dict[str, Any]:
        return {
            "reminder_id": row["reminder_id"],
            "text": row["text"],
            "due_at": row["due_at"],
            "due_local": datetime.fromisoformat(row["due_at"])
            .astimezone()
            .strftime("%d.%m %H:%M"),
            "delivered": bool(row["delivered"]),
            "cancelled": bool(row["cancelled"]),
            "attempts": int(row["attempts"] or 0),
            "claimed": bool(row["claim_token"]),
            "next_attempt_at": row["next_attempt_at"],
            "delivered_at": row["delivered_at"],
            "last_error": row["last_error"] or "",
            "status": self._status(row),
        }

    def list_active(self) -> ToolResult:
        """Reminders not yet delivered and not cancelled — those waiting,
        those being retried and those given up on, each with its status."""
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM reminders WHERE delivered = 0 AND "
                "cancelled = 0 ORDER BY due_at LIMIT 25"
            ).fetchall()
        items = [
            {
                "reminder_id": row["reminder_id"],
                "text": row["text"],
                "due_local": datetime.fromisoformat(row["due_at"])
                .astimezone()
                .strftime("%d.%m %H:%M"),
                "status": self._status(row),
                "attempts": int(row["attempts"] or 0),
            }
            for row in rows
        ]
        return ToolResult(
            ToolExecutionStatus.SUCCESS,
            "list_reminders",
            message=(
                f"{len(items)} aktif hatırlatıcı var."
                if items
                else "Aktif hatırlatıcı yok."
            ),
            data={"reminders": items},
            verified=True,
        )

    def list_undeliverable(self) -> list[dict[str, Any]]:
        """Reminders every attempt failed on; kept for the user to see."""
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM reminders WHERE delivered = 0 AND cancelled = 0 "
                "AND attempts >= ? ORDER BY due_at LIMIT 25",
                (self._max_attempts,),
            ).fetchall()
        return [self._row_to_dict(row) for row in rows]

    def cancel(self, reminder_id: str) -> ToolResult:
        """Cancel a reminder that has not been delivered — waiting, claimed
        or between retries alike. A delivery already in flight cannot be
        recalled, but its acknowledgment then fails and nothing is retried."""
        with self._connect() as connection:
            changed = connection.execute(
                "UPDATE reminders SET cancelled = 1, claim_token = NULL, "
                "claimed_at = NULL, next_attempt_at = NULL WHERE "
                "reminder_id = ? AND delivered = 0 AND cancelled = 0",
                (reminder_id.strip(),),
            ).rowcount
        if not changed:
            return ToolResult(
                ToolExecutionStatus.FAILED,
                "cancel_reminder",
                message="Bu kimlikte aktif bir hatırlatıcı yok.",
                error="not_found",
            )
        return ToolResult(
            ToolExecutionStatus.SUCCESS,
            "cancel_reminder",
            message="Hatırlatıcı iptal edildi.",
            verified=True,
        )

    # ------------------------------------------------------------------
    # the delivery lifecycle
    # ------------------------------------------------------------------

    def claim_due(self, *, now: datetime | None = None) -> list[dict[str, Any]]:
        """Lease the reminders that are due and return them.

        Each claim carries a ``claim_token``; the poller settles it with
        :meth:`acknowledge` or :meth:`release`. The whole claim runs in one
        immediate transaction, so two pollers on the same store never take
        the same reminder; a claim older than the lease is taken again.
        """
        moment = (now or self._now()).astimezone(timezone.utc)
        stamp = moment.isoformat()
        expired = (moment - timedelta(seconds=self._lease)).isoformat()
        claimed: list[dict[str, Any]] = []
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            rows = connection.execute(
                "SELECT reminder_id, text, attempts FROM reminders WHERE "
                "delivered = 0 AND cancelled = 0 AND due_at <= ? AND attempts < ? "
                "AND (next_attempt_at IS NULL OR next_attempt_at <= ?) "
                "AND (claim_token IS NULL OR claimed_at IS NULL OR claimed_at <= ?) "
                "ORDER BY due_at",
                (stamp, self._max_attempts, stamp, expired),
            ).fetchall()
            for row in rows:
                token = uuid.uuid4().hex
                moved = connection.execute(
                    "UPDATE reminders SET claim_token = ?, claimed_at = ?, "
                    "attempts = attempts + 1 WHERE reminder_id = ? AND "
                    "delivered = 0 AND cancelled = 0 AND "
                    "(claim_token IS NULL OR claimed_at IS NULL OR claimed_at <= ?)",
                    (token, stamp, row["reminder_id"], expired),
                ).rowcount
                if moved:
                    claimed.append(
                        {
                            "reminder_id": row["reminder_id"],
                            "text": row["text"],
                            "claim_token": token,
                            "attempt": int(row["attempts"] or 0) + 1,
                        }
                    )
        return claimed

    def acknowledge(
        self, reminder_id: str, claim_token: str, *, now: datetime | None = None
    ) -> bool:
        """Mark a claimed reminder delivered. False when the token no longer
        holds the claim: the lease ran out and another poller took it, or
        the reminder was cancelled meanwhile."""
        moment = (now or self._now()).astimezone(timezone.utc)
        with self._connect() as connection:
            changed = connection.execute(
                "UPDATE reminders SET delivered = 1, delivered_at = ?, "
                "claim_token = NULL, claimed_at = NULL, next_attempt_at = NULL, "
                "last_error = NULL WHERE reminder_id = ? AND claim_token = ? "
                "AND delivered = 0 AND cancelled = 0",
                (moment.isoformat(), str(reminder_id).strip(), str(claim_token)),
            ).rowcount
        return changed > 0

    def release(
        self,
        reminder_id: str,
        claim_token: str,
        *,
        error: BaseException | str | None = None,
        now: datetime | None = None,
    ) -> dict[str, Any] | None:
        """Give a failed claim back. The reminder returns after a growing,
        bounded delay; once every attempt is used it stays listed as
        undeliverable rather than being tried in a loop. None when the
        token no longer holds the claim."""
        moment = (now or self._now()).astimezone(timezone.utc)
        with self._connect() as connection:
            row = connection.execute(
                "SELECT attempts FROM reminders WHERE reminder_id = ? AND "
                "claim_token = ? AND delivered = 0 AND cancelled = 0",
                (str(reminder_id).strip(), str(claim_token)),
            ).fetchone()
            if row is None:
                return None
            attempts = int(row["attempts"] or 0)
            exhausted = attempts >= self._max_attempts
            next_attempt = (
                None
                if exhausted
                else (moment + timedelta(seconds=retry_delay_seconds(attempts))).isoformat()
            )
            connection.execute(
                "UPDATE reminders SET claim_token = NULL, claimed_at = NULL, "
                "next_attempt_at = ?, last_error = ? WHERE reminder_id = ? AND "
                "claim_token = ?",
                (
                    next_attempt,
                    _describe_error(error),
                    str(reminder_id).strip(),
                    str(claim_token),
                ),
            )
        return {
            "reminder_id": str(reminder_id).strip(),
            "attempts": attempts,
            "next_attempt_at": next_attempt,
            "exhausted": exhausted,
        }

    def deliver_due(self, deliver: Callable[[dict[str, Any]], Any]) -> int:
        """Claim, deliver and settle everything due; returns the number
        acknowledged. A raising callback releases its claim for a retry."""
        delivered = 0
        for reminder in self.claim_due():
            token = str(reminder["claim_token"])
            try:
                deliver(reminder)
            except Exception as exc:
                self.release(reminder["reminder_id"], token, error=exc)
                continue
            if self.acknowledge(reminder["reminder_id"], token):
                delivered += 1
        return delivered

    async def run_delivery_loop(
        self,
        deliver: Callable[[dict[str, Any]], Any],
    ) -> None:
        """Poll until cancelled; each due reminder is delivered and, on a
        failure, retried after a bounded delay."""
        while True:
            try:
                self.deliver_due(deliver)
            except Exception:
                pass
            await asyncio.sleep(self.POLL_SECONDS)

    # ------------------------------------------------------------------

    def register_tools(self, executor: Any) -> None:
        def define(
            name: str,
            description: str,
            *,
            risk: RiskLevel = RiskLevel.READ_ONLY,
        ) -> ToolDefinition:
            return ToolDefinition(
                name=name,
                description=description,
                risk_level=risk,
                version="1.0.0",
                capabilities=frozenset({"reminders", "schedule"}),
                tags=frozenset({"integration", "reminders"}),
                timeout_seconds=10.0,
                metadata={"verification_strategy": "store_readback"},
            )

        def create_reminder(
            text: str, minutes: int = 0, at: str = ""
        ) -> ToolResult:
            return self.create(text, minutes=minutes, at=at)

        def list_reminders() -> ToolResult:
            return self.list_active()

        def cancel_reminder(reminder_id: str) -> ToolResult:
            return self.cancel(reminder_id)

        executor.register(
            define(
                "create_reminder",
                "Hatırlatıcı kur: dakika sonra ('minutes') veya "
                "belirli saatte ('at', SS:DD).",
                risk=RiskLevel.LOW,
            ),
            create_reminder,
            source="integration:reminders",
        )
        executor.register(
            define(
                "list_reminders",
                "Aktif hatırlatıcıları listele.",
            ),
            list_reminders,
            source="integration:reminders",
        )
        executor.register(
            define(
                "cancel_reminder",
                "Kimliği verilen hatırlatıcıyı iptal et.",
                risk=RiskLevel.LOW,
            ),
            cancel_reminder,
            source="integration:reminders",
        )
