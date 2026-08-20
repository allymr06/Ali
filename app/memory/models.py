from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from uuid import UUID, uuid4

from app.core.time import utc_now


class MemoryType(str, Enum):
    FACT = "fact"
    PREFERENCE = "preference"
    GOAL = "goal"
    PROJECT = "project"
    CONTEXT = "context"
    INSTRUCTION = "instruction"


class MemorySource(str, Enum):
    USER = "user"
    INFERENCE = "inference"
    SYSTEM = "system"
    IMPORTED = "imported"


@dataclass(slots=True)
class MemoryEntry:
    """A durable piece of information known by JARVIS."""

    content: str
    memory_type: MemoryType = MemoryType.FACT
    importance: float = 0.5
    confidence: float = 1.0
    source: MemorySource = MemorySource.USER

    memory_id: UUID = field(default_factory=uuid4)
    created_at: datetime = field(default_factory=utc_now)
    updated_at: datetime = field(default_factory=utc_now)
    last_used_at: datetime | None = None

    active: bool = True
    supersedes: UUID | None = None
    superseded_by: UUID | None = None

    metadata: dict[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.content = self.content.strip()

        if not self.content:
            raise ValueError("Memory content cannot be empty.")

        if not 0.0 <= self.importance <= 1.0:
            raise ValueError(
                "Memory importance must be between 0.0 and 1.0."
            )

        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError(
                "Memory confidence must be between 0.0 and 1.0."
            )

    def touch(self) -> None:
        """Mark the memory as recently used."""
        self.last_used_at = utc_now()

    def update_content(self, content: str) -> None:
        """Update the memory content."""
        normalized = content.strip()

        if not normalized:
            raise ValueError("Memory content cannot be empty.")

        self.content = normalized
        self.updated_at = utc_now()

    def update(
        self,
        content: str,
        *,
        importance: float | None = None,
        confidence: float | None = None,
    ) -> None:
        """Update mutable memory attributes."""
        normalized = content.strip()

        if not normalized:
            raise ValueError("Memory content cannot be empty.")

        if importance is not None and not 0.0 <= importance <= 1.0:
            raise ValueError(
                "Memory importance must be between 0.0 and 1.0."
            )

        if confidence is not None and not 0.0 <= confidence <= 1.0:
            raise ValueError(
                "Memory confidence must be between 0.0 and 1.0."
            )

        self.content = normalized

        if importance is not None:
            self.importance = importance

        if confidence is not None:
            self.confidence = confidence

        self.updated_at = utc_now()

    def deactivate(self) -> None:
        """Mark the memory as no longer active."""
        self.active = False
        self.updated_at = utc_now()

    def supersede(self, replacement: MemoryEntry) -> None:
        """Mark this memory as replaced by another memory."""
        self.active = False
        self.superseded_by = replacement.memory_id
        self.updated_at = utc_now()

        replacement.supersedes = self.memory_id
