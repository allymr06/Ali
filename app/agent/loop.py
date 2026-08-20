from __future__ import annotations

from collections.abc import Callable, Iterable
from typing import Any

from app.agent.approval import ApprovalStatus, ApprovalStore
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

ModeRouter = Callable[
    [Request, Context],
    AgentMode,
]


class AgentLoop:
    """High-level request -> routing -> planning -> execution loop."""

    def __init__(
        self,
        *,
        engine: CoreEngine,
        plan_builder: PlanBuilder | None = None,
        mode_router: ModeRouter | None = None,
        approval_store: ApprovalStore | None = None,
    ) -> None:
        self._engine = engine
        self._plan_builder = plan_builder
        self._mode_router = mode_router or self._default_mode_router
        self._approval_store = approval_store or ApprovalStore()

    @property
    def engine(self) -> CoreEngine:
        return self._engine

    @property
    def approval_store(self) -> ApprovalStore:
        return self._approval_store

    def approve(
        self,
        operation_id,
    ):
        return self._approval_store.approve(
            operation_id
        )

    def deny(
        self,
        operation_id,
    ):
        return self._approval_store.deny(
            operation_id
        )

    def get_approval(
        self,
        operation_id,
    ):
        return self._approval_store.get(
            operation_id
        )

    def choose_mode(
        self,
        request: Request,
        context: Context,
    ) -> AgentMode:
        return self._mode_router(
            request,
            context,
        )

    def request_approval(
        self,
        *,
        operation: str,
        reason: str,
        risk_level: str,
        task_id=None,
        plan_id=None,
        metadata=None,
    ):
        return self._approval_store.create(
            operation=operation,
            reason=reason,
            risk_level=risk_level,
            task_id=task_id,
            plan_id=plan_id,
            metadata=metadata,
        )

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

        return self._engine.create_plan(
            request.text,
            list(built),
        )

    async def run(
        self,
        request: Request,
        *,
        context: Context | None = None,
        mode: AgentMode | None = None,
        cancel_event=None,
    ) -> AgentExecutionResult:
        active_context = (
            context
            if context is not None
            else Context()
        )

        selected_mode = (
            mode
            if mode is not None
            else self.choose_mode(
                request,
                active_context,
            )
        )

        if selected_mode is AgentMode.DIRECT:
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

        active_context.active_task_id = task.task_id

        if task.status.value == "completed":
            status = AgentStatus.COMPLETED
        elif task.status.value == "cancelled":
            status = AgentStatus.CANCELLED
        else:
            status = AgentStatus.FAILED

        return AgentExecutionResult(
            status=status,
            response_text=self._build_task_response(task),
            task_id=task.task_id,
            plan_id=plan.plan_id,
            metadata={
                "mode": AgentMode.TASK.value,
                "request_id": str(request.request_id),
                "conversation_id": str(
                    active_context.conversation_id
                ),
                "progress": task.progress,
                "task_status": task.status.value,
                "current_step": task.current_step,
            },
        )

    @staticmethod
    def _default_mode_router(
        request: Request,
        context: Context,
    ) -> AgentMode:
        text = request.text.strip().lower()

        direct_markers = (
            "merhaba",
            "selam",
            "hello",
            "hi",
            "hey",
            "nas?ls?n",
            "nasilsin",
            "how are you",
            "te?ekk?r",
            "tesekkur",
            "sa? ol",
            "sag ol",
            "thanks",
            "thank you",
            "kimdir",
            "what is",
            "nedir",
            "ne demek",
            "anlat?r m?s?n",
            "anlatir misin",
            "a??klar m?s?n",
            "aciklar misin",
            "explain",
            "what does",
        )

        task_markers = (
            "yap",
            "olu?tur",
            "olustur",
            "haz?rla",
            "hazirla",
            "ara?t?r",
            "arastir",
            "bul",
            "kontrol et",
            "indir",
            "kur",
            "sil",
            "de?i?tir",
            "degistir",
            "?al??t?r",
            "calistir",
            "planla",
            "execute",
            "create",
            "prepare",
            "research",
            "find",
            "download",
            "install",
            "delete",
            "change",
            "run",
            "do ",
            "agent ",
            "task",
            "fail",
            "cancel",
            "context",
        )

        if context.active_task_id is not None:
            return AgentMode.TASK

        if any(
            marker in text
            for marker in task_markers
        ):
            return AgentMode.TASK

        if any(
            text.startswith(marker)
            or marker in text
            for marker in direct_markers
        ):
            return AgentMode.DIRECT

        # Ambiguous non-conversational requests belong to the
        # task path. This also guarantees that task-mode requests
        # cannot silently fall back to direct provider execution
        # when no plan builder has been configured.
        return AgentMode.TASK

    @staticmethod
    def _build_task_response(task) -> str:
        if task.status.value == "completed":
            return f"Task completed: {task.goal}"

        if task.status.value == "cancelled":
            return f"Task cancelled: {task.goal}"

        return (
            f"Task failed: {task.goal}. "
            f"{task.error or 'Task execution failed.'}"
        )
