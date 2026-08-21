from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import FrozenInstanceError
from datetime import timedelta
from uuid import uuid4

import pytest

from app.agent.approval import ApprovalStatus, ApprovalStore
from app.agent.approval_gate import ApprovalGate, ApprovalGateResult
from app.core.models import RiskLevel, ToolDefinition, ToolExecutionStatus
from app.core.time import utc_now
from app.planning.models import PlanStep
from app.security.approval import (
    ApprovalExecutionContext,
    ApprovalGrant,
    approval_binding_digest,
    validate_approval_grant,
)
from app.security.permissions import (
    ParameterPermissionRule,
    PermissionDecision,
    PermissionEngine,
    PermissionPolicy,
    PermissionScope,
)
from app.tools.executor import ToolExecutor
from tests.security_helpers import bound_approval


def test_permission_policy_requires_complete_non_overlapping_classification() -> None:
    with pytest.raises(ValueError):
        PermissionPolicy(
            auto_allow=frozenset({RiskLevel.READ_ONLY}),
            require_confirmation=frozenset({RiskLevel.MEDIUM}),
            deny=frozenset({RiskLevel.CRITICAL}),
        )

    with pytest.raises(ValueError):
        PermissionPolicy(
            auto_allow=frozenset({RiskLevel.READ_ONLY, RiskLevel.LOW}),
            require_confirmation=frozenset(
                {RiskLevel.LOW, RiskLevel.MEDIUM, RiskLevel.HIGH}
            ),
            deny=frozenset({RiskLevel.CRITICAL}),
        )


def test_permission_scope_enforces_tool_allowlist_denylist_and_risk_ceiling() -> None:
    engine = PermissionEngine()
    low = ToolDefinition(name="low", description="Low", risk_level=RiskLevel.LOW)
    high = ToolDefinition(
        name="high",
        description="High",
        risk_level=RiskLevel.HIGH,
    )

    outside = engine.evaluate(
        low,
        scope=PermissionScope(allowed_tools=frozenset({"other"})),
    )
    denied = engine.evaluate(
        low,
        scope=PermissionScope(denied_tools=frozenset({"low"})),
    )
    excessive = engine.evaluate(
        high,
        scope=PermissionScope(max_risk_level=RiskLevel.MEDIUM),
    )

    assert outside.denied and "outside" in outside.reason
    assert denied.denied and "denied" in denied.reason
    assert excessive.denied and "exceeds" in excessive.reason


def test_parameter_rule_can_force_denial_but_never_force_allow() -> None:
    with pytest.raises(ValueError):
        ParameterPermissionRule(
            name="unsafe-allow",
            risk_level=RiskLevel.READ_ONLY,
            matches=lambda _: True,
            decision=PermissionDecision.ALLOW,
        )

    engine = PermissionEngine()
    engine.register_parameter_rule(
        "file",
        ParameterPermissionRule(
            name="protected-path",
            risk_level=RiskLevel.CRITICAL,
            matches=lambda values: values.get("path") == "system",
            decision=PermissionDecision.DENY,
            reason="Protected path.",
        ),
    )
    result = engine.evaluate(
        ToolDefinition(name="file", description="File"),
        parameters={"path": "system"},
    )

    assert result.denied
    assert result.reason == "Protected path."
    assert result.matched_rules == ("protected-path",)


def test_permission_rule_lifecycle_revision_and_bounded_audit() -> None:
    engine = PermissionEngine(audit_capacity=2)
    rule = ParameterPermissionRule(
        name="confirm-large",
        risk_level=RiskLevel.HIGH,
        matches=lambda values: bool(values.get("large")),
    )
    engine.register_parameter_rule("copy", rule)
    assert engine.revision == 1
    assert engine.list_parameter_rules("copy") == (rule,)
    with pytest.raises(ValueError):
        engine.register_parameter_rule("copy", rule)

    tool = ToolDefinition(name="copy", description="Copy")
    engine.evaluate(tool, parameters={"large": False})
    engine.evaluate(tool, parameters={"large": True})
    final = engine.evaluate(tool, parameters={"large": False})

    audit = engine.audit_log()
    assert len(audit) == 2
    assert audit[-1].evaluation_id == final.evaluation_id
    assert audit[-1].evaluated_at.tzinfo is not None
    assert audit[-1].policy_revision == 1

    assert engine.unregister_parameter_rule("copy", "confirm-large") is rule
    assert engine.revision == 2


def test_approval_grant_validation_binds_every_execution_identity() -> None:
    task_id = uuid4()
    plan_id = uuid4()
    step_id = uuid4()
    parameters = {"path": "a.txt"}
    context = ApprovalExecutionContext(task_id, plan_id, step_id)
    grant = ApprovalGrant(
        operation_id=uuid4(),
        binding_digest=approval_binding_digest(
            operation="delete",
            tool_name="file",
            parameters=parameters,
            task_id=task_id,
            plan_id=plan_id,
            step_id=step_id,
            tool_version="1.0.0",
        ),
        expires_at=utc_now() + timedelta(minutes=1),
        task_id=task_id,
    )

    assert validate_approval_grant(
        grant,
        operation="delete",
        tool_name="file",
        parameters=parameters,
        context=context,
        tool_version="1.0.0",
    ).valid
    assert not validate_approval_grant(
        grant,
        operation="delete",
        tool_name="file",
        parameters={"path": "b.txt"},
        context=context,
        tool_version="1.0.0",
    ).valid
    assert not validate_approval_grant(
        grant,
        operation="delete",
        tool_name="file",
        parameters=parameters,
        context=ApprovalExecutionContext(task_id, plan_id, uuid4()),
        tool_version="1.0.0",
    ).valid
    assert not validate_approval_grant(
        grant,
        operation="delete",
        tool_name="file",
        parameters=parameters,
        context=context,
        tool_version="2.0.0",
    ).valid


def test_expired_or_naive_approval_grants_fail_closed() -> None:
    context = ApprovalExecutionContext(None, None, uuid4())
    digest = approval_binding_digest(
        operation="write",
        tool_name="file",
        parameters={},
        task_id=None,
        plan_id=None,
        step_id=context.step_id,
    )
    expired = ApprovalGrant(
        uuid4(),
        digest,
        utc_now() - timedelta(seconds=1),
        None,
    )
    naive = ApprovalGrant(
        uuid4(),
        digest,
        (utc_now() + timedelta(seconds=1)).replace(tzinfo=None),
        None,
    )

    for grant in (expired, naive):
        validation = validate_approval_grant(
            grant,
            operation="write",
            tool_name="file",
            parameters={},
            context=context,
        )
        assert validation.valid is False


def test_unbound_confirmation_boolean_cannot_authorize_execution() -> None:
    executor = ToolExecutor()
    executor.register(
        ToolDefinition(
            name="dangerous",
            description="Dangerous",
            risk_level=RiskLevel.HIGH,
        ),
        lambda: "executed",
    )

    result = executor.execute("dangerous", confirmation_granted=True)

    assert result.status is ToolExecutionStatus.BLOCKED
    assert "unbound" in (result.error or "").lower()


def test_executor_accepts_exact_bound_grant_and_rejects_changed_parameters() -> None:
    executor = ToolExecutor()
    executor.register(
        ToolDefinition(
            name="write",
            description="Write",
            risk_level=RiskLevel.MEDIUM,
        ),
        lambda path: path,
    )
    authorization = bound_approval("write", parameters={"path": "a.txt"})

    accepted = executor.execute(
        "write",
        parameters={"path": "a.txt"},
        **authorization,
    )
    rejected = executor.execute(
        "write",
        parameters={"path": "b.txt"},
        **authorization,
    )

    assert accepted.status is ToolExecutionStatus.SUCCESS
    assert rejected.status is ToolExecutionStatus.BLOCKED


def test_agent_gate_uses_real_tool_contract_and_cannot_be_downgraded() -> None:
    tools = ToolExecutor()
    tools.register(
        ToolDefinition(
            name="send",
            description="Send",
            risk_level=RiskLevel.HIGH,
        ),
        lambda: "sent",
    )
    gate = ApprovalGate(tool_executor=tools)
    step = PlanStep(
        "send",
        metadata={
            "tool_name": "send",
            "risk_level": "read_only",
            "requires_approval": False,
        },
    )

    decision = gate.evaluate(step=step, plan_id=uuid4())

    assert decision.result is ApprovalGateResult.PENDING
    assert decision.request is not None
    assert decision.request.risk_level == RiskLevel.HIGH.value


def test_agent_gate_denies_critical_tool_without_creating_approval() -> None:
    tools = ToolExecutor()
    tools.register(
        ToolDefinition(
            name="critical",
            description="Critical",
            risk_level=RiskLevel.CRITICAL,
        ),
        lambda: "never",
    )
    gate = ApprovalGate(tool_executor=tools)

    decision = gate.evaluate(
        step=PlanStep("critical", metadata={"tool_name": "critical"})
    )

    assert decision.result is ApprovalGateResult.DENIED
    assert decision.request is None
    assert gate.store.list() == []


def test_approval_requests_are_immutable_and_store_transition_is_atomic() -> None:
    store = ApprovalStore()
    request = store.create(
        operation="send",
        reason="Send message",
        risk_level="high",
    )
    with pytest.raises(FrozenInstanceError):
        request.status = ApprovalStatus.APPROVED  # type: ignore[misc]

    def approve() -> str:
        try:
            return store.approve(request.operation_id).status.value
        except ValueError:
            return "rejected"

    with ThreadPoolExecutor(max_workers=8) as pool:
        outcomes = list(pool.map(lambda _: approve(), range(8)))

    assert outcomes.count(ApprovalStatus.APPROVED.value) == 1
    assert outcomes.count("rejected") == 7


def test_approval_store_expires_stale_requests_with_injected_clock() -> None:
    now = [utc_now()]
    store = ApprovalStore(clock=lambda: now[0])
    request = store.create(
        operation="write",
        reason="Write file",
        risk_level="medium",
        expires_in_seconds=1,
    )
    now[0] += timedelta(seconds=2)

    expired = store.expire_stale()

    assert expired[0].operation_id == request.operation_id
    assert expired[0].status is ApprovalStatus.EXPIRED
    assert store.get(request.operation_id).status is ApprovalStatus.EXPIRED
