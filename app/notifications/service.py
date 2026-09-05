"""In-session notification centre and the reminder watch that feeds it.

The centre is a bounded, thread-safe list of things that deserve the
user's attention: a reminder that came due, an approval that waited while
the window was hidden, a reply or a research report that arrived while
nobody was looking, a warning sealed in the diagnostics ledger, a screen
observation. It stores plain data only, keeps the newest entries, and
tells subscribers about every change so a shell can show a badge, a toast
or a native notification. It is deliberately session-scoped: nothing here
is persisted, nothing here acts on the user's behalf, and every string it
receives is bounded before it is kept.

The reminder watch polls a :class:`~app.reminders.service.ReminderService`
on its own daemon thread; the service claims due reminders atomically, so
each one is handed to the delivery callback exactly once even if the shell
restarts the watch.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

NOTIFICATION_KINDS: frozenset[str] = frozenset(
    {"reminder", "approval", "reply", "task", "diagnostic", "observation", "system"}
)
NOTIFICATION_SEVERITIES: frozenset[str] = frozenset({"info", "warning", "error"})
MAX_TITLE_LENGTH = 120
MAX_BODY_LENGTH = 600
MAX_DATA_FIELDS = 12
MAX_DATA_VALUE_LENGTH = 200
DEFAULT_MAX_ENTRIES = 200
DEFAULT_DEDUPE_SECONDS = 60.0
DEFAULT_REMINDER_POLL_SECONDS = 10.0

Listener = Callable[["Notification", int], None]


def _now() -> datetime:
    return datetime.now(timezone.utc).astimezone()


def _bounded(value: Any, limit: int) -> str:
    text = " ".join(str(value or "").split())
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def _bounded_data(data: Any) -> dict[str, Any]:
    """Keep a small, JSON-friendly copy of the caller's context."""
    if not isinstance(data, dict):
        return {}
    kept: dict[str, Any] = {}
    for key, value in data.items():
        if len(kept) >= MAX_DATA_FIELDS:
            break
        name = _bounded(key, 40)
        if not name:
            continue
        if isinstance(value, bool) or value is None:
            kept[name] = value
        elif isinstance(value, (int, float)):
            kept[name] = value
        else:
            kept[name] = _bounded(value, MAX_DATA_VALUE_LENGTH)
    return kept


@dataclass
class Notification:
    """One entry in the centre; mutable only through the centre."""

    notification_id: str
    kind: str
    title: str
    body: str
    severity: str
    created_at: datetime
    updated_at: datetime
    target: str | None = None
    reference: str | None = None
    data: dict[str, Any] = field(default_factory=dict)
    count: int = 1
    read: bool = False
    dedupe_key: str | None = field(default=None, repr=False)

    def to_dict(self) -> dict[str, Any]:
        return {
            "notification_id": self.notification_id,
            "kind": self.kind,
            "title": self.title,
            "body": self.body,
            "severity": self.severity,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
            "target": self.target,
            "reference": self.reference,
            "data": dict(self.data),
            "count": self.count,
            "read": self.read,
        }


_STORE_SCHEMA = """
CREATE TABLE IF NOT EXISTS notifications (
    notification_id TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    title TEXT NOT NULL,
    body TEXT NOT NULL,
    severity TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    target TEXT,
    reference TEXT,
    data_json TEXT NOT NULL,
    count INTEGER NOT NULL DEFAULT 1,
    read INTEGER NOT NULL DEFAULT 0,
    dedupe_key TEXT
)
"""


class NotificationStore:
    """SQLite persistence for the centre: write-through, newest kept.

    Every method swallows storage errors after reporting False: a full
    disk must never stop a reminder from being shown.
    """

    def __init__(self, database_path: str | Path) -> None:
        self._path = Path(database_path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.execute(_STORE_SCHEMA)

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self._path)
        connection.row_factory = sqlite3.Row
        return connection

    def load(self, limit: int) -> list[Notification]:
        try:
            with self._connect() as connection:
                rows = connection.execute(
                    # rowid breaks ties: Windows clocks can stamp several
                    # entries with the same microsecond.
                    "SELECT * FROM notifications ORDER BY created_at DESC, rowid DESC LIMIT ?",
                    (int(limit),),
                ).fetchall()
        except (sqlite3.Error, OSError):
            return []
        loaded: list[Notification] = []
        for row in rows:
            try:
                data = json.loads(row["data_json"] or "{}")
                loaded.append(
                    Notification(
                        notification_id=row["notification_id"],
                        kind=row["kind"],
                        title=row["title"],
                        body=row["body"],
                        severity=row["severity"],
                        created_at=datetime.fromisoformat(row["created_at"]),
                        updated_at=datetime.fromisoformat(row["updated_at"]),
                        target=row["target"],
                        reference=row["reference"],
                        data=data if isinstance(data, dict) else {},
                        count=int(row["count"] or 1),
                        read=bool(row["read"]),
                        dedupe_key=row["dedupe_key"],
                    )
                )
            except (KeyError, ValueError, TypeError):
                continue
        return loaded

    def save(self, entry: Notification) -> bool:
        try:
            with self._connect() as connection:
                # An upsert keeps the row's rowid, so a collapsed repeat
                # stays where it was instead of jumping to the top on reload.
                connection.execute(
                    "INSERT INTO notifications (notification_id, kind, title, "
                    "body, severity, created_at, updated_at, target, reference, data_json, "
                    "count, read, dedupe_key) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                    "ON CONFLICT(notification_id) DO UPDATE SET body = excluded.body, "
                    "severity = excluded.severity, updated_at = excluded.updated_at, "
                    "data_json = excluded.data_json, count = excluded.count, read = excluded.read",
                    (
                        entry.notification_id,
                        entry.kind,
                        entry.title,
                        entry.body,
                        entry.severity,
                        entry.created_at.isoformat(),
                        entry.updated_at.isoformat(),
                        entry.target,
                        entry.reference,
                        json.dumps(entry.data, ensure_ascii=False, default=str),
                        int(entry.count),
                        int(entry.read),
                        entry.dedupe_key,
                    ),
                )
        except (sqlite3.Error, OSError, TypeError, ValueError):
            return False
        return True

    def mark_read(self, notification_ids: list[str]) -> bool:
        if not notification_ids:
            return True
        try:
            with self._connect() as connection:
                connection.executemany(
                    "UPDATE notifications SET read = 1 WHERE notification_id = ?",
                    [(item,) for item in notification_ids],
                )
        except (sqlite3.Error, OSError):
            return False
        return True

    def delete(self, notification_id: str) -> bool:
        try:
            with self._connect() as connection:
                connection.execute(
                    "DELETE FROM notifications WHERE notification_id = ?", (notification_id,)
                )
        except (sqlite3.Error, OSError):
            return False
        return True

    def clear(self) -> bool:
        try:
            with self._connect() as connection:
                connection.execute("DELETE FROM notifications")
        except (sqlite3.Error, OSError):
            return False
        return True

    def prune(self, keep_ids: list[str]) -> bool:
        """Drop rows the bounded centre no longer holds."""
        try:
            with self._connect() as connection:
                if keep_ids:
                    placeholders = ",".join("?" for _ in keep_ids)
                    connection.execute(
                        f"DELETE FROM notifications WHERE notification_id NOT IN ({placeholders})",
                        list(keep_ids),
                    )
                else:
                    connection.execute("DELETE FROM notifications")
        except (sqlite3.Error, OSError):
            return False
        return True


class NotificationCenter:
    """Bounded, thread-safe, session-scoped attention list.

    ``max_entries`` newest entries are kept; older ones fall off the end.
    A ``dedupe_key`` collapses repeats: an unread entry with the same key
    published within ``dedupe_seconds`` is updated in place and its
    ``count`` grows, so a chatty warning cannot flood the list.
    """

    def __init__(
        self,
        *,
        max_entries: int = DEFAULT_MAX_ENTRIES,
        dedupe_seconds: float = DEFAULT_DEDUPE_SECONDS,
        clock: Callable[[], datetime] | None = None,
        store: NotificationStore | None = None,
    ) -> None:
        if int(max_entries) < 1:
            raise ValueError("max_entries must be at least 1.")
        if float(dedupe_seconds) < 0:
            raise ValueError("dedupe_seconds must not be negative.")
        self._max_entries = int(max_entries)
        self._dedupe_seconds = float(dedupe_seconds)
        self._clock = clock or _now
        self._items: list[Notification] = []  # newest first
        self._listeners: list[Listener] = []
        self._lock = threading.Lock()
        self._store = store
        if store is not None:
            self._items = store.load(self._max_entries)
            self._items.sort(key=lambda entry: entry.created_at, reverse=True)

    @property
    def persistent(self) -> bool:
        return self._store is not None

    # ------------------------------------------------------------------
    # publishing
    # ------------------------------------------------------------------
    def publish(
        self,
        kind: str,
        title: str,
        body: str = "",
        *,
        severity: str = "info",
        target: str | None = None,
        reference: str | None = None,
        data: dict[str, Any] | None = None,
        dedupe_key: str | None = None,
    ) -> Notification:
        if kind not in NOTIFICATION_KINDS:
            raise ValueError(f"Unknown notification kind: {kind!r}")
        if severity not in NOTIFICATION_SEVERITIES:
            raise ValueError(f"Unknown notification severity: {severity!r}")
        clean_title = _bounded(title, MAX_TITLE_LENGTH)
        if not clean_title:
            raise ValueError("A notification needs a title.")
        clean_body = _bounded(body, MAX_BODY_LENGTH)
        now = self._clock()
        with self._lock:
            entry = self._find_duplicate(dedupe_key, now)
            if entry is not None:
                entry.count += 1
                entry.body = clean_body or entry.body
                entry.updated_at = now
                entry.severity = severity
                entry.data = _bounded_data(data) or entry.data
            else:
                entry = Notification(
                    notification_id=uuid.uuid4().hex[:12],
                    kind=kind,
                    title=clean_title,
                    body=clean_body,
                    severity=severity,
                    created_at=now,
                    updated_at=now,
                    target=_bounded(target, 40) or None,
                    reference=_bounded(reference, 120) or None,
                    data=_bounded_data(data),
                    dedupe_key=_bounded(dedupe_key, 120) or None,
                )
                self._items.insert(0, entry)
                dropped = self._items[self._max_entries :]
                del self._items[self._max_entries :]
                if dropped and self._store is not None:
                    self._store.prune([item.notification_id for item in self._items])
            if self._store is not None:
                self._store.save(entry)
            unread = self._unread_locked()
            listeners = list(self._listeners)
        self._notify(listeners, entry, unread)
        return entry

    def _find_duplicate(self, dedupe_key: str | None, now: datetime) -> Notification | None:
        key = _bounded(dedupe_key, 120) or None
        if key is None or self._dedupe_seconds <= 0:
            return None
        for entry in self._items:
            if entry.dedupe_key != key or entry.read:
                continue
            age = (now - entry.updated_at).total_seconds()
            if 0 <= age <= self._dedupe_seconds:
                return entry
            return None
        return None

    @staticmethod
    def _notify(listeners: Iterable[Listener], entry: Notification, unread: int) -> None:
        for listener in listeners:
            try:
                listener(entry, unread)
            except Exception:
                # A broken observer must never take the publisher down.
                continue

    # ------------------------------------------------------------------
    # reading
    # ------------------------------------------------------------------
    def list(self, *, limit: int = 100, unread_only: bool = False) -> list[Notification]:
        bound = max(1, min(int(limit), self._max_entries))
        with self._lock:
            items = [e for e in self._items if not unread_only or not e.read]
            return list(items[:bound])

    def get(self, notification_id: str) -> Notification | None:
        wanted = str(notification_id or "")
        with self._lock:
            for entry in self._items:
                if entry.notification_id == wanted:
                    return entry
        return None

    def _unread_locked(self) -> int:
        return sum(1 for entry in self._items if not entry.read)

    def unread_count(self) -> int:
        with self._lock:
            return self._unread_locked()

    def summary(self) -> dict[str, int]:
        with self._lock:
            return {"total": len(self._items), "unread": self._unread_locked()}

    # ------------------------------------------------------------------
    # acknowledging
    # ------------------------------------------------------------------
    def mark_read(self, notification_ids: Iterable[str] | None = None) -> int:
        """Mark the given entries read (all of them when ``None``)."""
        wanted = None if notification_ids is None else {str(i) for i in notification_ids}
        changed = 0
        touched: list[str] = []
        with self._lock:
            for entry in self._items:
                if entry.read:
                    continue
                if wanted is None or entry.notification_id in wanted:
                    entry.read = True
                    changed += 1
                    touched.append(entry.notification_id)
            if touched and self._store is not None:
                self._store.mark_read(touched)
        return changed

    def dismiss(self, notification_id: str) -> bool:
        wanted = str(notification_id or "")
        with self._lock:
            for index, entry in enumerate(self._items):
                if entry.notification_id == wanted:
                    del self._items[index]
                    if self._store is not None:
                        self._store.delete(wanted)
                    return True
        return False

    def clear(self) -> int:
        with self._lock:
            count = len(self._items)
            self._items.clear()
            if self._store is not None:
                self._store.clear()
        return count

    # ------------------------------------------------------------------
    # observation
    # ------------------------------------------------------------------
    def subscribe(self, listener: Listener) -> Callable[[], None]:
        """Register ``listener(notification, unread_count)``; returns a detacher."""
        with self._lock:
            self._listeners.append(listener)

        def detach() -> None:
            with self._lock:
                try:
                    self._listeners.remove(listener)
                except ValueError:
                    pass

        return detach


class ReminderWatch:
    """Hand due reminders to ``deliver`` from a daemon thread.

    The first poll happens as soon as the watch starts, so reminders that
    came due while the desktop was closed fire right after boot. Errors in
    the store or in delivery are swallowed: a broken reminder must not
    stop the next one, and the thread must never die noisily.
    """

    def __init__(
        self,
        reminders: Any,
        deliver: Callable[[dict[str, str]], None],
        *,
        interval_seconds: float = DEFAULT_REMINDER_POLL_SECONDS,
    ) -> None:
        if float(interval_seconds) <= 0:
            raise ValueError("interval_seconds must be greater than 0.")
        self._reminders = reminders
        self._deliver = deliver
        self._interval = float(interval_seconds)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()

    @property
    def running(self) -> bool:
        thread = self._thread
        return thread is not None and thread.is_alive() and not self._stop.is_set()

    def poll_once(self) -> int:
        """Claim and deliver everything due right now; returns the count."""
        try:
            due = list(self._reminders.claim_due())
        except Exception:
            return 0
        delivered = 0
        for reminder in due:
            try:
                self._deliver(reminder)
                delivered += 1
            except Exception:
                continue
        return delivered

    def start(self) -> None:
        with self._lock:
            if self.running:
                return
            self._stop = threading.Event()
            self._thread = threading.Thread(
                target=self._run, name="jarvis-reminder-watch", daemon=True
            )
            self._thread.start()

    def stop(self, timeout: float = 2.0) -> None:
        with self._lock:
            thread = self._thread
            self._stop.set()
            self._thread = None
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout)

    def _run(self) -> None:
        stop = self._stop
        while not stop.is_set():
            self.poll_once()
            stop.wait(self._interval)


__all__ = [
    "DEFAULT_MAX_ENTRIES",
    "DEFAULT_REMINDER_POLL_SECONDS",
    "MAX_BODY_LENGTH",
    "MAX_TITLE_LENGTH",
    "NOTIFICATION_KINDS",
    "NOTIFICATION_SEVERITIES",
    "Notification",
    "NotificationCenter",
    "NotificationStore",
    "ReminderWatch",
]
