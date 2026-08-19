from __future__ import annotations

from threading import RLock
from uuid import UUID

from app.core.models import Task, TaskStatus


class TaskManager:
    """Manage the lifecycle of JARVIS tasks."""

    def __init__(self) -> None:
        self._tasks: dict[UUID, Task] = {}
        self._lock = RLock()

    def create(self, goal: str) -> Task:
        task = Task(goal=goal)

        with self._lock:
            self._tasks[task.task_id] = task

        return task

    def get(self, task_id: UUID) -> Task:
        with self._lock:
            try:
                return self._tasks[task_id]
            except KeyError as exc:
                raise KeyError(
                    f"Unknown task: {task_id}"
                ) from exc

    def list(self) -> list[Task]:
        with self._lock:
            return list(self._tasks.values())

    def start(self, task_id: UUID) -> Task:
        task = self.get(task_id)

        if task.status is not TaskStatus.QUEUED:
            raise ValueError(
                f"Cannot start task from state {task.status.value}."
            )

        task.status = TaskStatus.RUNNING
        task.updated_at = task.updated_at.__class__.now(
            task.updated_at.tzinfo
        )

        return task

    def pause(self, task_id: UUID) -> Task:
        task = self.get(task_id)

        if task.status is not TaskStatus.RUNNING:
            raise ValueError(
                f"Cannot pause task from state {task.status.value}."
            )

        task.status = TaskStatus.PAUSED
        return task

    def resume(self, task_id: UUID) -> Task:
        task = self.get(task_id)

        if task.status is not TaskStatus.PAUSED:
            raise ValueError(
                f"Cannot resume task from state {task.status.value}."
            )

        task.status = TaskStatus.RUNNING
        return task

    def cancel(self, task_id: UUID) -> Task:
        task = self.get(task_id)

        if task.status in (
            TaskStatus.COMPLETED,
            TaskStatus.CANCELLED,
        ):
            raise ValueError(
                f"Cannot cancel task from state {task.status.value}."
            )

        task.status = TaskStatus.CANCELLED
        return task

    def complete(
        self,
        task_id: UUID,
        result: object = None,
    ) -> Task:
        task = self.get(task_id)

        if task.status is not TaskStatus.RUNNING:
            raise ValueError(
                f"Cannot complete task from state {task.status.value}."
            )

        task.status = TaskStatus.COMPLETED
        task.progress = 1.0
        task.result = result
        return task

    def fail(
        self,
        task_id: UUID,
        error: str,
    ) -> Task:
        task = self.get(task_id)

        if task.status not in (
            TaskStatus.RUNNING,
            TaskStatus.PAUSED,
        ):
            raise ValueError(
                f"Cannot fail task from state {task.status.value}."
            )

        task.status = TaskStatus.FAILED
        task.error = error
        return task

    def update_progress(
        self,
        task_id: UUID,
        progress: float,
        current_step: str | None = None,
    ) -> Task:
        task = self.get(task_id)

        if task.status is not TaskStatus.RUNNING:
            raise ValueError(
                f"Cannot update progress from state {task.status.value}."
            )

        task.update_progress(
            progress,
            current_step=current_step,
        )

        return task
