from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from threading import Lock
from typing import Iterator
from urllib.parse import urlsplit

from app.core.models import ToolDefinition, ToolExecutionStatus, ToolResult
from app.research.citations import validate_citations
from app.research.errors import FetchError, SearchError
from app.research.extractor import detect_prompt_injection
from app.research.fetcher import SafeWebFetcher
from app.research.models import (
    Freshness,
    ResearchClaim,
    ResearchReport,
    ResearchSource,
    ResearchStage,
    SearchHit,
    WebDocument,
)
from app.research.search import SearchProvider
from app.research.sources import (
    MultiSourceSearchProvider,
    normalize_site,
    parse_sources,
    source_spec,
)
from app.research.sqlite_cache import SQLiteResearchCache
from app.tools.executor import ToolExecutor

_SENTENCE = re.compile(r"(?<=[.!?])\s+")
_WORD = re.compile(r"[a-zA-Z0-9À-ž]{3,}")


@dataclass(slots=True)
class _KeyLock:
    lock: Lock = field(default_factory=Lock)
    users: int = 0


def _tokens(text: str) -> frozenset[str]:
    return frozenset(match.group(0).casefold() for match in _WORD.finditer(text))


def _freshness(published_at: datetime | None, now: datetime) -> Freshness:
    if published_at is None:
        return Freshness.UNKNOWN
    age = now - published_at.astimezone(UTC)
    if age <= timedelta(days=30):
        return Freshness.CURRENT
    if age <= timedelta(days=365):
        return Freshness.AGING
    return Freshness.STALE


def _best_excerpt(document: WebDocument, query: str, limit: int = 700) -> str:
    query_tokens = _tokens(query)
    sentences = [value.strip() for value in _SENTENCE.split(document.text) if value.strip()]
    if not sentences:
        return document.text[:limit]
    ranked = sorted(
        enumerate(sentences[:200]),
        key=lambda item: (len(_tokens(item[1]) & query_tokens), -item[0]),
        reverse=True,
    )
    chosen = [value for _, value in ranked[:2]]
    return " ".join(chosen)[:limit]


@dataclass(slots=True)
class ResearchService:
    search_provider: SearchProvider
    fetcher: SafeWebFetcher
    max_sources: int = 5
    max_concurrency: int = 3
    operation_timeout_seconds: float = 45.0
    cache: SQLiteResearchCache | None = None
    # The places beside the web. search_provider stays the configured web
    # backend, so what the settings chose is what the report's web hits come
    # from; the catalogue wraps it for the sources the page may pick.
    sources: MultiSourceSearchProvider | None = None
    _cache_guard: Lock = field(default_factory=Lock, init=False, repr=False)
    _cache_locks: dict[str, _KeyLock] = field(default_factory=dict, init=False, repr=False)

    def __post_init__(self) -> None:
        if not 1 <= self.max_sources <= 10:
            raise ValueError("max_sources must be between 1 and 10.")
        if not 1 <= self.max_concurrency <= 8:
            raise ValueError("max_concurrency must be between 1 and 8.")
        if self.operation_timeout_seconds <= 0:
            raise ValueError("operation_timeout_seconds must be positive.")

    def research(
        self,
        query: str,
        max_sources: int | None = None,
        time_range: str | None = None,
        *,
        refresh: bool = False,
        sources: object = None,
        site: str | None = None,
    ) -> ResearchReport:
        question = query.strip()
        if not question or len(question) > 500:
            raise ValueError("Research question must contain between 1 and 500 characters.")
        limit = max_sources if max_sources is not None else self.max_sources
        if not 1 <= limit <= self.max_sources:
            raise ValueError(f"max_sources must be between 1 and {self.max_sources}.")
        chosen = parse_sources(sources)
        host = normalize_site(site)
        if host is not None and "site" not in chosen:
            chosen = chosen + ("site",)
        if self.cache is None:
            return self._research_uncached(question, limit, time_range, chosen, host)

        baseline = self.cache.get(question, limit, time_range, allow_stale=True, sources=chosen, site=host)
        if not refresh:
            fresh = self.cache.get(question, limit, time_range, sources=chosen, site=host)
            if fresh is not None:
                return fresh

        cache_key = self.cache.key_for(question, limit, time_range, sources=chosen, site=host)
        with self._single_flight(cache_key):
            fresh = self.cache.get(question, limit, time_range, sources=chosen, site=host)
            if not refresh and fresh is not None:
                return fresh
            if refresh and fresh is not None and (
                baseline is None or fresh.cached_at != baseline.cached_at
            ):
                return fresh
            try:
                report = self._research_uncached(question, limit, time_range, chosen, host)
            except (FetchError, SearchError, OSError, TimeoutError) as exc:
                stale = self.cache.get(
                    question,
                    limit,
                    time_range,
                    allow_stale=True,
                    sources=chosen,
                    site=host,
                )
                if stale is None:
                    raise
                uncertainty = (
                    "Live research was unavailable; cached evidence was returned and may "
                    f"be outdated ({type(exc).__name__})."
                )
                return replace(
                    stale,
                    stale=True,
                    uncertainties=tuple((*stale.uncertainties, uncertainty)),
                )
            if not report.sources and baseline is not None:
                return replace(
                    baseline,
                    stale=True,
                    uncertainties=tuple(
                        (
                            *baseline.uncertainties,
                            "Live research returned no usable sources; cached evidence "
                            "was returned and may be outdated.",
                        )
                    ),
                )
            return self.cache.put(question, limit, time_range, report, sources=chosen, site=host)

    @contextmanager
    def _single_flight(self, cache_key: str) -> Iterator[None]:
        with self._cache_guard:
            entry = self._cache_locks.get(cache_key)
            if entry is None:
                entry = _KeyLock()
                self._cache_locks[cache_key] = entry
            entry.users += 1
        entry.lock.acquire()
        try:
            yield
        finally:
            entry.lock.release()
            with self._cache_guard:
                entry.users -= 1
                if entry.users == 0:
                    self._cache_locks.pop(cache_key, None)

    def _search(
        self,
        question: str,
        limit: int,
        time_range: str | None,
        sources: tuple[str, ...],
        site: str | None,
    ) -> tuple[tuple[SearchHit, ...], tuple[tuple[str, str], ...]]:
        provider = self.sources if self.sources is not None else self.search_provider
        if isinstance(provider, MultiSourceSearchProvider):
            result = provider.search_sources(
                question, limit=limit * 2, time_range=time_range, sources=sources or None, site=site
            )
            return result.hits, result.failures
        if (sources and tuple(sources) != ("web",)) or site:
            raise ValueError("This research backend searches the web only.")
        return provider.search(question, limit=limit * 2, time_range=time_range), ()

    def _research_uncached(
        self,
        question: str,
        limit: int,
        time_range: str | None,
        sources: tuple[str, ...] = (),
        site: str | None = None,
    ) -> ResearchReport:
        stages = [ResearchStage.QUESTION, ResearchStage.SEARCH]
        hits, source_failures = self._search(question, limit, time_range, sources, site)
        stages.extend((ResearchStage.COLLECT, ResearchStage.FILTER))
        now = datetime.now(UTC)
        # A hit that carries its own evidence - what the source's endpoint
        # said about it - is cited as it is. Only the rest are fetched, and
        # only as many as the report still has room for.
        direct = [hit for hit in hits if hit.evidence][:limit]
        pending = tuple(hit for hit in hits if not hit.evidence)
        remaining = limit - len(direct)
        documents, failures = self._collect(pending, remaining) if remaining > 0 and pending else ([], 0)
        origins = {hit.url: hit for hit in hits}
        collected: list[ResearchSource] = []
        for hit in direct:
            collected.append(
                ResearchSource(
                    source_id="",
                    title=hit.title,
                    url=hit.url,
                    excerpt=hit.evidence[:700],
                    observed_at=now,
                    published_at=hit.published_at,
                    freshness=_freshness(hit.published_at, now),
                    content_hash=sha256(hit.evidence.encode("utf-8")).hexdigest(),
                    injection_findings=detect_prompt_injection(hit.evidence),
                    kind=hit.kind,
                    source=hit.source,
                    meta=hit.meta,
                )
            )
        for document in documents:
            origin = origins.get(document.url) or origins.get(document.final_url)
            collected.append(
                ResearchSource(
                    source_id="",
                    title=document.title,
                    url=document.final_url,
                    excerpt=_best_excerpt(document, question),
                    observed_at=document.observed_at,
                    published_at=document.published_at,
                    freshness=_freshness(document.published_at, now),
                    content_hash=document.content_hash,
                    resolved_addresses=document.resolved_addresses,
                    injection_findings=document.findings,
                    kind=origin.kind if origin is not None else "web",
                    source=origin.source if origin is not None else "web",
                    meta=origin.meta if origin is not None else (),
                )
            )
        sources_out = tuple(
            replace(item, source_id=f"S{index}") for index, item in enumerate(collected, start=1)
        )
        stages.extend((ResearchStage.CROSS_CHECK, ResearchStage.SYNTHESIZE))
        claims = self._synthesize(sources_out)
        uncertainties: list[str] = []
        for source_id, reason in source_failures:
            uncertainties.append(f"Source {source_spec(source_id).label} was unavailable ({reason}).")
        if failures:
            uncertainties.append(f"{failures} candidate source(s) could not be safely collected.")
        if not sources_out:
            uncertainties.append("No eligible source content was collected.")
        elif all(source.freshness is Freshness.UNKNOWN for source in sources_out):
            uncertainties.append("The publication dates of all collected sources are unknown.")
        if len({urlsplit(source.url).hostname for source in sources_out}) < 2:
            uncertainties.append("The evidence was not corroborated across independent domains.")
        stages.append(ResearchStage.CITE)
        report = ResearchReport(
            question=question,
            sources=sources_out,
            claims=claims,
            uncertainties=tuple(uncertainties),
            stages=tuple(stages + [ResearchStage.COMPLETE]),
        )
        validate_citations(report)
        return report

    def _collect(
        self, hits: tuple[SearchHit, ...], limit: int
    ) -> tuple[list[WebDocument], int]:
        documents: list[WebDocument] = []
        failures = 0
        seen_hosts: set[str] = set()
        with ThreadPoolExecutor(max_workers=self.max_concurrency) as pool:
            futures = {pool.submit(self.fetcher.fetch, hit.url): hit for hit in hits[: limit * 2]}
            for future in as_completed(futures):
                try:
                    document = future.result()
                except Exception:
                    failures += 1
                    continue
                host = urlsplit(document.final_url).hostname or ""
                key = f"{host}:{document.content_hash}"
                if key in seen_hosts:
                    continue
                seen_hosts.add(key)
                documents.append(document)
                if len(documents) >= limit:
                    for pending in futures:
                        pending.cancel()
                    break
        documents.sort(key=lambda item: item.final_url)
        return documents, failures

    @staticmethod
    def _synthesize(sources: tuple[ResearchSource, ...]) -> tuple[ResearchClaim, ...]:
        claims: list[ResearchClaim] = []
        for source in sources:
            support = [source.source_id]
            source_tokens = _tokens(source.excerpt)
            for candidate in sources:
                if candidate.source_id == source.source_id:
                    continue
                candidate_tokens = _tokens(candidate.excerpt)
                denominator = max(1, min(len(source_tokens), len(candidate_tokens)))
                if len(source_tokens & candidate_tokens) / denominator >= 0.35:
                    support.append(candidate.source_id)
            claims.append(
                ResearchClaim(
                    text=source.excerpt,
                    source_ids=tuple(dict.fromkeys(support)),
                    confidence=0.8 if len(support) > 1 else 0.55,
                    inference=False,
                )
            )
        return tuple(claims)

    def register_tools(self, executor: ToolExecutor) -> None:
        def research_web(
            query: str,
            max_sources: int = 5,
            time_range: str | None = None,
            refresh: bool = False,
            sources: str | None = None,
            site: str | None = None,
        ) -> ToolResult:
            report = self.research(
                query,
                max_sources=max_sources,
                time_range=time_range,
                refresh=refresh,
                sources=sources,
                site=site,
            )
            return ToolResult(
                status=ToolExecutionStatus.SUCCESS,
                tool_name="research_web",
                message="Untrusted web evidence was safely collected and citation-checked.",
                data=report.to_dict(),
                verified=True,
            )

        executor.register(
            ToolDefinition(
                name="research_web",
                description=(
                    "Research a question using bounded untrusted web evidence; returns "
                    "source timestamps, hashes, citations, and explicit uncertainties. "
                    "Use it whenever the answer needs current or verifiable facts - "
                    "news, dates, prices, schedules, guidelines, anything after the "
                    "training cutoff - instead of guessing. sources is an optional "
                    "comma list of web, youtube, github, wikipedia, pubmed, arxiv, "
                    "stackoverflow, hackernews; site restricts the web search to one "
                    "host."
                ),
                timeout_seconds=self.operation_timeout_seconds,
                capabilities=frozenset({"web", "research", "search"}),
                tags=frozenset({"read-only", "network", "untrusted-content"}),
                max_concurrency=2,
                idempotent=True,
                metadata={
                    "verification_strategy": "retrieval_and_citation_integrity",
                    "content_trust": "untrusted",
                    "automatic_instruction_execution": False,
                },
            ),
            research_web,
            source="core:research",
        )
