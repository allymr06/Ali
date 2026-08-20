from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from uuid import UUID

from app.agent.approval import (
    ApprovalGrant,
    ApprovalRequest,
    ApprovalStatus,
    ApprovalStore,
    approval_binding_digest,
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
    grant: ApprovalGrant | None = None
    message: str = ""


class ApprovalGate:
    """Turn step metadata into deterministic approval decisions."""

    def __init__(
        self,
        store: ApprovalStore | None = None,
        *,
        approval_ttl_seconds: float = 300.0,
    ) -> None:
        if approval_ttl_seconds <= 0:
            raise ValueError("approval_ttl_seconds must be greater than 0.")

        self._store = store or ApprovalStore()
        self._approval_ttl_seconds = approval_ttl_seconds

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

        operation = str(
            step.metadata.get(
                "operation",
                step.metadata.get(
                    "tool_name",
                    step.name,
                ),
            )
        )
        tool_name = str(
            step.metadata.get("tool_name", step.name)
        )
        parameters = step.metadata.get("parameters", {})

        if not isinstance(parameters, dict):
            return ApprovalGateDecision(
                result=ApprovalGateResult.DENIED,
                message="Approval parameters must be a dictionary.",
            )

        try:
            binding_digest = approval_binding_digest(
                operation=operation,
                tool_name=tool_name,
                parameters=parameters,
                task_id=task_id,
                plan_id=plan_id,
                step_id=step.step_id,
            )
        except ValueError as exc:
            return ApprovalGateDecision(
                result=ApprovalGateResult.DENIED,
                message=str(exc),
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
                if (
                    existing.is_expired
                    or existing.binding_digest != binding_digest
                ):
                    self._store.expire(existing.operation_id)
                    existing = None

            if existing is not None:
                if existing.status is ApprovalStatus.APPROVED:
                    return ApprovalGateDecision(
                        result=ApprovalGateResult.APPROVED,
                        request=existing,
                        grant=ApprovalGrant(
                            operation_id=existing.operation_id,
                            binding_digest=binding_digest,
                            expires_at=existing.expires_at,
                            task_id=task_id,
                        ),
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
                "step_id": str(step.step_id),
                "tool_name": tool_name,
            },
            binding_digest=binding_digest,
            expires_in_seconds=self._approval_ttl_seconds,
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
