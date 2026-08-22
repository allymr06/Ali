from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from hashlib import sha256
from typing import Mapping


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
    cache_hit: bool = False
    cached_at: datetime | None = None
    expires_at: datetime | None = None
    stale: bool = False

    def to_dict(self) -> dict[str, object]:
        return {
            "question": self.question,
            "created_at": self.created_at.isoformat(),
            "cache_hit": self.cache_hit,
            "cached_at": self.cached_at.isoformat() if self.cached_at is not None else None,
            "expires_at": self.expires_at.isoformat() if self.expires_at is not None else None,
            "stale": self.stale,
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
                    "prompt_injection_evidence": [
                        {
                            "category": finding.category,
                            "evidence_hash": finding.evidence_hash,
                        }
                        for finding in source.injection_findings
                    ],
                }
                for source in self.sources
            ],
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, object]) -> ResearchReport:
        """Deserialize a report without trusting executable or loosely typed data."""

        if not isinstance(payload, Mapping):
            raise ValueError("Research report payload must be an object.")

        def text(value: object, name: str, *, allow_empty: bool = False) -> str:
            if not isinstance(value, str) or (not allow_empty and not value.strip()):
                raise ValueError(f"{name} must be a non-empty string.")
            return value

        def sequence(value: object, name: str, *, maximum: int) -> list[object]:
            if not isinstance(value, list) or len(value) > maximum:
                raise ValueError(f"{name} must be a bounded list.")
            return value

        def timestamp(value: object, name: str, *, optional: bool = False) -> datetime | None:
            if optional and value is None:
                return None
            raw = text(value, name)
            try:
                parsed = datetime.fromisoformat(raw)
            except ValueError as exc:
                raise ValueError(f"{name} must be an ISO-8601 timestamp.") from exc
            if parsed.tzinfo is None:
                raise ValueError(f"{name} must include a timezone.")
            return parsed.astimezone(UTC)

        def boolean(value: object, name: str, *, default: bool = False) -> bool:
            if value is None:
                return default
            if not isinstance(value, bool):
                raise ValueError(f"{name} must be a boolean.")
            return value

        sources: list[ResearchSource] = []
        for index, raw_source in enumerate(sequence(payload.get("sources"), "sources", maximum=20)):
            if not isinstance(raw_source, Mapping):
                raise ValueError(f"sources[{index}] must be an object.")
            raw_findings = raw_source.get("prompt_injection_evidence")
            findings: list[InjectionFinding] = []
            if raw_findings is not None:
                for finding_index, raw_finding in enumerate(
                    sequence(raw_findings, "prompt_injection_evidence", maximum=50)
                ):
                    if not isinstance(raw_finding, Mapping):
                        raise ValueError(
                            f"prompt_injection_evidence[{finding_index}] must be an object."
                        )
                    findings.append(
                        InjectionFinding(
                            category=text(raw_finding.get("category"), "finding.category"),
                            evidence_hash=text(
                                raw_finding.get("evidence_hash"), "finding.evidence_hash"
                            ),
                        )
                    )
            else:
                for category in sequence(
                    raw_source.get("prompt_injection_findings", []),
                    "prompt_injection_findings",
                    maximum=50,
                ):
                    normalized_category = text(category, "finding.category")
                    findings.append(
                        InjectionFinding(
                            category=normalized_category,
                            evidence_hash=sha256(normalized_category.encode("utf-8")).hexdigest(),
                        )
                    )
            addresses = tuple(
                text(value, "resolved_address")
                for value in sequence(
                    raw_source.get("resolved_addresses", []),
                    "resolved_addresses",
                    maximum=32,
                )
            )
            sources.append(
                ResearchSource(
                    source_id=text(raw_source.get("id"), "source.id"),
                    title=text(raw_source.get("title"), "source.title", allow_empty=True),
                    url=text(raw_source.get("url"), "source.url"),
                    excerpt=text(raw_source.get("excerpt"), "source.excerpt", allow_empty=True),
                    observed_at=timestamp(raw_source.get("observed_at"), "source.observed_at"),
                    published_at=timestamp(
                        raw_source.get("published_at"), "source.published_at", optional=True
                    ),
                    freshness=Freshness(text(raw_source.get("freshness"), "source.freshness")),
                    content_hash=text(raw_source.get("content_hash"), "source.content_hash"),
                    resolved_addresses=addresses,
                    injection_findings=tuple(findings),
                )
            )

        claims: list[ResearchClaim] = []
        for index, raw_claim in enumerate(sequence(payload.get("claims"), "claims", maximum=200)):
            if not isinstance(raw_claim, Mapping):
                raise ValueError(f"claims[{index}] must be an object.")
            confidence = raw_claim.get("confidence")
            if isinstance(confidence, bool) or not isinstance(confidence, (int, float)):
                raise ValueError("claim.confidence must be a number.")
            claims.append(
                ResearchClaim(
                    text=text(raw_claim.get("text"), "claim.text"),
                    source_ids=tuple(
                        text(value, "claim.citation")
                        for value in sequence(
                            raw_claim.get("citations"), "claim.citations", maximum=20
                        )
                    ),
                    confidence=float(confidence),
                    inference=boolean(raw_claim.get("inference"), "claim.inference"),
                )
            )

        stages = tuple(
            ResearchStage(text(value, "stage"))
            for value in sequence(payload.get("stages"), "stages", maximum=20)
        )
        uncertainties = tuple(
            text(value, "uncertainty")
            for value in sequence(payload.get("uncertainties"), "uncertainties", maximum=100)
        )
        return cls(
            question=text(payload.get("question"), "question"),
            sources=tuple(sources),
            claims=tuple(claims),
            uncertainties=uncertainties,
            stages=stages,
            created_at=timestamp(payload.get("created_at"), "created_at"),
            cache_hit=boolean(payload.get("cache_hit"), "cache_hit"),
            cached_at=timestamp(payload.get("cached_at"), "cached_at", optional=True),
            expires_at=timestamp(payload.get("expires_at"), "expires_at", optional=True),
            stale=boolean(payload.get("stale"), "stale"),
        )
