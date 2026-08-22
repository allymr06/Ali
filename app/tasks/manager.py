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
from app.tasks.base import TaskStore


class TaskManager:
    """Manage the lifecycle of JARVIS tasks and their steps."""

    def __init__(self, store: TaskStore | None = None) -> None:
        self._store = store
        self._tasks: dict[UUID, Task] = {
            task.task_id: task
            for task in (store.list() if store is not None else ())
        }
        self._lock = RLock()
        self._recover_interrupted_tasks()

    def _persist(self, task: Task) -> None:
        if self._store is not None:
            self._store.save(task)

    def _recover_interrupted_tasks(self) -> None:
        for task in self._tasks.values():
            if task.status is not TaskStatus.RUNNING:
                continue
            task.status = TaskStatus.PAUSED
            task.metadata["recovery_required"] = True
            task.metadata["recovery_reason"] = "interrupted_process"
            task.updated_at = utc_now()
            for step in task.steps:
                if step.status is TaskStepStatus.RUNNING:
                    step.status = TaskStepStatus.PAUSED
                    step.updated_at = task.updated_at
            self._persist(task)

    def create(
        self,
        goal: str,
        *,
        parent_task_id: UUID | None = None,
    ) -> Task:
        if not goal.strip():
            raise ValueError("Task goal cannot be empty.")
        if parent_task_id is not None:
            self.get(parent_task_id)
        task = Task(
            goal=goal.strip(),
            metadata=(
                {"parent_task_id": str(parent_task_id)}
                if parent_task_id is not None
                else {}
            ),
        )

        with self._lock:
            self._tasks[task.task_id] = task
            self._persist(task)

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
        task.metadata.pop("recovery_required", None)
        task.metadata.pop("recovery_reason", None)
        task.updated_at = utc_now()
        self._persist(task)

        return task

    def pause(self, task_id: UUID) -> Task:
        task = self.get(task_id)

        if task.status is not TaskStatus.RUNNING:
            raise ValueError(
                f"Cannot pause task from state {task.status.value}."
            )

        task.status = TaskStatus.PAUSED
        task.updated_at = utc_now()
        self._persist(task)
        return task

    def resume(self, task_id: UUID) -> Task:
        task = self.get(task_id)

        if task.status is not TaskStatus.PAUSED:
            raise ValueError(
                f"Cannot resume task from state {task.status.value}."
            )

        task.status = TaskStatus.RUNNING
        task.updated_at = utc_now()
        self._persist(task)
        return task

    def wait_for_input(self, task_id: UUID) -> Task:
        task = self.get(task_id)

        if task.status is not TaskStatus.RUNNING:
            raise ValueError(
                f"Cannot wait for input from state {task.status.value}."
            )

        task.status = TaskStatus.WAITING_FOR_INPUT
        task.updated_at = utc_now()
        self._persist(task)
        return task

    def wait_for_approval(self, task_id: UUID) -> Task:
        task = self.get(task_id)

        if task.status is not TaskStatus.RUNNING:
            raise ValueError(
                f"Cannot wait for approval from state {task.status.value}."
            )

        task.status = TaskStatus.WAITING_FOR_APPROVAL
        task.updated_at = utc_now()
        self._persist(task)
        return task

    def resume_from_input(self, task_id: UUID) -> Task:
        task = self.get(task_id)

        if task.status is not TaskStatus.WAITING_FOR_INPUT:
            raise ValueError(
                f"Cannot resume from state {task.status.value}."
            )

        task.status = TaskStatus.RUNNING
        task.updated_at = utc_now()
        self._persist(task)
        return task

    def resume_from_approval(self, task_id: UUID) -> Task:
        task = self.get(task_id)

        if task.status is not TaskStatus.WAITING_FOR_APPROVAL:
            raise ValueError(
                f"Cannot resume from state {task.status.value}."
            )

        task.status = TaskStatus.RUNNING
        task.updated_at = utc_now()
        self._persist(task)
        return task

    def cancel(
        self,
        task_id: UUID,
        *,
        result: object = None,
    ) -> Task:
        task = self.get(task_id)

        if task.status in (
            TaskStatus.COMPLETED,
            TaskStatus.CANCELLED,
        ):
            raise ValueError(
                f"Cannot cancel task from state {task.status.value}."
            )

        task.status = TaskStatus.CANCELLED
        task.result = result
        task.updated_at = utc_now()
        self._persist(task)
        return task

    def create_subtask(self, parent_task_id: UUID, goal: str) -> Task:
        """Create a durable child task linked to an existing parent."""
        return self.create(goal, parent_task_id=parent_task_id)

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
        self._persist(task)
        return task

    def fail(
        self,
        task_id: UUID,
        error: str,
        *,
        result: object = None,
    ) -> Task:
        task = self.get(task_id)

        if task.status not in (
            TaskStatus.QUEUED,
            TaskStatus.RUNNING,
            TaskStatus.PAUSED,
        ):
            raise ValueError(
                f"Cannot fail task from state {task.status.value}."
            )

        task.status = TaskStatus.FAILED
        task.error = error
        task.result = result
        task.updated_at = utc_now()
        self._persist(task)
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
        self._persist(task)

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
            self._persist(task)

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
        self._persist(task)

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
        self._persist(task)

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
        self._persist(task)

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
        self._persist(task)

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
        self._persist(task)

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
        self._persist(task)

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
        self._persist(task)

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
        self._persist(task)

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
        self._persist(task)

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
        self._persist(task)

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

    def recoverable(self) -> tuple[Task, ...]:
        """Return tasks paused by restart or waiting on an external decision."""
        with self._lock:
            return tuple(
                task
                for task in self._tasks.values()
                if task.status in {
                    TaskStatus.PAUSED,
                    TaskStatus.WAITING_FOR_INPUT,
                    TaskStatus.WAITING_FOR_APPROVAL,
                }
            )

    def reconcile_plan(self, task_id: UUID, plan) -> Task:
        """Project authoritative recovered plan state onto a durable task."""
        from app.planning.models import PlanStatus, PlanStepStatus

        task = self.get(task_id)
        by_plan_step = {
            str(step.metadata.get("plan_step_id")): step
            for step in task.steps
        }
        status_map = {
            PlanStepStatus.PENDING: TaskStepStatus.QUEUED,
            PlanStepStatus.READY: TaskStepStatus.QUEUED,
            PlanStepStatus.RUNNING: TaskStepStatus.RUNNING,
            PlanStepStatus.COMPLETED: TaskStepStatus.COMPLETED,
            PlanStepStatus.FAILED: TaskStepStatus.FAILED,
            PlanStepStatus.CANCELLED: TaskStepStatus.CANCELLED,
            PlanStepStatus.BLOCKED: TaskStepStatus.PAUSED,
            PlanStepStatus.SKIPPED: TaskStepStatus.CANCELLED,
        }
        for plan_step in plan.steps:
            task_step = by_plan_step.get(str(plan_step.step_id))
            if task_step is None:
                task_step = TaskStep(
                    name=plan_step.name,
                    metadata={"plan_step_id": str(plan_step.step_id)},
                )
                task.steps.append(task_step)
            task_step.status = status_map[plan_step.status]
            task_step.result = plan_step.metadata.get("tool_result")
            task_step.error = plan_step.metadata.get("tool_error")
            task_step.updated_at = utc_now()

        task.progress = plan.progress
        task.current_step = next(
            (
                step.name
                for step in task.steps
                if step.status is TaskStepStatus.RUNNING
            ),
            None,
        )
        if plan.status is PlanStatus.COMPLETED:
            task.status = TaskStatus.COMPLETED
            task.progress = 1.0
        elif plan.status is PlanStatus.FAILED:
            task.status = TaskStatus.FAILED
            task.error = str(
                plan.metadata.get("execution_error", "Plan execution failed.")
            )
        elif plan.status is PlanStatus.CANCELLED:
            task.status = TaskStatus.CANCELLED
        else:
            task.status = TaskStatus.PAUSED
        task.result = {
            "plan_id": str(plan.plan_id),
            "plan_goal": plan.goal,
            "plan_progress": plan.progress,
            "outcome": plan.metadata.get("execution_outcome", plan.status.value),
            "usage": plan.metadata.get("execution_usage", {}),
        }
        task.metadata.pop("recovery_required", None)
        task.metadata.pop("recovery_reason", None)
        task.updated_at = utc_now()
        self._persist(task)
        return task

    def update_metadata(
        self,
        task_id: UUID,
        values: dict[str, object],
    ) -> Task:
        task = self.get(task_id)
        if not isinstance(values, dict) or not all(
            isinstance(key, str) for key in values
        ):
            raise TypeError("Task metadata must use string keys.")
        previous = dict(task.metadata)
        task.metadata.update(values)
        task.updated_at = utc_now()
        try:
            self._persist(task)
        except Exception:
            task.metadata.clear()
            task.metadata.update(previous)
            raise
        return task

    def set_result(self, task_id: UUID, result: object) -> Task:
        task = self.get(task_id)
        task.result = result
        task.updated_at = utc_now()
        self._persist(task)
        return task

    def close(self) -> None:
        if self._store is not None:
            self._store.close()
