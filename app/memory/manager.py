from __future__ import annotations

from collections.abc import Sequence
from uuid import UUID

from app.memory.base import MemoryStore
from app.memory.models import MemoryEntry, MemoryType


class MemoryManager:
    """
    High-level memory orchestration layer.

    The manager controls the lifecycle of memories while
    delegating persistence to the MemoryStore abstraction.
    """

    def __init__(self, store: MemoryStore) -> None:
        self._store = store

    def remember(
        self,
        content: str,
        *,
        memory_type: MemoryType = MemoryType.FACT,
        importance: float = 0.5,
        confidence: float = 1.0,
    ) -> MemoryEntry:
        """Create and persist a new memory."""
        normalized_content = content.strip()

        for existing in self._store.list_all():
            if (
                existing.active
                and existing.content.casefold() == normalized_content.casefold()
                and existing.memory_type is memory_type
            ):
                return existing

        memory = MemoryEntry(
            content=normalized_content,
            memory_type=memory_type,
            importance=importance,
            confidence=confidence,
        )

        return self._store.save(memory)
    def recall(
        self,
        query: str,
        *,
        limit: int = 5,
    ) -> Sequence[MemoryEntry]:
        """Retrieve memories relevant to a query."""
        results = self._store.search(
            query,
            limit=limit,
        )

        for memory in results:
            memory.touch()

        return results

    def forget(self, memory_id: UUID) -> MemoryEntry:
        """Deactivate a memory."""
        memory = self._store.get(memory_id)
        memory.deactivate()
        self._store.save(memory)

        return memory

    def get(self, memory_id: UUID) -> MemoryEntry:
        """Retrieve a specific memory."""
        return self._store.get(memory_id)

    def update(
        self,
        memory_id: UUID,
        content: str,
        *,
        importance: float | None = None,
        confidence: float | None = None,
    ) -> MemoryEntry:
        """Update an existing memory."""
        memory = self._store.get(memory_id)

        memory.update(
            content,
            importance=importance,
            confidence=confidence,
        )

        self._store.save(memory)

        return memory

    def supersede(
        self,
        memory_id: UUID,
        content: str,
        *,
        memory_type: MemoryType | None = None,
        importance: float | None = None,
        confidence: float = 1.0,
    ) -> MemoryEntry:
        """
        Replace an existing memory with a new one.

        The old memory remains in the store for history but becomes
        inactive and points to its replacement.
        """
        old_memory = self._store.get(memory_id)

        replacement = MemoryEntry(
            content=content,
            memory_type=memory_type or old_memory.memory_type,
            importance=(
                importance
                if importance is not None
                else old_memory.importance
            ),
            confidence=confidence,
            supersedes=old_memory.memory_id,
        )

        old_memory.supersede(replacement)

        self._store.save(old_memory)
        self._store.save(replacement)

        return replacement

    def all(self) -> Sequence[MemoryEntry]:
        """Return all stored memories."""
        return self._store.list_all()

    def active(self) -> Sequence[MemoryEntry]:
        """Return only currently active memories."""
        return tuple(
            memory
            for memory in self._store.list_all()
            if memory.active
        )

    def count(self) -> int:
    	"""Return the number of active memories."""
    	return len(self.active())
