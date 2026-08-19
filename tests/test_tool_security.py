from __future__ import annotations

import asyncio

from app.core.models import ToolDefinition, ToolExecutionStatus
from app.tools.executor import ToolExecutor


def test_executor_rejects_unknown_tool() -> None:
    executor = ToolExecutor()

    result = executor.execute(
        "does_not_exist",
    )

    assert result.status is ToolExecutionStatus.FAILED
    assert result.verified is False
    assert "not registered" in (result.error or "")


def test_executor_rejects_invalid_arguments_before_handler_runs() -> None:
    called = {"value": False}

    def tool(city: str) -> str:
        called["value"] = True
        return city

    executor = ToolExecutor()

    executor.register(
        ToolDefinition(
            name="weather",
            description="Get weather.",
        ),
        tool,
    )

    result = executor.execute(
        "weather",
        parameters={"wrong": "Baku"},
    )

    assert called["value"] is False
    assert result.status is ToolExecutionStatus.FAILED
    assert result.verified is False


def test_executor_accepts_valid_arguments() -> None:
    executor = ToolExecutor()

    executor.register(
        ToolDefinition(
            name="weather",
            description="Get weather.",
        ),
        lambda city: f"{city}: sunny",
    )

    result = executor.execute(
        "weather",
        parameters={"city": "Baku"},
    )

    assert result.status is ToolExecutionStatus.SUCCESS
    assert result.data == "Baku: sunny"
    assert result.verified is True


def test_executor_preserves_permission_gate_before_execution() -> None:
    called = {"value": False}

    def dangerous() -> str:
        called["value"] = True
        return "executed"

    executor = ToolExecutor()

    executor.register(
        ToolDefinition(
            name="dangerous",
            description="Dangerous operation.",
            risk_level=__import__(
                "app.core.models",
                fromlist=["RiskLevel"],
            ).RiskLevel.MEDIUM,
        ),
        dangerous,
    )

    result = executor.execute("dangerous")

    assert result.status is ToolExecutionStatus.BLOCKED
    assert called["value"] is False


def test_executor_async_tool_still_works() -> None:
    async def async_tool(value: int) -> int:
        return value * 2

    executor = ToolExecutor()

    executor.register(
        ToolDefinition(
            name="double",
            description="Double a number.",
        ),
        async_tool,
    )

    async def run() -> None:
        result = await executor.execute(
            "double",
            parameters={"value": 21},
        )

        assert result.status is ToolExecutionStatus.SUCCESS
        assert result.data == 42
        assert result.verified is True

    asyncio.run(run())
