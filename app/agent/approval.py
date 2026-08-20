from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from uuid import UUID, uuid4

from app.core.time import utc_now


class ApprovalRequirement(str, Enum):
    NOT_REQUIRED = "not_required"
    REQUIRED = "required"


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
    requirement: ApprovalRequirement = ApprovalRequirement.REQUIRED
    metadata: dict[str, object] = field(default_factory=dict)
    binding_digest: str | None = None
    created_at: datetime = field(default_factory=utc_now)
    expires_at: datetime | None = None

    @property
    def is_resolved(self) -> bool:
        return self.status in {
            ApprovalStatus.APPROVED,
            ApprovalStatus.DENIED,
            ApprovalStatus.EXPIRED,
        }

    @property
    def is_expired(self) -> bool:
        return (
            self.status is ApprovalStatus.EXPIRED
            or (
                self.expires_at is not None
                and utc_now() >= self.expires_at
            )
        )


@dataclass(frozen=True, slots=True)
class ApprovalGrant:
    """Opaque in-memory proof that an exact action was approved."""

    operation_id: UUID
    binding_digest: str
    expires_at: datetime | None
    task_id: UUID | None


def approval_binding_digest(
    *,
    operation: str,
    tool_name: str,
    parameters: dict[str, object],
    task_id: UUID | None,
    plan_id: UUID | None,
    step_id: UUID,
) -> str:
    """Create a stable fingerprint for the exact approved action."""
    payload = {
        "operation": operation,
        "tool_name": tool_name,
        "parameters": parameters,
        "task_id": str(task_id) if task_id is not None else None,
        "plan_id": str(plan_id) if plan_id is not None else None,
        "step_id": str(step_id),
    }

    try:
        encoded = json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ValueError(
            "Approval parameters must be JSON-serializable."
        ) from exc

    return hashlib.sha256(encoded).hexdigest()


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
        binding_digest: str | None = None,
        expires_in_seconds: float | None = None,
    ) -> ApprovalRequest:
        if expires_in_seconds is not None and expires_in_seconds <= 0:
            raise ValueError("Approval expiry must be greater than 0 seconds.")

        created_at = utc_now()
        request = ApprovalRequest(
            operation=operation,
            reason=reason,
            risk_level=risk_level,
            task_id=task_id,
            plan_id=plan_id,
            metadata=dict(metadata or {}),
            binding_digest=binding_digest,
            created_at=created_at,
            expires_at=(
                created_at + timedelta(seconds=expires_in_seconds)
                if expires_in_seconds is not None
                else None
            ),
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

        if request.is_expired:
            request.status = ApprovalStatus.EXPIRED
            raise ValueError("Cannot approve an expired request.")

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

    def expire(self, operation_id: UUID) -> ApprovalRequest:
        request = self.get(operation_id)

        if request.status in {
            ApprovalStatus.PENDING,
            ApprovalStatus.APPROVED,
        }:
            request.status = ApprovalStatus.EXPIRED

        return request

    def list(self) -> list[ApprovalRequest]:
        return list(self._requests.values())


@dataclass(slots=True)
class ApprovalDecision:
    status: ApprovalStatus
    operation_id: UUID | None = None
    message: str = ""
