from __future__ import annotations

from collections import deque
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from threading import RLock
from uuid import UUID, uuid4

from app.core.models import RiskLevel, ToolDefinition
from app.core.time import utc_now


class PermissionDecision(str, Enum):
    """Possible outcomes of a permission evaluation."""

    ALLOW = "allow"
    DENY = "deny"
    CONFIRM = "confirm"


@dataclass(frozen=True, slots=True)
class PermissionPolicy:
    """Deterministic mapping from effective risk to a decision."""

    auto_allow: frozenset[RiskLevel] = field(
        default_factory=lambda: frozenset(
            {RiskLevel.READ_ONLY, RiskLevel.LOW}
        )
    )
    require_confirmation: frozenset[RiskLevel] = field(
        default_factory=lambda: frozenset(
            {RiskLevel.MEDIUM, RiskLevel.HIGH}
        )
    )
    deny: frozenset[RiskLevel] = field(
        default_factory=lambda: frozenset({RiskLevel.CRITICAL})
    )

    def __post_init__(self) -> None:
        groups = (
            self.auto_allow,
            self.require_confirmation,
            self.deny,
        )
        if not all(
            isinstance(level, RiskLevel)
            for group in groups
            for level in group
        ):
            raise TypeError("Permission policy groups must contain RiskLevel values.")

        combined = set().union(*groups)
        if combined != set(RiskLevel):
            raise ValueError("Permission policy must classify every risk level.")
        if sum(len(group) for group in groups) != len(combined):
            raise ValueError("Permission policy risk groups cannot overlap.")

    def decision_for(self, risk_level: RiskLevel) -> PermissionDecision:
        if risk_level in self.auto_allow:
            return PermissionDecision.ALLOW
        if risk_level in self.require_confirmation:
            return PermissionDecision.CONFIRM
        return PermissionDecision.DENY


@dataclass(frozen=True, slots=True)
class PermissionScope:
    """Least-privilege restrictions for one execution context."""

    allowed_tools: frozenset[str] | None = None
    denied_tools: frozenset[str] = field(default_factory=frozenset)
    max_risk_level: RiskLevel = RiskLevel.CRITICAL

    def __post_init__(self) -> None:
        if self.allowed_tools is not None:
            object.__setattr__(
                self,
                "allowed_tools",
                self._normalize_names(self.allowed_tools, "allowed_tools"),
            )
        object.__setattr__(
            self,
            "denied_tools",
            self._normalize_names(self.denied_tools, "denied_tools"),
        )
        if not isinstance(self.max_risk_level, RiskLevel):
            raise TypeError("max_risk_level must be a RiskLevel.")

    @staticmethod
    def _normalize_names(values, field_name: str) -> frozenset[str]:
        if isinstance(values, str) or not all(
            isinstance(value, str) for value in values
        ):
            raise TypeError(f"{field_name} must contain tool-name strings.")
        return frozenset(
            value.strip() for value in values if value.strip()
        )


@dataclass(frozen=True, slots=True)
class PermissionResult:
    """Auditable result of a permission evaluation."""

    decision: PermissionDecision
    reason: str
    operation: str
    risk_level: RiskLevel
    parameter_rule: str | None = None
    matched_rules: tuple[str, ...] = ()
    tool_name: str = ""
    declared_risk_level: RiskLevel = RiskLevel.READ_ONLY
    evaluation_id: UUID = field(default_factory=uuid4)
    evaluated_at: datetime = field(default_factory=utc_now)
    policy_revision: int = 0

    @property
    def allowed(self) -> bool:
        return self.decision is PermissionDecision.ALLOW

    @property
    def requires_confirmation(self) -> bool:
        return self.decision is PermissionDecision.CONFIRM

    @property
    def denied(self) -> bool:
        return self.decision is PermissionDecision.DENY


@dataclass(frozen=True, slots=True)
class ParameterPermissionRule:
    """Elevate risk or force a safer decision based on parameters."""

    name: str
    risk_level: RiskLevel
    matches: Callable[[Mapping[str, object]], bool]
    decision: PermissionDecision | None = None
    reason: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.name, str):
            raise TypeError("Parameter rule name must be a string.")
        normalized_name = self.name.strip()
        if not normalized_name:
            raise ValueError("Parameter rule name cannot be empty.")
        object.__setattr__(self, "name", normalized_name)
        if not callable(self.matches):
            raise TypeError("Parameter rule matcher must be callable.")
        if not isinstance(self.risk_level, RiskLevel):
            raise TypeError("Parameter rule risk_level must be a RiskLevel.")
        if self.decision is PermissionDecision.ALLOW:
            raise ValueError("Parameter rules cannot force an allow decision.")
        if self.decision is not None and not isinstance(
            self.decision, PermissionDecision
        ):
            raise TypeError("Parameter rule decision must be a PermissionDecision.")


class PermissionEngine:
    """Thread-safe, fail-closed permission policy and audit boundary."""

    _RISK_ORDER = {
        RiskLevel.READ_ONLY: 0,
        RiskLevel.LOW: 1,
        RiskLevel.MEDIUM: 2,
        RiskLevel.HIGH: 3,
        RiskLevel.CRITICAL: 4,
    }

    def __init__(
        self,
        policy: PermissionPolicy | None = None,
        *,
        audit_capacity: int = 1000,
    ) -> None:
        if audit_capacity < 1:
            raise ValueError("audit_capacity must be at least 1.")
        self._policy = policy or PermissionPolicy()
        self._parameter_rules: dict[str, list[ParameterPermissionRule]] = {}
        self._audit: deque[PermissionResult] = deque(maxlen=audit_capacity)
        self._lock = RLock()
        self._revision = 0

    @property
    def revision(self) -> int:
        with self._lock:
            return self._revision

    @property
    def policy(self) -> PermissionPolicy:
        return self._policy

    def register_parameter_rule(
        self,
        tool_name: str,
        rule: ParameterPermissionRule,
    ) -> None:
        normalized_name = tool_name.strip()
        if not normalized_name:
            raise ValueError("Tool name cannot be empty.")
        if not isinstance(rule, ParameterPermissionRule):
            raise TypeError("rule must be a ParameterPermissionRule.")

        with self._lock:
            rules = self._parameter_rules.setdefault(normalized_name, [])
            if any(existing.name == rule.name for existing in rules):
                raise ValueError(
                    f"Parameter rule '{rule.name}' is already registered "
                    f"for tool '{normalized_name}'."
                )
            rules.append(rule)
            self._revision += 1

    def unregister_parameter_rule(
        self,
        tool_name: str,
        rule_name: str,
    ) -> ParameterPermissionRule:
        normalized_tool = tool_name.strip()
        normalized_rule = rule_name.strip()
        with self._lock:
            rules = self._parameter_rules.get(normalized_tool, [])
            for index, rule in enumerate(rules):
                if rule.name == normalized_rule:
                    removed = rules.pop(index)
                    if not rules:
                        self._parameter_rules.pop(normalized_tool, None)
                    self._revision += 1
                    return removed
        raise KeyError(
            f"Parameter rule '{normalized_rule}' is not registered "
            f"for tool '{normalized_tool}'."
        )

    def list_parameter_rules(
        self,
        tool_name: str,
    ) -> tuple[ParameterPermissionRule, ...]:
        with self._lock:
            return tuple(self._parameter_rules.get(tool_name.strip(), ()))

    def audit_log(self) -> tuple[PermissionResult, ...]:
        with self._lock:
            return tuple(self._audit)

    def _parameter_risk(
        self,
        tool: ToolDefinition,
        parameters: Mapping[str, object],
    ) -> tuple[
        RiskLevel,
        tuple[str, ...],
        PermissionDecision | None,
        str | None,
    ]:
        risk = tool.risk_level
        matched: list[str] = []
        forced_decision: PermissionDecision | None = None
        forced_reason: str | None = None

        with self._lock:
            rules = tuple(self._parameter_rules.get(tool.name, ()))

        for rule in rules:
            try:
                matches = bool(rule.matches(parameters))
            except Exception:
                return (
                    RiskLevel.CRITICAL,
                    (rule.name,),
                    PermissionDecision.DENY,
                    f"Parameter rule '{rule.name}' failed closed.",
                )

            if not matches:
                continue

            matched.append(rule.name)
            if self._RISK_ORDER[rule.risk_level] > self._RISK_ORDER[risk]:
                risk = rule.risk_level
            if rule.decision is PermissionDecision.DENY:
                forced_decision = PermissionDecision.DENY
                forced_reason = rule.reason or (
                    f"Parameter rule '{rule.name}' denied the operation."
                )
            elif (
                rule.decision is PermissionDecision.CONFIRM
                and forced_decision is None
            ):
                forced_decision = PermissionDecision.CONFIRM
                forced_reason = rule.reason or (
                    f"Parameter rule '{rule.name}' requires confirmation."
                )

        return risk, tuple(matched), forced_decision, forced_reason

    def _record(
        self,
        *,
        tool: ToolDefinition,
        operation: str,
        risk: RiskLevel,
        decision: PermissionDecision,
        reason: str,
        matched_rules: tuple[str, ...],
    ) -> PermissionResult:
        result = PermissionResult(
            decision=decision,
            reason=reason,
            operation=operation,
            risk_level=risk,
            parameter_rule=matched_rules[-1] if matched_rules else None,
            matched_rules=matched_rules,
            tool_name=tool.name,
            declared_risk_level=tool.risk_level,
            policy_revision=self.revision,
        )
        with self._lock:
            self._audit.append(result)
        return result

    def evaluate(
        self,
        tool: ToolDefinition,
        *,
        operation: str | None = None,
        parameters: dict[str, object] | None = None,
        scope: PermissionScope | None = None,
    ) -> PermissionResult:
        if not isinstance(tool, ToolDefinition):
            raise TypeError("tool must be a ToolDefinition.")
        if parameters is not None and not isinstance(parameters, dict):
            raise TypeError("parameters must be a dictionary.")
        if scope is not None and not isinstance(scope, PermissionScope):
            raise TypeError("scope must be a PermissionScope.")

        operation_name = (
            operation.strip()
            if isinstance(operation, str) and operation.strip()
            else tool.name
        )
        risk, matched, forced_decision, forced_reason = self._parameter_risk(
            tool,
            parameters or {},
        )

        if scope is not None:
            if tool.name in scope.denied_tools:
                return self._record(
                    tool=tool,
                    operation=operation_name,
                    risk=risk,
                    decision=PermissionDecision.DENY,
                    reason="Tool is denied by the execution scope.",
                    matched_rules=matched,
                )
            if (
                scope.allowed_tools is not None
                and tool.name not in scope.allowed_tools
            ):
                return self._record(
                    tool=tool,
                    operation=operation_name,
                    risk=risk,
                    decision=PermissionDecision.DENY,
                    reason="Tool is outside the allowed execution scope.",
                    matched_rules=matched,
                )
            if self._RISK_ORDER[risk] > self._RISK_ORDER[scope.max_risk_level]:
                return self._record(
                    tool=tool,
                    operation=operation_name,
                    risk=risk,
                    decision=PermissionDecision.DENY,
                    reason="Effective risk exceeds the execution scope.",
                    matched_rules=matched,
                )

        policy_decision = self._policy.decision_for(risk)

        if forced_decision is PermissionDecision.DENY:
            decision = forced_decision
            reason = forced_reason or "Permission rule decision."
        elif policy_decision is PermissionDecision.DENY:
            decision = PermissionDecision.DENY
            reason = (
                f"{risk.value.replace('_', ' ').title()} operations "
                "are denied by policy."
            )
        elif forced_decision is PermissionDecision.CONFIRM:
            decision = forced_decision
            reason = forced_reason or "Permission rule decision."
        elif tool.requires_confirmation:
            decision = PermissionDecision.CONFIRM
            reason = "Tool explicitly requires user confirmation."
        else:
            decision = policy_decision
            if decision is PermissionDecision.ALLOW:
                reason = f"{risk.value.replace('_', ' ').title()} operation."
            elif decision is PermissionDecision.CONFIRM:
                reason = (
                    f"{risk.value.replace('_', ' ').title()} operation "
                    "requires confirmation."
                )
            else:  # pragma: no cover - policy denial handled above
                reason = "Operation denied by policy."

        return self._record(
            tool=tool,
            operation=operation_name,
            risk=risk,
            decision=decision,
            reason=reason,
            matched_rules=matched,
        )
