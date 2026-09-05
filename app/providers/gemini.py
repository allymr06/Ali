from __future__ import annotations

from dataclasses import replace
from typing import Any

from app.config.provider_preferences import DEFAULT_GEMINI_MODEL
from app.config.settings import Settings
from app.core.models import Context, Request
from app.providers.openai import OpenAIProvider
from app.providers.reasoning import ReasoningPolicy


class GeminiProvider(OpenAIProvider):
    """Gemini adapter using Google's OpenAI-compatible API surface.

    Gemini 3 models attach a thought signature to every function call
    they make and reject a follow-up whose replayed function call has
    none. Model-made calls keep the signature the API returned; calls
    JARVIS injects itself (deterministic and fast-action routes) and
    calls stored before signatures were kept are replayed with the
    documented skip marker instead of failing the whole turn.
    """

    THOUGHT_SIGNATURE_SKIP = "skip_thought_signature_validator"

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

    @classmethod
    def _signed_tool_call(cls, tool_call: Any) -> Any:
        if not isinstance(tool_call, dict):
            return tool_call
        extra = tool_call.get("extra_content")
        google = extra.get("google") if isinstance(extra, dict) else None
        if isinstance(google, dict) and google.get("thought_signature"):
            return tool_call
        signed = dict(tool_call)
        signed["extra_content"] = {
            **(extra if isinstance(extra, dict) else {}),
            "google": {
                **(google if isinstance(google, dict) else {}),
                "thought_signature": cls.THOUGHT_SIGNATURE_SKIP,
            },
        }
        return signed

    def _messages(
        self,
        request: Request,
        context: Context,
        system_prompt: str | None,
    ) -> list[dict[str, Any]]:
        messages = super()._messages(request, context, system_prompt)
        for message in messages:
            tool_calls = message.get("tool_calls")
            if message.get("role") != "assistant" or not tool_calls:
                continue
            message["tool_calls"] = [
                self._signed_tool_call(tool_call) for tool_call in tool_calls
            ]
        return messages

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
