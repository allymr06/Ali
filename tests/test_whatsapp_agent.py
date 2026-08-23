from __future__ import annotations

import asyncio

import pytest

from app.core.models import (
    Response,
    RiskLevel,
    ToolExecutionStatus,
    ToolResult,
)
from app.integrations.whatsapp_agent import (
    WhatsAppConversationAgent,
    screen_draft,
)
from app.tools.executor import ToolExecutor


class FakeWhatsApp:
    def __init__(self, message_batches=None):
        self.batches = list(message_batches or [])
        self.sent = []
        self.opened = []

    async def open_chat(self, contact, message=None):
        self.opened.append(contact)
        return ToolResult(
            ToolExecutionStatus.SUCCESS, "whatsapp_open_chat", verified=True
        )

    async def read_open_conversation(self, limit=12):
        messages = self.batches.pop(0) if self.batches else []
        return ToolResult(
            ToolExecutionStatus.SUCCESS,
            "whatsapp_read_conversation",
            data={"messages": messages},
            verified=True,
        )

    async def send_message(self, contact, message):
        self.sent.append((contact, message))
        return ToolResult(
            ToolExecutionStatus.SUCCESS,
            "whatsapp_send_message",
            verified=True,
        )


class FakeEngine:
    def __init__(self, reply="Merhaba, birazdan dönerim."):
        self.reply = reply
        self.requests = []

    async def handle(self, request, context=None, **kwargs):
        self.requests.append(request)
        return Response(self.reply, request_id=request.request_id)


# -------------------------------------------------------- draft screening


def test_screen_draft_blocks_secrets_and_bounds_length() -> None:
    assert screen_draft("")[1] == "empty_draft"

    safe, reason = screen_draft("Tamam, yarın görüşürüz.")
    assert reason is None and safe == "Tamam, yarın görüşürüz."

    blocked, reason = screen_draft("IBAN numaram TR00 1234")
    assert blocked is None and reason.startswith("blocked_content")

    blocked, reason = screen_draft("şifrem 1234")
    assert blocked is None

    long_text = "kelime " * 200
    trimmed, reason = screen_draft(long_text)
    assert reason is None and len(trimmed) <= 321


# ------------------------------------------------------------- lifecycle


@pytest.mark.asyncio
async def test_delegation_replies_to_new_messages_only() -> None:
    whatsapp = FakeWhatsApp(
        [
            ["Ali 10:00 Selam"],            # seed
            ["Ali 10:00 Selam"],            # unchanged -> no reply
            ["Ali 10:00 Selam", "Ali 10:01 Naber?"],  # new -> reply
        ]
    )
    engine = FakeEngine()
    agent = WhatsAppConversationAgent(
        whatsapp=whatsapp, engine=engine, max_turns=1
    )
    agent.POLL_SECONDS = 0.01

    started = await agent.start("Ali", "Kısa cevap ver", max_turns=1)
    assert started.status is ToolExecutionStatus.SUCCESS
    assert whatsapp.opened == ["Ali"]

    for _ in range(200):
        await asyncio.sleep(0.01)
        if whatsapp.sent:
            break

    assert whatsapp.sent == [("Ali", "Merhaba, birazdan dönerim.")]
    # The draft prompt must carry the delegation instruction.
    assert "Kısa cevap ver" in engine.requests[0].text
    # Drafting must never be allowed to call tools.
    assert engine.requests[0].metadata["allowed_tools"] == []
    agent.stop()


@pytest.mark.asyncio
async def test_delegation_stops_and_reports_status() -> None:
    agent = WhatsAppConversationAgent(
        whatsapp=FakeWhatsApp([[]]), engine=FakeEngine()
    )
    agent.POLL_SECONDS = 0.01

    idle = agent.status()
    assert idle.data["active"] is False

    await agent.start("Ali")
    assert agent.active is True
    assert agent.status().data["contact"] == "Ali"

    stopped = agent.stop()
    assert stopped.status is ToolExecutionStatus.SUCCESS
    assert agent.active is False


@pytest.mark.asyncio
async def test_second_delegation_is_blocked_while_active() -> None:
    agent = WhatsAppConversationAgent(
        whatsapp=FakeWhatsApp([[]]), engine=FakeEngine()
    )
    agent.POLL_SECONDS = 0.01
    await agent.start("Ali")
    second = await agent.start("Veli")
    assert second.status is ToolExecutionStatus.BLOCKED
    agent.stop()


@pytest.mark.asyncio
async def test_unsafe_draft_is_never_sent() -> None:
    whatsapp = FakeWhatsApp(
        [["seed 10:00"], ["seed 10:00", "Ali 10:02 IBAN at"]]
    )
    agent = WhatsAppConversationAgent(
        whatsapp=whatsapp,
        engine=FakeEngine("IBAN numaram TR00 9999 8888"),
        max_turns=1,
    )
    agent.POLL_SECONDS = 0.01

    await agent.start("Ali")
    for _ in range(150):
        await asyncio.sleep(0.01)
        if agent._state and agent._state.log:
            break

    assert whatsapp.sent == []
    assert agent._state.log[0]["kind"] == "skipped"
    agent.stop()


def test_delegation_tool_is_high_risk_and_confirmed() -> None:
    executor = ToolExecutor()
    WhatsAppConversationAgent(
        whatsapp=FakeWhatsApp(), engine=FakeEngine()
    ).register_tools(executor)
    definition = executor.get("whatsapp_delegate_chat").definition
    assert definition.risk_level is RiskLevel.HIGH
    assert definition.requires_confirmation is True
