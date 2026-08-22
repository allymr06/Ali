from __future__ import annotations

import asyncio
from types import SimpleNamespace

from app.core.engine import CoreEngine
from app.core.models import (
    Request,
    ToolDefinition,
)
from app.memory.in_memory import InMemoryStore
from app.memory.manager import MemoryManager
from app.providers.mock import MockProvider
from app.providers.registry import ProviderRegistry
from app.tools.executor import ToolExecutor


class OllamaLikeProvider(MockProvider):
    def __init__(self) -> None:
        self.calls = []

    @property
    def name(self) -> str:
        return "ollama"

    async def generate(
        self,
        request,
        context,
        **kwargs,
    ):
        self.calls.append(
            {
                "messages": list(
                    context.values.get(
                        "messages",
                        [],
                    )
                ),
                "tools": kwargs.get("tools"),
            }
        )

        return SimpleNamespace(
            text="final response",
            model="test-ollama",
            provider="ollama",
            finish_reason="stop",
            tool_calls=[],
            usage={},
            metadata={},
        )


def create_engine(
    provider,
    *,
    called,
) -> CoreEngine:
    registry = ProviderRegistry()

    registry.register(
        provider,
        make_default=True,
    )

    tool_executor = ToolExecutor()

    def get_windows_system_info():
        called["count"] += 1

        return {
            "release": "11",
            "version": "10.0.26200",
            "logical_cpu_count": 8,
            "memory_total_gib": 7.84,
            "memory_available_gib": 1.5,
            "disk_total_gib": 118.01,
            "disk_free_gib": 10.0,
        }

    tool_executor.register(
        ToolDefinition(
            name="get_windows_system_info",
            description=(
                "Read verified local Windows "
                "system information."
            ),
        ),
        get_windows_system_info,
    )

    return CoreEngine(
        registry,
        MemoryManager(
            InMemoryStore()
        ),
        tool_executor=tool_executor,
    )


def test_core_routes_system_info_before_first_model_call(
) -> None:
    called = {"count": 0}
    provider = OllamaLikeProvider()

    engine = create_engine(
        provider,
        called=called,
    )

    response = asyncio.run(
        engine.handle(
            Request(
                "Bu bilgisayar\u0131n "
                "sistem bilgilerini g\u00f6ster."
            )
        )
    )

    assert called["count"] == 1

    assert len(provider.calls) == 1

    messages = provider.calls[0][
        "messages"
    ]

    assert any(
        message.get("role") == "tool"
        and "10.0.26200"
        in str(
            message.get("content")
        )
        for message in messages
    )

    assert response.text == "final response"

    assert (
        response.metadata[
            "deterministic_tool_route"
        ]
        == "get_windows_system_info"
    )

    assert response.metadata[
        "model_iterations"
    ] == 1

    assert response.metadata[
        "tool_calls"
    ] == 1

    assert response.metadata[
        "tool_iterations"
    ] == 1


def test_core_removes_already_routed_tool_from_model_tools(
) -> None:
    called = {"count": 0}
    provider = OllamaLikeProvider()

    engine = create_engine(
        provider,
        called=called,
    )

    asyncio.run(
        engine.handle(
            Request(
                "Bilgisayar\u0131m\u0131n "
                "\u00f6zelliklerini g\u00f6ster."
            )
        )
    )

    assert called["count"] == 1
    assert len(provider.calls) == 1

    tools = provider.calls[0]["tools"]

    assert not tools


def test_core_can_disable_deterministic_routing(
) -> None:
    called = {"count": 0}
    provider = OllamaLikeProvider()

    engine = create_engine(
        provider,
        called=called,
    )

    response = asyncio.run(
        engine.handle(
            Request(
                "Bu bilgisayar\u0131n "
                "sistem bilgilerini g\u00f6ster.",
                metadata={
                    "deterministic_tool_routing": False,
                },
            )
        )
    )

    assert called["count"] == 0
    assert len(provider.calls) == 1

    assert (
        response.metadata[
            "deterministic_tool_route"
        ]
        is None
    )

    assert response.metadata[
        "tool_calls"
    ] == 0


def test_core_respects_allowed_tools_filter_for_route(
) -> None:
    called = {"count": 0}
    provider = OllamaLikeProvider()

    engine = create_engine(
        provider,
        called=called,
    )

    response = asyncio.run(
        engine.handle(
            Request(
                "Bu bilgisayar\u0131n "
                "sistem bilgilerini g\u00f6ster.",
                metadata={
                    "allowed_tools": [
                        "some_other_tool"
                    ],
                },
            )
        )
    )

    assert called["count"] == 0

    assert (
        response.metadata[
            "deterministic_tool_route"
        ]
        is None
    )

    assert response.metadata[
        "tool_calls"
    ] == 0



def test_core_does_not_reexecute_deterministically_routed_tool(
) -> None:
    called = {"count": 0}

    class RepeatingToolProvider(MockProvider):
        def __init__(self) -> None:
            self.calls = []

        @property
        def name(self) -> str:
            return "ollama"

        async def generate(
            self,
            request,
            context,
            **kwargs,
        ):
            self.calls.append(
                {
                    "messages": list(
                        context.values.get(
                            "messages",
                            [],
                        )
                    ),
                    "tools": kwargs.get("tools"),
                }
            )

            if len(self.calls) == 1:
                return SimpleNamespace(
                    text="",
                    model="test-ollama",
                    provider="ollama",
                    finish_reason="tool_calls",
                    tool_calls=[
                        {
                            "id": "repeat-system-info",
                            "type": "function",
                            "function": {
                                "name": (
                                    "get_windows_system_info"
                                ),
                                "arguments": "{}",
                            },
                        }
                    ],
                    usage={},
                    metadata={},
                )

            return SimpleNamespace(
                text="final response",
                model="test-ollama",
                provider="ollama",
                finish_reason="stop",
                tool_calls=[],
                usage={},
                metadata={},
            )

    provider = RepeatingToolProvider()

    engine = create_engine(
        provider,
        called=called,
    )

    response = asyncio.run(
        engine.handle(
            Request(
                "Bu bilgisayar\u0131n "
                "sistem bilgilerini g\u00f6ster."
            )
        )
    )

    assert called["count"] == 1
    assert len(provider.calls) == 2

    assert response.metadata[
        "tool_calls"
    ] == 1

    assert response.metadata[
        "tool_call_attempts"
    ] == 2

    assert response.metadata[
        "invalid_tool_calls"
    ] == 1

    assert response.metadata[
        "failed_tool_calls"
    ] == 1

    assert (
        response.metadata[
            "completion_verified"
        ]
        is False
    )

    assert response.text == "final response"
