from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from uuid import UUID

from app.core.time import utc_now
from app.memory.base import MemoryStore
from app.memory.models import (
    MemoryEntry,
    MemorySensitivity,
    MemorySource,
    MemoryType,
)
from app.memory.safety import SensitiveDataGuard


class MemoryManager:
    """
    High-level memory orchestration layer.

    The manager controls the lifecycle of memories while
    delegating persistence to the MemoryStore abstraction.
    """

    def __init__(
        self,
        store: MemoryStore,
        *,
        sensitive_data_guard: SensitiveDataGuard | None = None,
    ) -> None:
        self._store = store
        self._sensitive_data_guard = sensitive_data_guard or SensitiveDataGuard()

    def remember(
        self,
        content: str,
        *,
        memory_type: MemoryType = MemoryType.FACT,
        importance: float = 0.5,
        confidence: float = 1.0,
        source: MemorySource = MemorySource.USER,
        source_reference: str | None = None,
        sensitivity: MemorySensitivity = MemorySensitivity.NORMAL,
        expires_at: datetime | None = None,
        metadata: dict[str, object] | None = None,
    ) -> MemoryEntry:
        """Create and persist a new memory."""
        normalized_content = content.strip()
        self._sensitive_data_guard.ensure_safe(normalized_content)

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
            source=source,
            source_reference=source_reference,
            sensitivity=sensitivity,
            expires_at=expires_at,
            metadata=dict(metadata or {}),
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
            self._store.save(memory)

        return results

    def forget(self, memory_id: UUID) -> MemoryEntry:
        """Deactivate a memory."""
        memory = self._store.get(memory_id)
        memory.deactivate()
        self._store.save(memory)

        return memory

    def delete(self, memory_id: UUID) -> MemoryEntry:
        """Permanently delete a memory at the user's request."""
        return self._store.delete(memory_id)

    def conflicts(self, memory_id: UUID) -> Sequence[MemoryEntry]:
        """Find active memories that declare the same subject."""
        memory = self._store.get(memory_id)
        subject = memory.metadata.get("subject")
        if not isinstance(subject, str) or not subject.strip():
            return ()
        normalized_subject = subject.strip().casefold()
        return tuple(
            candidate
            for candidate in self.active()
            if candidate.memory_id != memory.memory_id
            and isinstance(candidate.metadata.get("subject"), str)
            and str(candidate.metadata["subject"]).strip().casefold()
            == normalized_subject
            and candidate.content.casefold() != memory.content.casefold()
        )

    def purge_expired(self, *, now: datetime | None = None) -> tuple[MemoryEntry, ...]:
        """Permanently remove entries whose explicit retention time elapsed."""
        reference_time = now or utc_now()
        expired = tuple(
            memory
            for memory in self._store.list_all()
            if memory.expires_at is not None and memory.expires_at <= reference_time
        )
        for memory in expired:
            self._store.delete(memory.memory_id)
        return expired

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
        self._sensitive_data_guard.ensure_safe(content)

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
        self._sensitive_data_guard.ensure_safe(content)

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

    def close(self) -> None:
        """Release resources owned by the configured store."""
        self._store.close()
