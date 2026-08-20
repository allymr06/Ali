from __future__ import annotations

import asyncio
from pathlib import Path
from uuid import UUID, uuid4

import pytest

from app.core.models import ToolDefinition
from app.execution.coordinator import (
    ExecutionCoordinator,
    RecoveryCandidateStatus,
)
from app.execution.persistence import (
    FileExecutionStateStore,
)
from app.execution.service import ExecutionService
from app.execution.state import (
    ExecutionSnapshot,
    ExecutionSnapshotStatus,
)
from app.execution.verification import VerificationEngine
from app.planning.executor import PlanExecutor
from app.planning.models import (
    PlanStatus,
    PlanStepStatus,
)
from app.planning.persistence import PlanStore
from app.planning.planner import Planner
from app.tools.executor import ToolExecutor


def create_environment(
    tmp_path,
):
    plan_path = (
        Path(tmp_path) / "plan.json"
    )

    state_path = (
        Path(tmp_path) / "state.json"
    )

    planner = Planner()
    tools = ToolExecutor()

    state_store = FileExecutionStateStore(
        state_path
    )

    execution_service = ExecutionService(
        tool_executor=tools,
        plan_executor=PlanExecutor(planner),
        verification_engine=VerificationEngine(),
        state_store=state_store,
    )

    coordinator = ExecutionCoordinator(
        plan_store=PlanStore(plan_path),
        state_store=state_store,
        execution_service=execution_service,
    )

    return (
        planner,
        tools,
        execution_service,
        coordinator,
    )


def test_coordinator_missing_plan_is_reported(
    tmp_path,
):
    (
        _,
        _,
        _,
        coordinator,
    ) = create_environment(
        tmp_path
    )

    candidate = coordinator.inspect()

    assert (
        candidate.status
        is RecoveryCandidateStatus.MISSING_PLAN
    )
    assert candidate.goal is None


def test_coordinator_missing_state_is_reported(
    tmp_path,
):
    (
        planner,
        _,
        _,
        coordinator,
    ) = create_environment(
        tmp_path
    )

    plan = planner.create_plan(
        "missing state",
        [],
    )

    coordinator.save_plan(plan)

    candidate = coordinator.inspect()

    assert (
        candidate.status
        is RecoveryCandidateStatus.MISSING_STATE
    )
    assert candidate.plan_id == plan.plan_id


def test_coordinator_identifies_running_execution(
    tmp_path,
):
    (
        planner,
        _,
        _,
        coordinator,
    ) = create_environment(
        tmp_path
    )

    from app.planning.models import PlanStep

    plan = planner.create_plan(
        "recover me",
        [
            PlanStep(
                "step",
                metadata={
                    "tool_name": "none",
                    "parameters": {},
                },
            ),
        ],
    )

    coordinator.save_plan(plan)

    coordinator.state_store.save(
        ExecutionSnapshot(
            plan_id=plan.plan_id,
            status=ExecutionSnapshotStatus.RUNNING,
            goal=plan.goal,
            current_step_id=plan.steps[0].step_id,
            current_step_name=plan.steps[0].name,
        )
    )

    candidate = coordinator.inspect()

    assert (
        candidate.status
        is RecoveryCandidateStatus.RECOVERABLE
    )
    assert candidate.snapshot_status == "running"
    assert candidate.metadata["completed_steps"] == 0


def test_coordinator_identifies_completed_execution(
    tmp_path,
):
    (
        planner,
        _,
        _,
        coordinator,
    ) = create_environment(
        tmp_path
    )

    from app.planning.models import PlanStep

    plan = planner.create_plan(
        "completed",
        [
            PlanStep("done"),
        ],
    )

    coordinator.save_plan(plan)

    coordinator.state_store.save(
        ExecutionSnapshot(
            plan_id=plan.plan_id,
            status=ExecutionSnapshotStatus.COMPLETED,
            goal=plan.goal,
            completed_step_ids=[
                plan.steps[0].step_id
            ],
        )
    )

    candidate = coordinator.inspect()

    assert (
        candidate.status
        is RecoveryCandidateStatus.COMPLETED
    )


def test_coordinator_identifies_terminal_failure(
    tmp_path,
):
    (
        planner,
        _,
        _,
        coordinator,
    ) = create_environment(
        tmp_path
    )

    plan = planner.create_plan(
        "failed",
        [],
    )

    coordinator.save_plan(plan)

    coordinator.state_store.save(
        ExecutionSnapshot(
            plan_id=plan.plan_id,
            status=ExecutionSnapshotStatus.FAILED,
            goal=plan.goal,
        )
    )

    candidate = coordinator.inspect()

    assert (
        candidate.status
        is RecoveryCandidateStatus.FAILED
    )


def test_coordinator_can_recover(
    tmp_path,
):
    (
        planner,
        tools,
        _,
        coordinator,
    ) = create_environment(
        tmp_path
    )

    calls = {"count": 0}

    def recoverable():
        calls["count"] += 1
        return "recovered"

    tools.register(
        ToolDefinition(
            name="recoverable",
            description="recoverable",
        ),
        recoverable,
    )

    from app.planning.models import PlanStep

    plan = planner.create_plan(
        "recover",
        [
            PlanStep(
                "step",
                metadata={
                    "tool_name": "recoverable",
                    "parameters": {},
                },
            ),
        ],
    )

    coordinator.save_plan(plan)

    coordinator.state_store.save(
        ExecutionSnapshot(
            plan_id=plan.plan_id,
            status=ExecutionSnapshotStatus.RUNNING,
            goal=plan.goal,
            current_step_id=plan.steps[0].step_id,
            current_step_name=plan.steps[0].name,
        )
    )

    assert coordinator.can_recover()

    result = asyncio.run(
        coordinator.recover()
    )

    assert calls["count"] == 1
    assert result.status is PlanStatus.COMPLETED

    restored = coordinator.plan_store.load()

    assert restored.status is PlanStatus.COMPLETED
    assert (
        restored.steps[0].status
        is PlanStepStatus.COMPLETED
    )


def test_coordinator_recovery_is_idempotent(
    tmp_path,
):
    (
        planner,
        tools,
        _,
        coordinator,
    ) = create_environment(
        tmp_path
    )

    calls = {"count": 0}

    def once():
        calls["count"] += 1
        return "ok"

    tools.register(
        ToolDefinition(
            name="once",
            description="once",
        ),
        once,
    )

    from app.planning.models import PlanStep

    plan = planner.create_plan(
        "idempotent recovery",
        [
            PlanStep(
                "once",
                metadata={
                    "tool_name": "once",
                    "parameters": {},
                },
            ),
        ],
    )

    coordinator.save_plan(plan)

    coordinator.state_store.save(
        ExecutionSnapshot(
            plan_id=plan.plan_id,
            status=ExecutionSnapshotStatus.RUNNING,
            goal=plan.goal,
            current_step_id=plan.steps[0].step_id,
            current_step_name=plan.steps[0].name,
        )
    )

    first = asyncio.run(
        coordinator.recover()
    )

    second = asyncio.run(
        coordinator.recover()
    )

    assert first.status is PlanStatus.COMPLETED
    assert second.status is PlanStatus.COMPLETED
    assert calls["count"] == 1


def test_coordinator_refuses_failed_terminal_state(
    tmp_path,
):
    (
        planner,
        _,
        _,
        coordinator,
    ) = create_environment(
        tmp_path
    )

    plan = planner.create_plan(
        "terminal",
        [],
    )

    coordinator.save_plan(plan)

    coordinator.state_store.save(
        ExecutionSnapshot(
            plan_id=plan.plan_id,
            status=ExecutionSnapshotStatus.FAILED,
            goal=plan.goal,
        )
    )

    assert (
        coordinator.can_recover()
        is False
    )

    with pytest.raises(
        ValueError,
        match="Terminal",
    ):
        asyncio.run(
            coordinator.recover()
        )


def test_coordinator_refuses_cancelled_terminal_state(
    tmp_path,
):
    (
        planner,
        _,
        _,
        coordinator,
    ) = create_environment(
        tmp_path
    )

    plan = planner.create_plan(
        "cancelled",
        [],
    )

    coordinator.save_plan(plan)

    coordinator.state_store.save(
        ExecutionSnapshot(
            plan_id=plan.plan_id,
            status=ExecutionSnapshotStatus.CANCELLED,
            goal=plan.goal,
        )
    )

    assert (
        coordinator.can_recover()
        is False
    )

    with pytest.raises(
        ValueError,
        match="Terminal",
    ):
        asyncio.run(
            coordinator.recover()
        )


def test_coordinator_marks_completed_snapshot_as_completed(
    tmp_path,
):
    (
        planner,
        _,
        _,
        coordinator,
    ) = create_environment(
        tmp_path
    )

    plan = planner.create_plan(
        "already done",
        [],
    )

    coordinator.save_plan(plan)

    coordinator.state_store.save(
        ExecutionSnapshot(
            plan_id=plan.plan_id,
            status=ExecutionSnapshotStatus.COMPLETED,
            goal=plan.goal,
        )
    )

    result = asyncio.run(
        coordinator.recover()
    )

    assert result.status is PlanStatus.COMPLETED
    assert (
        coordinator.plan_store.load().status
        is PlanStatus.COMPLETED
    )
