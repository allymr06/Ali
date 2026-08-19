from __future__ import annotations

import asyncio
from typing import Any

from app.config.settings import Settings
from app.core.models import Context, Request
from app.providers.base import (
    AIProvider,
    ModelCapabilities,
    ModelResponse,
    ProviderAuthenticationError,
    ProviderRateLimitError,
    ProviderUnavailableError,
)


class OpenAIProvider(AIProvider):
    """OpenAI-compatible provider adapter.

    The CoreEngine depends only on AIProvider, so this implementation
    can be registered or removed without changing the JARVIS core.
    """

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

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        client: Any | None = None,
    ) -> None:
        self._settings = settings or Settings.from_environment()
        self._client = client

    async def generate(
        self,
        request: Request,
        context: Context,
        *,
        model: str | None = None,
        system_prompt: str | None = None,
        tools: list[dict[str, Any]] | None = None,
    ) -> ModelResponse:
        if self._client is None:
            raise ProviderUnavailableError(
                "OpenAI client is not configured."
            )

        selected_model = (
            model
            or self._settings.default_model
        )

        messages: list[dict[str, Any]] = []

        if system_prompt:
            messages.append(
                {
                    "role": "system",
                    "content": system_prompt,
                }
            )

        for memory in context.memories:
            messages.append(
                {
                    "role": "system",
                    "content": f"Relevant memory: {memory}",
                }
            )

        messages.append(
            {
                "role": "user",
                "content": request.text,
            }
        )

        last_error: Exception | None = None

        for attempt in range(
            self._settings.provider_max_retries + 1
        ):
            try:
                response = await asyncio.wait_for(
                    self._client.chat.completions.create(
                        model=selected_model,
                        messages=messages,
                        tools=tools or None,
                    ),
                    timeout=self._settings.provider_timeout_seconds,
                )

                choice = response.choices[0]
                message = choice.message

                tool_calls: list[dict[str, Any]] = []

                for tool_call in (
                    getattr(message, "tool_calls", None) or []
                ):
                    tool_calls.append(
                        {
                            "id": getattr(
                                tool_call,
                                "id",
                                None,
                            ),
                            "type": getattr(
                                tool_call,
                                "type",
                                "function",
                            ),
                            "function": {
                                "name": getattr(
                                    getattr(
                                        tool_call,
                                        "function",
                                        None,
                                    ),
                                    "name",
                                    None,
                                ),
                                "arguments": getattr(
                                    getattr(
                                        tool_call,
                                        "function",
                                        None,
                                    ),
                                    "arguments",
                                    None,
                                ),
                            },
                        }
                    )

                usage = {}

                raw_usage = getattr(
                    response,
                    "usage",
                    None,
                )

                if raw_usage is not None:
                    usage = {
                        key: value
                        for key, value in {
                            "prompt_tokens": getattr(
                                raw_usage,
                                "prompt_tokens",
                                None,
                            ),
                            "completion_tokens": getattr(
                                raw_usage,
                                "completion_tokens",
                                None,
                            ),
                            "total_tokens": getattr(
                                raw_usage,
                                "total_tokens",
                                None,
                            ),
                        }.items()
                        if value is not None
                    }

                return ModelResponse(
                    text=getattr(
                        message,
                        "content",
                        "",
                    )
                    or "",
                    model=selected_model,
                    provider=self.name,
                    finish_reason=getattr(
                        choice,
                        "finish_reason",
                        None,
                    ),
                    tool_calls=tool_calls,
                    usage=usage,
                    metadata={
                        "attempt": attempt + 1,
                    },
                )

            except asyncio.TimeoutError as exc:
                last_error = exc

            except Exception as exc:
                last_error = exc

                status_code = getattr(
                    exc,
                    "status_code",
                    None,
                )

                if status_code == 401:
                    raise ProviderAuthenticationError(
                        "OpenAI authentication failed."
                    ) from exc

                if status_code == 429:
                    if attempt < self._settings.provider_max_retries:
                        await asyncio.sleep(
                            2 ** attempt
                        )
                        continue

                    raise ProviderRateLimitError(
                        "OpenAI rate limit exceeded."
                    ) from exc

                if attempt < self._settings.provider_max_retries:
                    await asyncio.sleep(
                        2 ** attempt
                    )
                    continue

        raise ProviderUnavailableError(
            "OpenAI provider failed after "
            f"{self._settings.provider_max_retries + 1} attempts."
        ) from last_error
