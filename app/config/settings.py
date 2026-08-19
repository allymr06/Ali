from __future__ import annotations

import os
from dataclasses import dataclass


def _get_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)

    if value is None:
        return default

    return value.strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


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

    if parsed < 0:
        raise ValueError(
            f"Environment variable '{name}' cannot be negative."
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


@dataclass(frozen=True, slots=True)
class Settings:
    """Runtime configuration loaded from environment variables."""

    app_name: str = "JARVIS"
    environment: str = "development"
    debug: bool = False

    default_provider: str = "mock"
    default_model: str = "mock-model"

    provider_timeout_seconds: float = 30.0
    provider_max_retries: int = 2

    api_key: str | None = None
    api_base_url: str | None = None

    @property
    def openai_api_key(self) -> str | None:
        return self.api_key

    @property
    def openai_base_url(self) -> str:
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
            provider_timeout_seconds=_get_float(
                "JARVIS_PROVIDER_TIMEOUT",
                30.0,
            ),
            provider_max_retries=_get_non_negative_int(
                "JARVIS_PROVIDER_MAX_RETRIES",
                2,
            ),
            api_key=os.getenv("JARVIS_API_KEY"),
            api_base_url=os.getenv(
                "JARVIS_API_BASE_URL",
                None,
            ),
        )

