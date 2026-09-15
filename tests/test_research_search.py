from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest

from app.research.errors import SearchError
from app.research.models import WebDocument
from app.research.search import (
    DuckDuckGoSearchProvider,
    GeminiGroundedSearch,
    SearXNGSearchProvider,
)
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


# ---------------------------------------------------------------------------
# Gemini grounded search
# ---------------------------------------------------------------------------


class FakeGeminiTransport:
    def __init__(self, payload: object) -> None:
        self.payload = payload
        self.calls: list[tuple[str, dict, dict]] = []

    def __call__(self, path: str, body: dict, headers: dict) -> object:
        self.calls.append((path, body, dict(headers)))
        return self.payload


GROUNDED_PAYLOAD = {
    "candidates": [
        {
            "content": {"parts": [{"text": "The exam is in March."}]},
            "groundingMetadata": {
                "webSearchQueries": ["tus 2026 tarihi"],
                "groundingChunks": [
                    {"web": {"uri": "https://redirect.example/one", "title": "osym.gov.tr"}},
                    {"web": {"uri": "https://redirect.example/two", "title": "resmigazete.gov.tr"}},
                    {"web": {"uri": "https://redirect.example/one", "title": "duplicate"}},
                    {"retrievedContext": {"uri": "https://ignored.example/none"}},
                    {"web": {"uri": "ftp://bad.example/x", "title": "unsafe scheme"}},
                ],
                "groundingSupports": [
                    {"segment": {"text": "The exam is on 15 March."}, "groundingChunkIndices": [0, 1]},
                    {"segment": {"text": "Applications open in January."}, "groundingChunkIndices": [0]},
                ],
            },
        }
    ]
}


def grounded_provider(payload: object) -> tuple[GeminiGroundedSearch, FakeGeminiTransport]:
    transport = FakeGeminiTransport(payload)
    provider = GeminiGroundedSearch(
        "unit-test-key", "gemini-3.5-flash-lite", public_policy(), transport=transport
    )
    return provider, transport


def test_grounded_search_turns_chunks_into_hits_and_keeps_the_key_out_of_the_url() -> None:
    provider, transport = grounded_provider(GROUNDED_PAYLOAD)

    hits = provider.search("TUS 2026 ne zaman?", limit=5)

    path, body, headers = transport.calls[0]
    assert path == "/v1beta/models/gemini-3.5-flash-lite:generateContent"
    assert "unit-test-key" not in path and "key" not in path
    assert headers["x-goog-api-key"] == "unit-test-key"
    assert body["tools"] == [{"google_search": {}}]
    assert "TUS 2026 ne zaman?" in body["contents"][0]["parts"][0]["text"]

    # Two unique safe URLs: the duplicate, the non-web chunk and the
    # unsafe scheme are all dropped without inventing anything.
    assert [hit.url for hit in hits] == [
        "https://redirect.example/one",
        "https://redirect.example/two",
    ]
    assert hits[0].title == "osym.gov.tr" and hits[0].engine == "google"
    assert "15 March" in hits[0].snippet and "January" in hits[0].snippet
    assert "January" not in hits[1].snippet

    assert len(provider.search("TUS 2026 ne zaman?", limit=1)) == 1


def test_grounded_search_passes_the_time_range_as_a_stated_preference() -> None:
    provider, transport = grounded_provider(GROUNDED_PAYLOAD)

    provider.search("guncel kilavuz", limit=3, time_range="month")

    assert "past month" in transport.calls[0][1]["contents"][0]["parts"][0]["text"]


def test_grounded_search_reports_an_ungrounded_answer_as_zero_hits() -> None:
    provider, _transport = grounded_provider(
        {"candidates": [{"content": {"parts": [{"text": "From memory."}]}}]}
    )
    assert provider.search("bir soru", limit=5) == ()

    empty, _ = grounded_provider({"candidates": []})
    assert empty.search("bir soru", limit=5) == ()


def test_grounded_search_rejects_bad_payloads_and_bad_parameters() -> None:
    provider, _transport = grounded_provider("not a dict")
    with pytest.raises(SearchError):
        provider.search("bir soru", limit=5)

    good, _ = grounded_provider(GROUNDED_PAYLOAD)
    with pytest.raises(ValueError):
        good.search("   ", limit=5)
    with pytest.raises(ValueError):
        good.search("x" * 501, limit=5)
    with pytest.raises(ValueError):
        good.search("soru", limit=0)
    with pytest.raises(ValueError):
        good.search("soru", limit=21)
    with pytest.raises(ValueError):
        good.search("soru", limit=5, time_range="week")

    with pytest.raises(ValueError, match="API key"):
        GeminiGroundedSearch("   ", "gemini-3.5-flash-lite", public_policy())
    with pytest.raises(ValueError, match="model"):
        GeminiGroundedSearch("key", "bad/model?x", public_policy())


# ---------------------------------------------------------------------------
# DuckDuckGo provider (the keyless default)
# ---------------------------------------------------------------------------


class FakeDDGTransport:
    def __init__(self, body: str, status: int = 200) -> None:
        self.body = body
        self.status = status
        self.calls: list[tuple[str, str]] = []

    def request(self, target, *, address, timeout_seconds, max_bytes, user_agent):
        from app.research.fetcher import TransportResponse

        self.calls.append((target.url, user_agent))
        return TransportResponse(self.status, {}, self.body.encode("utf-8"))


DDG_PAGE = """
<html><head><meta charset="utf-8"><title>q at DuckDuckGo</title></head><body>
<div class="result results_links web-result">
 <h2 class="result__title">
  <a rel="nofollow" class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fosym.gov.tr%2Ftakvim&amp;rut=aa">2026 &Ouml;SYM S&#305;nav Takvimi</a>
 </h2>
 <a class="result__snippet" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fosym.gov.tr%2Ftakvim&amp;rut=aa"><b>TUS</b> 15 Mart 2026 tarihinde yap&#305;lacak.</a>
</div>
<div class="result result--ad">
 <h2 class="result__title">
  <a rel="nofollow" class="result__a" href="https://duckduckgo.com/y.js?ad_domain=kurs.example">Reklam kursu</a>
 </h2>
 <a class="result__snippet" href="https://duckduckgo.com/y.js?ad_domain=kurs.example">Sponsorlu kurs.</a>
</div>
<div class="result results_links web-result">
 <h2 class="result__title">
  <a rel="nofollow" class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fosym.gov.tr%2Ftakvim&amp;rut=bb">Ayn&#305; sayfa tekrar</a>
 </h2>
</div>
<div class="result results_links web-result">
 <h2 class="result__title">
  <a rel="nofollow" class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fmemurlar.example%2Ftus&amp;rut=cc">TUS ba&#351;vuru rehberi</a>
 </h2>
 <a class="result__snippet" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fmemurlar.example%2Ftus&amp;rut=cc">Ba&#351;vurular Ocak ay&#305;nda.</a>
</div>
</body></html>
"""


def test_duckduckgo_provider_decodes_redirects_and_drops_ads() -> None:
    transport = FakeDDGTransport(DDG_PAGE)
    provider = DuckDuckGoSearchProvider(public_policy(), transport=transport)

    hits = provider.search("tus 2026 tarihi", limit=5)

    requested_url, user_agent = transport.calls[0]
    assert requested_url.startswith("https://html.duckduckgo.com/html/?")
    assert "q=tus+2026+tarihi" in requested_url and "kp=1" in requested_url
    assert "Mozilla/5.0" in user_agent, "a browser agent, or the endpoint serves a blank page"

    # The ad router link and the duplicate are gone; entities are decoded.
    assert [hit.url for hit in hits] == [
        "https://osym.gov.tr/takvim",
        "https://memurlar.example/tus",
    ]
    assert hits[0].title == "2026 ÖSYM Sınav Takvimi"
    assert hits[0].snippet == "TUS 15 Mart 2026 tarihinde yapılacak."
    assert hits[0].engine == "duckduckgo"

    assert len(provider.search("tus 2026 tarihi", limit=1)) == 1


def test_duckduckgo_provider_states_the_time_range_and_rejects_failures() -> None:
    transport = FakeDDGTransport(DDG_PAGE)
    provider = DuckDuckGoSearchProvider(public_policy(), transport=transport)

    provider.search("guncel kilavuz", limit=3, time_range="month")
    assert "df=m" in transport.calls[-1][0]

    empty = DuckDuckGoSearchProvider(
        public_policy(), transport=FakeDDGTransport("<html><body>challenge</body></html>")
    )
    assert empty.search("soru", limit=5) == ()

    failing = DuckDuckGoSearchProvider(
        public_policy(), transport=FakeDDGTransport(DDG_PAGE, status=503)
    )
    with pytest.raises(SearchError):
        failing.search("soru", limit=5)

    with pytest.raises(ValueError):
        provider.search("", limit=5)
    with pytest.raises(ValueError):
        provider.search("soru", limit=0)
    with pytest.raises(ValueError):
        provider.search("soru", limit=5, time_range="week")


def test_duckduckgo_redirect_decoder_is_strict() -> None:
    decode = DuckDuckGoSearchProvider._decode_redirect

    assert decode("//duckduckgo.com/l/?uddg=https%3A%2F%2Fa.example%2Fx&rut=1") == "https://a.example/x"
    assert decode("https://duckduckgo.com/l/?uddg=https%3A%2F%2Fa.example") == "https://a.example"
    assert decode("https://duckduckgo.com/y.js?ad_domain=x.example") is None
    assert decode("https://evil.example/l/?uddg=https%3A%2F%2Fa.example") is None
    assert decode("//duckduckgo.com/l/?rut=1") is None
    assert decode("") is None
