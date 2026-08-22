from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path
from threading import RLock
from uuid import UUID

from app.core.models import Task, TaskStatus
from app.execution.coordinator import ExecutionCoordinator, RecoveryCandidate
from app.execution.models import ExecutionLimits, RetryPolicy
from app.execution.persistence import FileExecutionStateStore
from app.execution.recovery import ExecutionRecoveryService
from app.execution.service import ExecutionService
from app.execution.task_service import TaskExecutionService
from app.execution.verification import VerificationEngine
from app.planning.executor import PlanExecutor
from app.planning.models import Plan
from app.planning.persistence import PlanStore
from app.planning.planner import Planner
from app.tasks.manager import TaskManager


@dataclass(slots=True)
class _ActiveTaskControl:
    cancel_event: asyncio.Event
    pause_event: asyncio.Event


class DurableTaskRuntime:
    """Persist and safely resume multiple long-running task executions."""

    def __init__(
        self,
        directory: str | Path,
        *,
        task_manager: TaskManager,
        tool_executor,
        limits: ExecutionLimits | None = None,
        retry_policy: RetryPolicy | None = None,
    ) -> None:
        self.directory = Path(directory).expanduser().resolve()
        self.directory.mkdir(parents=True, exist_ok=True)
        self._task_manager = task_manager
        self._tool_executor = tool_executor
        self._limits = limits or ExecutionLimits()
        self._retry_policy = retry_policy or RetryPolicy()
        self._active: dict[UUID, _ActiveTaskControl] = {}
        self._lock = RLock()

    def _task_directory(self, task_id: UUID) -> Path:
        path = (self.directory / str(task_id)).resolve()
        if path.parent != self.directory:
            raise ValueError("Task runtime path escaped its configured directory.")
        path.mkdir(parents=True, exist_ok=True)
        return path

    def _components(
        self, task_id: UUID
    ) -> tuple[PlanStore, FileExecutionStateStore, ExecutionService]:
        task_directory = self._task_directory(task_id)
        plan_store = PlanStore(task_directory / "plan.json")
        state_store = FileExecutionStateStore(task_directory / "state.json")
        service = ExecutionService(
            tool_executor=self._tool_executor,
            plan_executor=PlanExecutor(Planner()),
            verification_engine=VerificationEngine(),
            retry_policy=self._retry_policy,
            limits=self._limits,
            state_store=state_store,
        )
        return plan_store, state_store, service

    def _admit(
        self,
        task_id: UUID,
        *,
        cancel_event: asyncio.Event | None = None,
    ) -> _ActiveTaskControl:
        with self._lock:
            if task_id in self._active:
                raise RuntimeError(f"Task is already active: {task_id}")
            control = _ActiveTaskControl(
                cancel_event or asyncio.Event(),
                asyncio.Event(),
            )
            self._active[task_id] = control
            return control

    def _release(self, task_id: UUID) -> None:
        with self._lock:
            self._active.pop(task_id, None)

    async def execute_new(
        self,
        task_id: UUID,
        plan: Plan,
        *,
        request_id: UUID | None = None,
        conversation_id: UUID | None = None,
        cancel_event: asyncio.Event | None = None,
    ) -> Task:
        task = self._task_manager.get(task_id)
        if task.status is not TaskStatus.QUEUED:
            raise ValueError(f"Cannot execute task from state {task.status.value}.")
        plan_store, _, execution_service = self._components(task_id)
        try:
            plan_store.save(plan)
        except Exception:
            self._task_manager.fail(
                task_id,
                "Plan could not be persisted safely.",
            )
            raise
        self._task_manager.update_metadata(
            task_id,
            {
                "plan_id": str(plan.plan_id),
                "runtime_directory": str(self._task_directory(task_id)),
                "request_id": str(request_id) if request_id else None,
                "conversation_id": (
                    str(conversation_id) if conversation_id else None
                ),
            },
        )
        control = self._admit(task_id, cancel_event=cancel_event)
        tracked = TaskExecutionService(
            task_manager=self._task_manager,
            execution_service=execution_service,
            retry_policy=self._retry_policy,
            limits=self._limits,
        )
        try:
            result = await tracked.execute(
                task_id,
                plan,
                cancel_event=control.cancel_event,
                pause_event=control.pause_event,
                request_id=request_id,
                conversation_id=conversation_id,
            )
            persisted_plan = plan_store.save(plan)
            self._task_manager.update_metadata(
                task_id, {"plan_id": str(persisted_plan.plan_id)}
            )
            return result
        finally:
            self._release(task_id)

    def inspect(self, task_id: UUID) -> RecoveryCandidate:
        plan_store, state_store, execution_service = self._components(task_id)
        return ExecutionCoordinator(
            plan_store=plan_store,
            state_store=state_store,
            execution_service=execution_service,
        ).inspect()

    def recoverable(self) -> tuple[Task, ...]:
        candidates: list[Task] = []
        for task in self._task_manager.recoverable():
            try:
                candidate = self.inspect(task.task_id)
            except (OSError, ValueError):
                continue
            if candidate.status.value == "recoverable":
                candidates.append(task)
        return tuple(candidates)

    async def resume(self, task_id: UUID) -> Task:
        task = self._task_manager.get(task_id)
        if task.status is not TaskStatus.PAUSED:
            raise ValueError(f"Cannot resume task from state {task.status.value}.")
        plan_store, state_store, execution_service = self._components(task_id)
        control = self._admit(task_id)
        self._task_manager.resume(task_id)
        recovery = ExecutionRecoveryService(
            plan_store=plan_store,
            state_store=state_store,
            execution_service=execution_service,
        )
        try:
            plan = await recovery.recover(
                cancel_event=control.cancel_event,
                pause_event=control.pause_event,
            )
            plan_store.save(plan)
            return self._task_manager.reconcile_plan(task_id, plan)
        except Exception:
            current = self._task_manager.get(task_id)
            if current.status is TaskStatus.RUNNING:
                self._task_manager.pause(task_id)
            raise
        finally:
            self._release(task_id)

    def request_pause(self, task_id: UUID) -> None:
        with self._lock:
            try:
                self._active[task_id].pause_event.set()
            except KeyError as exc:
                raise ValueError(f"Task is not actively executing: {task_id}") from exc

    def request_cancel(self, task_id: UUID) -> None:
        with self._lock:
            try:
                self._active[task_id].cancel_event.set()
            except KeyError as exc:
                raise ValueError(f"Task is not actively executing: {task_id}") from exc

    def is_active(self, task_id: UUID) -> bool:
        with self._lock:
            return task_id in self._active
