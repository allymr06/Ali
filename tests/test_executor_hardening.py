from __future__ import annotations

import asyncio
import threading
import time
from typing import Annotated, Literal

from app.core.models import (
    RiskLevel,
    ToolDefinition,
    ToolExecutionStatus,
    ToolResult,
)
from app.tools.executor import ToolExecutor


def test_executor_rejects_async_handler_from_sync_api() -> None:
    async def tool() -> str:
        return "async"

    executor = ToolExecutor()
    executor.register(
        ToolDefinition(name="async_tool", description="Async tool"),
        tool,
    )

    result = executor.execute("async_tool")

    assert result.status is ToolExecutionStatus.FAILED
    assert result.verified is False
    assert "asynchronous" in (result.error or "").lower()


def test_executor_async_handler_works_through_async_api() -> None:
    async def tool(value: int) -> int:
        await asyncio.sleep(0)
        return value * 2

    executor = ToolExecutor()
    executor.register(
        ToolDefinition(name="async_tool", description="Async tool"),
        tool,
    )

    async def run() -> None:
        result = await executor.execute(
            "async_tool",
            parameters={"value": 21},
        )

        assert result.status is ToolExecutionStatus.SUCCESS
        assert result.data == 42
        assert result.verified is True

    asyncio.run(run())


def test_executor_rejects_none_for_non_optional_argument() -> None:
    called = False

    def tool(value: int) -> int:
        nonlocal called
        called = True
        return value

    executor = ToolExecutor()
    executor.register(
        ToolDefinition(name="typed", description="Typed tool"),
        tool,
    )

    result = executor.execute(
        "typed",
        parameters={"value": None},
    )

    assert result.status is ToolExecutionStatus.FAILED
    assert result.verified is False
    assert called is False


def test_executor_accepts_none_for_optional_argument() -> None:
    def tool(value: int | None = None) -> str:
        return "none" if value is None else str(value)

    executor = ToolExecutor()
    executor.register(
        ToolDefinition(name="optional", description="Optional tool"),
        tool,
    )

    result = executor.execute(
        "optional",
        parameters={"value": None},
    )

    assert result.status is ToolExecutionStatus.SUCCESS
    assert result.data == "none"
    assert result.verified is True


def test_executor_validates_nested_list_types() -> None:
    called = False

    def tool(values: list[int]) -> int:
        nonlocal called
        called = True
        return sum(values)

    executor = ToolExecutor()
    executor.register(
        ToolDefinition(name="nested", description="Nested types"),
        tool,
    )

    result = executor.execute(
        "nested",
        parameters={"values": [1, 2, "bad"]},
    )

    assert result.status is ToolExecutionStatus.FAILED
    assert result.verified is False
    assert called is False


def test_executor_validates_nested_dict_types() -> None:
    called = False

    def tool(metadata: dict[str, int]) -> int:
        nonlocal called
        called = True
        return sum(metadata.values())

    executor = ToolExecutor()
    executor.register(
        ToolDefinition(name="nested_dict", description="Nested dict"),
        tool,
    )

    result = executor.execute(
        "nested_dict",
        parameters={"metadata": {"ok": 1, "bad": "x"}},
    )

    assert result.status is ToolExecutionStatus.FAILED
    assert result.verified is False
    assert called is False


def test_executor_validates_tuple_types() -> None:
    called = False

    def tool(values: tuple[int, str]) -> str:
        nonlocal called
        called = True
        return f"{values[0]}:{values[1]}"

    executor = ToolExecutor()
    executor.register(
        ToolDefinition(name="tuple_tool", description="Tuple tool"),
        tool,
    )

    result = executor.execute(
        "tuple_tool",
        parameters={"values": (1, 2)},
    )

    assert result.status is ToolExecutionStatus.FAILED
    assert result.verified is False
    assert called is False


def test_executor_validates_literal_values() -> None:
    called = False

    def tool(mode: Literal["safe", "fast"]) -> str:
        nonlocal called
        called = True
        return mode

    executor = ToolExecutor()
    executor.register(
        ToolDefinition(name="literal", description="Literal tool"),
        tool,
    )

    result = executor.execute(
        "literal",
        parameters={"mode": "dangerous"},
    )

    assert result.status is ToolExecutionStatus.FAILED
    assert result.verified is False
    assert called is False


def test_executor_accepts_valid_literal_value() -> None:
    def tool(mode: Literal["safe", "fast"]) -> str:
        return mode

    executor = ToolExecutor()
    executor.register(
        ToolDefinition(name="literal", description="Literal tool"),
        tool,
    )

    result = executor.execute(
        "literal",
        parameters={"mode": "safe"},
    )

    assert result.status is ToolExecutionStatus.SUCCESS
    assert result.data == "safe"
    assert result.verified is True


def test_executor_validates_annotated_type() -> None:
    called = False

    def tool(value: Annotated[int, "positive"]) -> int:
        nonlocal called
        called = True
        return value

    executor = ToolExecutor()
    executor.register(
        ToolDefinition(name="annotated", description="Annotated tool"),
        tool,
    )

    result = executor.execute(
        "annotated",
        parameters={"value": "wrong"},
    )

    assert result.status is ToolExecutionStatus.FAILED
    assert result.verified is False
    assert called is False


def test_executor_permission_gate_precedes_type_validation_for_high_risk() -> None:
    called = False

    def tool(value: int) -> int:
        nonlocal called
        called = True
        return value

    executor = ToolExecutor()
    executor.register(
        ToolDefinition(
            name="protected",
            description="Protected",
            risk_level=RiskLevel.HIGH,
        ),
        tool,
    )

    result = executor.execute(
        "protected",
        parameters={"value": "wrong"},
    )

    assert result.status is ToolExecutionStatus.BLOCKED
    assert result.error == "User confirmation required."
    assert called is False


def test_executor_critical_tool_cannot_be_forced_by_confirmation() -> None:
    called = False

    def tool() -> str:
        nonlocal called
        called = True
        return "executed"

    executor = ToolExecutor()
    executor.register(
        ToolDefinition(
            name="critical",
            description="Critical",
            risk_level=RiskLevel.CRITICAL,
        ),
        tool,
    )

    result = executor.execute(
        "critical",
        confirmation_granted=True,
    )

    assert result.status is ToolExecutionStatus.BLOCKED
    assert result.verified is False
    assert called is False


def test_executor_unverified_success_result_becomes_verified() -> None:
    def tool() -> ToolResult:
        return ToolResult(
            status=ToolExecutionStatus.SUCCESS,
            tool_name="fake",
            message="Success",
            data="payload",
            verified=False,
        )

    executor = ToolExecutor()
    executor.register(
        ToolDefinition(name="trust", description="Trust boundary"),
        tool,
    )

    result = executor.execute("trust")

    assert result.status is ToolExecutionStatus.SUCCESS
    assert result.data == "payload"
    assert result.tool_name == "trust"
    assert result.verified is True


def test_executor_failed_result_cannot_become_verified() -> None:
    def tool() -> ToolResult:
        return ToolResult(
            status=ToolExecutionStatus.FAILED,
            tool_name="fake",
            message="Failure",
            error="boom",
            verified=True,
        )

    executor = ToolExecutor()
    executor.register(
        ToolDefinition(name="trust", description="Trust boundary"),
        tool,
    )

    result = executor.execute("trust")

    assert result.status is ToolExecutionStatus.FAILED
    assert result.error == "boom"
    assert result.verified is False


def test_executor_timeout_does_not_report_success() -> None:
    def slow_tool() -> str:
        time.sleep(0.2)
        return "finished"

    executor = ToolExecutor()
    executor.register(
        ToolDefinition(
            name="timeout",
            description="Timeout",
            timeout_seconds=0.01,
        ),
        slow_tool,
    )

    result = executor.execute("timeout")

    assert result.status is ToolExecutionStatus.TIMEOUT
    assert result.verified is False
    assert result.data is None


def test_executor_async_timeout_does_not_report_success() -> None:
    async def slow_tool() -> str:
        await asyncio.sleep(0.2)
        return "finished"

    executor = ToolExecutor()
    executor.register(
        ToolDefinition(
            name="async_timeout",
            description="Async timeout",
            timeout_seconds=0.01,
        ),
        slow_tool,
    )

    async def run() -> None:
        result = await executor.execute("async_timeout")

        assert result.status is ToolExecutionStatus.TIMEOUT
        assert result.verified is False
        assert result.data is None

    asyncio.run(run())


def test_executor_sync_handler_runs_in_worker_thread() -> None:
    main_thread = threading.current_thread().name

    def tool() -> str:
        return threading.current_thread().name

    executor = ToolExecutor()
    executor.register(
        ToolDefinition(name="threaded", description="Threaded"),
        tool,
    )

    result = executor.execute("threaded")

    assert result.status is ToolExecutionStatus.SUCCESS
    assert result.data != main_thread


def test_executor_preserves_timezone_aware_timestamps() -> None:
    executor = ToolExecutor()
    executor.register(
        ToolDefinition(name="time", description="Time"),
        lambda: "ok",
    )

    result = executor.execute("time")

    assert result.started_at is not None
    assert result.finished_at is not None
    assert result.started_at.tzinfo is not None
    assert result.finished_at.tzinfo is not None


def test_executor_openai_schema_preserves_literal_enum() -> None:
    def tool(mode: Literal["safe", "fast"]) -> str:
        return mode

    executor = ToolExecutor()
    executor.register(
        ToolDefinition(name="literal_schema", description="Literal schema"),
        tool,
    )

    schema = executor.get_openai_tools()[0]["function"]["parameters"]

    assert schema["properties"]["mode"]["enum"] == ["safe", "fast"]
    assert schema["required"] == ["mode"]


def test_executor_openai_schema_handles_optional_union() -> None:
    def tool(value: int | None = None) -> str:
        return str(value)

    executor = ToolExecutor()
    executor.register(
        ToolDefinition(name="optional_schema", description="Optional schema"),
        tool,
    )

    schema = executor.get_openai_tools()[0]["function"]["parameters"]

    assert schema["properties"]["value"]["type"] == "integer"
    assert schema["properties"]["value"]["default"] is None
    assert "required" not in schema


def test_executor_does_not_execute_after_invalid_parameter_container() -> None:
    called = False

    def tool(value: int) -> int:
        nonlocal called
        called = True
        return value

    executor = ToolExecutor()
    executor.register(
        ToolDefinition(name="invalid_container", description="Invalid"),
        tool,
    )

    result = executor.execute(
        "invalid_container",
        parameters=["not", "a", "dict"],
    )

    assert result.status is ToolExecutionStatus.FAILED
    assert result.verified is False
    assert called is False
