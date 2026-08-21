from __future__ import annotations

import hashlib
import hmac
import json
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from app.core.time import utc_now


@dataclass(frozen=True, slots=True)
class ApprovalGrant:
    """Opaque in-memory proof that an exact action was approved."""

    operation_id: UUID
    binding_digest: str
    expires_at: datetime | None
    task_id: UUID | None


@dataclass(frozen=True, slots=True)
class ApprovalExecutionContext:
    """Identity needed to validate an approval at execution time."""

    task_id: UUID | None
    plan_id: UUID | None
    step_id: UUID


@dataclass(frozen=True, slots=True)
class ApprovalValidation:
    valid: bool
    reason: str


def approval_binding_digest(
    *,
    operation: str,
    tool_name: str,
    parameters: dict[str, object],
    task_id: UUID | None,
    plan_id: UUID | None,
    step_id: UUID,
    tool_version: str | None = None,
) -> str:
    """Create a stable fingerprint for the exact approved action."""
    if not isinstance(operation, str) or not operation.strip():
        raise ValueError("Approval operation cannot be empty.")
    if not isinstance(tool_name, str) or not tool_name.strip():
        raise ValueError("Approval tool_name cannot be empty.")
    if not isinstance(parameters, dict) or not all(
        isinstance(key, str) for key in parameters
    ):
        raise ValueError("Approval parameters must be an object with string keys.")
    if not isinstance(step_id, UUID):
        raise TypeError("Approval step_id must be a UUID.")

    payload = {
        "operation": operation.strip(),
        "tool_name": tool_name.strip(),
        "parameters": parameters,
        "task_id": str(task_id) if task_id is not None else None,
        "plan_id": str(plan_id) if plan_id is not None else None,
        "step_id": str(step_id),
        "tool_version": tool_version,
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


def validate_approval_grant(
    grant: ApprovalGrant | None,
    *,
    operation: str,
    tool_name: str,
    parameters: dict[str, object],
    context: ApprovalExecutionContext | None,
    tool_version: str | None = None,
) -> ApprovalValidation:
    """Validate expiry and exact action identity at the final tool boundary."""
    if grant is None:
        return ApprovalValidation(False, "A bound approval grant is required.")
    if not isinstance(grant, ApprovalGrant):
        return ApprovalValidation(False, "Approval grant type is invalid.")
    if context is None or not isinstance(context, ApprovalExecutionContext):
        return ApprovalValidation(False, "Approval execution context is required.")
    if grant.expires_at is not None:
        if grant.expires_at.tzinfo is None:
            return ApprovalValidation(False, "Approval expiry must be timezone-aware.")
        if utc_now() >= grant.expires_at:
            return ApprovalValidation(False, "Approval grant has expired.")
    if grant.task_id is not None and grant.task_id != context.task_id:
        return ApprovalValidation(False, "Approval task identity does not match.")

    try:
        expected = approval_binding_digest(
            operation=operation,
            tool_name=tool_name,
            parameters=parameters,
            task_id=grant.task_id,
            plan_id=context.plan_id,
            step_id=context.step_id,
            tool_version=tool_version,
        )
    except ValueError as exc:
        return ApprovalValidation(False, str(exc))

    if not hmac.compare_digest(grant.binding_digest, expected):
        return ApprovalValidation(False, "Approval binding does not match the action.")
    return ApprovalValidation(True, "Approval grant is valid.")
