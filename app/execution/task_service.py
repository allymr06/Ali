from __future__ import annotations

from typing import Any
from uuid import UUID

from app.core.models import TaskStepStatus
from app.execution.models import ExecutionLimits, RetryPolicy
from app.execution.service import ExecutionObserver, ExecutionService
from app.execution.verification import VerificationEngine
from app.planning.executor import PlanExecutor
from app.planning.models import Plan, PlanStatus
from app.tasks.manager import TaskManager


class TaskExecutionObserver(ExecutionObserver):
    """Mirror plan execution lifecycle into TaskManager."""

    def __init__(
        self,
        task_manager: TaskManager,
        task_id: UUID,
        plan_to_task_steps: dict[str, Any],
    ) -> None:
        self._task_manager = task_manager
        self._task_id = task_id
        self._plan_to_task_steps = plan_to_task_steps

    async def on_step_started(self, plan, step) -> None:
        task_step = self._plan_to_task_steps[step.name]

        self._task_manager.start_step(
            self._task_id,
            task_step.step_id,
        )

    async def on_step_completed(self, plan, step) -> None:
        task_step = self._plan_to_task_steps[step.name]

        self._task_manager.complete_step(
            self._task_id,
            task_step.step_id,
            result=step.metadata.get("tool_result"),
        )

    async def on_step_failed(
        self,
        plan,
        step,
        error: str,
    ) -> None:
        task_step = self._plan_to_task_steps[step.name]

        self._task_manager.fail_step(
            self._task_id,
            task_step.step_id,
            error,
        )

    async def on_plan_cancelled(self, plan) -> None:
        for task_step in self._plan_to_task_steps.values():
            if task_step.status not in {
                TaskStepStatus.COMPLETED,
                TaskStepStatus.CANCELLED,
                TaskStepStatus.FAILED,
            }:
                self._task_manager.cancel_step(
                    self._task_id,
                    task_step.step_id,
                )


class TaskExecutionService:
    """
    Execute a Plan as a tracked Task.

    Task lifecycle:
        QUEUED -> RUNNING -> COMPLETED / FAILED / CANCELLED

    Plan step lifecycle is mirrored into TaskStep lifecycle.
    """

    def __init__(
        self,
        *,
        task_manager: TaskManager,
        tool_executor: Any | None = None,
        execution_service: ExecutionService | None = None,
        retry_policy: RetryPolicy | None = None,
        limits: ExecutionLimits | None = None,
    ) -> None:
        self._task_manager = task_manager
        self._tool_executor = tool_executor
        self._retry_policy = retry_policy or RetryPolicy()
        self._limits = limits or ExecutionLimits()

        if execution_service is None and tool_executor is None:
            raise ValueError(
                "tool_executor is required when execution_service is absent."
            )

        self._execution_service = execution_service or ExecutionService(
            tool_executor=tool_executor,
            plan_executor=PlanExecutor(),
            verification_engine=VerificationEngine(),
            retry_policy=self._retry_policy,
            limits=self._limits,
        )

    async def execute(
        self,
        task_id: UUID,
        plan: Plan,
        *,
        cancel_event=None,
        pause_event=None,
        request_id=None,
        conversation_id=None,
    ):
        task = self._task_manager.get(task_id)

        if task.goal.strip() != plan.goal.strip():
            error = "Task goal and plan goal must match."
            self._task_manager.fail(task_id, error)
            raise ValueError(
                error
            )

        if task.status.value != "queued":
            raise ValueError(
                f"Cannot execute task from state {task.status.value}."
            )

        if not plan.steps:
            error = "Cannot execute a task without plan steps."
            self._task_manager.fail(task_id, error)
            raise ValueError(
                error
            )

        mapping = {}

        for plan_step in plan.steps:
            task_step = self._task_manager.add_step(
                task_id,
                plan_step.name,
                metadata={
                    "plan_step_id": str(plan_step.step_id),
                    "dependencies": list(plan_step.dependencies),
                },
            )
            mapping[plan_step.name] = task_step

        self._task_manager.start(task_id)

        observer = TaskExecutionObserver(
            self._task_manager,
            task_id,
            mapping,
        )

        try:
            from app.execution.context import ExecutionContext

            result = await self._execution_service.execute(
                plan,
                execution_context=ExecutionContext(
                    request_id=request_id,
                    conversation_id=conversation_id,
                    task_id=task_id,
                    plan_id=plan.plan_id,
                ),
                cancel_event=cancel_event,
                pause_event=pause_event,
                observer=observer,
                limits=self._limits,
            )

            current = self._task_manager.get(task_id)

            if result.status is PlanStatus.COMPLETED:
                self._task_manager.complete(
                    task_id,
                    result={
                        "plan_id": str(result.plan_id),
                        "plan_goal": result.goal,
                        "plan_progress": result.progress,
                        "outcome": result.metadata.get(
                            "execution_outcome",
                            "completed",
                        ),
                        "usage": result.metadata.get("execution_usage", {}),
                    },
                )

            elif result.status is PlanStatus.FAILED:
                error = "Plan execution failed."

                for step in result.steps:
                    step_error = step.metadata.get("tool_error")
                    if step_error:
                        error = str(step_error)
                        break

                partial_results = [
                    {
                        "step_id": str(step.step_id),
                        "step_name": step.name,
                        "result": step.metadata.get("tool_result"),
                    }
                    for step in result.steps
                    if step.metadata.get("partial") is True
                ]

                self._task_manager.fail(
                    task_id,
                    error,
                    result={
                        "plan_id": str(result.plan_id),
                        "plan_goal": result.goal,
                        "plan_progress": result.progress,
                        "outcome": result.metadata.get(
                            "execution_outcome",
                            "failed",
                        ),
                        "partial_results": partial_results,
                        "usage": result.metadata.get("execution_usage", {}),
                    },
                )

            elif result.status is PlanStatus.CANCELLED:
                if current.status.value not in {
                    "completed",
                    "failed",
                    "cancelled",
                }:
                    self._task_manager.cancel(
                        task_id,
                        result={
                            "plan_id": str(result.plan_id),
                            "plan_goal": result.goal,
                            "plan_progress": result.progress,
                            "outcome": result.metadata.get(
                                "execution_outcome",
                                "cancelled",
                            ),
                            "usage": result.metadata.get(
                                "execution_usage",
                                {},
                            ),
                        },
                    )

            elif result.status is PlanStatus.PAUSED:
                if current.status.value == "running":
                    self._task_manager.pause(task_id)
                self._task_manager.set_result(task_id, {
                    "plan_id": str(result.plan_id),
                    "plan_goal": result.goal,
                    "plan_progress": result.progress,
                    "outcome": "paused",
                    "usage": result.metadata.get("execution_usage", {}),
                })

            elif current.status.value not in {
                "completed",
                "failed",
                "cancelled",
            }:
                self._task_manager.fail(
                    task_id,
                    f"Unexpected plan terminal state: {result.status.value}",
                )

            return self._task_manager.get(task_id)

        except Exception as exc:
            current = self._task_manager.get(task_id)

            if current.status.value not in {
                "completed",
                "failed",
                "cancelled",
            }:
                self._task_manager.fail(
                    task_id,
                    str(exc),
                )

            raise
