from __future__ import annotations

import http.client
import json
import re
import ssl
import time
from abc import ABC, abstractmethod
from collections.abc import Callable, Mapping
from html.parser import HTMLParser
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from app.research.errors import ContentRejectedError, FetchError, SearchError, UnsafeURLError
from app.research.extractor import parse_datetime
from app.research.fetcher import PinnedHTTPTransport, SafeWebFetcher, WebTransport
from app.research.models import SearchHit
from app.research.url_policy import URLPolicy


class SearchProvider(ABC):
    @abstractmethod
    def search(
        self,
        query: str,
        *,
        limit: int,
        time_range: str | None = None,
    ) -> tuple[SearchHit, ...]:
        """Return bounded, normalized search results."""


class SearXNGSearchProvider(SearchProvider):
    """Strict client for the administrator-configured SearXNG JSON API."""

    def __init__(
        self,
        endpoint: str,
        fetcher: SafeWebFetcher,
        result_policy: URLPolicy,
    ) -> None:
        self._endpoint = endpoint.strip().rstrip("/")
        if not self._endpoint:
            raise ValueError("SearXNG endpoint cannot be empty.")
        parsed = urlsplit(self._endpoint)
        if (
            parsed.scheme.casefold() not in {"http", "https"}
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("SearXNG endpoint must be a credential-free HTTP(S) base URL.")
        self._fetcher = fetcher
        self._result_policy = result_policy

    def search(
        self,
        query: str,
        *,
        limit: int,
        time_range: str | None = None,
    ) -> tuple[SearchHit, ...]:
        normalized = query.strip()
        if not normalized or len(normalized) > 500:
            raise ValueError("Search query must contain between 1 and 500 characters.")
        if not 1 <= limit <= 20:
            raise ValueError("Search result limit must be between 1 and 20.")
        if time_range not in {None, "day", "month", "year"}:
            raise ValueError("time_range must be day, month, year, or omitted.")
        endpoint = self._build_url(normalized, time_range)
        document = self._fetcher.fetch(endpoint)
        try:
            payload = json.loads(document.text)
            raw_results = payload["results"]
        except (json.JSONDecodeError, KeyError, TypeError) as exc:
            raise SearchError("Search provider returned invalid JSON.") from exc
        if not isinstance(raw_results, list):
            raise SearchError("Search provider results must be a list.")
        hits: list[SearchHit] = []
        seen: set[str] = set()
        for item in raw_results:
            if not isinstance(item, dict):
                continue
            title = item.get("title")
            url = item.get("url")
            if not isinstance(title, str) or not isinstance(url, str):
                continue
            try:
                resolved = self._result_policy.validate(url)
            except UnsafeURLError:
                continue
            if resolved.url in seen:
                continue
            seen.add(resolved.url)
            snippet = item.get("content", "")
            engines = item.get("engines", [])
            hits.append(
                SearchHit(
                    title=title.strip()[:500],
                    url=resolved.url,
                    snippet=snippet.strip()[:2_000] if isinstance(snippet, str) else "",
                    published_at=parse_datetime(
                        item.get("publishedDate")
                        if isinstance(item.get("publishedDate"), str)
                        else None
                    ),
                    engine=(
                        ",".join(str(value) for value in engines[:5])
                        if isinstance(engines, list)
                        else None
                    ),
                )
            )
            if len(hits) >= limit:
                break
        return tuple(hits)

    def _build_url(self, query: str, time_range: str | None) -> str:
        parsed = urlsplit(f"{self._endpoint}/search")
        params = dict(parse_qsl(parsed.query, keep_blank_values=True))
        params.update({"q": query, "format": "json", "safesearch": "2"})
        if time_range:
            params["time_range"] = time_range
        return urlunsplit(
            (parsed.scheme, parsed.netloc, parsed.path, urlencode(params), "")
        )


_MODEL_NAME = re.compile(r"^[A-Za-z0-9._-]{1,80}$")


class GeminiGroundedSearch(SearchProvider):
    """Search hits from Gemini's Google Search grounding.

    JARVIS's one production provider doubles as the search engine: the
    query goes to the native ``generateContent`` endpoint with the
    ``google_search`` tool, and only the grounding chunks come back as
    hits. The generated prose is deliberately discarded — the research
    pipeline fetches and reads every cited page itself, so claims stay
    anchored to fetched documents, never to model text. The API key
    travels in a header, is never placed in a URL, and never appears in
    an error message.
    """

    _HOST = "generativelanguage.googleapis.com"
    _MAX_RESPONSE_BYTES = 4_000_000

    def __init__(
        self,
        api_key: str,
        model: str,
        result_policy: URLPolicy,
        *,
        timeout_seconds: float = 10.0,
        transport: Callable[[str, dict, Mapping[str, str]], object] | None = None,
    ) -> None:
        key = (api_key or "").strip()
        if not key:
            raise ValueError("Gemini grounded search requires an API key.")
        name = (model or "").strip()
        if not _MODEL_NAME.match(name):
            raise ValueError("Gemini grounded search model name is invalid.")
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive.")
        self._api_key = key
        self._model = name
        self._result_policy = result_policy
        self._timeout_seconds = timeout_seconds
        self._transport = transport or self._post_json

    def search(
        self,
        query: str,
        *,
        limit: int,
        time_range: str | None = None,
    ) -> tuple[SearchHit, ...]:
        normalized = query.strip()
        if not normalized or len(normalized) > 500:
            raise ValueError("Search query must contain between 1 and 500 characters.")
        if not 1 <= limit <= 20:
            raise ValueError("Search result limit must be between 1 and 20.")
        if time_range not in {None, "day", "month", "year"}:
            raise ValueError("time_range must be day, month, year, or omitted.")
        prompt = f"Search the web and answer with sources: {normalized}"
        if time_range:
            prompt += f" (prefer sources from the past {time_range})"
        body = {
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "tools": [{"google_search": {}}],
            "generationConfig": {"temperature": 0.0, "maxOutputTokens": 1024},
        }
        headers = {
            "Content-Type": "application/json",
            "x-goog-api-key": self._api_key,
        }
        payload = self._transport(
            f"/v1beta/models/{self._model}:generateContent", body, headers
        )
        return self._hits(payload, limit)

    def _hits(self, payload: object, limit: int) -> tuple[SearchHit, ...]:
        if not isinstance(payload, dict):
            raise SearchError("Search backend returned an invalid response.")
        candidates = payload.get("candidates")
        if not isinstance(candidates, list) or not candidates:
            # The model produced nothing at all; an answer without
            # grounding is handled below as simply zero hits.
            return ()
        first = candidates[0] if isinstance(candidates[0], dict) else {}
        metadata = first.get("groundingMetadata")
        if not isinstance(metadata, dict):
            return ()
        chunks = metadata.get("groundingChunks")
        supports = metadata.get("groundingSupports")
        if not isinstance(chunks, list):
            return ()
        snippets: dict[int, list[str]] = {}
        if isinstance(supports, list):
            for support in supports:
                if not isinstance(support, dict):
                    continue
                segment = support.get("segment")
                text = segment.get("text") if isinstance(segment, dict) else None
                indices = support.get("groundingChunkIndices")
                if not isinstance(text, str) or not isinstance(indices, list):
                    continue
                for index in indices:
                    if isinstance(index, int):
                        snippets.setdefault(index, []).append(text.strip())
        hits: list[SearchHit] = []
        seen: set[str] = set()
        for index, chunk in enumerate(chunks):
            if not isinstance(chunk, dict):
                continue
            web = chunk.get("web")
            if not isinstance(web, dict):
                continue
            uri = web.get("uri")
            title = web.get("title")
            if not isinstance(uri, str) or not isinstance(title, str):
                continue
            try:
                resolved = self._result_policy.validate(uri)
            except UnsafeURLError:
                continue
            if resolved.url in seen:
                continue
            seen.add(resolved.url)
            hits.append(
                SearchHit(
                    title=title.strip()[:500],
                    url=resolved.url,
                    snippet=" ".join(snippets.get(index, []))[:2_000],
                    published_at=None,
                    engine="google",
                )
            )
            if len(hits) >= limit:
                break
        return tuple(hits)

    def _post_json(
        self, path: str, body: dict, headers: Mapping[str, str]
    ) -> object:
        connection = http.client.HTTPSConnection(
            self._HOST, timeout=self._timeout_seconds
        )
        try:
            connection.request(
                "POST", path, body=json.dumps(body), headers=dict(headers)
            )
            response = connection.getresponse()
            raw = response.read(self._MAX_RESPONSE_BYTES + 1)
        except (OSError, http.client.HTTPException, ssl.SSLError) as exc:
            raise SearchError("The search backend could not be reached.") from exc
        finally:
            connection.close()
        if response.status != 200:
            raise SearchError(f"Search backend returned HTTP {response.status}.")
        if len(raw) > self._MAX_RESPONSE_BYTES:
            raise SearchError("Search backend response exceeded the size limit.")
        try:
            return json.loads(raw)
        except json.JSONDecodeError as exc:
            raise SearchError("Search backend returned invalid JSON.") from exc


class _DuckDuckGoResultParser(HTMLParser):
    """Collect (href, title, snippet) triples from the no-script results page."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.results: list[dict[str, str]] = []
        self._mode: str | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.casefold() != "a":
            return
        values = {key.casefold(): value or "" for key, value in attrs}
        classes = values.get("class", "").split()
        if "result__a" in classes:
            self.results.append(
                {"href": values.get("href", ""), "title": "", "snippet": ""}
            )
            self._mode = "title"
        elif "result__snippet" in classes and self.results:
            self._mode = "snippet"

    def handle_endtag(self, tag: str) -> None:
        if tag.casefold() == "a":
            self._mode = None

    def handle_data(self, data: str) -> None:
        if self._mode and self.results:
            self.results[-1][self._mode] += data


class DuckDuckGoSearchProvider(SearchProvider):
    """Keyless search over DuckDuckGo's no-JavaScript HTML endpoint.

    The default backend: it needs no API key, no account and no
    self-hosted service. Result links arrive as DuckDuckGo redirect URLs;
    the real destination is decoded from the ``uddg`` parameter and then
    validated by the same URL policy as every other fetch, which also
    drops advertising links (they do not use the standard redirect).
    The endpoint serves an empty challenge page to non-browser agents,
    so the request presents a common browser user-agent string - the
    same thing any personal browser would send.
    """

    _ENDPOINT = "https://html.duckduckgo.com/html/"
    _THROTTLE_PAUSE_SECONDS = 1.5
    _TIME_RANGES = {"day": "d", "month": "m", "year": "y"}
    _BROWSER_UA = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/152.0 Safari/537.36"
    )

    def __init__(
        self,
        result_policy: URLPolicy,
        *,
        timeout_seconds: float = 10.0,
        max_response_bytes: int = 2_000_000,
        region: str = "tr-tr",
        transport: WebTransport | None = None,
    ) -> None:
        if timeout_seconds <= 0 or max_response_bytes <= 0:
            raise ValueError("DuckDuckGo search limits must be positive.")
        self._result_policy = result_policy
        self._timeout_seconds = timeout_seconds
        self._max_response_bytes = max_response_bytes
        self._region = region.strip()
        self._transport = transport or PinnedHTTPTransport()

    def search(
        self,
        query: str,
        *,
        limit: int,
        time_range: str | None = None,
    ) -> tuple[SearchHit, ...]:
        normalized = query.strip()
        if not normalized or len(normalized) > 500:
            raise ValueError("Search query must contain between 1 and 500 characters.")
        if not 1 <= limit <= 20:
            raise ValueError("Search result limit must be between 1 and 20.")
        if time_range not in {None, "day", "month", "year"}:
            raise ValueError("time_range must be day, month, year, or omitted.")
        params = {"q": normalized, "kp": "1"}
        if self._region:
            params["kl"] = self._region
        if time_range:
            params["df"] = self._TIME_RANGES[time_range]
        target = self._result_policy.validate(f"{self._ENDPOINT}?{urlencode(params)}")
        response = None
        # A burst of searches gets HTTP 202 and a challenge page instead of
        # results: the endpoint's throttle, not a failure. One patient
        # retry usually clears it; a second 202 is reported as it is.
        for attempt in range(2):
            try:
                response = self._transport.request(
                    target,
                    address=target.addresses[0],
                    timeout_seconds=self._timeout_seconds,
                    max_bytes=self._max_response_bytes,
                    user_agent=self._BROWSER_UA,
                )
            except (FetchError, ContentRejectedError) as exc:
                raise SearchError("The search page could not be fetched.") from exc
            if response.status != 202 or attempt:
                break
            time.sleep(self._THROTTLE_PAUSE_SECONDS)
        if response.status != 200:
            raise SearchError(f"Search page returned HTTP {response.status}.")
        parser = _DuckDuckGoResultParser()
        parser.feed(response.body.decode("utf-8", "replace"))
        hits: list[SearchHit] = []
        seen: set[str] = set()
        for row in parser.results:
            destination = self._decode_redirect(row["href"])
            if destination is None:
                continue
            try:
                resolved = self._result_policy.validate(destination)
            except UnsafeURLError:
                continue
            if resolved.url in seen:
                continue
            title = " ".join(row["title"].split())
            if not title:
                continue
            seen.add(resolved.url)
            hits.append(
                SearchHit(
                    title=title[:500],
                    url=resolved.url,
                    snippet=" ".join(row["snippet"].split())[:2_000],
                    published_at=None,
                    engine="duckduckgo",
                )
            )
            if len(hits) >= limit:
                break
        return tuple(hits)

    @staticmethod
    def _decode_redirect(href: str) -> str | None:
        """The real destination behind ``//duckduckgo.com/l/?uddg=...``.

        Anything that is not the standard organic-result redirect - ad
        routers included - is dropped rather than guessed at.
        """
        candidate = (href or "").strip()
        if not candidate:
            return None
        if candidate.startswith("//"):
            candidate = f"https:{candidate}"
        parsed = urlsplit(candidate)
        if (parsed.hostname or "").casefold() != "duckduckgo.com":
            return None
        if not parsed.path.startswith("/l/"):
            return None
        for key, value in parse_qsl(parsed.query):
            if key == "uddg" and value:
                return value
        return None
