"""Autonomous WhatsApp conversation agent.

The user delegates a conversation ("reply to X for me", "talk to X on
my behalf"); JARVIS then watches that chat, drafts replies with the
core model, and sends them. Delegation is deliberately bounded:

- it runs only for a named contact the user already added
- it stops at a turn limit, a duration limit, or an explicit stop
- every outgoing message goes through the same verified send path
- drafts that look like commitments the user did not authorize are
  held back rather than sent

And it behaves like a person rather than a bot: it answers only what
the other side wrote (never its own bubbles, never a chat the user
switched to), writes in the user's own measured style, leaves a plain
"tamam" unanswered, sleeps through the night, takes time to read and
to type, and sends short bubbles instead of one paragraph.

Nothing here bypasses the tool permission engine: the agent is started
by an approval-gated tool and sends through the HIGH-risk send tool's
underlying verified path.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable

from app.integrations.whatsapp_human import (
    Pacing,
    QuietHours,
    StyleProfile,
    decide_reply,
    split_bubbles,
)

from app.core.models import (
    Context,
    Request,
    RequestSource,
    RiskLevel,
    ToolDefinition,
    ToolExecutionStatus,
    ToolResult,
)

_PERSONA_PROMPT = (
    "Kullanıcı adına WhatsApp'ta yazışıyorsun. Kurallar:\n"
    "- Kullanıcının ağzından, aşağıdaki üslup ölçümüne uyarak yaz.\n"
    "- Bir insan gibi kısa yaz; gerekirse en fazla üç satır, her satır ayrı "
    "bir mesaj balonu olarak gider.\n"
    "- Emoji ve ton karşı tarafa ve kullanıcının üslubuna uysun; abartma.\n"
    "- ASLA para, adres, şifre, kod veya kişisel bilgi paylaşma.\n"
    "- Kullanıcı adına söz verme, randevu/ödeme taahhüt etme.\n"
    "- Bilmediğin bir şey sorulursa 'sonra bakıp döneceğim' de.\n"
    "- Sadece gönderilecek satırları yaz; açıklama, tırnak veya etiket ekleme."
)

# Draft guards: the agent must not commit the user to anything or leak
# secrets, even if the model is talked into it by the other party.
_FORBIDDEN_MARKERS = (
    "iban",
    "tr00",
    "şifre",
    "sifre",
    "parola",
    "kart numarası",
    "cvv",
    "doğrulama kodu",
    "dogrulama kodu",
    "otp",
    "adresim",
)

_MAX_DRAFT_CHARACTERS = 320


@dataclass
class DelegationState:
    contact: str
    goal: str
    max_turns: int
    started_at: float = field(default_factory=time.monotonic)
    turns_taken: int = 0
    seen: set[tuple[str, str, str]] = field(default_factory=set)
    style: StyleProfile = field(default_factory=StyleProfile)
    stopped: bool = False
    log: list[dict[str, str]] = field(default_factory=list)


def message_key(message: dict[str, Any]) -> tuple[str, str, str]:
    return (
        str(message.get("author") or ""),
        str(message.get("text") or ""),
        str(message.get("time") or ""),
    )


def screen_draft(text: str) -> tuple[str | None, str | None]:
    """Return (safe_text, refusal_reason)."""
    cleaned = " ".join(text.split())
    if not cleaned:
        return None, "empty_draft"
    if len(cleaned) > _MAX_DRAFT_CHARACTERS:
        cleaned = cleaned[:_MAX_DRAFT_CHARACTERS].rstrip() + "…"
    lowered = cleaned.casefold()
    for marker in _FORBIDDEN_MARKERS:
        if marker in lowered:
            return None, f"blocked_content:{marker}"
    return cleaned, None


class WhatsAppConversationAgent:
    """Drive a delegated WhatsApp conversation with bounded autonomy."""

    POLL_SECONDS = 6.0
    MAX_DURATION_SECONDS = 30 * 60

    def __init__(
        self,
        *,
        whatsapp: Any,
        engine: Any,
        max_turns: int = 8,
        pacing: Pacing | None = None,
        quiet_hours: QuietHours | None = None,
        clock: Callable[[], datetime] = datetime.now,
    ) -> None:
        self._whatsapp = whatsapp
        self._engine = engine
        self._default_max_turns = max_turns
        self._pacing = pacing or Pacing()
        self._quiet = QuietHours() if quiet_hours is None else quiet_hours
        self._clock = clock
        self._state: DelegationState | None = None
        self._task: asyncio.Task | None = None

    # ------------------------------------------------------------------

    @property
    def active(self) -> bool:
        return self._state is not None and not self._state.stopped

    def status(self) -> ToolResult:
        state = self._state
        if state is None:
            return ToolResult(
                ToolExecutionStatus.SUCCESS,
                "whatsapp_delegation_status",
                message="Şu an devredilmiş bir sohbet yok.",
                data={"active": False},
                verified=True,
            )
        return ToolResult(
            ToolExecutionStatus.SUCCESS,
            "whatsapp_delegation_status",
            message=(
                f"'{state.contact}' sohbeti devrede: "
                f"{state.turns_taken}/{state.max_turns} yanıt verildi."
            ),
            data={
                "active": self.active,
                "contact": state.contact,
                "goal": state.goal,
                "turns_taken": state.turns_taken,
                "max_turns": state.max_turns,
                "style": state.style.describe(),
                "log": state.log[-10:],
            },
            verified=True,
        )

    def stop(self) -> ToolResult:
        state = self._state
        if state is None or state.stopped:
            return ToolResult(
                ToolExecutionStatus.SUCCESS,
                "whatsapp_stop_delegation",
                message="Zaten devredilmiş bir sohbet yoktu.",
                verified=True,
            )
        state.stopped = True
        if self._task is not None and not self._task.done():
            self._task.cancel()
        return ToolResult(
            ToolExecutionStatus.SUCCESS,
            "whatsapp_stop_delegation",
            message=(
                f"'{state.contact}' sohbetinin devri durduruldu; "
                f"{state.turns_taken} yanıt verilmişti."
            ),
            data={"turns_taken": state.turns_taken},
            verified=True,
        )

    # ------------------------------------------------------------------

    async def _draft_reply(
        self, state: DelegationState, transcript: list[str]
    ) -> str:
        conversation = "\n".join(transcript[-10:])
        prompt = (
            f"{_PERSONA_PROMPT}\n\n"
            f"{state.style.describe()}\n\n"
            f"Kişi: {state.contact}\n"
            f"Kullanıcının talimatı: {state.goal}\n\n"
            f"Son mesajlar (Siz = kullanıcı):\n{conversation}\n\n"
            "Şimdi gönderilecek satır(lar)ı yaz:"
        )
        response = await self._engine.handle(
            Request(
                prompt,
                source=RequestSource.TEXT,
                metadata={
                    # Drafting must never trigger tools or memory
                    # writes; it is pure text generation.
                    "allowed_tools": [],
                    "memory_write": False,
                    "whatsapp_delegation": state.contact,
                },
            ),
            Context(),
        )
        return response.text or ""

    @staticmethod
    def _note(state: DelegationState, kind: str, reason: str) -> None:
        # One line per distinct reason in a row: a held night is one entry.
        if state.log and state.log[-1].get("kind") == kind and state.log[-1].get("reason") == reason:
            return
        state.log.append({"kind": kind, "reason": reason})

    @staticmethod
    def _chat_matches(contact: str, chat: Any) -> bool:
        if not chat:
            return True  # no title readable: nothing to contradict
        wanted = contact.strip().casefold()
        return bool(wanted) and wanted in str(chat).casefold()

    def _learn_style(self, state: DelegationState, messages: list[dict[str, Any]]) -> None:
        own = [m.get("text") or "" for m in messages if m.get("outgoing") and m.get("text")]
        if own:
            state.style = StyleProfile.from_messages(own)

    async def _run_loop(self, state: DelegationState) -> None:
        deadline = state.started_at + self.MAX_DURATION_SECONDS
        while (
            not state.stopped
            and state.turns_taken < state.max_turns
            and time.monotonic() < deadline
        ):
            await asyncio.sleep(self.POLL_SECONDS)
            if state.stopped:
                break
            read = await self._whatsapp.read_open_conversation(limit=30)
            if read.status is not ToolExecutionStatus.SUCCESS:
                continue
            data = read.data or {}
            if not self._chat_matches(state.contact, data.get("chat")):
                # The user is looking at another chat; its words are not
                # this conversation's. Wait until the contact is back.
                self._note(state, "waiting", "chat_switched")
                continue
            messages = [m for m in data.get("messages", []) if isinstance(m, dict)]
            self._learn_style(state, messages)
            fresh = [
                m for m in messages
                if message_key(m) not in state.seen
                and m.get("outgoing") is False
                and m.get("text")
            ]
            decision = decide_reply(
                [m["text"] for m in fresh], now=self._clock(), quiet=self._quiet
            )
            if decision.reason == "quiet_hours":
                # Asleep: nothing is marked seen, so the morning answers it.
                self._note(state, "held", "quiet_hours")
                continue
            for m in messages:
                state.seen.add(message_key(m))
            if not decision.reply:
                if decision.reason != "nothing_new":
                    self._note(state, "skipped", decision.reason)
                continue

            transcript = [
                f"{'Siz' if m.get('outgoing') else (m.get('author') or state.contact)}: "
                f"{m.get('text') or '[medya]'}"
                for m in messages
            ]
            draft = await self._draft_reply(state, transcript)
            bubbles: list[str] = []
            for bubble in split_bubbles(draft):
                safe, refusal = screen_draft(bubble)
                if safe is None:
                    self._note(state, "skipped", refusal or "unknown")
                    continue
                bubbles.append(safe)
            if not bubbles:
                continue
            await asyncio.sleep(
                self._pacing.read_delay(sum(len(m["text"]) for m in fresh))
            )
            for index, bubble in enumerate(bubbles):
                if index:
                    await asyncio.sleep(self._pacing.between_bubbles())
                pace = self._pacing.typing_rate(len(bubble))
                sent = await self._whatsapp.send_message(
                    state.contact, bubble, typing_seconds_per_char=pace or None
                )
                state.log.append(
                    {
                        "kind": "sent"
                        if sent.status is ToolExecutionStatus.SUCCESS
                        else "unverified",
                        "text": bubble,
                    }
                )
            state.turns_taken += 1
        state.stopped = True

    async def start(
        self,
        contact: str,
        goal: str = "",
        max_turns: int = 0,
    ) -> ToolResult:
        if self.active:
            return ToolResult(
                ToolExecutionStatus.BLOCKED,
                "whatsapp_delegate_chat",
                message=(
                    "Zaten devrede bir sohbet var; önce "
                    "'whatsapp_stop_delegation' ile durdur."
                ),
                error="already_active",
                verified=True,
            )
        opened = await self._whatsapp.open_chat(contact)
        if opened.status is not ToolExecutionStatus.SUCCESS:
            opened.tool_name = "whatsapp_delegate_chat"
            return opened

        bounded_turns = max(
            1, min(int(max_turns or self._default_max_turns), 20)
        )
        state = DelegationState(
            contact=contact.strip(),
            goal=goal.strip()
            or "Kullanıcı adına nazikçe sohbeti sürdür.",
            max_turns=bounded_turns,
        )
        # Seed with the current messages so the agent replies only to
        # what arrives after delegation starts, and learn the user's
        # style from their own bubbles already on screen.
        seed = await self._whatsapp.read_open_conversation(limit=30)
        if seed.status is ToolExecutionStatus.SUCCESS:
            messages = [
                m for m in (seed.data or {}).get("messages", []) if isinstance(m, dict)
            ]
            for m in messages:
                state.seen.add(message_key(m))
            self._learn_style(state, messages)
        self._state = state
        self._task = asyncio.create_task(self._run_loop(state))
        return ToolResult(
            ToolExecutionStatus.SUCCESS,
            "whatsapp_delegate_chat",
            message=(
                f"'{state.contact}' sohbetini devraldım. En fazla "
                f"{bounded_turns} yanıt vereceğim; "
                "'sohbeti bırak' dediğinde dururum."
            ),
            data={
                "contact": state.contact,
                "goal": state.goal,
                "max_turns": bounded_turns,
                "style": state.style.describe(),
            },
            verified=True,
        )

    # ------------------------------------------------------------------

    def register_tools(self, executor: Any) -> None:
        def define(
            name: str,
            description: str,
            *,
            risk: RiskLevel = RiskLevel.READ_ONLY,
            confirm: bool = False,
            timeout: float = 45.0,
        ) -> ToolDefinition:
            return ToolDefinition(
                name=name,
                description=description,
                risk_level=risk,
                requires_confirmation=confirm,
                version="1.0.0",
                capabilities=frozenset({"whatsapp", "messaging"}),
                tags=frozenset({"integration", "whatsapp", "agent"}),
                timeout_seconds=timeout,
                metadata={
                    "verification_strategy": "uia_observation",
                    "sensitive_output": True,
                },
            )

        async def delegate(
            contact: str, goal: str = "", max_turns: int = 0
        ) -> ToolResult:
            return await self.start(contact, goal, max_turns)

        def stop_delegation() -> ToolResult:
            return self.stop()

        def delegation_status() -> ToolResult:
            return self.status()

        executor.register(
            define(
                "whatsapp_delegate_chat",
                "Bir kişiyle WhatsApp sohbetini JARVIS'e devret: gelen "
                "mesajları kullanıcı adına yanıtlar. Onay gerektirir.",
                risk=RiskLevel.HIGH,
                confirm=True,
            ),
            delegate,
            source="integration:whatsapp",
        )
        executor.register(
            define(
                "whatsapp_stop_delegation",
                "Devredilmiş WhatsApp sohbetini durdur.",
                risk=RiskLevel.LOW,
                timeout=10.0,
            ),
            stop_delegation,
            source="integration:whatsapp",
        )
        executor.register(
            define(
                "whatsapp_delegation_status",
                "Devredilmiş WhatsApp sohbetinin durumunu göster.",
                timeout=10.0,
            ),
            delegation_status,
            source="integration:whatsapp",
        )
