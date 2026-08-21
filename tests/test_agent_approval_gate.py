from __future__ import annotations

from datetime import timedelta
from uuid import uuid4

import pytest

from app.agent.approval import (
    ApprovalStatus,
    ApprovalStore,
)
from app.agent.approval_gate import (
    ApprovalGate,
    ApprovalGateResult,
)
from app.agent.loop import AgentLoop
from app.main import create_application
from app.core.time import utc_now
from app.planning.models import PlanStep


def create_loop():
    application = create_application()

    loop = AgentLoop(
        engine=application.engine,
    )

    return loop


def test_gate_does_not_require_approval_for_normal_step():
    gate = ApprovalGate()

    step = PlanStep(
        "normal",
        metadata={
            "tool_name": "read_file",
        },
    )

    decision = gate.evaluate(
        step=step,
    )

    assert decision.result is ApprovalGateResult.NOT_REQUIRED
    assert decision.request is None


def test_gate_requires_approval_for_explicit_requirement():
    gate = ApprovalGate()

    step = PlanStep(
        "delete",
        metadata={
            "tool_name": "delete_file",
            "requires_approval": True,
            "risk_level": "high",
        },
    )

    decision = gate.evaluate(
        step=step,
        task_id=uuid4(),
        plan_id=uuid4(),
    )

    assert decision.result is ApprovalGateResult.PENDING
    assert decision.request is not None
    assert decision.request.status is ApprovalStatus.PENDING

    assert (
        step.metadata["approval_operation_id"]
        == decision.request.operation_id
    )


def test_gate_requires_approval_for_risk_level():
    gate = ApprovalGate()

    step = PlanStep(
        "danger",
        metadata={
            "tool_name": "dangerous_tool",
            "risk_level": "critical",
        },
    )

    decision = gate.evaluate(
        step=step,
    )

    assert decision.result is ApprovalGateResult.PENDING
    assert decision.request is not None


def test_gate_reuses_pending_request():
    gate = ApprovalGate()

    step = PlanStep(
        "delete",
        metadata={
            "tool_name": "delete_file",
            "requires_approval": True,
        },
    )

    first = gate.evaluate(
        step=step,
    )

    second = gate.evaluate(
        step=step,
    )

    assert first.request is second.request
    assert first.request.operation_id == (
        second.request.operation_id
    )


def test_gate_returns_approved_after_approval():
    gate = ApprovalGate()

    step = PlanStep(
        "delete",
        metadata={
            "tool_name": "delete_file",
            "requires_approval": True,
        },
    )

    pending = gate.evaluate(
        step=step,
    )

    gate.approve(
        pending.request.operation_id
    )

    approved = gate.evaluate(
        step=step,
    )

    assert approved.result is ApprovalGateResult.APPROVED
    assert approved.grant is not None
    assert approved.grant.operation_id == pending.request.operation_id


def test_gate_returns_denied_after_denial():
    gate = ApprovalGate()

    step = PlanStep(
        "delete",
        metadata={
            "tool_name": "delete_file",
            "requires_approval": True,
        },
    )

    pending = gate.evaluate(
        step=step,
    )

    gate.deny(
        pending.request.operation_id
    )

    denied = gate.evaluate(
        step=step,
    )

    assert denied.result is ApprovalGateResult.DENIED


def test_agent_loop_exposes_approval_gate():
    loop = create_loop()

    assert isinstance(
        loop.approval_gate,
        ApprovalGate,
    )


def test_agent_loop_evaluates_step_approval():
    loop = create_loop()

    step = PlanStep(
        "send",
        metadata={
            "tool_name": "send_email",
            "risk_level": "medium",
        },
    )

    decision = loop.evaluate_approval(
        step=step,
    )

    assert decision.result is ApprovalGateResult.PENDING
    assert decision.request is not None


def test_gate_invalidates_approval_when_parameters_change():
    gate = ApprovalGate()
    step = PlanStep(
        "delete",
        metadata={
            "tool_name": "delete_file",
            "requires_approval": True,
            "parameters": {"path": "a.txt"},
        },
    )
    first = gate.evaluate(step=step)
    gate.approve(first.request.operation_id)
    step.metadata["parameters"] = {"path": "b.txt"}

    second = gate.evaluate(step=step)

    assert (
        gate.store.get(first.request.operation_id).status
        is ApprovalStatus.EXPIRED
    )
    assert second.result is ApprovalGateResult.PENDING
    assert second.request.operation_id != first.request.operation_id


def test_gate_invalidates_approval_when_plan_changes():
    gate = ApprovalGate()
    step = PlanStep(
        "send",
        metadata={"tool_name": "send_email", "requires_approval": True},
    )
    first_plan_id = uuid4()
    first = gate.evaluate(step=step, plan_id=first_plan_id)
    gate.approve(first.request.operation_id)

    second = gate.evaluate(step=step, plan_id=uuid4())

    assert second.result is ApprovalGateResult.PENDING
    assert second.request.operation_id != first.request.operation_id


def test_gate_reuses_approval_for_equivalent_parameter_order():
    gate = ApprovalGate()
    step = PlanStep(
        "write",
        metadata={
            "tool_name": "write_file",
            "requires_approval": True,
            "parameters": {"path": "a.txt", "content": "hello"},
        },
    )
    first = gate.evaluate(step=step)
    gate.approve(first.request.operation_id)
    step.metadata["parameters"] = {"content": "hello", "path": "a.txt"}

    second = gate.evaluate(step=step)

    assert second.result is ApprovalGateResult.APPROVED
    assert second.request.operation_id == first.request.operation_id


def test_gate_replaces_expired_approval_request():
    now = [utc_now()]
    store = ApprovalStore(clock=lambda: now[0])
    gate = ApprovalGate(store, approval_ttl_seconds=1)
    step = PlanStep(
        "delete",
        metadata={"tool_name": "delete_file", "requires_approval": True},
    )
    first = gate.evaluate(step=step)
    now[0] += timedelta(seconds=2)

    second = gate.evaluate(step=step)

    assert (
        store.get(first.request.operation_id).status
        is ApprovalStatus.EXPIRED
    )
    assert second.result is ApprovalGateResult.PENDING
    assert second.request.operation_id != first.request.operation_id


def test_gate_denies_non_serializable_approval_parameters():
    gate = ApprovalGate()
    step = PlanStep(
        "unsafe",
        metadata={
            "tool_name": "unsafe_tool",
            "requires_approval": True,
            "parameters": {"value": object()},
        },
    )

    decision = gate.evaluate(step=step)

    assert decision.result is ApprovalGateResult.DENIED
    assert decision.request is None
