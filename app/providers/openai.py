from __future__ import annotations

import base64
import re
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


_RETRY_SECONDS_PATTERN = re.compile(
    r"retry in\s+(\d+(?:\.\d+)?)\s*s", re.IGNORECASE
)


def _retry_seconds_from_text(text: str) -> float | None:
    match = _RETRY_SECONDS_PATTERN.search(text or "")
    if match is None:
        return None
    try:
        return float(match.group(1))
    except ValueError:
        return None


WARM_UP_TIMEOUT_SECONDS = 5.0


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
                timeout=self._settings.provider_timeout_seconds,
                # ProviderGateway is the single retry owner. Disabling SDK
                # retries prevents one gateway attempt from multiplying into
                # several hidden HTTP requests.
                max_retries=0,
            )

    @property
    def name(self) -> str:
        return "openai"

    @property
    def provider_label(self) -> str:
        return "OpenAI"

    @property
    def is_configured(self) -> bool:
        return self._client is not None

    @property
    def capabilities(self) -> ModelCapabilities:
        return ModelCapabilities(
            text=True,
            streaming=True,
            structured_output=True,
            tool_calling=True,
            vision=True,
        )

    async def warm_up(self) -> bool:
        """Open the connection before the first real request.

        The first call of a process pays DNS, TLS and client set-up;
        a model listing is cheap, counts against no generation quota,
        and leaves a warm pooled connection behind. Failures are
        reported, never raised.
        """
        if self._client is None:
            return False
        try:
            await self._client.models.list(timeout=WARM_UP_TIMEOUT_SECONDS)
        except Exception:
            return False
        return True

    def _require_client(self):
        if self._client is None:
            raise ProviderConfigurationError(
                f"{self.provider_label} client is not configured.",
                provider=self.name,
            )
        return self._client

    def _messages(
        self,
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
            messages.extend(dict(message) for message in context_messages)
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
        images = request.metadata.get("images")
        if images is not None:
            content = self._image_content(request.text, images)
            replaced = False
            for message in reversed(messages):
                if message.get("role") == "user" and message.get("content") == request.text:
                    message["content"] = content
                    replaced = True
                    break
            if not replaced:
                messages.append({"role": "user", "content": content})
        return messages

    def _image_content(self, text: str, images) -> list[dict[str, Any]]:
        if not isinstance(images, list) or not images:
            raise ValueError("Vision images must be a non-empty list.")
        if len(images) > self._settings.vision_max_images:
            raise ValueError("Vision image count exceeds the configured limit.")
        content: list[dict[str, Any]] = [{"type": "text", "text": text}]
        total_bytes = 0
        for image in images:
            if not isinstance(image, dict):
                raise TypeError("Vision image entries must be objects.")
            data = image.get("data")
            mime_type = image.get("mime_type")
            detail = image.get("detail", self._settings.vision_detail)
            if not isinstance(data, (bytes, bytearray)) or not data:
                raise ValueError("Vision image data must be non-empty bytes.")
            if mime_type not in {"image/png", "image/jpeg", "image/webp", "image/gif"}:
                raise ValueError("Vision image type is not supported.")
            if detail not in {"low", "high", "original", "auto"}:
                raise ValueError("Vision detail level is invalid.")
            total_bytes += len(data)
            if total_bytes > self._settings.vision_max_encoded_bytes:
                raise ValueError("Vision image payload exceeds the configured limit.")
            encoded = base64.b64encode(data).decode("ascii")
            content.append(
                {
                    "type": "image_url",
                    "image_url": {
                        "url": f"data:{mime_type};base64,{encoded}",
                        "detail": detail,
                    },
                }
            )
        return content

    @staticmethod
    def _tool_calls(message) -> list[dict[str, Any]]:
        normalized: list[dict[str, Any]] = []
        for tool_call in getattr(message, "tool_calls", None) or []:
            function = getattr(tool_call, "function", None)
            item = {
                "id": getattr(tool_call, "id", None),
                "type": getattr(tool_call, "type", "function"),
                "function": {
                    "name": getattr(function, "name", None),
                    "arguments": getattr(function, "arguments", None),
                },
            }
            index = getattr(tool_call, "index", None)
            if isinstance(index, int) and not isinstance(index, bool):
                item["index"] = index
            extra_content = getattr(tool_call, "extra_content", None)
            if extra_content is None:
                model_extra = getattr(tool_call, "model_extra", None)
                if isinstance(model_extra, dict):
                    extra_content = model_extra.get("extra_content")
            if extra_content is not None:
                item["extra_content"] = extra_content
            normalized.append(item)
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
                f"{self.provider_label} authentication failed.",
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
            if retry_after_seconds is None:
                # Gemini reports the wait in the body ("Please retry in
                # 30.8s") rather than in a Retry-After header.
                retry_after_seconds = _retry_seconds_from_text(str(error))
            return ProviderRateLimitError(
                f"{self.provider_label} rate limit exceeded.",
                provider=self.name,
                status_code=status_code,
                retry_after_seconds=retry_after_seconds,
            )
        if isinstance(error, TimeoutError) or status_code == 408:
            return ProviderTimeoutError(
                f"{self.provider_label} request timed out.",
                provider=self.name,
                status_code=status_code,
            )
        if status_code is None or status_code >= 500:
            return ProviderUnavailableError(
                f"{self.provider_label} provider is unavailable.",
                provider=self.name,
                status_code=status_code,
            )
        return ProviderError(
            f"{self.provider_label} request failed with status {status_code}.",
            provider=self.name,
            status_code=status_code,
        )

    def _chat_request_options(
        self,
        selected_model: str,
    ) -> dict[str, Any]:
        """Provider-specific Chat Completions options."""
        return {}

    def _chat_request_options_for_request(
        self,
        selected_model: str,
        request: Request,
    ) -> dict[str, Any]:
        """Request-aware hook that preserves existing provider overrides."""
        return self._chat_request_options(selected_model)

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
        request_arguments.update(
            self._chat_request_options_for_request(
                selected_model,
                request,
            )
        )
        if response_format is not None:
            request_arguments["response_format"] = response_format
        try:
            response = await client.chat.completions.create(**request_arguments)
        except Exception as exc:
            raise self._translate_error(exc) from exc

        choices = getattr(response, "choices", None) or []
        if not choices:
            raise ProviderInvalidResponseError(
                f"{self.provider_label} response did not contain a choice.",
                provider=self.name,
            )
        choice = choices[0]
        message = getattr(choice, "message", None)
        if message is None:
            raise ProviderInvalidResponseError(
                f"{self.provider_label} response did not contain a message.",
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
            request_arguments = {
                "model": selected_model,
                "messages": messages,
                "tools": tools or None,
                "stream": True,
                "stream_options": {
                    "include_usage": True
                },
            }
            request_arguments.update(
                self._chat_request_options_for_request(
                    selected_model,
                    request,
                )
            )
            stream = await client.chat.completions.create(
                **request_arguments
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
