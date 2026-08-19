from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from uuid import UUID, uuid4


class PlanStatus(str, Enum):
    """Lifecycle state of a JARVIS plan."""

    DRAFT = "draft"
    READY = "ready"
    RUNNING = "running"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class PlanStepStatus(str, Enum):
    """Lifecycle state of an individual plan step."""

    PENDING = "pending"
    READY = "ready"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    BLOCKED = "blocked"
    SKIPPED = "skipped"


@dataclass(slots=True)
class PlanStep:
    """A single step in an executable plan."""

    name: str
    description: str = ""
    dependencies: list[str] = field(default_factory=list)
    status: PlanStepStatus = PlanStepStatus.PENDING
    step_id: UUID = field(default_factory=uuid4)
    metadata: dict[str, object] = field(default_factory=dict)


@dataclass(slots=True)
class Plan:
    """Executable plan produced by the JARVIS planning layer."""

    goal: str
    steps: list[PlanStep] = field(default_factory=list)
    status: PlanStatus = PlanStatus.DRAFT
    plan_id: UUID = field(default_factory=uuid4)
    metadata: dict[str, object] = field(default_factory=dict)

    def add_step(self, step: PlanStep) -> None:
        if not step.name.strip():
            raise ValueError("Plan step name cannot be empty.")

        self.steps.append(step)

    def get_step(self, name: str) -> PlanStep:
        for step in self.steps:
            if step.name == name:
                return step

        raise KeyError(f"Unknown plan step: {name}")

    @property
    def completed_steps(self) -> int:
        return sum(
            step.status is PlanStepStatus.COMPLETED
            for step in self.steps
        )

    @property
    def progress(self) -> float:
        if not self.steps:
            return 0.0

        return self.completed_steps / len(self.steps)
