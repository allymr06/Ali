from __future__ import annotations

import json
import math
import re
import shutil
import sqlite3
from datetime import datetime
from pathlib import Path
from threading import RLock
from uuid import UUID

from app.core.time import utc_now
from app.memory.base import MemoryStore
from app.memory.models import (
    MemoryEntry,
    MemorySensitivity,
    MemorySource,
    MemoryType,
)


class MemoryCorruptionError(RuntimeError):
    """Raised when a memory database fails an integrity or schema check."""


class SQLiteMemoryStore(MemoryStore):
    """Thread-safe, migration-aware durable memory storage."""

    SCHEMA_VERSION = 1
    _TOKEN = re.compile(r"\w+", re.UNICODE)

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path).expanduser().resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = RLock()
        try:
            self._connection = sqlite3.connect(
                self.path,
                timeout=5.0,
                check_same_thread=False,
            )
            self._connection.row_factory = sqlite3.Row
            self._connection.execute("PRAGMA foreign_keys = ON")
            self._connection.execute("PRAGMA journal_mode = WAL")
            self._connection.execute("PRAGMA synchronous = FULL")
            self._connection.execute("PRAGMA busy_timeout = 5000")
            self._migrate()
            self.check_integrity()
        except sqlite3.DatabaseError as exc:
            connection = getattr(self, "_connection", None)
            if connection is not None:
                connection.close()
            raise MemoryCorruptionError(
                f"Memory database is unreadable: {self.path}"
            ) from exc

    def _migrate(self) -> None:
        version = int(self._connection.execute("PRAGMA user_version").fetchone()[0])
        if version > self.SCHEMA_VERSION:
            raise MemoryCorruptionError(
                f"Memory schema {version} is newer than supported version "
                f"{self.SCHEMA_VERSION}."
            )
        if version == 0:
            with self._connection:
                self._connection.executescript(
                    """
                    CREATE TABLE IF NOT EXISTS memories (
                        memory_id TEXT PRIMARY KEY,
                        content TEXT NOT NULL,
                        memory_type TEXT NOT NULL,
                        importance REAL NOT NULL CHECK (importance BETWEEN 0 AND 1),
                        confidence REAL NOT NULL CHECK (confidence BETWEEN 0 AND 1),
                        source TEXT NOT NULL,
                        source_reference TEXT,
                        sensitivity TEXT NOT NULL,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL,
                        last_used_at TEXT,
                        last_verified_at TEXT,
                        expires_at TEXT,
                        active INTEGER NOT NULL CHECK (active IN (0, 1)),
                        supersedes TEXT,
                        superseded_by TEXT,
                        metadata_json TEXT NOT NULL
                    );
                    CREATE INDEX IF NOT EXISTS idx_memories_active_updated
                        ON memories(active, updated_at DESC);
                    CREATE INDEX IF NOT EXISTS idx_memories_type_active
                        ON memories(memory_type, active);
                    PRAGMA user_version = 1;
                    """
                )

    def save(self, memory: MemoryEntry) -> MemoryEntry:
        metadata_json = json.dumps(
            memory.metadata,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        values = (
            str(memory.memory_id), memory.content, memory.memory_type.value,
            memory.importance, memory.confidence, memory.source.value,
            memory.source_reference, memory.sensitivity.value,
            self._datetime(memory.created_at), self._datetime(memory.updated_at),
            self._datetime(memory.last_used_at),
            self._datetime(memory.last_verified_at),
            self._datetime(memory.expires_at), int(memory.active),
            self._uuid(memory.supersedes), self._uuid(memory.superseded_by),
            metadata_json,
        )
        with self._lock, self._connection:
            self._connection.execute(
                """
                INSERT INTO memories VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(memory_id) DO UPDATE SET
                    content=excluded.content,
                    memory_type=excluded.memory_type,
                    importance=excluded.importance,
                    confidence=excluded.confidence,
                    source=excluded.source,
                    source_reference=excluded.source_reference,
                    sensitivity=excluded.sensitivity,
                    created_at=excluded.created_at,
                    updated_at=excluded.updated_at,
                    last_used_at=excluded.last_used_at,
                    last_verified_at=excluded.last_verified_at,
                    expires_at=excluded.expires_at,
                    active=excluded.active,
                    supersedes=excluded.supersedes,
                    superseded_by=excluded.superseded_by,
                    metadata_json=excluded.metadata_json
                """,
                values,
            )
        return memory

    def get(self, memory_id: UUID) -> MemoryEntry:
        with self._lock:
            row = self._connection.execute(
                "SELECT * FROM memories WHERE memory_id = ?", (str(memory_id),)
            ).fetchone()
        if row is None:
            raise KeyError(f"Memory '{memory_id}' was not found.")
        return self._from_row(row)

    def delete(self, memory_id: UUID) -> MemoryEntry:
        memory = self.get(memory_id)
        with self._lock, self._connection:
            cursor = self._connection.execute(
                "DELETE FROM memories WHERE memory_id = ?", (str(memory_id),)
            )
        if cursor.rowcount != 1:
            raise KeyError(f"Memory '{memory_id}' was not found.")
        return memory

    def search(self, query: str, *, limit: int = 10) -> list[MemoryEntry]:
        normalized = query.strip().casefold()
        if not normalized:
            return []
        if limit < 1:
            raise ValueError("Search limit must be at least 1.")
        query_tokens = set(self._TOKEN.findall(normalized))
        now = utc_now()
        candidates = [
            item for item in self.list_all()
            if item.active and item.freshness(now=now).value != "expired"
        ]
        ranked: list[tuple[float, datetime, str, MemoryEntry]] = []
        for memory in candidates:
            content = memory.content.casefold()
            content_tokens = set(self._TOKEN.findall(content))
            overlap = query_tokens & content_tokens
            if not overlap and normalized not in content:
                continue
            coverage = len(overlap) / max(len(query_tokens), 1)
            specificity = len(overlap) / max(len(content_tokens), 1)
            exact_bonus = 1.0 if normalized in content else 0.0
            age_days = max((now - memory.updated_at).total_seconds() / 86400, 0.0)
            recency = math.exp(-age_days / 180.0)
            score = (
                coverage * 0.42 + specificity * 0.18 + exact_bonus * 0.12
                + memory.importance * 0.13 + memory.confidence * 0.10
                + recency * 0.05
            )
            ranked.append((score, memory.updated_at, str(memory.memory_id), memory))
        ranked.sort(key=lambda item: (item[0], item[1], item[2]), reverse=True)
        return [item[3] for item in ranked[:limit]]

    def list_all(self) -> list[MemoryEntry]:
        with self._lock:
            rows = self._connection.execute(
                "SELECT * FROM memories ORDER BY created_at, memory_id"
            ).fetchall()
        return [self._from_row(row) for row in rows]

    def __len__(self) -> int:
        with self._lock:
            return int(self._connection.execute("SELECT COUNT(*) FROM memories").fetchone()[0])

    def check_integrity(self) -> None:
        with self._lock:
            result = self._connection.execute("PRAGMA quick_check").fetchone()[0]
        if result != "ok":
            raise MemoryCorruptionError(f"Memory database integrity check failed: {result}")

    def backup_to(self, destination: str | Path) -> Path:
        backup_path = Path(destination).expanduser().resolve()
        if backup_path == self.path:
            raise ValueError("Backup destination must differ from the live database.")
        backup_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = backup_path.with_suffix(backup_path.suffix + ".tmp")
        if temporary.exists():
            temporary.unlink()
        target = sqlite3.connect(temporary)
        try:
            with self._lock:
                self._connection.backup(target)
            result = target.execute("PRAGMA quick_check").fetchone()[0]
            if result != "ok":
                raise MemoryCorruptionError(
                    f"Backup integrity check failed: {result}"
                )
        finally:
            target.close()
        temporary.replace(backup_path)
        return backup_path

    @classmethod
    def restore_from_backup(
        cls, backup: str | Path, destination: str | Path
    ) -> SQLiteMemoryStore:
        backup_path = Path(backup).expanduser().resolve()
        destination_path = Path(destination).expanduser().resolve()
        if not backup_path.is_file():
            raise FileNotFoundError(backup_path)
        destination_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination_path.with_suffix(destination_path.suffix + ".restore")
        shutil.copy2(backup_path, temporary)
        probe = sqlite3.connect(temporary)
        try:
            result = probe.execute("PRAGMA quick_check").fetchone()[0]
            if result != "ok":
                raise MemoryCorruptionError(f"Backup integrity check failed: {result}")
        finally:
            probe.close()
        temporary.replace(destination_path)
        return cls(destination_path)

    def close(self) -> None:
        with self._lock:
            self._connection.close()

    @staticmethod
    def _datetime(value: datetime | None) -> str | None:
        return value.isoformat() if value is not None else None

    @staticmethod
    def _uuid(value: UUID | None) -> str | None:
        return str(value) if value is not None else None

    @staticmethod
    def _parse_datetime(value: str | None) -> datetime | None:
        return datetime.fromisoformat(value) if value is not None else None

    def _from_row(self, row: sqlite3.Row) -> MemoryEntry:
        try:
            metadata = json.loads(row["metadata_json"])
            if not isinstance(metadata, dict):
                raise ValueError("metadata_json is not an object")
            return MemoryEntry(
                content=row["content"], memory_type=MemoryType(row["memory_type"]),
                importance=float(row["importance"]), confidence=float(row["confidence"]),
                source=MemorySource(row["source"]),
                source_reference=row["source_reference"],
                sensitivity=MemorySensitivity(row["sensitivity"]),
                memory_id=UUID(row["memory_id"]),
                created_at=self._parse_datetime(row["created_at"]),
                updated_at=self._parse_datetime(row["updated_at"]),
                last_used_at=self._parse_datetime(row["last_used_at"]),
                last_verified_at=self._parse_datetime(row["last_verified_at"]),
                expires_at=self._parse_datetime(row["expires_at"]),
                active=bool(row["active"]),
                supersedes=UUID(row["supersedes"]) if row["supersedes"] else None,
                superseded_by=UUID(row["superseded_by"]) if row["superseded_by"] else None,
                metadata=metadata,
            )
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise MemoryCorruptionError(
                f"Invalid memory record: {row['memory_id']}"
            ) from exc
