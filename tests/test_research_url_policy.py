from __future__ import annotations

import pytest

from app.research.errors import UnsafeURLError
from app.research.url_policy import URLPolicy


def resolver(*addresses: str):
    return lambda _host, _port: addresses


def test_url_policy_canonicalizes_public_https_url() -> None:
    policy = URLPolicy(resolver=resolver("93.184.216.34"))

    result = policy.validate("HTTPS://Example.COM:443/path?q=1#fragment")

    assert result.url == "https://example.com/path?q=1"
    assert result.host == "example.com"
    assert result.port == 443
    assert result.addresses == ("93.184.216.34",)


@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "ftp://example.com/file",
        "https://user:secret@example.com/",
        "https://localhost/",
        "https://service.internal/",
        "https://example.com:8443/",
        "https:///missing-host",
    ],
)
def test_url_policy_rejects_unsafe_url_forms(url: str) -> None:
    with pytest.raises(UnsafeURLError):
        URLPolicy(resolver=resolver("93.184.216.34")).validate(url)


@pytest.mark.parametrize(
    "address",
    [
        "127.0.0.1",
        "10.0.0.1",
        "169.254.169.254",
        "192.168.1.2",
        "::1",
        "fe80::1",
        "::ffff:127.0.0.1",
        "0.0.0.0",
    ],
)
def test_url_policy_rejects_non_public_addresses(address: str) -> None:
    with pytest.raises(UnsafeURLError, match="non-public"):
        URLPolicy(resolver=resolver(address)).validate("https://example.com/")


def test_url_policy_rejects_mixed_public_and_private_dns_answers() -> None:
    with pytest.raises(UnsafeURLError, match="non-public"):
        URLPolicy(
            resolver=resolver("93.184.216.34", "127.0.0.1")
        ).validate("https://example.com/")


def test_url_policy_allows_http_only_when_explicitly_enabled() -> None:
    strict = URLPolicy(resolver=resolver("93.184.216.34"))
    enabled = URLPolicy(
        resolver=resolver("93.184.216.34"), allow_http=True
    )

    with pytest.raises(UnsafeURLError):
        strict.validate("http://example.com/")
    assert enabled.validate("http://example.com/").port == 80
