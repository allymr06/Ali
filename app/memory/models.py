from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
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


class MemoryFreshness(str, Enum):
    CURRENT = "current"
    STALE = "stale"
    EXPIRED = "expired"


class MemorySensitivity(str, Enum):
    NORMAL = "normal"
    PERSONAL = "personal"
    SECRET = "secret"


@dataclass(slots=True)
class MemoryEntry:
    """A durable piece of information known by JARVIS."""

    content: str
    memory_type: MemoryType = MemoryType.FACT
    importance: float = 0.5
    confidence: float = 1.0
    source: MemorySource = MemorySource.USER
    source_reference: str | None = None
    sensitivity: MemorySensitivity = MemorySensitivity.NORMAL

    memory_id: UUID = field(default_factory=uuid4)
    created_at: datetime = field(default_factory=utc_now)
    updated_at: datetime = field(default_factory=utc_now)
    last_used_at: datetime | None = None
    last_verified_at: datetime | None = None
    expires_at: datetime | None = None

    active: bool = True
    supersedes: UUID | None = None
    superseded_by: UUID | None = None

    metadata: dict[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if isinstance(self.memory_type, str):
            self.memory_type = MemoryType(self.memory_type)
        if isinstance(self.source, str):
            self.source = MemorySource(self.source)
        if isinstance(self.sensitivity, str):
            self.sensitivity = MemorySensitivity(self.sensitivity)
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

        if self.source_reference is not None:
            self.source_reference = self.source_reference.strip() or None

        for field_name in (
            "created_at",
            "updated_at",
            "last_used_at",
            "last_verified_at",
            "expires_at",
        ):
            value = getattr(self, field_name)
            if value is not None and value.tzinfo is None:
                raise ValueError(f"{field_name} must be timezone-aware.")

        if self.expires_at is not None and self.expires_at < self.created_at:
            raise ValueError("expires_at cannot be earlier than created_at.")

        if not isinstance(self.metadata, dict):
            raise TypeError("Memory metadata must be a dictionary.")

    def touch(self) -> None:
        """Mark the memory as recently used."""
        self.last_used_at = utc_now()

    def freshness(
        self,
        *,
        now: datetime | None = None,
        stale_after: timedelta = timedelta(days=90),
    ) -> MemoryFreshness:
        """Return freshness without mutating the memory."""
        reference_time = now or utc_now()
        if self.expires_at is not None and self.expires_at <= reference_time:
            return MemoryFreshness.EXPIRED
        verified_at = self.last_verified_at or self.updated_at
        if reference_time - verified_at > stale_after:
            return MemoryFreshness.STALE
        return MemoryFreshness.CURRENT

    def verify(self, *, confidence: float | None = None) -> None:
        """Record that the memory was checked against its source."""
        if confidence is not None and not 0.0 <= confidence <= 1.0:
            raise ValueError("Memory confidence must be between 0.0 and 1.0.")
        if confidence is not None:
            self.confidence = confidence
        self.last_verified_at = utc_now()
        self.updated_at = self.last_verified_at

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
