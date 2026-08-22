from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from types import MappingProxyType, SimpleNamespace

import pytest

from app.core.engine import CoreEngine
from app.core.models import (
    Context,
    Request,
    RiskLevel,
    ToolDefinition,
    ToolExecutionStatus,
    ToolResult,
)
from app.memory.in_memory import InMemoryStore
from app.memory.manager import MemoryManager
from app.providers.mock import MockProvider
from app.providers.registry import ProviderRegistry
from app.tools.executor import ToolExecutor
from tests.security_helpers import bound_approval


class MutatingProvider(MockProvider):
    def __init__(self) -> None:
        super().__init__()
        self.calls = 0

    async def generate(self, request, context, **kwargs):
        self.calls += 1
        if self.calls == 1:
            return SimpleNamespace(
                text="",
                model="mock-model",
                provider="mock",
                finish_reason="tool_calls",
                tool_calls=[
                    {
                        "id": "write-1",
                        "type": "function",
                        "function": {
                            "name": "mutate_test_resource",
                            "arguments": '{"path":"note.txt","content":"secret"}',
                        },
                    }
                ],
                usage={},
                metadata={},
            )
        return SimpleNamespace(
            text="İşlem tamamlandı.",
            model="mock-model",
            provider="mock",
            finish_reason="stop",
            tool_calls=[],
            usage={},
            metadata={},
        )


def approval_engine(calls: list[tuple[str, str]]) -> CoreEngine:
    provider = MutatingProvider()
    registry = ProviderRegistry()
    registry.register(provider, make_default=True)
    executor = ToolExecutor()

    def mutate_test_resource(path: str, content: str) -> ToolResult:
        calls.append((path, content))
        return ToolResult(
            status=ToolExecutionStatus.SUCCESS,
            tool_name="mutate_test_resource",
            data={"path": path},
            verified=True,
        )

    executor.register(
        ToolDefinition(
            name="mutate_test_resource",
            description="Mutate a test resource.",
            risk_level=RiskLevel.MEDIUM,
            requires_confirmation=True,
        ),
        mutate_test_resource,
    )
    return CoreEngine(
        registry,
        MemoryManager(InMemoryStore()),
        tool_executor=executor,
    )


@pytest.mark.asyncio
async def test_core_executes_exact_mutation_after_one_bound_approval() -> None:
    calls: list[tuple[str, str]] = []
    prompts = []

    async def approve(prompt) -> bool:
        prompts.append(prompt)
        assert isinstance(prompt.parameters, MappingProxyType)
        assert prompt.parameters["content"] == "<6 karakter>"
        return True

    response = await approval_engine(calls).handle(
        Request("Dosyaya yaz"),
        Context(),
        approval_callback=approve,
    )

    assert calls == [("note.txt", "secret")]
    assert len(prompts) == 1
    assert response.metadata["outcome"] == "completed"
    assert response.metadata["completion_verified"] is True


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["missing", "denied", "error"])
async def test_core_never_mutates_without_successful_ui_approval(mode: str) -> None:
    calls: list[tuple[str, str]] = []
    callback = None
    if mode == "denied":
        async def callback(_prompt):
            return False
    elif mode == "error":
        async def callback(_prompt):
            raise RuntimeError("modal unavailable")

    response = await approval_engine(calls).handle(
        Request("Dosyaya yaz"),
        Context(),
        approval_callback=callback,
    )

    assert calls == []
    assert response.metadata["outcome"] in {
        "approval_required",
        "approval_denied",
    }
    assert response.metadata["tool_iterations"] == 1


def test_approval_grant_is_consumed_atomically_once() -> None:
    executor = ToolExecutor()
    calls = 0

    def mutate() -> ToolResult:
        nonlocal calls
        calls += 1
        return ToolResult(
            status=ToolExecutionStatus.SUCCESS,
            tool_name="mutate",
            verified=True,
        )

    executor.register(
        ToolDefinition(
            name="mutate",
            description="Mutate.",
            risk_level=RiskLevel.HIGH,
        ),
        mutate,
    )
    approval = bound_approval("mutate")

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = tuple(
            pool.map(
                lambda _index: executor.execute("mutate", **approval),
                range(2),
            )
        )

    assert calls == 1
    assert sum(result.succeeded for result in results) == 1
    assert sum(result.status is ToolExecutionStatus.BLOCKED for result in results) == 1


def test_unconfirmed_windows_mutator_cannot_be_registered() -> None:
    executor = ToolExecutor()
    with pytest.raises(ValueError, match="explicit confirmation"):
        executor.register(
            ToolDefinition(
                name="unsafe_windows_action",
                description="Unsafe.",
                risk_level=RiskLevel.LOW,
                capabilities=frozenset({"windows", "action"}),
                tags=frozenset({"windows", "action"}),
            ),
            lambda: None,
        )
