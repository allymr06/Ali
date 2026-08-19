from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Sequence
from uuid import UUID

from app.memory.models import MemoryEntry


class MemoryStore(ABC):
    """
    Provider-independent interface for JARVIS memory storage.

    Core systems depend on this abstraction rather than a specific
    database or storage engine.
    """

    @abstractmethod
    def save(self, memory: MemoryEntry) -> MemoryEntry:
        """Persist a memory entry."""
        raise NotImplementedError

    @abstractmethod
    def get(self, memory_id: UUID) -> MemoryEntry:
        """Retrieve a memory by ID."""
        raise NotImplementedError

    @abstractmethod
    def delete(self, memory_id: UUID) -> MemoryEntry:
        """Delete and return a memory."""
        raise NotImplementedError

    @abstractmethod
    def search(
        self,
        query: str,
        *,
        limit: int = 10,
    ) -> Sequence[MemoryEntry]:
        """Search stored memories."""
        raise NotImplementedError

    @abstractmethod
    def list_all(self) -> Sequence[MemoryEntry]:
        """Return all stored memories."""
        raise NotImplementedError

    @abstractmethod
    def __len__(self) -> int:
        """Return the number of stored memories."""
        raise NotImplementedError