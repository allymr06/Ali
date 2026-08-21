from __future__ import annotations

import asyncio
import hmac
from typing import Any

from app.core.models import ToolExecutionStatus
from app.core.time import utc_now
from app.execution.context import ExecutionContext
from app.execution.events import (
    ExecutionEvent,
    ExecutionEventBus,
    ExecutionEventType,
)
from app.execution.journal import ExecutionJournal
from app.execution.models import ExecutionLimits, RetryPolicy
from app.execution.replanner import Replanner
from app.execution.state import (
    ExecutionSnapshot,
    ExecutionSnapshotStatus,
    ExecutionStateStore,
)
from app.execution.verification import VerificationEngine
from app.planning.executor import PlanExecutor
from app.planning.models import Plan, PlanStatus, PlanStep
from app.security.approval import ApprovalGrant, approval_binding_digest


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
        limits: ExecutionLimits | None = None,
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
        self._limits = limits or ExecutionLimits()

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
        metadata: dict[str, Any] | None = None,
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

        if metadata:
            snapshot.metadata.update(metadata)

        snapshot.touch()
        return self._state_store.save(snapshot)

    async def execute(
        self,
        plan: Plan,
        *,
        execution_context: ExecutionContext | None = None,
        cancel_event: asyncio.Event | None = None,
        observer: ExecutionObserver | None = None,
        limits: ExecutionLimits | None = None,
    ) -> Plan:
        context = (
            execution_context
            if execution_context is not None
            else ExecutionContext(
                plan_id=plan.plan_id,
            )
        )

        context.plan_id = plan.plan_id
        context.limits = limits or self._limits
        context.usage.start()
        active_observer = observer or self._observer

        if len(plan.steps) > context.limits.max_plan_steps:
            error = (
                "Plan step budget exceeded: "
                f"{len(plan.steps)} > {context.limits.max_plan_steps}."
            )
            plan.status = PlanStatus.FAILED
            plan.metadata["execution_error"] = error
            plan.metadata["execution_outcome"] = "budget_exhausted"
            plan.metadata["execution_usage"] = context.usage.to_dict()
            self._save_snapshot(
                plan,
                status=ExecutionSnapshotStatus.FAILED,
                error=error,
                metadata={"usage": context.usage.to_dict()},
            )
            await active_observer.on_plan_failed(plan)
            await self._publish(
                ExecutionEvent(
                    event_type=ExecutionEventType.PLAN_FAILED,
                    plan_id=plan.plan_id,
                    data={"error": error, "reason": "budget_exhausted"},
                )
            )
            return plan

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
                break

            step = self._plan_executor.next_step(plan)

            if step is None:
                break

            context.for_step(
                step_id=step.step_id,
                step_name=step.name,
            )
            context.usage.plan_steps += 1

            self._save_snapshot(
                plan,
                status=ExecutionSnapshotStatus.RUNNING,
                step=step,
            )

            await active_observer.on_step_started(
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
                cancel_event=cancel_event,
                observer=active_observer,
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
            plan.metadata["execution_outcome"] = "completed"
            plan.metadata["execution_usage"] = context.usage.to_dict()
            self._save_snapshot(
                plan,
                status=ExecutionSnapshotStatus.COMPLETED,
                metadata={"usage": context.usage.to_dict()},
            )

            await active_observer.on_plan_completed(
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
            plan.metadata.setdefault("execution_outcome", "failed")
            plan.metadata["execution_usage"] = context.usage.to_dict()
            self._save_snapshot(
                plan,
                status=ExecutionSnapshotStatus.FAILED,
                metadata={"usage": context.usage.to_dict()},
            )

            await active_observer.on_plan_failed(
                plan
            )

            await self._publish(
                ExecutionEvent(
                    event_type=ExecutionEventType.PLAN_FAILED,
                    plan_id=plan.plan_id,
                )
            )

        elif plan.status.value == "cancelled":
            plan.metadata["execution_outcome"] = "cancelled"
            plan.metadata["execution_usage"] = context.usage.to_dict()
            self._save_snapshot(
                plan,
                status=ExecutionSnapshotStatus.CANCELLED,
                metadata={"usage": context.usage.to_dict()},
            )

            await active_observer.on_plan_cancelled(
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
        cancel_event: asyncio.Event | None,
        observer: ExecutionObserver,
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

            await observer.on_step_failed(
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

            await observer.on_step_failed(
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

            await observer.on_step_failed(
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

        approval_grant = step.metadata.pop("_approval_grant", None)
        confirmation_granted = False

        if isinstance(approval_grant, ApprovalGrant):
            operation = str(
                step.metadata.get("operation", tool_name)
            )
            try:
                expected_binding = approval_binding_digest(
                    operation=operation,
                    tool_name=tool_name,
                    parameters=parameters,
                    task_id=approval_grant.task_id,
                    plan_id=plan.plan_id,
                    step_id=step.step_id,
                )
            except ValueError:
                expected_binding = ""

            confirmation_granted = bool(expected_binding) and (
                hmac.compare_digest(
                    approval_grant.binding_digest,
                    expected_binding,
                )
                and (
                    approval_grant.expires_at is None
                    or utc_now() < approval_grant.expires_at
                )
                and (
                    approval_grant.task_id is None
                    or approval_grant.task_id == execution_context.task_id
                )
            )

        last_error = ""
        effective_max_attempts = self._retry_policy.max_attempts
        effective_backoff_seconds = self._retry_policy.backoff_seconds

        try:
            tool_definition = self._tool_executor.get(
                tool_name.strip()
            ).definition
        except KeyError:
            tool_definition = None

        if (
            tool_definition is not None
            and tool_definition.retry_max_attempts > effective_max_attempts
        ):
            effective_max_attempts = tool_definition.retry_max_attempts
            effective_backoff_seconds = (
                tool_definition.retry_backoff_seconds
            )

        for attempt in range(
            1,
            effective_max_attempts + 1,
        ):
            remaining = execution_context.usage.remaining_seconds(
                execution_context.limits
            )

            if remaining <= 0:
                last_error = "Execution time budget exhausted."
                plan.metadata["execution_outcome"] = "budget_exhausted"
                break

            if (
                attempt > 1
                and effective_backoff_seconds > 0
            ):
                delay = (
                    effective_backoff_seconds
                    * (2 ** (attempt - 2))
                )

                delay = min(delay, remaining)

                if cancel_event is None:
                    await asyncio.sleep(delay)
                else:
                    try:
                        await asyncio.wait_for(
                            cancel_event.wait(),
                            timeout=delay,
                        )
                    except TimeoutError:
                        pass
                    else:
                        self._plan_executor.cancel(plan)
                        step.metadata["verified"] = False
                        step.metadata["tool_error"] = (
                            "Cancellation requested during retry backoff."
                        )
                        return

                if execution_context.usage.remaining_seconds(
                    execution_context.limits
                ) <= 0:
                    last_error = "Execution time budget exhausted."
                    plan.metadata["execution_outcome"] = "budget_exhausted"
                    break

            if (
                execution_context.usage.tool_calls
                >= execution_context.limits.max_tool_calls
            ):
                last_error = (
                    "Tool-call budget exhausted: "
                    f"{execution_context.limits.max_tool_calls}."
                )
                plan.metadata["execution_outcome"] = "budget_exhausted"
                break

            execution_context.usage.tool_calls += 1

            if attempt > 1:
                execution_context.usage.retries += 1

            try:
                execution_kwargs = {
                    "parameters": parameters,
                }

                if confirmation_granted:
                    execution_kwargs["confirmation_granted"] = True

                if cancel_event is not None:
                    execution_kwargs["cancel_event"] = cancel_event

                result = await asyncio.wait_for(
                    self._tool_executor.execute(
                        tool_name.strip(),
                        **execution_kwargs,
                    ),
                    timeout=execution_context.usage.remaining_seconds(
                        execution_context.limits
                    ),
                )
            except TimeoutError:
                last_error = "Execution time budget exhausted."
                plan.metadata["execution_outcome"] = "budget_exhausted"
                step.metadata["attempts"] = attempt
                break
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

            if result.status is ToolExecutionStatus.CANCELLED:
                step.metadata["verified"] = False
                step.metadata["tool_error"] = (
                    result.error or result.message or "Tool execution cancelled."
                )
                self._plan_executor.cancel(plan)
                return

            if result.status is ToolExecutionStatus.PARTIAL:
                step.metadata["partial"] = True
                step.metadata["tool_result"] = result.data
                last_error = (
                    result.error
                    or result.message
                    or "Tool execution produced a partial result."
                )
                plan.metadata["execution_outcome"] = "partial"
                break

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

                await observer.on_step_completed(
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

            if (
                result.status is ToolExecutionStatus.TIMEOUT
                and result.side_effects_may_continue
            ):
                break

        attempts = step.metadata.get(
            "attempts"
        )

        if not isinstance(attempts, int):
            attempts = (
                effective_max_attempts
            )

        step.metadata["attempts"] = attempts
        step.metadata["verified"] = False
        step.metadata["tool_error"] = last_error
        plan.metadata["execution_error"] = last_error

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

        await observer.on_step_failed(
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
