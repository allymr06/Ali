import pytest

from app.core.models import Context, Request
from app.providers.base import (
    AIProvider,
    ModelCapabilities,
    ModelResponse,
    ProviderAuthenticationError,
    ProviderError,
    ProviderRateLimitError,
    ProviderUnavailableError,
)


class FakeProvider(AIProvider):
    @property
    def name(self) -> str:
        return "fake"

    @property
    def capabilities(self) -> ModelCapabilities:
        return ModelCapabilities(
            text=True,
            streaming=True,
            structured_output=True,
            tool_calling=True,
            vision=True,
        )

    async def generate(
        self,
        request,
        context,
        *,
        model=None,
        system_prompt=None,
        tools=None,
    ):
        return ModelResponse(
            text=f"Fake response: {request.text}",
            model=model or "fake-model",
            provider=self.name,
        )


def test_model_capabilities_have_safe_defaults():
    capabilities = ModelCapabilities()

    assert capabilities.text is True
    assert capabilities.streaming is False
    assert capabilities.structured_output is False
    assert capabilities.tool_calling is False
    assert capabilities.vision is False


def test_model_response_contains_provider_information():
    response = ModelResponse(
        text="Merhaba",
        model="test-model",
        provider="test-provider",
    )

    assert response.text == "Merhaba"
    assert response.model == "test-model"
    assert response.provider == "test-provider"
    assert response.tool_calls == []
    assert response.usage == {}


def test_fake_provider_exposes_capabilities():
    provider = FakeProvider()

    assert provider.name == "fake"
    assert provider.capabilities.text is True
    assert provider.capabilities.streaming is True
    assert provider.capabilities.vision is True


@pytest.mark.asyncio
async def test_provider_can_generate_response():
    provider = FakeProvider()
    request = Request("Merhaba JARVIS")
    context = Context()

    response = await provider.generate(
        request,
        context,
        model="fake-model",
    )

    assert response.text == "Fake response: Merhaba JARVIS"
    assert response.model == "fake-model"
    assert response.provider == "fake"


def test_provider_error_hierarchy():
    assert issubclass(ProviderAuthenticationError, ProviderError)
    assert issubclass(ProviderRateLimitError, ProviderError)
    assert issubclass(ProviderUnavailableError, ProviderError)


def test_provider_is_abstract():
    with pytest.raises(TypeError):
        AIProvider()