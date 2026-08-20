from __future__ import annotations

import asyncio

import pytest

from app.core.models import ToolDefinition
from app.execution.models import RetryPolicy
from app.execution.task_service import TaskExecutionService
from app.memory.in_memory import InMemoryStore
from app.memory.manager import MemoryManager
from app.planning.models import PlanStatus, PlanStep
from app.planning.planner import Planner
from app.tasks.manager import TaskManager
from app.tools.executor import ToolExecutor


def create_service(
    *,
    retry_policy: RetryPolicy | None = None,
):
    task_manager = TaskManager()
    tool_executor = ToolExecutor()

    service = TaskExecutionService(
        task_manager=task_manager,
        tool_executor=tool_executor,
        retry_policy=retry_policy,
    )

    return task_manager, tool_executor, service


def test_task_execution_requires_matching_goal():
    task_manager, _, service = create_service()

    task = task_manager.create("A")

    plan = Planner().create_plan(
        "B",
        [
            PlanStep("step"),
        ],
    )

    with pytest.raises(ValueError, match="goal"):
        asyncio.run(
            service.execute(
                task.task_id,
                plan,
            )
        )


def test_task_execution_rejects_empty_plan():
    task_manager, _, service = create_service()

    task = task_manager.create("A")
    plan = Planner().create_plan("A")

    with pytest.raises(
        ValueError,
        match="without plan steps",
    ):
        asyncio.run(
            service.execute(
                task.task_id,
                plan,
            )
        )


def test_task_execution_requires_queued_task():
    task_manager, _, service = create_service()

    task = task_manager.create("A")
    task_manager.start(task.task_id)

    plan = Planner().create_plan(
        "A",
        [
            PlanStep("step"),
        ],
    )

    with pytest.raises(ValueError, match="state running"):
        asyncio.run(
            service.execute(
                task.task_id,
                plan,
            )
        )


def test_task_execution_mirrors_successful_plan():
    task_manager, tool_executor, service = create_service()

    tool_executor.register(
        ToolDefinition(
            name="hello",
            description="returns hello",
        ),
        lambda: "hello",
    )

    task = task_manager.create("Run hello")

    plan = Planner().create_plan(
        "Run hello",
        [
            PlanStep(
                "hello",
                metadata={
                    "tool_name": "hello",
                    "parameters": {},
                },
            ),
        ],
    )

    result = asyncio.run(
        service.execute(
            task.task_id,
            plan,
        )
    )

    assert result.status.value == "completed"
    assert result.progress == 1.0
    assert len(result.steps) == 1
    assert result.steps[0].status.value == "completed"
    assert result.steps[0].result == "hello"
    assert result.current_step is None


def test_task_execution_mirrors_multiple_steps_in_dependency_order():
    task_manager, tool_executor, service = create_service()

    order: list[str] = []

    tool_executor.register(
        ToolDefinition(
            name="first",
            description="first",
        ),
        lambda: order.append("first") or "first",
    )

    tool_executor.register(
        ToolDefinition(
            name="second",
            description="second",
        ),
        lambda: order.append("second") or "second",
    )

    task = task_manager.create("Multi-step")

    plan = Planner().create_plan(
        "Multi-step",
        [
            PlanStep(
                "second",
                dependencies=["first"],
                metadata={
                    "tool_name": "second",
                    "parameters": {},
                },
            ),
            PlanStep(
                "first",
                metadata={
                    "tool_name": "first",
                    "parameters": {},
                },
            ),
        ],
    )

    result = asyncio.run(
        service.execute(
            task.task_id,
            plan,
        )
    )

    assert order == ["first", "second"]
    assert result.status.value == "completed"
    assert result.progress == 1.0

    assert [step.status.value for step in result.steps] == [
        "completed",
        "completed",
    ]


def test_task_execution_progresses_after_each_completed_step():
    task_manager, tool_executor, service = create_service()

    tool_executor.register(
        ToolDefinition(
            name="one",
            description="one",
        ),
        lambda: "one",
    )

    tool_executor.register(
        ToolDefinition(
            name="two",
            description="two",
        ),
        lambda: "two",
    )

    task = task_manager.create("Two steps")

    plan = Planner().create_plan(
        "Two steps",
        [
            PlanStep(
                "one",
                metadata={
                    "tool_name": "one",
                    "parameters": {},
                },
            ),
            PlanStep(
                "two",
                dependencies=["one"],
                metadata={
                    "tool_name": "two",
                    "parameters": {},
                },
            ),
        ],
    )

    result = asyncio.run(
        service.execute(
            task.task_id,
            plan,
        )
    )

    assert result.progress == 1.0
    assert result.steps[0].status.value == "completed"
    assert result.steps[1].status.value == "completed"


def test_task_execution_mirrors_failure():
    task_manager, tool_executor, service = create_service()

    def fail():
        raise RuntimeError("failure")

    tool_executor.register(
        ToolDefinition(
            name="fail",
            description="failure",
        ),
        fail,
    )

    task = task_manager.create("Failure")

    plan = Planner().create_plan(
        "Failure",
        [
            PlanStep(
                "fail",
                metadata={
                    "tool_name": "fail",
                    "parameters": {},
                },
            ),
        ],
    )

    result = asyncio.run(
        service.execute(
            task.task_id,
            plan,
        )
    )

    assert result.status.value == "failed"
    assert result.error is not None
    assert result.steps[0].status.value == "failed"


def test_task_execution_retries_and_completes():
    task_manager, tool_executor, _ = create_service()

    calls = {"count": 0}

    def flaky():
        calls["count"] += 1

        if calls["count"] == 1:
            raise RuntimeError("temporary")

        return "ok"

    tool_executor.register(
        ToolDefinition(
            name="flaky",
            description="flaky",
        ),
        flaky,
    )

    service = TaskExecutionService(
        task_manager=task_manager,
        tool_executor=tool_executor,
        retry_policy=RetryPolicy(max_attempts=2),
    )

    task = task_manager.create("Retry")

    plan = Planner().create_plan(
        "Retry",
        [
            PlanStep(
                "flaky",
                metadata={
                    "tool_name": "flaky",
                    "parameters": {},
                },
            ),
        ],
    )

    result = asyncio.run(
        service.execute(
            task.task_id,
            plan,
        )
    )

    assert calls["count"] == 2
    assert result.status.value == "completed"
    assert result.steps[0].status.value == "completed"


def test_task_execution_retries_verification_failure():
    task_manager, tool_executor, _ = create_service()

    calls = {"count": 0}

    def produce_value():
        calls["count"] += 1
        return calls["count"]

    tool_executor.register(
        ToolDefinition(
            name="value",
            description="value",
        ),
        produce_value,
    )

    service = TaskExecutionService(
        task_manager=task_manager,
        tool_executor=tool_executor,
        retry_policy=RetryPolicy(max_attempts=2),
    )

    task = task_manager.create("Verification retry")

    plan = Planner().create_plan(
        "Verification retry",
        [
            PlanStep(
                "verify",
                metadata={
                    "tool_name": "value",
                    "parameters": {},
                    "verifier": lambda value: value == 2,
                },
            ),
        ],
    )

    result = asyncio.run(
        service.execute(
            task.task_id,
            plan,
        )
    )

    assert calls["count"] == 2
    assert result.status.value == "completed"


def test_task_execution_fails_after_retry_exhaustion():
    task_manager, tool_executor, service = create_service(
        retry_policy=RetryPolicy(max_attempts=3),
    )

    calls = {"count": 0}

    def fail():
        calls["count"] += 1
        raise RuntimeError("permanent failure")

    tool_executor.register(
        ToolDefinition(
            name="fail",
            description="failure",
        ),
        fail,
    )

    task = task_manager.create("Permanent failure")

    plan = Planner().create_plan(
        "Permanent failure",
        [
            PlanStep(
                "fail",
                metadata={
                    "tool_name": "fail",
                    "parameters": {},
                },
            ),
        ],
    )

    result = asyncio.run(
        service.execute(
            task.task_id,
            plan,
        )
    )

    assert calls["count"] == 3
    assert result.status.value == "failed"
    assert result.steps[0].status.value == "failed"


def test_task_plan_mapping_is_preserved():
    task_manager, tool_executor, service = create_service()

    tool_executor.register(
        ToolDefinition(
            name="test",
            description="test",
        ),
        lambda: "ok",
    )

    task = task_manager.create("Mapping")

    plan = Planner().create_plan(
        "Mapping",
        [
            PlanStep(
                "step",
                metadata={
                    "tool_name": "test",
                    "parameters": {},
                },
            ),
        ],
    )

    result = asyncio.run(
        service.execute(
            task.task_id,
            plan,
        )
    )

    task_step = result.steps[0]

    assert task_step.metadata["plan_step_id"] == str(
        plan.steps[0].step_id
    )
