from __future__ import annotations

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

    @property
    def allowed(self) -> bool:
        return self.decision == PermissionDecision.ALLOW

    @property
    def requires_confirmation(self) -> bool:
        return self.decision == PermissionDecision.CONFIRM

    @property
    def denied(self) -> bool:
        return self.decision == PermissionDecision.DENY


class PermissionEngine:
    """
    Central security gate for JARVIS tool execution.

    Permission decisions are deterministic and independent from
    the language model.
    """

    def evaluate(
        self,
        tool: ToolDefinition,
        *,
        operation: str | None = None,
        parameters: dict[str, object] | None = None,
    ) -> PermissionResult:
        operation_name = operation or tool.name
        risk = tool.risk_level

        if tool.requires_confirmation:
            return PermissionResult(
                decision=PermissionDecision.CONFIRM,
                reason="Tool explicitly requires user confirmation.",
                operation=operation_name,
                risk_level=risk,
            )

        if risk is RiskLevel.READ_ONLY:
            return PermissionResult(
                decision=PermissionDecision.ALLOW,
                reason="Read-only operation.",
                operation=operation_name,
                risk_level=risk,
            )

        if risk is RiskLevel.LOW:
            return PermissionResult(
                decision=PermissionDecision.ALLOW,
                reason="Low-risk operation.",
                operation=operation_name,
                risk_level=risk,
            )

        if risk is RiskLevel.MEDIUM:
            return PermissionResult(
                decision=PermissionDecision.CONFIRM,
                reason="Medium-risk operation requires confirmation.",
                operation=operation_name,
                risk_level=risk,
            )

        if risk is RiskLevel.HIGH:
            return PermissionResult(
                decision=PermissionDecision.CONFIRM,
                reason="High-risk operation requires confirmation.",
                operation=operation_name,
                risk_level=risk,
            )

        if risk is RiskLevel.CRITICAL:
            return PermissionResult(
                decision=PermissionDecision.DENY,
                reason="Critical-risk operations are denied by default.",
                operation=operation_name,
                risk_level=risk,
            )

        return PermissionResult(
            decision=PermissionDecision.DENY,
            reason="Unknown risk level.",
            operation=operation_name,
            risk_level=risk,
        )
