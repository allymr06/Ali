from __future__ import annotations

from datetime import timedelta
from uuid import uuid4

import pytest

from app.agent.approval import (
    ApprovalStatus,
    ApprovalStore,
)
from app.agent.loop import AgentLoop
from app.agent.models import AgentMode
from app.core.models import Context, Request
from app.core.time import utc_now
from app.main import create_application


def create_loop():
    application = create_application()

    return (
        AgentLoop(
            engine=application.engine,
        ),
        application,
    )


def test_approval_store_creates_pending_request():
    store = ApprovalStore()

    request = store.create(
        operation="delete_file",
        reason="Destructive operation",
        risk_level="high",
    )

    assert request.status is ApprovalStatus.PENDING
    assert request.operation == "delete_file"
    assert request.reason == "Destructive operation"
    assert request.risk_level == "high"


def test_approval_store_approves_request():
    store = ApprovalStore()

    request = store.create(
        operation="write_file",
        reason="Write operation",
        risk_level="medium",
    )

    approved = store.approve(
        request.operation_id
    )

    assert approved.status is ApprovalStatus.APPROVED
    assert (
        store.get(request.operation_id).status
        is ApprovalStatus.APPROVED
    )


def test_approval_store_denies_request():
    store = ApprovalStore()

    request = store.create(
        operation="delete_file",
        reason="Delete operation",
        risk_level="critical",
    )

    denied = store.deny(
        request.operation_id
    )

    assert denied.status is ApprovalStatus.DENIED


def test_approval_store_rejects_double_approval():
    store = ApprovalStore()

    request = store.create(
        operation="test",
        reason="test",
        risk_level="low",
    )

    store.approve(
        request.operation_id
    )

    with pytest.raises(ValueError):
        store.approve(
            request.operation_id
        )


def test_approval_store_rejects_unknown_request():
    store = ApprovalStore()

    with pytest.raises(KeyError):
        store.get(uuid4())


def test_agent_loop_exposes_approval_store():
    loop, _ = create_loop()

    assert isinstance(
        loop.approval_store,
        ApprovalStore,
    )


def test_agent_loop_creates_approval_request():
    loop, _ = create_loop()

    request = loop.request_approval(
        operation="delete_file",
        reason="Delete user-selected file",
        risk_level="high",
    )

    assert request.status is ApprovalStatus.PENDING

    assert (
        loop.get_approval(
            request.operation_id
        )
        is request
    )


def test_agent_loop_can_approve_request():
    loop, _ = create_loop()

    request = loop.request_approval(
        operation="send_email",
        reason="Send outbound email",
        risk_level="medium",
    )

    result = loop.approve(
        request.operation_id
    )

    assert result.status is ApprovalStatus.APPROVED


def test_agent_loop_can_deny_request():
    loop, _ = create_loop()

    request = loop.request_approval(
        operation="delete_file",
        reason="Delete file",
        risk_level="high",
    )

    result = loop.deny(
        request.operation_id
    )

    assert result.status is ApprovalStatus.DENIED


def test_approval_store_rejects_expired_request():
    now = [utc_now()]
    store = ApprovalStore(clock=lambda: now[0])
    request = store.create(
        operation="delete_file",
        reason="Delete operation",
        risk_level="high",
        expires_in_seconds=1,
    )
    now[0] += timedelta(seconds=2)
    with pytest.raises(ValueError, match="expired"):
        store.approve(request.operation_id)

    assert store.get(request.operation_id).status is ApprovalStatus.EXPIRED
