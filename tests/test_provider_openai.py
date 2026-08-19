from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.config.settings import Settings
from app.core.models import Context, Request
from app.providers.base import (
    ProviderAuthenticationError,
    ProviderRateLimitError,
    ProviderUnavailableError,
)
from app.providers.openai import OpenAIProvider


class FakeCompletions:
    def __init__(self, response=None, error=None):
        self.response = response
        self.error = error
        self.calls = []

    async def create(self, **kwargs):
        self.calls.append(kwargs)

        if self.error is not None:
            raise self.error

        return self.response


class FakeClient:
    def __init__(self, response=None, error=None):
        self.chat = SimpleNamespace(
            completions=FakeCompletions(
                response=response,
                error=error,
            )
        )


def make_response():
    return SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(
                    content="Merhaba Ali",
                    tool_calls=[],
                ),
                finish_reason="stop",
            )
        ],
        usage=SimpleNamespace(
            prompt_tokens=10,
            completion_tokens=5,
            total_tokens=15,
        ),
    )


def make_settings():
    return Settings(
        default_model="test-model",
        provider_timeout_seconds=5.0,
        provider_max_retries=2,
    )


@pytest.mark.asyncio
async def test_openai_provider_identity():
    provider = OpenAIProvider(
        make_settings(),
        client=FakeClient(make_response()),
    )

    assert provider.name == "openai"
    assert provider.capabilities.text is True
    assert provider.capabilities.streaming is True
    assert provider.capabilities.tool_calling is True
    assert provider.capabilities.vision is True


@pytest.mark.asyncio
async def test_openai_provider_generates_response():
    client = FakeClient(make_response())

    provider = OpenAIProvider(
        make_settings(),
        client=client,
    )

    response = await provider.generate(
        Request("Merhaba JARVIS"),
        Context(),
    )

    assert response.text == "Merhaba Ali"
    assert response.provider == "openai"
    assert response.model == "test-model"
    assert response.finish_reason == "stop"
    assert response.usage["total_tokens"] == 15
    assert len(client.chat.completions.calls) == 1


@pytest.mark.asyncio
async def test_openai_provider_passes_system_prompt_and_memories():
    client = FakeClient(make_response())

    provider = OpenAIProvider(
        make_settings(),
        client=client,
    )

    context = Context(
        memories=["Ali JARVIS'i geliştiriyor."]
    )

    await provider.generate(
        Request("Nerede kaldık?"),
        context,
        system_prompt="Sen JARVIS'sin.",
    )

    call = client.chat.completions.calls[0]

    assert call["model"] == "test-model"
    assert call["messages"][0]["role"] == "system"
    assert call["messages"][0]["content"] == "Sen JARVIS'sin."
    assert "Ali JARVIS'i geliştiriyor." in (
        call["messages"][1]["content"]
    )
    assert call["messages"][-1]["content"] == "Nerede kaldık?"


@pytest.mark.asyncio
async def test_openai_provider_authentication_error():
    error = RuntimeError("unauthorized")
    error.status_code = 401

    provider = OpenAIProvider(
        make_settings(),
        client=FakeClient(error=error),
    )

    with pytest.raises(ProviderAuthenticationError):
        await provider.generate(
            Request("test"),
            Context(),
        )


@pytest.mark.asyncio
async def test_openai_provider_rate_limit_error():
    error = RuntimeError("rate limited")
    error.status_code = 429

    provider = OpenAIProvider(
        Settings(
            default_model="test-model",
            provider_timeout_seconds=5.0,
            provider_max_retries=0,
        ),
        client=FakeClient(error=error),
    )

    with pytest.raises(ProviderRateLimitError):
        await provider.generate(
            Request("test"),
            Context(),
        )


@pytest.mark.asyncio
async def test_openai_provider_requires_client():
    provider = OpenAIProvider(make_settings())

    with pytest.raises(ProviderUnavailableError):
        await provider.generate(
            Request("test"),
            Context(),
        )
