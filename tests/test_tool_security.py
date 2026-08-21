from __future__ import annotations

import asyncio

from app.core.models import ToolDefinition, ToolExecutionStatus
from app.tools.executor import ToolExecutor
from tests.security_helpers import bound_approval


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
    assert result.verified is False


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
        assert result.verified is False

    asyncio.run(run())

# ===== Additional Tool Security Tests =====

def test_executor_cannot_bypass_medium_risk_without_confirmation() -> None:
    called = False

    def dangerous() -> str:
        nonlocal called
        called = True
        return "executed"

    executor = ToolExecutor()

    executor.register(
        ToolDefinition(
            name="dangerous",
            description="Medium-risk operation",
            risk_level=__import__(
                "app.core.models",
                fromlist=["RiskLevel"],
            ).RiskLevel.MEDIUM,
        ),
        dangerous,
    )

    result = executor.execute(
        "dangerous",
        confirmation_granted=False,
    )

    assert result.status is ToolExecutionStatus.BLOCKED
    assert result.verified is False
    assert result.error == "User confirmation required."
    assert called is False


def test_executor_allows_medium_risk_only_with_confirmation() -> None:
    called = False

    def dangerous() -> str:
        nonlocal called
        called = True
        return "executed"

    executor = ToolExecutor()

    executor.register(
        ToolDefinition(
            name="dangerous",
            description="Medium-risk operation",
            risk_level=__import__(
                "app.core.models",
                fromlist=["RiskLevel"],
            ).RiskLevel.MEDIUM,
        ),
        dangerous,
    )

    result = executor.execute(
        "dangerous",
        **bound_approval("dangerous"),
    )

    assert result.status is ToolExecutionStatus.SUCCESS
    assert result.data == "executed"
    assert result.verified is False
    assert called is True


def test_executor_critical_tool_remains_blocked_even_with_confirmation() -> None:
    called = False

    def critical() -> str:
        nonlocal called
        called = True
        return "executed"

    executor = ToolExecutor()

    executor.register(
        ToolDefinition(
            name="critical",
            description="Critical operation",
            risk_level=__import__(
                "app.core.models",
                fromlist=["RiskLevel"],
            ).RiskLevel.CRITICAL,
        ),
        critical,
    )

    result = executor.execute(
        "critical",
        confirmation_granted=True,
    )

    assert result.status is ToolExecutionStatus.BLOCKED
    assert result.verified is False
    assert called is False


def test_executor_explicit_confirmation_requirement_cannot_be_bypassed() -> None:
    called = False

    def protected() -> str:
        nonlocal called
        called = True
        return "executed"

    executor = ToolExecutor()

    executor.register(
        ToolDefinition(
            name="protected",
            description="Protected operation",
            risk_level=__import__(
                "app.core.models",
                fromlist=["RiskLevel"],
            ).RiskLevel.READ_ONLY,
            requires_confirmation=True,
        ),
        protected,
    )

    result = executor.execute(
        "protected",
        confirmation_granted=False,
    )

    assert result.status is ToolExecutionStatus.BLOCKED
    assert result.verified is False
    assert result.error == "User confirmation required."
    assert called is False


def test_executor_explicit_confirmation_requirement_allows_execution_after_confirmation() -> None:
    called = False

    def protected() -> str:
        nonlocal called
        called = True
        return "executed"

    executor = ToolExecutor()

    executor.register(
        ToolDefinition(
            name="protected",
            description="Protected operation",
            risk_level=__import__(
                "app.core.models",
                fromlist=["RiskLevel"],
            ).RiskLevel.READ_ONLY,
            requires_confirmation=True,
        ),
        protected,
    )

    result = executor.execute(
        "protected",
        **bound_approval("protected"),
    )

    assert result.status is ToolExecutionStatus.SUCCESS
    assert result.data == "executed"
    assert result.verified is False
    assert called is True


def test_executor_async_permission_gate_runs_before_async_handler() -> None:
    called = False

    async def dangerous() -> str:
        nonlocal called
        called = True
        return "executed"

    executor = ToolExecutor()

    executor.register(
        ToolDefinition(
            name="async_dangerous",
            description="Async medium-risk operation",
            risk_level=__import__(
                "app.core.models",
                fromlist=["RiskLevel"],
            ).RiskLevel.MEDIUM,
        ),
        dangerous,
    )

    async def run() -> None:
        result = await executor.execute("async_dangerous")

        assert result.status is ToolExecutionStatus.BLOCKED
        assert result.verified is False
        assert result.error == "User confirmation required."
        assert called is False

    asyncio.run(run())

