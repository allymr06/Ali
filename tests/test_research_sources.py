"""The places research looks beside the web: each source over a canned transport, and all of them at once."""
from __future__ import annotations

import gzip
import json
from datetime import UTC, datetime
from urllib.parse import quote, urlsplit

import pytest

from app.research.errors import SearchError
from app.research.fetcher import TransportResponse
from app.research.models import ResearchReport, SearchHit
from app.research.search import DuckDuckGoSearchProvider, SearchProvider
from app.research.service import ResearchService
from app.research.sources import (
    SOURCE_CATALOG,
    SOURCE_IDS,
    ArxivSearchProvider,
    GitHubSearchProvider,
    HackerNewsSearchProvider,
    MultiSourceSearchProvider,
    PubMedSearchProvider,
    SiteSearchProvider,
    StackOverflowSearchProvider,
    WikipediaSearchProvider,
    YouTubeSearchProvider,
    _APIClient,
    build_source_providers,
    normalize_site,
    parse_sources,
)
from app.research.sqlite_cache import SQLiteResearchCache
from app.research.url_policy import URLPolicy


def policy() -> URLPolicy:
    return URLPolicy(resolver=lambda _host, _port: ("93.184.216.34",))


class CannedTransport:
    """Answers by host and path prefix; records every URL it was asked for."""

    def __init__(self, answers: dict[str, object]) -> None:
        self.answers = answers
        self.urls: list[str] = []
        self.accepts: list[str | None] = []

    def request(self, target, *, address, timeout_seconds, max_bytes, user_agent, accept=None):
        self.urls.append(target.url)
        self.accepts.append(accept)
        parsed = urlsplit(target.url)
        for key, answer in self.answers.items():
            if (parsed.hostname + parsed.path).startswith(key) or target.url.startswith(key):
                if isinstance(answer, Exception):
                    raise answer
                if isinstance(answer, TransportResponse):
                    return answer
                body = answer if isinstance(answer, bytes) else json.dumps(answer).encode("utf-8")
                return TransportResponse(200, {"content-type": "application/json"}, body)
        return TransportResponse(404, {}, b"")


def client(answers: dict[str, object]) -> tuple[_APIClient, CannedTransport]:
    transport = CannedTransport(answers)
    return _APIClient(policy(), transport=transport, user_agent="JARVIS/test"), transport


def ddg_page(links: list[tuple[str, str, str]]) -> bytes:
    rows = "".join(
        f'<a class="result__a" href="//duckduckgo.com/l/?uddg={quote(url, safe="")}">{title}</a>'
        f'<a class="result__snippet" href="#">{snippet}</a>'
        for url, title, snippet in links
    )
    return f"<html><body>{rows}</body></html>".encode("utf-8")


# ---------------------------------------------------------------------------
# the catalogue
# ---------------------------------------------------------------------------


def test_the_catalogue_is_the_only_list_and_the_web_is_always_in_it() -> None:
    assert SOURCE_IDS[0] == "web" and "site" in SOURCE_IDS
    assert len({spec.id for spec in SOURCE_CATALOG}) == len(SOURCE_CATALOG)
    assert parse_sources("youtube, GitHub,,web") == ("youtube", "github", "web")
    assert parse_sources(["pubmed", "pubmed"]) == ("pubmed",)
    assert parse_sources(None) == () and parse_sources("") == ()
    with pytest.raises(ValueError, match="Unknown research source"):
        parse_sources("web,reddit")
    with pytest.raises(ValueError):
        parse_sources(42)


def test_a_site_is_a_bare_host_name_or_nothing() -> None:
    assert normalize_site(None) is None and normalize_site("  ") is None
    assert normalize_site("https://www.Example.org/path?q=1") == "example.org"
    assert normalize_site("docs.python.org") == "docs.python.org"
    for bad in ("localhost", "not a host", "http://", "127.0.0.1"):
        with pytest.raises(ValueError):
            normalize_site(bad)


# ---------------------------------------------------------------------------
# one source at a time
# ---------------------------------------------------------------------------


def test_github_repositories_carry_their_facts_as_evidence() -> None:
    api, transport = client({"api.github.com/search/repositories": {"items": [
        {"full_name": "octo/anatomy-atlas", "html_url": "https://github.com/octo/anatomy-atlas",
         "description": "3D <b>atlas</b> of the upper limb", "stargazers_count": 1240, "language": "Python",
         "pushed_at": "2026-09-01T10:00:00Z", "owner": {"login": "octo"}, "topics": ["anatomy", "3d"]},
        {"full_name": "evil/x", "html_url": "https://evil.example/x", "description": "not github"},
        {"full_name": "", "html_url": "https://github.com/none/none"},
    ]}})

    hits = GitHubSearchProvider(api).search("anatomy atlas", limit=5)

    assert len(hits) == 1
    hit = hits[0]
    assert hit.kind == "repo" and hit.source == "github" and hit.engine == "github"
    assert hit.evidence == "octo/anatomy-atlas: 3D atlas of the upper limb"
    assert dict(hit.meta) == {"stars": "1240", "language": "Python", "owner": "octo",
                              "updated": "2026-09-01", "topics": "anatomy, 3d"}
    assert hit.published_at == datetime(2026, 9, 1, 10, tzinfo=UTC)
    assert "sort=stars" in transport.urls[0] and "per_page=5" in transport.urls[0]


def test_youtube_videos_come_from_the_web_index_and_are_confirmed_by_oembed() -> None:
    web_transport = CannedTransport({"html.duckduckgo.com": TransportResponse(200, {}, ddg_page([
        ("https://www.youtube.com/watch?v=dQw4w9WgXcQ&t=3s", "Omuz anatomisi - ders", "Omuz kuşağı anlatımı"),
        ("https://youtu.be/abc123DEF45", "Kısa link", ""),
        ("https://www.youtube.com/watch?v=dQw4w9WgXcQ", "Aynı video yine", ""),
        ("https://www.youtube.com/channel/UCxyz", "Bir kanal sayfası", ""),
        ("https://example.org/not-youtube", "Başka site", ""),
    ]))})
    web = DuckDuckGoSearchProvider(policy(), transport=web_transport)
    answers: dict[str, object] = {}

    def oembed(video: str, title: str, author: str):
        answers[f"https://www.youtube.com/oembed?url={quote(f'https://www.youtube.com/watch?v={video}', safe='')}"] = {
            "title": title, "author_name": author, "thumbnail_url": f"https://i.ytimg.com/vi/{video}/hqdefault.jpg"}

    oembed("dQw4w9WgXcQ", "Omuz Anatomisi | Tam Ders", "Anatomi Kanalı")
    api, transport = client(answers)

    hits = YouTubeSearchProvider(web, api).search("omuz anatomisi", limit=5)

    assert [hit.url for hit in hits] == [
        "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
        "https://www.youtube.com/watch?v=abc123DEF45",
    ]
    confirmed, unconfirmed = hits
    assert confirmed.title == "Omuz Anatomisi | Tam Ders" and dict(confirmed.meta)["channel"] == "Anatomi Kanalı"
    assert dict(confirmed.meta)["confirmed"] == "yes" and confirmed.kind == "video"
    assert "Omuz kuşağı anlatımı" in confirmed.evidence
    # A video oEmbed could not confirm keeps the index's title and says so.
    assert unconfirmed.title == "Kısa link" and dict(unconfirmed.meta)["confirmed"] == "no"
    assert YouTubeSearchProvider.video_id("https://www.youtube.com/shorts/Zz9_yYx-abc") == "Zz9_yYx-abc"
    assert YouTubeSearchProvider.video_id("https://www.youtube.com/watch?v=<script>") is None
    assert "site%3Ayoutube.com" in web_transport.urls[0]


def test_wikipedia_asks_turkish_first_and_english_only_when_short() -> None:
    api, transport = client({
        "tr.wikipedia.org": {"query": {"search": [
            {"title": "Skapula", "snippet": '<span class="searchmatch">Skapula</span> ya da kürek kemiği', "timestamp": "2026-05-01T00:00:00Z"},
        ]}},
        "en.wikipedia.org": {"query": {"search": [
            {"title": "Scapula", "snippet": "The scapula, also known as the shoulder blade", "timestamp": "2026-06-01T00:00:00Z"},
            {"title": "Skapula", "snippet": "duplicate title, different language", "timestamp": "2026-06-01T00:00:00Z"},
        ]}},
    })

    hits = WikipediaSearchProvider(api).search("skapula", limit=3)

    assert [hit.url for hit in hits] == [
        "https://tr.wikipedia.org/wiki/Skapula",
        "https://en.wikipedia.org/wiki/Scapula",
        "https://en.wikipedia.org/wiki/Skapula",
    ]
    assert hits[0].evidence == "Skapula: Skapula ya da kürek kemiği"
    assert dict(hits[0].meta) == {"language": "tr"} and hits[0].kind == "encyclopedia"
    assert [urlsplit(url).hostname for url in transport.urls] == ["tr.wikipedia.org", "en.wikipedia.org"]

    enough, _ = client({"tr.wikipedia.org": {"query": {"search": [
        {"title": f"Madde {index}", "snippet": "x"} for index in range(3)]}}})
    assert len(WikipediaSearchProvider(enough).search("x", limit=3)) == 3


def test_pubmed_lists_ids_then_summaries_with_journal_and_year() -> None:
    api, transport = client({
        "eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi": {"esearchresult": {"idlist": ["41000001", "bad", "41000002"]}},
        "eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi": {"result": {
            "41000001": {"title": "Rotator cuff anatomy revisited", "source": "J Anat", "fulljournalname": "Journal of Anatomy",
                          "pubdate": "2025 Mar 12", "authors": [{"name": "Yılmaz A"}, {"name": "Kaya B"}, {"name": "Demir C"}, {"name": "Öz D"}]},
            "41000002": {"title": "", "source": "x"},
        }},
    })

    hits = PubMedSearchProvider(api).search("rotator cuff", limit=5)

    assert len(hits) == 1
    hit = hits[0]
    assert hit.url == "https://pubmed.ncbi.nlm.nih.gov/41000001/" and hit.kind == "paper"
    assert hit.evidence == "Rotator cuff anatomy revisited. Yılmaz A, Kaya B, Demir C ve diğerleri. Journal of Anatomy (2025 Mar 12)"
    assert dict(hit.meta)["year"] == "2025" and dict(hit.meta)["pmid"] == "41000001"
    assert hit.published_at == datetime(2025, 3, 12, tzinfo=UTC)
    assert PubMedSearchProvider.published("2024") == datetime(2024, 1, 1, tzinfo=UTC)
    assert PubMedSearchProvider.published("") is None
    assert "id=41000001%2C41000002" in transport.urls[1]


def test_arxiv_entries_are_read_from_the_atom_feed() -> None:
    feed = """<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom">
    <entry><id>http://arxiv.org/abs/2609.01234v1</id><title> Deep   learning for
    shoulder MRI </title><summary>We study &amp; report.</summary><published>2026-09-02T00:00:00Z</published>
    <author><name>A. Author</name></author><author><name>B. Author</name></author></entry>
    <entry><id></id><title>broken</title></entry>
    </feed>"""
    api, _ = client({"export.arxiv.org": TransportResponse(200, {"content-type": "application/atom+xml"}, feed.encode("utf-8"))})

    hits = ArxivSearchProvider(api).search("shoulder MRI", limit=3)

    assert len(hits) == 1
    assert hits[0].url == "https://arxiv.org/abs/2609.01234v1"
    assert hits[0].title == "Deep learning for shoulder MRI"
    assert hits[0].evidence == "Deep learning for shoulder MRI. We study & report."
    assert dict(hits[0].meta) == {"authors": "A. Author, B. Author", "year": "2026"}


def test_stack_overflow_answers_are_unpacked_from_gzip() -> None:
    payload = {"items": [
        {"title": "How do I &quot;pin&quot; a version?", "link": "https://stackoverflow.com/questions/1/pin",
         "score": 42, "answer_count": 3, "is_answered": True, "creation_date": 1700000000, "tags": ["python", "pip"]},
        {"title": "Unsafe link", "link": "http://stackoverflow.com/insecure"},
    ]}
    body = gzip.compress(json.dumps(payload).encode("utf-8"))
    api, _ = client({"api.stackexchange.com": TransportResponse(200, {"content-encoding": "gzip"}, body)})

    hits = StackOverflowSearchProvider(api).search("pin version", limit=5)

    assert len(hits) == 1
    assert hits[0].title == 'How do I "pin" a version?'
    assert hits[0].evidence == 'How do I "pin" a version?. 3 cevap (kabul edilmiş cevap var), puan 42'
    assert dict(hits[0].meta) == {"score": "42", "answers": "3", "answered": "yes", "tags": "python, pip"}
    assert hits[0].published_at == datetime.fromtimestamp(1700000000, tz=UTC)


def test_hacker_news_stories_fall_back_to_the_discussion_link() -> None:
    api, _ = client({"hn.algolia.com": {"hits": [
        {"title": "Show HN: an anatomy atlas", "url": "https://atlas.example/", "points": 120, "num_comments": 40,
         "created_at": "2026-08-10T12:00:00Z", "objectID": "1001", "author": "octo"},
        {"title": "Ask HN: anatomy resources?", "url": None, "points": 5, "num_comments": 2,
         "created_at": "2026-08-11T12:00:00Z", "objectID": "1002", "story_text": "Looking for <i>free</i> atlases."},
    ]}})

    hits = HackerNewsSearchProvider(api).search("anatomy atlas", limit=5)

    assert [hit.url for hit in hits] == ["https://atlas.example/", "https://news.ycombinator.com/item?id=1002"]
    assert hits[0].evidence == "Show HN: an anatomy atlas. 120 puan, 40 yorum"
    assert dict(hits[0].meta)["discussion"] == "https://news.ycombinator.com/item?id=1001"
    assert hits[1].evidence == "Ask HN: anatomy resources?. Looking for free atlases.. 5 puan, 2 yorum"


def test_a_site_search_keeps_only_that_host() -> None:
    transport = CannedTransport({"html.duckduckgo.com": TransportResponse(200, {}, ddg_page([
        ("https://docs.python.org/3/library/gzip.html", "gzip", "Support for gzip files"),
        ("https://sub.docs.python.org/x", "sub", ""),
        ("https://python.org/other", "other host", ""),
    ]))})
    web = DuckDuckGoSearchProvider(policy(), transport=transport)

    hits = SiteSearchProvider(web, "https://docs.python.org/").search("gzip", limit=5)

    assert [hit.url for hit in hits] == ["https://docs.python.org/3/library/gzip.html", "https://sub.docs.python.org/x"]
    assert hits[0].source == "site" and dict(hits[0].meta) == {"site": "docs.python.org"}
    assert "site%3Adocs.python.org+gzip" in transport.urls[0]


def test_a_failed_source_is_named_by_its_status_when_there_is_one() -> None:
    from app.research.sources import failure_reason

    assert failure_reason(SearchError("Search page returned HTTP 202.")) == "HTTP 202"
    assert failure_reason(SearchError("down")) == "SearchError"
    wrapped = SearchError("The source endpoint could not be reached.")
    wrapped.__cause__ = RuntimeError("HTTP 503 from upstream")
    assert failure_reason(wrapped) == "HTTP 503"


def test_duckduckgo_waits_once_through_its_throttle(monkeypatch) -> None:
    from app.research import search as search_module

    answers = [TransportResponse(202, {}, b"<html>challenge</html>"), TransportResponse(200, {}, ddg_page([
        ("https://docs.example/one", "One", "first result")]))]
    calls: list[str] = []

    class Throttling:
        def request(self, target, *, address, timeout_seconds, max_bytes, user_agent, accept=None):
            calls.append(target.url)
            return answers.pop(0)

    naps: list[float] = []
    monkeypatch.setattr(search_module.time, "sleep", lambda seconds: naps.append(seconds))
    provider = DuckDuckGoSearchProvider(policy(), transport=Throttling())
    hits = provider.search("q", limit=3)
    assert [hit.url for hit in hits] == ["https://docs.example/one"]
    assert len(calls) == 2 and naps == [DuckDuckGoSearchProvider._THROTTLE_PAUSE_SECONDS]

    answers.extend([TransportResponse(202, {}, b""), TransportResponse(202, {}, b"")])
    with pytest.raises(SearchError, match="HTTP 202"):
        provider.search("q", limit=3)


def test_a_non_200_answer_or_bad_json_is_a_search_error_not_evidence() -> None:
    api, _ = client({"api.github.com": TransportResponse(403, {}, b"rate limited")})
    with pytest.raises(SearchError, match="HTTP 403"):
        GitHubSearchProvider(api).search("x", limit=3)
    api, _ = client({"hn.algolia.com": TransportResponse(200, {}, b"<html>not json")})
    with pytest.raises(SearchError, match="invalid JSON"):
        HackerNewsSearchProvider(api).search("x", limit=3)


# ---------------------------------------------------------------------------
# all of them at once
# ---------------------------------------------------------------------------


class FixedProvider(SearchProvider):
    def __init__(self, source: str, urls: list[str], *, error: Exception | None = None) -> None:
        self.source = source
        self.urls = urls
        self.error = error
        self.calls: list[int] = []

    def search(self, query, *, limit, time_range=None):
        self.calls.append(limit)
        if self.error is not None:
            raise self.error
        return tuple(
            SearchHit(f"{self.source} {index}", url, engine=self.source, kind="repo" if self.source == "github" else "web",
                      source=self.source, evidence=f"{self.source} says {index}" if self.source != "web" else "")
            for index, url in enumerate(self.urls[:limit])
        )


def test_sources_are_searched_together_interleaved_and_one_failure_is_named() -> None:
    web = FixedProvider("web", ["https://w.example/1", "https://w.example/2", "https://w.example/3"])
    github = FixedProvider("github", ["https://github.com/a/b", "https://w.example/1"])
    pubmed = FixedProvider("pubmed", [], error=SearchError("down"))
    multi = MultiSourceSearchProvider({"web": web, "github": github, "pubmed": pubmed})

    result = multi.search_sources("q", limit=4, sources=("web", "github", "pubmed"))

    assert [hit.url for hit in result.hits] == [
        "https://w.example/1", "https://github.com/a/b", "https://w.example/2", "https://w.example/3",
    ], "round-robin by source, duplicates dropped, capped at the limit"
    assert result.failures == (("pubmed", "SearchError"),)
    assert web.calls == [3] and github.calls == [3]
    # The plain contract stays: the tool gets the web alone unless asked.
    assert [hit.source for hit in multi.search("q", limit=2)] == ["web", "web"]
    with pytest.raises(ValueError, match="not enabled"):
        multi.search_sources("q", limit=2, sources=("web", "arxiv"))


def test_the_site_source_needs_a_host_and_the_web_index() -> None:
    web = FixedProvider("web", ["https://w.example/1"])
    multi = MultiSourceSearchProvider({"web": web})
    with pytest.raises(ValueError, match="host name"):
        multi.search_sources("q", limit=2, sources=("site",))
    with pytest.raises(SearchError, match="web source"):
        multi.search_sources("q", limit=2, sources=("site",), site="example.org")
    assert multi.available == ("web",)


def test_build_wires_exactly_the_enabled_sources() -> None:
    web = FixedProvider("web", [])
    ddg = DuckDuckGoSearchProvider(policy(), transport=CannedTransport({}))
    multi = build_source_providers(
        policy(), web=web, duckduckgo=ddg, enabled=("web", "github", "site", "pubmed"),
        timeout_seconds=5.0, max_response_bytes=100_000, user_agent="JARVIS/test",
    )
    assert multi.available == ("web", "github", "pubmed", "site")


# ---------------------------------------------------------------------------
# the report
# ---------------------------------------------------------------------------


class NoFetch:
    def fetch(self, url):  # pragma: no cover - a fetch here is the failure
        raise AssertionError(f"evidence-bearing hits are never fetched: {url}")


def test_evidence_bearing_hits_are_cited_without_a_fetch_and_failures_are_named() -> None:
    github = FixedProvider("github", ["https://github.com/a/b", "https://github.com/c/d"])
    hackernews = FixedProvider("hackernews", [], error=SearchError("down"))
    web = FixedProvider("web", [])
    service = ResearchService(
        search_provider=MultiSourceSearchProvider({"web": web, "github": github, "hackernews": hackernews}),
        fetcher=NoFetch(),
        max_sources=5,
    )

    report = service.research("anatomy atlas", max_sources=2, sources="github,hackernews")

    assert [source.source_id for source in report.sources] == ["S1", "S2"]
    assert {source.source for source in report.sources} == {"github"}
    assert report.sources[0].kind == "repo" and report.sources[0].excerpt == "github says 0"
    assert report.sources[0].resolved_addresses == ()
    assert "Source Hacker News was unavailable (SearchError)." in report.uncertainties
    assert all(claim.source_ids for claim in report.claims)
    payload = report.to_dict()
    assert payload["sources"][0]["kind"] == "repo" and payload["sources"][0]["source"] == "github"
    restored = ResearchReport.from_dict(payload)
    assert restored.sources[0].kind == "repo" and restored.sources[0].meta == report.sources[0].meta


def test_a_single_web_backend_refuses_other_sources_honestly() -> None:
    service = ResearchService(search_provider=FixedProvider("web", []), fetcher=NoFetch(), max_sources=3)
    with pytest.raises(ValueError, match="web only"):
        service.research("q", sources="youtube")
    with pytest.raises(ValueError, match="web only"):
        service.research("q", site="example.org")


def test_the_cache_key_tells_a_youtube_question_from_a_web_question(tmp_path) -> None:
    plain = SQLiteResearchCache.key_for("q", 3, None)
    assert SQLiteResearchCache.key_for("q", 3, None, sources=("web",)) == plain, "the old key survives"
    assert SQLiteResearchCache.key_for("q", 3, None, sources=("youtube",)) != plain
    assert SQLiteResearchCache.key_for("q", 3, None, site="example.org") != plain
    github = FixedProvider("github", ["https://github.com/a/b"])
    web = FixedProvider("web", [])
    service = ResearchService(
        search_provider=MultiSourceSearchProvider({"web": web, "github": github}),
        fetcher=NoFetch(),
        max_sources=3,
        cache=SQLiteResearchCache(str(tmp_path / "research.sqlite3")),
    )
    first = service.research("q", max_sources=2, sources="github")
    again = service.research("q", max_sources=2, sources="github")
    assert again.cache_hit is True and first.cache_hit is False
    assert len(github.calls) == 1, "the second answer came from the cache"


def test_the_tool_accepts_a_source_list_and_a_site() -> None:
    from app.security.permissions import PermissionEngine
    from app.tools.executor import ToolExecutor

    github = FixedProvider("github", ["https://github.com/a/b"])
    service = ResearchService(
        search_provider=MultiSourceSearchProvider({"web": FixedProvider("web", []), "github": github}),
        fetcher=NoFetch(),
        max_sources=3,
    )
    executor = ToolExecutor(PermissionEngine())
    service.register_tools(executor)
    contract = json.dumps(executor.get_tool_contracts(names={"research_web"})[0])
    assert '"sources"' in contract and '"site"' in contract, "the model can ask for a source list and a host"
    result = executor.execute("research_web", parameters={"query": "q", "max_sources": 2, "sources": "github"})
    assert result.status.value == "success"
    assert result.data["sources"][0]["source"] == "github"


def test_arxiv_says_it_takes_atom_and_json_sources_ask_for_json() -> None:
    feed = "<feed><entry><id>http://arxiv.org/abs/1</id><title>T</title><summary>S</summary><published>2026-01-01T00:00:00Z</published></entry></feed>"
    api, transport = client({"export.arxiv.org": TransportResponse(200, {}, feed.encode("utf-8")),
                             "hn.algolia.com": {"hits": []}})
    ArxivSearchProvider(api).search("t", limit=1)
    HackerNewsSearchProvider(api).search("t", limit=1)
    assert transport.accepts[0].startswith("application/atom+xml"), "arXiv answers 406 to a client that does not say so"
    assert transport.accepts[1] == "application/json"


def test_the_bridge_clamps_the_source_count_to_what_the_service_allows() -> None:
    from app.ui.nova.shell import MAX_RESEARCH_SOURCES, research_source_limit

    assert research_source_limit(8, 5) == 5, "asking for more than the service allows gives what it allows, not an error"
    assert research_source_limit(3, 5) == 3
    assert research_source_limit(50, 10) == MAX_RESEARCH_SOURCES
    assert research_source_limit("abc", 10) == 5 and research_source_limit(8, None) == 8
    assert research_source_limit(0, 10) == 1


def test_the_research_tls_context_verifies_and_takes_the_bundle_when_present(monkeypatch) -> None:
    import ssl

    from app.research import fetcher

    monkeypatch.setattr(fetcher, "_ssl_context", None)
    context = fetcher.research_ssl_context()
    assert context.verify_mode is ssl.CERT_REQUIRED and context.check_hostname is True
    assert fetcher.research_ssl_context() is context, "built once per process"
    try:
        import certifi
    except ImportError:  # pragma: no cover - the bundle is optional
        certifi = None
    if certifi is not None:
        assert context.cert_store_stats()["x509_ca"] > 0


def test_the_catalogue_sits_beside_the_configured_web_backend_not_over_it() -> None:
    web = FixedProvider("web", ["https://w.example/1"])
    github = FixedProvider("github", ["https://github.com/a/b"])
    catalogue = MultiSourceSearchProvider({"web": web, "github": github})
    service = ResearchService(search_provider=web, sources=catalogue, fetcher=NoFetch(), max_sources=3)

    assert service.search_provider is web, "what the settings chose is still the web backend"
    report = service.research("q", max_sources=2, sources="github")
    assert [source.source for source in report.sources] == ["github"]
    assert catalogue.available == ("web", "github")
