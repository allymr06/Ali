from __future__ import annotations

import asyncio

from app.core.models import ToolDefinition
from app.execution.replanner import (
    Replanner,
    replace_failed_step,
)
from app.execution.service import ExecutionService
from app.execution.verification import VerificationEngine
from app.planning.executor import PlanExecutor
from app.planning.models import (
    PlanStatus,
    PlanStep,
)
from app.planning.planner import Planner
from app.tools.executor import ToolExecutor


def test_replanner_rejects_negative_limit():
    try:
        Replanner(max_replans=-1)
    except ValueError:
        return

    raise AssertionError("Expected ValueError")


def test_replanner_without_callback_returns_none():
    planner = Planner()

    plan = planner.create_plan(
        "goal",
        [PlanStep("step")],
    )

    step = plan.steps[0]

    replanner = Replanner(
        max_replans=1,
    )

    assert replanner.can_replan(0) is False
    assert replanner.replan(
        plan,
        step,
        "failure",
    ) is None


def test_replanner_callback_receives_failure_context():
    planner = Planner()

    plan = planner.create_plan(
        "goal",
        [PlanStep("step")],
    )

    step = plan.steps[0]
    captured = {}

    def callback(
        received_plan,
        received_step,
        error,
    ):
        captured["plan"] = received_plan
        captured["step"] = received_step
        captured["error"] = error

        return planner.create_plan(
            "goal",
            [PlanStep("replacement")],
        )

    replanner = Replanner(
        callback,
        max_replans=1,
    )

    result = replanner.replan(
        plan,
        step,
        "boom",
    )

    assert result is not None
    assert captured["plan"] is plan
    assert captured["step"] is step
    assert captured["error"] == "boom"


def test_replace_failed_step_preserves_dependencies():
    planner = Planner()

    plan = planner.create_plan(
        "goal",
        [
            PlanStep("first"),
            PlanStep(
                "failed",
                dependencies=["first"],
            ),
        ],
    )

    failed = plan.get_step("failed")
    replacement = PlanStep("replacement")

    replace_failed_step(
        plan,
        failed,
        replacement,
    )

    assert plan.steps[1] is replacement
    assert replacement.dependencies == ["first"]
    assert replacement.status.value == "pending"


def test_execution_replans_after_failure():
    tools = ToolExecutor()
    calls = {"count": 0}

    def broken():
        calls["count"] += 1
        raise RuntimeError("broken")

    def recovery():
        calls["count"] += 1
        return "recovered"

    tools.register(
        ToolDefinition(
            name="broken",
            description="broken",
        ),
        broken,
    )

    tools.register(
        ToolDefinition(
            name="recovery",
            description="recovery",
        ),
        recovery,
    )

    planner = Planner()

    original_plan = planner.create_plan(
        "recoverable task",
        [
            PlanStep(
                "broken",
                metadata={
                    "tool_name": "broken",
                    "parameters": {},
                },
            ),
        ],
    )

    def callback(
        plan,
        failed_step,
        error,
    ):
        return planner.create_plan(
            "recoverable task",
            [
                PlanStep(
                    "recovery",
                    metadata={
                        "tool_name": "recovery",
                        "parameters": {},
                    },
                ),
            ],
        )

    service = ExecutionService(
        tool_executor=tools,
        plan_executor=PlanExecutor(planner),
        verification_engine=VerificationEngine(),
        replanner=Replanner(
            callback,
            max_replans=1,
        ),
    )

    result = asyncio.run(
        service.execute(original_plan)
    )

    assert result.status is PlanStatus.COMPLETED
    assert calls["count"] == 2
    assert result.steps[0].name == "recovery"


def test_execution_does_not_replan_without_available_budget():
    tools = ToolExecutor()

    def broken():
        raise RuntimeError("broken")

    tools.register(
        ToolDefinition(
            name="broken",
            description="broken",
        ),
        broken,
    )

    planner = Planner()

    plan = planner.create_plan(
        "non recoverable",
        [
            PlanStep(
                "broken",
                metadata={
                    "tool_name": "broken",
                    "parameters": {},
                },
            ),
        ],
    )

    callback_calls = {"count": 0}

    def callback(plan, step, error):
        callback_calls["count"] += 1
        return None

    service = ExecutionService(
        tool_executor=tools,
        plan_executor=PlanExecutor(planner),
        verification_engine=VerificationEngine(),
        replanner=Replanner(
            callback,
            max_replans=0,
        ),
    )

    result = asyncio.run(
        service.execute(plan)
    )

    assert result.status is PlanStatus.FAILED
    assert callback_calls["count"] == 0
