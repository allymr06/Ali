from __future__ import annotations

import asyncio

from app.bootstrap import create_application
from app.config.settings import Settings
from app.core.engine import CoreEngine
from app.core.models import (
    Context,
    Request,
    ToolDefinition,
)
from app.memory.in_memory import InMemoryStore
from app.memory.manager import MemoryManager
from app.providers.base import (
    AIProvider,
    ModelCapabilities,
    ModelResponse,
    ModelStreamChunk,
)
from app.providers.gateway import ProviderGateway
from app.providers.ollama_hybrid import (
    OllamaHybridPolicy,
)
from app.providers.registry import ProviderRegistry
from app.tools.executor import ToolExecutor


def make_policy() -> OllamaHybridPolicy:
    return OllamaHybridPolicy(
        enabled=True,
        chat_model="gemma3:4b",
        tool_model="llama3.2:latest",
    )


def test_normal_conversation_routes_to_gemma() -> None:
    decision = make_policy().route(
        Request(
            "Bug\u00fcn biraz s\u0131k\u0131ld\u0131m. "
            "Benimle konu\u015f."
        ),
        provider_name="ollama",
        interaction_kind="general",
    )

    assert decision is not None
    assert decision.role == "chat"
    assert decision.model == "gemma3:4b"
    assert decision.expose_tools is False
    assert decision.system_prompt is not None


def test_notepad_routes_to_llama_tool_model() -> None:
    decision = make_policy().route(
        Request("Notepad a\u00e7."),
        provider_name="ollama",
        interaction_kind="general",
    )

    assert decision is not None
    assert decision.role == "tool"
    assert decision.model == "llama3.2:latest"
    assert decision.expose_tools is True


def test_system_info_routes_to_tool_model() -> None:
    decision = make_policy().route(
        Request(
            "Bu bilgisayar\u0131n sistem "
            "bilgilerini g\u00f6ster."
        ),
        provider_name="ollama",
        interaction_kind="general",
    )

    assert decision is not None
    assert decision.role == "tool"


def test_deterministic_chain_stays_on_tool_model() -> None:
    decision = make_policy().route(
        Request(
            "Windows uygulamalar\u0131n\u0131 listele."
        ),
        provider_name="ollama",
        interaction_kind="general",
        deterministic_tool_name=(
            "list_windows_applications"
        ),
    )

    assert decision is not None
    assert decision.role == "tool"
    assert (
        decision.reason
        == "deterministic_tool_chain"
    )


def test_identity_remains_core_owned() -> None:
    decision = make_policy().route(
        Request("sen kimsin?"),
        provider_name="ollama",
        interaction_kind="identity",
    )

    assert decision is None


def test_explicit_model_override_is_preserved() -> None:
    request = Request("Merhaba.")
    request.metadata["model"] = "custom-model"

    decision = make_policy().route(
        request,
        provider_name="ollama",
        interaction_kind="social",
    )

    assert decision is None


def test_non_ollama_provider_is_untouched() -> None:
    decision = make_policy().route(
        Request("Merhaba."),
        provider_name="openai",
        interaction_kind="social",
    )

    assert decision is None


class CapturingOllamaProvider(AIProvider):
    def __init__(self) -> None:
        self.calls = []
        self.stream_calls = []

    @property
    def name(self) -> str:
        return "ollama"

    @property
    def capabilities(self) -> ModelCapabilities:
        return ModelCapabilities(
            text=True,
            streaming=True,
            structured_output=True,
            tool_calling=True,
            vision=False,
        )

    @property
    def is_configured(self) -> bool:
        return True

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
        self.calls.append(
            {
                "model": model,
                "system_prompt": system_prompt,
                "tools": tools,
            }
        )

        return ModelResponse(
            text="Tamam.",
            model=model or "llama3.2:latest",
            provider="ollama",
            finish_reason="stop",
        )



    async def stream(
        self,
        request,
        context,
        *,
        model=None,
        system_prompt=None,
        tools=None,
    ):
        self.stream_calls.append(
            {
                "model": model,
                "system_prompt": system_prompt,
                "tools": tools,
            }
        )

        yield ModelStreamChunk(
            text="Mer",
            model=model or "gemma3:4b",
            provider="ollama",
        )

        yield ModelStreamChunk(
            text="haba",
            model=model or "gemma3:4b",
            provider="ollama",
            finish_reason="stop",
            usage={
                "completion_tokens": 2,
                "total_tokens": 4,
            },
        )

def make_core():
    provider = CapturingOllamaProvider()

    registry = ProviderRegistry(
        default_provider="ollama",
    )

    registry.register(provider)

    executor = ToolExecutor()

    def launch(application: str):
        return {
            "application": application,
        }

    executor.register(
        ToolDefinition(
            name="launch_windows_application",
            description=(
                "Launch a Windows application."
            ),
        ),
        launch,
    )

    gateway = ProviderGateway(
        registry,
        max_retries=0,
        fallback_enabled=False,
    )

    engine = CoreEngine(
        provider_registry=registry,
        memory_manager=MemoryManager(
            InMemoryStore()
        ),
        tool_executor=executor,
        provider_gateway=gateway,
        ollama_hybrid_policy=make_policy(),
    )

    return engine, provider


def test_core_chat_uses_gemma_with_zero_tools() -> None:
    engine, provider = make_core()

    response = asyncio.run(
        engine.handle(
            Request(
                "Bug\u00fcn biraz s\u0131k\u0131ld\u0131m. "
                "Benimle konu\u015f."
            ),
            Context(),
        )
    )

    assert len(provider.calls) == 1

    call = provider.calls[0]

    assert call["model"] == "gemma3:4b"
    assert call["tools"] is None
    assert "JARVIS" in call["system_prompt"]

    assert (
        response.metadata["hybrid_model_role"]
        == "chat"
    )

    assert response.metadata["model"] == "gemma3:4b"


def test_core_action_uses_llama_and_retains_tools() -> None:
    engine, provider = make_core()

    response = asyncio.run(
        engine.handle(
            Request("Notepad a\u00e7."),
            Context(),
        )
    )

    assert len(provider.calls) == 1

    call = provider.calls[0]

    assert call["model"] == "llama3.2:latest"
    assert call["tools"] is not None

    names = {
        item["function"]["name"]
        for item in call["tools"]
    }

    assert (
        "launch_windows_application"
        in names
    )

    assert (
        response.metadata["hybrid_model_role"]
        == "tool"
    )




def test_core_chat_streams_gemma_without_generate(
) -> None:
    engine, provider = make_core()
    updates = []

    response = asyncio.run(
        engine.handle(
            Request(
                "Bug\u00fcn biraz s\u0131k\u0131ld\u0131m. "
                "Benimle konu\u015f."
            ),
            Context(),
            stream_callback=updates.append,
        )
    )

    assert provider.calls == []
    assert len(provider.stream_calls) == 1

    call = provider.stream_calls[0]

    assert call["model"] == "gemma3:4b"
    assert call["tools"] is None

    assert updates == [
        "Mer",
        "Merhaba",
    ]

    assert response.text == "Merhaba"

    assert (
        response.metadata[
            "provider_metadata"
        ]["streamed"]
        is True
    )


def test_core_tool_route_does_not_stream(
) -> None:
    engine, provider = make_core()
    updates = []

    response = asyncio.run(
        engine.handle(
            Request(
                "Notepad a\u00e7."
            ),
            Context(),
            stream_callback=updates.append,
        )
    )

    assert provider.stream_calls == []
    assert len(provider.calls) == 1
    assert updates == []

    assert (
        response.metadata[
            "hybrid_model_role"
        ]
        == "tool"
    )

def test_hybrid_settings_load_from_environment(
    monkeypatch,
) -> None:
    monkeypatch.setenv(
        "JARVIS_OLLAMA_HYBRID_ENABLED",
        "true",
    )

    monkeypatch.setenv(
        "JARVIS_OLLAMA_CHAT_MODEL",
        "gemma3:4b",
    )

    settings = Settings.from_environment()

    assert settings.ollama_hybrid_enabled is True
    assert settings.ollama_chat_model == "gemma3:4b"


def test_bootstrap_registers_gemma_without_tool_capability(
) -> None:
    application = create_application(
        Settings(
            default_provider="ollama",
            default_model="llama3.2:latest",
            ollama_model="llama3.2:latest",
            ollama_enabled=True,
            ollama_hybrid_enabled=True,
            ollama_chat_model="gemma3:4b",
            windows_integrations_enabled=False,
            memory_database_path=None,
            conversation_database_path=None,
            task_database_path=None,
            task_runtime_directory=None,
        )
    )

    try:
        profile = application.model_catalog.get(
            "ollama",
            "gemma3:4b",
        )

        assert profile.capabilities.text is True
        assert profile.capabilities.tool_calling is False

    finally:
        application.close()


def test_bootstrap_starts_two_hybrid_warmers(
    monkeypatch,
) -> None:
    instances = []

    class FakeWarmKeeper:
        def __init__(
            self,
            **kwargs,
        ):
            self.kwargs = kwargs
            self.started = False
            self.closed = False
            instances.append(self)

        def start(self):
            self.started = True
            return True

        def close(self):
            self.closed = True

    monkeypatch.setattr(
        "app.bootstrap.OllamaWarmKeeper",
        FakeWarmKeeper,
    )

    application = create_application(
        Settings(
            default_provider="ollama",
            default_model="llama3.2:latest",
            ollama_model="llama3.2:latest",
            ollama_enabled=True,
            ollama_hybrid_enabled=True,
            ollama_chat_model="gemma3:4b",
            ollama_warm_enabled=True,
            windows_integrations_enabled=False,
            memory_database_path=None,
            conversation_database_path=None,
            task_database_path=None,
            task_runtime_directory=None,
        )
    )

    try:
        assert len(instances) == 2

        assert (
            instances[0].kwargs["model"]
            == "llama3.2:latest"
        )

        assert (
            instances[1].kwargs["model"]
            == "gemma3:4b"
        )

        assert all(
            instance.started
            for instance in instances
        )

        assert (
            application.ollama_warm_keeper
            is instances[0]
        )

        assert (
            application.ollama_chat_warm_keeper
            is instances[1]
        )

    finally:
        application.close()

    assert all(
        instance.closed
        for instance in instances
    )
