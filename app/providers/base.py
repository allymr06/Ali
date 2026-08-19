from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

from app.core.models import Context, Request


@dataclass(slots=True)
class ModelCapabilities:
    """Capabilities exposed by an AI model provider."""

    text: bool = True
    streaming: bool = False
    structured_output: bool = False
    tool_calling: bool = False
    vision: bool = False


@dataclass(slots=True)
class ModelResponse:
    """Provider-independent response returned by an AI model."""

    text: str
    model: str
    provider: str
    finish_reason: str | None = None
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    usage: dict[str, int] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)


class AIProvider(ABC):
    """
    Provider-independent interface for AI model integrations.

    JARVIS Core depends on this abstraction rather than a specific
    vendor SDK.
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """Return the unique provider identifier."""
        raise NotImplementedError

    @property
    @abstractmethod
    def capabilities(self) -> ModelCapabilities:
        """Return the capabilities supported by this provider."""
        raise NotImplementedError

    @abstractmethod
    async def generate(
        self,
        request: Request,
        context: Context,
        *,
        model: str | None = None,
        system_prompt: str | None = None,
        tools: list[dict[str, Any]] | None = None,
    ) -> ModelResponse:
        """Generate a response from the provider."""
        raise NotImplementedError

    async def stream(
        self,
        request: Request,
        context: Context,
        *,
        model: str | None = None,
        system_prompt: str | None = None,
        tools: list[dict[str, Any]] | None = None,
    ) -> AsyncIterator[str]:
        """
        Stream response tokens/chunks.

        Providers that support streaming should override this method.
        """
        raise NotImplementedError(
            f"Provider '{self.name}' does not support streaming."
        )
        yield  # pragma: no cover


class ProviderError(RuntimeError):
    """Base exception for provider failures."""


class ProviderAuthenticationError(ProviderError):
    """Raised when provider authentication fails."""


class ProviderRateLimitError(ProviderError):
    """Raised when a provider rate limit is reached."""


class ProviderUnavailableError(ProviderError):
    """Raised when the provider cannot currently be reached."""