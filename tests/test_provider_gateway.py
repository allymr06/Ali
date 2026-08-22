from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from app.core.models import Context, Request
from app.providers.base import (
    AIProvider,
    ModelCapabilities,
    ModelResponse,
    ModelStreamChunk,
    ProviderAuthenticationError,
    ProviderCapability,
    ProviderCapabilityError,
    ProviderError,
    ProviderInvalidResponseError,
    ProviderTimeoutError,
    ProviderUnavailableError,
)
from app.providers.catalog import ModelCatalog
from app.providers.gateway import ProviderGateway
from app.providers.models import ModelProfile, TaskType
from app.providers.registry import ProviderRegistry
from app.providers.router import ModelRouter
from app.reliability.circuit import CircuitState


class StaticProvider(AIProvider):
    def __init__(
        self,
        name: str,
        *,
        capabilities: ModelCapabilities | None = None,
        response=None,
        errors: list[Exception] | None = None,
    ) -> None:
        self._name = name
        self._capabilities = capabilities or ModelCapabilities()
        self.response = response
        self.errors = list(errors or [])
        self.calls = 0
        self.models: list[str | None] = []
        self.response_formats: list[dict | None] = []

    @property
    def name(self) -> str:
        return self._name

    @property
    def capabilities(self) -> ModelCapabilities:
        return self._capabilities

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
        self.models.append(model)
        self.response_formats.append(response_format)
        if self.errors:
            raise self.errors.pop(0)
        return self.response or ModelResponse(
            text=f"{self.name}:{request.text}",
            model=model or f"{self.name}-model",
            provider=self.name,
        )


@pytest.mark.asyncio
async def test_gateway_circuit_opens_and_skips_repeated_provider_calls() -> None:
    provider = StaticProvider(
        "unstable",
        errors=[
            ProviderUnavailableError("down", provider="unstable"),
            ProviderUnavailableError("down", provider="unstable"),
        ],
    )
    gateway = ProviderGateway(
        registry_with(provider),
        max_retries=0,
        fallback_enabled=False,
        circuit_failure_threshold=2,
        circuit_recovery_seconds=60,
    )

    for _ in range(2):
        with pytest.raises(ProviderUnavailableError):
            await gateway.generate(Request("test"), Context())
    with pytest.raises(ProviderUnavailableError, match="circuit"):
        await gateway.generate(Request("test"), Context())

    assert provider.calls == 2
    assert gateway.health("unstable").circuit_state is CircuitState.OPEN


def registry_with(*providers: AIProvider, default: str | None = None):
    registry = ProviderRegistry(default_provider=default)
    for provider in providers:
        registry.register(provider)
    return registry


def profile(
    provider: str,
    model: str,
    *,
    capabilities: ModelCapabilities | None = None,
    priority: int = 100,
) -> ModelProfile:
    return ModelProfile(
        provider=provider,
        model=model,
        capabilities=capabilities or ModelCapabilities(),
        priority=priority,
    )


def test_capabilities_report_missing_requirements():
    capabilities = ModelCapabilities(text=True, tool_calling=True)
    required = frozenset(
        {ProviderCapability.TEXT, ProviderCapability.VISION}
    )

    assert capabilities.supports(required) is False
    assert capabilities.missing(required) == {ProviderCapability.VISION}


@pytest.mark.parametrize(
    "kwargs,error",
    [
        ({"text": None}, TypeError),
        ({"model": ""}, ValueError),
        ({"provider": ""}, ValueError),
        ({"tool_calls": ["bad"]}, TypeError),
        ({"usage": {"total_tokens": -1}}, ValueError),
    ],
)
def test_model_response_rejects_invalid_contract(kwargs, error):
    values = {"text": "ok", "model": "model", "provider": "provider"}
    values.update(kwargs)

    with pytest.raises(error):
        ModelResponse(**values)


def test_model_catalog_orders_capable_candidates_by_priority_and_cost():
    catalog = ModelCatalog()
    capabilities = ModelCapabilities(text=True, tool_calling=True)
    catalog.register(
        ModelProfile(
            provider="one",
            model="expensive",
            capabilities=capabilities,
            priority=20,
            input_cost_per_million=10,
        )
    )
    catalog.register(
        ModelProfile(
            provider="one",
            model="cheap",
            capabilities=capabilities,
            priority=10,
            input_cost_per_million=1,
        )
    )

    candidates = catalog.candidates(
        task_type=TaskType.AGENTIC,
        required=frozenset({ProviderCapability.TOOL_CALLING}),
    )

    assert [item.model for item in candidates] == ["cheap", "expensive"]


def test_model_catalog_rejects_duplicate_and_unknown_model():
    catalog = ModelCatalog()
    catalog.register(profile("one", "model"))

    with pytest.raises(ValueError):
        catalog.register(profile("one", "model"))
    with pytest.raises(KeyError):
        catalog.get("one", "missing")


@pytest.mark.parametrize(
    "case_request,tools,expected",
    [
        (Request("hello"), None, TaskType.SIMPLE),
        (
            Request("Please analyze and design the complete architecture"),
            None,
            TaskType.COMPLEX,
        ),
        (Request("monitor this in background"), None, TaskType.LONG_RUNNING),
        (Request("inspect", metadata={"vision": True}), None, TaskType.VISION),
        (Request("use tool"), [{"type": "function"}], TaskType.AGENTIC),
    ],
)
def test_router_classifies_task_types(case_request, tools, expected):
    provider = StaticProvider("default")
    router = ModelRouter(registry_with(provider))

    assert router.classify(case_request, tools=tools) is expected


def test_router_honors_explicit_task_type():
    router = ModelRouter(registry_with(StaticProvider("default")))

    assert router.classify(
        Request("hello", metadata={"task_type": "standard"})
    ) is TaskType.STANDARD

    with pytest.raises(ValueError, match="Unknown task type"):
        router.classify(Request("hello"), task_type="unknown")


def test_router_selects_capable_alternative_to_default():
    text = StaticProvider("text", capabilities=ModelCapabilities(text=True))
    vision_caps = ModelCapabilities(text=True, vision=True)
    vision = StaticProvider("vision", capabilities=vision_caps)
    registry = registry_with(text, vision, default="text")
    catalog = ModelCatalog()
    catalog.register(profile("text", "text-model"))
    catalog.register(profile("vision", "vision-model", capabilities=vision_caps))
    router = ModelRouter(registry, catalog)

    decision = router.route(Request("inspect", metadata={"vision": True}))

    assert decision.provider == "vision"
    assert decision.model == "vision-model"
    assert decision.reason == "capability_routing"


def test_router_discovers_capable_provider_without_catalog_profile():
    text = StaticProvider("text", capabilities=ModelCapabilities(text=True))
    vision = StaticProvider(
        "vision",
        capabilities=ModelCapabilities(text=True, vision=True),
    )
    router = ModelRouter(registry_with(text, vision, default="text"))

    decision = router.route(Request("inspect", metadata={"vision": True}))

    assert decision.provider == "vision"
    assert decision.model is None


def test_router_selects_model_matching_task_type():
    capabilities = ModelCapabilities(text=True)
    provider = StaticProvider("one", capabilities=capabilities)
    registry = registry_with(provider)
    catalog = ModelCatalog()
    catalog.register(
        ModelProfile(
            provider="one",
            model="fast",
            capabilities=capabilities,
            task_types=frozenset({TaskType.SIMPLE}),
        )
    )
    catalog.register(
        ModelProfile(
            provider="one",
            model="deep",
            capabilities=capabilities,
            task_types=frozenset({TaskType.COMPLEX}),
        )
    )
    router = ModelRouter(registry, catalog)

    simple = router.route(Request("hello"))
    complex_decision = router.route(Request("analyze this architecture"))

    assert simple.model == "fast"
    assert complex_decision.model == "deep"


def test_router_rejects_incapable_user_override():
    registry = registry_with(
        StaticProvider("text", capabilities=ModelCapabilities(text=True))
    )
    router = ModelRouter(registry)

    with pytest.raises(ProviderCapabilityError, match="vision"):
        router.route(
            Request(
                "inspect",
                metadata={"provider": "text", "vision": True},
            )
        )


@pytest.mark.asyncio
async def test_gateway_normalizes_compatible_provider_response():
    provider = StaticProvider(
        "one",
        response=SimpleNamespace(
            text="normalized",
            model="one-model",
            provider="one",
            finish_reason="stop",
            tool_calls=[],
            usage={"total_tokens": 3},
            metadata={"source": "fake"},
        ),
    )
    gateway = ProviderGateway(registry_with(provider), max_retries=0)

    response = await gateway.generate(Request("hello"), Context())

    assert isinstance(response, ModelResponse)
    assert response.text == "normalized"
    assert response.metadata["gateway_attempt"] == 1
    assert response.metadata["task_type"] == "simple"


@pytest.mark.asyncio
async def test_gateway_rejects_provider_identity_mismatch():
    provider = StaticProvider(
        "one",
        response=ModelResponse(text="bad", model="m", provider="other"),
    )
    gateway = ProviderGateway(registry_with(provider), max_retries=0)

    with pytest.raises(ProviderInvalidResponseError, match="identity mismatch"):
        await gateway.generate(Request("hello"), Context())


@pytest.mark.asyncio
async def test_gateway_sanitizes_unexpected_provider_exception():
    provider = StaticProvider("one", errors=[RuntimeError("secret details")])
    gateway = ProviderGateway(registry_with(provider), max_retries=0)

    with pytest.raises(ProviderError, match="failed unexpectedly") as captured:
        await gateway.generate(Request("hello"), Context())

    assert "secret details" not in str(captured.value)
    assert gateway.health("one").last_error == str(captured.value)


@pytest.mark.asyncio
async def test_gateway_retries_only_retryable_errors():
    retryable = StaticProvider(
        "retryable",
        errors=[ProviderUnavailableError("temporary")],
    )
    gateway = ProviderGateway(
        registry_with(retryable),
        max_retries=1,
        retry_backoff_seconds=0,
    )

    response = await gateway.generate(Request("hello"), Context())

    assert response.text.startswith("retryable:")
    assert retryable.calls == 2
    assert response.metadata["gateway_attempt"] == 2

    permanent = StaticProvider(
        "permanent",
        errors=[ProviderAuthenticationError("bad key")],
    )
    permanent_gateway = ProviderGateway(
        registry_with(permanent),
        max_retries=5,
        retry_backoff_seconds=0,
    )

    with pytest.raises(ProviderAuthenticationError):
        await permanent_gateway.generate(Request("hello"), Context())
    assert permanent.calls == 1


@pytest.mark.asyncio
async def test_gateway_falls_back_and_records_health():
    primary = StaticProvider(
        "primary",
        errors=[ProviderUnavailableError("down")],
    )
    secondary = StaticProvider("secondary")
    registry = registry_with(primary, secondary, default="primary")
    catalog = ModelCatalog()
    catalog.register(profile("primary", "primary-model", priority=1))
    catalog.register(profile("secondary", "secondary-model", priority=2))
    gateway = ProviderGateway(
        registry,
        router=ModelRouter(registry, catalog),
        max_retries=0,
    )

    response = await gateway.generate(Request("hello"), Context())

    assert response.provider == "secondary"
    assert response.metadata["fallback_count"] == 1
    assert gateway.health("primary").failures == 1
    assert gateway.health("secondary").successes == 1


@pytest.mark.asyncio
async def test_gateway_never_hides_live_provider_failure_with_mock_echo():
    primary = StaticProvider(
        "openai",
        errors=[ProviderUnavailableError("down", provider="openai")],
    )
    mock = StaticProvider("mock")
    registry = registry_with(primary, mock, default="openai")
    catalog = ModelCatalog()
    catalog.register(profile("openai", "live-model", priority=1))
    catalog.register(profile("mock", "mock-model", priority=2))
    gateway = ProviderGateway(
        registry,
        router=ModelRouter(registry, catalog),
        max_retries=0,
    )

    with pytest.raises(ProviderUnavailableError):
        await gateway.generate(Request("hello"), Context())

    assert primary.calls == 1
    assert mock.calls == 0


@pytest.mark.asyncio
async def test_gateway_does_not_fallback_for_user_override():
    primary = StaticProvider(
        "primary",
        errors=[ProviderUnavailableError("down")],
    )
    secondary = StaticProvider("secondary")
    registry = registry_with(primary, secondary, default="primary")
    catalog = ModelCatalog()
    catalog.register(profile("primary", "primary-model"))
    catalog.register(profile("secondary", "secondary-model"))
    gateway = ProviderGateway(
        registry,
        router=ModelRouter(registry, catalog),
        max_retries=0,
    )

    with pytest.raises(ProviderUnavailableError):
        await gateway.generate(
            Request("hello", metadata={"provider": "primary"}),
            Context(),
        )

    assert secondary.calls == 0


@pytest.mark.asyncio
async def test_gateway_passes_model_override_to_provider():
    provider = StaticProvider("one")
    gateway = ProviderGateway(registry_with(provider), max_retries=0)

    response = await gateway.generate(
        Request("hello", metadata={"model": "chosen-model"}),
        Context(),
    )

    assert provider.models == ["chosen-model"]
    assert response.model == "chosen-model"


@pytest.mark.asyncio
async def test_gateway_enforces_and_passes_structured_output_contract():
    incapable = StaticProvider("plain")
    with pytest.raises(ProviderCapabilityError, match="structured_output"):
        await ProviderGateway(
            registry_with(incapable),
            max_retries=0,
        ).generate(
            Request("hello"),
            Context(),
            response_format={"type": "json_object"},
        )

    capable = StaticProvider(
        "structured",
        capabilities=ModelCapabilities(text=True, structured_output=True),
    )
    gateway = ProviderGateway(registry_with(capable), max_retries=0)
    schema = {"type": "json_object"}

    await gateway.generate(
        Request("hello"),
        Context(),
        response_format=schema,
    )

    assert capable.response_formats == [schema]

    with pytest.raises(TypeError, match="response_format"):
        await gateway.generate(
            Request("hello"),
            Context(),
            response_format="json",
        )


def test_router_rejects_invalid_capability_values():
    router = ModelRouter(registry_with(StaticProvider("one")))

    with pytest.raises(TypeError, match="ProviderCapability"):
        router.required_capabilities(Request("hello"), extra=["vision"])


@pytest.mark.asyncio
async def test_gateway_estimates_known_model_cost():
    provider = StaticProvider(
        "priced",
        response=ModelResponse(
            text="ok",
            model="priced-model",
            provider="priced",
            usage={"input_tokens": 1_000, "output_tokens": 500},
        ),
    )
    registry = registry_with(provider)
    catalog = ModelCatalog()
    catalog.register(
        ModelProfile(
            provider="priced",
            model="priced-model",
            input_cost_per_million=2,
            output_cost_per_million=4,
            max_context_tokens=10_000,
        )
    )
    gateway = ProviderGateway(
        registry,
        router=ModelRouter(registry, catalog),
        max_retries=0,
    )

    response = await gateway.generate(Request("hello"), Context())

    assert response.metadata["estimated_cost_usd"] == pytest.approx(0.004)
    assert response.metadata["model_context_limit"] == 10_000


@pytest.mark.asyncio
async def test_gateway_timeout_cancels_provider_operation():
    stopped = asyncio.Event()

    class SlowProvider(StaticProvider):
        async def generate(self, *args, **kwargs):
            try:
                await asyncio.Event().wait()
            finally:
                stopped.set()

    provider = SlowProvider("slow")
    gateway = ProviderGateway(
        registry_with(provider),
        timeout_seconds=0.01,
        max_retries=0,
    )

    with pytest.raises(ProviderTimeoutError):
        await gateway.generate(Request("hello"), Context())

    assert stopped.is_set()


@pytest.mark.asyncio
async def test_gateway_cancel_event_stops_provider_operation():
    started = asyncio.Event()
    stopped = asyncio.Event()

    class SlowProvider(StaticProvider):
        async def generate(self, *args, **kwargs):
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                stopped.set()

    provider = SlowProvider("slow")
    gateway = ProviderGateway(registry_with(provider), max_retries=0)
    cancel_event = asyncio.Event()
    running = asyncio.create_task(
        gateway.generate(
            Request("hello"),
            Context(),
            cancel_event=cancel_event,
        )
    )
    await started.wait()
    cancel_event.set()

    with pytest.raises(asyncio.CancelledError):
        await running
    assert stopped.is_set()


class StreamingProvider(StaticProvider):
    def __init__(self, name: str, events: list[object]) -> None:
        super().__init__(
            name,
            capabilities=ModelCapabilities(text=True, streaming=True),
        )
        self.events = list(events)
        self.stream_calls = 0

    async def stream(self, request, context, **kwargs):
        self.stream_calls += 1
        events = self.events.pop(0)
        if isinstance(events, Exception):
            raise events
        for event in events:
            if isinstance(event, Exception):
                raise event
            yield event


@pytest.mark.asyncio
async def test_gateway_streams_normalized_chunks_with_metadata():
    provider = StreamingProvider(
        "stream",
        [[ModelStreamChunk(text="hi", model="m", provider="stream")]],
    )
    gateway = ProviderGateway(registry_with(provider), max_retries=0)

    chunks = [
        chunk
        async for chunk in gateway.stream(Request("hello"), Context())
    ]

    assert [chunk.text for chunk in chunks] == ["hi"]
    assert chunks[0].metadata["gateway_attempt"] == 1
    assert gateway.health("stream").successes == 1


@pytest.mark.asyncio
async def test_gateway_retries_stream_only_before_first_chunk():
    provider = StreamingProvider(
        "stream",
        [
            ProviderUnavailableError("before output"),
            [ModelStreamChunk(text="ok", model="m", provider="stream")],
        ],
    )
    gateway = ProviderGateway(
        registry_with(provider),
        max_retries=1,
        retry_backoff_seconds=0,
    )

    chunks = [
        chunk
        async for chunk in gateway.stream(Request("hello"), Context())
    ]

    assert [chunk.text for chunk in chunks] == ["ok"]
    assert provider.stream_calls == 2


@pytest.mark.asyncio
async def test_gateway_does_not_restart_stream_after_output():
    provider = StreamingProvider(
        "stream",
        [
            [
                ModelStreamChunk(text="partial", model="m", provider="stream"),
                ProviderUnavailableError("after output"),
            ],
            [ModelStreamChunk(text="duplicate", model="m", provider="stream")],
        ],
    )
    gateway = ProviderGateway(
        registry_with(provider),
        max_retries=1,
        retry_backoff_seconds=0,
    )
    received = []

    with pytest.raises(ProviderUnavailableError):
        async for chunk in gateway.stream(Request("hello"), Context()):
            received.append(chunk.text)

    assert received == ["partial"]
    assert provider.stream_calls == 1
