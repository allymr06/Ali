from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.config.settings import Settings
from app.core.models import Context, Request
from app.providers.base import ProviderRateLimitError
from app.providers.gemini import GeminiProvider


class FakeCompletions:
    def __init__(self, response=None, error=None) -> None:
        self.response = response
        self.error = error
        self.calls: list[dict[str, object]] = []

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return self.response


class FakeClient:
    def __init__(self, response=None, error=None) -> None:
        self.chat = SimpleNamespace(
            completions=FakeCompletions(response=response, error=error)
        )


def response(text: str = "Gemini response"):
    return SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(content=text, tool_calls=[]),
                finish_reason="stop",
            )
        ],
        usage=SimpleNamespace(total_tokens=7),
    )


@pytest.mark.asyncio
async def test_gemini_provider_uses_gemini_identity_and_model() -> None:
    client = FakeClient(response())
    provider = GeminiProvider(
        Settings(gemini_model="gemini-test"), client=client
    )

    result = await provider.generate(Request("Hello"), Context())

    assert provider.name == "gemini"
    assert result.provider == "gemini"
    assert result.model == "gemini-test"
    assert result.text == "Gemini response"
    assert client.chat.completions.calls[0]["model"] == "gemini-test"


def test_gemini_provider_creates_compatible_client_from_settings() -> None:
    provider = GeminiProvider(
        Settings(
            gemini_api_key="gemini-secret",
            gemini_base_url="https://gemini.example/v1beta/openai/",
        )
    )

    assert provider._client is not None
    assert provider._client.api_key == "gemini-secret"
    assert str(provider._client.base_url).rstrip("/") == (
        "https://gemini.example/v1beta/openai"
    )


@pytest.mark.asyncio
async def test_gemini_errors_keep_provider_identity() -> None:
    error = RuntimeError("quota exhausted")
    error.status_code = 429
    provider = GeminiProvider(Settings(), client=FakeClient(error=error))

    with pytest.raises(ProviderRateLimitError) as captured:
        await provider.generate(Request("Hello"), Context())

    assert captured.value.provider == "gemini"
    assert "Gemini rate limit exceeded" in str(captured.value)
