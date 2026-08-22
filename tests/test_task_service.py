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
