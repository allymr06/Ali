from __future__ import annotations

import asyncio
from typing import Any

from app.core.models import ToolExecutionStatus
from app.execution.context import ExecutionContext
from app.execution.events import (
    ExecutionEvent,
    ExecutionEventBus,
    ExecutionEventType,
)
from app.execution.journal import ExecutionJournal
from app.execution.models import RetryPolicy
from app.execution.replanner import Replanner
from app.execution.state import (
    ExecutionSnapshot,
    ExecutionSnapshotStatus,
    ExecutionStateStore,
)
from app.execution.verification import VerificationEngine
from app.planning.executor import PlanExecutor
from app.planning.models import Plan, PlanStep


class ExecutionObserver:
    """Lifecycle observer for execution events."""

    async def on_step_started(
        self,
        plan: Plan,
        step: PlanStep,
    ) -> None:
        return None

    async def on_step_completed(
        self,
        plan: Plan,
        step: PlanStep,
    ) -> None:
        return None

    async def on_step_failed(
        self,
        plan: Plan,
        step: PlanStep,
        error: str,
    ) -> None:
        return None

    async def on_plan_completed(
        self,
        plan: Plan,
    ) -> None:
        return None

    async def on_plan_failed(
        self,
        plan: Plan,
    ) -> None:
        return None

    async def on_plan_cancelled(
        self,
        plan: Plan,
    ) -> None:
        return None


class ExecutionService:
    """Coordinate planning execution, verification, retry, replanning, and state."""

    def __init__(
        self,
        *,
        tool_executor: Any,
        plan_executor: PlanExecutor,
        verification_engine: VerificationEngine | None = None,
        retry_policy: RetryPolicy | None = None,
        observer: ExecutionObserver | None = None,
        event_bus: ExecutionEventBus | None = None,
        state_store: ExecutionStateStore | None = None,
        journal: ExecutionJournal | None = None,
        replanner: Replanner | None = None,
    ) -> None:
        self._tool_executor = tool_executor
        self._plan_executor = plan_executor
        self._verification_engine = (
            verification_engine
            if verification_engine is not None
            else VerificationEngine()
        )
        self._retry_policy = retry_policy or RetryPolicy()
        self._observer = observer or ExecutionObserver()
        self._event_bus = event_bus or ExecutionEventBus()
        self._state_store = state_store or ExecutionStateStore()
        self._journal = journal
        self._replanner = replanner or Replanner()

    @property
    def event_bus(self) -> ExecutionEventBus:
        return self._event_bus

    @property
    def state_store(self) -> ExecutionStateStore:
        return self._state_store

    async def _publish(
        self,
        event: ExecutionEvent,
    ) -> None:
        await self._event_bus.publish(event)

        if self._journal is not None:
            await self._journal.append(event)

    def _save_snapshot(
        self,
        plan: Plan,
        *,
        status: ExecutionSnapshotStatus | None = None,
        step: PlanStep | None = None,
        error: str | None = None,
    ) -> ExecutionSnapshot:
        try:
            snapshot = self._state_store.get(plan.plan_id)
        except KeyError:
            snapshot = ExecutionSnapshot(
                plan_id=plan.plan_id,
                status=(
                    status
                    or ExecutionSnapshotStatus.RUNNING
                ),
                goal=plan.goal,
            )

        if status is not None:
            snapshot.status = status

        if step is not None:
            snapshot.current_step_id = step.step_id
            snapshot.current_step_name = step.name

            attempts = step.metadata.get("attempts")
            if isinstance(attempts, int):
                snapshot.attempts[step.name] = attempts

        if error is not None:
            snapshot.metadata["error"] = error

        snapshot.touch()
        return self._state_store.save(snapshot)

    async def execute(
        self,
        plan: Plan,
        *,
        execution_context: ExecutionContext | None = None,
        cancel_event: asyncio.Event | None = None,
    ) -> Plan:
        context = (
            execution_context
            if execution_context is not None
            else ExecutionContext(
                plan_id=plan.plan_id,
            )
        )

        context.plan_id = plan.plan_id

        self._plan_executor.start(plan)

        self._save_snapshot(
            plan,
            status=ExecutionSnapshotStatus.RUNNING,
        )

        await self._publish(
            ExecutionEvent(
                event_type=ExecutionEventType.PLAN_STARTED,
                plan_id=plan.plan_id,
                data={
                    "goal": plan.goal,
                },
            )
        )

        while plan.status.value == "running":
            if (
                cancel_event is not None
                and cancel_event.is_set()
            ):
                self._plan_executor.cancel(plan)
                self._save_snapshot(
                    plan,
                    status=ExecutionSnapshotStatus.CANCELLED,
                )

                await self._observer.on_plan_cancelled(
                    plan
                )

                await self._publish(
                    ExecutionEvent(
                        event_type=ExecutionEventType.PLAN_CANCELLED,
                        plan_id=plan.plan_id,
                    )
                )

                break

            step = self._plan_executor.next_step(plan)

            if step is None:
                break

            context.for_step(
                step_id=step.step_id,
                step_name=step.name,
            )

            self._save_snapshot(
                plan,
                status=ExecutionSnapshotStatus.RUNNING,
                step=step,
            )

            await self._observer.on_step_started(
                plan,
                step,
            )

            await self._publish(
                ExecutionEvent(
                    event_type=ExecutionEventType.STEP_STARTED,
                    plan_id=plan.plan_id,
                    step_id=step.step_id,
                    step_name=step.name,
                )
            )

            previous_status = plan.status

            await self._execute_step(
                plan,
                step,
                execution_context=context,
            )

            if (
                plan.status.value == "failed"
                and self._replanner.can_replan(
                    int(
                        context.metadata.get(
                            "replan_count",
                            0,
                        )
                    )
                )
            ):
                replan_count = int(
                    context.metadata.get(
                        "replan_count",
                        0,
                    )
                )

                error = str(
                    step.metadata.get(
                        "tool_error",
                        "Execution failed.",
                    )
                )

                replacement = self._replanner.replan(
                    plan,
                    step,
                    error,
                )

                if replacement is not None:
                    context.metadata["replan_count"] = (
                        replan_count + 1
                    )

                    plan = replacement
                    context.plan_id = plan.plan_id

                    self._plan_executor.start(plan)

                    self._save_snapshot(
                        plan,
                        status=ExecutionSnapshotStatus.RUNNING,
                    )

                    continue

            if plan.status is not previous_status:
                continue

        if plan.status.value == "completed":
            self._save_snapshot(
                plan,
                status=ExecutionSnapshotStatus.COMPLETED,
            )

            await self._observer.on_plan_completed(
                plan
            )

            await self._publish(
                ExecutionEvent(
                    event_type=ExecutionEventType.PLAN_COMPLETED,
                    plan_id=plan.plan_id,
                    data={
                        "progress": plan.progress,
                    },
                )
            )

        elif plan.status.value == "failed":
            self._save_snapshot(
                plan,
                status=ExecutionSnapshotStatus.FAILED,
            )

            await self._observer.on_plan_failed(
                plan
            )

            await self._publish(
                ExecutionEvent(
                    event_type=ExecutionEventType.PLAN_FAILED,
                    plan_id=plan.plan_id,
                )
            )

        elif plan.status.value == "cancelled":
            self._save_snapshot(
                plan,
                status=ExecutionSnapshotStatus.CANCELLED,
            )

            await self._observer.on_plan_cancelled(
                plan
            )

            await self._publish(
                ExecutionEvent(
                    event_type=ExecutionEventType.PLAN_CANCELLED,
                    plan_id=plan.plan_id,
                )
            )

        return plan

    async def _execute_step(
        self,
        plan: Plan,
        step: PlanStep,
        *,
        execution_context: ExecutionContext,
    ) -> None:
        tool_name = step.metadata.get(
            "tool_name"
        )

        if (
            not isinstance(tool_name, str)
            or not tool_name.strip()
        ):
            error = "Invalid tool_name."

            self._fail(
                plan,
                step,
                error,
            )

            await self._observer.on_step_failed(
                plan,
                step,
                error,
            )

            await self._publish(
                ExecutionEvent(
                    event_type=ExecutionEventType.STEP_FAILED,
                    plan_id=plan.plan_id,
                    step_id=step.step_id,
                    step_name=step.name,
                    data={
                        "error": error,
                    },
                )
            )

            return

        parameters = step.metadata.get(
            "parameters",
            {},
        )

        if not isinstance(parameters, dict):
            error = "Parameters must be a dictionary."

            self._fail(
                plan,
                step,
                error,
            )

            await self._observer.on_step_failed(
                plan,
                step,
                error,
            )

            await self._publish(
                ExecutionEvent(
                    event_type=ExecutionEventType.STEP_FAILED,
                    plan_id=plan.plan_id,
                    step_id=step.step_id,
                    step_name=step.name,
                    data={
                        "error": error,
                    },
                )
            )

            return

        verifier = step.metadata.get(
            "verifier"
        )

        if (
            verifier is not None
            and not callable(verifier)
        ):
            error = "Verifier must be callable."

            self._fail(
                plan,
                step,
                error,
            )

            await self._observer.on_step_failed(
                plan,
                step,
                error,
            )

            await self._publish(
                ExecutionEvent(
                    event_type=ExecutionEventType.STEP_FAILED,
                    plan_id=plan.plan_id,
                    step_id=step.step_id,
                    step_name=step.name,
                    data={
                        "error": error,
                    },
                )
            )

            return

        last_error = ""

        for attempt in range(
            1,
            self._retry_policy.max_attempts + 1,
        ):
            if (
                attempt > 1
                and self._retry_policy.backoff_seconds > 0
            ):
                await asyncio.sleep(
                    self._retry_policy.backoff_seconds
                    * (2 ** (attempt - 2))
                )

            try:
                result = await self._tool_executor.execute(
                    tool_name.strip(),
                    parameters=parameters,
                )
            except Exception as exc:
                last_error = (
                    f"Tool executor exception: {exc}"
                )

                step.metadata["attempts"] = attempt

                continue

            step.metadata["attempts"] = attempt
            step.metadata["last_status"] = (
                result.status.value
            )
            step.metadata["execution_id"] = str(
                result.execution_id
            )

            execution_context.metadata[
                "last_execution_id"
            ] = str(result.execution_id)

            execution_context.metadata[
                "last_tool_name"
            ] = tool_name

            if attempt > 1:
                await self._publish(
                    ExecutionEvent(
                        event_type=ExecutionEventType.STEP_RETRYING,
                        plan_id=plan.plan_id,
                        step_id=step.step_id,
                        step_name=step.name,
                        execution_id=result.execution_id,
                        attempt=attempt,
                        data={
                            "status": result.status.value,
                        },
                    )
                )

            verification = (
                self._verification_engine.verify(
                    result,
                    verifier=verifier,
                )
            )

            if verification.passed:
                step.metadata["tool_result"] = (
                    result.data
                )
                step.metadata["verified"] = True
                step.metadata[
                    "verification_reason"
                ] = verification.reason

                self._plan_executor.complete_step(
                    plan,
                    step,
                )

                snapshot = self._save_snapshot(
                    plan,
                    status=ExecutionSnapshotStatus.RUNNING,
                    step=step,
                )

                if step.step_id not in (
                    snapshot.completed_step_ids
                ):
                    snapshot.completed_step_ids.append(
                        step.step_id
                    )
                    self._state_store.save(
                        snapshot
                    )

                await self._observer.on_step_completed(
                    plan,
                    step,
                )

                await self._publish(
                    ExecutionEvent(
                        event_type=ExecutionEventType.STEP_COMPLETED,
                        plan_id=plan.plan_id,
                        step_id=step.step_id,
                        step_name=step.name,
                        execution_id=result.execution_id,
                        attempt=attempt,
                        data={
                            "verified": True,
                        },
                    )
                )

                return

            last_error = (
                verification.reason
                or result.error
                or result.message
                or "Verification failed."
            )

            if result.status in (
                ToolExecutionStatus.BLOCKED,
                ToolExecutionStatus.CANCELLED,
            ):
                break

        attempts = step.metadata.get(
            "attempts"
        )

        if not isinstance(attempts, int):
            attempts = (
                self._retry_policy.max_attempts
            )

        step.metadata["attempts"] = attempts
        step.metadata["verified"] = False
        step.metadata["tool_error"] = last_error

        snapshot = self._save_snapshot(
            plan,
            status=ExecutionSnapshotStatus.FAILED,
            step=step,
            error=last_error,
        )

        if step.step_id not in (
            snapshot.failed_step_ids
        ):
            snapshot.failed_step_ids.append(
                step.step_id
            )
            self._state_store.save(
                snapshot
            )

        self._fail(
            plan,
            step,
            last_error,
        )

        await self._observer.on_step_failed(
            plan,
            step,
            last_error,
        )

        await self._publish(
            ExecutionEvent(
                event_type=ExecutionEventType.STEP_FAILED,
                plan_id=plan.plan_id,
                step_id=step.step_id,
                step_name=step.name,
                attempt=attempts,
                data={
                    "error": last_error,
                },
            )
        )

    def _fail(
        self,
        plan: Plan,
        step: PlanStep,
        error: str,
    ) -> None:
        step.metadata["verified"] = False
        step.metadata["tool_error"] = error

        self._plan_executor.fail_step(
            plan,
            step,
        )
