from __future__ import annotations

import json
from abc import ABC, abstractmethod
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from app.research.errors import SearchError, UnsafeURLError
from app.research.extractor import parse_datetime
from app.research.fetcher import SafeWebFetcher
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
