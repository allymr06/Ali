from __future__ import annotations

from app.bootstrap import create_application
from app.config.settings import Settings
from app.core.models import RiskLevel, ToolExecutionStatus


def create_durable_application(tmp_path):
    return create_application(
        Settings(
            memory_database_path=str(tmp_path / "memory.sqlite3"),
            task_database_path=str(tmp_path / "tasks.sqlite3"),
            task_runtime_directory=str(tmp_path / "runtime"),
            windows_integrations_enabled=False,
        )
    )


def test_bootstrap_registers_durable_task_tools(tmp_path) -> None:
    application = create_durable_application(tmp_path)

    assert {
        "list_tasks",
        "get_task",
        "pause_task",
        "resume_task",
        "cancel_task",
    }.issubset(application.tool_executor.list_names())
    assert application.tool_executor.get("resume_task").definition.risk_level is RiskLevel.MEDIUM
    application.memory_manager.close()
    application.task_manager.close()


def test_task_service_reports_progress_and_steps(tmp_path) -> None:
    application = create_durable_application(tmp_path)
    task = application.task_manager.create("Visible task")
    step = application.task_manager.add_step(task.task_id, "Inspect")
    application.task_manager.start(task.task_id)
    application.task_manager.start_step(task.task_id, step.step_id)
    application.task_manager.complete_step(task.task_id, step.step_id, "done")

    record = application.task_service.get(str(task.task_id))

    assert record["goal"] == "Visible task"
    assert record["progress"] == 1.0
    assert record["steps"][0]["status"] == "completed"
    application.memory_manager.close()
    application.task_manager.close()


def test_read_only_task_tool_executes_without_approval(tmp_path) -> None:
    application = create_durable_application(tmp_path)
    task = application.task_manager.create("Observable")

    result = application.tool_executor.execute("list_tasks")

    assert result.status is ToolExecutionStatus.SUCCESS
    assert result.verified is True
    assert result.data[0]["task_id"] == str(task.task_id)
    application.memory_manager.close()
    application.task_manager.close()


def test_task_control_mutations_require_bound_approval(tmp_path) -> None:
    application = create_durable_application(tmp_path)
    task = application.task_manager.create("Protected control")

    result = application.tool_executor.execute(
        "resume_task",
        parameters={"task_id": str(task.task_id)},
    )

    assert result.status is ToolExecutionStatus.BLOCKED
    assert application.task_manager.get(task.task_id).status.value == "queued"
    application.memory_manager.close()
    application.task_manager.close()


# ---------------------------------------------------------------------------
# audit fixes: a resume tells the truth, and a parked task can be cancelled
# ---------------------------------------------------------------------------


def test_resume_reports_the_outcome_it_actually_reached(tmp_path) -> None:
    """A resume that fails is a failure, not a verified success."""
    import asyncio
    from types import SimpleNamespace
    from uuid import uuid4

    from app.core.models import Task, TaskStatus
    from app.tasks.service import TaskControlService

    def service_for(status: TaskStatus, error: str | None = None):
        task = Task(goal="uzun is", task_id=uuid4())
        task.status = status
        task.error = error
        runtime = SimpleNamespace(
            resume=lambda _identifier: asyncio.sleep(0, result=task),
            is_active=lambda _identifier: False,
        )
        manager = SimpleNamespace(get=lambda _identifier: task)
        return TaskControlService(manager, runtime), task

    service, _task = service_for(TaskStatus.COMPLETED)
    completed = asyncio.run(service.resume(str(uuid4())))
    assert completed.status is ToolExecutionStatus.SUCCESS and completed.verified is True

    service, _task = service_for(TaskStatus.FAILED, "adım patladı")
    failed = asyncio.run(service.resume(str(uuid4())))
    assert failed.status is ToolExecutionStatus.FAILED, "a failed resume is never a success"
    assert failed.verified is False and failed.error == "adım patladı"

    service, _task = service_for(TaskStatus.CANCELLED)
    cancelled = asyncio.run(service.resume(str(uuid4())))
    assert cancelled.status is ToolExecutionStatus.FAILED and cancelled.verified is False

    service, _task = service_for(TaskStatus.PAUSED)
    paused = asyncio.run(service.resume(str(uuid4())))
    assert paused.status is ToolExecutionStatus.PARTIAL, "a resume that parks again is partial"
    assert paused.verified is False and paused.side_effects_may_continue is True


def test_a_task_that_is_not_running_can_still_be_cancelled(tmp_path) -> None:
    """The phone offers cancel for queued and paused tasks; it must work."""
    import asyncio
    from uuid import uuid4

    from app.core.models import TaskStatus
    from app.tasks.manager import TaskManager
    from app.tasks.service import TaskControlService

    manager = TaskManager()
    task = manager.create("beklemedeki iş")
    idle_runtime = type("Idle", (), {"is_active": staticmethod(lambda _identifier: False)})()
    service = TaskControlService(manager, idle_runtime)

    result = asyncio.run(service.cancel(str(task.task_id)))

    assert result.status is ToolExecutionStatus.SUCCESS and result.verified is True
    assert manager.get(task.task_id).status is TaskStatus.CANCELLED

    # A terminal task still refuses, and the refusal is the manager's own.
    try:
        asyncio.run(service.cancel(str(task.task_id)))
    except ValueError as error:
        assert "cancel" in str(error).lower()
    else:  # pragma: no cover - a second cancel must not silently succeed
        raise AssertionError("cancelling a cancelled task must refuse")

    # An unknown id is reported, not silently treated as cancelled.
    try:
        asyncio.run(service.cancel(str(uuid4())))
    except KeyError as error:
        assert "Unknown task" in str(error)
    else:  # pragma: no cover
        raise AssertionError("an unknown task must not report a cancellation")
