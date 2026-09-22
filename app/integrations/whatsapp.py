"""WhatsApp Desktop integration.

Reading uses the Windows UI Automation tree of the real WhatsApp window
— native metadata, not pixel scraping — and never takes the user's focus:
the chat list is the rows of its grid, the open conversation is read
through the page's text pattern bubble by bubble, with the author label
("Siz:" or the contact's name) and the outgoing status button telling
who wrote what. Sending is a HIGH-risk, approval-gated action that
drives the official whatsapp://send deep link (or the visible chat row),
types the text — at a human pace when asked — then invokes the send
button and verifies the result. If any step cannot be verified the
message is left unsent in the input box, never fired blindly.

Contacts live in a user-editable JSON file inside the JARVIS state
directory; phone numbers never enter the model prompt unless the user
placed them there.
"""

from __future__ import annotations

import asyncio
import json
import re
import time
import urllib.parse
from pathlib import Path
from typing import Any

from app.core.models import (
    RiskLevel,
    ToolDefinition,
    ToolExecutionStatus,
    ToolResult,
)
from app.integrations.runtime import UriLauncher

_PHONE_PATTERN = re.compile(r"^\+?[0-9]{7,15}$")

_WINDOW_TITLE = "WhatsApp"
_SEND_BUTTON_NAMES = ("Gönder", "Gonder", "Send")
_COMPOSER_HINTS = ("mesaj yaz", "type a message")

# Chat-list rows read "[N okunmamış mesaj ]<name> <when> <preview>"; the
# "when" is a clock time today, a day word, a date, or a weekday.
# The badge text sits before the name on most rows and after the time on
# some (the layout reads the badge later when the preview wraps).
_UNREAD_BADGE = re.compile(r"(?<!\S)(?P<count>\d[\d.]*)\s+okunmamış mesaj(?!\S)", re.I)
_WHEN_TOKEN = re.compile(
    r"(?<!\S)(?P<when>\d{1,2}:\d{2}|Dün|Bugün|Yesterday|Today|\d{1,2}\.\d{1,2}\.\d{2,4}"
    r"|Pazartesi|Salı|Çarşamba|Perşembe|Cuma|Cumartesi|Pazar"
    r"|Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday)(?!\S)"
)
_TIME_LINE = re.compile(r"^\d{1,2}:\d{2}$")
_OWN_LABELS = frozenset({"siz", "sen", "you", "ben"})


def parse_chat_row(name: str) -> dict[str, Any]:
    """One chat-list row into name, unread count, when and preview."""
    text = " ".join(str(name).split())
    unread = 0
    match = _UNREAD_BADGE.search(text)
    if match:
        unread = int(match.group("count").replace(".", "") or 0)
        text = " ".join((text[: match.start()] + " " + text[match.end():]).split())
    title, when, preview = text, "", ""
    found = _WHEN_TOKEN.search(text)
    if found:
        title = text[: found.start()].strip()
        when = found.group("when")
        preview = text[found.end():].strip()
    return {"name": title, "unread": unread, "when": when, "preview": preview[:120]}


def parse_message_row(text: str, has_status: bool) -> dict[str, Any] | None:
    """One bubble's text run into author, text, time and direction.

    The run is an optional author label line ("Siz:" for the user's own
    bubbles, "<Name>:" for the other side), the message lines, and the
    HH:MM line. WhatsApp omits the label on a run of bubbles by the same
    author, so a row without one answers ``outgoing=None`` for the caller
    to inherit. A row with a label and no text is media.
    """
    lines = [" ".join(line.split()) for line in str(text).splitlines()]
    lines = [line for line in lines if line]
    if not lines:
        return None
    time_index = None
    for index in range(len(lines) - 1, -1, -1):
        if _TIME_LINE.match(lines[index]):
            time_index = index
            break
    when = lines[time_index] if time_index is not None else ""
    body_lines = lines[:time_index] if time_index is not None else lines
    author = None
    if body_lines and body_lines[0].endswith(":") and len(body_lines[0]) <= 80:
        author = body_lines[0][:-1].strip()
        body_lines = body_lines[1:]
    outgoing: bool | None = True if has_status else None
    if author is not None:
        outgoing = has_status or author.casefold() in _OWN_LABELS
    body = " ".join(body_lines).strip()
    return {
        "author": author,
        "text": body,
        "time": when,
        "outgoing": outgoing,
        "kind": "text" if body else "media",
    }


def messages_from_rows(rows: list[tuple[str, bool]]) -> list[dict[str, Any]]:
    """Parse rows in order, inheriting the author across unlabeled runs."""
    messages: list[dict[str, Any]] = []
    previous_author: str | None = None
    previous_outgoing: bool | None = None
    for text, has_status in rows:
        parsed = parse_message_row(text, has_status)
        if parsed is None:
            continue
        if parsed["author"] is None:
            parsed["author"] = previous_author
            if parsed["outgoing"] is None:
                parsed["outgoing"] = previous_outgoing
        previous_author, previous_outgoing = parsed["author"], parsed["outgoing"]
        messages.append(parsed)
    return messages


def message_line(message: dict[str, Any]) -> str:
    """A transcript line the model reads: who said what."""
    who = "Siz" if message.get("outgoing") else (message.get("author") or "?")
    body = message.get("text") or "[medya]"
    return f"{who}: {body}"


def parse_composer_name(name: str | None) -> tuple[str, bool] | None:
    """The open chat's title (and whether it is a group) from the message
    box's accessible name, "<title>[ grup] sohbetine bir mesaj yazın"."""
    text = " ".join(str(name or "").split())
    lowered = text.casefold()
    for marker, group in ((" grup sohbetine", True), (" sohbetine", False)):
        index = lowered.find(marker)
        if index > 0:
            return text[:index].strip(), group
    return None


class WhatsAppIntegration:
    def __init__(
        self,
        *,
        contacts_path: str | Path,
        uia_client: Any | None = None,
        uri_launcher: UriLauncher | None = None,
    ) -> None:
        self._contacts_path = Path(contacts_path)
        self._uia = uia_client
        self._uri = uri_launcher or UriLauncher()

    def _uia_client(self) -> Any:
        if self._uia is None:
            from app.integrations.uia import UiaClient

            self._uia = UiaClient()
        return self._uia

    # ------------------------------------------------------------------
    # Contacts
    # ------------------------------------------------------------------

    def _load_contacts(self) -> dict[str, str]:
        try:
            payload = json.loads(
                self._contacts_path.read_text(encoding="utf-8")
            )
        except (OSError, ValueError):
            return {}
        if not isinstance(payload, dict):
            return {}
        return {
            str(name): str(phone)
            for name, phone in payload.items()
            if isinstance(name, str) and isinstance(phone, str)
        }

    def _save_contacts(self, contacts: dict[str, str]) -> None:
        self._contacts_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self._contacts_path.with_suffix(".tmp")
        temporary.write_text(
            json.dumps(
                contacts, ensure_ascii=False, indent=2, sort_keys=True
            ),
            encoding="utf-8",
        )
        temporary.replace(self._contacts_path)

    def _resolve_phone(self, contact: str) -> str | None:
        candidate = contact.strip()
        if _PHONE_PATTERN.fullmatch(candidate.replace(" ", "")):
            return candidate.replace(" ", "").lstrip("+")
        contacts = self._load_contacts()
        for name, phone in contacts.items():
            if name.strip().casefold() == candidate.casefold():
                return phone.replace(" ", "").lstrip("+")
        return None

    def add_contact(self, name: str, phone: str) -> ToolResult:
        clean_name = name.strip()
        clean_phone = phone.strip().replace(" ", "")
        if not clean_name:
            return ToolResult(
                ToolExecutionStatus.FAILED,
                "whatsapp_add_contact",
                message="Kişi adı boş olamaz.",
                error="empty_name",
            )
        if not _PHONE_PATTERN.fullmatch(clean_phone):
            return ToolResult(
                ToolExecutionStatus.FAILED,
                "whatsapp_add_contact",
                message=(
                    "Telefon numarası ülke koduyla, örn. "
                    "+905551112233 biçiminde olmalı."
                ),
                error="invalid_phone",
            )
        contacts = self._load_contacts()
        contacts[clean_name] = clean_phone
        self._save_contacts(contacts)
        stored = self._load_contacts().get(clean_name) == clean_phone
        return ToolResult(
            ToolExecutionStatus.SUCCESS,
            "whatsapp_add_contact",
            message=f"'{clean_name}' rehbere eklendi.",
            data={"name": clean_name},
            verified=stored,
        )

    def list_contacts(self) -> ToolResult:
        contacts = self._load_contacts()
        return ToolResult(
            ToolExecutionStatus.SUCCESS,
            "whatsapp_list_contacts",
            message=(
                f"{len(contacts)} kayıtlı kişi var."
                if contacts
                else "Rehber boş. 'whatsapp_add_contact' ile ekle."
            ),
            data={"names": sorted(contacts)},
            verified=True,
        )

    # ------------------------------------------------------------------
    # Window observation and actions
    # ------------------------------------------------------------------

    async def _ensure_window(
        self, timeout_seconds: float = 18.0
    ) -> bool:
        """Launch WhatsApp if it is not running and wait for it.

        The user asked for a message, not for an errand; a closed app
        is JARVIS's problem to solve, not something to bounce back.
        """
        client = self._uia_client()
        if await asyncio.to_thread(
            client.window_exists, _WINDOW_TITLE
        ):
            return True
        if not self._uri.open("whatsapp:"):
            return False
        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            await asyncio.sleep(1.0)
            if await asyncio.to_thread(
                client.window_exists, _WINDOW_TITLE
            ):
                # Cold start: give the chat list a moment to populate.
                await asyncio.sleep(2.5)
                return True
        return False

    async def read_recent_chats(self, limit: int = 8, launch: bool = True) -> ToolResult:
        """The chat list as a person glances at it: who, how many unread.

        ``launch=False`` is the quiet glance: a closed WhatsApp answers
        BLOCKED instead of being started - the home remote polls with
        it, and a poll must never open applications by itself.
        """
        bounded = max(1, min(int(limit), 20))
        client = self._uia_client()
        if launch:
            await self._ensure_window()
        elif not await asyncio.to_thread(client.window_exists, _WINDOW_TITLE):
            return ToolResult(
                ToolExecutionStatus.BLOCKED,
                "whatsapp_read_chats",
                message="WhatsApp kapalı.",
                error="window_not_found",
                verified=True,
            )
        try:
            rows = await asyncio.to_thread(
                client.read_chat_rows, _WINDOW_TITLE, limit=bounded
            )
        except Exception as exc:
            return ToolResult(
                ToolExecutionStatus.FAILED,
                "whatsapp_read_chats",
                message="Sohbet listesi okunamadı.",
                error=f"uia_{type(exc).__name__}",
            )
        if rows is None:
            return ToolResult(
                ToolExecutionStatus.BLOCKED,
                "whatsapp_read_chats",
                message=(
                    "WhatsApp penceresi bulunamadı. Uygulamayı aç ve "
                    "tekrar dene."
                ),
                error="window_not_found",
                verified=True,
            )
        entries = [parse_chat_row(row) for row in rows]
        unread = sum(1 for entry in entries if entry["unread"])
        message = f"{len(entries)} sohbet okundu"
        message += f"; {unread} sohbette okunmamış mesaj var." if unread else "."
        return ToolResult(
            ToolExecutionStatus.SUCCESS,
            "whatsapp_read_chats",
            message=message,
            data={"chats": rows, "entries": entries, "unread_chats": unread},
            verified=bool(entries),
        )

    async def current_chat(self) -> dict[str, Any] | None:
        """Title of the chat open on screen, or None when none is.

        The header strip answers in milliseconds; the message box's
        accessible name is the fallback when the header is not found.
        """
        client = self._uia_client()
        try:
            header = await asyncio.to_thread(client.chat_title, _WINDOW_TITLE)
        except Exception:
            header = None
        if header:
            return {"title": header[0], "group": bool(header[1])}
        try:
            name = await asyncio.to_thread(client.composer_name, _WINDOW_TITLE)
        except Exception:
            return None
        parsed = parse_composer_name(name)
        if parsed is None:
            return None
        return {"title": parsed[0], "group": parsed[1]}

    async def read_open_conversation(
        self, limit: int = 12
    ) -> ToolResult:
        """Read the open chat bubble by bubble, with who wrote each."""
        bounded = max(1, min(int(limit), 40))
        client = self._uia_client()
        await self._ensure_window()
        try:
            rows = await asyncio.to_thread(
                client.read_message_rows, _WINDOW_TITLE
            )
        except Exception as exc:
            return ToolResult(
                ToolExecutionStatus.FAILED,
                "whatsapp_read_conversation",
                message="Sohbet içeriği okunamadı.",
                error=f"uia_{type(exc).__name__}",
            )
        if rows is None:
            return ToolResult(
                ToolExecutionStatus.BLOCKED,
                "whatsapp_read_conversation",
                message="WhatsApp penceresi bulunamadı.",
                error="window_not_found",
                verified=True,
            )
        messages = messages_from_rows(rows)[-bounded:]
        chat = await self.current_chat()
        summary = f"{len(messages)} mesaj okundu"
        summary += f" ({chat['title']})." if chat else "."
        return ToolResult(
            ToolExecutionStatus.SUCCESS,
            "whatsapp_read_conversation",
            message=summary,
            data={
                "messages": messages,
                "lines": [message_line(item) for item in messages],
                "chat": chat["title"] if chat else None,
                "group": chat["group"] if chat else None,
            },
            verified=bool(messages),
        )

    async def _open_chat_by_name(
        self, contact: str, message: str | None
    ) -> ToolResult:
        """Open an existing chat by its visible name in the chat list.

        Works with an empty contact book: whoever appears in the
        WhatsApp chat list is reachable by name alone. New numbers
        still require 'whatsapp_add_contact'.
        """
        if not await self._ensure_window():
            return ToolResult(
                ToolExecutionStatus.BLOCKED,
                "whatsapp_open_chat",
                message="WhatsApp başlatılamadı.",
                error="window_not_found",
                verified=True,
            )
        client = self._uia_client()
        try:
            row = await asyncio.to_thread(
                client.click_item_by_name, _WINDOW_TITLE, contact
            )
        except Exception as exc:
            return ToolResult(
                ToolExecutionStatus.FAILED,
                "whatsapp_open_chat",
                message="Sohbet listesi taranamadı.",
                error=f"uia_{type(exc).__name__}",
            )
        if row is None:
            return ToolResult(
                ToolExecutionStatus.FAILED,
                "whatsapp_open_chat",
                message=(
                    f"'{contact}' rehberde kayıtlı değil ve sohbet "
                    "listesinde de görünmüyor. Numarasını "
                    "'whatsapp_add_contact' ile ekleyebilirsin."
                ),
                error="contact_not_found",
            )

        def composer_open() -> bool:
            return any(
                client.find_edit_value(_WINDOW_TITLE, hint)
                for hint in _COMPOSER_HINTS
            )

        placed = True
        if message and message.strip():
            # Typed keystrokes are live: a newline would press Enter
            # and SEND from a tool that promises it never sends.
            # Collapse all whitespace (and thus every control
            # character) into single spaces before anything is typed.
            draft = " ".join(message.split())

            def place() -> bool:
                for hint in _COMPOSER_HINTS:
                    if client.type_into_edit(
                        _WINDOW_TITLE, hint, draft
                    ):
                        return True
                return False

            placed = await asyncio.to_thread(place)
        verified = await asyncio.to_thread(composer_open)
        return ToolResult(
            ToolExecutionStatus.SUCCESS
            if placed
            else ToolExecutionStatus.PARTIAL,
            "whatsapp_open_chat",
            message=(
                f"'{contact}' sohbeti açıldı"
                + (
                    "; mesaj yazı kutusuna yerleştirildi."
                    if message and placed
                    else "."
                )
            ),
            data={"contact": contact, "via": "chat_list"},
            error=None if placed else "composer_not_found",
            verified=verified,
        )

    async def open_chat(
        self, contact: str, message: str | None = None
    ) -> ToolResult:
        phone = self._resolve_phone(contact)
        if phone is None:
            return await self._open_chat_by_name(contact, message)
        uri = f"whatsapp://send?phone={phone}"
        if message and message.strip():
            uri += "&text=" + urllib.parse.quote(message.strip())
        if not self._uri.open(uri):
            return ToolResult(
                ToolExecutionStatus.FAILED,
                "whatsapp_open_chat",
                message="WhatsApp sohbeti açılamadı.",
                error="uri_launch_failed",
            )
        # A cold start takes far longer than a foreground switch, so
        # poll for the window instead of sleeping a fixed beat.
        client = self._uia_client()
        window_present = False
        deadline = time.monotonic() + 18.0
        while time.monotonic() < deadline:
            await asyncio.sleep(1.0)
            if await asyncio.to_thread(
                client.window_exists, _WINDOW_TITLE
            ):
                window_present = True
                await asyncio.sleep(1.5)
                break
        return ToolResult(
            ToolExecutionStatus.SUCCESS,
            "whatsapp_open_chat",
            message=(
                f"'{contact}' sohbeti açıldı"
                + (
                    "; mesaj yazı kutusuna yerleştirildi, göndermek "
                    "sana kalmış."
                    if message
                    else "."
                )
            ),
            data={"contact": contact},
            verified=window_present,
        )

    async def send_message(
        self,
        contact: str,
        message: str,
        *,
        typing_seconds_per_char: float | None = None,
    ) -> ToolResult:
        """Send one message; with a typing pace, type it like a person.

        The paced path opens the chat without a prefilled draft and types
        the text keystroke by keystroke, which is what the other side sees
        as "yazıyor…". Everything after that - the verified send button,
        the honest PARTIAL when it cannot be verified - is the same.
        """
        normalized = message.strip()
        if not normalized:
            return ToolResult(
                ToolExecutionStatus.FAILED,
                "whatsapp_send_message",
                message="Mesaj boş olamaz.",
                error="empty_message",
            )
        if typing_seconds_per_char:
            opened = await self.open_chat(contact)
            if opened.status is not ToolExecutionStatus.SUCCESS:
                opened.tool_name = "whatsapp_send_message"
                return opened
            draft = " ".join(normalized.split())
            client = self._uia_client()
            pace = float(typing_seconds_per_char)

            def place() -> bool:
                for hint in _COMPOSER_HINTS:
                    if client.type_into_edit(
                        _WINDOW_TITLE, hint, draft, per_char_seconds=pace
                    ):
                        return True
                return False

            if not await asyncio.to_thread(place):
                return ToolResult(
                    ToolExecutionStatus.PARTIAL,
                    "whatsapp_send_message",
                    message="Yazı kutusu bulunamadı; mesaj yazılamadı.",
                    data={"contact": contact},
                    error="composer_not_found",
                )
        else:
            opened = await self.open_chat(contact, normalized)
            if opened.status is not ToolExecutionStatus.SUCCESS:
                opened.tool_name = "whatsapp_send_message"
                return opened
        await asyncio.sleep(1.2)
        client = self._uia_client()
        try:
            invoked = await asyncio.to_thread(
                client.invoke_button,
                _WINDOW_TITLE,
                _SEND_BUTTON_NAMES,
            )
        except Exception:
            invoked = False
        if invoked:
            return ToolResult(
                ToolExecutionStatus.SUCCESS,
                "whatsapp_send_message",
                message=f"Mesaj '{contact}' kişisine gönderildi.",
                data={"contact": contact},
                verified=True,
            )
        return ToolResult(
            ToolExecutionStatus.PARTIAL,
            "whatsapp_send_message",
            message=(
                "Mesaj yazı kutusuna yerleştirildi ama gönder düğmesi "
                "doğrulanamadı; göndermek için WhatsApp'ta Enter'a "
                "basman yeterli."
            ),
            data={"contact": contact},
            error="send_button_not_verified",
        )

    # ------------------------------------------------------------------

    def register_tools(self, executor: Any) -> None:
        def define(
            name: str,
            description: str,
            *,
            risk: RiskLevel = RiskLevel.READ_ONLY,
            confirm: bool = False,
            timeout: float = 25.0,
        ) -> ToolDefinition:
            return ToolDefinition(
                name=name,
                description=description,
                risk_level=risk,
                requires_confirmation=confirm,
                version="1.0.0",
                capabilities=frozenset({"whatsapp", "messaging"}),
                tags=frozenset({"integration", "whatsapp"}),
                timeout_seconds=timeout,
                metadata={
                    "verification_strategy": "uia_observation",
                    "sensitive_output": True,
                },
            )

        def add_contact(name: str, phone: str) -> ToolResult:
            return self.add_contact(name, phone)

        def list_contacts() -> ToolResult:
            return self.list_contacts()

        async def read_chats(limit: int = 8, launch: bool = True) -> ToolResult:
            return await self.read_recent_chats(limit, launch=launch)

        async def read_conversation(limit: int = 12) -> ToolResult:
            return await self.read_open_conversation(limit)

        async def open_chat(
            contact: str, message: str = ""
        ) -> ToolResult:
            return await self.open_chat(contact, message or None)

        async def send_message(
            contact: str, message: str
        ) -> ToolResult:
            return await self.send_message(contact, message)

        executor.register(
            define(
                "whatsapp_add_contact",
                "JARVIS rehberine WhatsApp kişisi ekle "
                "(ad ve +90... numara).",
                risk=RiskLevel.MEDIUM,
                confirm=True,
            ),
            add_contact,
            source="integration:whatsapp",
        )
        executor.register(
            define(
                "whatsapp_list_contacts",
                "JARVIS rehberindeki WhatsApp kişilerini listele.",
            ),
            list_contacts,
            source="integration:whatsapp",
        )
        executor.register(
            define(
                "whatsapp_read_chats",
                "WhatsApp sohbet listesini oku: kimden kaç okunmamış "
                "mesaj var, son mesaj ne zaman. Pencereyi öne getirmez.",
            ),
            read_chats,
            source="integration:whatsapp",
        )
        executor.register(
            define(
                "whatsapp_read_conversation",
                "Açık olan WhatsApp sohbetindeki son mesajları kimin "
                "yazdığıyla birlikte oku. Pencereyi öne getirmez.",
            ),
            read_conversation,
            source="integration:whatsapp",
        )
        executor.register(
            define(
                "whatsapp_open_chat",
                "Bir kişinin WhatsApp sohbetini aç (rehberden veya "
                "sohbet listesindeki adıyla); istersen mesajı kutuya "
                "hazır yaz (göndermez).",
                risk=RiskLevel.LOW,
                timeout=45.0,
            ),
            open_chat,
            source="integration:whatsapp",
        )
        executor.register(
            define(
                "whatsapp_send_message",
                "Bir kişiye WhatsApp mesajı GÖNDER (rehberden veya "
                "sohbet listesindeki adıyla). Onay gerektirir.",
                risk=RiskLevel.HIGH,
                confirm=True,
                timeout=60.0,
            ),
            send_message,
            source="integration:whatsapp",
        )
