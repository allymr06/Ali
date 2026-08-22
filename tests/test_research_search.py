from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest

from app.research.errors import SearchError
from app.research.models import WebDocument
from app.research.search import SearXNGSearchProvider
from app.research.url_policy import URLPolicy


class FakeFetcher:
    def __init__(self, payload: object) -> None:
        self.payload = payload
        self.urls: list[str] = []

    def fetch(self, url: str) -> WebDocument:
        self.urls.append(url)
        text = self.payload if isinstance(self.payload, str) else json.dumps(self.payload)
        return WebDocument.create(
            url=url,
            final_url=url,
            title="search",
            text=text,
            content_type="application/json",
            status=200,
            resolved_addresses=("93.184.216.34",),
            observed_at=datetime.now(UTC),
        )


def public_policy() -> URLPolicy:
    return URLPolicy(resolver=lambda _host, _port: ("93.184.216.34",))


def test_searxng_provider_builds_json_request_and_filters_results() -> None:
    fetcher = FakeFetcher(
        {
            "results": [
                {
                    "title": "One",
                    "url": "https://one.example/a",
                    "content": "Snippet",
                    "publishedDate": "2026-08-20T00:00:00Z",
                    "engines": ["engine-a"],
                },
                {"title": "Duplicate", "url": "https://one.example/a#part"},
                {"title": "Unsafe", "url": "http://127.0.0.1/private"},
            ]
        }
    )
    provider = SearXNGSearchProvider(
        "https://search.example", fetcher, public_policy()
    )

    hits = provider.search("bounded research", limit=5, time_range="month")

    assert len(hits) == 1
    assert hits[0].title == "One"
    assert hits[0].published_at is not None
    assert "format=json" in fetcher.urls[0]
    assert "safesearch=2" in fetcher.urls[0]
    assert "time_range=month" in fetcher.urls[0]


def test_searxng_provider_rejects_invalid_payload_and_parameters() -> None:
    provider = SearXNGSearchProvider(
        "https://search.example", FakeFetcher("not-json"), public_policy()
    )
    with pytest.raises(SearchError):
        provider.search("question", limit=5)
    with pytest.raises(ValueError):
        provider.search("", limit=5)
    with pytest.raises(ValueError):
        provider.search("question", limit=21)
    with pytest.raises(ValueError):
        provider.search("question", limit=5, time_range="week")


@pytest.mark.parametrize(
    "endpoint",
    [
        "file:///search",
        "https://user:secret@search.example",
        "https://search.example?mode=unsafe",
    ],
)
def test_searxng_provider_rejects_invalid_endpoint(endpoint: str) -> None:
    with pytest.raises(ValueError, match="endpoint"):
        SearXNGSearchProvider(endpoint, FakeFetcher({}), public_policy())
