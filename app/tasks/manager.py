from __future__ import annotations

from threading import RLock
from uuid import UUID

from app.core.models import (
    Task,
    TaskStatus,
    TaskStep,
    TaskStepStatus,
    utc_now,
)


class TaskManager:
    """Manage the lifecycle of JARVIS tasks and their steps."""

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
        task.updated_at = utc_now()

        return task

    def pause(self, task_id: UUID) -> Task:
        task = self.get(task_id)

        if task.status is not TaskStatus.RUNNING:
            raise ValueError(
                f"Cannot pause task from state {task.status.value}."
            )

        task.status = TaskStatus.PAUSED
        task.updated_at = utc_now()
        return task

    def resume(self, task_id: UUID) -> Task:
        task = self.get(task_id)

        if task.status is not TaskStatus.PAUSED:
            raise ValueError(
                f"Cannot resume task from state {task.status.value}."
            )

        task.status = TaskStatus.RUNNING
        task.updated_at = utc_now()
        return task

    def wait_for_input(self, task_id: UUID) -> Task:
        task = self.get(task_id)

        if task.status is not TaskStatus.RUNNING:
            raise ValueError(
                f"Cannot wait for input from state {task.status.value}."
            )

        task.status = TaskStatus.WAITING_FOR_INPUT
        task.updated_at = utc_now()
        return task

    def wait_for_approval(self, task_id: UUID) -> Task:
        task = self.get(task_id)

        if task.status is not TaskStatus.RUNNING:
            raise ValueError(
                f"Cannot wait for approval from state {task.status.value}."
            )

        task.status = TaskStatus.WAITING_FOR_APPROVAL
        task.updated_at = utc_now()
        return task

    def resume_from_input(self, task_id: UUID) -> Task:
        task = self.get(task_id)

        if task.status is not TaskStatus.WAITING_FOR_INPUT:
            raise ValueError(
                f"Cannot resume from state {task.status.value}."
            )

        task.status = TaskStatus.RUNNING
        task.updated_at = utc_now()
        return task

    def resume_from_approval(self, task_id: UUID) -> Task:
        task = self.get(task_id)

        if task.status is not TaskStatus.WAITING_FOR_APPROVAL:
            raise ValueError(
                f"Cannot resume from state {task.status.value}."
            )

        task.status = TaskStatus.RUNNING
        task.updated_at = utc_now()
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
        task.updated_at = utc_now()
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
        task.updated_at = utc_now()
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
        task.updated_at = utc_now()
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

    def add_step(
        self,
        task_id: UUID,
        name: str,
        *,
        metadata: dict[str, object] | None = None,
    ) -> TaskStep:
        task = self.get(task_id)

        if task.status in (
            TaskStatus.COMPLETED,
            TaskStatus.CANCELLED,
            TaskStatus.FAILED,
        ):
            raise ValueError(
                f"Cannot add step to task from state {task.status.value}."
            )

        if not name.strip():
            raise ValueError("Task step name cannot be empty.")

        step = TaskStep(
            name=name.strip(),
            metadata=dict(metadata or {}),
        )

        with self._lock:
            task.steps.append(step)
            task.updated_at = utc_now()

        return step

    def get_step(
        self,
        task_id: UUID,
        step_id: UUID,
    ) -> TaskStep:
        task = self.get(task_id)

        with self._lock:
            for step in task.steps:
                if step.step_id == step_id:
                    return step

        raise KeyError(
            f"Unknown task step: {step_id}"
        )

    def list_steps(
        self,
        task_id: UUID,
    ) -> list[TaskStep]:
        task = self.get(task_id)

        with self._lock:
            return list(task.steps)

    def start_step(
        self,
        task_id: UUID,
        step_id: UUID,
    ) -> TaskStep:
        task = self.get(task_id)
        step = self.get_step(task.task_id, step_id)

        if task.status is not TaskStatus.RUNNING:
            raise ValueError(
                f"Cannot start step while task is "
                f"in state {task.status.value}."
            )

        if step.status is not TaskStepStatus.QUEUED:
            raise ValueError(
                f"Cannot start step from state {step.status.value}."
            )

        step.status = TaskStepStatus.RUNNING
        step.updated_at = utc_now()

        task.current_step = step.name
        task.updated_at = utc_now()

        return step

    def wait_step_for_input(
        self,
        task_id: UUID,
        step_id: UUID,
    ) -> TaskStep:
        task = self.get(task_id)
        step = self.get_step(task.task_id, step_id)

        if step.status is not TaskStepStatus.RUNNING:
            raise ValueError(
                f"Cannot wait for input from state {step.status.value}."
            )

        step.status = TaskStepStatus.WAITING_FOR_INPUT
        step.updated_at = utc_now()
        task.updated_at = utc_now()

        return step

    def wait_step_for_approval(
        self,
        task_id: UUID,
        step_id: UUID,
    ) -> TaskStep:
        task = self.get(task_id)
        step = self.get_step(task.task_id, step_id)

        if step.status is not TaskStepStatus.RUNNING:
            raise ValueError(
                f"Cannot wait for approval from state {step.status.value}."
            )

        step.status = TaskStepStatus.WAITING_FOR_APPROVAL
        step.updated_at = utc_now()
        task.updated_at = utc_now()

        return step

    def pause_step(
        self,
        task_id: UUID,
        step_id: UUID,
    ) -> TaskStep:
        task = self.get(task_id)
        step = self.get_step(task.task_id, step_id)

        if step.status is not TaskStepStatus.RUNNING:
            raise ValueError(
                f"Cannot pause step from state {step.status.value}."
            )

        step.status = TaskStepStatus.PAUSED
        step.updated_at = utc_now()
        task.updated_at = utc_now()

        return step

    def resume_step(
        self,
        task_id: UUID,
        step_id: UUID,
    ) -> TaskStep:
        task = self.get(task_id)
        step = self.get_step(task.task_id, step_id)

        if step.status is not TaskStepStatus.PAUSED:
            raise ValueError(
                f"Cannot resume step from state {step.status.value}."
            )

        step.status = TaskStepStatus.RUNNING
        step.updated_at = utc_now()
        task.updated_at = utc_now()

        return step

    def resume_step_from_input(
        self,
        task_id: UUID,
        step_id: UUID,
    ) -> TaskStep:
        task = self.get(task_id)
        step = self.get_step(task.task_id, step_id)

        if step.status is not TaskStepStatus.WAITING_FOR_INPUT:
            raise ValueError(
                f"Cannot resume step from state {step.status.value}."
            )

        step.status = TaskStepStatus.RUNNING
        step.updated_at = utc_now()
        task.updated_at = utc_now()

        return step

    def resume_step_from_approval(
        self,
        task_id: UUID,
        step_id: UUID,
    ) -> TaskStep:
        task = self.get(task_id)
        step = self.get_step(task.task_id, step_id)

        if step.status is not TaskStepStatus.WAITING_FOR_APPROVAL:
            raise ValueError(
                f"Cannot resume step from state {step.status.value}."
            )

        step.status = TaskStepStatus.RUNNING
        step.updated_at = utc_now()
        task.updated_at = utc_now()

        return step

    def complete_step(
        self,
        task_id: UUID,
        step_id: UUID,
        result: object = None,
    ) -> TaskStep:
        task = self.get(task_id)
        step = self.get_step(task.task_id, step_id)

        if step.status is not TaskStepStatus.RUNNING:
            raise ValueError(
                f"Cannot complete step from state {step.status.value}."
            )

        step.status = TaskStepStatus.COMPLETED
        step.result = result
        step.updated_at = utc_now()

        self._recalculate_progress(task)

        return step

    def fail_step(
        self,
        task_id: UUID,
        step_id: UUID,
        error: str,
    ) -> TaskStep:
        task = self.get(task_id)
        step = self.get_step(task.task_id, step_id)

        if step.status not in (
            TaskStepStatus.RUNNING,
            TaskStepStatus.PAUSED,
        ):
            raise ValueError(
                f"Cannot fail step from state {step.status.value}."
            )

        step.status = TaskStepStatus.FAILED
        step.error = error
        step.updated_at = utc_now()

        task.updated_at = utc_now()

        return step

    def cancel_step(
        self,
        task_id: UUID,
        step_id: UUID,
    ) -> TaskStep:
        task = self.get(task_id)
        step = self.get_step(task.task_id, step_id)

        if step.status in (
            TaskStepStatus.COMPLETED,
            TaskStepStatus.CANCELLED,
        ):
            raise ValueError(
                f"Cannot cancel step from state {step.status.value}."
            )

        step.status = TaskStepStatus.CANCELLED
        step.updated_at = utc_now()
        task.updated_at = utc_now()

        return step

    @staticmethod
    def _recalculate_progress(task: Task) -> None:
        if not task.steps:
            return

        completed = sum(
            step.status is TaskStepStatus.COMPLETED
            for step in task.steps
        )

        task.progress = completed / len(task.steps)
        task.updated_at = utc_now()

        if all(
            step.status is TaskStepStatus.COMPLETED
            for step in task.steps
        ):
            task.current_step = None
