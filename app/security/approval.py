from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID


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
