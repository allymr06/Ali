from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass

from app.core.models import PermissionRequest, RiskLevel, ToolDefinition


class PermissionDecision(str):
    """Possible outcomes of a permission evaluation."""

    ALLOW = "allow"
    DENY = "deny"
    CONFIRM = "confirm"


@dataclass(frozen=True, slots=True)
class PermissionResult:
    """Result of a permission evaluation."""

    decision: str
    reason: str
    operation: str
    risk_level: RiskLevel
    parameter_rule: str | None = None

    @property
    def allowed(self) -> bool:
        return self.decision == PermissionDecision.ALLOW

    @property
    def requires_confirmation(self) -> bool:
        return self.decision == PermissionDecision.CONFIRM

    @property
    def denied(self) -> bool:
        return self.decision == PermissionDecision.DENY


@dataclass(frozen=True, slots=True)
class ParameterPermissionRule:
    """Elevate a tool's risk when trusted code matches its parameters."""

    name: str
    risk_level: RiskLevel
    matches: Callable[[Mapping[str, object]], bool]

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ValueError("Parameter rule name cannot be empty.")

        if not callable(self.matches):
            raise TypeError("Parameter rule matcher must be callable.")

        if not isinstance(self.risk_level, RiskLevel):
            raise TypeError("Parameter rule risk_level must be a RiskLevel.")


class PermissionEngine:
    """
    Central security gate for JARVIS tool execution.

    Permission decisions are deterministic and independent from
    the language model.
    """

    _RISK_ORDER = {
        RiskLevel.READ_ONLY: 0,
        RiskLevel.LOW: 1,
        RiskLevel.MEDIUM: 2,
        RiskLevel.HIGH: 3,
        RiskLevel.CRITICAL: 4,
    }

    def __init__(self) -> None:
        self._parameter_rules: dict[str, list[ParameterPermissionRule]] = {}

    def register_parameter_rule(
        self,
        tool_name: str,
        rule: ParameterPermissionRule,
    ) -> None:
        """Register a trusted parameter-sensitive permission rule."""
        normalized_name = tool_name.strip()

        if not normalized_name:
            raise ValueError("Tool name cannot be empty.")

        self._parameter_rules.setdefault(normalized_name, []).append(rule)

    def _parameter_risk(
        self,
        tool: ToolDefinition,
        parameters: Mapping[str, object],
    ) -> tuple[RiskLevel, str | None, str | None]:
        risk = tool.risk_level
        matched_rule: str | None = None

        for rule in self._parameter_rules.get(tool.name, ()):
            try:
                matches = bool(rule.matches(parameters))
            except Exception as exc:
                return (
                    RiskLevel.CRITICAL,
                    rule.name,
                    f"Parameter rule '{rule.name}' failed closed: {exc}",
                )

            if (
                matches
                and self._RISK_ORDER[rule.risk_level]
                > self._RISK_ORDER[risk]
            ):
                risk = rule.risk_level
                matched_rule = rule.name

        return risk, matched_rule, None

    def evaluate(
        self,
        tool: ToolDefinition,
        *,
        operation: str | None = None,
        parameters: dict[str, object] | None = None,
    ) -> PermissionResult:
        operation_name = operation or tool.name
        risk, matched_rule, rule_error = self._parameter_risk(
            tool,
            parameters or {},
        )

        if rule_error is not None:
            return PermissionResult(
                decision=PermissionDecision.DENY,
                reason=rule_error,
                operation=operation_name,
                risk_level=risk,
                parameter_rule=matched_rule,
            )

        if risk is RiskLevel.CRITICAL:
            return PermissionResult(
                decision=PermissionDecision.DENY,
                reason="Critical-risk operations are denied by default.",
                operation=operation_name,
                risk_level=risk,
                parameter_rule=matched_rule,
            )

        if tool.requires_confirmation:
            return PermissionResult(
                decision=PermissionDecision.CONFIRM,
                reason="Tool explicitly requires user confirmation.",
                operation=operation_name,
                risk_level=risk,
                parameter_rule=matched_rule,
            )

        if risk is RiskLevel.READ_ONLY:
            return PermissionResult(
                decision=PermissionDecision.ALLOW,
                reason="Read-only operation.",
                operation=operation_name,
                risk_level=risk,
                parameter_rule=matched_rule,
            )

        if risk is RiskLevel.LOW:
            return PermissionResult(
                decision=PermissionDecision.ALLOW,
                reason="Low-risk operation.",
                operation=operation_name,
                risk_level=risk,
                parameter_rule=matched_rule,
            )

        if risk is RiskLevel.MEDIUM:
            return PermissionResult(
                decision=PermissionDecision.CONFIRM,
                reason="Medium-risk operation requires confirmation.",
                operation=operation_name,
                risk_level=risk,
                parameter_rule=matched_rule,
            )

        if risk is RiskLevel.HIGH:
            return PermissionResult(
                decision=PermissionDecision.CONFIRM,
                reason="High-risk operation requires confirmation.",
                operation=operation_name,
                risk_level=risk,
                parameter_rule=matched_rule,
            )

        return PermissionResult(
            decision=PermissionDecision.DENY,
            reason="Unknown risk level.",
            operation=operation_name,
            risk_level=risk,
            parameter_rule=matched_rule,
        )
