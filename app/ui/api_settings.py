from __future__ import annotations

import os
from dataclasses import dataclass, replace
from typing import Any, Callable

from openai import AsyncOpenAI

from app.config.provider_preferences import (
    DEFAULT_GEMINI_MODEL,
    DEFAULT_OLLAMA_MODEL,
    DEFAULT_OPENAI_MODEL,
    ProviderPreferences,
    ProviderPreferencesStore,
    validate_model,
    validate_provider,
)
from app.config.settings import Settings
from app.security.credentials import CredentialStore, WindowsCredentialStore


@dataclass(frozen=True, slots=True)
class APISettingsSnapshot:
    provider: str
    model: str
    credential_configured: bool
    credential_required: bool = True


@dataclass(frozen=True, slots=True)
class ConnectionTestResult:
    ok: bool
    message: str


class APISettingsService:
    """Coordinate non-secret preferences and OS-backed API credentials."""

    _CREDENTIAL_PROVIDERS = frozenset(
        {"gemini", "openai"}
    )

    def __init__(
        self,
        credentials: CredentialStore | None = None,
        credential_stores: dict[str, CredentialStore] | None = None,
        preferences: ProviderPreferencesStore | None = None,
        client_factory: Callable[..., Any] = AsyncOpenAI,
    ) -> None:
        if credential_stores is not None:
            self._credential_stores = dict(credential_stores)
        elif credentials is not None:
            self._credential_stores = {
                "gemini": credentials,
                "openai": credentials,
            }
        else:
            self._credential_stores = {
                "gemini": WindowsCredentialStore("JARVIS/Gemini API"),
                "openai": WindowsCredentialStore("JARVIS/OpenAI API"),
            }
        self.preferences = preferences or ProviderPreferencesStore()
        self._client_factory = client_factory

    @classmethod
    def requires_credential(
        cls,
        provider: str,
    ) -> bool:
        return (
            validate_provider(provider)
            in cls._CREDENTIAL_PROVIDERS
        )

    @property
    def credentials(self) -> CredentialStore:
        """Return the credential store for the currently selected provider."""
        provider = self.preferences.load().provider

        if not self.requires_credential(
            provider
        ):
            raise ValueError(
                f"{provider.title()} does not use an API credential."
            )

        return self._credential_store(
            provider
        )

    def _credential_store(self, provider: str) -> CredentialStore:
        selected = validate_provider(provider)
        if selected == "mock":
            selected = "gemini"

        if selected not in self._credential_stores:
            raise ValueError(
                f"{selected.title()} does not use an API credential."
            )

        return self._credential_stores[
            selected
        ]

    def snapshot(self) -> APISettingsSnapshot:
        profile = self.preferences.load()

        credential_required = (
            self.requires_credential(
                profile.provider
            )
        )

        configured = (
            bool(
                self._credential_store(
                    profile.provider
                ).read()
            )
            if credential_required
            else False
        )

        return APISettingsSnapshot(
            provider=profile.provider,
            model=profile.model,
            credential_configured=configured,
            credential_required=credential_required,
        )

    def save(self, provider: str, model: str, api_key: str | None = None) -> None:
        selected_provider = validate_provider(provider)
        selected_model = validate_model(model)
        normalized_key = api_key.strip() if api_key is not None else ""

        if self.requires_credential(
            selected_provider
        ):
            store = self._credential_store(
                selected_provider
            )

            if normalized_key:
                store.write(
                    normalized_key
                )

            if not (
                normalized_key
                or store.read()
            ):
                raise ValueError(
                    f"{selected_provider.title()} requires an API key."
                )

        elif normalized_key:
            raise ValueError(
                f"{selected_provider.title()} does not use an API key."
            )

        self.preferences.save(
            ProviderPreferences(
                provider=selected_provider,
                model=selected_model,
            )
        )

    def delete_api_key(self) -> bool:
        profile = self.preferences.load()

        if not self.requires_credential(
            profile.provider
        ):
            return False

        deleted = (
            self._credential_store(
                profile.provider
            ).delete()
        )

        self.preferences.save(
            ProviderPreferences(
                provider="mock",
                model=profile.model,
            )
        )

        return deleted

    def build_runtime_settings(self) -> Settings:
        base = Settings.from_environment()
        profile = self.preferences.load()
        provider = os.getenv("JARVIS_DEFAULT_PROVIDER") or profile.provider
        provider_model_env = {
            "gemini": "JARVIS_GEMINI_MODEL",
            "ollama": "JARVIS_OLLAMA_MODEL",
            "openai": "JARVIS_OPENAI_MODEL",
        }.get(provider)

        model = (
            os.getenv(provider_model_env)
            if provider_model_env
            else None
        ) or os.getenv(
            "JARVIS_DEFAULT_MODEL"
        ) or profile.model
        voice_provider_names = {
            base.voice_stt_provider.strip().casefold(),
            base.voice_tts_provider.strip().casefold(),
        }

        needs_openai = (
            provider == "openai"
            or "openai" in voice_provider_names
        )

        needs_gemini = (
            provider == "gemini"
            or "gemini" in voice_provider_names
        )

        stored_openai_key = (
            self._credential_store(
                "openai"
            ).read()
            if needs_openai
            else None
        )

        stored_gemini_key = (
            self._credential_store(
                "gemini"
            ).read()
            if needs_gemini
            else None
        )

        return replace(
            base,
            default_provider=provider,
            default_model=model,
            openai_model=(
                model
                if provider == "openai"
                else base.openai_model
            ),
            gemini_model=(
                model
                if provider == "gemini"
                else base.gemini_model
            ),
            ollama_model=(
                model
                if provider == "ollama"
                else base.ollama_model
            ),
            ollama_enabled=(
                base.ollama_enabled
                or provider == "ollama"
            ),
            voice_enabled=(
                base.voice_enabled
                if "JARVIS_VOICE_ENABLED" in os.environ
                else provider in {"gemini", "openai"}
            ),
            api_key=(
                base.api_key
                or stored_openai_key
                if needs_openai
                else base.api_key
            ),
            gemini_api_key=(
                base.gemini_api_key
                or stored_gemini_key
                if needs_gemini
                else base.gemini_api_key
            ),
        )

    async def test_connection(
        self,
        provider: str,
        model: str,
        api_key: str | None = None,
    ) -> ConnectionTestResult:
        selected_provider = validate_provider(provider)
        selected_model = validate_model(model)
        if selected_provider == "mock":
            return ConnectionTestResult(
                True,
                "Local mock provider is available.",
            )

        settings = (
            Settings.from_environment()
        )

        secret_candidate: str | None = None

        if selected_provider == "ollama":
            client_arguments: dict[str, Any] = {
                "api_key": "ollama",
                "base_url": (
                    settings.ollama_base_url
                ),
                "timeout": 15.0,
                "max_retries": 0,
            }

        else:
            secret_candidate = (
                (api_key or "").strip()
                or self._credential_store(
                    selected_provider
                ).read()
            )

            if not secret_candidate:
                return ConnectionTestResult(
                    False,
                    "Enter an API key first.",
                )

            client_arguments = {
                "api_key": secret_candidate,
                "timeout": 15.0,
                "max_retries": 0,
            }

            if selected_provider == "gemini":
                client_arguments["base_url"] = (
                    settings.gemini_base_url
                )
        client = self._client_factory(
            **client_arguments,
        )
        try:
            model_record = await client.models.retrieve(selected_model)
            resolved = getattr(model_record, "id", selected_model)
            return ConnectionTestResult(
                True,
                f"Connected successfully. Model: {resolved}",
            )
        except Exception as exc:
            safe_message = str(exc)

            if secret_candidate:
                safe_message = (
                    safe_message.replace(
                        secret_candidate,
                        "[REDACTED]",
                    )
                )

            return ConnectionTestResult(
                False,
                f"Connection failed: {safe_message}",
            )
        finally:
            close = getattr(client, "close", None)
            if close is not None:
                result = close()
                if hasattr(result, "__await__"):
                    await result


def create_api_settings_service() -> APISettingsService:
    return APISettingsService()


__all__ = [
    "APISettingsService",
    "APISettingsSnapshot",
    "ConnectionTestResult",
    "DEFAULT_GEMINI_MODEL",
    "DEFAULT_OLLAMA_MODEL",
    "DEFAULT_OPENAI_MODEL",
    "create_api_settings_service",
]
