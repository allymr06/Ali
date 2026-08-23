"""General system controls: browser and audio volume.

Browser navigation validates the URL shape locally (https/http only,
no embedded credentials) before handing it to the OS default browser,
so a model-generated string can never smuggle another scheme.
"""

from __future__ import annotations

import re
import urllib.parse
from typing import Any

from app.core.models import (
    RiskLevel,
    ToolDefinition,
    ToolExecutionStatus,
    ToolResult,
)
from app.integrations.runtime import MediaKeySender, UriLauncher

_VK_VOLUME_MUTE = 0xAD
_VK_VOLUME_DOWN = 0xAE
_VK_VOLUME_UP = 0xAF


def _validate_web_url(raw: str) -> tuple[str | None, str | None]:
    candidate = raw.strip()
    if not candidate:
        return None, "URL boş olamaz."
    scheme_prefix = re.match(r"^[A-Za-z][A-Za-z0-9+.\-]*:", candidate)
    if scheme_prefix and not candidate.lower().startswith(
        ("http://", "https://")
    ):
        return None, "Yalnızca http ve https adresleri açılabilir."
    if "://" not in candidate:
        candidate = "https://" + candidate
    parsed = urllib.parse.urlsplit(candidate)
    if parsed.scheme not in {"http", "https"}:
        return None, "Yalnızca http ve https adresleri açılabilir."
    if not parsed.hostname:
        return None, "Geçerli bir alan adı gerekli."
    if parsed.username or parsed.password:
        return None, "Kimlik bilgisi gömülü adresler reddedilir."
    if any(ch.isspace() for ch in candidate):
        return None, "URL boşluk içeremez."
    try:
        parsed.port
    except ValueError:
        return None, "Geçersiz bağlantı noktası."
    return candidate, None


class SystemControlIntegration:
    def __init__(
        self,
        *,
        uri_launcher: UriLauncher | None = None,
        media_keys: MediaKeySender | None = None,
    ) -> None:
        self._uri = uri_launcher or UriLauncher()
        self._keys = media_keys or MediaKeySender()

    def open_website(self, url: str) -> ToolResult:
        validated, problem = _validate_web_url(url)
        if validated is None:
            return ToolResult(
                ToolExecutionStatus.FAILED,
                "open_website",
                message=problem or "Geçersiz URL.",
                error="invalid_url",
            )
        if not self._uri.open(validated):
            return ToolResult(
                ToolExecutionStatus.FAILED,
                "open_website",
                message="Varsayılan tarayıcı açılamadı.",
                error="launch_failed",
            )
        return ToolResult(
            ToolExecutionStatus.SUCCESS,
            "open_website",
            message=f"Tarayıcıda açıldı: {validated}",
            data={"url": validated},
        )

    def web_search(self, query: str) -> ToolResult:
        normalized = query.strip()
        if not normalized:
            return ToolResult(
                ToolExecutionStatus.FAILED,
                "open_web_search",
                message="Arama metni boş olamaz.",
                error="empty_query",
            )
        url = "https://www.google.com/search?q=" + urllib.parse.quote(
            normalized
        )
        return self.open_website(url)

    def adjust_volume(
        self, direction: str, steps: int = 4
    ) -> ToolResult:
        action = direction.strip().casefold()
        bounded = max(1, min(int(steps), 20))
        key = {
            "up": _VK_VOLUME_UP,
            "yukari": _VK_VOLUME_UP,
            "down": _VK_VOLUME_DOWN,
            "asagi": _VK_VOLUME_DOWN,
            "mute": _VK_VOLUME_MUTE,
            "sessiz": _VK_VOLUME_MUTE,
        }.get(action)
        if key is None:
            return ToolResult(
                ToolExecutionStatus.FAILED,
                "system_volume",
                message="Yön 'yukari', 'asagi' veya 'sessiz' olmalı.",
                error="invalid_direction",
            )
        repeats = 1 if key == _VK_VOLUME_MUTE else bounded
        for _ in range(repeats):
            if not self._keys.send(key):
                return ToolResult(
                    ToolExecutionStatus.FAILED,
                    "system_volume",
                    message="Ses tuşu gönderilemedi.",
                    error="media_key_send_failed",
                )
        label = {
            _VK_VOLUME_UP: f"Ses {repeats} kademe artırıldı.",
            _VK_VOLUME_DOWN: f"Ses {repeats} kademe azaltıldı.",
            _VK_VOLUME_MUTE: "Sessize alma anahtarı değiştirildi.",
        }[key]
        return ToolResult(
            ToolExecutionStatus.SUCCESS,
            "system_volume",
            message=label,
        )

    def register_tools(self, executor: Any) -> None:
        def define(
            name: str,
            description: str,
            *,
            risk: RiskLevel = RiskLevel.LOW,
        ) -> ToolDefinition:
            return ToolDefinition(
                name=name,
                description=description,
                risk_level=risk,
                version="1.0.0",
                capabilities=frozenset({"system", "control"}),
                tags=frozenset({"integration", "system"}),
                timeout_seconds=10.0,
                metadata={"verification_strategy": "best_effort"},
            )

        def open_website(url: str) -> ToolResult:
            return self.open_website(url)

        def open_web_search(query: str) -> ToolResult:
            return self.web_search(query)

        def system_volume(
            direction: str, steps: int = 4
        ) -> ToolResult:
            return self.adjust_volume(direction, steps)

        executor.register(
            define(
                "open_website",
                "Bir web adresini varsayılan tarayıcıda aç "
                "(yalnızca http/https).",
            ),
            open_website,
            source="integration:system",
        )
        executor.register(
            define(
                "open_web_search",
                "Varsayılan tarayıcıda bir Google araması aç.",
            ),
            open_web_search,
            source="integration:system",
        )
        executor.register(
            define(
                "system_volume",
                "Sistem ses düzeyini ayarla: yukari/asagi (+kademe) "
                "veya sessiz.",
            ),
            system_volume,
            source="integration:system",
        )
