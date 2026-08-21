from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.agent.loop import AgentLoop
from app.agent.models import AgentMode, AgentStatus
from app.core.engine import CoreEngine
from app.core.models import (
    Context,
    Request,
    TaskStatus,
    TaskStepStatus,
    ToolDefinition,
    ToolExecutionStatus,
    ToolResult,
)
from app.execution.events import ExecutionEventType
from app.execution.models import ExecutionLimits, RetryPolicy
from app.execution.task_service import TaskExecutionService
from app.memory.in_memory import InMemoryStore
from app.memory.manager import MemoryManager
from app.planning.models import PlanStatus, PlanStep
from app.providers.mock import MockProvider
from app.providers.registry import ProviderRegistry
from app.tools.executor import ToolExecutor


def make_engine(
    *,
    provider=None,
    tools: ToolExecutor | None = None,
    limits: ExecutionLimits | None = None,
) -> CoreEngine:
    registry = ProviderRegistry()
    registry.register(provider or MockProvider(), make_default=True)
    return CoreEngine(
        registry,
        MemoryManager(InMemoryStore()),
        tool_executor=tools,
        execution_limits=limits,
    )


@pytest.mark.parametrize(
    "kwargs",
    [
        {"max_plan_steps": 0},
        {"max_tool_calls": 0},
        {"max_model_iterations": 0},
        {"max_model_tokens": 0},
        {"timeout_seconds": 0},
    ],
)
def test_execution_limits_reject_non_positive_values(kwargs):
    with pytest.raises(ValueError):
        ExecutionLimits(**kwargs)


@pytest.mark.asyncio
async def test_core_uses_one_shared_execution_service_for_tasks_and_plans():
    tools = ToolExecutor()
    tools.register(
        ToolDefinition(name="shared", description="Shared"),
        lambda: "ok",
    )
    engine = make_engine(tools=tools)
    events = []
    engine.execution_service.event_bus.subscribe(events.append)
    plan = engine.create_plan(
        "shared service",
        [PlanStep("step", metadata={"tool_name": "shared"})],
    )

    task = await engine.execute_task("shared service", plan)

    assert task.status is TaskStatus.COMPLETED
    assert [event.event_type for event in events] == [
        ExecutionEventType.PLAN_STARTED,
        ExecutionEventType.STEP_STARTED,
        ExecutionEventType.STEP_COMPLETED,
        ExecutionEventType.PLAN_COMPLETED,
    ]


@pytest.mark.asyncio
async def test_core_propagates_request_and_conversation_identity_to_execution():
    tools = ToolExecutor()
    tools.register(
        ToolDefinition(name="identity", description="Identity"),
        lambda: "ok",
    )
    engine = make_engine(tools=tools)
    original_execute = engine.execution_service.execute
    captured = {}

    async def capture_context(plan, **kwargs):
        context = kwargs["execution_context"]
        captured["request_id"] = context.request_id
        captured["conversation_id"] = context.conversation_id
        captured["task_id"] = context.task_id
        return await original_execute(plan, **kwargs)

    engine.execution_service.execute = capture_context
    request_id = uuid4()
    conversation_id = uuid4()
    plan = engine.create_plan(
        "identity",
        [PlanStep("step", metadata={"tool_name": "identity"})],
    )

    task = await engine.execute_task(
        "identity",
        plan,
        request_id=request_id,
        conversation_id=conversation_id,
    )

    assert captured == {
        "request_id": request_id,
        "conversation_id": conversation_id,
        "task_id": task.task_id,
    }


@pytest.mark.asyncio
async def test_plan_step_budget_rejects_plan_before_any_tool_runs():
    called = False

    def tool() -> str:
        nonlocal called
        called = True
        return "unexpected"

    tools = ToolExecutor()
    tools.register(ToolDefinition(name="bounded", description="Bounded"), tool)
    engine = make_engine(
        tools=tools,
        limits=ExecutionLimits(max_plan_steps=1),
    )
    plan = engine.create_plan(
        "too many steps",
        [
            PlanStep("one", metadata={"tool_name": "bounded"}),
            PlanStep("two", dependencies=["one"], metadata={"tool_name": "bounded"}),
        ],
    )

    result = await engine.execute_plan(plan)

    assert result.status is PlanStatus.FAILED
    assert result.metadata["execution_outcome"] == "budget_exhausted"
    assert "step budget" in result.metadata["execution_error"].lower()
    assert called is False


@pytest.mark.asyncio
async def test_plan_tool_call_budget_stops_before_duplicate_side_effect():
    calls = 0

    def tool() -> str:
        nonlocal calls
        calls += 1
        return "ok"

    tools = ToolExecutor()
    tools.register(ToolDefinition(name="bounded", description="Bounded"), tool)
    engine = make_engine(
        tools=tools,
        limits=ExecutionLimits(max_tool_calls=1),
    )
    plan = engine.create_plan(
        "bounded calls",
        [
            PlanStep("one", metadata={"tool_name": "bounded"}),
            PlanStep("two", dependencies=["one"], metadata={"tool_name": "bounded"}),
        ],
    )

    result = await engine.execute_plan(plan)

    assert result.status is PlanStatus.FAILED
    assert result.metadata["execution_outcome"] == "budget_exhausted"
    assert result.metadata["execution_usage"]["tool_calls"] == 1
    assert calls == 1


@pytest.mark.asyncio
async def test_plan_time_budget_cancels_running_async_tool():
    stopped = asyncio.Event()

    async def tool() -> str:
        try:
            await asyncio.sleep(10)
            return "late"
        finally:
            stopped.set()

    tools = ToolExecutor()
    tools.register(ToolDefinition(name="slow", description="Slow"), tool)
    engine = make_engine(
        tools=tools,
        limits=ExecutionLimits(timeout_seconds=0.02),
    )
    plan = engine.create_plan(
        "bounded time",
        [PlanStep("step", metadata={"tool_name": "slow"})],
    )
    started = time.monotonic()

    result = await engine.execute_plan(plan)

    assert time.monotonic() - started < 0.2
    assert result.status is PlanStatus.FAILED
    assert result.metadata["execution_outcome"] == "budget_exhausted"
    assert stopped.is_set()


@pytest.mark.asyncio
async def test_partial_tool_result_is_preserved_and_not_retried():
    calls = 0

    def tool() -> ToolResult:
        nonlocal calls
        calls += 1
        return ToolResult(
            status=ToolExecutionStatus.PARTIAL,
            tool_name="partial",
            message="Only half completed.",
            data={"completed": 1, "remaining": 1},
        )

    tools = ToolExecutor()
    tools.register(ToolDefinition(name="partial", description="Partial"), tool)
    engine = make_engine(tools=tools)
    engine._task_execution_service = TaskExecutionService(
        task_manager=engine.task_manager,
        execution_service=engine.execution_service,
        retry_policy=RetryPolicy(max_attempts=3),
        limits=engine.execution_limits,
    )
    plan = engine.create_plan(
        "partial",
        [PlanStep("step", metadata={"tool_name": "partial"})],
    )

    task = await engine.execute_task("partial", plan)

    assert task.status is TaskStatus.FAILED
    assert task.result["outcome"] == "partial"
    assert task.result["partial_results"][0]["result"] == {
        "completed": 1,
        "remaining": 1,
    }
    assert calls == 1


@pytest.mark.asyncio
async def test_task_cancellation_cancels_active_task_step():
    started = asyncio.Event()

    async def tool() -> str:
        started.set()
        await asyncio.Event().wait()
        return "late"

    tools = ToolExecutor()
    tools.register(ToolDefinition(name="cancel", description="Cancel"), tool)
    engine = make_engine(tools=tools)
    plan = engine.create_plan(
        "cancel task",
        [PlanStep("step", metadata={"tool_name": "cancel"})],
    )
    cancel_event = asyncio.Event()
    running = asyncio.create_task(
        engine.execute_task("cancel task", plan, cancel_event=cancel_event)
    )
    await started.wait()
    cancel_event.set()

    task = await running

    assert task.status is TaskStatus.CANCELLED
    assert task.steps[0].status is TaskStepStatus.CANCELLED
    assert task.result["outcome"] == "cancelled"
    assert task.result["usage"]["tool_calls"] == 1


@pytest.mark.asyncio
async def test_plan_cancellation_publishes_one_terminal_event():
    cancel_event = asyncio.Event()
    cancel_event.set()
    engine = make_engine()
    events = []
    engine.execution_service.event_bus.subscribe(events.append)
    plan = engine.create_plan(
        "cancel once",
        [PlanStep("step", metadata={"tool_name": "unused"})],
    )

    result = await engine.execute_plan(plan, cancel_event=cancel_event)

    assert result.status is PlanStatus.CANCELLED
    assert [
        event.event_type
        for event in events
        if event.event_type is ExecutionEventType.PLAN_CANCELLED
    ] == [ExecutionEventType.PLAN_CANCELLED]


@pytest.mark.asyncio
async def test_failed_task_preserves_outcome_and_usage():
    engine = make_engine()
    plan = engine.create_plan(
        "fail visibly",
        [PlanStep("step", metadata={"tool_name": "missing"})],
    )

    task = await engine.execute_task("fail visibly", plan)

    assert task.status is TaskStatus.FAILED
    assert task.result["outcome"] == "failed"
    assert task.result["usage"]["tool_calls"] == 1
    assert task.result["partial_results"] == []


@pytest.mark.asyncio
async def test_invalid_task_plan_does_not_create_orphan_task():
    engine = make_engine()
    plan = engine.create_plan(
        "plan goal",
        [PlanStep("step", metadata={"tool_name": "unused"})],
    )

    with pytest.raises(ValueError, match="must match"):
        await engine.execute_task("different goal", plan)

    assert engine.task_manager.list() == []


@pytest.mark.asyncio
async def test_direct_tool_budget_stops_before_second_tool():
    calls = []
    tools = ToolExecutor()

    def tool(value: int) -> int:
        calls.append(value)
        return value

    tools.register(ToolDefinition(name="bounded", description="Bounded"), tool)

    class TwoToolProvider(MockProvider):
        async def generate(self, request, context, **kwargs):
            return SimpleNamespace(
                text="",
                model="mock-model",
                provider="mock",
                finish_reason="tool_calls",
                tool_calls=[
                    {"id": "one", "function": {"name": "bounded", "arguments": '{"value":1}'}},
                    {"id": "two", "function": {"name": "bounded", "arguments": '{"value":2}'}},
                ],
                usage={},
            )

    engine = make_engine(
        provider=TwoToolProvider(),
        tools=tools,
        limits=ExecutionLimits(max_tool_calls=1),
    )

    response = await engine.handle(Request("bounded"))

    assert calls == [1]
    assert response.metadata["outcome"] == "budget_exhausted"
    assert response.metadata["budget_reason"] == "tool_calls"
    assert response.metadata["completion_verified"] is False


@pytest.mark.asyncio
async def test_model_token_budget_stops_unverified_completion():
    class ExpensiveProvider(MockProvider):
        async def generate(self, request, context, **kwargs):
            return SimpleNamespace(
                text="Untrusted final claim.",
                model="mock-model",
                provider="mock",
                finish_reason="stop",
                tool_calls=[],
                usage={"total_tokens": 11},
            )

    engine = make_engine(
        provider=ExpensiveProvider(),
        limits=ExecutionLimits(max_model_tokens=10),
    )

    response = await engine.handle(Request("expensive"))

    assert response.metadata["outcome"] == "budget_exhausted"
    assert response.metadata["budget_reason"] == "model_tokens"
    assert response.metadata["completion_verified"] is False


@pytest.mark.asyncio
async def test_direct_request_cancellation_interrupts_provider():
    started = asyncio.Event()
    stopped = asyncio.Event()

    class BlockingProvider(MockProvider):
        async def generate(self, request, context, **kwargs):
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                stopped.set()

    engine = make_engine(provider=BlockingProvider())
    cancel_event = asyncio.Event()
    running = asyncio.create_task(
        engine.handle(Request("cancel"), cancel_event=cancel_event)
    )
    await started.wait()
    cancel_event.set()

    response = await running

    assert response.metadata["outcome"] == "cancelled"
    assert response.metadata["completion_verified"] is False
    assert stopped.is_set()


@pytest.mark.asyncio
async def test_external_request_cancellation_stops_provider_task():
    started = asyncio.Event()
    stopped = asyncio.Event()

    class BlockingProvider(MockProvider):
        async def generate(self, request, context, **kwargs):
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                stopped.set()

    engine = make_engine(provider=BlockingProvider())
    running = asyncio.create_task(engine.handle(Request("cancel externally")))
    await started.wait()
    running.cancel()

    with pytest.raises(asyncio.CancelledError):
        await running

    assert stopped.is_set()


@pytest.mark.asyncio
async def test_agent_direct_mode_does_not_report_failed_tool_as_completed():
    class FailingToolProvider(MockProvider):
        def __init__(self):
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
                        {"id": "bad", "function": {"name": "missing", "arguments": "{}"}},
                    ],
                    usage={},
                )

            return SimpleNamespace(
                text="I am done.",
                model="mock-model",
                provider="mock",
                finish_reason="stop",
                tool_calls=[],
                usage={},
            )

    loop = AgentLoop(engine=make_engine(provider=FailingToolProvider()))

    result = await loop.run(Request("do it"), mode=AgentMode.DIRECT)

    assert result.status is AgentStatus.FAILED
    assert result.metadata["completion_verified"] is False


@pytest.mark.asyncio
async def test_agent_direct_mode_converts_provider_exception_to_failure():
    class BrokenProvider(MockProvider):
        async def generate(self, request, context, **kwargs):
            raise RuntimeError("provider broke")

    loop = AgentLoop(engine=make_engine(provider=BrokenProvider()))

    result = await loop.run(Request("hello"), mode=AgentMode.DIRECT)

    assert result.status is AgentStatus.FAILED
    assert result.metadata["error"] == "provider broke"


@pytest.mark.asyncio
async def test_invalid_tool_call_is_returned_to_provider_as_structured_failure():
    class InvalidThenFinalProvider(MockProvider):
        def __init__(self):
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
                        {"id": "invalid", "function": {"name": "echo", "arguments": "[]"}},
                    ],
                    usage={},
                )

            tool_message = context.values["messages"][-1]
            assert tool_message["role"] == "tool"
            assert tool_message["tool_call_id"] == "invalid"
            assert "JSON object" in tool_message["content"]
            return SimpleNamespace(
                text="The tool call failed.",
                model="mock-model",
                provider="mock",
                finish_reason="stop",
                tool_calls=[],
                usage={},
            )

    tools = ToolExecutor()
    tools.register(
        ToolDefinition(name="echo", description="Echo"),
        lambda value: value,
    )
    engine = make_engine(provider=InvalidThenFinalProvider(), tools=tools)

    response = await engine.handle(Request("invalid"), context=Context())

    assert response.metadata["invalid_tool_calls"] == 1
    assert response.metadata["failed_tool_calls"] == 1
    assert response.metadata["completion_verified"] is False
