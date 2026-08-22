from __future__ import annotations

import sqlite3
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Lock

import pytest

from app.research.errors import SearchError
from app.research.models import InjectionFinding, SearchHit, WebDocument
from app.research.search import SearchProvider
from app.research.service import ResearchService
from app.research.sqlite_cache import (
    ResearchCacheIntegrityError,
    SQLiteResearchCache,
)


class CountingSearch(SearchProvider):
    def __init__(self, *, delay: float = 0.0) -> None:
        self.calls = 0
        self.delay = delay
        self.error: Exception | None = None
        self._lock = Lock()

    def search(
        self,
        query: str,
        *,
        limit: int,
        time_range: str | None = None,
    ) -> tuple[SearchHit, ...]:
        with self._lock:
            self.calls += 1
        if self.delay:
            time.sleep(self.delay)
        if self.error is not None:
            raise self.error
        return (SearchHit("Evidence", "https://evidence.example/article"),)


class StaticFetcher:
    def fetch(self, url: str) -> WebDocument:
        return WebDocument.create(
            url=url,
            final_url=url,
            title="Evidence",
            text="The evidence is current and independently inspectable.",
            content_type="text/plain",
            status=200,
            resolved_addresses=("93.184.216.34",),
            published_at=datetime.now(UTC) - timedelta(days=1),
            findings=(InjectionFinding("instruction-like", "abc123"),),
        )


def service_with_cache(path: Path, *, delay: float = 0.0, ttl_seconds: int = 3600):
    search = CountingSearch(delay=delay)
    cache = SQLiteResearchCache(path, ttl=timedelta(seconds=ttl_seconds))
    service = ResearchService(search, StaticFetcher(), max_sources=1, cache=cache)
    return service, search, cache


def test_cache_requires_absolute_path() -> None:
    with pytest.raises(ValueError, match="absolute"):
        SQLiteResearchCache("relative/research.sqlite3")


def test_cache_rejects_future_or_incomplete_schema(tmp_path: Path) -> None:
    future = tmp_path / "future.sqlite3"
    connection = sqlite3.connect(future)
    connection.execute("PRAGMA user_version = 99")
    connection.close()
    with pytest.raises(ResearchCacheIntegrityError, match="Unsupported"):
        SQLiteResearchCache(future)

    incomplete = tmp_path / "incomplete.sqlite3"
    connection = sqlite3.connect(incomplete)
    connection.execute("CREATE TABLE research_cache (cache_key TEXT PRIMARY KEY)")
    connection.execute("PRAGMA user_version = 1")
    connection.close()
    with pytest.raises(ResearchCacheIntegrityError, match="incomplete"):
        SQLiteResearchCache(incomplete)


def test_cache_normalizes_key_dimensions() -> None:
    first = SQLiteResearchCache.key_for("  Latest   News ", 3, " MONTH ")
    second = SQLiteResearchCache.key_for("latest news", 3, "month")

    assert first == second
    assert first != SQLiteResearchCache.key_for("latest news", 4, "month")
    assert first != SQLiteResearchCache.key_for("latest news", 3, "year")


def test_cache_round_trips_report_and_sqlite_safety_settings(tmp_path: Path) -> None:
    service, _, cache = service_with_cache(tmp_path / "research.sqlite3")
    original = service.research("What is the evidence?", max_sources=1)
    cached = cache.get("what is the evidence?", 1, None)

    assert original.cache_hit is False
    assert original.cached_at is not None
    assert cached is not None
    assert cached.cache_hit is True
    assert cached.sources == original.sources
    assert cached.claims == original.claims
    assert cached.sources[0].injection_findings[0].evidence_hash == "abc123"
    assert cached.to_dict()["cached_at"] is not None
    with cache._connect() as connection:  # noqa: SLF001 - verifies the persistence contract
        assert connection.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        assert connection.execute("PRAGMA synchronous").fetchone()[0] == 2
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 1
    cache.quick_check()


def test_expired_entry_is_available_only_as_stale(tmp_path: Path) -> None:
    service, _, cache = service_with_cache(tmp_path / "research.sqlite3", ttl_seconds=1)
    report = service.research("Evidence?", max_sources=1)
    future = report.expires_at + timedelta(seconds=1)

    assert cache.get("Evidence?", 1, None, now=future) is None
    stale = cache.get("Evidence?", 1, None, allow_stale=True, now=future)
    assert stale is not None
    assert stale.stale is True


def test_service_uses_fresh_cache_and_refresh_bypasses_it(tmp_path: Path) -> None:
    service, search, _ = service_with_cache(tmp_path / "research.sqlite3")

    first = service.research("Evidence?", max_sources=1)
    second = service.research("  evidence?  ", max_sources=1)
    refreshed = service.research("Evidence?", max_sources=1, refresh=True)

    assert search.calls == 2
    assert first.cache_hit is False
    assert second.cache_hit is True
    assert refreshed.cache_hit is False


def test_service_returns_explicitly_uncertain_stale_report_on_network_error(
    tmp_path: Path,
) -> None:
    service, search, _ = service_with_cache(tmp_path / "research.sqlite3")
    service.research("Evidence?", max_sources=1)
    search.error = SearchError("offline")

    stale = service.research("Evidence?", max_sources=1, refresh=True)

    assert stale.cache_hit is True
    assert stale.stale is True
    assert any("cached evidence" in item for item in stale.uncertainties)


def test_same_key_concurrent_calls_are_collapsed_to_one_research(tmp_path: Path) -> None:
    service, search, _ = service_with_cache(tmp_path / "research.sqlite3", delay=0.05)

    with ThreadPoolExecutor(max_workers=5) as pool:
        reports = list(pool.map(lambda _: service.research("Evidence?", 1), range(5)))

    assert search.calls == 1
    assert sum(not report.cache_hit for report in reports) == 1
    assert sum(report.cache_hit for report in reports) == 4


def test_malformed_cached_json_is_discarded(tmp_path: Path) -> None:
    _, _, cache = service_with_cache(tmp_path / "research.sqlite3")
    key = cache.key_for("Evidence?", 1, None)
    now = datetime.now(UTC)
    with sqlite3.connect(cache.path) as connection:
        connection.execute(
            "INSERT INTO research_cache VALUES (?, ?, ?, ?)",
            (key, "{broken", now.isoformat(), (now + timedelta(hours=1)).isoformat()),
        )

    assert cache.get("Evidence?", 1, None) is None
    with sqlite3.connect(cache.path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM research_cache").fetchone()[0] == 0
