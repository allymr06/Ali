from __future__ import annotations

import asyncio

import pytest

from app.core.models import Context, Request
from app.providers.base import (
    ModelCapabilities,
    ModelResponse,
    ProviderUnavailableError,
)
from app.providers.mock import MockProvider
from app.providers.reliable import ReliableProvider


def test_reliable_provider_preserves_identity_and_capabilities():
    provider = ReliableProvider(MockProvider())

    assert provider.name == "mock"
    assert provider.capabilities.text is True
    assert provider.capabilities.streaming is False


@pytest.mark.asyncio
async def test_reliable_provider_returns_successful_response():
    provider = ReliableProvider(MockProvider())
    request = Request("Merhaba JARVIS")

    response = await provider.generate(
        request,
        Context(),
    )

    assert response.provider == "mock"
    assert response.model == "mock-model"
    assert response.text == "Mock yanıtı: Merhaba JARVIS"


def test_reliable_provider_rejects_invalid_configuration():
    with pytest.raises(ValueError):
        ReliableProvider(MockProvider(), timeout_seconds=0)

    with pytest.raises(ValueError):
        ReliableProvider(MockProvider(), max_retries=-1)


class FlakyProvider(MockProvider):
    def __init__(self, failures: int) -> None:
        self.failures = failures
        self.calls = 0

    async def generate(
        self,
        request,
        context,
        *,
        model=None,
        system_prompt=None,
        tools=None,
    ):
        self.calls += 1

        if self.calls <= self.failures:
            raise ProviderUnavailableError("temporary failure")

        return ModelResponse(
            text="success",
            model=model or "flaky-model",
            provider=self.name,
        )


@pytest.mark.asyncio
async def test_reliable_provider_retries_temporary_failure():
    provider = FlakyProvider(failures=2)
    reliable = ReliableProvider(
        provider,
        max_retries=2,
    )

    response = await reliable.generate(
        Request("retry"),
        Context(),
    )

    assert response.text == "success"
    assert provider.calls == 3


@pytest.mark.asyncio
async def test_reliable_provider_stops_after_retry_limit():
    provider = FlakyProvider(failures=5)
    reliable = ReliableProvider(
        provider,
        max_retries=2,
    )

    with pytest.raises(ProviderUnavailableError):
        await reliable.generate(
            Request("retry"),
            Context(),
        )

    assert provider.calls == 3


class SlowProvider(MockProvider):
    async def generate(
        self,
        request,
        context,
        *,
        model=None,
        system_prompt=None,
        tools=None,
    ):
        await asyncio.sleep(0.05)

        return ModelResponse(
            text="too late",
            model="slow-model",
            provider=self.name,
        )


@pytest.mark.asyncio
async def test_reliable_provider_enforces_timeout():
    provider = ReliableProvider(
        SlowProvider(),
        timeout_seconds=0.001,
    )

    with pytest.raises(asyncio.TimeoutError):
        await provider.generate(
            Request("timeout"),
            Context(),
        )
