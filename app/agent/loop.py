from __future__ import annotations

from collections.abc import Callable, Iterable
from typing import Any

from app.agent.approval import ApprovalStatus, ApprovalStore
from app.agent.approval_gate import ApprovalGate
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
        approval_ttl_seconds: float = 300.0,
    ) -> None:
        self._engine = engine
        self._plan_builder = plan_builder
        self._mode_router = mode_router or self._default_mode_router
        self._approval_store = approval_store or ApprovalStore()
        self._approval_gate = ApprovalGate(
            self._approval_store,
            approval_ttl_seconds=approval_ttl_seconds,
            tool_executor=self._engine.tool_executor,
        )

    @property
    def engine(self) -> CoreEngine:
        return self._engine

    @property
    def approval_store(self) -> ApprovalStore:
        return self._approval_store

    @property
    def approval_gate(self) -> ApprovalGate:
        return self._approval_gate

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

    def evaluate_approval(
        self,
        *,
        step,
        task_id=None,
        plan_id=None,
        request_id=None,
        conversation_id=None,
    ):
        return self._approval_gate.evaluate(
            step=step,
            task_id=task_id,
            plan_id=plan_id,
            request_id=request_id,
            conversation_id=conversation_id,
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

    def _check_plan_approvals(
        self,
        plan: Plan,
        *,
        request_id=None,
        conversation_id=None,
    ):
        if "_approval_request_id" not in plan.metadata:
            plan.metadata["_approval_request_id"] = request_id
        if "_approval_conversation_id" not in plan.metadata:
            plan.metadata["_approval_conversation_id"] = conversation_id
        request_id = plan.metadata["_approval_request_id"]
        conversation_id = plan.metadata["_approval_conversation_id"]
        pending = []
        denied = []

        for step in plan.steps:
            decision = self._approval_gate.evaluate(
                step=step,
                plan_id=plan.plan_id,
                request_id=request_id,
                conversation_id=conversation_id,
            )

            if decision.result.value == "pending":
                pending.append(
                    {
                        "step_name": step.name,
                        "operation_id": (
                            str(decision.request.operation_id)
                            if decision.request is not None
                            else None
                        ),
                        "operation": (
                            decision.request.operation
                            if decision.request is not None
                            else step.name
                        ),
                        "reason": (
                            decision.request.reason
                            if decision.request is not None
                            else decision.message
                        ),
                        "risk_level": (
                            decision.request.risk_level
                            if decision.request is not None
                            else step.metadata.get(
                                "risk_level",
                                "unknown",
                            )
                        ),
                    }
                )

            elif decision.result.value == "denied":
                denied.append(
                    {
                        "step_name": step.name,
                        "operation_id": (
                            str(decision.request.operation_id)
                            if decision.request is not None
                            else None
                        ),
                        "operation": (
                            decision.request.operation
                            if decision.request is not None
                            else step.name
                        ),
                        "reason": (
                            decision.request.reason
                            if decision.request is not None
                            else decision.message
                        ),
                    }
                )

            elif decision.result.value == "approved":
                step.metadata["_approval_grant"] = decision.grant

        return pending, denied

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
            try:
                response = await self._engine.handle(
                    request,
                    active_context,
                    cancel_event=cancel_event,
                )
            except Exception as exc:
                return AgentExecutionResult(
                    status=AgentStatus.FAILED,
                    response_text=f"Request failed: {exc}",
                    metadata={
                        "mode": AgentMode.DIRECT.value,
                        "request_id": str(request.request_id),
                        "error": str(exc),
                    },
                )

            outcome = response.metadata.get("outcome", "completed")

            if outcome == "cancelled":
                direct_status = AgentStatus.CANCELLED
            elif response.metadata.get("completion_verified", True):
                direct_status = AgentStatus.COMPLETED
            else:
                direct_status = AgentStatus.FAILED

            return AgentExecutionResult(
                status=direct_status,
                response_text=response.text,
                metadata={
                    "mode": AgentMode.DIRECT.value,
                    "request_id": str(request.request_id),
                    "response_id": str(response.response_id),
                    "outcome": outcome,
                    "completion_verified": response.metadata.get(
                        "completion_verified",
                        True,
                    ),
                },
            )

        plan = self.build_plan(
            request,
            active_context,
        )

        approval_request_id = plan.metadata.setdefault(
            "_approval_request_id",
            request.request_id,
        )
        approval_conversation_id = plan.metadata.setdefault(
            "_approval_conversation_id",
            active_context.conversation_id,
        )

        pending_approvals, denied_approvals = (
            self._check_plan_approvals(
                plan,
                request_id=approval_request_id,
                conversation_id=approval_conversation_id,
            )
        )

        if denied_approvals:
            first = denied_approvals[0]

            return AgentExecutionResult(
                status=AgentStatus.FAILED,
                response_text=(
                    f"Approval denied for {first['operation']}."
                ),
                plan_id=plan.plan_id,
                metadata={
                    "mode": AgentMode.TASK.value,
                    "request_id": str(request.request_id),
                    "approval_status": "denied",
                    "denied_approvals": denied_approvals,
                },
            )

        if pending_approvals:
            operations = ", ".join(
                item["operation"]
                for item in pending_approvals
            )

            return AgentExecutionResult(
                status=AgentStatus.WAITING_FOR_APPROVAL,
                response_text=(
                    "Approval required before execution: "
                    f"{operations}"
                ),
                plan_id=plan.plan_id,
                metadata={
                    "mode": AgentMode.TASK.value,
                    "request_id": str(request.request_id),
                    "approval_status": "pending",
                    "pending_approvals": pending_approvals,
                },
            )

        task = await self._engine.execute_task(
            request.text,
            plan,
            cancel_event=cancel_event,
            request_id=approval_request_id,
            conversation_id=approval_conversation_id,
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

        if context.active_task_id is not None:
            return AgentMode.TASK

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
            "send",
            "g?nder",
            "gonder",
            "mail",
            "email",
            "e-posta",
        )

        if any(marker in text for marker in task_markers):
            return AgentMode.TASK

        if any(
            text.startswith(marker) or marker in text
            for marker in direct_markers
        ):
            return AgentMode.DIRECT

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
