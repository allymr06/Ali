"""Persistent reminders with native Windows toast delivery.

Reminders survive restarts in their own SQLite store. A bounded
background loop (run on the desktop's async runner) marks due entries
delivered exactly once and hands them to a delivery callback; the
desktop turns that into a toast plus a conversation notice.
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


class ReminderService:
    POLL_SECONDS = 10.0

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

    @staticmethod
    def _parse_due(
        minutes: int, at: str
    ) -> tuple[datetime | None, str | None]:
        now = datetime.now(timezone.utc)
        clock = at.strip()
        if clock:
            try:
                hour, _, minute = clock.partition(":")
                local_now = datetime.now().astimezone()
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
                    datetime.now(timezone.utc).isoformat(),
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

    def list_active(self) -> ToolResult:
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

    def cancel(self, reminder_id: str) -> ToolResult:
        with self._connect() as connection:
            changed = connection.execute(
                "UPDATE reminders SET cancelled = 1 WHERE "
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

    def claim_due(self) -> list[dict[str, str]]:
        """Atomically mark due reminders delivered and return them."""
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT reminder_id, text FROM reminders WHERE "
                "delivered = 0 AND cancelled = 0 AND due_at <= ?",
                (now,),
            ).fetchall()
            if rows:
                connection.execute(
                    "UPDATE reminders SET delivered = 1 WHERE "
                    "delivered = 0 AND cancelled = 0 AND due_at <= ?",
                    (now,),
                )
        return [
            {"reminder_id": row["reminder_id"], "text": row["text"]}
            for row in rows
        ]

    async def run_delivery_loop(
        self,
        deliver: Callable[[dict[str, str]], None],
    ) -> None:
        """Poll until cancelled; each due reminder fires exactly once."""
        while True:
            for reminder in self.claim_due():
                try:
                    deliver(reminder)
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
