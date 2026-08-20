from __future__ import annotations

from collections.abc import Callable, Iterable
from typing import Any
from uuid import UUID

from app.agent.models import (
    AgentExecutionResult,
    AgentMode,
    AgentStatus,
)
from app.core.engine import CoreEngine
from app.core.models import Context, Request
from app.planning.models import Plan, PlanStep


PlanBuilder = Callable[
    [Request, Context],
    Plan | Iterable[PlanStep],
]


class AgentLoop:
    """High-level request -> plan -> tracked task -> execution loop."""

    def __init__(
        self,
        *,
        engine: CoreEngine,
        plan_builder: PlanBuilder | None = None,
    ) -> None:
        self._engine = engine
        self._plan_builder = plan_builder

    @property
    def engine(self) -> CoreEngine:
        return self._engine

    def build_plan(
        self,
        request: Request,
        context: Context,
    ) -> Plan:
        if self._plan_builder is None:
            raise ValueError(
                "AgentLoop requires a plan_builder for task execution."
            )

        built = self._plan_builder(
            request,
            context,
        )

        if isinstance(built, Plan):
            return built

        steps = list(built)

        return self._engine.create_plan(
            request.text,
            steps,
        )

    async def run(
        self,
        request: Request,
        *,
        context: Context | None = None,
        mode: AgentMode = AgentMode.TASK,
        cancel_event=None,
    ) -> AgentExecutionResult:
        active_context = context or Context()

        if mode is AgentMode.DIRECT:
            response = await self._engine.handle(
                request,
                active_context,
            )

            return AgentExecutionResult(
                status=AgentStatus.COMPLETED,
                response_text=response.text,
                metadata={
                    "mode": AgentMode.DIRECT.value,
                    "request_id": str(request.request_id),
                    "response_id": str(response.response_id),
                },
            )

        plan = self.build_plan(
            request,
            active_context,
        )

        task = await self._engine.execute_task(
            request.text,
            plan,
            cancel_event=cancel_event,
            request_id=request.request_id,
            conversation_id=active_context.conversation_id,
        )

        if task.status.value == "completed":
            status = AgentStatus.COMPLETED
        elif task.status.value == "cancelled":
            status = AgentStatus.CANCELLED
        else:
            status = AgentStatus.FAILED

        response_text = self._build_task_response(
            task,
            plan,
        )

        return AgentExecutionResult(
            status=status,
            response_text=response_text,
            task_id=task.task_id,
            plan_id=plan.plan_id,
            metadata={
                "mode": AgentMode.TASK.value,
                "request_id": str(request.request_id),
                "progress": task.progress,
                "task_status": task.status.value,
            },
        )

    @staticmethod
    def _build_task_response(
        task,
        plan: Plan,
    ) -> str:
        if task.status.value == "completed":
            return (
                f"Task completed: {task.goal}"
            )

        if task.status.value == "cancelled":
            return (
                f"Task cancelled: {task.goal}"
            )

        error = task.error or "Task execution failed."

        return (
            f"Task failed: {task.goal}. "
            f"{error}"
        )
