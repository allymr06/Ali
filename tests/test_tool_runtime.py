from __future__ import annotations

import asyncio
from typing import Any

import pytest

from app.core.engine import CoreEngine
from app.core.models import Request, ToolDefinition, ToolExecutionStatus
from app.execution.service import ExecutionService
from app.execution.verification import VerificationEngine
from app.memory.in_memory import InMemoryStore
from app.memory.manager import MemoryManager
from app.planning.executor import PlanExecutor
from app.planning.models import PlanStatus, PlanStep
from app.planning.planner import Planner
from app.providers.base import ModelResponse
from app.providers.mock import MockProvider
from app.providers.registry import ProviderRegistry
from app.tools.base import RegisteredTool
from app.tools.executor import ToolExecutor
from app.tools.registry import ToolRegistry


def test_tool_definition_normalizes_discovery_metadata() -> None:
    definition = ToolDefinition(
        name=" search ",
        description=" Search data ",
        version=" 2.1.0 ",
        capabilities=frozenset({" Web ", "SEARCH"}),
        tags=frozenset({" Read-Only ", "Public"}),
    )

    assert definition.name == "search"
    assert definition.description == "Search data"
    assert definition.version == "2.1.0"
    assert definition.capabilities == frozenset({"web", "search"})
    assert definition.tags == frozenset({"read-only", "public"})


@pytest.mark.parametrize(
    "kwargs",
    [
        {"version": " "},
        {"max_concurrency": 0},
        {"retry_max_attempts": 0},
        {"retry_backoff_seconds": -0.1},
        {"retry_max_attempts": 2, "idempotent": False},
    ],
)
def test_tool_definition_rejects_unsafe_runtime_contracts(
    kwargs: dict[str, Any],
) -> None:
    with pytest.raises(ValueError):
        ToolDefinition(name="tool", description="Tool", **kwargs)


def test_registry_lifecycle_and_revision_are_explicit() -> None:
    registry = ToolRegistry()
    tool = RegisteredTool(
        ToolDefinition(name="dynamic", description="Dynamic"),
        lambda: "ok",
        source="plugin:test",
    )

    assert registry.revision == 0
    registry.register(tool)
    assert registry.revision == 1
    registry.disable("dynamic")
    assert registry.revision == 2
    assert registry.contains("dynamic") is False
    assert registry.contains("dynamic", include_disabled=True) is True
    assert registry.list_names() == ()
    assert registry.get("dynamic", include_disabled=True) is tool

    registry.enable("dynamic")
    assert registry.revision == 3
    assert registry.get("dynamic") is tool


def test_contract_discovery_filters_names_capabilities_and_tags() -> None:
    tools = ToolExecutor()
    tools.register(
        ToolDefinition(
            name="weather",
            description="Weather",
            capabilities=frozenset({"internet", "weather"}),
            tags=frozenset({"read"}),
        ),
        lambda city: city,
    )
    tools.register(
        ToolDefinition(
            name="notes",
            description="Notes",
            capabilities=frozenset({"storage"}),
            tags=frozenset({"write"}),
        ),
        lambda text: text,
    )

    contracts = tools.get_tool_contracts(
        names={"weather", "notes"},
        capabilities={"INTERNET"},
        tags={"READ"},
    )

    assert [contract["name"] for contract in contracts] == ["weather"]
    assert contracts[0]["version"] == "1.0.0"
    assert contracts[0]["capabilities"] == ["internet", "weather"]
    assert contracts[0]["retry_policy"]["max_attempts"] == 1


def test_disabled_tools_are_hidden_from_provider_and_execution() -> None:
    tools = ToolExecutor()
    tools.register(
        ToolDefinition(name="hidden", description="Hidden"),
        lambda: "secret",
    )
    tools.disable("hidden")

    assert tools.get_openai_tools() == []
    result = tools.execute("hidden")
    assert result.status is ToolExecutionStatus.FAILED
    assert "disabled" in (result.error or "").lower()


def test_executor_registers_source_and_initial_lifecycle_state() -> None:
    tools = ToolExecutor()
    tools.register(
        ToolDefinition(name="plugin_tool", description="Plugin tool"),
        lambda: "ok",
        enabled=False,
        source="plugin:calendar",
    )

    contract = tools.get_tool_contracts(include_disabled=True)[0]

    assert contract["enabled"] is False
    assert contract["source"] == "plugin:calendar"
    assert tools.get_openai_tools() == []


@pytest.mark.asyncio
async def test_per_tool_concurrency_limit_blocks_overlapping_execution() -> None:
    tools = ToolExecutor()
    started = asyncio.Event()
    release = asyncio.Event()

    async def slow() -> str:
        started.set()
        await release.wait()
        return "done"

    tools.register(
        ToolDefinition(
            name="slow",
            description="Slow",
            max_concurrency=1,
        ),
        slow,
    )

    first_task = asyncio.create_task(tools.execute("slow"))
    await started.wait()
    second = await tools.execute("slow")
    release.set()
    first = await first_task

    assert second.status is ToolExecutionStatus.BLOCKED
    assert "concurrency" in second.message.lower()
    assert first.status is ToolExecutionStatus.SUCCESS


@pytest.mark.asyncio
async def test_tool_contract_can_raise_execution_service_retry_budget() -> None:
    tools = ToolExecutor()
    calls = 0

    def flaky() -> str:
        nonlocal calls
        calls += 1
        if calls < 2:
            raise RuntimeError("temporary")
        return "ok"

    tools.register(
        ToolDefinition(
            name="flaky_contract",
            description="Flaky",
            retry_max_attempts=2,
            retry_backoff_seconds=0,
            idempotent=True,
        ),
        flaky,
    )
    planner = Planner()
    service = ExecutionService(
        tool_executor=tools,
        plan_executor=PlanExecutor(planner),
        verification_engine=VerificationEngine(),
    )
    plan = planner.create_plan(
        "retry",
        [
            PlanStep(
                "step",
                metadata={"tool_name": "flaky_contract", "parameters": {}},
            )
        ],
    )

    result = await service.execute(plan)

    assert result.status is PlanStatus.COMPLETED
    assert calls == 2
    assert result.steps[0].metadata["attempts"] == 2


@pytest.mark.asyncio
async def test_core_exposes_only_request_scoped_tools_to_provider() -> None:
    class CapturingProvider(MockProvider):
        def __init__(self) -> None:
            self.tools: list[dict[str, Any]] | None = None

        async def generate(self, request, context, **kwargs):
            self.tools = kwargs.get("tools")
            return await super().generate(request, context, **kwargs)

    provider = CapturingProvider()
    providers = ProviderRegistry()
    providers.register(provider, make_default=True)
    tools = ToolExecutor()
    tools.register(
        ToolDefinition(
            name="weather",
            description="Weather",
            capabilities=frozenset({"weather"}),
        ),
        lambda: "sunny",
    )
    tools.register(
        ToolDefinition(
            name="mail",
            description="Mail",
            capabilities=frozenset({"mail"}),
        ),
        lambda: "sent",
    )
    engine = CoreEngine(
        providers,
        MemoryManager(InMemoryStore()),
        tool_executor=tools,
    )

    await engine.handle(
        Request("Weather", metadata={"tool_capabilities": ["weather"]})
    )

    assert provider.tools is not None
    names = [item["function"]["name"] for item in provider.tools]
    assert names == ["weather"]


@pytest.mark.asyncio
async def test_invalid_core_tool_filter_fails_closed() -> None:
    class CapturingProvider(MockProvider):
        def __init__(self) -> None:
            self.tools = "not-called"

        async def generate(self, request, context, **kwargs):
            self.tools = kwargs.get("tools")
            return await super().generate(request, context, **kwargs)

    provider = CapturingProvider()
    providers = ProviderRegistry()
    providers.register(provider, make_default=True)
    request = Request("Test", metadata={"allowed_tools": {"bad": "shape"}})
    engine = CoreEngine(providers, MemoryManager(InMemoryStore()))

    await engine.handle(request)

    assert provider.tools is None
    assert "tool_filter_error" in request.metadata


@pytest.mark.asyncio
async def test_core_rejects_tool_call_outside_request_scope() -> None:
    class OutOfScopeProvider(MockProvider):
        def __init__(self) -> None:
            self.calls = 0

        async def generate(self, request, context, **kwargs):
            self.calls += 1
            if self.calls == 1:
                return ModelResponse(
                    text="",
                    model="mock-model",
                    provider="mock",
                    finish_reason="tool_calls",
                    tool_calls=[
                        {
                            "id": "call-1",
                            "type": "function",
                            "function": {
                                "name": "mail",
                                "arguments": "{}",
                            },
                        }
                    ],
                )
            return ModelResponse(
                text="done",
                model="mock-model",
                provider="mock",
                finish_reason="stop",
            )

    provider = OutOfScopeProvider()
    providers = ProviderRegistry()
    providers.register(provider, make_default=True)
    tools = ToolExecutor()
    executed = False

    def mail() -> str:
        nonlocal executed
        executed = True
        return "sent"

    tools.register(
        ToolDefinition(
            name="mail",
            description="Mail",
            capabilities=frozenset({"mail"}),
        ),
        mail,
    )
    engine = CoreEngine(
        providers,
        MemoryManager(InMemoryStore()),
        tool_executor=tools,
    )

    response = await engine.handle(
        Request("Weather", metadata={"tool_capabilities": ["weather"]})
    )

    assert executed is False
    assert response.metadata["invalid_tool_calls"] == 1
    assert response.metadata["tool_calls"] == 0
