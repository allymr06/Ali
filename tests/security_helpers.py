from __future__ import annotations

from datetime import timedelta
from uuid import UUID, uuid4

from app.security.approval import (
    ApprovalExecutionContext,
    ApprovalGrant,
    approval_binding_digest,
)
from app.core.time import utc_now


def bound_approval(
    tool_name: str,
    *,
    operation: str | None = None,
    parameters: dict[str, object] | None = None,
    task_id: UUID | None = None,
    plan_id: UUID | None = None,
    request_id: UUID | None = None,
    conversation_id: UUID | None = None,
    tool_version: str = "1.0.0",
) -> dict[str, object]:
    """Build a valid grant/context pair for executor boundary tests."""
    step_id = uuid4()
    active_parameters = dict(parameters or {})
    active_operation = operation or tool_name
    operation_id = uuid4()
    context = ApprovalExecutionContext(
        task_id=task_id,
        plan_id=plan_id,
        step_id=step_id,
        request_id=request_id,
        conversation_id=conversation_id,
        approval_operation_id=operation_id,
    )
    grant = ApprovalGrant(
        operation_id=operation_id,
        binding_digest=approval_binding_digest(
            operation=active_operation,
            tool_name=tool_name,
            parameters=active_parameters,
            task_id=task_id,
            plan_id=plan_id,
            step_id=step_id,
            tool_version=tool_version,
            request_id=request_id,
            conversation_id=conversation_id,
        ),
        expires_at=utc_now() + timedelta(minutes=5),
        task_id=task_id,
    )
    return {
        "approval_grant": grant,
        "approval_context": context,
    }
