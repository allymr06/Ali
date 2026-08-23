"""WhatsApp Desktop integration.

Reading uses the Windows UI Automation accessibility tree of the real
WhatsApp window — native metadata, not pixel scraping. Sending is a
HIGH-risk, approval-gated action that drives the official
whatsapp://send deep link, then invokes the send button through UI
Automation and verifies the result. If any step cannot be verified the
message is left unsent in the input box, never fired blindly.

Contacts live in a user-editable JSON file inside the JARVIS state
directory; phone numbers never enter the model prompt unless the user
placed them there.
"""

from __future__ import annotations

import asyncio
import json
import re
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

    async def read_recent_chats(self, limit: int = 8) -> ToolResult:
        bounded = max(1, min(int(limit), 20))
        client = self._uia_client()
        try:
            entries = await asyncio.to_thread(
                client.read_items, _WINDOW_TITLE, limit=bounded
            )
        except Exception as exc:
            return ToolResult(
                ToolExecutionStatus.FAILED,
                "whatsapp_read_chats",
                message="Sohbet listesi okunamadı.",
                error=f"uia_{type(exc).__name__}",
            )
        if entries is None:
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
        return ToolResult(
            ToolExecutionStatus.SUCCESS,
            "whatsapp_read_chats",
            message=f"{len(entries)} sohbet girdisi okundu.",
            data={"chats": entries},
            verified=bool(entries),
        )

    async def open_chat(
        self, contact: str, message: str | None = None
    ) -> ToolResult:
        phone = self._resolve_phone(contact)
        if phone is None:
            return ToolResult(
                ToolExecutionStatus.FAILED,
                "whatsapp_open_chat",
                message=(
                    f"'{contact}' rehberde yok. Önce "
                    "'whatsapp_add_contact' ile numarasını ekle."
                ),
                error="unknown_contact",
            )
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
        await asyncio.sleep(2.0)
        client = self._uia_client()
        window_present = await asyncio.to_thread(
            client.window_exists, _WINDOW_TITLE
        )
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
        self, contact: str, message: str
    ) -> ToolResult:
        normalized = message.strip()
        if not normalized:
            return ToolResult(
                ToolExecutionStatus.FAILED,
                "whatsapp_send_message",
                message="Mesaj boş olamaz.",
                error="empty_message",
            )
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

        async def read_chats(limit: int = 8) -> ToolResult:
            return await self.read_recent_chats(limit)

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
                "Açık WhatsApp penceresindeki son sohbetleri "
                "erişilebilirlik ağacından oku.",
            ),
            read_chats,
            source="integration:whatsapp",
        )
        executor.register(
            define(
                "whatsapp_open_chat",
                "Bir kişinin WhatsApp sohbetini aç; istersen mesajı "
                "kutuya hazır yaz (göndermez).",
                risk=RiskLevel.LOW,
            ),
            open_chat,
            source="integration:whatsapp",
        )
        executor.register(
            define(
                "whatsapp_send_message",
                "Rehberdeki bir kişiye WhatsApp mesajı GÖNDER. "
                "Onay gerektirir.",
                risk=RiskLevel.HIGH,
                confirm=True,
                timeout=40.0,
            ),
            send_message,
            source="integration:whatsapp",
        )
