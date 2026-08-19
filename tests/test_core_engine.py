from __future__ import annotations

import asyncio

from app.core.engine import CoreEngine
from app.core.models import Context, Request
from app.memory.in_memory import InMemoryStore
from app.memory.manager import MemoryManager
from app.providers.mock import MockProvider
from app.providers.registry import ProviderRegistry


def create_engine() -> CoreEngine:
    registry = ProviderRegistry()
    registry.register(
        MockProvider(),
        make_default=True,
    )

    memory_manager = MemoryManager(
        InMemoryStore()
    )

    return CoreEngine(
        registry,
        memory_manager,
    )


def test_core_engine_handles_request() -> None:
    engine = create_engine()
    request = Request("Merhaba JARVIS")

    response = asyncio.run(
        engine.handle(request)
    )

    assert response.text == "Mock yanıtı: Merhaba JARVIS"
    assert response.metadata["provider"] == "mock"


def test_core_engine_creates_context_when_missing() -> None:
    engine = create_engine()
    request = Request("Test")

    response = asyncio.run(
        engine.handle(request)
    )

    assert response.request_id == request.request_id
    assert response.text == "Mock yanıtı: Test"


def test_core_engine_accepts_existing_context() -> None:
    engine = create_engine()
    request = Request("Mevcut context testi")
    context = Context()

    response = asyncio.run(
        engine.handle(request, context)
    )

    assert response.text == "Mock yanıtı: Mevcut context testi"


def test_core_engine_uses_registered_default_provider() -> None:
    registry = ProviderRegistry()
    provider = MockProvider()

    registry.register(
        provider,
        make_default=True,
    )

    memory_manager = MemoryManager(
        InMemoryStore()
    )

    engine = CoreEngine(
        registry,
        memory_manager,
    )

    request = Request("Provider testi")

    response = asyncio.run(
        engine.handle(request)
    )

    assert response.metadata["provider"] == "mock"
def test_core_engine_executes_tool_call() -> None:
    from types import SimpleNamespace

    from app.core.models import ToolDefinition
    from app.tools.executor import ToolExecutor

    registry = ProviderRegistry()
    memory_manager = MemoryManager(InMemoryStore())
    tool_executor = ToolExecutor()

    called = {"value": False}

    def get_weather(city: str) -> str:
        called["value"] = True
        return f"{city}: sunny"

    tool_executor.register(
        ToolDefinition(
            name="get_weather",
            description="Get weather information.",
        ),
        get_weather,
    )

    class ToolCallingProvider(MockProvider):
        async def generate(self, request, context, **kwargs):
            return SimpleNamespace(
                text="",
                model="mock-model",
                provider="mock",
                finish_reason="tool_calls",
                tool_calls=[
                    {
                        "id": "call_1",
                        "type": "function",
                        "function": {
                            "name": "get_weather",
                            "arguments": '{"city":"Baku"}',
                        },
                    }
                ],
                usage={},
                metadata={},
            )

    registry.register(
        ToolCallingProvider(),
        make_default=True,
    )

    engine = CoreEngine(
        registry,
        memory_manager,
        tool_executor=tool_executor,
    )

    response = asyncio.run(
        engine.handle(Request("Baku'de hava nas?l?"))
    )

    assert called["value"] is True
    assert response.metadata["tool_calls"] == 1

def test_core_engine_sends_tool_result_back_to_provider() -> None:
    from types import SimpleNamespace

    from app.core.models import ToolDefinition
    from app.tools.executor import ToolExecutor

    registry = ProviderRegistry()
    memory_manager = MemoryManager(InMemoryStore())
    tool_executor = ToolExecutor()

    tool_executor.register(
        ToolDefinition(
            name="get_weather",
            description="Get weather information.",
        ),
        lambda city: f"{city}: sunny",
    )

    calls = []

    class TwoStepProvider(MockProvider):
        async def generate(self, request, context, **kwargs):
            calls.append(context.values.get("messages", []).copy())

            if len(calls) == 1:
                return SimpleNamespace(
                    text="",
                    model="mock-model",
                    provider="mock",
                    finish_reason="tool_calls",
                    tool_calls=[
                        {
                            "id": "call_1",
                            "type": "function",
                            "function": {
                                "name": "get_weather",
                                "arguments": '{"city":"Baku"}',
                            },
                        }
                    ],
                    usage={},
                    metadata={},
                )

            messages = context.values.get("messages", [])

            assert any(
                message.get("role") == "tool"
                and message.get("tool_call_id") == "call_1"
                and message.get("content") == "Baku: sunny"
                for message in messages
            )

            return SimpleNamespace(
                text="Baku'de hava gunesli.",
                model="mock-model",
                provider="mock",
                finish_reason="stop",
                tool_calls=[],
                usage={},
                metadata={},
            )

    registry.register(
        TwoStepProvider(),
        make_default=True,
    )

    engine = CoreEngine(
        registry,
        memory_manager,
        tool_executor=tool_executor,
    )

    response = asyncio.run(
        engine.handle(Request("Baku'de hava nas?l?"))
    )

    assert response.text == "Baku'de hava gunesli."
    assert len(calls) == 2

def test_core_engine_completes_tool_call_loop() -> None:
    from types import SimpleNamespace

    from app.core.models import ToolDefinition
    from app.tools.executor import ToolExecutor

    registry = ProviderRegistry()
    memory_manager = MemoryManager(InMemoryStore())
    tool_executor = ToolExecutor()

    tool_executor.register(
        ToolDefinition(
            name="get_weather",
            description="Get weather information.",
        ),
        lambda city: f"{city}: sunny",
    )

    calls = []

    class AgentProvider(MockProvider):
        async def generate(self, request, context, **kwargs):
            calls.append(
                list(
                    context.values.get(
                        "messages",
                        [],
                    )
                )
            )

            if len(calls) == 1:
                return SimpleNamespace(
                    text="",
                    model="mock-model",
                    provider="mock",
                    finish_reason="tool_calls",
                    tool_calls=[
                        {
                            "id": "call_weather",
                            "type": "function",
                            "function": {
                                "name": "get_weather",
                                "arguments": '{"city":"Baku"}',
                            },
                        }
                    ],
                    usage={},
                    metadata={},
                )

            assert any(
                message.get("role") == "tool"
                and message.get("tool_call_id") == "call_weather"
                and message.get("content") == "Baku: sunny"
                for message in context.values["messages"]
            )

            return SimpleNamespace(
                text="Baku'de hava gunesli.",
                model="mock-model",
                provider="mock",
                finish_reason="stop",
                tool_calls=[],
                usage={},
                metadata={},
            )

    registry.register(
        AgentProvider(),
        make_default=True,
    )

    engine = CoreEngine(
        registry,
        memory_manager,
        tool_executor=tool_executor,
    )

    response = asyncio.run(
        engine.handle(
            Request("Baku'de hava nas?l?")
        )
    )

    assert response.text == "Baku'de hava gunesli."
    assert response.metadata["tool_calls"] == 1
    assert response.metadata["tool_iterations"] == 1
    assert len(calls) == 2

def test_core_engine_sends_tool_error_back_to_provider() -> None:
    from types import SimpleNamespace

    from app.core.models import ToolDefinition
    from app.tools.executor import ToolExecutor

    registry = ProviderRegistry()
    memory_manager = MemoryManager(InMemoryStore())
    tool_executor = ToolExecutor()

    def failing_weather(city: str) -> str:
        raise RuntimeError("Weather service unavailable")

    tool_executor.register(
        ToolDefinition(
            name="get_weather",
            description="Get weather information.",
        ),
        failing_weather,
    )

    calls = []

    class ErrorAwareProvider(MockProvider):
        async def generate(self, request, context, **kwargs):
            calls.append(
                list(
                    context.values.get(
                        "messages",
                        [],
                    )
                )
            )

            if len(calls) == 1:
                return SimpleNamespace(
                    text="",
                    model="mock-model",
                    provider="mock",
                    finish_reason="tool_calls",
                    tool_calls=[
                        {
                            "id": "call_weather",
                            "type": "function",
                            "function": {
                                "name": "get_weather",
                                "arguments": '{"city":"Baku"}',
                            },
                        }
                    ],
                    usage={},
                    metadata={},
                )

            assert any(
                message.get("role") == "tool"
                and message.get("tool_call_id") == "call_weather"
                and "Weather service unavailable"
                in message.get("content", "")
                for message in context.values["messages"]
            )

            return SimpleNamespace(
                text="Hava durumu servisine su anda ulas?lam?yor.",
                model="mock-model",
                provider="mock",
                finish_reason="stop",
                tool_calls=[],
                usage={},
                metadata={},
            )

    registry.register(
        ErrorAwareProvider(),
        make_default=True,
    )

    engine = CoreEngine(
        registry,
        memory_manager,
        tool_executor=tool_executor,
    )

    response = asyncio.run(
        engine.handle(
            Request("Baku'de hava nas?l?")
        )
    )

    assert response.text == (
        "Hava durumu servisine su anda ulas?lam?yor."
    )
    assert len(calls) == 2

def test_core_engine_handles_invalid_tool_arguments() -> None:
    from types import SimpleNamespace

    from app.core.models import ToolDefinition
    from app.tools.executor import ToolExecutor

    registry = ProviderRegistry()
    memory_manager = MemoryManager(InMemoryStore())
    tool_executor = ToolExecutor()

    called = {"value": False}

    def get_weather(city: str) -> str:
        called["value"] = True
        return f"{city}: sunny"

    tool_executor.register(
        ToolDefinition(
            name="get_weather",
            description="Get weather information.",
        ),
        get_weather,
    )

    class InvalidArgumentsProvider(MockProvider):
        async def generate(self, request, context, **kwargs):
            return SimpleNamespace(
                text="Tool cagr?s? icin gecersiz parametreler.",
                model="mock-model",
                provider="mock",
                finish_reason="stop",
                tool_calls=[
                    {
                        "id": "call_invalid",
                        "type": "function",
                        "function": {
                            "name": "get_weather",
                            "arguments": '{"city":"Baku"',
                        },
                    }
                ],
                usage={},
                metadata={},
            )

    registry.register(
        InvalidArgumentsProvider(),
        make_default=True,
    )

    engine = CoreEngine(
        registry,
        memory_manager,
        tool_executor=tool_executor,
    )

    response = asyncio.run(
        engine.handle(
            Request("Baku'de hava nas?l?")
        )
    )

    assert called["value"] is False
    assert response.text == (
        "Tool cagr?s? icin gecersiz parametreler."
    )


def test_core_engine_passes_registered_tools_to_provider() -> None:
    from types import SimpleNamespace

    from app.core.models import ToolDefinition
    from app.tools.executor import ToolExecutor

    registry = ProviderRegistry()
    memory_manager = MemoryManager(InMemoryStore())
    tool_executor = ToolExecutor()

    tool_executor.register(
        ToolDefinition(
            name="get_weather",
            description="Get weather information.",
        ),
        lambda city: f"{city}: sunny",
    )

    captured = {"tools": None}

    class InspectingProvider(MockProvider):
        async def generate(self, request, context, **kwargs):
            captured["tools"] = kwargs.get("tools")

            return SimpleNamespace(
                text="Haz?r.",
                model="mock-model",
                provider="mock",
                finish_reason="stop",
                tool_calls=[],
                usage={},
                metadata={},
            )

    registry.register(
        InspectingProvider(),
        make_default=True,
    )

    engine = CoreEngine(
        registry,
        memory_manager,
        tool_executor=tool_executor,
    )

    asyncio.run(
        engine.handle(
            Request("Baku'de hava nas?l?")
        )
    )

    assert captured["tools"] == [
        {
            "type": "function",
            "function": {
                "name": "get_weather",
                "description": "Get weather information.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "city": {
                            "type": "string",
                        },
                    },
                    "required": ["city"],
                },
            },
        }
    ]
