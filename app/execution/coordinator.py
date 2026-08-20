from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable
from uuid import UUID

from app.execution.context import ExecutionContext
from app.execution.persistence import FileExecutionStateStore
from app.execution.recovery import ExecutionRecoveryService
from app.execution.state import (
    ExecutionSnapshotStatus,
)
from app.planning.models import Plan, PlanStatus
from app.planning.persistence import PlanStore


class RecoveryCandidateStatus(str, Enum):
    RECOVERABLE = "recoverable"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    MISSING_PLAN = "missing_plan"
    MISSING_STATE = "missing_state"


@dataclass(slots=True, frozen=True)
class RecoveryCandidate:
    plan_id: UUID
    goal: str | None
    status: RecoveryCandidateStatus
    snapshot_status: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


class ExecutionCoordinator:
    """
    Coordinate persistent plans, persistent execution state, and recovery.

    This class intentionally stays above ExecutionService so that the
    execution engine remains focused on running a plan while this layer
    owns discovery and recovery workflows.
    """

    def __init__(
        self,
        *,
        plan_store: PlanStore,
        state_store: FileExecutionStateStore,
        execution_service,
    ) -> None:
        self._plan_store = plan_store
        self._state_store = state_store
        self._execution_service = execution_service

    @property
    def plan_store(self) -> PlanStore:
        return self._plan_store

    @property
    def state_store(self) -> FileExecutionStateStore:
        return self._state_store

    def save_plan(
        self,
        plan: Plan,
    ) -> Plan:
        return self._plan_store.save(plan)

    def load_plan(self) -> Plan:
        return self._plan_store.load()

    def inspect(
        self,
    ) -> RecoveryCandidate:
        try:
            plan = self._plan_store.load()
        except FileNotFoundError:
            return RecoveryCandidate(
                plan_id=UUID(int=0),
                goal=None,
                status=RecoveryCandidateStatus.MISSING_PLAN,
            )

        try:
            snapshot = self._state_store.get(
                plan.plan_id
            )
        except KeyError:
            return RecoveryCandidate(
                plan_id=plan.plan_id,
                goal=plan.goal,
                status=RecoveryCandidateStatus.MISSING_STATE,
            )

        status_map = {
            ExecutionSnapshotStatus.COMPLETED: (
                RecoveryCandidateStatus.COMPLETED
            ),
            ExecutionSnapshotStatus.FAILED: (
                RecoveryCandidateStatus.FAILED
            ),
            ExecutionSnapshotStatus.CANCELLED: (
                RecoveryCandidateStatus.CANCELLED
            ),
            ExecutionSnapshotStatus.RUNNING: (
                RecoveryCandidateStatus.RECOVERABLE
            ),
            ExecutionSnapshotStatus.PAUSED: (
                RecoveryCandidateStatus.RECOVERABLE
            ),
            ExecutionSnapshotStatus.WAITING_FOR_APPROVAL: (
                RecoveryCandidateStatus.RECOVERABLE
            ),
        }

        return RecoveryCandidate(
            plan_id=plan.plan_id,
            goal=plan.goal,
            status=status_map[
                snapshot.status
            ],
            snapshot_status=snapshot.status.value,
            metadata={
                "completed_steps": len(
                    snapshot.completed_step_ids
                ),
                "failed_steps": len(
                    snapshot.failed_step_ids
                ),
                "attempts": dict(
                    snapshot.attempts
                ),
            },
        )

    async def recover(
        self,
        *,
        cancel_event: asyncio.Event | None = None,
    ) -> Plan:
        candidate = self.inspect()

        if candidate.status is (
            RecoveryCandidateStatus.MISSING_PLAN
        ):
            raise FileNotFoundError(
                "No persisted plan is available for recovery."
            )

        if candidate.status is (
            RecoveryCandidateStatus.MISSING_STATE
        ):
            raise KeyError(
                "No persisted execution state is available for recovery."
            )

        if candidate.status is (
            RecoveryCandidateStatus.COMPLETED
        ):
            plan = self._plan_store.load()
            plan.status = PlanStatus.COMPLETED
            self._plan_store.save(plan)
            return plan

        if candidate.status in {
            RecoveryCandidateStatus.FAILED,
            RecoveryCandidateStatus.CANCELLED,
        }:
            raise ValueError(
                "Terminal execution state cannot be recovered."
            )

        recovery_service = ExecutionRecoveryService(
            plan_store=self._plan_store,
            state_store=self._state_store,
            execution_service=self._execution_service,
        )

        result = await recovery_service.recover(
            cancel_event=cancel_event,
        )

        self._plan_store.save(result)

        return result

    def can_recover(self) -> bool:
        candidate = self.inspect()

        return candidate.status is (
            RecoveryCandidateStatus.RECOVERABLE
        )
