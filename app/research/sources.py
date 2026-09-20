"""Where research looks: the open web and the places that have their own doors.

Every source here is keyless and read-only. Each speaks to a public JSON or
Atom endpoint through the same pinned transport and URL policy as every
other fetch, so a source can never reach a private address, follow a
redirect on its own, or exceed the byte budget. What an endpoint returns
is evidence about a result - a repository's description, a paper's
abstract, a video's author - and it is carried on the hit as
``evidence`` so the report can cite it without downloading a page that
would say less. Web hits carry no evidence and are fetched as before.

The catalogue below is the only list of sources; the page, the settings
and the report all read it.
"""
from __future__ import annotations

import gzip
import html
import json
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import UTC, datetime
from html.parser import HTMLParser
from math import ceil
from typing import Mapping
from urllib.parse import parse_qs, quote, urlencode, urlsplit

from app.research.errors import ContentRejectedError, FetchError, SearchError, UnsafeURLError
from app.research.fetcher import PinnedHTTPTransport, WebTransport
from app.research.models import SearchHit
from app.research.search import DuckDuckGoSearchProvider, SearchProvider
from app.research.url_policy import URLPolicy


@dataclass(frozen=True, slots=True)
class SourceSpec:
    id: str
    label: str
    kind: str
    description: str


SOURCE_CATALOG: tuple[SourceSpec, ...] = (
    SourceSpec("web", "Web", "web", "Genel web araması (DuckDuckGo)."),
    SourceSpec("youtube", "YouTube", "video", "Videolar; başlık ve kanal videonun kendisinden doğrulanır."),
    SourceSpec("github", "GitHub", "repo", "Depolar; yıldız, dil ve son güncelleme ile."),
    SourceSpec("wikipedia", "Wikipedia", "encyclopedia", "Önce Türkçe, yetmezse İngilizce maddeler."),
    SourceSpec("pubmed", "PubMed", "paper", "Tıp literatürü; dergi, yazarlar ve yıl ile."),
    SourceSpec("arxiv", "arXiv", "paper", "Ön baskılar; özetleriyle."),
    SourceSpec("stackoverflow", "Stack Overflow", "discussion", "Soru ve cevaplar; puan ve cevap sayısıyla."),
    SourceSpec("hackernews", "Hacker News", "discussion", "Teknoloji tartışmaları; puan ve yorum sayısıyla."),
    SourceSpec("site", "Belirli site", "web", "Verdiğin alan adında arar."),
)
SOURCE_IDS: tuple[str, ...] = tuple(spec.id for spec in SOURCE_CATALOG)
DEFAULT_TOOL_SOURCES: tuple[str, ...] = ("web",)

_SITE_PATTERN = re.compile(r"^(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,24}$")
_TAG = re.compile(r"<[^>]+>")
_SPACE = re.compile(r"\s+")
_YOUTUBE_ID = re.compile(r"^[A-Za-z0-9_-]{6,20}$")


def source_spec(source_id: str) -> SourceSpec:
    for spec in SOURCE_CATALOG:
        if spec.id == source_id:
            return spec
    raise ValueError(f"Unknown research source: {source_id}")


def normalize_site(value: str | None) -> str | None:
    """A bare host name, or None: no scheme, no path, no tricks."""
    if value is None:
        return None
    candidate = value.strip().casefold()
    if not candidate:
        return None
    if "://" in candidate:
        candidate = urlsplit(candidate).hostname or ""
    candidate = candidate.split("/", 1)[0].strip().rstrip(".")
    if candidate.startswith("www."):
        candidate = candidate[4:]
    if not _SITE_PATTERN.match(candidate):
        raise ValueError("Site must be a plain host name such as example.org.")
    return candidate


def parse_sources(value: object) -> tuple[str, ...]:
    """A comma list or sequence of catalogue ids, in the order given, deduplicated."""
    if value is None:
        return ()
    if isinstance(value, str):
        raw = [part.strip().casefold() for part in value.split(",")]
    elif isinstance(value, (list, tuple)):
        raw = [str(part).strip().casefold() for part in value]
    else:
        raise ValueError("Sources must be a comma list or a list of source ids.")
    chosen: list[str] = []
    for item in raw:
        if not item:
            continue
        if item not in SOURCE_IDS:
            raise ValueError(f"Unknown research source: {item}")
        if item not in chosen:
            chosen.append(item)
    return tuple(chosen)


def _clean(text: object, limit: int) -> str:
    return _SPACE.sub(" ", html.unescape(_TAG.sub(" ", str(text or "")))).strip()[:limit]


def _iso(value: object) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    raw = value.strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


class _APIClient:
    """One bounded GET against a public endpoint, decoded as text.

    The transport refuses nothing by content type, so this is where an
    endpoint that insists on gzip (Stack Exchange does) is unpacked -
    within the same byte budget - and where a non-200 answer becomes a
    SearchError instead of evidence.
    """

    def __init__(
        self,
        policy: URLPolicy,
        *,
        transport: WebTransport | None = None,
        timeout_seconds: float = 10.0,
        max_response_bytes: int = 2_000_000,
        user_agent: str = "JARVIS/0.1",
    ) -> None:
        if timeout_seconds <= 0 or max_response_bytes <= 0:
            raise ValueError("Source client limits must be positive.")
        self._policy = policy
        self._transport = transport or PinnedHTTPTransport()
        self._timeout = timeout_seconds
        self._max_bytes = max_response_bytes
        self._user_agent = user_agent

    def text(self, url: str, *, accept: str | None = None) -> str:
        try:
            target = self._policy.validate(url)
        except UnsafeURLError as exc:
            raise SearchError("The source endpoint is not allowed.") from exc
        request = {
            "address": target.addresses[0],
            "timeout_seconds": self._timeout,
            "max_bytes": self._max_bytes,
            "user_agent": self._user_agent,
        }
        if accept:
            # arXiv answers 406 unless the client says it takes Atom; the
            # default Accept names only what the page fetcher can read.
            request["accept"] = accept
        try:
            response = self._transport.request(target, **request)
        except (FetchError, ContentRejectedError) as exc:
            raise SearchError("The source endpoint could not be reached.") from exc
        if response.status != 200:
            raise SearchError(f"The source endpoint returned HTTP {response.status}.")
        body = response.body
        encoding = {k.casefold(): v for k, v in response.headers.items()}.get("content-encoding", "")
        if "gzip" in encoding.casefold():
            try:
                body = gzip.decompress(body)
            except (OSError, EOFError) as exc:
                raise SearchError("The source endpoint sent unreadable compressed data.") from exc
            if len(body) > self._max_bytes:
                raise SearchError("The source endpoint answer exceeded the byte limit.")
        return body.decode("utf-8", "replace")

    def json(self, url: str) -> object:
        try:
            return json.loads(self.text(url, accept="application/json"))
        except json.JSONDecodeError as exc:
            raise SearchError("The source endpoint returned invalid JSON.") from exc


def _check(query: str, limit: int) -> str:
    normalized = " ".join(query.split())
    if not normalized or len(normalized) > 500:
        raise ValueError("Search query must contain between 1 and 500 characters.")
    if not 1 <= limit <= 20:
        raise ValueError("Search result limit must be between 1 and 20.")
    return normalized


class GitHubSearchProvider(SearchProvider):
    """Repositories from GitHub's public search, most starred first."""

    _ENDPOINT = "https://api.github.com/search/repositories"

    def __init__(self, client: _APIClient) -> None:
        self._client = client

    def search(self, query: str, *, limit: int, time_range: str | None = None) -> tuple[SearchHit, ...]:
        normalized = _check(query, limit)
        params = {"q": normalized, "sort": "stars", "order": "desc", "per_page": str(limit)}
        payload = self._client.json(f"{self._ENDPOINT}?{urlencode(params)}")
        items = payload.get("items") if isinstance(payload, Mapping) else None
        hits: list[SearchHit] = []
        for item in items or []:
            if not isinstance(item, Mapping):
                continue
            url = str(item.get("html_url") or "")
            name = _clean(item.get("full_name"), 200)
            if not url.startswith("https://github.com/") or not name:
                continue
            description = _clean(item.get("description"), 600)
            language = _clean(item.get("language"), 40)
            stars = item.get("stargazers_count")
            owner = item.get("owner") if isinstance(item.get("owner"), Mapping) else {}
            topics = item.get("topics") if isinstance(item.get("topics"), list) else []
            meta = [
                ("stars", str(int(stars)) if isinstance(stars, int) and not isinstance(stars, bool) else ""),
                ("language", language),
                ("owner", _clean(owner.get("login"), 80)),
                ("updated", str(item.get("pushed_at") or "")[:10]),
                ("topics", ", ".join(_clean(t, 30) for t in topics[:6])),
            ]
            evidence = f"{name}: {description}" if description else name
            hits.append(
                SearchHit(
                    title=name,
                    url=url,
                    snippet=description,
                    published_at=_iso(item.get("pushed_at")),
                    engine="github",
                    kind="repo",
                    source="github",
                    evidence=evidence,
                    meta=tuple((key, value) for key, value in meta if value),
                )
            )
            if len(hits) >= limit:
                break
        return tuple(hits)


class YouTubeSearchProvider(SearchProvider):
    """Videos found through the web index and confirmed by YouTube itself.

    There is no keyless search endpoint at YouTube, so the candidates come
    from a site-restricted web search; each video's title and channel are
    then read from YouTube's own oEmbed answer, which needs no key. A video
    oEmbed cannot confirm keeps the search engine's title and says so.
    """

    _WATCH = "https://www.youtube.com/watch?v="
    _OEMBED = "https://www.youtube.com/oembed"

    def __init__(self, web: DuckDuckGoSearchProvider, client: _APIClient) -> None:
        self._web = web
        self._client = client

    @staticmethod
    def video_id(url: str) -> str | None:
        parsed = urlsplit(url)
        host = (parsed.hostname or "").casefold()
        if host in {"youtu.be", "www.youtu.be"}:
            candidate = parsed.path.strip("/").split("/", 1)[0]
        elif host.endswith("youtube.com"):
            if parsed.path == "/watch":
                candidate = (parse_qs(parsed.query).get("v") or [""])[0]
            elif parsed.path.startswith(("/shorts/", "/embed/", "/live/")):
                candidate = parsed.path.split("/", 2)[2].split("/", 1)[0]
            else:
                return None
        else:
            return None
        return candidate if _YOUTUBE_ID.match(candidate or "") else None

    def search(self, query: str, *, limit: int, time_range: str | None = None) -> tuple[SearchHit, ...]:
        normalized = _check(query, limit)
        candidates = self._web.search(f"site:youtube.com {normalized}", limit=min(20, limit * 3), time_range=time_range)
        hits: list[SearchHit] = []
        seen: set[str] = set()
        for candidate in candidates:
            video = self.video_id(candidate.url)
            if video is None or video in seen:
                continue
            seen.add(video)
            url = f"{self._WATCH}{video}"
            title, author, thumbnail, confirmed = candidate.title, "", "", False
            try:
                payload = self._client.json(f"{self._OEMBED}?{urlencode({'url': url, 'format': 'json'})}")
            except SearchError:
                payload = None
            if isinstance(payload, Mapping):
                title = _clean(payload.get("title"), 300) or title
                author = _clean(payload.get("author_name"), 120)
                thumbnail = str(payload.get("thumbnail_url") or "")
                confirmed = True
            meta = [("channel", author), ("video_id", video), ("thumbnail", thumbnail),
                    ("confirmed", "yes" if confirmed else "no")]
            evidence = f"{title} — {author}" if author else title
            if candidate.snippet:
                evidence = f"{evidence}. {candidate.snippet}"
            hits.append(
                SearchHit(
                    title=title,
                    url=url,
                    snippet=candidate.snippet,
                    engine="youtube",
                    kind="video",
                    source="youtube",
                    evidence=evidence[:900],
                    meta=tuple((key, value) for key, value in meta if value),
                )
            )
            if len(hits) >= limit:
                break
        return tuple(hits)


class WikipediaSearchProvider(SearchProvider):
    """Articles, Turkish first and English when Turkish comes up short."""

    _LANGUAGES = ("tr", "en")

    def __init__(self, client: _APIClient, languages: tuple[str, ...] = _LANGUAGES) -> None:
        self._client = client
        self._languages = languages

    def search(self, query: str, *, limit: int, time_range: str | None = None) -> tuple[SearchHit, ...]:
        normalized = _check(query, limit)
        hits: list[SearchHit] = []
        seen: set[str] = set()
        for language in self._languages:
            if len(hits) >= limit:
                break
            params = {"action": "query", "list": "search", "srsearch": normalized, "srlimit": str(limit),
                      "format": "json", "utf8": "1", "srprop": "snippet|timestamp"}
            try:
                payload = self._client.json(f"https://{language}.wikipedia.org/w/api.php?{urlencode(params)}")
            except SearchError:
                if not hits:
                    raise
                break
            rows = payload.get("query", {}).get("search") if isinstance(payload, Mapping) else None
            for row in rows or []:
                if not isinstance(row, Mapping):
                    continue
                title = _clean(row.get("title"), 300)
                if not title:
                    continue
                url = f"https://{language}.wikipedia.org/wiki/{quote(title.replace(' ', '_'))}"
                if url in seen:
                    continue
                seen.add(url)
                snippet = _clean(row.get("snippet"), 600)
                hits.append(
                    SearchHit(
                        title=title,
                        url=url,
                        snippet=snippet,
                        published_at=_iso(row.get("timestamp")),
                        engine="wikipedia",
                        kind="encyclopedia",
                        source="wikipedia",
                        evidence=f"{title}: {snippet}" if snippet else title,
                        meta=(("language", language),),
                    )
                )
                if len(hits) >= limit:
                    break
        return tuple(hits)


class PubMedSearchProvider(SearchProvider):
    """Medical literature through NCBI's E-utilities: ids first, then summaries."""

    _BASE = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
    _MONTHS = {name: index for index, name in enumerate(
        ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"), start=1)}

    def __init__(self, client: _APIClient) -> None:
        self._client = client

    @classmethod
    def published(cls, pubdate: object) -> datetime | None:
        parts = str(pubdate or "").split()
        if not parts or not parts[0].isdigit():
            return None
        year = int(parts[0])
        month = cls._MONTHS.get(parts[1][:3].casefold(), 1) if len(parts) > 1 else 1
        day = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else 1
        try:
            return datetime(year, month, min(day, 28), tzinfo=UTC)
        except ValueError:
            return None

    def search(self, query: str, *, limit: int, time_range: str | None = None) -> tuple[SearchHit, ...]:
        normalized = _check(query, limit)
        params = {"db": "pubmed", "term": normalized, "retmode": "json", "retmax": str(limit), "sort": "relevance"}
        found = self._client.json(f"{self._BASE}/esearch.fcgi?{urlencode(params)}")
        ids = found.get("esearchresult", {}).get("idlist") if isinstance(found, Mapping) else None
        clean_ids = [str(value) for value in (ids or []) if str(value).isdigit()][:limit]
        if not clean_ids:
            return ()
        summary = self._client.json(f"{self._BASE}/esummary.fcgi?{urlencode({'db': 'pubmed', 'id': ','.join(clean_ids), 'retmode': 'json'})}")
        result = summary.get("result") if isinstance(summary, Mapping) else None
        hits: list[SearchHit] = []
        for pmid in clean_ids:
            row = result.get(pmid) if isinstance(result, Mapping) else None
            if not isinstance(row, Mapping):
                continue
            title = _clean(row.get("title"), 400)
            if not title:
                continue
            authors = [_clean(a.get("name"), 60) for a in row.get("authors", []) if isinstance(a, Mapping)]
            journal = _clean(row.get("fulljournalname") or row.get("source"), 200)
            pubdate = _clean(row.get("pubdate"), 40)
            byline = ", ".join(author for author in authors[:3] if author)
            if len(authors) > 3:
                byline += " ve diğerleri"
            evidence = ". ".join(part for part in (title, byline, f"{journal} ({pubdate})" if journal else pubdate) if part)
            meta = [("journal", journal), ("year", pubdate[:4]), ("authors", byline), ("pmid", pmid)]
            hits.append(
                SearchHit(
                    title=title,
                    url=f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/",
                    snippet=evidence,
                    published_at=self.published(pubdate),
                    engine="pubmed",
                    kind="paper",
                    source="pubmed",
                    evidence=evidence[:900],
                    meta=tuple((key, value) for key, value in meta if value),
                )
            )
        return tuple(hits)


class ArxivSearchProvider(SearchProvider):
    """Preprints from arXiv's Atom feed, read with a small entry scanner."""

    _ENDPOINT = "https://export.arxiv.org/api/query"
    _ENTRY = re.compile(r"<entry>(.*?)</entry>", re.S)
    _FIELD = {name: re.compile(rf"<{name}[^>]*>(.*?)</{name}>", re.S) for name in ("id", "title", "summary", "published")}
    _AUTHOR = re.compile(r"<author>\s*<name>(.*?)</name>", re.S)

    def __init__(self, client: _APIClient) -> None:
        self._client = client

    def search(self, query: str, *, limit: int, time_range: str | None = None) -> tuple[SearchHit, ...]:
        normalized = _check(query, limit)
        params = {"search_query": f"all:{normalized}", "start": "0", "max_results": str(limit)}
        feed = self._client.text(
            f"{self._ENDPOINT}?{urlencode(params)}",
            accept="application/atom+xml, application/xml;q=0.9, text/xml;q=0.8",
        )
        hits: list[SearchHit] = []
        for entry in self._ENTRY.findall(feed):
            fields = {name: (pattern.search(entry).group(1) if pattern.search(entry) else "") for name, pattern in self._FIELD.items()}
            url = _clean(fields["id"], 300)
            title = _clean(fields["title"], 400)
            if not url.startswith("http") or not title:
                continue
            url = url.replace("http://", "https://", 1)
            summary = _clean(fields["summary"], 900)
            authors = [_clean(name, 60) for name in self._AUTHOR.findall(entry)]
            byline = ", ".join(a for a in authors[:3] if a) + (" ve diğerleri" if len(authors) > 3 else "")
            published = _iso(_clean(fields["published"], 40))
            meta = [("authors", byline), ("year", published.strftime("%Y") if published else "")]
            hits.append(
                SearchHit(
                    title=title,
                    url=url,
                    snippet=summary,
                    published_at=published,
                    engine="arxiv",
                    kind="paper",
                    source="arxiv",
                    evidence=f"{title}. {summary}" if summary else title,
                    meta=tuple((key, value) for key, value in meta if value),
                )
            )
            if len(hits) >= limit:
                break
        return tuple(hits)


class StackOverflowSearchProvider(SearchProvider):
    """Questions from the Stack Exchange API; the answer is always gzip-packed."""

    _ENDPOINT = "https://api.stackexchange.com/2.3/search/advanced"

    def __init__(self, client: _APIClient, site: str = "stackoverflow") -> None:
        self._client = client
        self._site = site

    def search(self, query: str, *, limit: int, time_range: str | None = None) -> tuple[SearchHit, ...]:
        normalized = _check(query, limit)
        params = {"order": "desc", "sort": "relevance", "q": normalized, "site": self._site, "pagesize": str(limit)}
        payload = self._client.json(f"{self._ENDPOINT}?{urlencode(params)}")
        items = payload.get("items") if isinstance(payload, Mapping) else None
        hits: list[SearchHit] = []
        for item in items or []:
            if not isinstance(item, Mapping):
                continue
            title = _clean(item.get("title"), 300)
            url = str(item.get("link") or "")
            if not title or not url.startswith("https://"):
                continue
            score = item.get("score")
            answers = item.get("answer_count")
            answered = bool(item.get("is_answered"))
            tags = [_clean(t, 30) for t in item.get("tags", []) if isinstance(t, str)][:6]
            created = item.get("creation_date")
            published = (
                datetime.fromtimestamp(int(created), tz=UTC)
                if isinstance(created, int) and not isinstance(created, bool) and created > 0
                else None
            )
            facts = []
            if isinstance(answers, int) and not isinstance(answers, bool):
                facts.append(f"{answers} cevap" + (" (kabul edilmiş cevap var)" if answered else ""))
            if isinstance(score, int) and not isinstance(score, bool):
                facts.append(f"puan {score}")
            evidence = f"{title}. " + ", ".join(facts) if facts else title
            meta = [("score", str(score) if isinstance(score, int) and not isinstance(score, bool) else ""),
                    ("answers", str(answers) if isinstance(answers, int) and not isinstance(answers, bool) else ""),
                    ("answered", "yes" if answered else "no"), ("tags", ", ".join(tags))]
            hits.append(
                SearchHit(
                    title=title,
                    url=url,
                    snippet=", ".join(facts),
                    published_at=published,
                    engine="stackoverflow",
                    kind="discussion",
                    source="stackoverflow",
                    evidence=evidence[:900],
                    meta=tuple((key, value) for key, value in meta if value),
                )
            )
            if len(hits) >= limit:
                break
        return tuple(hits)


class HackerNewsSearchProvider(SearchProvider):
    """Stories from Hacker News through the Algolia index."""

    _ENDPOINT = "https://hn.algolia.com/api/v1/search"

    def __init__(self, client: _APIClient) -> None:
        self._client = client

    def search(self, query: str, *, limit: int, time_range: str | None = None) -> tuple[SearchHit, ...]:
        normalized = _check(query, limit)
        params = {"query": normalized, "tags": "story", "hitsPerPage": str(limit)}
        payload = self._client.json(f"{self._ENDPOINT}?{urlencode(params)}")
        rows = payload.get("hits") if isinstance(payload, Mapping) else None
        hits: list[SearchHit] = []
        for row in rows or []:
            if not isinstance(row, Mapping):
                continue
            title = _clean(row.get("title"), 300)
            story_id = _clean(row.get("objectID"), 20)
            if not title or not story_id:
                continue
            url = str(row.get("url") or "") or f"https://news.ycombinator.com/item?id={story_id}"
            if not url.startswith("https://"):
                url = f"https://news.ycombinator.com/item?id={story_id}"
            points = row.get("points")
            comments = row.get("num_comments")
            story_text = _clean(row.get("story_text"), 600)
            facts = []
            if isinstance(points, int) and not isinstance(points, bool):
                facts.append(f"{points} puan")
            if isinstance(comments, int) and not isinstance(comments, bool):
                facts.append(f"{comments} yorum")
            evidence = ". ".join(part for part in (title, story_text, ", ".join(facts)) if part)
            meta = [("points", str(points) if isinstance(points, int) and not isinstance(points, bool) else ""),
                    ("comments", str(comments) if isinstance(comments, int) and not isinstance(comments, bool) else ""),
                    ("author", _clean(row.get("author"), 60)),
                    ("discussion", f"https://news.ycombinator.com/item?id={story_id}")]
            hits.append(
                SearchHit(
                    title=title,
                    url=url,
                    snippet=", ".join(facts),
                    published_at=_iso(row.get("created_at")),
                    engine="hackernews",
                    kind="discussion",
                    source="hackernews",
                    evidence=evidence[:900],
                    meta=tuple((key, value) for key, value in meta if value),
                )
            )
            if len(hits) >= limit:
                break
        return tuple(hits)


class SiteSearchProvider(SearchProvider):
    """The web search, held to one host the student named."""

    def __init__(self, web: DuckDuckGoSearchProvider, site: str) -> None:
        normalized = normalize_site(site)
        if normalized is None:
            raise ValueError("A site search needs a host name.")
        self._web = web
        self.site = normalized

    def search(self, query: str, *, limit: int, time_range: str | None = None) -> tuple[SearchHit, ...]:
        normalized = _check(query, limit)
        hits = self._web.search(f"site:{self.site} {normalized}", limit=limit, time_range=time_range)
        kept: list[SearchHit] = []
        for hit in hits:
            host = (urlsplit(hit.url).hostname or "").casefold()
            if host != self.site and not host.endswith(f".{self.site}"):
                continue
            kept.append(SearchHit(
                title=hit.title, url=hit.url, snippet=hit.snippet, published_at=hit.published_at,
                engine="site", kind="web", source="site", meta=(("site", self.site),),
            ))
        return tuple(kept)


_HTTP_STATUS = re.compile(r"HTTP (\d{3})")


def failure_reason(exc: BaseException) -> str:
    """What to tell the student about a source that did not answer.

    An HTTP status is the one fact worth carrying - a 202 from DuckDuckGo
    is its throttle, a 403 is a refusal - and it is short enough to sit in
    a sentence. Anything else is named by its kind, never by its text.
    """
    match = _HTTP_STATUS.search(str(exc))
    if match:
        return f"HTTP {match.group(1)}"
    cause = exc.__cause__
    if cause is not None:
        inner = _HTTP_STATUS.search(str(cause))
        if inner:
            return f"HTTP {inner.group(1)}"
    return type(exc).__name__


@dataclass(frozen=True, slots=True)
class MultiSearchResult:
    hits: tuple[SearchHit, ...]
    failures: tuple[tuple[str, str], ...]   # (source id, reason)


class MultiSourceSearchProvider(SearchProvider):
    """Every chosen source at once, their answers interleaved.

    Each source runs in its own thread with its own deadline; one that fails
    is reported by name and never takes the others down with it. The plain
    ``search`` keeps the tool's contract - the web alone unless asked - so
    a model that calls research_web gets what it always got.
    """

    def __init__(
        self,
        providers: Mapping[str, SearchProvider],
        *,
        web: DuckDuckGoSearchProvider | None = None,
        default_sources: tuple[str, ...] = DEFAULT_TOOL_SOURCES,
        timeout_seconds: float = 20.0,
    ) -> None:
        if "web" not in providers:
            raise ValueError("The web source is required.")
        for source_id in providers:
            source_spec(source_id)
        self._providers = dict(providers)
        self._web = web
        self._default_sources = default_sources
        self._timeout = timeout_seconds

    @property
    def available(self) -> tuple[str, ...]:
        ids = tuple(source_id for source_id in SOURCE_IDS if source_id in self._providers)
        return ids + (("site",) if self._web is not None and "site" not in ids else ())

    def search(self, query: str, *, limit: int, time_range: str | None = None) -> tuple[SearchHit, ...]:
        return self.search_sources(query, limit=limit, time_range=time_range, sources=self._default_sources).hits

    def search_sources(
        self,
        query: str,
        *,
        limit: int,
        time_range: str | None = None,
        sources: tuple[str, ...] | None = None,
        site: str | None = None,
    ) -> MultiSearchResult:
        normalized = _check(query, limit)
        chosen = tuple(sources) if sources else self._default_sources
        host = normalize_site(site)
        jobs: dict[str, SearchProvider] = {}
        for source_id in chosen:
            if source_id == "site":
                if host is None:
                    raise ValueError("The site source needs a host name.")
                if self._web is None:
                    raise SearchError("Site search needs the web source.")
                jobs["site"] = SiteSearchProvider(self._web, host)
                continue
            provider = self._providers.get(source_id)
            if provider is None:
                raise ValueError(f"Research source is not enabled: {source_id}")
            jobs[source_id] = provider
        if not jobs:
            raise ValueError("At least one research source is required.")
        per_source = max(2, ceil(limit / len(jobs)) + 1)
        results: dict[str, tuple[SearchHit, ...]] = {}
        failures: list[tuple[str, str]] = []
        with ThreadPoolExecutor(max_workers=len(jobs)) as pool:
            futures = {
                pool.submit(provider.search, normalized, limit=min(20, per_source), time_range=time_range): source_id
                for source_id, provider in jobs.items()
            }
            try:
                for future in as_completed(futures, timeout=self._timeout):
                    source_id = futures[future]
                    try:
                        results[source_id] = future.result()
                    except (SearchError, ValueError, OSError, TimeoutError) as exc:
                        failures.append((source_id, failure_reason(exc)))
            except TimeoutError:
                for future, source_id in futures.items():
                    if source_id not in results and not any(f[0] == source_id for f in failures):
                        future.cancel()
                        failures.append((source_id, "TimeoutError"))
        ordered: list[SearchHit] = []
        seen: set[str] = set()
        queues = [list(results.get(source_id, ())) for source_id in jobs]
        while any(queues) and len(ordered) < limit:
            for queue in queues:
                if not queue:
                    continue
                hit = queue.pop(0)
                if hit.url in seen:
                    continue
                seen.add(hit.url)
                ordered.append(hit)
                if len(ordered) >= limit:
                    break
        return MultiSearchResult(tuple(ordered), tuple(failures))


def build_source_providers(
    policy: URLPolicy,
    *,
    web: SearchProvider,
    duckduckgo: DuckDuckGoSearchProvider | None,
    enabled: tuple[str, ...],
    timeout_seconds: float,
    max_response_bytes: int,
    user_agent: str,
    transport: WebTransport | None = None,
) -> MultiSourceSearchProvider:
    """The catalogue, wired: every enabled source over one client and policy."""
    client = _APIClient(
        policy,
        transport=transport,
        timeout_seconds=timeout_seconds,
        max_response_bytes=max_response_bytes,
        user_agent=user_agent,
    )
    providers: dict[str, SearchProvider] = {"web": web}
    if "youtube" in enabled and duckduckgo is not None:
        providers["youtube"] = YouTubeSearchProvider(duckduckgo, client)
    if "github" in enabled:
        providers["github"] = GitHubSearchProvider(client)
    if "wikipedia" in enabled:
        providers["wikipedia"] = WikipediaSearchProvider(client)
    if "pubmed" in enabled:
        providers["pubmed"] = PubMedSearchProvider(client)
    if "arxiv" in enabled:
        providers["arxiv"] = ArxivSearchProvider(client)
    if "stackoverflow" in enabled:
        providers["stackoverflow"] = StackOverflowSearchProvider(client)
    if "hackernews" in enabled:
        providers["hackernews"] = HackerNewsSearchProvider(client)
    return MultiSourceSearchProvider(
        providers,
        web=duckduckgo if "site" in enabled else None,
        timeout_seconds=max(timeout_seconds * 2, 15.0),
    )
