from __future__ import annotations

from collections.abc import Sequence
from uuid import UUID

from app.memory.base import MemoryStore
from app.memory.models import MemoryEntry, MemoryType


class MemoryManager:
    """
    High-level memory orchestration layer.

    The manager decides how JARVIS interacts with memory while
    delegating actual storage to the MemoryStore abstraction.
    """

    def __init__(self, store: MemoryStore) -> None:
        self._store = store

    def remember(
        self,
        content: str,
        *,
        memory_type: MemoryType = MemoryType.FACT,
        importance: float = 0.5,
    ) -> MemoryEntry:
        """Create and persist a new memory."""
        memory = MemoryEntry(
            content=content,
            memory_type=memory_type,
            importance=importance,
        )

        return self._store.save(memory)

    def recall(
        self,
        query: str,
        *,
        limit: int = 5,
    ) -> Sequence[MemoryEntry]:
        """Retrieve memories relevant to a query."""
        return self._store.search(
            query,
            limit=limit,
        )

    def forget(self, memory_id: UUID) -> MemoryEntry:
        """Remove a memory by ID."""
        return self._store.delete(memory_id)

    def get(self, memory_id: UUID) -> MemoryEntry:
        """Retrieve a specific memory by ID."""
        return self._store.get(memory_id)

    def all(self) -> Sequence[MemoryEntry]:
        """Return all stored memories."""
        return self._store.list_all()

    def count(self) -> int:
        """Return the number of stored memories."""
        return len(self._store)