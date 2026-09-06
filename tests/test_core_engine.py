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


def test_streaming_route_accepts_tool_calls_and_streams_the_final_answer() -> None:
    import json

    from app.providers.base import ModelCapabilities, ModelStreamChunk

    registry = ProviderRegistry()
    memory_manager = MemoryManager(InMemoryStore())
    tool_executor = ToolExecutor()
    tool_executor.register(
        ToolDefinition(name="get_weather", description="Get weather information."),
        lambda city: f"{city}: sunny",
    )
    seen: list[dict] = []

    class StreamingProvider(MockProvider):
        @property
        def capabilities(self):
            return ModelCapabilities(
                text=True, streaming=True, structured_output=True, tool_calling=True, vision=False
            )

        async def stream(self, request, context, *, model=None, system_prompt=None, tools=None):
            seen.append(
                {
                    "tools": tools,
                    "messages": json.loads(json.dumps(context.values.get("messages", []))),
                    "task_type": request.metadata.get("task_type"),
                    "reasoning": request.metadata.get("reasoning_task_type"),
                }
            )
            if len(seen) == 1:
                yield ModelStreamChunk(
                    text="", model="mock-model", provider="mock",
                    tool_calls=[{
                        "index": 0, "id": "call_1", "type": "function",
                        "function": {"name": "get_weather", "arguments": '{"city":'},
                        "extra_content": {"google": {"thought_signature": "SIG"}},
                    }],
                )
                yield ModelStreamChunk(
                    text="", model="mock-model", provider="mock", finish_reason="tool_calls",
                    tool_calls=[{"index": 0, "function": {"arguments": '"Baku"}'}}],
                )
            else:
                yield ModelStreamChunk(text="Sunny ", model="mock-model", provider="mock")
                yield ModelStreamChunk(
                    text="in Baku.", model="mock-model", provider="mock", finish_reason="stop"
                )

    registry.register(StreamingProvider(), make_default=True)
    engine = CoreEngine(registry, memory_manager, tool_executor=tool_executor)
    streamed: list[str] = []

    response = asyncio.run(
        engine.handle(Request("Baku'de hava nasil?"), stream_callback=streamed.append)
    )

    assert response.text == "Sunny in Baku."
    assert streamed == ["Sunny ", "Sunny in Baku."]
    assert response.metadata["tool_calls"] == 1
    assert response.metadata["model_iterations"] == 2
    assert response.metadata["provider_metadata"]["streamed"] is True
    assert seen[0]["tools"] and seen[0]["task_type"] == "agentic"
    # The finalization call runs with the lighter task type.
    assert seen[1]["task_type"] == "simple" and seen[1]["reasoning"] == "simple"
    replayed = next(
        m for m in seen[1]["messages"] if m["role"] == "assistant" and m.get("tool_calls")
    )
    assert replayed["tool_calls"] == [{
        "id": "call_1", "type": "function",
        "function": {"name": "get_weather", "arguments": '{"city":"Baku"}'},
        "extra_content": {"google": {"thought_signature": "SIG"}},
    }]


def test_streaming_is_skipped_for_providers_that_cannot_stream() -> None:
    registry = ProviderRegistry()
    registry.register(MockProvider(), make_default=True)
    engine = CoreEngine(registry, MemoryManager(InMemoryStore()), tool_executor=ToolExecutor())
    streamed: list[str] = []

    response = asyncio.run(engine.handle(Request("merhaba dunya"), stream_callback=streamed.append))

    assert response.text.startswith("Mock yan")
    assert streamed == []


def test_action_model_rate_limit_falls_back_at_once_and_starts_a_cooldown() -> None:
    from app.core import engine as engine_module
    from app.diagnostics.service import DiagnosticsService
    from app.providers.base import ModelResponse, ProviderRateLimitError

    registry = ProviderRegistry()
    tool_executor = ToolExecutor()
    tool_executor.register(
        ToolDefinition(name="get_weather", description="Get weather information."),
        lambda city: f"{city}: sunny",
    )
    calls: list[dict] = []

    class QuotaProvider(MockProvider):
        @property
        def name(self):
            return "gemini"

        async def generate(self, request, context, *, model=None, **kwargs):
            calls.append({"model": model, "single": request.metadata.get("gateway_single_attempt")})
            if model == "action-model":
                raise ProviderRateLimitError("quota", provider="gemini", retry_after_seconds=90)
            return ModelResponse(
                text="Sunny.", model=model or "lite-model", provider="gemini", finish_reason="stop"
            )

    registry.register(QuotaProvider(), make_default=True)
    diagnostics = DiagnosticsService()
    engine = CoreEngine(
        registry, MemoryManager(InMemoryStore()), tool_executor=tool_executor,
        action_model="action-model", diagnostics=diagnostics,
    )

    first = asyncio.run(engine.handle(Request("Baku'de hava nasil?")))
    assert first.text == "Sunny."
    assert [c["model"] for c in calls] == ["action-model", None]
    assert calls[0]["single"] is True and calls[1]["single"] is None
    assert engine._action_model_cooldown_until > engine_module.time.monotonic() + 60
    assert engine._action_model_cooldown_seconds == 2 * engine_module.ACTION_MODEL_COOLDOWN_SECONDS

    calls.clear()
    second = asyncio.run(engine.handle(Request("Baku'de hava nasil?")))
    assert second.text == "Sunny."
    # The cooling-down action model is not even attempted.
    assert [c["model"] for c in calls] == [None]
    names = [e.name for e in diagnostics.ledger.list(component="core", limit=50)]
    assert names.count("request.model_fallback") == 2
    fallbacks = [e for e in diagnostics.ledger.list(component="core", limit=50) if e.name == "request.model_fallback"]
    assert {e.attributes["reason"] for e in fallbacks} == {"rate_limited", "cooldown"}
    model_calls = [e for e in diagnostics.ledger.list(component="core", limit=50) if e.name == "request.model_call"]
    assert model_calls and all("latency_seconds" in e.attributes for e in model_calls)
    assert "core.model.latency" in diagnostics.metrics.snapshot()["timers"]

    # Once the pause has passed the action model is offered again.
    engine._action_model_cooldown_until = 0.0
    calls.clear()
    asyncio.run(engine.handle(Request("Baku'de hava nasil?")))
    assert calls[0]["model"] == "action-model"


def test_clock_directive_is_turkish_and_local() -> None:
    import re
    from datetime import datetime, timezone

    from app.core.engine import clock_directive

    moment = datetime(2026, 9, 5, 14, 30, tzinfo=timezone.utc).astimezone()
    line = clock_directive(moment)
    assert line.startswith("Şu an yerel tarih ve saat: 5 Eylül 2026 Cumartesi, ")
    assert f"{moment:%H:%M}" in line
    assert "Saat, tarih veya gün sorulursa" in line
    # The live line must name today's real weekday, whatever day it is. The
    # names are compared as whole words: "Pazar" is a substring of "Pazartesi",
    # and a plain containment count read two weekdays into every Monday.
    days = ("Pazartesi", "Salı", "Çarşamba", "Perşembe", "Cuma", "Cumartesi", "Pazar")
    today = clock_directive()
    words = set(re.findall(r"\w+", today))
    assert days[datetime.now().astimezone().weekday()] in words
    assert sum(day in words for day in days) == 1


def test_every_system_prompt_carries_the_clock() -> None:
    registry = ProviderRegistry()
    prompts: list[str | None] = []

    class RecordingProvider(MockProvider):
        async def generate(self, request, context, *, system_prompt=None, **kwargs):
            prompts.append(system_prompt)
            return await super().generate(request, context, **kwargs)

    registry.register(RecordingProvider(), make_default=True)
    engine = CoreEngine(registry, MemoryManager(InMemoryStore()), tool_executor=ToolExecutor())
    asyncio.run(engine.handle(Request("Baku'de hava nasil?")))
    asyncio.run(engine.handle(Request("merhaba")))
    assert len(prompts) == 2
    for prompt in prompts:
        assert prompt is not None and "Şu an yerel tarih ve saat:" in prompt


def test_clock_questions_are_answered_from_the_clock_without_a_model_call() -> None:
    from datetime import datetime, timezone

    from app.core.interaction_policy import InteractionPolicy, clock_answer

    moment = datetime(2026, 9, 5, 14, 30, tzinfo=timezone.utc).astimezone()
    spoken = clock_answer(moment)
    assert spoken.startswith(f"Şu an saat {moment:%H:%M}; bugün 5 Eylül 2026, Cumartesi.")

    policy = InteractionPolicy()
    for text in ("saat kaç?", "Saat kac", "bugün günlerden ne", "bugunun tarihi ne", "what time is it"):
        assert policy.is_clock_question(policy._normalize(text)), text
    for text in ("saat 9'da toplantım olduğunu hatırlat", "yarın saat kaçta çıkalım, uzun bir plan yapalım mı sence bugün", "tarihte bugün ne oldu"):
        assert not policy.is_clock_question(policy._normalize(text)), text

    registry = ProviderRegistry()
    calls: list[str] = []

    class RecordingProvider(MockProvider):
        async def generate(self, request, context, **kwargs):
            calls.append(request.text)
            return await super().generate(request, context, **kwargs)

    registry.register(RecordingProvider(), make_default=True)
    engine = CoreEngine(registry, MemoryManager(InMemoryStore()), tool_executor=ToolExecutor())
    response = asyncio.run(engine.handle(Request("saat kaç?")))
    assert response.text.startswith("Şu an saat ")
    assert response.metadata["interaction_kind"] == "clock"
    assert response.metadata["model"] == "jarvis-identity-composer"
    assert calls == []
