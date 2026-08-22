from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from app.core.models import Context, Request


class ProviderCapability(str, Enum):
    TEXT = "text"
    STREAMING = "streaming"
    STRUCTURED_OUTPUT = "structured_output"
    TOOL_CALLING = "tool_calling"
    VISION = "vision"


@dataclass(frozen=True, slots=True)
class ModelCapabilities:
    """Capabilities exposed by an AI model provider."""

    text: bool = True
    streaming: bool = False
    structured_output: bool = False
    tool_calling: bool = False
    vision: bool = False

    def as_set(self) -> frozenset[ProviderCapability]:
        return frozenset(
            capability
            for capability in ProviderCapability
            if getattr(self, capability.value)
        )

    def missing(
        self,
        required: set[ProviderCapability] | frozenset[ProviderCapability],
    ) -> frozenset[ProviderCapability]:
        return frozenset(required) - self.as_set()

    def supports(
        self,
        required: set[ProviderCapability] | frozenset[ProviderCapability],
    ) -> bool:
        return not self.missing(required)


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

    def __post_init__(self) -> None:
        if not isinstance(self.text, str):
            raise TypeError("Model response text must be a string.")
        if not isinstance(self.model, str) or not self.model.strip():
            raise ValueError("Model response model cannot be empty.")
        if not isinstance(self.provider, str) or not self.provider.strip():
            raise ValueError("Model response provider cannot be empty.")
        self.model = self.model.strip()
        self.provider = self.provider.strip()
        if not isinstance(self.tool_calls, list) or not all(
            isinstance(item, dict) for item in self.tool_calls
        ):
            raise TypeError("Model response tool_calls must be a list of objects.")
        if not isinstance(self.usage, dict) or any(
            not isinstance(value, int) or value < 0
            for value in self.usage.values()
        ):
            raise ValueError("Model response usage must contain non-negative integers.")


@dataclass(slots=True)
class ModelStreamChunk:
    """Provider-independent streaming event."""

    text: str
    model: str
    provider: str
    finish_reason: str | None = None
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    usage: dict[str, int] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        ModelResponse(
            text=self.text,
            model=self.model,
            provider=self.provider,
            finish_reason=self.finish_reason,
            tool_calls=self.tool_calls,
            usage=self.usage,
            metadata=self.metadata,
        )
        self.model = self.model.strip()
        self.provider = self.provider.strip()


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

    @property
    def is_configured(self) -> bool:
        """
        Return whether this provider is ready for live use.

        Provider implementations backed by credentials or
        external clients should override this property.
        """
        return True

    def try_deterministic_finalization(
        self,
        request: Request,
        context: Context,
        *,
        model: str | None = None,
    ) -> ModelResponse | None:
        """
        Return a fully verified response without model inference.

        Providers should override this only when the final text can
        be derived deterministically from the current verified
        execution context.
        """
        return None

    @abstractmethod
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
    ) -> AsyncIterator[ModelStreamChunk]:
        """
        Stream response tokens/chunks.

        Providers that support streaming should override this method.
        """
        raise NotImplementedError(
            f"Provider '{self.name}' does not support streaming."
        )
        yield  # pragma: no cover


class ProviderError(RuntimeError):
    """Base exception for classified provider failures."""

    retryable = False

    def __init__(
        self,
        message: str,
        *,
        provider: str | None = None,
        status_code: int | None = None,
        retry_after_seconds: float | None = None,
    ) -> None:
        super().__init__(message)
        self.provider = provider
        self.status_code = status_code
        self.retry_after_seconds = retry_after_seconds


class ProviderAuthenticationError(ProviderError):
    """Raised when provider authentication fails."""


class ProviderRateLimitError(ProviderError):
    """Raised when a provider rate limit is reached."""

    retryable = True


class ProviderUnavailableError(ProviderError):
    """Raised when the provider cannot currently be reached."""

    retryable = True


class ProviderTimeoutError(ProviderError, TimeoutError):
    """Raised when a provider exceeds its execution deadline."""

    retryable = True


class ProviderInvalidResponseError(ProviderError):
    """Raised when a provider violates the gateway response contract."""


class ProviderCapabilityError(ProviderError):
    """Raised when a request needs an unsupported provider capability."""


class ProviderConfigurationError(ProviderUnavailableError):
    """Raised when provider configuration is missing or invalid."""

    retryable = False
