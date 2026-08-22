from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from uuid import UUID

from app.core.models import RiskLevel
from app.agent.approval import (
    ApprovalRequest,
    ApprovalStatus,
    ApprovalStore,
)
from app.security.approval import ApprovalGrant, approval_binding_digest
from app.security.permissions import PermissionDecision


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
        tool_executor=None,
    ) -> None:
        if approval_ttl_seconds <= 0:
            raise ValueError("approval_ttl_seconds must be greater than 0.")

        self._store = store or ApprovalStore()
        self._approval_ttl_seconds = approval_ttl_seconds
        self._tool_executor = tool_executor

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
        request_id: UUID | None = None,
        conversation_id: UUID | None = None,
    ) -> ApprovalGateDecision:
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

        permission = None
        tool_version = None
        if self._tool_executor is not None:
            try:
                registered = self._tool_executor.get(tool_name)
            except KeyError:
                registered = None

            if registered is not None:
                tool_version = registered.definition.version
                permission = self._tool_executor.permission_engine.evaluate(
                    registered.definition,
                    operation=operation,
                    parameters=parameters,
                )

        if (
            permission is not None
            and permission.decision is PermissionDecision.DENY
        ):
            return ApprovalGateDecision(
                result=ApprovalGateResult.DENIED,
                message=permission.reason,
            )

        requires_approval = self.requires_approval(step) or (
            permission is not None
            and permission.decision is PermissionDecision.CONFIRM
        )

        if not requires_approval:
            return ApprovalGateDecision(
                result=ApprovalGateResult.NOT_REQUIRED,
                message="Approval is not required.",
            )

        try:
            binding_digest = approval_binding_digest(
                operation=operation,
                tool_name=tool_name,
                parameters=parameters,
                task_id=task_id,
                plan_id=plan_id,
                step_id=step.step_id,
                tool_version=tool_version,
                request_id=request_id,
                conversation_id=conversation_id,
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
                    self._store.is_expired(existing)
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
                permission.reason
                if permission is not None
                else f"Approval required for {operation}.",
            )
        )

        declared_risk = str(step.metadata.get("risk_level", "")).lower()
        risk_order = {level.value: index for index, level in enumerate(RiskLevel)}
        actual_risk = (
            permission.risk_level.value if permission is not None else "high"
        )
        risk_level = max(
            (actual_risk, declared_risk),
            key=lambda value: risk_order.get(value, -1),
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
                "tool_version": tool_version,
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
