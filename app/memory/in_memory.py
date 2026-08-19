from __future__ import annotations

from threading import RLock
from uuid import UUID

from app.memory.base import MemoryStore
from app.memory.models import MemoryEntry


class InMemoryStore(MemoryStore):
    """
    Thread-safe in-memory memory store.

    Used during development and testing before a persistent
    storage backend is introduced.
    """

    def __init__(self) -> None:
        self._memories: dict[UUID, MemoryEntry] = {}
        self._lock = RLock()

    def save(self, memory: MemoryEntry) -> MemoryEntry:
        with self._lock:
            self._memories[memory.memory_id] = memory
            return memory

    def get(self, memory_id: UUID) -> MemoryEntry:
        with self._lock:
            try:
                return self._memories[memory_id]
            except KeyError as exc:
                raise KeyError(
                    f"Memory '{memory_id}' was not found."
                ) from exc

    def delete(self, memory_id: UUID) -> MemoryEntry:
        with self._lock:
            try:
                return self._memories.pop(memory_id)
            except KeyError as exc:
                raise KeyError(
                    f"Memory '{memory_id}' was not found."
                ) from exc

    def search(
        self,
        query: str,
        *,
        limit: int = 10,
    ) -> list[MemoryEntry]:
        normalized_query = query.strip().lower()

        if not normalized_query:
            return []

        if limit < 1:
            raise ValueError("Search limit must be at least 1.")

        with self._lock:
            matches = [
                memory
                for memory in self._memories.values()
                if normalized_query in memory.content.lower()
            ]

            matches.sort(
                key=lambda memory: (
                    memory.importance,
                    memory.updated_at,
                ),
                reverse=True,
            )

            return matches[:limit]

    def list_all(self) -> list[MemoryEntry]:
        with self._lock:
            return list(self._memories.values())

    def __len__(self) -> int:
        with self._lock:
            return len(self._memories)