from __future__ import annotations

from dataclasses import dataclass, field
from uuid import UUID

from app.execution.models import ExecutionLimits, ExecutionUsage


@dataclass(slots=True)
class ExecutionContext:
    """Structured identity and runtime state for one execution."""

    request_id: UUID | None = None
    conversation_id: UUID | None = None
    task_id: UUID | None = None
    plan_id: UUID | None = None
    current_step_id: UUID | None = None
    current_step_name: str | None = None
    metadata: dict[str, object] = field(default_factory=dict)
    limits: ExecutionLimits = field(default_factory=ExecutionLimits)
    usage: ExecutionUsage = field(default_factory=ExecutionUsage)

    def for_step(
        self,
        *,
        step_id: UUID,
        step_name: str,
    ) -> "ExecutionContext":
        self.current_step_id = step_id
        self.current_step_name = step_name
        return self
