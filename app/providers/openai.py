from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

from openai import AsyncOpenAI

from app.config.settings import Settings
from app.core.models import Context, Request
from app.providers.base import (
    AIProvider,
    ModelCapabilities,
    ModelResponse,
    ModelStreamChunk,
    ProviderAuthenticationError,
    ProviderConfigurationError,
    ProviderError,
    ProviderInvalidResponseError,
    ProviderRateLimitError,
    ProviderTimeoutError,
    ProviderUnavailableError,
)


class OpenAIProvider(AIProvider):
    """Translate OpenAI SDK contracts into provider-neutral JARVIS models."""

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        client: Any | None = None,
    ) -> None:
        self._settings = settings or Settings.from_environment()
        self._client = client
        if self._client is None and self._settings.api_key:
            self._client = AsyncOpenAI(
                api_key=self._settings.api_key,
                base_url=self._settings.api_base_url or None,
            )

    @property
    def name(self) -> str:
        return "openai"

    @property
    def capabilities(self) -> ModelCapabilities:
        return ModelCapabilities(
            text=True,
            streaming=True,
            structured_output=True,
            tool_calling=True,
            vision=True,
        )

    def _require_client(self):
        if self._client is None:
            raise ProviderConfigurationError(
                "OpenAI client is not configured.",
                provider=self.name,
            )
        return self._client

    @staticmethod
    def _messages(
        request: Request,
        context: Context,
        system_prompt: str | None,
    ) -> list[dict[str, Any]]:
        messages: list[dict[str, Any]] = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.extend(
            {
                "role": "system",
                "content": f"Relevant memory: {memory}",
            }
            for memory in context.memories
        )
        context_messages = context.values.get("messages", [])
        if context_messages:
            if not isinstance(context_messages, list) or not all(
                isinstance(message, dict) for message in context_messages
            ):
                raise ValueError("Context messages must be a list of objects.")
            messages.extend(context_messages)
        request_is_present = (
            context.values.get("conversation_request_id")
            == str(request.request_id)
        )
        active_tool_chain = any(
            message.get("role") == "tool"
            or (
                message.get("role") == "assistant"
                and message.get("tool_calls")
            )
            for message in context_messages
        )
        last_matches_request = bool(context_messages) and (
            context_messages[-1].get("role") == "user"
            and context_messages[-1].get("content") == request.text
        )
        if not (
            request_is_present
            or active_tool_chain
            or last_matches_request
        ):
            messages.append({"role": "user", "content": request.text})
        return messages

    @staticmethod
    def _tool_calls(message) -> list[dict[str, Any]]:
        normalized: list[dict[str, Any]] = []
        for tool_call in getattr(message, "tool_calls", None) or []:
            function = getattr(tool_call, "function", None)
            normalized.append(
                {
                    "id": getattr(tool_call, "id", None),
                    "type": getattr(tool_call, "type", "function"),
                    "function": {
                        "name": getattr(function, "name", None),
                        "arguments": getattr(function, "arguments", None),
                    },
                }
            )
        return normalized

    @staticmethod
    def _usage(raw_usage) -> dict[str, int]:
        if raw_usage is None:
            return {}
        return {
            key: value
            for key, value in {
                "prompt_tokens": getattr(raw_usage, "prompt_tokens", None),
                "completion_tokens": getattr(
                    raw_usage,
                    "completion_tokens",
                    None,
                ),
                "total_tokens": getattr(raw_usage, "total_tokens", None),
            }.items()
            if isinstance(value, int) and value >= 0
        }

    def _translate_error(self, error: Exception) -> ProviderError:
        if isinstance(error, ProviderError):
            return error
        status_code = getattr(error, "status_code", None)
        if status_code in {401, 403}:
            return ProviderAuthenticationError(
                "OpenAI authentication failed.",
                provider=self.name,
                status_code=status_code,
            )
        if status_code == 429:
            response = getattr(error, "response", None)
            headers = getattr(response, "headers", None) or {}
            retry_after = headers.get("retry-after")
            try:
                retry_after_seconds = (
                    float(retry_after) if retry_after is not None else None
                )
            except (TypeError, ValueError):
                retry_after_seconds = None
            return ProviderRateLimitError(
                "OpenAI rate limit exceeded.",
                provider=self.name,
                status_code=status_code,
                retry_after_seconds=retry_after_seconds,
            )
        if isinstance(error, TimeoutError) or status_code == 408:
            return ProviderTimeoutError(
                "OpenAI request timed out.",
                provider=self.name,
                status_code=status_code,
            )
        if status_code is None or status_code >= 500:
            return ProviderUnavailableError(
                "OpenAI provider is unavailable.",
                provider=self.name,
                status_code=status_code,
            )
        return ProviderError(
            f"OpenAI request failed with status {status_code}.",
            provider=self.name,
            status_code=status_code,
        )

    async def generate(
        self,
        request: Request,
        context: Context,
        *,
        model: str | None = None,
        system_prompt: str | None = None,
        tools: list[dict[str, Any]] | None = None,
        response_format: dict[str, Any] | None = None,
    ) -> ModelResponse:
        client = self._require_client()
        selected_model = (
            model
            or self._settings.openai_model
            or self._settings.default_model
        )
        messages = self._messages(request, context, system_prompt)
        request_arguments = {
            "model": selected_model,
            "messages": messages,
            "tools": tools or None,
        }
        if response_format is not None:
            request_arguments["response_format"] = response_format
        try:
            response = await client.chat.completions.create(**request_arguments)
        except Exception as exc:
            raise self._translate_error(exc) from exc

        choices = getattr(response, "choices", None) or []
        if not choices:
            raise ProviderInvalidResponseError(
                "OpenAI response did not contain a choice.",
                provider=self.name,
            )
        choice = choices[0]
        message = getattr(choice, "message", None)
        if message is None:
            raise ProviderInvalidResponseError(
                "OpenAI response did not contain a message.",
                provider=self.name,
            )
        return ModelResponse(
            text=getattr(message, "content", "") or "",
            model=selected_model,
            provider=self.name,
            finish_reason=getattr(choice, "finish_reason", None),
            tool_calls=self._tool_calls(message),
            usage=self._usage(getattr(response, "usage", None)),
        )

    async def stream(
        self,
        request: Request,
        context: Context,
        *,
        model: str | None = None,
        system_prompt: str | None = None,
        tools: list[dict[str, Any]] | None = None,
    ) -> AsyncIterator[ModelStreamChunk]:
        client = self._require_client()
        selected_model = (
            model
            or self._settings.openai_model
            or self._settings.default_model
        )
        messages = self._messages(request, context, system_prompt)
        try:
            stream = await client.chat.completions.create(
                model=selected_model,
                messages=messages,
                tools=tools or None,
                stream=True,
                stream_options={"include_usage": True},
            )
            async for raw_chunk in stream:
                choices = getattr(raw_chunk, "choices", None) or []
                choice = choices[0] if choices else None
                delta = getattr(choice, "delta", None) if choice else None
                yield ModelStreamChunk(
                    text=getattr(delta, "content", "") or "",
                    model=getattr(raw_chunk, "model", None) or selected_model,
                    provider=self.name,
                    finish_reason=(
                        getattr(choice, "finish_reason", None)
                        if choice is not None
                        else None
                    ),
                    tool_calls=(
                        self._tool_calls(delta) if delta is not None else []
                    ),
                    usage=self._usage(getattr(raw_chunk, "usage", None)),
                )
        except Exception as exc:
            raise self._translate_error(exc) from exc
