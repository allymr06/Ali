from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any
from uuid import UUID


class AgentMode(str, Enum):
    DIRECT = "direct"
    TASK = "task"


class AgentStatus(str, Enum):
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass(slots=True, frozen=True)
class AgentExecutionResult:
    status: AgentStatus
    response_text: str
    task_id: UUID | None = None
    plan_id: UUID | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
