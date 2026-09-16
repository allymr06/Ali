"""Device enrollment and revocable sessions for the mobile companion.

Single owner, several phones. A pairing code is minted on the trusted PC
(desktop card or CLI), typed once on the phone, and exchanged for a
session token that lives only in an HttpOnly cookie. Codes and tokens are
stored as SHA-256 digests, never in clear; a code is single-use and
short-lived; a session can be revoked from the PC at any time and stops
working on the very next request.
"""

from __future__ import annotations

import hashlib
import secrets
import sqlite3
import threading
import time
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

# No 0/O or 1/I: the code is typed on a phone keyboard.
PAIRING_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
PAIRING_LENGTH = 8
DEFAULT_PAIRING_TTL_SECONDS = 600
DEFAULT_SESSION_DAYS = 30
TOKEN_BYTES = 32
LAST_SEEN_WRITE_INTERVAL_SECONDS = 60.0


class PairingError(ValueError):
    """A pairing attempt that must not succeed, with a user-facing reason."""


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def normalize_pairing_code(raw: str) -> str:
    """Uppercase, strip separators and lookalikes typed by hand."""
    cleaned = "".join(ch for ch in str(raw or "").upper() if ch.isalnum())
    return cleaned.replace("0", "O").replace("1", "I")[:PAIRING_LENGTH]


def format_pairing_code(code: str) -> str:
    return f"{code[:4]}-{code[4:]}"


@dataclass(frozen=True, slots=True)
class DeviceSession:
    session_id: str
    label: str
    created_at: datetime
    last_seen_at: datetime
    expires_at: datetime
    revoked_at: datetime | None
    conversation_id: str | None

    @property
    def active(self) -> bool:
        return self.revoked_at is None and self.expires_at > _utc_now()

    def to_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "label": self.label,
            "created_at": self.created_at.isoformat(),
            "last_seen_at": self.last_seen_at.isoformat(),
            "expires_at": self.expires_at.isoformat(),
            "revoked": self.revoked_at is not None,
            "active": self.active,
        }


class RateLimiter:
    """A sliding window per key, in memory; enough for a single owner."""

    def __init__(self, limit: int, window_seconds: float, *, clock: Callable[[], float] | None = None) -> None:
        self._limit = max(1, int(limit))
        self._window = float(window_seconds)
        self._clock = clock or time.monotonic
        self._hits: dict[str, deque[float]] = {}
        self._lock = threading.Lock()

    def allow(self, key: str) -> bool:
        now = self._clock()
        with self._lock:
            hits = self._hits.setdefault(key, deque())
            while hits and now - hits[0] > self._window:
                hits.popleft()
            if len(hits) >= self._limit:
                return False
            hits.append(now)
            return True

    def reset(self, key: str) -> None:
        with self._lock:
            self._hits.pop(key, None)


class MobileSessionStore:
    """SQLite-backed pairing codes and device sessions."""

    def __init__(
        self,
        path: str | Path,
        *,
        pairing_ttl_seconds: int = DEFAULT_PAIRING_TTL_SECONDS,
        session_days: int = DEFAULT_SESSION_DAYS,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.path = Path(path)
        self._pairing_ttl = timedelta(seconds=max(30, int(pairing_ttl_seconds)))
        self._session_ttl = timedelta(days=max(1, int(session_days)))
        self._clock = clock or _utc_now
        self._lock = threading.Lock()
        self._last_seen_written: dict[str, float] = {}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS pairing_codes (
                    code_hash TEXT PRIMARY KEY,
                    label TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    used_at TEXT
                );
                CREATE TABLE IF NOT EXISTS sessions (
                    session_id TEXT PRIMARY KEY,
                    token_hash TEXT NOT NULL UNIQUE,
                    label TEXT NOT NULL DEFAULT '',
                    user_agent TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    last_seen_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    revoked_at TEXT,
                    conversation_id TEXT
                );
                """
            )

    # ------------------------------------------------------------------ plumbing
    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=5.0)
        connection.row_factory = sqlite3.Row
        return connection

    @staticmethod
    def _row_session(row: sqlite3.Row) -> DeviceSession:
        return DeviceSession(
            session_id=row["session_id"],
            label=row["label"],
            created_at=datetime.fromisoformat(row["created_at"]),
            last_seen_at=datetime.fromisoformat(row["last_seen_at"]),
            expires_at=datetime.fromisoformat(row["expires_at"]),
            revoked_at=datetime.fromisoformat(row["revoked_at"]) if row["revoked_at"] else None,
            conversation_id=row["conversation_id"],
        )

    # ------------------------------------------------------------------ pairing
    def create_pairing_code(self, label: str = "") -> tuple[str, datetime]:
        """Mint one short-lived, single-use code; the clear text is shown once."""
        code = "".join(secrets.choice(PAIRING_ALPHABET) for _ in range(PAIRING_LENGTH))
        now = self._clock()
        expires = now + self._pairing_ttl
        with self._lock, self._connect() as connection:
            # Any earlier unused code is retired: one live code at a time
            # keeps the guessing surface as small as it can be.
            connection.execute(
                "UPDATE pairing_codes SET used_at = ? WHERE used_at IS NULL",
                (now.isoformat(),),
            )
            connection.execute(
                "INSERT INTO pairing_codes (code_hash, label, created_at, expires_at) VALUES (?, ?, ?, ?)",
                (_digest(code), str(label or "")[:80], now.isoformat(), expires.isoformat()),
            )
            connection.execute(
                "DELETE FROM pairing_codes WHERE expires_at < ?",
                ((now - timedelta(days=1)).isoformat(),),
            )
        return code, expires

    def pending_pairing(self) -> dict[str, Any] | None:
        """Whether an unused, unexpired code exists (never the code itself)."""
        now = self._clock()
        with self._connect() as connection:
            row = connection.execute(
                "SELECT created_at, expires_at FROM pairing_codes WHERE used_at IS NULL AND expires_at > ? ORDER BY created_at DESC LIMIT 1",
                (now.isoformat(),),
            ).fetchone()
        if row is None:
            return None
        return {"created_at": row["created_at"], "expires_at": row["expires_at"]}

    def redeem_pairing_code(self, raw_code: str, *, label: str, user_agent: str = "") -> tuple[str, DeviceSession]:
        """Exchange a code for a session; returns the clear token exactly once."""
        code = normalize_pairing_code(raw_code)
        if len(code) != PAIRING_LENGTH:
            raise PairingError("Eşleştirme kodu 8 karakter olmalı.")
        now = self._clock()
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT code_hash, expires_at, used_at FROM pairing_codes WHERE code_hash = ?",
                (_digest(code),),
            ).fetchone()
            if row is None:
                raise PairingError("Eşleştirme kodu geçersiz.")
            if row["used_at"]:
                raise PairingError("Bu kod daha önce kullanıldı; masaüstünden yeni kod al.")
            if datetime.fromisoformat(row["expires_at"]) <= now:
                raise PairingError("Eşleştirme kodunun süresi doldu; masaüstünden yeni kod al.")
            connection.execute(
                "UPDATE pairing_codes SET used_at = ? WHERE code_hash = ?",
                (now.isoformat(), row["code_hash"]),
            )
            token = secrets.token_urlsafe(TOKEN_BYTES)
            session_id = secrets.token_hex(8)
            expires = now + self._session_ttl
            connection.execute(
                "INSERT INTO sessions (session_id, token_hash, label, user_agent, created_at, last_seen_at, expires_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (session_id, _digest(token), str(label or "Telefon")[:80], str(user_agent or "")[:200], now.isoformat(), now.isoformat(), expires.isoformat()),
            )
        return token, DeviceSession(session_id, str(label or "Telefon")[:80], now, now, expires, None, None)

    # ----------------------------------------------------------------- sessions
    def authenticate(self, token: str | None) -> DeviceSession | None:
        """The live session behind a token, or None; touches last_seen sparingly."""
        if not token:
            return None
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM sessions WHERE token_hash = ?", (_digest(str(token)),)
            ).fetchone()
        if row is None:
            return None
        session = self._row_session(row)
        # Judged by the store's own clock, so expiry is testable and exact.
        if session.revoked_at is not None or session.expires_at <= self._clock():
            return None
        wall = time.monotonic()
        if wall - self._last_seen_written.get(session.session_id, 0.0) > LAST_SEEN_WRITE_INTERVAL_SECONDS:
            self._last_seen_written[session.session_id] = wall
            with self._lock, self._connect() as connection:
                connection.execute(
                    "UPDATE sessions SET last_seen_at = ? WHERE session_id = ?",
                    (self._clock().isoformat(), session.session_id),
                )
        return session

    def get(self, session_id: str) -> DeviceSession | None:
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM sessions WHERE session_id = ?", (str(session_id),)).fetchone()
        return self._row_session(row) if row is not None else None

    def list_sessions(self, *, include_revoked: bool = False) -> list[DeviceSession]:
        with self._connect() as connection:
            rows = connection.execute("SELECT * FROM sessions ORDER BY created_at DESC LIMIT 100").fetchall()
        sessions = [self._row_session(row) for row in rows]
        if not include_revoked:
            sessions = [session for session in sessions if session.active]
        return sessions

    def revoke(self, session_id: str) -> bool:
        with self._lock, self._connect() as connection:
            cursor = connection.execute(
                "UPDATE sessions SET revoked_at = ? WHERE session_id = ? AND revoked_at IS NULL",
                (self._clock().isoformat(), str(session_id)),
            )
        return cursor.rowcount > 0

    def revoke_all(self) -> int:
        with self._lock, self._connect() as connection:
            cursor = connection.execute(
                "UPDATE sessions SET revoked_at = ? WHERE revoked_at IS NULL",
                (self._clock().isoformat(),),
            )
        return cursor.rowcount

    def set_conversation(self, session_id: str, conversation_id: str | None) -> None:
        with self._lock, self._connect() as connection:
            connection.execute(
                "UPDATE sessions SET conversation_id = ? WHERE session_id = ?",
                (conversation_id, str(session_id)),
            )


__all__ = [
    "DeviceSession",
    "MobileSessionStore",
    "PairingError",
    "RateLimiter",
    "format_pairing_code",
    "normalize_pairing_code",
]
