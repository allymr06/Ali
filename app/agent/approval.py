from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from enum import Enum
from threading import RLock
from uuid import UUID, uuid4

from app.core.models import RiskLevel
from app.core.time import utc_now
from app.security.approval import ApprovalGrant, approval_binding_digest


class ApprovalRequirement(str, Enum):
    NOT_REQUIRED = "not_required"
    REQUIRED = "required"


class ApprovalStatus(str, Enum):
    NOT_REQUIRED = "not_required"
    PENDING = "pending"
    APPROVED = "approved"
    DENIED = "denied"
    EXPIRED = "expired"


@dataclass(frozen=True, slots=True)
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

    def __post_init__(self) -> None:
        operation = self.operation.strip()
        reason = self.reason.strip()
        risk_level = self.risk_level.strip().lower()
        if not operation:
            raise ValueError("Approval operation cannot be empty.")
        if not reason:
            raise ValueError("Approval reason cannot be empty.")
        if risk_level not in {level.value for level in RiskLevel}:
            raise ValueError("Approval risk_level is invalid.")
        if self.created_at.tzinfo is None:
            raise ValueError("Approval created_at must be timezone-aware.")
        if self.expires_at is not None and self.expires_at.tzinfo is None:
            raise ValueError("Approval expires_at must be timezone-aware.")
        object.__setattr__(self, "operation", operation)
        object.__setattr__(self, "reason", reason)
        object.__setattr__(self, "risk_level", risk_level)

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


class ApprovalStore:

    """In-memory approval state store."""

    def __init__(
        self,
        *,
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        if not callable(clock):
            raise TypeError("Approval store clock must be callable.")
        self._requests: dict[UUID, ApprovalRequest] = {}
        self._lock = RLock()
        self._clock = clock

    def is_expired(self, request: ApprovalRequest) -> bool:
        return (
            request.status is ApprovalStatus.EXPIRED
            or (
                request.expires_at is not None
                and self._clock() >= request.expires_at
            )
        )

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
        if expires_in_seconds is not None:
            if (
                isinstance(expires_in_seconds, bool)
                or not isinstance(expires_in_seconds, (int, float))
                or not math.isfinite(expires_in_seconds)
                or expires_in_seconds <= 0
            ):
                raise ValueError(
                    "Approval expiry must be a finite number greater than 0."
                )

        created_at = self._clock()
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

        with self._lock:
            self._requests[request.operation_id] = request
        return request

    def get(self, operation_id: UUID) -> ApprovalRequest:
        with self._lock:
            try:
                return self._requests[operation_id]
            except KeyError as exc:
                raise KeyError(
                    f"Unknown approval request: {operation_id}"
                ) from exc

    def approve(self, operation_id: UUID) -> ApprovalRequest:
        with self._lock:
            request = self.get(operation_id)

            if self.is_expired(request):
                self._requests[operation_id] = replace(
                    request,
                    status=ApprovalStatus.EXPIRED,
                )
                raise ValueError("Cannot approve an expired request.")

            if request.status is not ApprovalStatus.PENDING:
                raise ValueError(
                    f"Cannot approve request from state "
                    f"{request.status.value}."
                )

            request = replace(request, status=ApprovalStatus.APPROVED)
            self._requests[operation_id] = request
            return request

    def deny(self, operation_id: UUID) -> ApprovalRequest:
        with self._lock:
            request = self.get(operation_id)

            if self.is_expired(request):
                self._requests[operation_id] = replace(
                    request,
                    status=ApprovalStatus.EXPIRED,
                )
                raise ValueError("Cannot deny an expired request.")

            if request.status is not ApprovalStatus.PENDING:
                raise ValueError(
                    f"Cannot deny request from state "
                    f"{request.status.value}."
                )

            request = replace(request, status=ApprovalStatus.DENIED)
            self._requests[operation_id] = request
            return request

    def expire(self, operation_id: UUID) -> ApprovalRequest:
        with self._lock:
            request = self.get(operation_id)

            if request.status in {
                ApprovalStatus.PENDING,
                ApprovalStatus.APPROVED,
            }:
                request = replace(request, status=ApprovalStatus.EXPIRED)
                self._requests[operation_id] = request

            return request

    def list(self) -> list[ApprovalRequest]:
        with self._lock:
            return list(self._requests.values())

    def expire_stale(self) -> tuple[ApprovalRequest, ...]:
        """Expire and return requests whose TTL has elapsed."""
        expired: list[ApprovalRequest] = []
        with self._lock:
            for request in self._requests.values():
                if (
                    self.is_expired(request)
                    and request.status is not ApprovalStatus.EXPIRED
                ):
                    updated = replace(request, status=ApprovalStatus.EXPIRED)
                    self._requests[request.operation_id] = updated
                    expired.append(updated)
        return tuple(expired)


@dataclass(slots=True)
class ApprovalDecision:
    status: ApprovalStatus
    operation_id: UUID | None = None
    message: str = ""
