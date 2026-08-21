from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from app.providers.base import ModelCapabilities, ProviderCapability


class TaskType(str, Enum):
    SIMPLE = "simple"
    STANDARD = "standard"
    COMPLEX = "complex"
    LONG_RUNNING = "long_running"
    VISION = "vision"
    AGENTIC = "agentic"


@dataclass(frozen=True, slots=True)
class ModelProfile:
    """A routable model and its declared operational properties."""

    provider: str
    model: str
    capabilities: ModelCapabilities = field(default_factory=ModelCapabilities)
    task_types: frozenset[TaskType] = field(
        default_factory=lambda: frozenset(TaskType)
    )
    priority: int = 100
    max_context_tokens: int | None = None
    input_cost_per_million: float | None = None
    output_cost_per_million: float | None = None

    def __post_init__(self) -> None:
        if not self.provider.strip():
            raise ValueError("Model profile provider cannot be empty.")
        if not self.model.strip():
            raise ValueError("Model profile model cannot be empty.")
        object.__setattr__(self, "provider", self.provider.strip())
        object.__setattr__(self, "model", self.model.strip())
        if not isinstance(self.capabilities, ModelCapabilities):
            raise TypeError("capabilities must be ModelCapabilities.")
        if not self.task_types:
            raise ValueError("Model profile must support at least one task type.")
        if not all(isinstance(item, TaskType) for item in self.task_types):
            raise TypeError("task_types must contain TaskType values.")
        if self.priority < 0:
            raise ValueError("Model profile priority cannot be negative.")
        if self.max_context_tokens is not None and self.max_context_tokens < 1:
            raise ValueError("max_context_tokens must be positive when set.")
        for value in (
            self.input_cost_per_million,
            self.output_cost_per_million,
        ):
            if value is not None and value < 0:
                raise ValueError("Model cost cannot be negative.")

    def supports(
        self,
        task_type: TaskType,
        required: frozenset[ProviderCapability],
    ) -> bool:
        return (
            task_type in self.task_types
            and self.capabilities.supports(required)
        )


@dataclass(frozen=True, slots=True)
class RoutingDecision:
    provider: str
    model: str | None
    task_type: TaskType
    required_capabilities: frozenset[ProviderCapability]
    reason: str
    fallback_candidates: tuple[tuple[str, str | None], ...] = ()
    user_override: bool = False
