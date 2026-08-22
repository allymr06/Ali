from __future__ import annotations

import pytest

from app.core.models import (
    Context,
    Request,
)
from app.providers.base import (
    AIProvider,
    ModelCapabilities,
    ModelResponse,
    ProviderCapability,
    ProviderRateLimitError,
)
from app.providers.gateway import (
    ProviderGateway,
)
from app.providers.models import (
    RoutingDecision,
    TaskType,
)
from app.providers.registry import (
    ProviderRegistry,
)


class FixedRouter:
    def __init__(
        self,
        decision,
    ) -> None:
        self.decision = decision

        class EmptyCatalog:
            def contains(
                self,
                _provider,
                _model,
            ):
                return False

        self.catalog = EmptyCatalog()

    def required_capabilities(
        self,
        request,
        **_kwargs,
    ):
        return frozenset(
            {
                ProviderCapability.TEXT,
            }
        )

    def route(
        self,
        request,
        **_kwargs,
    ):
        return self.decision


class FakeProvider(AIProvider):
    def __init__(
        self,
        name,
        *,
        configured=True,
        error=None,
        text="ok",
    ) -> None:
        self._name = name
        self._configured = configured
        self.error = error
        self.text = text
        self.calls = 0

    @property
    def name(self):
        return self._name

    @property
    def capabilities(self):
        return ModelCapabilities(
            text=True,
        )

    @property
    def is_configured(self):
        return self._configured

    async def generate(
        self,
        request,
        context,
        *,
        model=None,
        system_prompt=None,
        tools=None,
        response_format=None,
    ):
        self.calls += 1

        if self.error is not None:
            raise self.error

        return ModelResponse(
            text=self.text,
            model=model or "model",
            provider=self.name,
        )


def decision():
    return RoutingDecision(
        provider="gemini",
        model="gemini-model",
        task_type=TaskType.SIMPLE,
        required_capabilities=frozenset(
            {
                ProviderCapability.TEXT,
            }
        ),
        reason="test",
        fallback_candidates=(
            (
                "openai",
                "openai-model",
            ),
        ),
    )


@pytest.mark.asyncio
async def test_unconfigured_provider_is_not_used_as_fallback(
) -> None:
    gemini_error = (
        ProviderRateLimitError(
            "Gemini rate limit exceeded.",
            provider="gemini",
            status_code=429,
        )
    )

    gemini = FakeProvider(
        "gemini",
        error=gemini_error,
    )

    openai = FakeProvider(
        "openai",
        configured=False,
    )

    registry = ProviderRegistry(
        default_provider="gemini"
    )

    registry.register(gemini)
    registry.register(openai)

    gateway = ProviderGateway(
        registry,
        router=FixedRouter(
            decision()
        ),
        max_retries=0,
        fallback_enabled=True,
    )

    with pytest.raises(
        ProviderRateLimitError,
        match="Gemini rate limit",
    ):
        await gateway.generate(
            Request("status"),
            Context(),
        )

    assert gemini.calls == 1
    assert openai.calls == 0


@pytest.mark.asyncio
async def test_configured_provider_remains_valid_fallback(
) -> None:
    gemini = FakeProvider(
        "gemini",
        error=ProviderRateLimitError(
            "Gemini rate limit exceeded.",
            provider="gemini",
            status_code=429,
        ),
    )

    openai = FakeProvider(
        "openai",
        configured=True,
        text="fallback response",
    )

    registry = ProviderRegistry(
        default_provider="gemini"
    )

    registry.register(gemini)
    registry.register(openai)

    gateway = ProviderGateway(
        registry,
        router=FixedRouter(
            decision()
        ),
        max_retries=0,
        fallback_enabled=True,
    )

    response = await gateway.generate(
        Request("status"),
        Context(),
    )

    assert (
        response.provider
        == "openai"
    )

    assert (
        response.text
        == "fallback response"
    )

    assert (
        response.metadata[
            "fallback_count"
        ]
        == 1
    )

    assert gemini.calls == 1
    assert openai.calls == 1
