from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.core.models import ToolExecutionStatus
from app.research.citations import validate_citations
from app.research.errors import CitationIntegrityError, FetchError
from app.research.models import (
    Freshness,
    ResearchClaim,
    ResearchReport,
    ResearchStage,
    SearchHit,
    WebDocument,
)
from app.research.search import SearchProvider
from app.research.service import ResearchService
from app.security.permissions import PermissionEngine
from app.tools.executor import ToolExecutor


class FakeSearch(SearchProvider):
    def __init__(self, hits: tuple[SearchHit, ...]) -> None:
        self.hits = hits
        self.calls: list[tuple[str, int, str | None]] = []

    def search(self, query: str, *, limit: int, time_range: str | None = None):
        self.calls.append((query, limit, time_range))
        return self.hits[:limit]


class FakeFetcher:
    def __init__(self, documents: dict[str, WebDocument | Exception]) -> None:
        self.documents = documents

    def fetch(self, url: str) -> WebDocument:
        value = self.documents[url]
        if isinstance(value, Exception):
            raise value
        return value


def document(url: str, text: str, *, days_old: int | None = 1) -> WebDocument:
    now = datetime.now(UTC)
    return WebDocument.create(
        url=url,
        final_url=url,
        title=f"Title {url}",
        text=text,
        content_type="text/plain",
        status=200,
        resolved_addresses=("93.184.216.34",),
        published_at=(now - timedelta(days=days_old) if days_old is not None else None),
        observed_at=now,
    )


def make_service() -> ResearchService:
    urls = ("https://a.example/", "https://b.example/", "https://bad.example/")
    return ResearchService(
        search_provider=FakeSearch(tuple(SearchHit(url, url) for url in urls)),
        fetcher=FakeFetcher(
            {
                urls[0]: document(
                    urls[0], "The release happened in August 2026. It was confirmed officially."
                ),
                urls[1]: document(
                    urls[1], "The release happened in August 2026. Independent reporting confirms it."
                ),
                urls[2]: FetchError("blocked"),
            }
        ),
        max_sources=3,
        max_concurrency=2,
    )


def test_research_service_runs_full_workflow_and_preserves_provenance() -> None:
    report = make_service().research("When did the release happen?", max_sources=3)

    assert report.stages == tuple(ResearchStage)
    assert len(report.sources) == 2
    assert all(source.content_hash for source in report.sources)
    assert all(source.freshness is Freshness.CURRENT for source in report.sources)
    assert any(len(claim.source_ids) == 2 for claim in report.claims)
    assert any("could not be safely collected" in item for item in report.uncertainties)
    assert report.to_dict()["sources"][0]["untrusted_web_content"] is True


def test_research_service_marks_unknown_freshness_and_single_domain_uncertainty() -> None:
    url = "https://a.example/one"
    service = ResearchService(
        FakeSearch((SearchHit("one", url),)),
        FakeFetcher({url: document(url, "Evidence sentence.", days_old=None)}),
    )

    report = service.research("Evidence?", max_sources=1)

    assert report.sources[0].freshness is Freshness.UNKNOWN
    assert len(report.uncertainties) == 2


def test_research_service_validates_bounds() -> None:
    service = make_service()
    with pytest.raises(ValueError):
        service.research("")
    with pytest.raises(ValueError):
        service.research("q", max_sources=4)
    with pytest.raises(ValueError):
        ResearchService(service.search_provider, service.fetcher, max_sources=11)


def test_citation_validator_rejects_unknown_or_missing_source() -> None:
    base = ResearchReport("q", (), (ResearchClaim("claim", ("S9",), 0.5),), (), ())
    with pytest.raises(CitationIntegrityError, match="unknown"):
        validate_citations(base)
    missing = ResearchReport("q", (), (ResearchClaim("claim", (), 0.5),), (), ())
    with pytest.raises(CitationIntegrityError, match="requires"):
        validate_citations(missing)


@pytest.mark.asyncio
async def test_research_tool_exposes_strict_untrusted_contract_and_report() -> None:
    service = make_service()
    executor = ToolExecutor(PermissionEngine())
    service.register_tools(executor)

    contract = executor.get_contract_objects(names={"research_web"})[0]
    result = await executor.execute(
        "research_web",
        parameters={"query": "When did the release happen?", "max_sources": 3},
    )

    assert contract.definition.risk_level.value == "read_only"
    assert "untrusted-content" in contract.definition.tags
    assert contract.definition.metadata["automatic_instruction_execution"] is False
    assert result.status is ToolExecutionStatus.SUCCESS
    assert result.verified is True
    assert result.data["claims"]
