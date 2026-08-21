from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

from app.core.models import Context, Request
from app.providers.base import (
    AIProvider,
    ModelCapabilities,
    ModelResponse,
    ModelStreamChunk,
    ProviderAuthenticationError,
    ProviderError,
    ProviderTimeoutError,
)


class ReliableProvider(AIProvider):
    """Provider wrapper that adds timeout and retry handling."""

    def __init__(
        self,
        provider: AIProvider,
        *,
        timeout_seconds: float = 30.0,
        max_retries: int = 2,
    ) -> None:
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be greater than 0.")

        if max_retries < 0:
            raise ValueError("max_retries must be greater than or equal to 0.")

        self._provider = provider
        self._timeout_seconds = timeout_seconds
        self._max_retries = max_retries

    @property
    def name(self) -> str:
        return self._provider.name

    @property
    def capabilities(self) -> ModelCapabilities:
        return self._provider.capabilities

    async def generate(
        self,
        request: Request,
        context: Context,
        *,
        model: str | None = None,
        system_prompt: str | None = None,
        tools: list[dict[str, object]] | None = None,
        response_format: dict[str, object] | None = None,
    ) -> ModelResponse:
        attempts = self._max_retries + 1

        for attempt in range(attempts):
            try:
                provider_kwargs = {
                    "model": model,
                    "system_prompt": system_prompt,
                    "tools": tools,
                }
                if response_format is not None:
                    provider_kwargs["response_format"] = response_format
                return await asyncio.wait_for(
                    self._provider.generate(request, context, **provider_kwargs),
                    timeout=self._timeout_seconds,
                )
            except ProviderAuthenticationError:
                raise
            except TimeoutError as exc:
                error = ProviderTimeoutError(
                    "Provider request timed out.",
                    provider=self.name,
                )
                if attempt >= self._max_retries:
                    raise error from exc
            except ProviderError as exc:
                if not exc.retryable or attempt >= self._max_retries:
                    raise

        raise RuntimeError("Provider execution failed unexpectedly.")

    async def stream(
        self,
        request: Request,
        context: Context,
        *,
        model: str | None = None,
        system_prompt: str | None = None,
        tools: list[dict[str, object]] | None = None,
    ) -> AsyncIterator[ModelStreamChunk]:
        try:
            async with asyncio.timeout(self._timeout_seconds):
                async for chunk in self._provider.stream(
                    request,
                    context,
                    model=model,
                    system_prompt=system_prompt,
                    tools=tools,
                ):
                    yield chunk
        except TimeoutError as exc:
            raise ProviderTimeoutError(
                "Provider stream timed out.",
                provider=self.name,
            ) from exc
