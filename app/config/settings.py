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


def _get_float(name: str, default: float) -> float:
    value = os.getenv(name)

    if value is None:
        return default

    try:
        result = float(value)
    except ValueError as exc:
        raise ValueError(
            f"Environment variable '{name}' must be a number."
        ) from exc

    if result <= 0:
        raise ValueError(
            f"Environment variable '{name}' must be greater than 0."
        )

    return result


def _get_int(name: str, default: int = 0) -> int:
    value = os.getenv(name)

    if value is None:
        return default

    try:
        result = int(value)
    except ValueError as exc:
        raise ValueError(
            f"Environment variable '{name}' must be an integer."
        ) from exc

    if result < 0:
        raise ValueError(
            f"Environment variable '{name}' must be greater than or equal to 0."
        )

    return result


@dataclass(frozen=True, slots=True)
class Settings:
    """Runtime configuration loaded from environment variables."""

    app_name: str = "JARVIS"
    environment: str = "development"
    debug: bool = False

    default_provider: str = "mock"
    default_model: str = "mock-model"

    api_key: str | None = None
    api_base_url: str | None = None

    provider_timeout_seconds: float = 30.0
    provider_max_retries: int = 2

    @classmethod
    def from_environment(cls) -> Settings:
        api_key = os.getenv("JARVIS_API_KEY")
        api_base_url = os.getenv("JARVIS_API_BASE_URL")

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
            api_key=api_key,
            api_base_url=api_base_url,
            provider_timeout_seconds=_get_float(
                "JARVIS_PROVIDER_TIMEOUT",
                30.0,
            ),
            provider_max_retries=_get_int(
                "JARVIS_PROVIDER_MAX_RETRIES",
                2,
            ),
        )