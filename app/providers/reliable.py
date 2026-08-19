from __future__ import annotations

import asyncio

from app.core.models import Context, Request
from app.providers.base import (
    AIProvider,
    ModelCapabilities,
    ModelResponse,
    ProviderError,
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
    ) -> ModelResponse:
        attempts = self._max_retries + 1

        for attempt in range(attempts):
            try:
                return await asyncio.wait_for(
                    self._provider.generate(
                        request,
                        context,
                        model=model,
                        system_prompt=system_prompt,
                        tools=tools,
                    ),
                    timeout=self._timeout_seconds,
                )
            except ProviderError:
                if attempt >= self._max_retries:
                    raise

        raise RuntimeError("Provider execution failed unexpectedly.")
