from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any
from uuid import UUID, uuid4

from app.core.time import utc_now


class RequestSource(str, Enum):
    """Origin of a user request."""

    TEXT = "text"
    VOICE = "voice"
    SYSTEM = "system"
    API = "api"


class TaskStatus(str, Enum):
    """Lifecycle state of a JARVIS task."""

    QUEUED = "queued"
    RUNNING = "running"
    WAITING_FOR_INPUT = "waiting_for_input"
    WAITING_FOR_APPROVAL = "waiting_for_approval"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"



class TaskStepStatus(str, Enum):
    """Lifecycle state of an individual task step."""

    QUEUED = "queued"
    RUNNING = "running"
    WAITING_FOR_INPUT = "waiting_for_input"
    WAITING_FOR_APPROVAL = "waiting_for_approval"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class ToolExecutionStatus(str, Enum):
    """Outcome of a tool execution."""

    SUCCESS = "success"
    FAILED = "failed"
    PARTIAL = "partial"
    BLOCKED = "blocked"
    CANCELLED = "cancelled"
    TIMEOUT = "timeout"
    UNKNOWN = "unknown"


class RiskLevel(str, Enum):
    """Risk classification for operations."""

    READ_ONLY = "read_only"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


@dataclass(slots=True)
class Request:
    """Normalized request entering the JARVIS Core."""

    text: str
    source: RequestSource = RequestSource.TEXT
    request_id: UUID = field(default_factory=uuid4)
    created_at: datetime = field(default_factory=utc_now)
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.text = self.text.strip()

        if not self.text:
            raise ValueError("Request text cannot be empty.")


@dataclass(slots=True)
class Response:
    """Response produced by JARVIS."""

    text: str
    response_id: UUID = field(default_factory=uuid4)
    request_id: UUID | None = None
    created_at: datetime = field(default_factory=utc_now)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class Context:
    """Runtime context available to the orchestration layer."""

    conversation_id: UUID = field(default_factory=uuid4)
    user_id: str = "default"
    active_task_id: UUID | None = None
    values: dict[str, Any] = field(default_factory=dict)
    memories: list[str] = field(default_factory=list)


@dataclass(slots=True)
class TaskStep:
    """A single executable step belonging to a JARVIS task."""

    name: str
    step_id: UUID = field(default_factory=uuid4)
    status: TaskStepStatus = TaskStepStatus.QUEUED
    created_at: datetime = field(default_factory=utc_now)
    updated_at: datetime = field(default_factory=utc_now)
    result: Any = None
    error: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class Task:
    """Persistent representation of a potentially long-running task."""

    goal: str
    task_id: UUID = field(default_factory=uuid4)
    status: TaskStatus = TaskStatus.QUEUED
    created_at: datetime = field(default_factory=utc_now)
    updated_at: datetime = field(default_factory=utc_now)
    current_step: str | None = None
    progress: float = 0.0
    result: Any = None
    error: str | None = None
    steps: list[TaskStep] = field(default_factory=list)

    def update_progress(
        self,
        progress: float,
        current_step: str | None = None,
    ) -> None:
        if not 0.0 <= progress <= 1.0:
            raise ValueError("Progress must be between 0.0 and 1.0.")

        self.progress = progress
        self.current_step = current_step
        self.updated_at = utc_now()


@dataclass(slots=True)
class ToolResult:
    """Structured result returned by every tool."""

    status: ToolExecutionStatus
    tool_name: str
    message: str = ""
    data: Any = None
    error: str | None = None
    execution_id: UUID = field(default_factory=uuid4)
    started_at: datetime | None = None
    finished_at: datetime | None = None
    verified: bool = False

    @property
    def succeeded(self) -> bool:
        return self.status is ToolExecutionStatus.SUCCESS


@dataclass(slots=True)
class PermissionRequest:
    """Represents a permission decision required before an action."""

    operation: str
    risk_level: RiskLevel
    reason: str
    operation_id: UUID = field(default_factory=uuid4)
    parameters: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class ToolDefinition:
    """Describes a tool available to JARVIS."""

    name: str
    description: str
    risk_level: RiskLevel = RiskLevel.READ_ONLY
    requires_confirmation: bool = False
    timeout_seconds: float = 30.0
    metadata: dict[str, Any] = field(default_factory=dict)
