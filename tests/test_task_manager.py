from __future__ import annotations

from uuid import uuid4

import pytest

from app.core.models import TaskStatus
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
