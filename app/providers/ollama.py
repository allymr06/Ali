from __future__ import annotations

from dataclasses import replace
from typing import Any

from app.config.provider_preferences import (
    DEFAULT_OLLAMA_MODEL,
)
from app.config.settings import Settings
from app.providers.base import (
    ModelCapabilities,
)
from app.providers.openai import (
    OpenAIProvider,
)


class OllamaProvider(OpenAIProvider):
    """
    Local Ollama adapter using its OpenAI-compatible API.

    The transport is intentionally shared with OpenAIProvider,
    while provider identity, configuration and capabilities
    remain independent.
    """

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        client: Any | None = None,
    ) -> None:
        active = (
            settings
            or Settings.from_environment()
        )

        self._ollama_enabled = (
            active.ollama_enabled
            or (
                active.default_provider
                .strip()
                .casefold()
                == "ollama"
            )
        )

        ollama_settings = replace(
            active,
            api_key="ollama",
            api_base_url=(
                active.ollama_base_url
            ),
            openai_model=(
                active.ollama_model
                or DEFAULT_OLLAMA_MODEL
            ),
        )

        super().__init__(
            ollama_settings,
            client=client,
        )

    @property
    def name(self) -> str:
        return "ollama"

    @property
    def provider_label(self) -> str:
        return "Ollama"

    @property
    def is_configured(self) -> bool:
        """
        Primary Ollama selection is usable immediately.

        As a fallback provider, Ollama participates only when
        explicitly enabled, preventing an offline local server
        from masking the original provider error.
        """
        return (
            self._ollama_enabled
            and self._client is not None
        )

    @property
    def capabilities(
        self,
    ) -> ModelCapabilities:
        # The current built-in default, llama3.2, is text-only.
        # Vision-capable Ollama models can receive their own
        # model profile later without overstating capability.
        return ModelCapabilities(
            text=True,
            streaming=True,
            structured_output=True,
            tool_calling=True,
            vision=False,
        )
