from __future__ import annotations

import asyncio

import pytest

from app.agent.loop import AgentLoop
from app.agent.models import (
    AgentMode,
    AgentStatus,
)
from app.core.models import (
    Context,
    Request,
    ToolDefinition,
)
from app.main import create_application
from app.planning.models import Plan, PlanStep


def create_loop(plan_builder=None):
    application = create_application()

    return (
        AgentLoop(
            engine=application.engine,
            plan_builder=plan_builder,
        ),
        application,
    )


def test_agent_loop_requires_plan_builder_for_task_mode():
    loop, _ = create_loop()

    with pytest.raises(
        ValueError,
        match="plan_builder",
    ):
        asyncio.run(
            loop.run(
                Request("Do something"),
            )
        )


def test_agent_loop_can_build_from_plan_steps():
    def builder(request, context):
        return [
            PlanStep(
                "step",
                metadata={
                    "tool_name": "test",
                    "parameters": {},
                },
            ),
        ]

    loop, _ = create_loop(builder)

    plan = loop.build_plan(
        Request("Do something"),
        Context(),
    )

    assert isinstance(plan, Plan)
    assert plan.goal == "Do something"
    assert len(plan.steps) == 1


def test_agent_loop_accepts_prebuilt_plan():
    def builder(request, context):
        return Plan(
            goal=request.text,
            steps=[
                PlanStep(
                    "step",
                ),
            ],
        )

    loop, _ = create_loop(builder)

    request = Request("Prebuilt")

    plan = loop.build_plan(
        request,
        Context(),
    )

    assert plan.goal == "Prebuilt"
    assert len(plan.steps) == 1


def test_agent_loop_direct_mode_uses_core_engine():
    loop, _ = create_loop()

    request = Request("Merhaba JARVIS")

    result = asyncio.run(
        loop.run(
            request,
            mode=AgentMode.DIRECT,
        )
    )

    assert result.status is AgentStatus.COMPLETED
    assert "Merhaba JARVIS" in result.response_text
    assert result.task_id is None
    assert result.plan_id is None


@pytest.mark.asyncio
async def test_agent_loop_executes_tracked_task():
    def builder(request, context):
        return [
            PlanStep(
                "test",
                metadata={
                    "tool_name": "agent_test",
                    "parameters": {},
                },
            ),
        ]

    loop, application = create_loop(builder)

    application.tool_executor.register(
        ToolDefinition(
            name="agent_test",
            description="agent loop test",
        ),
        lambda: "agent-ok",
    )

    result = await loop.run(
        Request("Agent task"),
    )

    assert result.status is AgentStatus.COMPLETED
    assert result.task_id is not None
    assert result.plan_id is not None
    assert result.metadata["progress"] == 1.0
    assert result.metadata["task_status"] == "completed"


@pytest.mark.asyncio
async def test_agent_loop_propagates_task_failure():
    def builder(request, context):
        return [
            PlanStep(
                "failure",
                metadata={
                    "tool_name": "agent_failure",
                    "parameters": {},
                },
            ),
        ]

    loop, application = create_loop(builder)

    def fail():
        raise RuntimeError("agent failure")

    application.tool_executor.register(
        ToolDefinition(
            name="agent_failure",
            description="agent failure",
        ),
        fail,
    )

    result = await loop.run(
        Request("Fail"),
    )

    assert result.status is AgentStatus.FAILED
    assert result.task_id is not None
    assert result.plan_id is not None
    assert "Task failed:" in result.response_text


@pytest.mark.asyncio
async def test_agent_loop_supports_cancellation():
    import asyncio

    def builder(request, context):
        return [
            PlanStep(
                "cancel",
                metadata={
                    "tool_name": "never_run",
                    "parameters": {},
                },
            ),
        ]

    loop, application = create_loop(builder)

    called = {"value": False}

    def should_not_run():
        called["value"] = True
        return "unexpected"

    application.tool_executor.register(
        ToolDefinition(
            name="never_run",
            description="cancel test",
        ),
        should_not_run,
    )

    cancel_event = asyncio.Event()
    cancel_event.set()

    result = await loop.run(
        Request("Cancel"),
        cancel_event=cancel_event,
    )

    assert result.status is AgentStatus.CANCELLED
    assert called["value"] is False
    assert result.metadata["task_status"] == "cancelled"


@pytest.mark.asyncio
async def test_agent_loop_preserves_context():
    captured = {}

    def builder(request, context):
        captured["conversation_id"] = (
            context.conversation_id
        )

        return [
            PlanStep(
                "step",
                metadata={
                    "tool_name": "context_test",
                    "parameters": {},
                },
            ),
        ]

    loop, application = create_loop(builder)

    application.tool_executor.register(
        ToolDefinition(
            name="context_test",
            description="context test",
        ),
        lambda: "ok",
    )

    context = Context()

    result = await loop.run(
        Request("Context"),
        context=context,
    )

    assert result.status is AgentStatus.COMPLETED
    assert (
        captured["conversation_id"]
        == context.conversation_id
    )


@pytest.mark.asyncio
async def test_agent_loop_blocks_execution_until_approval():
    def builder(request, context):
        return [
            PlanStep(
                "delete",
                metadata={
                    "tool_name": "approval_delete_test",
                    "parameters": {},
                    "requires_approval": True,
                    "risk_level": "high",
                    "approval_reason": "Destructive test operation.",
                },
            ),
        ]

    loop, application = create_loop(builder)

    called = {"value": False}

    def should_not_run():
        called["value"] = True
        return "executed"

    application.tool_executor.register(
        ToolDefinition(
            name="approval_delete_test",
            description="approval barrier test",
        ),
        should_not_run,
    )

    result = await loop.run(
        Request("Delete something"),
    )

    assert (
        result.status
        is AgentStatus.WAITING_FOR_APPROVAL
    )
    assert result.task_id is None
    assert result.plan_id is not None
    assert result.metadata["approval_status"] == "pending"
    assert len(result.metadata["pending_approvals"]) == 1
    assert called["value"] is False


@pytest.mark.asyncio
async def test_agent_loop_denied_approval_blocks_execution():
    def builder(request, context):
        return [
            PlanStep(
                "delete",
                metadata={
                    "tool_name": "approval_denied_test",
                    "parameters": {},
                    "requires_approval": True,
                    "risk_level": "critical",
                    "approval_reason": "Critical test operation.",
                },
            ),
        ]

    loop, application = create_loop(builder)

    called = {"value": False}

    def should_not_run():
        called["value"] = True
        return "executed"

    application.tool_executor.register(
        ToolDefinition(
            name="approval_denied_test",
            description="approval denial test",
        ),
        should_not_run,
    )

    plan = loop.build_plan(
        Request("Critical action"),
        Context(),
    )

    pending, denied = loop._check_plan_approvals(plan)

    assert not denied
    assert len(pending) == 1

    operation_id = (
        plan.steps[0]
        .metadata["approval_operation_id"]
    )

    loop.deny(operation_id)

    pending, denied = loop._check_plan_approvals(plan)

    assert not pending
    assert len(denied) == 1

    plan_builder_called = {"value": False}

    def reuse_builder(request, context):
        plan_builder_called["value"] = True
        return plan

    loop2 = AgentLoop(
        engine=application.engine,
        plan_builder=reuse_builder,
        approval_store=loop.approval_store,
    )

    result = await loop2.run(
        Request("Critical action"),
    )

    assert result.status is AgentStatus.FAILED
    assert result.metadata["approval_status"] == "denied"
    assert called["value"] is False
    assert plan_builder_called["value"] is True


@pytest.mark.asyncio
async def test_agent_loop_resumes_same_plan_after_approval():
    def builder(request, context):
        if not hasattr(builder, "plan"):
            builder.plan = Plan(
                goal=request.text,
                steps=[
                    PlanStep(
                        "send",
                        metadata={
                            "tool_name": "approval_resume_test",
                            "parameters": {},
                            "requires_approval": True,
                            "risk_level": "medium",
                            "approval_reason": "Outbound action.",
                        },
                    ),
                ],
            )

        return builder.plan

    loop, application = create_loop(builder)

    called = {"value": False}

    def approved_action():
        called["value"] = True
        return "approved-ok"

    application.tool_executor.register(
        ToolDefinition(
            name="approval_resume_test",
            description="approval resume test",
        ),
        approved_action,
    )

    request = Request("Send something")

    first = await loop.run(request)

    assert (
        first.status
        is AgentStatus.WAITING_FOR_APPROVAL
    )

    operation_id = (
        builder.plan.steps[0]
        .metadata["approval_operation_id"]
    )

    loop.approve(operation_id)

    second = await loop.run(request)

    assert second.status is AgentStatus.COMPLETED
    assert second.task_id is not None
    assert second.plan_id == builder.plan.plan_id
    assert called["value"] is True


@pytest.mark.asyncio
async def test_agent_loop_read_only_task_does_not_require_approval():
    def builder(request, context):
        return [
            PlanStep(
                "read",
                metadata={
                    "tool_name": "approval_read_test",
                    "parameters": {},
                    "risk_level": "read_only",
                },
            ),
        ]

    loop, application = create_loop(builder)

    application.tool_executor.register(
        ToolDefinition(
            name="approval_read_test",
            description="approval read test",
        ),
        lambda: "read-ok",
    )

    result = await loop.run(
        Request("Read something"),
        mode=AgentMode.TASK,
    )

    assert result.status is AgentStatus.COMPLETED
    assert result.metadata["task_status"] == "completed"
