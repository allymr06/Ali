from tests.security_helpers import bound_approval


def test_tool_executor_generates_openai_schema() -> None:
    from app.core.models import ToolDefinition
    from app.tools.executor import ToolExecutor

    executor = ToolExecutor()

    def get_weather(
        city: str,
        days: int = 1,
    ) -> str:
        """Get weather information for a city."""
        return f"{city}: sunny"

    executor.register(
        ToolDefinition(
            name="get_weather",
            description="Get weather information for a city.",
        ),
        get_weather,
    )

    schema = executor.get_openai_tools()

    assert schema == [
        {
            "type": "function",
            "function": {
                "name": "get_weather",
                "description": "Get weather information for a city.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "city": {
                            "type": "string",
                        },
                        "days": {
                            "type": "integer",
                            "default": 1,
                        },
                    },
                    "additionalProperties": False,
                    "required": ["city"],
                },
            },
        }
    ]


def test_tool_executor_generates_schema_for_common_python_types() -> None:
    from typing import Optional

    from app.core.models import ToolDefinition
    from app.tools.executor import ToolExecutor

    executor = ToolExecutor()

    def search(
        query: str,
        limit: int = 10,
        score: float = 0.5,
        enabled: bool = True,
        tags: list[str] | None = None,
        metadata: dict[str, str] | None = None,
    ) -> str:
        return query

    executor.register(
        ToolDefinition(
            name="search",
            description="Search for information.",
        ),
        search,
    )

    schema = executor.get_openai_tools()

    parameters = schema[0]["function"]["parameters"]

    assert parameters["properties"]["query"] == {
        "type": "string",
    }

    assert parameters["properties"]["limit"] == {
        "type": "integer",
        "default": 10,
    }

    assert parameters["properties"]["score"] == {
        "type": "number",
        "default": 0.5,
    }

    assert parameters["properties"]["enabled"] == {
        "type": "boolean",
        "default": True,
    }

    assert parameters["properties"]["tags"] == {
        "anyOf": [
            {
                "type": "array",
                "items": {"type": "string"},
            },
            {"type": "null"},
        ],
        "default": None,
    }

    assert parameters["properties"]["metadata"] == {
        "anyOf": [
            {
                "type": "object",
                "additionalProperties": {"type": "string"},
            },
            {"type": "null"},
        ],
        "default": None,
    }

    assert parameters["required"] == ["query"]


def test_tool_executor_generates_schema_for_optional_types() -> None:
    from app.core.models import ToolDefinition
    from app.tools.executor import ToolExecutor

    executor = ToolExecutor()

    def search(
        query: str,
        tags: list[str] | None = None,
        metadata: dict[str, str] | None = None,
    ) -> str:
        return query

    executor.register(
        ToolDefinition(
            name="search",
            description="Search for information.",
        ),
        search,
    )

    schema = executor.get_openai_tools()
    parameters = schema[0]["function"]["parameters"]

    assert parameters["properties"]["query"] == {
        "type": "string",
    }

    assert parameters["properties"]["tags"] == {
        "anyOf": [
            {
                "type": "array",
                "items": {"type": "string"},
            },
            {"type": "null"},
        ],
        "default": None,
    }

    assert parameters["properties"]["metadata"] == {
        "anyOf": [
            {
                "type": "object",
                "additionalProperties": {"type": "string"},
            },
            {"type": "null"},
        ],
        "default": None,
    }


def test_tool_executor_marks_required_parameters() -> None:
    from app.core.models import ToolDefinition
    from app.tools.executor import ToolExecutor

    executor = ToolExecutor()

    def calculate(
        expression: str,
        precision: int = 2,
    ) -> str:
        return expression

    executor.register(
        ToolDefinition(
            name="calculate",
            description="Calculate an expression.",
        ),
        calculate,
    )

    schema = executor.get_openai_tools()
    parameters = schema[0]["function"]["parameters"]

    assert parameters["required"] == ["expression"]
    assert "precision" not in parameters["required"]


def test_tool_executor_ignores_variadic_parameters() -> None:
    from app.core.models import ToolDefinition
    from app.tools.executor import ToolExecutor

    executor = ToolExecutor()

    def logger(message: str, *args, **kwargs) -> str:
        return message

    executor.register(
        ToolDefinition(
            name="logger",
            description="Log a message.",
        ),
        logger,
    )

    schema = executor.get_openai_tools()
    properties = schema[0]["function"]["parameters"]["properties"]

    assert list(properties) == ["message"]


def test_tool_executor_unknown_tool_returns_failed_result() -> None:
    from app.tools.executor import ToolExecutor
    from app.core.models import ToolExecutionStatus

    executor = ToolExecutor()

    result = executor.execute("does_not_exist")

    assert result.status is ToolExecutionStatus.FAILED
    assert result.tool_name == "does_not_exist"
    assert result.verified is False


def test_tool_executor_blocks_tool_when_permission_is_denied() -> None:
    from app.core.models import (
        RiskLevel,
        ToolDefinition,
        ToolExecutionStatus,
    )
    from app.security.permissions import PermissionEngine
    from app.tools.executor import ToolExecutor

    executor = ToolExecutor(
        permission_engine=PermissionEngine()
    )

    called = {"value": False}

    def dangerous_action() -> str:
        called["value"] = True
        return "executed"

    executor.register(
        ToolDefinition(
            name="dangerous_action",
            description="Dangerous action.",
            risk_level=RiskLevel.CRITICAL,
        ),
        dangerous_action,
    )

    result = executor.execute(
        "dangerous_action"
    )

    assert result.status is ToolExecutionStatus.BLOCKED
    assert called["value"] is False
    assert result.verified is False


def test_tool_executor_requires_confirmation() -> None:
    from app.core.models import (
        ToolDefinition,
        ToolExecutionStatus,
    )
    from app.tools.executor import ToolExecutor

    executor = ToolExecutor()

    called = {"value": False}

    def send_message() -> str:
        called["value"] = True
        return "sent"

    executor.register(
        ToolDefinition(
            name="send_message",
            description="Send a message.",
            requires_confirmation=True,
        ),
        send_message,
    )

    result = executor.execute(
        "send_message"
    )

    assert result.status is ToolExecutionStatus.BLOCKED
    assert called["value"] is False

    result = executor.execute(
        "send_message",
        **bound_approval("send_message"),
    )

    assert result.status is ToolExecutionStatus.SUCCESS
    assert result.data == "sent"
    assert called["value"] is True


def test_tool_executor_captures_handler_exception() -> None:
    from app.core.models import (
        ToolDefinition,
        ToolExecutionStatus,
    )
    from app.tools.executor import ToolExecutor

    executor = ToolExecutor()

    def broken_tool() -> str:
        raise RuntimeError("boom")

    executor.register(
        ToolDefinition(
            name="broken_tool",
            description="Broken tool.",
        ),
        broken_tool,
    )

    result = executor.execute(
        "broken_tool"
    )

    assert result.status is ToolExecutionStatus.FAILED
    assert result.error == "boom"
    assert result.verified is False


async def _async_weather(city: str) -> str:
    return f"{city}: sunny"


def test_tool_executor_supports_async_handlers() -> None:
    import asyncio

    from app.core.models import ToolDefinition
    from app.tools.executor import ToolExecutor

    executor = ToolExecutor()

    executor.register(
        ToolDefinition(
            name="async_weather",
            description="Get weather asynchronously.",
        ),
        _async_weather,
    )

    async def run():
        return await executor.execute(
            "async_weather",
            parameters={"city": "Baku"},
        )

    result = asyncio.run(run())

    assert result.succeeded is True
    assert result.data == "Baku: sunny"


def test_tool_executor_times_out_slow_async_tool() -> None:
    import asyncio

    from app.core.models import ToolDefinition, ToolExecutionStatus
    from app.tools.executor import ToolExecutor

    executor = ToolExecutor()

    async def slow_tool() -> str:
        await asyncio.sleep(0.2)
        return "finished"

    executor.register(
        ToolDefinition(
            name="slow_tool",
            description="A deliberately slow tool.",
            timeout_seconds=0.05,
        ),
        slow_tool,
    )

    async def run():
        return await executor.execute("slow_tool")

    result = asyncio.run(run())

    assert result.status is ToolExecutionStatus.TIMEOUT
    assert result.verified is False
    assert result.tool_name == "slow_tool"
    assert result.finished_at is not None


def test_tool_executor_uses_tool_specific_timeout() -> None:
    import asyncio

    from app.core.models import ToolDefinition
    from app.tools.executor import ToolExecutor

    executor = ToolExecutor()

    async def fast_tool() -> str:
        await asyncio.sleep(0.01)
        return "done"

    executor.register(
        ToolDefinition(
            name="fast_tool",
            description="A fast tool.",
            timeout_seconds=0.5,
        ),
        fast_tool,
    )

    async def run():
        return await executor.execute("fast_tool")

    result = asyncio.run(run())

    assert result.succeeded is True
    assert result.data == "done"


def test_tool_executor_times_out_slow_sync_tool() -> None:
    import time

    from app.core.models import ToolDefinition, ToolExecutionStatus
    from app.tools.executor import ToolExecutor

    executor = ToolExecutor()

    def slow_tool() -> str:
        time.sleep(0.2)
        return "finished"

    executor.register(
        ToolDefinition(
            name="slow_sync_tool",
            description="A deliberately slow synchronous tool.",
            timeout_seconds=0.05,
        ),
        slow_tool,
    )

    result = executor.execute("slow_sync_tool")

    assert result.status is ToolExecutionStatus.TIMEOUT
    assert result.verified is False
    assert result.tool_name == "slow_sync_tool"
    assert result.finished_at is not None


def test_tool_executor_preserves_sync_tool_result() -> None:
    from app.core.models import ToolDefinition
    from app.tools.executor import ToolExecutor

    executor = ToolExecutor()

    def normal_tool(value: str) -> str:
        return value.upper()

    executor.register(
        ToolDefinition(
            name="normal_tool",
            description="A normal synchronous tool.",
        ),
        normal_tool,
    )

    result = executor.execute(
        "normal_tool",
        parameters={"value": "jarvis"},
    )

    assert result.succeeded is True
    assert result.data == "JARVIS"
