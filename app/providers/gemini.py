from __future__ import annotations

from dataclasses import replace
from typing import Any

from app.config.provider_preferences import DEFAULT_GEMINI_MODEL
from app.config.settings import Settings
from app.core.models import Request
from app.providers.openai import OpenAIProvider
from app.providers.reasoning import ReasoningPolicy


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

    def _chat_request_options_for_request(
        self,
        selected_model: str,
        request: Request,
    ) -> dict[str, Any]:
        metadata = dict(request.metadata)
        if ReasoningPolicy.explicitly_requests_deep_reasoning(request.text):
            metadata[ReasoningPolicy.DEEP_REASONING_METADATA_KEY] = True
        effort = ReasoningPolicy.select(
            task_type=request.metadata.get(
                "reasoning_task_type",
                request.metadata.get("task_type", "standard"),
            ),
            model=selected_model,
            config_override=self._settings.gemini_reasoning_effort,
            request_metadata=metadata,
        )
        request.metadata["_reasoning_level"] = effort.value

        return {
            "reasoning_effort": effort.value,
        }

    @property
    def name(self) -> str:
        return "gemini"

    @property
    def provider_label(self) -> str:
        return "Gemini"
