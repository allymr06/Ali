from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Sequence
from uuid import UUID

from app.core.models import Task


class TaskStore(ABC):
    """Persistence boundary for tracked JARVIS tasks."""

    @abstractmethod
    def save(self, task: Task) -> Task: ...

    @abstractmethod
    def get(self, task_id: UUID) -> Task: ...

    @abstractmethod
    def list(self) -> Sequence[Task]: ...

    @abstractmethod
    def delete(self, task_id: UUID) -> Task: ...

    def close(self) -> None:
        """Release backend resources when applicable."""
