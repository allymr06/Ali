from __future__ import annotations

import pytest

import asyncio
from uuid import uuid4

from app.security.approval import ApprovalGrant
from app.core.models import RiskLevel, ToolDefinition, ToolExecutionStatus, ToolResult
from app.execution.models import RetryPolicy
from app.execution.service import ExecutionService
from app.execution.verification import VerificationEngine
from app.planning.executor import PlanExecutor
from app.planning.models import PlanStatus, PlanStep, PlanStepStatus
from app.planning.planner import Planner
from app.tools.executor import ToolExecutor


def make_service(*, retry_policy=None, tool_executor=None):
    planner = Planner()
    return ExecutionService(
        tool_executor=tool_executor or ToolExecutor(),
        plan_executor=PlanExecutor(planner),
        verification_engine=VerificationEngine(),
        retry_policy=retry_policy,
    )


def test_retry_policy_validation():
    for kwargs in ({"max_attempts": 0}, {"backoff_seconds": -1}):
        try:
            RetryPolicy(**kwargs)
        except ValueError:
            continue
        raise AssertionError("Expected ValueError")


def test_verification_rejects_failed_result():
    result = ToolResult(
        status=ToolExecutionStatus.FAILED,
        tool_name="test",
        error="boom",
    )
    verification = VerificationEngine().verify(result)
    assert verification.passed is False
    assert "boom" in verification.reason


def test_verified_success_passes():
    result = ToolResult(
        status=ToolExecutionStatus.SUCCESS,
        tool_name="test",
        data={"ok": True},
        verified=True,
    )
    assert VerificationEngine().verify(result).passed is True


def test_custom_verifier_rejects_result():
    result = ToolResult(
        status=ToolExecutionStatus.SUCCESS,
        tool_name="test",
        data=1,
        verified=True,
    )
    verification = VerificationEngine().verify(
        result,
        verifier=lambda value: value == 2,
    )
    assert verification.passed is False


def test_execution_verifies_success():
    tools = ToolExecutor()
    tools.register(
        ToolDefinition(name="ok", description="ok"),
        lambda: "ok",
    )

    plan = Planner().create_plan(
        "success",
        [PlanStep("step", metadata={"tool_name": "ok", "parameters": {}})],
    )

    result = asyncio.run(
        make_service(tool_executor=tools).execute(plan)
    )

    assert result.status is PlanStatus.COMPLETED
    assert result.steps[0].metadata["verified"] is True


def test_execution_retries_transient_failure():
    tools = ToolExecutor()
    calls = {"count": 0}

    def flaky():
        calls["count"] += 1
        if calls["count"] == 1:
            raise RuntimeError("temporary")
        return "ok"

    tools.register(
        ToolDefinition(name="flaky", description="flaky"),
        flaky,
    )

    plan = Planner().create_plan(
        "retry",
        [PlanStep("step", metadata={"tool_name": "flaky", "parameters": {}})],
    )

    result = asyncio.run(
        make_service(
            tool_executor=tools,
            retry_policy=RetryPolicy(max_attempts=2),
        ).execute(plan)
    )

    assert calls["count"] == 2
    assert result.status is PlanStatus.COMPLETED


def test_execution_retries_verification_failure():
    tools = ToolExecutor()
    calls = {"count": 0}

    def value():
        calls["count"] += 1
        return calls["count"]

    tools.register(
        ToolDefinition(name="value", description="value"),
        value,
    )

    plan = Planner().create_plan(
        "verification retry",
        [
            PlanStep(
                "step",
                metadata={
                    "tool_name": "value",
                    "parameters": {},
                    "verifier": lambda item: item == 2,
                },
            )
        ],
    )

    result = asyncio.run(
        make_service(
            tool_executor=tools,
            retry_policy=RetryPolicy(max_attempts=2),
        ).execute(plan)
    )

    assert calls["count"] == 2
    assert result.status is PlanStatus.COMPLETED


def test_execution_cancels_plan_while_tool_is_running():
    tools = ToolExecutor()
    started = asyncio.Event()
    stopped = asyncio.Event()

    async def long_running() -> str:
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            stopped.set()

    tools.register(
        ToolDefinition(name="long_running", description="Long running"),
        long_running,
    )
    plan = Planner().create_plan(
        "cancel running tool",
        [PlanStep("step", metadata={"tool_name": "long_running"})],
    )

    async def run():
        cancel_event = asyncio.Event()
        task = asyncio.create_task(
            make_service(tool_executor=tools).execute(
                plan,
                cancel_event=cancel_event,
            )
        )
        await started.wait()
        cancel_event.set()
        return await task

    result = asyncio.run(run())

    assert result.status is PlanStatus.CANCELLED
    assert result.steps[0].status is PlanStepStatus.CANCELLED
    assert result.steps[0].metadata["verified"] is False
    assert stopped.is_set()


def test_execution_cancellation_interrupts_retry_backoff():
    tools = ToolExecutor()
    attempted = asyncio.Event()
    calls = 0

    async def failing() -> str:
        nonlocal calls
        calls += 1
        attempted.set()
        raise RuntimeError("retry later")

    tools.register(
        ToolDefinition(name="failing", description="Failing"),
        failing,
    )
    plan = Planner().create_plan(
        "cancel backoff",
        [PlanStep("step", metadata={"tool_name": "failing"})],
    )
    service = make_service(
        tool_executor=tools,
        retry_policy=RetryPolicy(max_attempts=3, backoff_seconds=10),
    )

    async def run():
        cancel_event = asyncio.Event()
        task = asyncio.create_task(
            service.execute(plan, cancel_event=cancel_event)
        )
        await attempted.wait()
        await asyncio.sleep(0)
        cancel_event.set()
        return await asyncio.wait_for(task, timeout=0.5)

    result = asyncio.run(run())

    assert result.status is PlanStatus.CANCELLED
    assert result.steps[0].status is PlanStepStatus.CANCELLED
    assert calls == 1


def test_execution_rejects_approval_grant_bound_to_another_action():
    tools = ToolExecutor()
    called = False

    def dangerous() -> str:
        nonlocal called
        called = True
        return "executed"

    tools.register(
        ToolDefinition(
            name="dangerous",
            description="Dangerous",
            risk_level=RiskLevel.HIGH,
        ),
        dangerous,
    )
    plan = Planner().create_plan(
        "reject mismatched approval",
        [
            PlanStep(
                "step",
                metadata={
                    "tool_name": "dangerous",
                    "parameters": {"target": "current"},
                    "_approval_grant": ApprovalGrant(
                        operation_id=uuid4(),
                        binding_digest="not-the-current-action",
                        expires_at=None,
                        task_id=None,
                    ),
                },
            )
        ],
    )

    result = asyncio.run(make_service(tool_executor=tools).execute(plan))

    assert result.status is PlanStatus.FAILED
    assert called is False
    assert "confirmation" in result.steps[0].metadata["tool_error"].lower()


def test_execution_fails_after_retry_exhaustion():
    tools = ToolExecutor()

    def always_fail():
        raise RuntimeError("boom")

    tools.register(
        ToolDefinition(name="fail", description="fail"),
        always_fail,
    )

    plan = Planner().create_plan(
        "failure",
        [PlanStep("step", metadata={"tool_name": "fail", "parameters": {}})],
    )

    result = asyncio.run(
        make_service(
            tool_executor=tools,
            retry_policy=RetryPolicy(max_attempts=3),
        ).execute(plan)
    )

    assert result.status is PlanStatus.FAILED
    assert result.steps[0].metadata["attempts"] == 3


def test_dependency_order_is_preserved():
    tools = ToolExecutor()
    order = []

    tools.register(
        ToolDefinition(name="a", description="a"),
        lambda: order.append("a") or "a",
    )
    tools.register(
        ToolDefinition(name="b", description="b"),
        lambda: order.append("b") or "b",
    )

    plan = Planner().create_plan(
        "dependency",
        [
            PlanStep(
                "b",
                dependencies=["a"],
                metadata={"tool_name": "b", "parameters": {}},
            ),
            PlanStep(
                "a",
                metadata={"tool_name": "a", "parameters": {}},
            ),
        ],
    )

    result = asyncio.run(
        make_service(tool_executor=tools).execute(plan)
    )

    assert order == ["a", "b"]
    assert result.status is PlanStatus.COMPLETED


def test_execution_event_bus_records_execution_lifecycle():
    import asyncio

    from app.execution.events import (
        ExecutionEventBus,
        ExecutionEventType,
    )

    tools = ToolExecutor()

    tools.register(
        ToolDefinition(
            name="event_tool",
            description="event test",
        ),
        lambda: "ok",
    )

    planner = Planner()

    plan = planner.create_plan(
        "event lifecycle",
        [
            PlanStep(
                "step",
                metadata={
                    "tool_name": "event_tool",
                    "parameters": {},
                },
            ),
        ],
    )

    event_bus = ExecutionEventBus()

    service = ExecutionService(
        tool_executor=tools,
        plan_executor=PlanExecutor(planner),
        verification_engine=VerificationEngine(),
        event_bus=event_bus,
    )

    result = asyncio.run(
        service.execute(plan)
    )

    assert result.status is PlanStatus.COMPLETED

    event_types = [
        event.event_type
        for event in event_bus.events
    ]

    assert event_types == [
        ExecutionEventType.PLAN_STARTED,
        ExecutionEventType.STEP_STARTED,
        ExecutionEventType.STEP_COMPLETED,
        ExecutionEventType.PLAN_COMPLETED,
    ]


def test_execution_event_bus_supports_async_subscribers():
    import asyncio
    from uuid import uuid4

    from app.execution.events import (
        ExecutionEvent,
        ExecutionEventBus,
        ExecutionEventType,
    )

    bus = ExecutionEventBus()
    received = []

    async def subscriber(event):
        received.append(event.event_type)

    bus.subscribe(subscriber)

    event = ExecutionEvent(
        event_type=ExecutionEventType.PLAN_STARTED,
        plan_id=uuid4(),
    )

    asyncio.run(
        bus.publish(event)
    )

    assert received == [
        ExecutionEventType.PLAN_STARTED,
    ]

    bus.unsubscribe(subscriber)

    asyncio.run(
        bus.publish(
            ExecutionEvent(
                event_type=ExecutionEventType.PLAN_COMPLETED,
                plan_id=event.plan_id,
            )
        )
    )

    assert received == [
        ExecutionEventType.PLAN_STARTED,
    ]


def test_execution_event_bus_deduplicates_subscribers():
    import asyncio
    from uuid import uuid4

    from app.execution.events import (
        ExecutionEvent,
        ExecutionEventBus,
        ExecutionEventType,
    )

    bus = ExecutionEventBus()
    count = {"value": 0}

    async def subscriber(event):
        count["value"] += 1

    bus.subscribe(subscriber)
    bus.subscribe(subscriber)

    asyncio.run(
        bus.publish(
            ExecutionEvent(
                event_type=ExecutionEventType.PLAN_STARTED,
                plan_id=uuid4(),
            )
        )
    )

    assert count["value"] == 1


@pytest.mark.asyncio
async def test_execution_event_bus_emits_retry_event():
    from app.execution.events import (
        ExecutionEventType,
        ExecutionEventBus,
    )

    tools = ToolExecutor()
    calls = {"count": 0}

    def flaky():
        calls["count"] += 1

        if calls["count"] == 1:
            raise RuntimeError("temporary")

        return "ok"

    tools.register(
        ToolDefinition(
            name="flaky",
            description="flaky",
        ),
        flaky,
    )

    planner = Planner()

    plan = planner.create_plan(
        "retry event",
        [
            PlanStep(
                "step",
                metadata={
                    "tool_name": "flaky",
                    "parameters": {},
                },
            ),
        ],
    )

    bus = ExecutionEventBus()

    service = ExecutionService(
        tool_executor=tools,
        plan_executor=PlanExecutor(planner),
        verification_engine=VerificationEngine(),
        retry_policy=RetryPolicy(max_attempts=2),
        event_bus=bus,
    )

    result = await service.execute(plan)

    assert result.status is PlanStatus.COMPLETED
    assert any(
        event.event_type is ExecutionEventType.STEP_RETRYING
        for event in bus.events
    )
