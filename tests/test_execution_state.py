from __future__ import annotations

import asyncio
from pathlib import Path
from uuid import uuid4

from app.core.models import ToolDefinition
from app.execution.events import (
    ExecutionEvent,
    ExecutionEventType,
)
from app.execution.journal import ExecutionJournal
from app.execution.service import ExecutionService
from app.execution.state import (
    ExecutionSnapshot,
    ExecutionSnapshotStatus,
    ExecutionStateStore,
)
from app.execution.verification import VerificationEngine
from app.planning.executor import PlanExecutor
from app.planning.models import PlanStatus, PlanStep
from app.planning.planner import Planner
from app.tools.executor import ToolExecutor


def test_execution_snapshot_round_trip():
    plan_id = uuid4()
    step_id = uuid4()

    snapshot = ExecutionSnapshot(
        plan_id=plan_id,
        status=ExecutionSnapshotStatus.RUNNING,
        goal="round trip",
        current_step_id=step_id,
        current_step_name="step",
        completed_step_ids=[step_id],
        attempts={"step": 2},
        metadata={"key": "value"},
    )

    restored = ExecutionSnapshot.from_dict(
        snapshot.to_dict()
    )

    assert restored.plan_id == plan_id
    assert restored.status is ExecutionSnapshotStatus.RUNNING
    assert restored.current_step_id == step_id
    assert restored.current_step_name == "step"
    assert restored.completed_step_ids == [step_id]
    assert restored.attempts["step"] == 2
    assert restored.metadata["key"] == "value"


def test_execution_state_store_save_get_delete():
    store = ExecutionStateStore()

    snapshot = ExecutionSnapshot(
        plan_id=uuid4(),
        status=ExecutionSnapshotStatus.RUNNING,
        goal="state",
    )

    store.save(snapshot)

    assert store.get(snapshot.plan_id) is snapshot
    assert snapshot in store.list()

    store.delete(snapshot.plan_id)

    try:
        store.get(snapshot.plan_id)
    except KeyError:
        pass
    else:
        raise AssertionError(
            "Expected KeyError"
        )


def test_execution_journal_appends_and_reads(tmp_path):
    path = Path(tmp_path) / "execution.jsonl"

    journal = ExecutionJournal(path)

    event = ExecutionEvent(
        event_type=ExecutionEventType.PLAN_STARTED,
        plan_id=uuid4(),
        data={"goal": "journal"},
    )

    asyncio.run(
        journal.append(event)
    )

    records = journal.read_all()

    assert len(records) == 1
    assert records[0]["event_type"] == "plan_started"
    assert records[0]["data"]["goal"] == "journal"


def test_execution_journal_clear(tmp_path):
    path = Path(tmp_path) / "execution.jsonl"

    journal = ExecutionJournal(path)

    asyncio.run(
        journal.append(
            ExecutionEvent(
                event_type=ExecutionEventType.PLAN_STARTED,
                plan_id=uuid4(),
            )
        )
    )

    assert journal.read_all()

    journal.clear()

    assert journal.read_all() == []


def test_execution_service_persists_completed_snapshot(tmp_path):
    tools = ToolExecutor()

    tools.register(
        ToolDefinition(
            name="persist",
            description="persist",
        ),
        lambda: "ok",
    )

    planner = Planner()

    plan = planner.create_plan(
        "persistent execution",
        [
            PlanStep(
                "persist",
                metadata={
                    "tool_name": "persist",
                    "parameters": {},
                },
            ),
        ],
    )

    store = ExecutionStateStore()

    journal = ExecutionJournal(
        Path(tmp_path) / "execution.jsonl"
    )

    service = ExecutionService(
        tool_executor=tools,
        plan_executor=PlanExecutor(planner),
        verification_engine=VerificationEngine(),
        state_store=store,
        journal=journal,
    )

    result = asyncio.run(
        service.execute(plan)
    )

    snapshot = store.get(plan.plan_id)

    assert result.status is PlanStatus.COMPLETED
    assert snapshot.status is ExecutionSnapshotStatus.COMPLETED
    assert snapshot.completed_step_ids == [
        plan.steps[0].step_id
    ]
    assert snapshot.attempts["persist"] == 1

    records = journal.read_all()

    assert [
        record["event_type"]
        for record in records
    ] == [
        "plan_started",
        "step_started",
        "step_completed",
        "plan_completed",
    ]


def test_execution_service_persists_failed_snapshot():
    tools = ToolExecutor()

    def fail():
        raise RuntimeError("boom")

    tools.register(
        ToolDefinition(
            name="fail",
            description="fail",
        ),
        fail,
    )

    planner = Planner()

    plan = planner.create_plan(
        "failed execution",
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

    store = ExecutionStateStore()

    service = ExecutionService(
        tool_executor=tools,
        plan_executor=PlanExecutor(planner),
        verification_engine=VerificationEngine(),
        state_store=store,
    )

    result = asyncio.run(
        service.execute(plan)
    )

    snapshot = store.get(plan.plan_id)

    assert result.status is PlanStatus.FAILED
    assert snapshot.status is ExecutionSnapshotStatus.FAILED
    assert snapshot.failed_step_ids == [
        plan.steps[0].step_id
    ]
    assert snapshot.metadata["error"]


def test_state_store_supports_multiple_plans():
    store = ExecutionStateStore()

    first = ExecutionSnapshot(
        plan_id=uuid4(),
        status=ExecutionSnapshotStatus.RUNNING,
        goal="first",
    )

    second = ExecutionSnapshot(
        plan_id=uuid4(),
        status=ExecutionSnapshotStatus.PAUSED,
        goal="second",
    )

    store.save(first)
    store.save(second)

    assert len(store.list()) == 2
