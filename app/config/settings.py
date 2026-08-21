from __future__ import annotations

import math
import os
from dataclasses import dataclass


def _get_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)

    if value is None:
        return default

    normalized = value.strip().lower()
    if normalized in {
        "1",
        "true",
        "yes",
        "on",
    }:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError(
        f"Environment variable '{name}' must be a boolean."
    )


def _get_float(name: str, default: float = 30.0) -> float:
    value = os.getenv(name)

    if value is None:
        return default

    try:
        parsed = float(value)
    except ValueError as exc:
        raise ValueError(
            f"Environment variable '{name}' must be a number."
        ) from exc

    if not math.isfinite(parsed) or parsed < 0:
        raise ValueError(
            f"Environment variable '{name}' must be a finite non-negative number."
        )

    return parsed


def _get_non_negative_int(name: str, default: int = 0) -> int:
    value = os.getenv(name)

    if value is None:
        return default

    try:
        parsed = int(value)
    except ValueError as exc:
        raise ValueError(
            f"Environment variable '{name}' must be an integer."
        ) from exc

    if parsed < 0:
        raise ValueError(
            f"Environment variable '{name}' cannot be negative."
        )

    return parsed


def _get_positive_int(name: str, default: int) -> int:
    parsed = _get_non_negative_int(name, default)
    if parsed < 1:
        raise ValueError(
            f"Environment variable '{name}' must be greater than 0."
        )
    return parsed


@dataclass(frozen=True, slots=True)
class Settings:
    """Runtime configuration loaded from environment variables."""

    app_name: str = "JARVIS"
    environment: str = "development"
    debug: bool = False

    default_provider: str = "mock"
    default_model: str = "mock-model"
    openai_model: str | None = None

    provider_timeout_seconds: float = 30.0
    provider_max_retries: int = 2
    provider_retry_backoff_seconds: float = 0.25
    provider_fallback_enabled: bool = True

    conversation_max_messages: int = 50
    conversation_max_characters: int = 50_000
    conversation_summary_max_characters: int = 4_000
    conversation_system_prompt: str | None = None

    api_key: str | None = None
    api_base_url: str | None = None

    def __post_init__(self) -> None:
        if not self.default_provider.strip():
            raise ValueError("default_provider cannot be empty.")
        if not self.default_model.strip():
            raise ValueError("default_model cannot be empty.")
        if self.openai_model is not None and not self.openai_model.strip():
            raise ValueError("openai_model cannot be empty when set.")
        if self.provider_timeout_seconds <= 0:
            raise ValueError("provider_timeout_seconds must be greater than 0.")
        if self.provider_max_retries < 0:
            raise ValueError("provider_max_retries cannot be negative.")
        if self.provider_retry_backoff_seconds < 0:
            raise ValueError(
                "provider_retry_backoff_seconds cannot be negative."
            )
        if self.conversation_max_messages < 2:
            raise ValueError("conversation_max_messages must be at least 2.")
        if self.conversation_max_characters < 100:
            raise ValueError("conversation_max_characters must be at least 100.")
        if self.conversation_summary_max_characters < 100:
            raise ValueError(
                "conversation_summary_max_characters must be at least 100."
            )

    @property
    def openai_api_key(self) -> str | None:
        return self.api_key

    @property
    def openai_base_url(self) -> str | None:
        return self.api_base_url

    @classmethod
    def from_environment(cls) -> Settings:
        return cls(
            app_name=os.getenv("JARVIS_APP_NAME", "JARVIS"),
            environment=os.getenv(
                "JARVIS_ENVIRONMENT",
                "development",
            ),
            debug=_get_bool("JARVIS_DEBUG"),
            default_provider=os.getenv(
                "JARVIS_DEFAULT_PROVIDER",
                "mock",
            ),
            default_model=os.getenv(
                "JARVIS_DEFAULT_MODEL",
                "mock-model",
            ),
            openai_model=os.getenv("JARVIS_OPENAI_MODEL"),
            provider_timeout_seconds=_get_float(
                "JARVIS_PROVIDER_TIMEOUT",
                30.0,
            ),
            provider_max_retries=_get_non_negative_int(
                "JARVIS_PROVIDER_MAX_RETRIES",
                2,
            ),
            provider_retry_backoff_seconds=_get_float(
                "JARVIS_PROVIDER_RETRY_BACKOFF",
                0.25,
            ),
            provider_fallback_enabled=_get_bool(
                "JARVIS_PROVIDER_FALLBACK",
                True,
            ),
            conversation_max_messages=_get_positive_int(
                "JARVIS_CONVERSATION_MAX_MESSAGES",
                50,
            ),
            conversation_max_characters=_get_positive_int(
                "JARVIS_CONVERSATION_MAX_CHARACTERS",
                50_000,
            ),
            conversation_summary_max_characters=_get_positive_int(
                "JARVIS_CONVERSATION_SUMMARY_MAX_CHARACTERS",
                4_000,
            ),
            conversation_system_prompt=os.getenv(
                "JARVIS_CONVERSATION_SYSTEM_PROMPT"
            ),
            api_key=os.getenv("JARVIS_API_KEY"),
            api_base_url=os.getenv(
                "JARVIS_API_BASE_URL",
                None,
            ),
        )

