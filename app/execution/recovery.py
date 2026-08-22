from __future__ import annotations

import asyncio
from enum import Enum
from pathlib import Path
from typing import Any
from uuid import UUID

from app.execution.context import ExecutionContext
from app.execution.journal import ExecutionJournal
from app.execution.persistence import FileExecutionStateStore
from app.execution.service import ExecutionService
from app.execution.state import (
    ExecutionSnapshotStatus,
)
from app.planning.models import (
    Plan,
    PlanStatus,
    PlanStepStatus,
)
from app.planning.persistence import PlanStore


class RecoveryStatus(str, Enum):
    RESUMED = "resumed"
    ALREADY_COMPLETED = "already_completed"
    NOT_RECOVERABLE = "not_recoverable"


class ExecutionRecoveryService:
    """
    Restore executable plans from persisted plan + execution state.

    Recovery deliberately retries an interrupted running step rather
    than marking it completed without a verified result.
    """

    def __init__(
        self,
        *,
        plan_store: PlanStore,
        state_store: FileExecutionStateStore,
        execution_service: ExecutionService,
    ) -> None:
        self._plan_store = plan_store
        self._state_store = state_store
        self._execution_service = execution_service

    def load_recoverable_plan(
        self,
    ) -> tuple[
        Plan,
        Any,
    ]:
        plan = self._plan_store.load()
        snapshot = self._state_store.get(
            plan.plan_id
        )

        return plan, snapshot

    def prepare(
        self,
        plan: Plan,
        snapshot,
    ) -> Plan:
        status = snapshot.status

        if status is ExecutionSnapshotStatus.COMPLETED:
            plan.status = PlanStatus.COMPLETED

            for step in plan.steps:
                if step.step_id in (
                    snapshot.completed_step_ids
                ):
                    step.status = PlanStepStatus.COMPLETED

            return plan

        if status in {
            ExecutionSnapshotStatus.FAILED,
            ExecutionSnapshotStatus.CANCELLED,
        }:
            raise ValueError(
                "Execution snapshot is terminal and cannot be resumed."
            )

        if status not in {
            ExecutionSnapshotStatus.RUNNING,
            ExecutionSnapshotStatus.PAUSED,
            ExecutionSnapshotStatus.WAITING_FOR_APPROVAL,
        }:
            raise ValueError(
                f"Unsupported recovery state: {status.value}"
            )

        completed = set(
            snapshot.completed_step_ids
        )

        failed = set(
            snapshot.failed_step_ids
        )

        for step in plan.steps:
            if step.step_id in completed:
                step.status = (
                    PlanStepStatus.COMPLETED
                )
                continue

            if step.step_id in failed:
                raise ValueError(
                    "Cannot resume a plan containing "
                    "a permanently failed step."
                )

            # RUNNING at the time of interruption means that
            # no verified completion was persisted.
            step.status = PlanStepStatus.PENDING

        plan.status = PlanStatus.READY

        return plan

    async def recover(
        self,
        *,
        cancel_event: asyncio.Event | None = None,
        pause_event: asyncio.Event | None = None,
    ) -> Plan:
        plan, snapshot = self.load_recoverable_plan()

        prepared = self.prepare(
            plan,
            snapshot,
        )

        if prepared.status is PlanStatus.COMPLETED:
            return prepared

        context = ExecutionContext(
            task_id=(
                UUID(str(snapshot.metadata["task_id"]))
                if snapshot.metadata
                and snapshot.metadata.get("task_id")
                else None
            ),
            plan_id=prepared.plan_id,
            metadata={
                "recovered": True,
                "original_snapshot_id": str(
                    snapshot.snapshot_id
                ),
            },
        )

        result = await self._execution_service.execute(
            prepared,
            execution_context=context,
            cancel_event=cancel_event,
            pause_event=pause_event,
        )

        self._plan_store.save(result)

        return result
