from __future__ import annotations
import pytest

import asyncio
from types import SimpleNamespace

from app.core.engine import CoreEngine
from app.core.models import Context, Request, ToolDefinition
from app.memory.in_memory import InMemoryStore
from app.memory.manager import MemoryManager
from app.providers.mock import MockProvider
from app.providers.registry import ProviderRegistry
from app.tools.executor import ToolExecutor


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

    assert response.text == (
        "Mock yanıtı: Mevcut context testi"
    )


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
    registry = ProviderRegistry()
    memory_manager = MemoryManager(
        InMemoryStore()
    )
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
        async def generate(
            self,
            request,
            context,
            **kwargs,
        ):
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
                            "arguments": (
                                '{"city":"Baku"}'
                            ),
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
        engine.handle(
            Request("Baku'de hava nas?l?")
        )
    )

    assert called["value"] is True
    assert response.metadata["tool_calls"] == 1


def test_core_engine_sends_tool_result_back_to_provider() -> None:
    registry = ProviderRegistry()
    memory_manager = MemoryManager(
        InMemoryStore()
    )
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
        async def generate(
            self,
            request,
            context,
            **kwargs,
        ):
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
                            "id": "call_1",
                            "type": "function",
                            "function": {
                                "name": "get_weather",
                                "arguments": (
                                    '{"city":"Baku"}'
                                ),
                            },
                        }
                    ],
                    usage={},
                    metadata={},
                )

            messages = context.values.get(
                "messages",
                [],
            )

            assert any(
                message.get("role") == "tool"
                and message.get(
                    "tool_call_id"
                ) == "call_1"
                and message.get("content")
                == "Baku: sunny"
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
        engine.handle(
            Request("Baku'de hava nas?l?")
        )
    )

    assert response.text == (
        "Baku'de hava gunesli."
    )
    assert len(calls) == 2


def test_core_engine_preserves_assistant_tool_call_message() -> None:
    registry = ProviderRegistry()
    memory_manager = MemoryManager(
        InMemoryStore()
    )
    tool_executor = ToolExecutor()

    tool_executor.register(
        ToolDefinition(
            name="get_weather",
            description="Get weather information.",
        ),
        lambda city: f"{city}: sunny",
    )

    calls = []

    class InspectingProvider(MockProvider):
        async def generate(
            self,
            request,
            context,
            **kwargs,
        ):
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
                            "id": "call_preserve",
                            "type": "function",
                            "function": {
                                "name": "get_weather",
                                "arguments": (
                                    '{"city":"Baku"}'
                                ),
                            },
                        }
                    ],
                    usage={},
                    metadata={},
                )

            messages = context.values[
                "messages"
            ]

            assistant_messages = [
                message
                for message in messages
                if message.get("role")
                == "assistant"
            ]

            assert len(
                assistant_messages
            ) == 1

            assistant_message = (
                assistant_messages[0]
            )

            assert (
                assistant_message.get(
                    "tool_calls"
                )
                == [
                    {
                        "id": "call_preserve",
                        "type": "function",
                        "function": {
                            "name": "get_weather",
                            "arguments": (
                                '{"city":"Baku"}'
                            ),
                        },
                    }
                ]
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
        InspectingProvider(),
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
        "Baku'de hava gunesli."
    )


def test_core_engine_preserves_complete_tool_message_chain() -> None:
    registry = ProviderRegistry()
    memory_manager = MemoryManager(
        InMemoryStore()
    )
    tool_executor = ToolExecutor()

    tool_executor.register(
        ToolDefinition(
            name="get_weather",
            description="Get weather information.",
        ),
        lambda city: f"{city}: sunny",
    )

    calls = []

    class ChainProvider(MockProvider):
        async def generate(
            self,
            request,
            context,
            **kwargs,
        ):
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
                            "id": "call_chain",
                            "type": "function",
                            "function": {
                                "name": "get_weather",
                                "arguments": (
                                    '{"city":"Baku"}'
                                ),
                            },
                        }
                    ],
                    usage={},
                    metadata={},
                )

            messages = context.values[
                "messages"
            ]

            assert len(messages) == 3

            user_message = messages[0]
            assistant_message = messages[1]
            tool_message = messages[2]

            assert user_message["role"] == "user"
            assert user_message["content"] == request.text

            assert (
                assistant_message["role"]
                == "assistant"
            )

            assert (
                assistant_message["tool_calls"][0][
                    "id"
                ]
                == "call_chain"
            )

            assert (
                tool_message["role"]
                == "tool"
            )

            assert (
                tool_message["tool_call_id"]
                == "call_chain"
            )

            assert (
                tool_message["content"]
                == "Baku: sunny"
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
        ChainProvider(),
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
        "Baku'de hava gunesli."
    )
    assert len(calls) == 2
    assert response.metadata["tool_calls"] == 1
    assert response.metadata["tool_iterations"] == 1


def test_core_engine_completes_tool_call_loop() -> None:
    registry = ProviderRegistry()
    memory_manager = MemoryManager(
        InMemoryStore()
    )
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
        async def generate(
            self,
            request,
            context,
            **kwargs,
        ):
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
                                "arguments": (
                                    '{"city":"Baku"}'
                                ),
                            },
                        }
                    ],
                    usage={},
                    metadata={},
                )

            assert any(
                message.get("role") == "assistant"
                and message.get("tool_calls")
                for message in context.values[
                    "messages"
                ]
            )

            assert any(
                message.get("role") == "tool"
                and message.get(
                    "tool_call_id"
                ) == "call_weather"
                and message.get("content")
                == "Baku: sunny"
                for message in context.values[
                    "messages"
                ]
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

    assert response.text == (
        "Baku'de hava gunesli."
    )
    assert response.metadata["tool_calls"] == 1
    assert response.metadata["tool_iterations"] == 1
    assert len(calls) == 2


def test_core_engine_sends_tool_error_back_to_provider() -> None:
    registry = ProviderRegistry()
    memory_manager = MemoryManager(
        InMemoryStore()
    )
    tool_executor = ToolExecutor()

    def failing_weather(city: str) -> str:
        raise RuntimeError(
            "Weather service unavailable"
        )

    tool_executor.register(
        ToolDefinition(
            name="get_weather",
            description="Get weather information.",
        ),
        failing_weather,
    )

    calls = []

    class ErrorAwareProvider(MockProvider):
        async def generate(
            self,
            request,
            context,
            **kwargs,
        ):
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
                                "arguments": (
                                    '{"city":"Baku"}'
                                ),
                            },
                        }
                    ],
                    usage={},
                    metadata={},
                )

            assert any(
                message.get("role") == "assistant"
                and message.get("tool_calls")
                for message in context.values[
                    "messages"
                ]
            )

            assert any(
                message.get("role") == "tool"
                and message.get(
                    "tool_call_id"
                ) == "call_weather"
                and "Weather service unavailable"
                in message.get("content", "")
                for message in context.values[
                    "messages"
                ]
            )

            return SimpleNamespace(
                text=(
                    "Hava durumu servisine su "
                    "anda ulas?lam?yor."
                ),
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
        "Hava durumu servisine su "
        "anda ulas?lam?yor."
    )
    assert len(calls) == 2


def test_core_engine_handles_invalid_tool_arguments() -> None:
    registry = ProviderRegistry()
    memory_manager = MemoryManager(
        InMemoryStore()
    )
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
        async def generate(
            self,
            request,
            context,
            **kwargs,
        ):
            return SimpleNamespace(
                text=(
                    "Tool cagr?s? icin gecersiz "
                    "parametreler."
                ),
                model="mock-model",
                provider="mock",
                finish_reason="stop",
                tool_calls=[
                    {
                        "id": "call_invalid",
                        "type": "function",
                        "function": {
                            "name": "get_weather",
                            "arguments": (
                                '{"city":"Baku"'
                            ),
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
        "Tool cagr?s? icin gecersiz "
        "parametreler."
    )


def test_core_engine_passes_registered_tools_to_provider() -> None:
    registry = ProviderRegistry()
    memory_manager = MemoryManager(
        InMemoryStore()
    )
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
        async def generate(
            self,
            request,
            context,
            **kwargs,
        ):
            captured["tools"] = kwargs.get(
                "tools"
            )

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
                "description": (
                    "Get weather information."
                ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                        "city": {
                            "type": "string",
                            },
                        },
                        "additionalProperties": False,
                        "required": ["city"],
                },
            },
        }
    ]

def test_core_engine_preserves_multiple_tool_call_chain() -> None:
    registry = ProviderRegistry()
    memory_manager = MemoryManager(
        InMemoryStore()
    )
    tool_executor = ToolExecutor()

    tool_executor.register(
        ToolDefinition(
            name="get_weather",
            description="Get weather information.",
        ),
        lambda city: f"{city}: sunny",
    )

    tool_executor.register(
        ToolDefinition(
            name="get_time",
            description="Get current time.",
        ),
        lambda city: f"{city}: 12:00",
    )

    calls = []

    class MultiToolProvider(MockProvider):
        async def generate(
            self,
            request,
            context,
            **kwargs,
        ):
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
                                "arguments": (
                                    '{"city":"Baku"}'
                                ),
                            },
                        },
                        {
                            "id": "call_time",
                            "type": "function",
                            "function": {
                                "name": "get_time",
                                "arguments": (
                                    '{"city":"Baku"}'
                                ),
                            },
                        },
                    ],
                    usage={},
                    metadata={},
                )

            messages = context.values[
                "messages"
            ]

            assert len(messages) == 4

            user_message = messages[0]
            assistant_message = messages[1]
            first_tool_message = messages[2]
            second_tool_message = messages[3]

            assert user_message["role"] == "user"
            assert user_message["content"] == request.text

            assert (
                assistant_message["role"]
                == "assistant"
            )

            assert len(
                assistant_message["tool_calls"]
            ) == 2

            assert (
                assistant_message["tool_calls"][0][
                    "id"
                ]
                == "call_weather"
            )

            assert (
                assistant_message["tool_calls"][1][
                    "id"
                ]
                == "call_time"
            )

            assert (
                first_tool_message["role"]
                == "tool"
            )

            assert (
                first_tool_message["tool_call_id"]
                == "call_weather"
            )

            assert (
                first_tool_message["content"]
                == "Baku: sunny"
            )

            assert (
                second_tool_message["role"]
                == "tool"
            )

            assert (
                second_tool_message["tool_call_id"]
                == "call_time"
            )

            assert (
                second_tool_message["content"]
                == "Baku: 12:00"
            )

            return SimpleNamespace(
                text="Baku hava durumu ve saat bilgisi hazir.",
                model="mock-model",
                provider="mock",
                finish_reason="stop",
                tool_calls=[],
                usage={},
                metadata={},
            )

    registry.register(
        MultiToolProvider(),
        make_default=True,
    )

    engine = CoreEngine(
        registry,
        memory_manager,
        tool_executor=tool_executor,
    )

    response = asyncio.run(
        engine.handle(
            Request("Baku hava ve saat bilgisi")
        )
    )

    assert response.text == (
        "Baku hava durumu ve saat bilgisi hazir."
    )
    assert len(calls) == 2
    assert response.metadata["tool_calls"] == 2
    assert response.metadata["tool_iterations"] == 1
    assert response.metadata["model_iterations"] == 2

def test_core_engine_does_not_execute_tool_when_arguments_are_not_a_dict() -> None:
    registry = ProviderRegistry()
    memory_manager = MemoryManager(InMemoryStore())
    tool_executor = ToolExecutor()

    called = {"value": False}

    def tool(city: str) -> str:
        called["value"] = True
        return city

    tool_executor.register(
        ToolDefinition(
            name="test_tool",
            description="Test tool.",
        ),
        tool,
    )

    class InvalidArgumentsProvider(MockProvider):
        async def generate(self, request, context, **kwargs):
            return SimpleNamespace(
                text="Gecersiz parametre.",
                model="mock-model",
                provider="mock",
                finish_reason="stop",
                tool_calls=[
                    {
                        "id": "call_invalid_type",
                        "type": "function",
                        "function": {
                            "name": "test_tool",
                            "arguments": '"not a dict"',
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
        engine.handle(Request("Test"))
    )

    assert called["value"] is False
    assert response.text == "Gecersiz parametre."


def test_core_engine_does_not_execute_unknown_tool() -> None:
    registry = ProviderRegistry()
    memory_manager = MemoryManager(InMemoryStore())
    tool_executor = ToolExecutor()

    class UnknownToolProvider(MockProvider):
        async def generate(self, request, context, **kwargs):
            return SimpleNamespace(
                text="Bilinmeyen tool.",
                model="mock-model",
                provider="mock",
                finish_reason="tool_calls",
                tool_calls=[
                    {
                        "id": "call_unknown",
                        "type": "function",
                        "function": {
                            "name": "does_not_exist",
                            "arguments": "{}",
                        },
                    }
                ],
                usage={},
                metadata={},
            )

    registry.register(
        UnknownToolProvider(),
        make_default=True,
    )

    engine = CoreEngine(
        registry,
        memory_manager,
        tool_executor=tool_executor,
    )

    response = asyncio.run(
        engine.handle(Request("Test"))
    )

    assert response.text == "Bilinmeyen tool."


def test_core_engine_does_not_execute_duplicate_tool_call_id() -> None:
    registry = ProviderRegistry()
    memory_manager = MemoryManager(InMemoryStore())
    tool_executor = ToolExecutor()

    call_count = {"value": 0}

    def test_tool() -> str:
        call_count["value"] += 1
        return "ok"

    tool_executor.register(
        ToolDefinition(
            name="test_tool",
            description="Test tool.",
        ),
        test_tool,
    )

    calls = []

    class DuplicateCallProvider(MockProvider):
        async def generate(self, request, context, **kwargs):
            calls.append(
                list(context.values.get("messages", []))
            )

            if len(calls) == 1:
                return SimpleNamespace(
                    text="",
                    model="mock-model",
                    provider="mock",
                    finish_reason="tool_calls",
                    tool_calls=[
                        {
                            "id": "duplicate_id",
                            "type": "function",
                            "function": {
                                "name": "test_tool",
                                "arguments": "{}",
                            },
                        }
                    ],
                    usage={},
                    metadata={},
                )

            return SimpleNamespace(
                text="Tamam.",
                model="mock-model",
                provider="mock",
                finish_reason="stop",
                tool_calls=[
                    {
                        "id": "duplicate_id",
                        "type": "function",
                        "function": {
                            "name": "test_tool",
                            "arguments": "{}",
                        },
                    }
                ],
                usage={},
                metadata={},
            )

    registry.register(
        DuplicateCallProvider(),
        make_default=True,
    )

    engine = CoreEngine(
        registry,
        memory_manager,
        tool_executor=tool_executor,
    )

    response = asyncio.run(
        engine.handle(Request("Test"))
    )

    assert call_count["value"] == 1
    assert response.text == "Tamam."


def test_core_engine_stops_after_max_tool_iterations() -> None:
    registry = ProviderRegistry()
    memory_manager = MemoryManager(InMemoryStore())
    tool_executor = ToolExecutor()

    call_count = {"value": 0}

    def looping_tool() -> str:
        call_count["value"] += 1
        return "loop"

    tool_executor.register(
        ToolDefinition(
            name="loop_tool",
            description="Looping test tool.",
        ),
        looping_tool,
    )

    class LoopProvider(MockProvider):
        async def generate(self, request, context, **kwargs):
            return SimpleNamespace(
                text="",
                model="mock-model",
                provider="mock",
                finish_reason="tool_calls",
                tool_calls=[
                    {
                        "id": f"call_{call_count['value']}",
                        "type": "function",
                        "function": {
                            "name": "loop_tool",
                            "arguments": "{}",
                        },
                    }
                ],
                usage={},
                metadata={},
            )

    registry.register(
        LoopProvider(),
        make_default=True,
    )

    engine = CoreEngine(
        registry,
        memory_manager,
        tool_executor=tool_executor,
    )

    response = asyncio.run(
        engine.handle(Request("Loop"))
    )

    assert call_count["value"] == 5
    assert response.metadata["tool_calls"] == 5
    assert response.metadata["tool_iterations"] == 5


def test_core_engine_skips_tool_call_when_function_name_is_missing() -> None:
    registry = ProviderRegistry()
    memory_manager = MemoryManager(InMemoryStore())
    tool_executor = ToolExecutor()

    called = {"value": False}

    def test_tool() -> str:
        called["value"] = True
        return "executed"

    tool_executor.register(
        ToolDefinition(
            name="test_tool",
            description="Test tool.",
        ),
        test_tool,
    )

    class MissingNameProvider(MockProvider):
        async def generate(self, request, context, **kwargs):
            return SimpleNamespace(
                text="Tool adi eksik.",
                model="mock-model",
                provider="mock",
                finish_reason="tool_calls",
                tool_calls=[
                    {
                        "id": "call_missing_name",
                        "type": "function",
                        "function": {
                            "arguments": "{}",
                        },
                    }
                ],
                usage={},
                metadata={},
            )

    registry.register(
        MissingNameProvider(),
        make_default=True,
    )

    engine = CoreEngine(
        registry,
        memory_manager,
        tool_executor=tool_executor,
    )

    response = asyncio.run(
        engine.handle(Request("Test"))
    )

    assert called["value"] is False
    assert response.text == "Tool adi eksik."


def test_core_engine_skips_tool_call_when_arguments_are_none() -> None:
    registry = ProviderRegistry()
    memory_manager = MemoryManager(InMemoryStore())
    tool_executor = ToolExecutor()

    called = {"value": False}

    def test_tool() -> str:
        called["value"] = True
        return "executed"

    tool_executor.register(
        ToolDefinition(
            name="test_tool",
            description="Test tool.",
        ),
        test_tool,
    )

    class NoneArgumentsProvider(MockProvider):
        async def generate(self, request, context, **kwargs):
            return SimpleNamespace(
                text="Gecersiz arguman.",
                model="mock-model",
                provider="mock",
                finish_reason="stop",
                tool_calls=[
                    {
                        "id": "call_none_arguments",
                        "type": "function",
                        "function": {
                            "name": "test_tool",
                            "arguments": None,
                        },
                    }
                ],
                usage={},
                metadata={},
            )

    registry.register(
        NoneArgumentsProvider(),
        make_default=True,
    )

    engine = CoreEngine(
        registry,
        memory_manager,
        tool_executor=tool_executor,
    )

    response = asyncio.run(
        engine.handle(Request("Test"))
    )

    assert called["value"] is False
    assert response.text == "Gecersiz arguman."


def test_core_engine_executes_duplicate_tool_call_id_only_once_in_same_response() -> None:
    registry = ProviderRegistry()
    memory_manager = MemoryManager(InMemoryStore())
    tool_executor = ToolExecutor()

    call_count = {"value": 0}

    def test_tool() -> str:
        call_count["value"] += 1
        return "ok"

    tool_executor.register(
        ToolDefinition(
            name="test_tool",
            description="Test tool.",
        ),
        test_tool,
    )

    class DuplicateSameResponseProvider(MockProvider):
        async def generate(self, request, context, **kwargs):
            return SimpleNamespace(
                text="Tamam.",
                model="mock-model",
                provider="mock",
                finish_reason="stop",
                tool_calls=[
                    {
                        "id": "same_id",
                        "type": "function",
                        "function": {
                            "name": "test_tool",
                            "arguments": "{}",
                        },
                    },
                    {
                        "id": "same_id",
                        "type": "function",
                        "function": {
                            "name": "test_tool",
                            "arguments": "{}",
                        },
                    },
                ],
                usage={},
                metadata={},
            )

    registry.register(
        DuplicateSameResponseProvider(),
        make_default=True,
    )

    engine = CoreEngine(
        registry,
        memory_manager,
        tool_executor=tool_executor,
    )

    response = asyncio.run(
        engine.handle(Request("Test"))
    )

    assert call_count["value"] == 1
    assert response.metadata["tool_calls"] == 1


def test_core_engine_rejects_tool_call_without_id() -> None:
    registry = ProviderRegistry()
    memory_manager = MemoryManager(InMemoryStore())
    tool_executor = ToolExecutor()

    called = {"value": False}

    def test_tool() -> str:
        called["value"] = True
        return "ok"

    tool_executor.register(
        ToolDefinition(
            name="test_tool",
            description="Test tool.",
        ),
        test_tool,
    )

    class MissingIdProvider(MockProvider):
        def __init__(self) -> None:
            self.calls = 0

        async def generate(self, request, context, **kwargs):
            self.calls += 1

            if self.calls > 1:
                return SimpleNamespace(
                    text="Kimliksiz araç çağrısı reddedildi.",
                    model="mock-model",
                    provider="mock",
                    finish_reason="stop",
                    tool_calls=[],
                    usage={},
                    metadata={},
                )

            return SimpleNamespace(
                text="",
                model="mock-model",
                provider="mock",
                finish_reason="stop",
                tool_calls=[
                    {
                        "type": "function",
                        "function": {
                            "name": "test_tool",
                            "arguments": "{}",
                        },
                    }
                ],
                usage={},
                metadata={},
            )

    registry.register(
        MissingIdProvider(),
        make_default=True,
    )

    engine = CoreEngine(
        registry,
        memory_manager,
        tool_executor=tool_executor,
    )

    response = asyncio.run(
        engine.handle(Request("Test"))
    )

    assert called["value"] is False
    assert response.metadata["tool_calls"] == 0
    assert response.metadata["invalid_tool_calls"] == 1
    assert response.metadata["failed_tool_calls"] == 1
    assert response.metadata["completion_verified"] is False


def test_core_engine_skips_tool_call_with_empty_function() -> None:
    registry = ProviderRegistry()
    memory_manager = MemoryManager(InMemoryStore())
    tool_executor = ToolExecutor()

    class EmptyFunctionProvider(MockProvider):
        async def generate(self, request, context, **kwargs):
            return SimpleNamespace(
                text="Fonksiyon bilgisi eksik.",
                model="mock-model",
                provider="mock",
                finish_reason="stop",
                tool_calls=[
                    {
                        "id": "call_empty_function",
                        "type": "function",
                        "function": {},
                    }
                ],
                usage={},
                metadata={},
            )

    registry.register(
        EmptyFunctionProvider(),
        make_default=True,
    )

    engine = CoreEngine(
        registry,
        memory_manager,
        tool_executor=tool_executor,
    )

    response = asyncio.run(
        engine.handle(Request("Test"))
    )

    assert response.text == "Fonksiyon bilgisi eksik."
    assert response.metadata["tool_calls"] == 0
    assert response.metadata["invalid_tool_calls"] == 1


def test_core_engine_uses_task_manager() -> None:
    registry = ProviderRegistry()
    registry.register(
        MockProvider(),
        make_default=True,
    )

    memory_manager = MemoryManager(
        InMemoryStore()
    )

    from app.tasks.manager import TaskManager

    task_manager = TaskManager()

    engine = CoreEngine(
        registry,
        memory_manager,
        task_manager=task_manager,
    )

    assert engine.task_manager is task_manager

def test_create_application_wires_task_manager() -> None:
    from app.config.settings import Settings
    from app.tasks.manager import TaskManager
    from app.main import create_application

    settings = Settings.from_environment()

    application = create_application(settings)

    assert application.engine.task_manager is not None
    assert isinstance(
        application.engine.task_manager,
        TaskManager,
    )


def test_create_application_shares_task_manager_with_engine() -> None:
    from app.main import create_application

    application = create_application()

    assert application.task_manager is application.engine.task_manager

def test_core_engine_exposes_functional_task_manager() -> None:
    from app.main import create_application

    application = create_application()

    task_manager = application.engine.task_manager

    assert task_manager is application.task_manager

    task = task_manager.create(
        goal="JARVIS test gorevi",
    )

    assert task is not None
    assert task_manager.get(task.task_id) == task








def create_test_engine() -> CoreEngine:
    return create_engine()

def test_core_engine_creates_plan():
    from app.planning.models import PlanStatus, PlanStep

    engine = create_test_engine()

    plan = engine.create_plan(
        "Dosyay? kontrol et",
        [
            PlanStep(
                "Kontrol et",
                metadata={
                    "tool_name": "test_tool",
                    "parameters": {},
                },
            )
        ],
    )

    assert plan.goal == "Dosyay? kontrol et"
    assert len(plan.steps) == 1
    assert plan.status is PlanStatus.READY


@pytest.mark.asyncio
async def test_core_engine_executes_plan_successfully():
    from app.planning.models import PlanStatus, PlanStep
    from app.core.models import ToolResult, ToolExecutionStatus

    engine = create_test_engine()

    async def fake_execute(
        tool_name,
        *,
        parameters=None,
    ):
            return ToolResult(
                status=ToolExecutionStatus.SUCCESS,
                tool_name=tool_name,
                data={"ok": True},
                verified=True,
            )

    engine._tool_executor.execute = fake_execute

    plan = engine.create_plan(
        "Test plan?",
        [
            PlanStep(
                "?lk ad?m",
                metadata={
                    "tool_name": "test_tool",
                    "parameters": {},
                },
            )
        ],
    )

    result = await engine.execute_plan(plan)

    assert result.status is PlanStatus.COMPLETED
    assert result.progress == 1.0
    assert result.steps[0].metadata["tool_result"] == {"ok": True}


@pytest.mark.asyncio
async def test_core_engine_fails_plan_when_tool_fails():
    from app.planning.models import PlanStatus, PlanStep
    from app.core.models import ToolResult, ToolExecutionStatus

    engine = create_test_engine()

    async def fake_execute(
        tool_name,
        *,
        parameters=None,
    ):
        return ToolResult(
            status=ToolExecutionStatus.FAILED,
            tool_name=tool_name,
            message="tool failed",
            error="execution error",
        )

    engine._tool_executor.execute = fake_execute

    plan = engine.create_plan(
        "Ba?ar?s?z plan",
        [
            PlanStep(
                "Hatal? ad?m",
                metadata={
                    "tool_name": "test_tool",
                    "parameters": {},
                },
            )
        ],
    )

    result = await engine.execute_plan(plan)

    assert result.status is PlanStatus.FAILED
    assert result.steps[0].status.value == "failed"
    assert result.steps[0].metadata["tool_error"] == "execution error"




@pytest.mark.asyncio
async def test_core_engine_executes_tracked_task():
    from app.core.models import ToolDefinition
    from app.planning.models import PlanStep
    from app.main import create_application

    application = create_application()
    engine = application.engine

    application.tool_executor.register(
        ToolDefinition(
            name="tracked_test",
            description="Tracked task test tool.",
        ),
        lambda: "tracked-ok",
    )

    plan = engine.create_plan(
        "Tracked execution",
        [
            PlanStep(
                "run",
                metadata={
                    "tool_name": "tracked_test",
                    "parameters": {},
                },
            ),
        ],
    )

    task = await engine.execute_task(
        "Tracked execution",
        plan,
    )

    assert task.status.value == "completed"
    assert task.progress == 1.0
    assert len(task.steps) == 1
    assert task.steps[0].status.value == "completed"
    assert task.steps[0].result == "tracked-ok"


@pytest.mark.asyncio
async def test_core_engine_tracked_task_failure_is_propagated():
    from app.core.models import ToolDefinition
    from app.planning.models import PlanStep
    from app.main import create_application

    application = create_application()
    engine = application.engine

    def failing():
        raise RuntimeError("tracked failure")

    application.tool_executor.register(
        ToolDefinition(
            name="tracked_failure",
            description="Tracked failure test tool.",
        ),
        failing,
    )

    plan = engine.create_plan(
        "Tracked failure",
        [
            PlanStep(
                "fail",
                metadata={
                    "tool_name": "tracked_failure",
                    "parameters": {},
                },
            ),
        ],
    )

    task = await engine.execute_task(
        "Tracked failure",
        plan,
    )

    assert task.status.value == "failed"
    assert task.error
    assert task.steps[0].status.value == "failed"


def test_core_engine_rejects_mismatched_task_and_plan():
    engine = create_engine()

    from app.planning.models import PlanStep

    plan = engine.create_plan(
        "Plan goal",
        [
            PlanStep("step"),
        ],
    )

    with pytest.raises(ValueError, match="goal"):
        asyncio.run(
            engine.execute_task(
                "Different task goal",
                plan,
            )
        )
