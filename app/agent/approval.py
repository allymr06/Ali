from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from uuid import UUID, uuid4


class ApprovalStatus(str, Enum):
    NOT_REQUIRED = "not_required"
    PENDING = "pending"
    APPROVED = "approved"
    DENIED = "denied"
    EXPIRED = "expired"


@dataclass(slots=True)
class ApprovalRequest:
    operation: str
    reason: str
    risk_level: str
    task_id: UUID | None = None
    plan_id: UUID | None = None
    operation_id: UUID = field(default_factory=uuid4)
    status: ApprovalStatus = ApprovalStatus.PENDING
    metadata: dict[str, object] = field(default_factory=dict)


class ApprovalStore:
    """In-memory approval state store."""

    def __init__(self) -> None:
        self._requests: dict[UUID, ApprovalRequest] = {}

    def create(
        self,
        *,
        operation: str,
        reason: str,
        risk_level: str,
        task_id: UUID | None = None,
        plan_id: UUID | None = None,
        metadata: dict[str, object] | None = None,
    ) -> ApprovalRequest:
        request = ApprovalRequest(
            operation=operation,
            reason=reason,
            risk_level=risk_level,
            task_id=task_id,
            plan_id=plan_id,
            metadata=dict(metadata or {}),
        )

        self._requests[request.operation_id] = request
        return request

    def get(self, operation_id: UUID) -> ApprovalRequest:
        try:
            return self._requests[operation_id]
        except KeyError as exc:
            raise KeyError(
                f"Unknown approval request: {operation_id}"
            ) from exc

    def approve(self, operation_id: UUID) -> ApprovalRequest:
        request = self.get(operation_id)

        if request.status is not ApprovalStatus.PENDING:
            raise ValueError(
                f"Cannot approve request from state "
                f"{request.status.value}."
            )

        request.status = ApprovalStatus.APPROVED
        return request

    def deny(self, operation_id: UUID) -> ApprovalRequest:
        request = self.get(operation_id)

        if request.status is not ApprovalStatus.PENDING:
            raise ValueError(
                f"Cannot deny request from state "
                f"{request.status.value}."
            )

        request.status = ApprovalStatus.DENIED
        return request

    def list(self) -> list[ApprovalRequest]:
        return list(self._requests.values())


@dataclass(slots=True)
class ApprovalDecision:
    status: ApprovalStatus
    operation_id: UUID | None = None
    message: str = ""
