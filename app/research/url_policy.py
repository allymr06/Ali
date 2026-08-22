from __future__ import annotations

import ipaddress
import socket
from collections.abc import Callable, Iterable
from urllib.parse import SplitResult, urlsplit, urlunsplit

from app.research.errors import UnsafeURLError
from app.research.models import ResolvedURL

Resolver = Callable[[str, int], Iterable[tuple[object, ...] | str]]


def _system_resolver(host: str, port: int) -> Iterable[tuple[object, ...]]:
    return socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)


def _extract_address(item: tuple[object, ...] | str) -> str:
    if isinstance(item, str):
        return item
    try:
        sockaddr = item[4]
        if isinstance(sockaddr, tuple) and sockaddr:
            return str(sockaddr[0])
    except (IndexError, TypeError):
        pass
    raise UnsafeURLError("DNS returned an invalid address record.")


def _is_public_address(value: str) -> bool:
    try:
        address = ipaddress.ip_address(value)
    except ValueError as exc:
        raise UnsafeURLError("DNS returned a malformed IP address.") from exc
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped:
        address = address.ipv4_mapped
    return bool(address.is_global)


class URLPolicy:
    """Validate and resolve public web URLs before every connection."""

    def __init__(
        self,
        *,
        resolver: Resolver | None = None,
        allow_http: bool = False,
        allowed_ports: frozenset[int] = frozenset({80, 443}),
    ) -> None:
        self._resolver = resolver or _system_resolver
        self._schemes = {"https"} | ({"http"} if allow_http else set())
        self._allowed_ports = allowed_ports

    def validate(self, url: str) -> ResolvedURL:
        if not isinstance(url, str) or not url.strip():
            raise UnsafeURLError("URL cannot be empty.")
        if len(url) > 2048:
            raise UnsafeURLError("URL exceeds the 2048 character limit.")
        try:
            parsed = urlsplit(url.strip())
            port = parsed.port
        except ValueError as exc:
            raise UnsafeURLError("URL is malformed.") from exc
        scheme = parsed.scheme.lower()
        if scheme not in self._schemes:
            raise UnsafeURLError("URL scheme is not allowed.")
        if parsed.username is not None or parsed.password is not None:
            raise UnsafeURLError("URLs containing credentials are forbidden.")
        if not parsed.hostname:
            raise UnsafeURLError("URL must include a hostname.")
        try:
            host = parsed.hostname.rstrip(".").encode("idna").decode("ascii").lower()
        except UnicodeError as exc:
            raise UnsafeURLError("URL hostname is invalid.") from exc
        if host in {"localhost", "localhost.localdomain"} or host.endswith(
            (".localhost", ".local", ".internal")
        ):
            raise UnsafeURLError("Local hostnames are forbidden.")
        effective_port = port or (443 if scheme == "https" else 80)
        if effective_port not in self._allowed_ports:
            raise UnsafeURLError("URL port is not allowed.")
        try:
            raw_addresses = tuple(self._resolver(host, effective_port))
        except (OSError, socket.gaierror) as exc:
            raise UnsafeURLError("Hostname could not be resolved safely.") from exc
        addresses = tuple(dict.fromkeys(_extract_address(item) for item in raw_addresses))
        if not addresses:
            raise UnsafeURLError("Hostname did not resolve to an address.")
        if any(not _is_public_address(address) for address in addresses):
            raise UnsafeURLError("Hostname resolves to a non-public address.")
        canonical = self._canonicalize(parsed, host, effective_port)
        return ResolvedURL(canonical, host, effective_port, addresses)

    @staticmethod
    def _canonicalize(parsed: SplitResult, host: str, port: int) -> str:
        scheme = parsed.scheme.lower()
        display_host = f"[{host}]" if ":" in host else host
        default_port = 443 if scheme == "https" else 80
        netloc = display_host if port == default_port else f"{display_host}:{port}"
        path = parsed.path or "/"
        return urlunsplit((scheme, netloc, path, parsed.query, ""))
