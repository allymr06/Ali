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
from app.execution.models import RetryPolicy
from app.execution.verification import VerificationEngine
from app.planning.executor import PlanExecutor
from app.planning.models import Plan, PlanStep


class ExecutionObserver:
    """Lifecycle observer for execution events."""

    async def on_step_started(
        self,
        plan,
        step,
    ) -> None:
        return None

    async def on_step_completed(
        self,
        plan,
        step,
    ) -> None:
        return None

    async def on_step_failed(
        self,
        plan,
        step,
        error: str,
    ) -> None:
        return None

    async def on_plan_completed(
        self,
        plan,
    ) -> None:
        return None

    async def on_plan_failed(
        self,
        plan,
    ) -> None:
        return None

    async def on_plan_cancelled(
        self,
        plan,
    ) -> None:
        return None


class ExecutionService:
    """Coordinate plan execution, verification, retries, and events."""

    def __init__(
        self,
        *,
        tool_executor: Any,
        plan_executor: PlanExecutor,
        verification_engine: VerificationEngine | None = None,
        retry_policy: RetryPolicy | None = None,
        observer: ExecutionObserver | None = None,
        event_bus: ExecutionEventBus | None = None,
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

    @property
    def event_bus(self) -> ExecutionEventBus:
        return self._event_bus

    async def execute(
        self,
        plan: Plan,
        *,
        execution_context: ExecutionContext | None = None,
        cancel_event: asyncio.Event | None = None,
    ) -> Plan:
        context = execution_context or ExecutionContext(
            plan_id=plan.plan_id,
        )

        context.plan_id = plan.plan_id

        self._plan_executor.start(plan)

        await self._event_bus.publish(
            ExecutionEvent(
                event_type=ExecutionEventType.PLAN_STARTED,
                plan_id=plan.plan_id,
                data={
                    "goal": plan.goal,
                },
            )
        )

        while plan.status.value == "running":
            if cancel_event is not None and cancel_event.is_set():
                self._plan_executor.cancel(plan)

                await self._observer.on_plan_cancelled(plan)

                await self._event_bus.publish(
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

            await self._observer.on_step_started(
                plan,
                step,
            )

            await self._event_bus.publish(
                ExecutionEvent(
                    event_type=ExecutionEventType.STEP_STARTED,
                    plan_id=plan.plan_id,
                    step_id=step.step_id,
                    step_name=step.name,
                )
            )

            await self._execute_step(
                plan,
                step,
                execution_context=context,
            )

        if plan.status.value == "completed":
            await self._observer.on_plan_completed(plan)

            await self._event_bus.publish(
                ExecutionEvent(
                    event_type=ExecutionEventType.PLAN_COMPLETED,
                    plan_id=plan.plan_id,
                    data={
                        "progress": plan.progress,
                    },
                )
            )

        elif plan.status.value == "failed":
            await self._observer.on_plan_failed(plan)

            await self._event_bus.publish(
                ExecutionEvent(
                    event_type=ExecutionEventType.PLAN_FAILED,
                    plan_id=plan.plan_id,
                )
            )

        elif plan.status.value == "cancelled":
            await self._observer.on_plan_cancelled(plan)

            await self._event_bus.publish(
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
        tool_name = step.metadata.get("tool_name")

        if not isinstance(tool_name, str) or not tool_name.strip():
            self._fail(
                plan,
                step,
                "Invalid tool_name.",
            )
            await self._observer.on_step_failed(
                plan,
                step,
                "Invalid tool_name.",
            )
            return

        parameters = step.metadata.get(
            "parameters",
            {},
        )

        if not isinstance(parameters, dict):
            self._fail(
                plan,
                step,
                "Parameters must be a dictionary.",
            )
            await self._observer.on_step_failed(
                plan,
                step,
                "Parameters must be a dictionary.",
            )
            return

        verifier = step.metadata.get("verifier")

        if verifier is not None and not callable(verifier):
            self._fail(
                plan,
                step,
                "Verifier must be callable.",
            )
            await self._observer.on_step_failed(
                plan,
                step,
                "Verifier must be callable.",
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
                continue

            step.metadata["attempts"] = attempt
            step.metadata["last_status"] = result.status.value
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
                await self._event_bus.publish(
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

            verification = self._verification_engine.verify(
                result,
                verifier=verifier,
            )

            if verification.passed:
                step.metadata["tool_result"] = result.data
                step.metadata["verified"] = True
                step.metadata["verification_reason"] = (
                    verification.reason
                )

                self._plan_executor.complete_step(
                    plan,
                    step,
                )

                await self._observer.on_step_completed(
                    plan,
                    step,
                )

                await self._event_bus.publish(
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

        attempts = step.metadata.get("attempts")

        if not isinstance(attempts, int):
            attempts = self._retry_policy.max_attempts

        step.metadata["attempts"] = attempts

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

        await self._event_bus.publish(
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
