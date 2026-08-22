from __future__ import annotations

import asyncio
from types import SimpleNamespace

from app.core.engine import CoreEngine
from app.core.models import (
    Request,
    ToolDefinition,
    ToolExecutionStatus,
    ToolResult,
)
from app.providers.base import ModelResponse
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



def test_core_routes_parameterized_memory_search(
) -> None:
    registry = ProviderRegistry()
    provider = OllamaLikeProvider()

    registry.register(
        provider,
        make_default=True,
    )

    tool_executor = ToolExecutor()
    captured = {}

    def search_memories(
        query: str,
        limit: int = 10,
    ):
        captured["query"] = query
        captured["limit"] = limit

        return [
            {
                "content": query,
            }
        ]

    tool_executor.register(
        ToolDefinition(
            name="search_memories",
            description="Search memory records.",
        ),
        search_memories,
    )

    engine = CoreEngine(
        registry,
        MemoryManager(
            InMemoryStore()
        ),
        tool_executor=tool_executor,
    )

    response = asyncio.run(
        engine.handle(
            Request(
                "Haf\u0131zanda ara: "
                "\u00e7ocuk n\u00f6rolojisi"
            )
        )
    )

    assert captured == {
        "query": (
            "\u00e7ocuk n\u00f6rolojisi"
        ),
        "limit": 10,
    }

    assert len(provider.calls) == 1

    assert (
        response.metadata[
            "deterministic_tool_route"
        ]
        == "search_memories"
    )

    assert response.metadata[
        "tool_calls"
    ] == 1

    assert response.metadata[
        "invalid_tool_calls"
    ] == 0


def test_core_routes_get_task_with_exact_uuid(
) -> None:
    registry = ProviderRegistry()
    provider = OllamaLikeProvider()

    registry.register(
        provider,
        make_default=True,
    )

    tool_executor = ToolExecutor()
    captured = {}

    def get_task(
        task_id: str,
    ):
        captured["task_id"] = task_id

        return {
            "task_id": task_id,
            "status": "completed",
        }

    tool_executor.register(
        ToolDefinition(
            name="get_task",
            description="Inspect one task.",
        ),
        get_task,
    )

    engine = CoreEngine(
        registry,
        MemoryManager(
            InMemoryStore()
        ),
        tool_executor=tool_executor,
    )

    task_id = (
        "12345678-1234-"
        "5678-1234-"
        "567812345678"
    )

    response = asyncio.run(
        engine.handle(
            Request(
                (
                    "G\u00f6rev "
                    f"{task_id} "
                    "detaylar\u0131n\u0131 "
                    "g\u00f6ster."
                )
            )
        )
    )

    assert captured[
        "task_id"
    ] == task_id

    assert len(provider.calls) == 1

    assert (
        response.metadata[
            "deterministic_tool_route"
        ]
        == "get_task"
    )

    assert response.metadata[
        "tool_calls"
    ] == 1

    assert response.metadata[
        "invalid_tool_calls"
    ] == 0



class FastFinalizingOllamaProvider(
    OllamaLikeProvider
):
    def __init__(self) -> None:
        super().__init__()
        self.finalization_calls = 0

    def try_deterministic_finalization(
        self,
        request,
        context,
        *,
        model=None,
    ):
        self.finalization_calls += 1

        return ModelResponse(
            text="fast deterministic response",
            model="test-ollama",
            provider="ollama",
            finish_reason="stop",
            metadata={
                "deterministic_finalization": (
                    "get_windows_system_info"
                ),
                "generation_skipped": True,
            },
        )


def test_core_skips_model_after_verified_fast_finalization(
) -> None:
    called = {"count": 0}

    provider = (
        FastFinalizingOllamaProvider()
    )

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

    # generate() must never be called.
    assert provider.calls == []

    assert (
        provider.finalization_calls
        == 1
    )

    assert (
        response.text
        == "fast deterministic response"
    )

    assert response.metadata[
        "model_iterations"
    ] == 0

    assert response.metadata[
        "model_tokens"
    ] == 0

    assert response.metadata[
        "tool_calls"
    ] == 1

    assert response.metadata[
        "verified_tool_calls"
    ] == 1

    assert (
        response.metadata[
            "completion_verified"
        ]
        is True
    )

    assert (
        response.metadata[
            "provider_metadata"
        ]["generation_skipped"]
        is True
    )


def test_core_never_fast_finalizes_failed_tool_result(
) -> None:
    registry = ProviderRegistry()

    provider = (
        FastFinalizingOllamaProvider()
    )

    registry.register(
        provider,
        make_default=True,
    )

    executor = ToolExecutor()

    def failing_system_info():
        return ToolResult(
            status=ToolExecutionStatus.FAILED,
            tool_name=(
                "get_windows_system_info"
            ),
            message=(
                "System information failed."
            ),
            error="test failure",
            verified=False,
        )

    executor.register(
        ToolDefinition(
            name="get_windows_system_info",
            description=(
                "Read verified local Windows "
                "system information."
            ),
        ),
        failing_system_info,
    )

    engine = CoreEngine(
        registry,
        MemoryManager(
            InMemoryStore()
        ),
        tool_executor=executor,
    )

    response = asyncio.run(
        engine.handle(
            Request(
                "Bu bilgisayar\u0131n "
                "sistem bilgilerini g\u00f6ster."
            )
        )
    )

    # Fast finalization is forbidden because
    # the tool result failed verification.
    assert (
        provider.finalization_calls
        == 0
    )

    # Normal provider path remains available.
    assert len(provider.calls) == 1

    assert response.metadata[
        "model_iterations"
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
