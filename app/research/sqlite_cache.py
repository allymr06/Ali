from __future__ import annotations

import json
import sqlite3
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from pathlib import Path
from typing import Mapping

from app.research.citations import validate_citations
from app.research.errors import CitationIntegrityError
from app.research.models import ResearchReport


class ResearchCacheIntegrityError(RuntimeError):
    """Raised when the isolated research cache database is not structurally safe."""


class SQLiteResearchCache:
    """Low-trust, disposable persistence for citation-preserving research reports."""

    SCHEMA_VERSION = 1

    def __init__(
        self,
        path: str | Path,
        *,
        ttl: timedelta = timedelta(hours=24),
        timeout_seconds: float = 5.0,
    ) -> None:
        candidate = Path(path).expanduser()
        if not candidate.is_absolute():
            raise ValueError("Research cache path must be absolute.")
        if ttl <= timedelta(0):
            raise ValueError("Research cache TTL must be positive.")
        if timeout_seconds <= 0:
            raise ValueError("Research cache timeout must be positive.")
        self.path = candidate.resolve()
        self.ttl = ttl
        self.timeout_seconds = timeout_seconds
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    @staticmethod
    def key_for(question: str, max_sources: int, time_range: str | None) -> str:
        normalized_question = " ".join(question.split()).casefold()
        normalized_range = " ".join((time_range or "").split()).casefold() or None
        material = json.dumps(
            {
                "format_version": SQLiteResearchCache.SCHEMA_VERSION,
                "question": normalized_question,
                "max_sources": int(max_sources),
                "time_range": normalized_range,
            },
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        return sha256(material.encode("utf-8")).hexdigest()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(
            self.path,
            timeout=self.timeout_seconds,
            isolation_level=None,
        )
        connection.execute(f"PRAGMA busy_timeout = {int(self.timeout_seconds * 1000)}")
        connection.execute("PRAGMA synchronous = FULL")
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    def _initialize(self) -> None:
        try:
            with self._connect() as connection:
                journal_mode = connection.execute("PRAGMA journal_mode = WAL").fetchone()
                if journal_mode is None or str(journal_mode[0]).casefold() != "wal":
                    raise ResearchCacheIntegrityError(
                        "Research cache could not enable WAL mode."
                    )
                version = int(connection.execute("PRAGMA user_version").fetchone()[0])
                if version not in {0, self.SCHEMA_VERSION}:
                    raise ResearchCacheIntegrityError(
                        f"Unsupported research cache schema version: {version}."
                    )
                if version == 0:
                    connection.execute("BEGIN IMMEDIATE")
                    try:
                        connection.execute(
                        """
                        CREATE TABLE IF NOT EXISTS research_cache (
                            cache_key TEXT PRIMARY KEY,
                            report_json TEXT NOT NULL,
                            cached_at TEXT NOT NULL,
                            expires_at TEXT NOT NULL
                        ) WITHOUT ROWID
                        """
                        )
                        connection.execute(
                            f"PRAGMA user_version = {self.SCHEMA_VERSION}"
                        )
                        connection.execute("COMMIT")
                    except Exception:
                        connection.execute("ROLLBACK")
                        raise
                columns = {
                    str(row[1])
                    for row in connection.execute(
                        "PRAGMA table_info(research_cache)"
                    ).fetchall()
                }
                required = {"cache_key", "report_json", "cached_at", "expires_at"}
                if required - columns:
                    raise ResearchCacheIntegrityError(
                        "Research cache schema is incomplete."
                    )
                self.quick_check(connection=connection)
        except sqlite3.DatabaseError as exc:
            raise ResearchCacheIntegrityError(
                f"Research cache is unreadable: {self.path}"
            ) from exc

    def quick_check(self, *, connection: sqlite3.Connection | None = None) -> None:
        owns_connection = connection is None
        active = connection or self._connect()
        try:
            rows = active.execute("PRAGMA quick_check").fetchall()
            if rows != [("ok",)]:
                detail = "; ".join(str(row[0]) for row in rows[:3]) or "unknown error"
                raise ResearchCacheIntegrityError(f"Research cache quick_check failed: {detail}")
        finally:
            if owns_connection:
                active.close()

    def put(
        self,
        question: str,
        max_sources: int,
        time_range: str | None,
        report: ResearchReport,
        *,
        now: datetime | None = None,
    ) -> ResearchReport:
        cached_at = (now or datetime.now(UTC)).astimezone(UTC)
        expires_at = cached_at + self.ttl
        stored = replace(
            report,
            cache_hit=False,
            cached_at=cached_at,
            expires_at=expires_at,
            stale=False,
        )
        encoded = json.dumps(
            stored.to_dict(),
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        cache_key = self.key_for(question, max_sources, time_range)
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                connection.execute(
                    """
                    INSERT INTO research_cache(cache_key, report_json, cached_at, expires_at)
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT(cache_key) DO UPDATE SET
                        report_json = excluded.report_json,
                        cached_at = excluded.cached_at,
                        expires_at = excluded.expires_at
                    """,
                    (cache_key, encoded, cached_at.isoformat(), expires_at.isoformat()),
                )
                connection.execute("COMMIT")
            except Exception:
                connection.execute("ROLLBACK")
                raise
        return stored

    def get(
        self,
        question: str,
        max_sources: int,
        time_range: str | None,
        *,
        allow_stale: bool = False,
        now: datetime | None = None,
    ) -> ResearchReport | None:
        cache_key = self.key_for(question, max_sources, time_range)
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT report_json, cached_at, expires_at
                FROM research_cache
                WHERE cache_key = ?
                """,
                (cache_key,),
            ).fetchone()
        if row is None:
            return None
        try:
            payload = json.loads(row[0])
            if not isinstance(payload, Mapping):
                raise ValueError("Cached report payload must be an object.")
            report = ResearchReport.from_dict(payload)
            validate_citations(report)
            cached_at = self._timestamp(row[1], "cached_at")
            expires_at = self._timestamp(row[2], "expires_at")
        except (CitationIntegrityError, TypeError, ValueError, json.JSONDecodeError):
            self._discard(cache_key)
            return None
        stale = (now or datetime.now(UTC)).astimezone(UTC) >= expires_at
        if stale and not allow_stale:
            return None
        return replace(
            report,
            cache_hit=True,
            cached_at=cached_at,
            expires_at=expires_at,
            stale=stale,
        )

    def _discard(self, cache_key: str) -> None:
        with self._connect() as connection:
            connection.execute("DELETE FROM research_cache WHERE cache_key = ?", (cache_key,))

    @staticmethod
    def _timestamp(value: object, name: str) -> datetime:
        if not isinstance(value, str):
            raise ValueError(f"Cached {name} must be text.")
        parsed = datetime.fromisoformat(value)
        if parsed.tzinfo is None:
            raise ValueError(f"Cached {name} must include a timezone.")
        return parsed.astimezone(UTC)
