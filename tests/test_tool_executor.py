from __future__ import annotations

import pytest

from app.core.models import RiskLevel, ToolDefinition, ToolExecutionStatus
from app.security.permissions import PermissionEngine
from app.tools.executor import ToolExecutor


def test_executor_registers_tool() -> None:
    executor = ToolExecutor()

    executor.register(
        ToolDefinition(
            name="echo",
            description="Echo input",
        ),
        lambda value: value,
    )

    assert len(executor) == 1
    assert executor.contains("echo")
    assert executor.list_names() == ("echo",)


def test_executor_rejects_duplicate_tool() -> None:
    executor = ToolExecutor()

    definition = ToolDefinition(
        name="echo",
        description="Echo input",
    )

    executor.register(definition, lambda: "first")

    with pytest.raises(ValueError):
        executor.register(definition, lambda: "second")


def test_executor_rejects_empty_name() -> None:
    executor = ToolExecutor()

    with pytest.raises(ValueError):
        executor.register(
            ToolDefinition(
                name="   ",
                description="Invalid",
            ),
            lambda: None,
        )


def test_executor_rejects_non_callable_handler() -> None:
    executor = ToolExecutor()

    with pytest.raises(TypeError):
        executor.register(
            ToolDefinition(
                name="bad",
                description="Bad tool",
            ),
            "not-callable",
        )


def test_executor_allows_read_only_tool() -> None:
    executor = ToolExecutor()

    executor.register(
        ToolDefinition(
            name="echo",
            description="Echo input",
            risk_level=RiskLevel.READ_ONLY,
        ),
        lambda value: value,
    )

    result = executor.execute(
        "echo",
        parameters={"value": "hello"},
    )

    assert result.status is ToolExecutionStatus.SUCCESS
    assert result.data == "hello"
    assert result.verified is True


def test_executor_blocks_medium_risk_without_confirmation() -> None:
    called = False

    def dangerous_operation() -> str:
        nonlocal called
        called = True
        return "executed"

    executor = ToolExecutor(
        PermissionEngine(),
    )

    executor.register(
        ToolDefinition(
            name="modify",
            description="Modify something",
            risk_level=RiskLevel.MEDIUM,
        ),
        dangerous_operation,
    )

    result = executor.execute("modify")

    assert result.status is ToolExecutionStatus.BLOCKED
    assert result.verified is False
    assert called is False


def test_executor_runs_medium_risk_with_confirmation() -> None:
    executor = ToolExecutor()

    executor.register(
        ToolDefinition(
            name="modify",
            description="Modify something",
            risk_level=RiskLevel.MEDIUM,
        ),
        lambda: "done",
    )

    result = executor.execute(
        "modify",
        confirmation_granted=True,
    )

    assert result.status is ToolExecutionStatus.SUCCESS
    assert result.data == "done"
    assert result.verified is True


def test_executor_blocks_critical_tool() -> None:
    called = False

    def critical_operation() -> None:
        nonlocal called
        called = True

    executor = ToolExecutor()

    executor.register(
        ToolDefinition(
            name="critical",
            description="Critical operation",
            risk_level=RiskLevel.CRITICAL,
        ),
        critical_operation,
    )

    result = executor.execute(
        "critical",
        confirmation_granted=True,
    )

    assert result.status is ToolExecutionStatus.BLOCKED
    assert called is False


def test_executor_handles_handler_failure() -> None:
    def failing_tool() -> None:
        raise RuntimeError("boom")

    executor = ToolExecutor()

    executor.register(
        ToolDefinition(
            name="fail",
            description="Failing tool",
        ),
        failing_tool,
    )

    result = executor.execute("fail")

    assert result.status is ToolExecutionStatus.FAILED
    assert result.verified is False
    assert result.error == "boom"


def test_executor_unregisters_tool() -> None:
    executor = ToolExecutor()

    executor.register(
        ToolDefinition(
            name="echo",
            description="Echo",
        ),
        lambda: "ok",
    )

    removed = executor.unregister("echo")

    assert removed.definition.name == "echo"
    assert len(executor) == 0


def test_executor_unknown_tool_fails() -> None:
    executor = ToolExecutor()

    with pytest.raises(KeyError):
        executor.execute("unknown")


def test_executor_passes_operation_to_permission_engine() -> None:
    executor = ToolExecutor()

    executor.register(
        ToolDefinition(
            name="browser",
            description="Browser",
            risk_level=RiskLevel.HIGH,
        ),
        lambda: "opened",
    )

    result = executor.execute(
        "browser",
        operation="open_site",
    )

    assert result.status is ToolExecutionStatus.BLOCKED
    assert result.error == "User confirmation required."
