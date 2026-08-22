from __future__ import annotations

import http.client
import socket
import ssl
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol
from urllib.parse import urljoin, urlsplit

from app.research.errors import ContentRejectedError, FetchError
from app.research.extractor import extract_content
from app.research.models import ResolvedURL, WebDocument
from app.research.url_policy import URLPolicy


@dataclass(frozen=True, slots=True)
class TransportResponse:
    status: int
    headers: Mapping[str, str]
    body: bytes


class WebTransport(Protocol):
    def request(
        self,
        target: ResolvedURL,
        *,
        address: str,
        timeout_seconds: float,
        max_bytes: int,
        user_agent: str,
    ) -> TransportResponse: ...


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    def __init__(self, host: str, address: str, port: int, timeout: float) -> None:
        super().__init__(host, port=port, timeout=timeout)
        self._address = address

    def connect(self) -> None:
        raw = socket.create_connection((self._address, self.port), self.timeout)
        self.sock = self._context.wrap_socket(raw, server_hostname=self.host)


class PinnedHTTPTransport:
    """Direct transport pinned to a prevalidated IP; it never uses proxies."""

    def request(
        self,
        target: ResolvedURL,
        *,
        address: str,
        timeout_seconds: float,
        max_bytes: int,
        user_agent: str,
    ) -> TransportResponse:
        parsed = urlsplit(target.url)
        path = parsed.path or "/"
        if parsed.query:
            path = f"{path}?{parsed.query}"
        if parsed.scheme == "https":
            connection: http.client.HTTPConnection = _PinnedHTTPSConnection(
                target.host, address, target.port, timeout_seconds
            )
        else:
            connection = http.client.HTTPConnection(
                address, port=target.port, timeout=timeout_seconds
            )
        host_header = target.host
        if ":" in host_header:
            host_header = f"[{host_header}]"
        default_port = 443 if parsed.scheme == "https" else 80
        if target.port != default_port:
            host_header = f"{host_header}:{target.port}"
        try:
            connection.request(
                "GET",
                path,
                headers={
                    "Host": host_header,
                    "User-Agent": user_agent,
                    "Accept": "text/html, text/plain, application/xhtml+xml, application/json",
                    "Accept-Encoding": "identity",
                    "Connection": "close",
                },
            )
            response = connection.getresponse()
            headers = {key.casefold(): value for key, value in response.getheaders()}
            length = headers.get("content-length")
            if length:
                try:
                    if int(length) > max_bytes:
                        raise ContentRejectedError("Response is larger than the configured limit.")
                except ValueError as exc:
                    raise ContentRejectedError("Response has an invalid Content-Length.") from exc
            body = response.read(max_bytes + 1)
            if len(body) > max_bytes:
                raise ContentRejectedError("Response exceeded the configured byte limit.")
            return TransportResponse(response.status, headers, body)
        except (OSError, http.client.HTTPException, ssl.SSLError) as exc:
            raise FetchError("The remote source could not be fetched.") from exc
        finally:
            connection.close()


class SafeWebFetcher:
    _allowed_types = {
        "text/html",
        "text/plain",
        "application/xhtml+xml",
        "application/json",
    }
    _redirects = {301, 302, 303, 307, 308}

    def __init__(
        self,
        policy: URLPolicy,
        *,
        transport: WebTransport | None = None,
        timeout_seconds: float = 10.0,
        max_bytes: int = 2_000_000,
        max_characters: int = 50_000,
        max_redirects: int = 3,
        user_agent: str = "JARVIS/0.1",
    ) -> None:
        if min(timeout_seconds, max_bytes, max_characters) <= 0 or max_redirects < 0:
            raise ValueError("Fetcher limits must be positive and redirects non-negative.")
        self.policy = policy
        self.transport = transport or PinnedHTTPTransport()
        self.timeout_seconds = timeout_seconds
        self.max_bytes = max_bytes
        self.max_characters = max_characters
        self.max_redirects = max_redirects
        self.user_agent = user_agent

    def fetch(self, url: str) -> WebDocument:
        original = url
        current = url
        previous_scheme: str | None = None
        for redirect_count in range(self.max_redirects + 1):
            target = self.policy.validate(current)
            scheme = urlsplit(target.url).scheme
            if previous_scheme == "https" and scheme != "https":
                raise ContentRejectedError("HTTPS redirects may not downgrade to HTTP.")
            response = self.transport.request(
                target,
                address=target.addresses[0],
                timeout_seconds=self.timeout_seconds,
                max_bytes=self.max_bytes,
                user_agent=self.user_agent,
            )
            headers = {key.casefold(): value for key, value in response.headers.items()}
            declared_length = headers.get("content-length")
            if declared_length:
                try:
                    if int(declared_length) > self.max_bytes:
                        raise ContentRejectedError(
                            "Response is larger than the configured limit."
                        )
                except ValueError as exc:
                    raise ContentRejectedError(
                        "Response has an invalid Content-Length."
                    ) from exc
            if response.status in self._redirects:
                location = headers.get("location")
                if not location:
                    raise FetchError("Redirect response did not include a location.")
                if redirect_count >= self.max_redirects:
                    raise FetchError("Redirect limit exceeded.")
                previous_scheme = scheme
                current = urljoin(target.url, location)
                continue
            if not 200 <= response.status < 300:
                raise FetchError(f"Remote source returned HTTP {response.status}.")
            disposition = headers.get("content-disposition", "").casefold()
            if "attachment" in disposition:
                raise ContentRejectedError("Download attachments are not allowed.")
            encoding = headers.get("content-encoding", "identity").casefold()
            if encoding not in {"", "identity"}:
                raise ContentRejectedError("Compressed web responses are not accepted.")
            content_type = headers.get("content-type", "").split(";", 1)[0].strip().casefold()
            if content_type not in self._allowed_types:
                raise ContentRejectedError("Response content type is not allowed.")
            if len(response.body) > self.max_bytes:
                raise ContentRejectedError("Response exceeded the configured byte limit.")
            title, text, published_at, findings = extract_content(
                response.body,
                headers.get("content-type", content_type),
                max_characters=self.max_characters,
            )
            if not text:
                raise ContentRejectedError("Response contained no usable text.")
            return WebDocument.create(
                url=original,
                final_url=target.url,
                title=title or target.host,
                text=text,
                content_type=content_type,
                status=response.status,
                observed_at=datetime.now(UTC),
                resolved_addresses=target.addresses,
                published_at=published_at,
                findings=findings,
            )
        raise FetchError("Redirect limit exceeded.")
