from __future__ import annotations

import asyncio

import pytest

from app.core.models import RiskLevel, ToolDefinition, ToolExecutionStatus, ToolResult
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
    assert result.verified is False


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
    assert result.verified is False


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
    from app.core.models import ToolExecutionStatus

    executor = ToolExecutor()

    result = executor.execute("unknown")

    assert result.status is ToolExecutionStatus.FAILED
    assert result.tool_name == "unknown"
    assert result.verified is False
    assert "not registered" in (result.error or "")


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

def test_executor_rejects_invalid_arguments_before_execution() -> None:
    called = False

    def tool(value: int) -> int:
        nonlocal called
        called = True
        return value * 2

    executor = ToolExecutor()

    executor.register(
        ToolDefinition(
            name="typed_tool",
            description="Typed tool",
        ),
        tool,
    )

    result = executor.execute(
        "typed_tool",
        parameters={},
    )

    assert result.status is ToolExecutionStatus.FAILED
    assert result.message == "Invalid tool arguments."
    assert result.verified is False
    assert called is False


def test_executor_accepts_valid_keyword_arguments() -> None:
    executor = ToolExecutor()

    executor.register(
        ToolDefinition(
            name="typed_tool",
            description="Typed tool",
        ),
        lambda value: value * 2,
    )

    result = executor.execute(
        "typed_tool",
        parameters={"value": 21},
    )

    assert result.status is ToolExecutionStatus.SUCCESS
    assert result.data == 42
    assert result.verified is False


def test_executor_rejects_unexpected_arguments() -> None:
    called = False

    def tool(value: int) -> int:
        nonlocal called
        called = True
        return value

    executor = ToolExecutor()

    executor.register(
        ToolDefinition(
            name="typed_tool",
            description="Typed tool",
        ),
        tool,
    )

    result = executor.execute(
        "typed_tool",
        parameters={
            "value": 10,
            "unexpected": True,
        },
    )

    assert result.status is ToolExecutionStatus.FAILED
    assert result.message == "Invalid tool arguments."
    assert called is False
def test_executor_rejects_wrong_argument_type() -> None:
    called = False

    def tool(value: int) -> int:
        nonlocal called
        called = True
        return value * 2

    executor = ToolExecutor()

    executor.register(
        ToolDefinition(
            name="typed_tool",
            description="Typed tool",
        ),
        tool,
    )

    result = executor.execute(
        "typed_tool",
        parameters={"value": "not-an-int"},
    )

    assert result.status is ToolExecutionStatus.FAILED
    assert result.message == "Invalid tool arguments."
    assert result.verified is False
    assert called is False


def test_executor_accepts_matching_argument_type() -> None:
    executor = ToolExecutor()

    executor.register(
        ToolDefinition(
            name="typed_tool",
            description="Typed tool",
        ),
        lambda value: value,
    )

    result = executor.execute(
        "typed_tool",
        parameters={"value": "hello"},
    )

    assert result.status is ToolExecutionStatus.SUCCESS
    assert result.data == "hello"
    assert result.verified is False
def test_executor_normalizes_tool_result() -> None:
    executor = ToolExecutor()

    executor.register(
        ToolDefinition(
            name="echo",
            description="Echo value",
        ),
        lambda value: value,
    )

    result = executor.execute(
        "echo",
        parameters={"value": "hello"},
    )

    assert result.status is ToolExecutionStatus.SUCCESS
    assert result.data == "hello"
    assert result.verified is False
    assert result.finished_at is not None


def test_executor_marks_failed_result_as_unverified() -> None:
    executor = ToolExecutor()

    def failing_tool() -> None:
        raise RuntimeError("tool failure")

    executor.register(
        ToolDefinition(
            name="failing",
            description="Failing tool",
        ),
        failing_tool,
    )

    result = executor.execute("failing")

    assert result.status is ToolExecutionStatus.FAILED
    assert result.verified is False
    assert result.data is None
    assert result.error == "tool failure"


def test_executor_does_not_mark_blocked_tool_as_verified() -> None:
    executor = ToolExecutor()

    executor.register(
        ToolDefinition(
            name="protected",
            description="Protected operation",
            risk_level=RiskLevel.MEDIUM,
        ),
        lambda: "executed",
    )

    result = executor.execute("protected")

    assert result.status is ToolExecutionStatus.BLOCKED
    assert result.verified is False
    assert result.data is None
def test_executor_wraps_existing_tool_result() -> None:
    executor = ToolExecutor()

    def tool() -> ToolResult:
        return ToolResult(
            status=ToolExecutionStatus.SUCCESS,
            tool_name="inner",
            message="Inner result",
            data={"value": 42},
            verified=True,
        )

    executor.register(
        ToolDefinition(
            name="wrapper",
            description="Returns a ToolResult",
        ),
        tool,
    )

    result = executor.execute("wrapper")

    assert result.status is ToolExecutionStatus.SUCCESS
    assert result.data == {"value": 42}
    assert result.verified is True
    assert result.tool_name == "wrapper"


def test_executor_preserves_failed_tool_result() -> None:
    executor = ToolExecutor()

    def tool() -> ToolResult:
        return ToolResult(
            status=ToolExecutionStatus.FAILED,
            tool_name="inner",
            message="Inner failure",
            error="failure",
            verified=False,
        )

    executor.register(
        ToolDefinition(
            name="wrapper",
            description="Returns a failed ToolResult",
        ),
        tool,
    )

    result = executor.execute("wrapper")

    assert result.status is ToolExecutionStatus.FAILED
    assert result.error == "failure"
    assert result.verified is False
    assert result.tool_name == "wrapper"


def test_executor_does_not_trust_unverified_tool_result() -> None:
    executor = ToolExecutor()

    def tool() -> ToolResult:
        return ToolResult(
            status=ToolExecutionStatus.SUCCESS,
            tool_name="inner",
            message="Unverified success",
            data="value",
            verified=False,
        )

    executor.register(
        ToolDefinition(
            name="wrapper",
            description="Returns an unverified result",
        ),
        tool,
    )

    result = executor.execute("wrapper")

    assert result.status is ToolExecutionStatus.SUCCESS
    assert result.data == "value"
    assert result.verified is False
# ===== JARVIS EXECUTOR HARDENING TESTS =====

def test_executor_async_validates_arguments_before_handler_runs() -> None:
    called = False

    async def tool(value: int) -> int:
        nonlocal called
        called = True
        return value * 2

    executor = ToolExecutor()

    executor.register(
        ToolDefinition(
            name="async_typed",
            description="Async typed tool",
        ),
        tool,
    )

    async def run() -> None:
        result = await executor.execute(
            "async_typed",
            parameters={"value": "wrong"},
        )

        assert result.status is ToolExecutionStatus.FAILED
        assert result.message == "Invalid tool arguments."
        assert result.verified is False
        assert called is False

    asyncio.run(run())


def test_executor_async_rejects_missing_required_arguments() -> None:
    called = False

    async def tool(value: int) -> int:
        nonlocal called
        called = True
        return value

    executor = ToolExecutor()

    executor.register(
        ToolDefinition(
            name="async_required",
            description="Async required argument tool",
        ),
        tool,
    )

    async def run() -> None:
        result = await executor.execute(
            "async_required",
            parameters={},
        )

        assert result.status is ToolExecutionStatus.FAILED
        assert result.message == "Invalid tool arguments."
        assert called is False

    asyncio.run(run())


def test_executor_async_rejects_unexpected_arguments_before_handler_runs() -> None:
    called = False

    async def tool(value: int) -> int:
        nonlocal called
        called = True
        return value

    executor = ToolExecutor()

    executor.register(
        ToolDefinition(
            name="async_unexpected",
            description="Async unexpected argument tool",
        ),
        tool,
    )

    async def run() -> None:
        result = await executor.execute(
            "async_unexpected",
            parameters={
                "value": 10,
                "unexpected": True,
            },
        )

        assert result.status is ToolExecutionStatus.FAILED
        assert result.message == "Invalid tool arguments."
        assert called is False

    asyncio.run(run())


def test_executor_async_preserves_failed_tool_result() -> None:
    async def tool() -> ToolResult:
        return ToolResult(
            status=ToolExecutionStatus.FAILED,
            tool_name="inner",
            message="Inner failure",
            error="failure",
            verified=False,
        )

    executor = ToolExecutor()

    executor.register(
        ToolDefinition(
            name="async_failed_result",
            description="Async failed result",
        ),
        tool,
    )

    async def run() -> None:
        result = await executor.execute("async_failed_result")

        assert result.status is ToolExecutionStatus.FAILED
        assert result.error == "failure"
        assert result.verified is False
        assert result.tool_name == "async_failed_result"

    asyncio.run(run())


def test_executor_async_timeout_returns_timeout_result() -> None:
    async def slow_tool() -> str:
        await asyncio.sleep(0.2)
        return "finished"

    executor = ToolExecutor()

    executor.register(
        ToolDefinition(
            name="slow_async",
            description="Slow async tool",
            timeout_seconds=0.01,
        ),
        slow_tool,
    )

    async def run() -> None:
        result = await executor.execute("slow_async")

        assert result.status is ToolExecutionStatus.TIMEOUT
        assert result.verified is False
        assert result.error is not None
        assert "timeout" in result.error.lower()

    asyncio.run(run())


def test_executor_sync_timeout_returns_timeout_result() -> None:
    import time

    def slow_tool() -> str:
        time.sleep(0.2)
        return "finished"

    executor = ToolExecutor()

    executor.register(
        ToolDefinition(
            name="slow_sync",
            description="Slow sync tool",
            timeout_seconds=0.01,
        ),
        slow_tool,
    )

    result = executor.execute("slow_sync")

    assert result.status is ToolExecutionStatus.TIMEOUT
    assert result.verified is False
    assert result.error is not None
    assert "timeout" in result.error.lower()


def test_executor_does_not_execute_blocked_tool_with_confirmation_false() -> None:
    called = False

    def tool() -> str:
        nonlocal called
        called = True
        return "executed"

    executor = ToolExecutor()

    executor.register(
        ToolDefinition(
            name="protected",
            description="Protected tool",
            risk_level=RiskLevel.HIGH,
        ),
        tool,
    )

    result = executor.execute(
        "protected",
        confirmation_granted=False,
    )

    assert result.status is ToolExecutionStatus.BLOCKED
    assert result.verified is False
    assert called is False


def test_executor_confirmation_allows_high_risk_tool() -> None:
    executor = ToolExecutor()

    executor.register(
        ToolDefinition(
            name="high_risk",
            description="High risk tool",
            risk_level=RiskLevel.HIGH,
        ),
        lambda: "executed",
    )

    result = executor.execute(
        "high_risk",
        confirmation_granted=True,
    )

    assert result.status is ToolExecutionStatus.SUCCESS
    assert result.data == "executed"
    assert result.verified is False


def test_executor_preserves_tool_result_data() -> None:
    executor = ToolExecutor()

    def tool() -> ToolResult:
        return ToolResult(
            status=ToolExecutionStatus.SUCCESS,
            tool_name="wrong_name",
            message="Success",
            data={"answer": 42},
            verified=False,
        )

    executor.register(
        ToolDefinition(
            name="result_tool",
            description="Result tool",
        ),
        tool,
    )

    result = executor.execute("result_tool")

    assert result.status is ToolExecutionStatus.SUCCESS
    assert result.tool_name == "result_tool"
    assert result.data == {"answer": 42}
    assert result.verified is False


def test_executor_openai_schema_contains_required_arguments() -> None:
    executor = ToolExecutor()

    def weather(city: str, days: int) -> str:
        return f"{city}: {days}"

    executor.register(
        ToolDefinition(
            name="weather",
            description="Get weather",
        ),
        weather,
    )

    tools = executor.get_openai_tools()

    assert len(tools) == 1

    function = tools[0]["function"]

    assert function["name"] == "weather"
    assert function["description"] == "Get weather"
    assert function["parameters"]["type"] == "object"
    assert function["parameters"]["properties"]["city"]["type"] == "string"
    assert function["parameters"]["properties"]["days"]["type"] == "integer"
    assert function["parameters"]["required"] == ["city", "days"]


def test_executor_openai_schema_handles_optional_argument() -> None:
    executor = ToolExecutor()

    def search(query: str, limit: int = 10) -> str:
        return query

    executor.register(
        ToolDefinition(
            name="search",
            description="Search",
        ),
        search,
    )

    tools = executor.get_openai_tools()

    parameters = tools[0]["function"]["parameters"]

    assert parameters["properties"]["query"]["type"] == "string"
    assert parameters["properties"]["limit"]["type"] == "integer"
    assert parameters["properties"]["limit"]["default"] == 10
    assert parameters["required"] == ["query"]


def test_executor_openai_schema_handles_list_and_dict() -> None:
    executor = ToolExecutor()

    def process(
        names: list[str],
        metadata: dict[str, int],
    ) -> str:
        return "ok"

    executor.register(
        ToolDefinition(
            name="process",
            description="Process data",
        ),
        process,
    )

    tools = executor.get_openai_tools()

    properties = tools[0]["function"]["parameters"]["properties"]

    assert properties["names"]["type"] == "array"
    assert properties["names"]["items"]["type"] == "string"

    assert properties["metadata"]["type"] == "object"
    assert properties["metadata"]["additionalProperties"]["type"] == "integer"


def test_executor_openai_schema_skips_variadic_parameters() -> None:
    executor = ToolExecutor()

    def tool(
        value: str,
        *args: str,
        **kwargs: str,
    ) -> str:
        return value

    executor.register(
        ToolDefinition(
            name="variadic",
            description="Variadic tool",
        ),
        tool,
    )

    tools = executor.get_openai_tools()

    properties = tools[0]["function"]["parameters"]["properties"]

    assert list(properties) == ["value"]
    assert properties["value"]["type"] == "string"


def test_executor_normalizes_tool_name_on_lookup() -> None:
    executor = ToolExecutor()

    executor.register(
        ToolDefinition(
            name="echo",
            description="Echo",
        ),
        lambda: "ok",
    )

    result = executor.execute("  echo  ")

    assert result.status is ToolExecutionStatus.SUCCESS
    assert result.tool_name == "echo"
    assert result.data == "ok"


def test_executor_unregistration_returns_original_registered_tool() -> None:
    executor = ToolExecutor()

    definition = ToolDefinition(
        name="echo",
        description="Echo",
    )

    handler = lambda: "ok"

    executor.register(definition, handler)

    removed = executor.unregister("  echo  ")

    assert removed.definition is definition
    assert removed.handler is handler
    assert not executor.contains("echo")


def test_executor_permission_check_happens_before_argument_validation() -> None:
    called = False

    def dangerous(value: int) -> int:
        nonlocal called
        called = True
        return value

    executor = ToolExecutor()

    executor.register(
        ToolDefinition(
            name="dangerous",
            description="Dangerous",
            risk_level=RiskLevel.MEDIUM,
        ),
        dangerous,
    )

    result = executor.execute(
        "dangerous",
        parameters={"value": "invalid"},
    )

    assert result.status is ToolExecutionStatus.BLOCKED
    assert result.error == "User confirmation required."
    assert called is False


def test_executor_async_permission_check_happens_before_handler() -> None:
    called = False

    async def dangerous() -> str:
        nonlocal called
        called = True
        return "executed"

    executor = ToolExecutor()

    executor.register(
        ToolDefinition(
            name="async_dangerous",
            description="Async dangerous",
            risk_level=RiskLevel.MEDIUM,
        ),
        dangerous,
    )

    async def run() -> None:
        result = await executor.execute("async_dangerous")

        assert result.status is ToolExecutionStatus.BLOCKED
        assert result.error == "User confirmation required."
        assert called is False

    asyncio.run(run())




# ===== Additional ToolExecutor tests =====


