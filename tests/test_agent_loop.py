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
