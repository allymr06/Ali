from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from hashlib import sha256


class ResearchStage(StrEnum):
    QUESTION = "question"
    SEARCH = "search"
    COLLECT = "collect"
    FILTER = "filter"
    CROSS_CHECK = "cross_check"
    SYNTHESIZE = "synthesize"
    CITE = "cite"
    COMPLETE = "complete"


class Freshness(StrEnum):
    CURRENT = "current"
    AGING = "aging"
    STALE = "stale"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class ResolvedURL:
    url: str
    host: str
    port: int
    addresses: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class SearchHit:
    title: str
    url: str
    snippet: str = ""
    published_at: datetime | None = None
    engine: str | None = None


@dataclass(frozen=True, slots=True)
class InjectionFinding:
    category: str
    evidence_hash: str


@dataclass(frozen=True, slots=True)
class WebDocument:
    url: str
    final_url: str
    title: str
    text: str
    content_type: str
    status: int
    observed_at: datetime
    resolved_addresses: tuple[str, ...]
    content_hash: str
    published_at: datetime | None = None
    findings: tuple[InjectionFinding, ...] = ()

    @classmethod
    def create(
        cls,
        *,
        url: str,
        final_url: str,
        title: str,
        text: str,
        content_type: str,
        status: int,
        resolved_addresses: tuple[str, ...],
        published_at: datetime | None = None,
        findings: tuple[InjectionFinding, ...] = (),
        observed_at: datetime | None = None,
    ) -> WebDocument:
        return cls(
            url=url,
            final_url=final_url,
            title=title.strip(),
            text=text.strip(),
            content_type=content_type,
            status=status,
            observed_at=observed_at or datetime.now(UTC),
            resolved_addresses=resolved_addresses,
            content_hash=sha256(text.encode("utf-8")).hexdigest(),
            published_at=published_at,
            findings=findings,
        )


@dataclass(frozen=True, slots=True)
class ResearchSource:
    source_id: str
    title: str
    url: str
    excerpt: str
    observed_at: datetime
    content_hash: str
    freshness: Freshness
    published_at: datetime | None = None
    resolved_addresses: tuple[str, ...] = ()
    injection_findings: tuple[InjectionFinding, ...] = ()


@dataclass(frozen=True, slots=True)
class ResearchClaim:
    text: str
    source_ids: tuple[str, ...]
    confidence: float
    inference: bool = False

    def __post_init__(self) -> None:
        if not self.text.strip():
            raise ValueError("Claim text cannot be empty.")
        if not 0 <= self.confidence <= 1:
            raise ValueError("Claim confidence must be between 0 and 1.")


@dataclass(frozen=True, slots=True)
class ResearchReport:
    question: str
    sources: tuple[ResearchSource, ...]
    claims: tuple[ResearchClaim, ...]
    uncertainties: tuple[str, ...]
    stages: tuple[ResearchStage, ...]
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def to_dict(self) -> dict[str, object]:
        return {
            "question": self.question,
            "created_at": self.created_at.isoformat(),
            "stages": [stage.value for stage in self.stages],
            "claims": [
                {
                    "text": claim.text,
                    "citations": list(claim.source_ids),
                    "confidence": claim.confidence,
                    "inference": claim.inference,
                }
                for claim in self.claims
            ],
            "uncertainties": list(self.uncertainties),
            "sources": [
                {
                    "id": source.source_id,
                    "title": source.title,
                    "url": source.url,
                    "excerpt": source.excerpt,
                    "observed_at": source.observed_at.isoformat(),
                    "published_at": (
                        source.published_at.isoformat()
                        if source.published_at is not None
                        else None
                    ),
                    "freshness": source.freshness.value,
                    "content_hash": source.content_hash,
                    "resolved_addresses": list(source.resolved_addresses),
                    "untrusted_web_content": True,
                    "prompt_injection_findings": [
                        finding.category
                        for finding in source.injection_findings
                    ],
                }
                for source in self.sources
            ],
        }
