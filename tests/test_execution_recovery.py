from __future__ import annotations

import asyncio
from pathlib import Path
from uuid import uuid4

import pytest

from app.core.models import ToolDefinition
from app.execution.context import ExecutionContext
from app.execution.persistence import FileExecutionStateStore
from app.execution.recovery import (
    ExecutionRecoveryService,
    RecoveryStatus,
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
from app.planning.persistence import (
    PlanPersistenceError,
    PlanStore,
)
from app.planning.planner import Planner
from app.tools.executor import ToolExecutor


def create_recovery_environment(
    tmp_path,
):
    plan_path = (
        Path(tmp_path)
        / "plan.json"
    )

    state_path = (
        Path(tmp_path)
        / "state.json"
    )

    plan_store = PlanStore(plan_path)

    state_store = FileExecutionStateStore(
        state_path
    )

    planner = Planner()
    tools = ToolExecutor()

    service = ExecutionService(
        tool_executor=tools,
        plan_executor=PlanExecutor(planner),
        verification_engine=VerificationEngine(),
        state_store=state_store,
    )

    recovery = ExecutionRecoveryService(
        plan_store=plan_store,
        state_store=state_store,
        execution_service=service,
    )

    return (
        planner,
        tools,
        service,
        recovery,
        plan_store,
        state_store,
    )


def test_plan_store_round_trip(tmp_path):
    (
        planner,
        _,
        _,
        _,
        plan_store,
        _,
    ) = create_recovery_environment(
        tmp_path
    )

    from app.planning.models import PlanStep

    plan = planner.create_plan(
        "round trip",
        [
            PlanStep(
                "step",
                metadata={
                    "tool_name": "test",
                    "parameters": {
                        "value": 42,
                    },
                },
            )
        ],
    )

    plan_store.save(plan)

    restored = plan_store.load()

    assert restored.plan_id == plan.plan_id
    assert restored.goal == plan.goal
    assert restored.steps[0].step_id == (
        plan.steps[0].step_id
    )
    assert restored.steps[0].metadata == (
        plan.steps[0].metadata
    )


def test_plan_store_preserves_uuid_metadata(tmp_path):
    (
        planner,
        _,
        _,
        _,
        plan_store,
        _,
    ) = create_recovery_environment(
        tmp_path
    )

    from app.planning.models import PlanStep

    value = uuid4()

    plan = planner.create_plan(
        "uuid metadata",
        [
            PlanStep(
                "step",
                metadata={
                    "tool_name": "test",
                    "parameters": {},
                    "reference": value,
                },
            )
        ],
    )

    plan_store.save(plan)

    restored = plan_store.load()

    assert (
        restored.steps[0].metadata["reference"]
        == value
    )


def test_plan_store_rejects_callable_metadata(tmp_path):
    (
        planner,
        _,
        _,
        _,
        plan_store,
        _,
    ) = create_recovery_environment(
        tmp_path
    )

    from app.planning.models import PlanStep

    plan = planner.create_plan(
        "invalid persistence",
        [
            PlanStep(
                "step",
                metadata={
                    "tool_name": "test",
                    "parameters": {},
                    "verifier": lambda _: True,
                },
            )
        ],
    )

    with pytest.raises(
        PlanPersistenceError,
        match="cannot be persisted",
    ):
        plan_store.save(plan)


def test_recovery_resets_interrupted_step_to_pending(
    tmp_path,
):
    (
        planner,
        _,
        _,
        recovery,
        plan_store,
        state_store,
    ) = create_recovery_environment(
        tmp_path
    )

    from app.planning.models import PlanStep

    plan = planner.create_plan(
        "resume",
        [
            PlanStep(
                "interrupted",
                metadata={
                    "tool_name": "test",
                    "parameters": {},
                },
            ),
        ],
    )

    snapshot = ExecutionSnapshot(
        plan_id=plan.plan_id,
        status=ExecutionSnapshotStatus.RUNNING,
        goal=plan.goal,
        current_step_id=plan.steps[0].step_id,
        current_step_name=plan.steps[0].name,
    )

    plan_store.save(plan)
    state_store.save(snapshot)

    restored_plan, restored_snapshot = (
        recovery.load_recoverable_plan()
    )

    prepared = recovery.prepare(
        restored_plan,
        restored_snapshot,
    )

    assert prepared.status is PlanStatus.READY
    assert (
        prepared.steps[0].status
        is PlanStepStatus.PENDING
    )


def test_recovery_preserves_completed_steps(
    tmp_path,
):
    (
        planner,
        _,
        _,
        recovery,
        plan_store,
        state_store,
    ) = create_recovery_environment(
        tmp_path
    )

    from app.planning.models import PlanStep

    first = PlanStep(
        "first",
        metadata={
            "tool_name": "first",
            "parameters": {},
        },
    )

    second = PlanStep(
        "second",
        dependencies=["first"],
        metadata={
            "tool_name": "second",
            "parameters": {},
        },
    )

    plan = planner.create_plan(
        "partial",
        [
            first,
            second,
        ],
    )

    snapshot = ExecutionSnapshot(
        plan_id=plan.plan_id,
        status=ExecutionSnapshotStatus.RUNNING,
        goal=plan.goal,
        current_step_id=second.step_id,
        current_step_name=second.name,
        completed_step_ids=[
            first.step_id
        ],
    )

    plan_store.save(plan)
    state_store.save(snapshot)

    restored_plan, restored_snapshot = (
        recovery.load_recoverable_plan()
    )

    prepared = recovery.prepare(
        restored_plan,
        restored_snapshot,
    )

    assert (
        prepared.steps[0].status
        is PlanStepStatus.COMPLETED
    )

    assert (
        prepared.steps[1].status
        is PlanStepStatus.PENDING
    )


def test_recovery_returns_completed_plan_without_execution(
    tmp_path,
):
    (
        planner,
        _,
        _,
        recovery,
        plan_store,
        state_store,
    ) = create_recovery_environment(
        tmp_path
    )

    from app.planning.models import PlanStep

    plan = planner.create_plan(
        "already complete",
        [
            PlanStep(
                "done",
                metadata={
                    "tool_name": "does_not_exist",
                    "parameters": {},
                },
            ),
        ],
    )

    snapshot = ExecutionSnapshot(
        plan_id=plan.plan_id,
        status=ExecutionSnapshotStatus.COMPLETED,
        goal=plan.goal,
        completed_step_ids=[
            plan.steps[0].step_id
        ],
    )

    plan_store.save(plan)
    state_store.save(snapshot)

    result = asyncio.run(
        recovery.recover()
    )

    assert result.status is PlanStatus.COMPLETED
    assert (
        result.steps[0].status
        is PlanStepStatus.COMPLETED
    )


def test_recovery_refuses_terminal_failure(
    tmp_path,
):
    (
        planner,
        _,
        _,
        recovery,
        plan_store,
        state_store,
    ) = create_recovery_environment(
        tmp_path
    )

    plan = planner.create_plan(
        "failed",
        [],
    )

    snapshot = ExecutionSnapshot(
        plan_id=plan.plan_id,
        status=ExecutionSnapshotStatus.FAILED,
        goal=plan.goal,
    )

    plan_store.save(plan)
    state_store.save(snapshot)

    with pytest.raises(
        ValueError,
        match="terminal",
    ):
        asyncio.run(
            recovery.recover()
        )


def test_recovery_refuses_cancelled_execution(
    tmp_path,
):
    (
        planner,
        _,
        _,
        recovery,
        plan_store,
        state_store,
    ) = create_recovery_environment(
        tmp_path
    )

    plan = planner.create_plan(
        "cancelled",
        [],
    )

    snapshot = ExecutionSnapshot(
        plan_id=plan.plan_id,
        status=ExecutionSnapshotStatus.CANCELLED,
        goal=plan.goal,
    )

    plan_store.save(plan)
    state_store.save(snapshot)

    with pytest.raises(
        ValueError,
        match="terminal",
    ):
        recovery.prepare(
            plan,
            snapshot,
        )


def test_recovery_executes_interrupted_step(
    tmp_path,
):
    (
        planner,
        tools,
        _,
        recovery,
        plan_store,
        state_store,
    ) = create_recovery_environment(
        tmp_path
    )

    calls = {"count": 0}

    def recoverable_tool():
        calls["count"] += 1
        return "recovered"

    tools.register(
        ToolDefinition(
            name="recoverable",
            description="recoverable",
        ),
        recoverable_tool,
    )

    from app.planning.models import PlanStep

    plan = planner.create_plan(
        "recover execution",
        [
            PlanStep(
                "recover",
                metadata={
                    "tool_name": "recoverable",
                    "parameters": {},
                },
            ),
        ],
    )

    snapshot = ExecutionSnapshot(
        plan_id=plan.plan_id,
        status=ExecutionSnapshotStatus.RUNNING,
        goal=plan.goal,
        current_step_id=plan.steps[0].step_id,
        current_step_name=plan.steps[0].name,
    )

    plan_store.save(plan)
    state_store.save(snapshot)

    result = asyncio.run(
        recovery.recover()
    )

    assert calls["count"] == 1
    assert result.status is PlanStatus.COMPLETED
    assert (
        result.steps[0].status
        is PlanStepStatus.COMPLETED
    )


def test_recovery_does_not_repeat_completed_step(
    tmp_path,
):
    (
        planner,
        tools,
        _,
        recovery,
        plan_store,
        state_store,
    ) = create_recovery_environment(
        tmp_path
    )

    calls = {
        "first": 0,
        "second": 0,
    }

    tools.register(
        ToolDefinition(
            name="first",
            description="first",
        ),
        lambda: calls.__setitem__(
            "first",
            calls["first"] + 1,
        ) or "first",
    )

    tools.register(
        ToolDefinition(
            name="second",
            description="second",
        ),
        lambda: calls.__setitem__(
            "second",
            calls["second"] + 1,
        ) or "second",
    )

    from app.planning.models import PlanStep

    first = PlanStep(
        "first",
        metadata={
            "tool_name": "first",
            "parameters": {},
        },
    )

    second = PlanStep(
        "second",
        dependencies=["first"],
        metadata={
            "tool_name": "second",
            "parameters": {},
        },
    )

    plan = planner.create_plan(
        "partial recovery",
        [
            first,
            second,
        ],
    )

    snapshot = ExecutionSnapshot(
        plan_id=plan.plan_id,
        status=ExecutionSnapshotStatus.RUNNING,
        goal=plan.goal,
        current_step_id=second.step_id,
        current_step_name=second.name,
        completed_step_ids=[
            first.step_id
        ],
    )

    plan_store.save(plan)
    state_store.save(snapshot)

    result = asyncio.run(
        recovery.recover()
    )

    assert result.status is PlanStatus.COMPLETED
    assert calls["first"] == 0
    assert calls["second"] == 1


def test_recovery_updates_persisted_plan_after_success(
    tmp_path,
):
    (
        planner,
        tools,
        _,
        recovery,
        plan_store,
        state_store,
    ) = create_recovery_environment(
        tmp_path
    )

    tools.register(
        ToolDefinition(
            name="persisted",
            description="persisted",
        ),
        lambda: "done",
    )

    from app.planning.models import PlanStep

    plan = planner.create_plan(
        "persisted result",
        [
            PlanStep(
                "persisted",
                metadata={
                    "tool_name": "persisted",
                    "parameters": {},
                },
            ),
        ],
    )

    state_store.save(
        ExecutionSnapshot(
            plan_id=plan.plan_id,
            status=ExecutionSnapshotStatus.RUNNING,
            goal=plan.goal,
            current_step_id=plan.steps[0].step_id,
            current_step_name=plan.steps[0].name,
        )
    )

    plan_store.save(plan)

    result = asyncio.run(
        recovery.recover()
    )

    restored = plan_store.load()

    assert result.status is PlanStatus.COMPLETED
    assert restored.status is PlanStatus.COMPLETED
    assert (
        restored.steps[0].status
        is PlanStepStatus.COMPLETED
    )


def test_recovery_is_idempotent_after_completion(
    tmp_path,
):
    (
        planner,
        tools,
        _,
        recovery,
        plan_store,
        state_store,
    ) = create_recovery_environment(
        tmp_path
    )

    calls = {"count": 0}

    tools.register(
        ToolDefinition(
            name="once",
            description="once",
        ),
        lambda: calls.__setitem__(
            "count",
            calls["count"] + 1,
        ) or "once",
    )

    from app.planning.models import PlanStep

    plan = planner.create_plan(
        "idempotent",
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

    state_store.save(
        ExecutionSnapshot(
            plan_id=plan.plan_id,
            status=ExecutionSnapshotStatus.RUNNING,
            goal=plan.goal,
            current_step_id=plan.steps[0].step_id,
            current_step_name=plan.steps[0].name,
        )
    )

    plan_store.save(plan)

    first = asyncio.run(
        recovery.recover()
    )

    assert first.status is PlanStatus.COMPLETED
    assert calls["count"] == 1

    second = asyncio.run(
        recovery.recover()
    )

    assert second.status is PlanStatus.COMPLETED
    assert calls["count"] == 1
