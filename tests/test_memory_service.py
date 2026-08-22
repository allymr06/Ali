from __future__ import annotations

from app.bootstrap import create_application
from app.config.settings import Settings
from app.core.models import RiskLevel, ToolExecutionStatus


def test_bootstrap_registers_memory_control_tools(tmp_path) -> None:
    application = create_application(
        Settings(memory_database_path=str(tmp_path / "memory.sqlite3"))
    )

    assert {
        "list_memories",
        "search_memories",
        "forget_memory",
        "delete_memory",
    }.issubset(application.tool_executor.list_names())
    assert application.tool_executor.get("forget_memory").definition.risk_level is RiskLevel.MEDIUM
    assert application.tool_executor.get("delete_memory").definition.risk_level is RiskLevel.HIGH
    application.memory_manager.close()


def test_memory_service_exposes_source_and_freshness(tmp_path) -> None:
    application = create_application(
        Settings(memory_database_path=str(tmp_path / "memory.sqlite3"))
    )
    memory = application.memory_manager.remember(
        "The project is JARVIS",
        source_reference="request:test",
    )

    records = application.memory_service.search("project")

    assert records[0]["memory_id"] == str(memory.memory_id)
    assert records[0]["source"] == "user"
    assert records[0]["source_reference"] == "request:test"
    assert records[0]["freshness"] == "current"
    application.memory_manager.close()


def test_read_only_memory_tool_executes_without_approval(tmp_path) -> None:
    application = create_application(
        Settings(memory_database_path=str(tmp_path / "memory.sqlite3"))
    )
    application.memory_manager.remember("Visible memory")

    result = application.tool_executor.execute("list_memories")

    assert result.status is ToolExecutionStatus.SUCCESS
    assert result.verified is True
    assert result.data[0]["content"] == "Visible memory"
    application.memory_manager.close()


def test_memory_mutation_tools_require_bound_approval(tmp_path) -> None:
    application = create_application(
        Settings(memory_database_path=str(tmp_path / "memory.sqlite3"))
    )
    memory = application.memory_manager.remember("Protected memory")

    result = application.tool_executor.execute(
        "forget_memory",
        parameters={"memory_id": str(memory.memory_id)},
    )

    assert result.status is ToolExecutionStatus.BLOCKED
    assert application.memory_manager.get(memory.memory_id).active is True
    application.memory_manager.close()
