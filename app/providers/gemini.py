from __future__ import annotations

from dataclasses import replace
from typing import Any

from app.config.provider_preferences import DEFAULT_GEMINI_MODEL
from app.config.settings import Settings
from app.providers.openai import OpenAIProvider


class GeminiProvider(OpenAIProvider):
    """Gemini adapter using Google's OpenAI-compatible API surface."""

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        client: Any | None = None,
    ) -> None:
        active = settings or Settings.from_environment()
        gemini_settings = replace(
            active,
            api_key=active.gemini_api_key,
            api_base_url=active.gemini_base_url,
            openai_model=active.gemini_model or DEFAULT_GEMINI_MODEL,
        )
        super().__init__(gemini_settings, client=client)

    @property
    def name(self) -> str:
        return "gemini"

    @property
    def provider_label(self) -> str:
        return "Gemini"
