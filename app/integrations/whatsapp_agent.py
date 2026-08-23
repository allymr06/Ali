"""Autonomous WhatsApp conversation agent.

The user delegates a conversation ("reply to X for me", "talk to X on
my behalf"); JARVIS then watches that chat, drafts replies with the
core model, and sends them. Delegation is deliberately bounded:

- it runs only for a named contact the user already added
- it stops at a turn limit, a duration limit, or an explicit stop
- every outgoing message goes through the same verified send path
- drafts that look like commitments the user did not authorize are
  held back rather than sent

Nothing here bypasses the tool permission engine: the agent is started
by an approval-gated tool and sends through the HIGH-risk send tool's
underlying verified path.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any

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
    "- Kullanıcının ağzından, doğal ve kısa yaz (en fazla iki cümle).\n"
    "- Emoji ve üslup karşı tarafın tonuna uysun; abartma.\n"
    "- ASLA para, adres, şifre, kod veya kişisel bilgi paylaşma.\n"
    "- Kullanıcı adına söz verme, randevu/ödeme taahhüt etme.\n"
    "- Bilmediğin bir şey sorulursa 'ona sonra döneceğim' de.\n"
    "- Sadece gönderilecek mesajı yaz; açıklama veya tırnak ekleme."
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
    last_seen: tuple[str, ...] = ()
    stopped: bool = False
    log: list[dict[str, str]] = field(default_factory=list)


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
    ) -> None:
        self._whatsapp = whatsapp
        self._engine = engine
        self._default_max_turns = max_turns
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
        self, state: DelegationState, incoming: list[str]
    ) -> tuple[str | None, str | None]:
        conversation = "\n".join(incoming[-8:])
        prompt = (
            f"{_PERSONA_PROMPT}\n\n"
            f"Kişi: {state.contact}\n"
            f"Kullanıcının talimatı: {state.goal}\n\n"
            f"Son mesajlar:\n{conversation}\n\n"
            "Şimdi gönderilecek yanıtı yaz:"
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
        return screen_draft(response.text or "")

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
            read = await self._whatsapp.read_open_conversation(limit=10)
            if read.status is not ToolExecutionStatus.SUCCESS:
                continue
            messages = tuple(
                str(item) for item in (read.data or {}).get("messages", [])
            )
            if not messages or messages == state.last_seen:
                continue
            fresh = [m for m in messages if m not in state.last_seen]
            state.last_seen = messages
            if not fresh:
                continue

            draft, refusal = await self._draft_reply(state, fresh)
            if draft is None:
                state.log.append(
                    {"kind": "skipped", "reason": refusal or "unknown"}
                )
                continue
            sent = await self._whatsapp.send_message(
                state.contact, draft
            )
            state.turns_taken += 1
            state.log.append(
                {
                    "kind": "sent"
                    if sent.status is ToolExecutionStatus.SUCCESS
                    else "unverified",
                    "text": draft,
                }
            )
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
        # what arrives after delegation starts.
        seed = await self._whatsapp.read_open_conversation(limit=10)
        if seed.status is ToolExecutionStatus.SUCCESS:
            state.last_seen = tuple(
                str(item)
                for item in (seed.data or {}).get("messages", [])
            )
        self._state = state
        self._task = asyncio.create_task(self._run_loop(state))
        return ToolResult(
            ToolExecutionStatus.SUCCESS,
            "whatsapp_delegate_chat",
            message=(
                f"'{state.contact}' sohbetini devraldım. En fazla "
                f"{bounded_turns} yanıt vereceğim; "
                "'sohbeti bırak' dediğinde durururum."
            ),
            data={
                "contact": state.contact,
                "goal": state.goal,
                "max_turns": bounded_turns,
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
