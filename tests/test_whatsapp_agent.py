from __future__ import annotations

import asyncio
import random
from datetime import datetime

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
from app.integrations.whatsapp_human import Pacing, QuietHours
from app.tools.executor import ToolExecutor

NOON = datetime(2026, 9, 21, 12, 0)


def incoming(text, time="10:00", author="Ali"):
    return {"author": author, "text": text, "time": time, "outgoing": False, "kind": "text"}


def outgoing(text, time="10:00"):
    return {"author": "Siz", "text": text, "time": time, "outgoing": True, "kind": "text"}


class FakeWhatsApp:
    """Serves conversation snapshots in order; the last one repeats."""

    def __init__(self, snapshots=None, chat="Ali"):
        self.snapshots = list(snapshots or [])
        self.chat = chat
        self.sent = []
        self.opened = []

    async def open_chat(self, contact, message=None):
        self.opened.append(contact)
        return ToolResult(ToolExecutionStatus.SUCCESS, "whatsapp_open_chat", verified=True)

    async def read_open_conversation(self, limit=12):
        if len(self.snapshots) > 1:
            messages = self.snapshots.pop(0)
        else:
            messages = self.snapshots[0] if self.snapshots else []
        return ToolResult(
            ToolExecutionStatus.SUCCESS,
            "whatsapp_read_conversation",
            data={"messages": list(messages), "chat": self.chat, "group": False},
            verified=True,
        )

    async def send_message(self, contact, message, *, typing_seconds_per_char=None):
        self.sent.append((contact, message, typing_seconds_per_char))
        return ToolResult(ToolExecutionStatus.SUCCESS, "whatsapp_send_message", verified=True)


class FakeEngine:
    def __init__(self, reply="Merhaba, birazdan d\u00f6nerim."):
        self.reply = reply
        self.requests = []

    async def handle(self, request, context=None, **kwargs):
        self.requests.append(request)
        return Response(self.reply, request_id=request.request_id)


def agent_for(whatsapp, engine=None, *, max_turns=8, pacing=None, quiet=None, clock=lambda: NOON):
    agent = WhatsAppConversationAgent(
        whatsapp=whatsapp,
        engine=engine or FakeEngine(),
        max_turns=max_turns,
        pacing=pacing or Pacing.instant(),
        quiet_hours=quiet if quiet is not None else QuietHours(),
        clock=clock,
    )
    agent.POLL_SECONDS = 0.01
    return agent


async def settle(predicate, ticks=300):
    for _ in range(ticks):
        await asyncio.sleep(0.01)
        if predicate():
            return True
    return predicate()


# -------------------------------------------------------- draft screening


def test_screen_draft_blocks_secrets_and_bounds_length() -> None:
    assert screen_draft("")[1] == "empty_draft"

    safe, reason = screen_draft("Tamam, yar\u0131n g\u00f6r\u00fc\u015f\u00fcr\u00fcz.")
    assert reason is None and safe == "Tamam, yar\u0131n g\u00f6r\u00fc\u015f\u00fcr\u00fcz."

    blocked, reason = screen_draft("IBAN numaram TR00 1234")
    assert blocked is None and reason.startswith("blocked_content")

    blocked, reason = screen_draft("\u015fifrem 1234")
    assert blocked is None

    long_text = "kelime " * 200
    trimmed, reason = screen_draft(long_text)
    assert reason is None and len(trimmed) <= 321


# ------------------------------------------------------------- lifecycle


@pytest.mark.asyncio
async def test_delegation_replies_to_new_messages_only_and_in_the_users_style() -> None:
    seed = [outgoing("kanka naber"), outgoing("iyiyim ya"), outgoing("gelirim ak\u015fam"), incoming("Selam")]
    whatsapp = FakeWhatsApp([seed, seed, seed + [incoming("Naber?", "10:01")]])
    engine = FakeEngine()
    agent = agent_for(whatsapp, engine, max_turns=1)

    started = await agent.start("Ali", "K\u0131sa cevap ver", max_turns=1)
    assert started.status is ToolExecutionStatus.SUCCESS
    assert whatsapp.opened == ["Ali"]
    assert "3 \u00f6rnek" in started.data["style"]

    assert await settle(lambda: bool(whatsapp.sent))
    assert whatsapp.sent == [("Ali", "Merhaba, birazdan d\u00f6nerim.", None)]
    prompt = engine.requests[0].text
    assert "K\u0131sa cevap ver" in prompt
    assert "Ali: Naber?" in prompt and "Siz: kanka naber" in prompt
    assert "k\u00fc\u00e7\u00fck harfle ba\u015flar" in prompt, "the measured style rides the prompt"
    assert engine.requests[0].metadata["allowed_tools"] == []
    agent.stop()


@pytest.mark.asyncio
async def test_the_agent_never_answers_its_own_bubbles() -> None:
    seed = [incoming("Selam")]
    whatsapp = FakeWhatsApp([seed, seed + [outgoing("ben yazd\u0131m", "10:02")], seed + [outgoing("ben yazd\u0131m", "10:02"), {"author": None, "text": "devam\u0131", "time": "10:03", "outgoing": None, "kind": "text"}]])
    agent = agent_for(whatsapp, max_turns=2)
    await agent.start("Ali")
    assert not await settle(lambda: bool(whatsapp.sent), ticks=80)
    assert whatsapp.sent == [], "own and unattributed bubbles are never replied to"
    agent.stop()


@pytest.mark.asyncio
async def test_a_plain_acknowledgement_is_left_alone() -> None:
    seed = [incoming("Selam")]
    whatsapp = FakeWhatsApp([seed, seed + [incoming("tamam \U0001F44D", "10:05")]])
    agent = agent_for(whatsapp, max_turns=2)
    await agent.start("Ali")
    assert await settle(lambda: any(e.get("reason") == "acknowledged" for e in agent._state.log))
    assert whatsapp.sent == []
    agent.stop()


@pytest.mark.asyncio
async def test_quiet_hours_hold_the_reply_and_the_morning_sends_it() -> None:
    seed = [incoming("Selam")]
    whatsapp = FakeWhatsApp([seed, seed + [incoming("Yar\u0131n geliyor musun?", "02:10")]])
    moment = {"now": datetime(2026, 9, 21, 2, 10)}
    agent = agent_for(whatsapp, max_turns=1, clock=lambda: moment["now"])
    await agent.start("Ali")
    assert await settle(lambda: any(e.get("reason") == "quiet_hours" for e in agent._state.log))
    assert whatsapp.sent == [], "asleep"
    held = [e for e in agent._state.log if e.get("reason") == "quiet_hours"]
    assert len(held) == 1, "one held entry, not one per poll"

    moment["now"] = datetime(2026, 9, 21, 8, 30)
    assert await settle(lambda: bool(whatsapp.sent))
    assert whatsapp.sent[0][1] == "Merhaba, birazdan d\u00f6nerim."
    agent.stop()


@pytest.mark.asyncio
async def test_a_switched_chat_is_never_read_as_the_contact() -> None:
    seed = [incoming("Selam")]
    whatsapp = FakeWhatsApp([seed, seed + [incoming("gizli", "10:09", author="Veli")]], chat="Ali")
    agent = agent_for(whatsapp, max_turns=2)
    await agent.start("Ali")
    whatsapp.chat = "Veli"
    assert await settle(lambda: any(e.get("reason") == "chat_switched" for e in agent._state.log))
    assert whatsapp.sent == []
    agent.stop()


@pytest.mark.asyncio
async def test_reply_lines_become_bubbles_typed_at_a_human_pace() -> None:
    seed = [incoming("Selam")]
    whatsapp = FakeWhatsApp([seed, seed + [incoming("bug\u00fcn ne yap\u0131yorsun?", "10:11")]])
    pacing = Pacing(rng=random.Random(3), seconds_per_char=(0.001, 0.002), max_typing_seconds=0.02, max_read_seconds=0.02)
    agent = agent_for(whatsapp, FakeEngine("ders \u00e7al\u0131\u015f\u0131yorum\nsen?"), max_turns=1, pacing=pacing)
    await agent.start("Ali")
    assert await settle(lambda: len(whatsapp.sent) == 2, ticks=600)
    texts = [item[1] for item in whatsapp.sent]
    assert texts == ["ders \u00e7al\u0131\u015f\u0131yorum", "sen?"]
    assert all(item[2] and item[2] > 0 for item in whatsapp.sent), "each bubble is typed, not pasted"
    assert agent._state.turns_taken == 1, "one exchange, however many bubbles"
    agent.stop()


@pytest.mark.asyncio
async def test_delegation_stops_and_reports_status() -> None:
    agent = agent_for(FakeWhatsApp([[]]))
    idle = agent.status()
    assert idle.data["active"] is False

    await agent.start("Ali")
    assert agent.active is True
    assert agent.status().data["contact"] == "Ali"
    assert "yeterli \u00f6rnek yok" in agent.status().data["style"]

    stopped = agent.stop()
    assert stopped.status is ToolExecutionStatus.SUCCESS
    assert agent.active is False


@pytest.mark.asyncio
async def test_second_delegation_is_blocked_while_active() -> None:
    agent = agent_for(FakeWhatsApp([[]]))
    await agent.start("Ali")
    second = await agent.start("Veli")
    assert second.status is ToolExecutionStatus.BLOCKED
    agent.stop()


@pytest.mark.asyncio
async def test_unsafe_draft_is_never_sent() -> None:
    seed = [incoming("seed")]
    whatsapp = FakeWhatsApp([seed, seed + [incoming("IBAN at", "10:02")]])
    agent = agent_for(whatsapp, FakeEngine("IBAN numaram TR00 9999 8888"), max_turns=1)
    await agent.start("Ali")
    assert await settle(lambda: any(e.get("kind") == "skipped" for e in agent._state.log))
    assert whatsapp.sent == []
    assert agent._state.log[-1]["reason"].startswith("blocked_content")
    agent.stop()


def test_delegation_tool_is_high_risk_and_confirmed() -> None:
    executor = ToolExecutor()
    WhatsAppConversationAgent(
        whatsapp=FakeWhatsApp(), engine=FakeEngine()
    ).register_tools(executor)
    definition = executor.get("whatsapp_delegate_chat").definition
    assert definition.risk_level is RiskLevel.HIGH
    assert definition.requires_confirmation is True
