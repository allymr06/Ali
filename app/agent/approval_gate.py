from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from uuid import UUID

from app.agent.approval import (
    ApprovalRequest,
    ApprovalStatus,
    ApprovalStore,
)


class ApprovalGateResult(str, Enum):
    NOT_REQUIRED = "not_required"
    APPROVED = "approved"
    PENDING = "pending"
    DENIED = "denied"


@dataclass(slots=True)
class ApprovalGateDecision:
    result: ApprovalGateResult
    request: ApprovalRequest | None = None
    message: str = ""


class ApprovalGate:
    """Turn step metadata into deterministic approval decisions."""

    def __init__(
        self,
        store: ApprovalStore | None = None,
    ) -> None:
        self._store = store or ApprovalStore()

    @property
    def store(self) -> ApprovalStore:
        return self._store

    @staticmethod
    def requires_approval(step) -> bool:
        value = step.metadata.get("requires_approval")

        if isinstance(value, bool):
            return value

        value = step.metadata.get("risk_level")

        return str(value).lower() in {
            "medium",
            "high",
            "critical",
        }

    def evaluate(
        self,
        *,
        step,
        task_id: UUID | None = None,
        plan_id: UUID | None = None,
    ) -> ApprovalGateDecision:
        if not self.requires_approval(step):
            return ApprovalGateDecision(
                result=ApprovalGateResult.NOT_REQUIRED,
                message="Approval is not required.",
            )

        existing_id = step.metadata.get(
            "approval_operation_id"
        )

        if existing_id is not None:
            try:
                existing = self._store.get(existing_id)
            except (KeyError, TypeError, ValueError):
                existing = None

            if existing is not None:
                if existing.status is ApprovalStatus.APPROVED:
                    return ApprovalGateDecision(
                        result=ApprovalGateResult.APPROVED,
                        request=existing,
                        message="Approval granted.",
                    )

                if existing.status is ApprovalStatus.DENIED:
                    return ApprovalGateDecision(
                        result=ApprovalGateResult.DENIED,
                        request=existing,
                        message="Approval denied.",
                    )

                return ApprovalGateDecision(
                    result=ApprovalGateResult.PENDING,
                    request=existing,
                    message="Approval is pending.",
                )

        operation = str(
            step.metadata.get(
                "operation",
                step.metadata.get(
                    "tool_name",
                    step.name,
                ),
            )
        )

        reason = str(
            step.metadata.get(
                "approval_reason",
                f"Approval required for {operation}.",
            )
        )

        risk_level = str(
            step.metadata.get(
                "risk_level",
                "high",
            )
        )

        request = self._store.create(
            operation=operation,
            reason=reason,
            risk_level=risk_level,
            task_id=task_id,
            plan_id=plan_id,
            metadata={
                "step_name": step.name,
            },
        )

        step.metadata["approval_operation_id"] = request.operation_id

        return ApprovalGateDecision(
            result=ApprovalGateResult.PENDING,
            request=request,
            message="Approval is required before execution.",
        )

    def approve(
        self,
        operation_id,
    ) -> ApprovalRequest:
        return self._store.approve(operation_id)

    def deny(
        self,
        operation_id,
    ) -> ApprovalRequest:
        return self._store.deny(operation_id)
