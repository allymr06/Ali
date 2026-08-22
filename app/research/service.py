from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from urllib.parse import urlsplit

from app.core.models import ToolDefinition, ToolExecutionStatus, ToolResult
from app.research.citations import validate_citations
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
from app.tools.executor import ToolExecutor

_SENTENCE = re.compile(r"(?<=[.!?])\s+")
_WORD = re.compile(r"[a-zA-Z0-9À-ž]{3,}")


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
    ) -> ResearchReport:
        question = query.strip()
        if not question or len(question) > 500:
            raise ValueError("Research question must contain between 1 and 500 characters.")
        limit = max_sources if max_sources is not None else self.max_sources
        if not 1 <= limit <= self.max_sources:
            raise ValueError(f"max_sources must be between 1 and {self.max_sources}.")
        stages = [ResearchStage.QUESTION, ResearchStage.SEARCH]
        hits = self.search_provider.search(question, limit=limit * 2, time_range=time_range)
        stages.extend((ResearchStage.COLLECT, ResearchStage.FILTER))
        documents, failures = self._collect(hits, limit)
        now = datetime.now(UTC)
        sources = tuple(
            ResearchSource(
                source_id=f"S{index}",
                title=document.title,
                url=document.final_url,
                excerpt=_best_excerpt(document, question),
                observed_at=document.observed_at,
                published_at=document.published_at,
                freshness=_freshness(document.published_at, now),
                content_hash=document.content_hash,
                resolved_addresses=document.resolved_addresses,
                injection_findings=document.findings,
            )
            for index, document in enumerate(documents, start=1)
        )
        stages.extend((ResearchStage.CROSS_CHECK, ResearchStage.SYNTHESIZE))
        claims = self._synthesize(sources)
        uncertainties: list[str] = []
        if failures:
            uncertainties.append(f"{failures} candidate source(s) could not be safely collected.")
        if not sources:
            uncertainties.append("No eligible source content was collected.")
        elif all(source.freshness is Freshness.UNKNOWN for source in sources):
            uncertainties.append("The publication dates of all collected sources are unknown.")
        if len({urlsplit(source.url).hostname for source in sources}) < 2:
            uncertainties.append("The evidence was not corroborated across independent domains.")
        stages.append(ResearchStage.CITE)
        report = ResearchReport(
            question=question,
            sources=sources,
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
        ) -> ToolResult:
            report = self.research(query, max_sources=max_sources, time_range=time_range)
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
                    "source timestamps, hashes, citations, and explicit uncertainties."
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
