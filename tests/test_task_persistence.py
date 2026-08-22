from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

import pytest

from app.core.models import TaskStatus, TaskStepStatus
from app.core.time import utc_now
from app.tasks.manager import TaskManager
from app.tasks.sqlite import SQLiteTaskStore, TaskPersistenceError


def test_sqlite_task_store_round_trips_task_and_steps(tmp_path) -> None:
    database = tmp_path / "tasks.sqlite3"
    manager = TaskManager(SQLiteTaskStore(database))
    task = manager.create("Build durable agent")
    manager.update_metadata(task.task_id, {"request_id": "request-1"})
    step = manager.add_step(
        task.task_id,
        "Inspect",
        metadata={"plan_step_id": "step-1"},
    )
    manager.start(task.task_id)
    manager.start_step(task.task_id, step.step_id)
    manager.complete_step(task.task_id, step.step_id, result={"verified": True})
    manager.complete(task.task_id, result={"outcome": "completed"})
    manager.close()

    reopened = TaskManager(SQLiteTaskStore(database))
    restored = reopened.get(task.task_id)

    assert restored.status is TaskStatus.COMPLETED
    assert restored.progress == 1.0
    assert restored.result == {"outcome": "completed"}
    assert restored.metadata == {"request_id": "request-1"}
    assert restored.steps[0].status is TaskStepStatus.COMPLETED
    assert restored.steps[0].result == {"verified": True}
    reopened.close()


def test_startup_pauses_interrupted_task_and_step(tmp_path) -> None:
    database = tmp_path / "tasks.sqlite3"
    first = TaskManager(SQLiteTaskStore(database))
    task = first.create("Interrupted work")
    step = first.add_step(task.task_id, "Running step")
    first.start(task.task_id)
    first.start_step(task.task_id, step.step_id)
    first.close()

    second = TaskManager(SQLiteTaskStore(database))
    recovered = second.get(task.task_id)

    assert recovered.status is TaskStatus.PAUSED
    assert recovered.steps[0].status is TaskStepStatus.PAUSED
    assert recovered.metadata["recovery_required"] is True
    assert recovered in second.recoverable()
    second.close()


def test_waiting_tasks_survive_restart_without_state_rewrite(tmp_path) -> None:
    database = tmp_path / "tasks.sqlite3"
    first = TaskManager(SQLiteTaskStore(database))
    task = first.create("Approval-bound work")
    first.start(task.task_id)
    first.wait_for_approval(task.task_id)
    first.close()

    second = TaskManager(SQLiteTaskStore(database))

    assert second.get(task.task_id).status is TaskStatus.WAITING_FOR_APPROVAL
    assert second.recoverable()[0].task_id == task.task_id
    second.close()


def test_sqlite_task_store_supports_concurrent_task_creation(tmp_path) -> None:
    manager = TaskManager(SQLiteTaskStore(tmp_path / "tasks.sqlite3"))

    with ThreadPoolExecutor(max_workers=8) as executor:
        tasks = list(executor.map(lambda index: manager.create(f"Task {index}"), range(100)))

    assert len(manager.list()) == 100
    assert len({task.task_id for task in tasks}) == 100
    manager.close()


def test_unpersistable_task_metadata_fails_closed(tmp_path) -> None:
    manager = TaskManager(SQLiteTaskStore(tmp_path / "tasks.sqlite3"))
    task = manager.create("Strict persistence")

    with pytest.raises(TaskPersistenceError, match="cannot be persisted"):
        manager.update_metadata(task.task_id, {"callable": lambda: None})

    manager.close()


def test_task_timestamps_remain_timezone_aware_after_restart(tmp_path) -> None:
    database = tmp_path / "tasks.sqlite3"
    first = TaskManager(SQLiteTaskStore(database))
    task = first.create("Timezone check")
    first.close()

    second = TaskManager(SQLiteTaskStore(database))
    restored = second.get(task.task_id)

    assert restored.created_at.tzinfo is not None
    assert utc_now() - restored.created_at < timedelta(minutes=1)
    second.close()


def test_subtask_relationship_survives_restart(tmp_path) -> None:
    database = tmp_path / "tasks.sqlite3"
    first = TaskManager(SQLiteTaskStore(database))
    parent = first.create("Parent goal")
    child = first.create_subtask(parent.task_id, "Child goal")
    first.close()

    second = TaskManager(SQLiteTaskStore(database))

    assert second.get(child.task_id).metadata["parent_task_id"] == str(parent.task_id)
    second.close()


def test_failed_metadata_update_rolls_back_in_memory_state(tmp_path) -> None:
    manager = TaskManager(SQLiteTaskStore(tmp_path / "tasks.sqlite3"))
    task = manager.create("Rollback")

    with pytest.raises(TaskPersistenceError):
        manager.update_metadata(task.task_id, {"callable": lambda: None})

    assert "callable" not in manager.get(task.task_id).metadata
    manager.close()


def test_corrupt_task_database_fails_closed(tmp_path) -> None:
    database = tmp_path / "tasks.sqlite3"
    database.write_bytes(b"not sqlite")

    with pytest.raises(TaskPersistenceError, match="unreadable"):
        SQLiteTaskStore(database)


def test_task_database_backup_and_restore(tmp_path) -> None:
    database = tmp_path / "tasks.sqlite3"
    backup = tmp_path / "backups" / "tasks.backup.sqlite3"
    destination = tmp_path / "restored.sqlite3"
    store = SQLiteTaskStore(database)
    manager = TaskManager(store)
    task = manager.create("Recoverable task database")

    assert store.backup_to(backup) == backup.resolve()
    manager.close()

    restored_store = SQLiteTaskStore.restore_from_backup(backup, destination)
    restored = TaskManager(restored_store)
    assert restored.get(task.task_id).goal == "Recoverable task database"
    restored.close()
