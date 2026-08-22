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
    VISION = "vision"
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
    metadata: dict[str, Any] = field(default_factory=dict)

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
    side_effects_may_continue: bool = False

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
    version: str = "1.0.0"
    capabilities: frozenset[str] = field(default_factory=frozenset)
    tags: frozenset[str] = field(default_factory=frozenset)
    max_concurrency: int | None = None
    retry_max_attempts: int = 1
    retry_backoff_seconds: float = 0.0
    idempotent: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for field_name in ("name", "description", "version"):
            if not isinstance(getattr(self, field_name), str):
                raise TypeError(f"Tool {field_name} must be a string.")

        if not isinstance(self.risk_level, RiskLevel):
            raise TypeError("Tool risk_level must be a RiskLevel.")

        if not isinstance(self.requires_confirmation, bool):
            raise TypeError("Tool requires_confirmation must be a boolean.")

        if (
            isinstance(self.timeout_seconds, bool)
            or not isinstance(self.timeout_seconds, (int, float))
        ):
            raise TypeError("Tool timeout_seconds must be numeric.")

        if isinstance(self.capabilities, str) or not all(
            isinstance(value, str) for value in self.capabilities
        ):
            raise TypeError("Tool capabilities must contain strings.")

        if isinstance(self.tags, str) or not all(
            isinstance(value, str) for value in self.tags
        ):
            raise TypeError("Tool tags must contain strings.")

        if not isinstance(self.metadata, dict):
            raise TypeError("Tool metadata must be a dictionary.")

        if not isinstance(self.idempotent, bool):
            raise TypeError("Tool idempotent must be a boolean.")

        if (
            self.max_concurrency is not None
            and (
                isinstance(self.max_concurrency, bool)
                or not isinstance(self.max_concurrency, int)
            )
        ):
            raise TypeError("Tool max_concurrency must be an integer.")

        if (
            isinstance(self.retry_max_attempts, bool)
            or not isinstance(self.retry_max_attempts, int)
        ):
            raise TypeError("Tool retry_max_attempts must be an integer.")

        if (
            isinstance(self.retry_backoff_seconds, bool)
            or not isinstance(self.retry_backoff_seconds, (int, float))
        ):
            raise TypeError("Tool retry_backoff_seconds must be numeric.")

        self.name = self.name.strip()
        self.description = self.description.strip()
        self.version = self.version.strip()
        self.capabilities = frozenset(
            value.strip().lower()
            for value in self.capabilities
            if value.strip()
        )
        self.tags = frozenset(
            value.strip().lower()
            for value in self.tags
            if value.strip()
        )

        if not self.name:
            raise ValueError("Tool name cannot be empty.")

        if not self.description:
            raise ValueError("Tool description cannot be empty.")

        if not self.version:
            raise ValueError("Tool version cannot be empty.")

        if self.timeout_seconds <= 0:
            raise ValueError("Tool timeout_seconds must be greater than 0.")

        if self.max_concurrency is not None and self.max_concurrency < 1:
            raise ValueError("Tool max_concurrency must be at least 1.")

        if self.retry_max_attempts < 1:
            raise ValueError("Tool retry_max_attempts must be at least 1.")

        if self.retry_backoff_seconds < 0:
            raise ValueError("Tool retry_backoff_seconds cannot be negative.")

        if self.retry_max_attempts > 1 and not self.idempotent:
            raise ValueError(
                "Retryable tools must declare idempotent=True."
            )
