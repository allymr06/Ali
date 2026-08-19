from __future__ import annotations

from uuid import UUID, uuid4

import pytest

from app.core.models import TaskStatus, TaskStepStatus
from app.tasks.manager import TaskManager

def test_task_manager_creates_task() -> None:
    manager = TaskManager()

    task = manager.create("Test task")

    assert task.goal == "Test task"
    assert task.status is TaskStatus.QUEUED
    assert task.progress == 0.0


def test_task_manager_gets_task() -> None:
    manager = TaskManager()
    task = manager.create("Test task")

    assert manager.get(task.task_id) is task


def test_task_manager_lists_tasks() -> None:
    manager = TaskManager()

    first = manager.create("First")
    second = manager.create("Second")

    assert manager.list() == [first, second]


def test_task_manager_unknown_task_fails() -> None:
    manager = TaskManager()

    with pytest.raises(KeyError):
        manager.get(uuid4())


def test_task_manager_starts_task() -> None:
    manager = TaskManager()
    task = manager.create("Test")

    manager.start(task.task_id)

    assert task.status is TaskStatus.RUNNING


def test_task_manager_completes_task() -> None:
    manager = TaskManager()
    task = manager.create("Test")

    manager.start(task.task_id)
    manager.complete(task.task_id, result="done")

    assert task.status is TaskStatus.COMPLETED
    assert task.progress == 1.0
    assert task.result == "done"


def test_task_manager_fails_task() -> None:
    manager = TaskManager()
    task = manager.create("Test")

    manager.start(task.task_id)
    manager.fail(task.task_id, "boom")

    assert task.status is TaskStatus.FAILED
    assert task.error == "boom"


def test_task_manager_pauses_and_resumes_task() -> None:
    manager = TaskManager()
    task = manager.create("Test")

    manager.start(task.task_id)
    manager.pause(task.task_id)

    assert task.status is TaskStatus.PAUSED

    manager.resume(task.task_id)

    assert task.status is TaskStatus.RUNNING


def test_task_manager_cancels_task() -> None:
    manager = TaskManager()
    task = manager.create("Test")

    manager.start(task.task_id)
    manager.cancel(task.task_id)

    assert task.status is TaskStatus.CANCELLED


def test_task_manager_updates_progress() -> None:
    manager = TaskManager()
    task = manager.create("Test")

    manager.start(task.task_id)
    manager.update_progress(
        task.task_id,
        0.5,
        "Halfway",
    )

    assert task.progress == 0.5
    assert task.current_step == "Halfway"


def test_task_rejects_invalid_progress() -> None:
    manager = TaskManager()
    task = manager.create("Test")

    manager.start(task.task_id)

    with pytest.raises(ValueError):
        manager.update_progress(task.task_id, 1.5)


def test_task_cannot_start_twice() -> None:
    manager = TaskManager()
    task = manager.create("Test")

    manager.start(task.task_id)

    with pytest.raises(ValueError):
        manager.start(task.task_id)


def test_completed_task_cannot_be_restarted() -> None:
    manager = TaskManager()
    task = manager.create("Test")

    manager.start(task.task_id)
    manager.complete(task.task_id)

    with pytest.raises(ValueError):
        manager.start(task.task_id)


def test_cancelled_task_cannot_be_resumed() -> None:
    manager = TaskManager()
    task = manager.create("Test")

    manager.start(task.task_id)
    manager.cancel(task.task_id)

    with pytest.raises(ValueError):
        manager.resume(task.task_id)


def test_cancelled_task_cannot_be_completed() -> None:
    manager = TaskManager()
    task = manager.create("Test")

    manager.start(task.task_id)
    manager.cancel(task.task_id)

    with pytest.raises(ValueError):
        manager.complete(task.task_id)


def test_completed_task_cannot_be_cancelled() -> None:
    manager = TaskManager()
    task = manager.create("Test")

    manager.start(task.task_id)
    manager.complete(task.task_id)

    with pytest.raises(ValueError):
        manager.cancel(task.task_id)

def test_task_manager_adds_step() -> None:
    manager = TaskManager()
    task = manager.create("Build JARVIS")

    step = manager.add_step(task.task_id, "Initialize system")

    assert step.name == "Initialize system"
    assert step.status is TaskStepStatus.QUEUED
    assert step in task.steps


def test_task_manager_rejects_empty_step_name() -> None:
    manager = TaskManager()
    task = manager.create("Build JARVIS")

    with pytest.raises(ValueError, match="step name cannot be empty"):
        manager.add_step(task.task_id, "   ")


def test_task_manager_lists_steps() -> None:
    manager = TaskManager()
    task = manager.create("Build JARVIS")

    first = manager.add_step(task.task_id, "First")
    second = manager.add_step(task.task_id, "Second")

    assert manager.list_steps(task.task_id) == [first, second]


def test_task_manager_gets_step() -> None:
    manager = TaskManager()
    task = manager.create("Build JARVIS")

    step = manager.add_step(task.task_id, "First")

    assert manager.get_step(task.task_id, step.step_id) is step


def test_task_manager_unknown_step_raises() -> None:
    manager = TaskManager()
    task = manager.create("Build JARVIS")

    unknown_id = UUID("00000000-0000-0000-0000-000000000000")

    with pytest.raises(KeyError, match="Unknown task step"):
        manager.get_step(task.task_id, unknown_id)


def test_task_manager_starts_step() -> None:
    manager = TaskManager()
    task = manager.create("Build JARVIS")
    manager.start(task.task_id)

    step = manager.add_step(task.task_id, "Initialize")

    started = manager.start_step(task.task_id, step.step_id)

    assert started.status is TaskStepStatus.RUNNING
    assert task.current_step == "Initialize"


def test_task_manager_cannot_start_step_when_task_not_running() -> None:
    manager = TaskManager()
    task = manager.create("Build JARVIS")
    step = manager.add_step(task.task_id, "Initialize")

    with pytest.raises(ValueError, match="Cannot start step while task"):
        manager.start_step(task.task_id, step.step_id)


def test_task_manager_cannot_start_step_twice() -> None:
    manager = TaskManager()
    task = manager.create("Build JARVIS")
    manager.start(task.task_id)

    step = manager.add_step(task.task_id, "Initialize")
    manager.start_step(task.task_id, step.step_id)

    with pytest.raises(ValueError, match="Cannot start step from state running"):
        manager.start_step(task.task_id, step.step_id)


def test_task_manager_completes_step() -> None:
    manager = TaskManager()
    task = manager.create("Build JARVIS")
    manager.start(task.task_id)

    step = manager.add_step(task.task_id, "Initialize")
    manager.start_step(task.task_id, step.step_id)

    completed = manager.complete_step(
        task.task_id,
        step.step_id,
        result="done",
    )

    assert completed.status is TaskStepStatus.COMPLETED
    assert completed.result == "done"


def test_task_manager_cannot_complete_queued_step() -> None:
    manager = TaskManager()
    task = manager.create("Build JARVIS")
    manager.start(task.task_id)

    step = manager.add_step(task.task_id, "Initialize")

    with pytest.raises(ValueError, match="Cannot complete step from state queued"):
        manager.complete_step(task.task_id, step.step_id)


def test_task_manager_fails_running_step() -> None:
    manager = TaskManager()
    task = manager.create("Build JARVIS")
    manager.start(task.task_id)

    step = manager.add_step(task.task_id, "Initialize")
    manager.start_step(task.task_id, step.step_id)

    failed = manager.fail_step(
        task.task_id,
        step.step_id,
        "Initialization failed",
    )

    assert failed.status is TaskStepStatus.FAILED
    assert failed.error == "Initialization failed"


def test_task_manager_cannot_fail_queued_step() -> None:
    manager = TaskManager()
    task = manager.create("Build JARVIS")
    manager.start(task.task_id)

    step = manager.add_step(task.task_id, "Initialize")

    with pytest.raises(ValueError, match="Cannot fail step from state queued"):
        manager.fail_step(
            task.task_id,
            step.step_id,
            "failed",
        )


def test_task_manager_cancels_step() -> None:
    manager = TaskManager()
    task = manager.create("Build JARVIS")
    manager.start(task.task_id)

    step = manager.add_step(task.task_id, "Initialize")

    cancelled = manager.cancel_step(
        task.task_id,
        step.step_id,
    )

    assert cancelled.status is TaskStepStatus.CANCELLED


def test_task_manager_cannot_cancel_completed_step() -> None:
    manager = TaskManager()
    task = manager.create("Build JARVIS")
    manager.start(task.task_id)

    step = manager.add_step(task.task_id, "Initialize")
    manager.start_step(task.task_id, step.step_id)
    manager.complete_step(task.task_id, step.step_id)

    with pytest.raises(
        ValueError,
        match="Cannot cancel step from state completed",
    ):
        manager.cancel_step(task.task_id, step.step_id)


def test_task_manager_progress_tracks_completed_steps() -> None:
    manager = TaskManager()
    task = manager.create("Build JARVIS")
    manager.start(task.task_id)

    first = manager.add_step(task.task_id, "First")
    second = manager.add_step(task.task_id, "Second")

    manager.start_step(task.task_id, first.step_id)
    manager.complete_step(task.task_id, first.step_id)

    assert task.progress == 0.5

    manager.start_step(task.task_id, second.step_id)
    manager.complete_step(task.task_id, second.step_id)

    assert task.progress == 1.0


def test_task_manager_all_steps_completed_clear_current_step() -> None:
    manager = TaskManager()
    task = manager.create("Build JARVIS")
    manager.start(task.task_id)

    first = manager.add_step(task.task_id, "First")
    second = manager.add_step(task.task_id, "Second")

    manager.start_step(task.task_id, first.step_id)
    manager.complete_step(task.task_id, first.step_id)

    manager.start_step(task.task_id, second.step_id)
    manager.complete_step(task.task_id, second.step_id)

    assert task.current_step is None
    assert task.progress == 1.0


def test_task_manager_cannot_add_step_to_completed_task() -> None:
    manager = TaskManager()
    task = manager.create("Build JARVIS")
    manager.start(task.task_id)
    manager.complete(task.task_id)

    with pytest.raises(
        ValueError,
        match="Cannot add step to task from state completed",
    ):
        manager.add_step(task.task_id, "Too late")


def test_task_manager_cannot_add_step_to_cancelled_task() -> None:
    manager = TaskManager()
    task = manager.create("Build JARVIS")
    manager.cancel(task.task_id)

    with pytest.raises(
        ValueError,
        match="Cannot add step to task from state cancelled",
    ):
        manager.add_step(task.task_id, "Too late")

