from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any
from uuid import UUID, uuid4

from app.core.models import utc_now


class MemoryType(str, Enum):
    """Classification of stored memory."""

    FACT = "fact"
    PREFERENCE = "preference"
    CONVERSATION = "conversation"
    TASK = "task"
    SYSTEM = "system"


@dataclass(slots=True)
class MemoryEntry:
    """
    A single persistent memory item.

    Memory is deliberately provider- and storage-independent.
    """

    content: str
    memory_type: MemoryType = MemoryType.FACT
    memory_id: UUID = field(default_factory=uuid4)
    created_at: datetime = field(default_factory=utc_now)
    updated_at: datetime = field(default_factory=utc_now)
    importance: float = 0.5
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.content = self.content.strip()

        if not self.content:
            raise ValueError("Memory content cannot be empty.")

        if not 0.0 <= self.importance <= 1.0:
            raise ValueError(
                "Memory importance must be between 0.0 and 1.0."
            )

    def update(
        self,
        content: str,
        *,
        importance: float | None = None,
    ) -> None:
        """Update the memory content and optional importance."""
        normalized_content = content.strip()

        if not normalized_content:
            raise ValueError("Memory content cannot be empty.")

        self.content = normalized_content

        if importance is not None:
            if not 0.0 <= importance <= 1.0:
                raise ValueError(
                    "Memory importance must be between 0.0 and 1.0."
                )

            self.importance = importance

        self.updated_at = utc_now()