from __future__ import annotations

import pytest

from app.core.models import RiskLevel, ToolDefinition
from app.security.permissions import PermissionDecision, PermissionEngine


@pytest.fixture
def engine() -> PermissionEngine:
    return PermissionEngine()


@pytest.mark.parametrize(
    ("risk", "expected"),
    [
        (RiskLevel.READ_ONLY, PermissionDecision.ALLOW),
        (RiskLevel.LOW, PermissionDecision.ALLOW),
        (RiskLevel.MEDIUM, PermissionDecision.CONFIRM),
        (RiskLevel.HIGH, PermissionDecision.CONFIRM),
        (RiskLevel.CRITICAL, PermissionDecision.DENY),
    ],
)
def test_permission_engine_risk_policy(
    engine: PermissionEngine,
    risk: RiskLevel,
    expected: str,
) -> None:
    tool = ToolDefinition(
        name="test_tool",
        description="Test tool",
        risk_level=risk,
    )

    result = engine.evaluate(tool)

    assert result.decision == expected
    assert result.risk_level is risk


def test_read_only_operation_is_allowed(
    engine: PermissionEngine,
) -> None:
    tool = ToolDefinition(
        name="read_file",
        description="Read a file",
        risk_level=RiskLevel.READ_ONLY,
    )

    result = engine.evaluate(tool)

    assert result.allowed is True
    assert result.denied is False
    assert result.requires_confirmation is False


def test_medium_risk_requires_confirmation(
    engine: PermissionEngine,
) -> None:
    tool = ToolDefinition(
        name="modify_file",
        description="Modify a file",
        risk_level=RiskLevel.MEDIUM,
    )

    result = engine.evaluate(tool)

    assert result.requires_confirmation is True
    assert result.allowed is False


def test_critical_operation_is_denied(
    engine: PermissionEngine,
) -> None:
    tool = ToolDefinition(
        name="format_disk",
        description="Format a disk",
        risk_level=RiskLevel.CRITICAL,
    )

    result = engine.evaluate(tool)

    assert result.denied is True
    assert result.allowed is False


def test_explicit_confirmation_requirement_overrides_low_risk(
    engine: PermissionEngine,
) -> None:
    tool = ToolDefinition(
        name="send_message",
        description="Send a message",
        risk_level=RiskLevel.LOW,
        requires_confirmation=True,
    )

    result = engine.evaluate(tool)

    assert result.requires_confirmation is True


def test_result_contains_operation_name(
    engine: PermissionEngine,
) -> None:
    tool = ToolDefinition(
        name="browser",
        description="Browser tool",
        risk_level=RiskLevel.HIGH,
    )

    result = engine.evaluate(
        tool,
        operation="open_external_site",
        parameters={"url": "https://example.com"},
    )

    assert result.operation == "open_external_site"
    assert result.risk_level is RiskLevel.HIGH
    assert result.reason


def test_permission_decision_values() -> None:
    assert PermissionDecision.ALLOW == "allow"
    assert PermissionDecision.CONFIRM == "confirm"
    assert PermissionDecision.DENY == "deny"
