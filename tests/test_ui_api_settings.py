from __future__ import annotations

import json
from dataclasses import dataclass

import pytest

from app.config.provider_preferences import (
    ProviderPreferences,
    ProviderPreferencesStore,
    validate_model,
    validate_provider,
)
from app.ui.api_settings import APISettingsService


@dataclass
class MemoryCredentialStore:
    value: str | None = None

    def read(self) -> str | None:
        return self.value

    def write(self, secret: str) -> None:
        self.value = secret

    def delete(self) -> bool:
        existed = self.value is not None
        self.value = None
        return existed


class FakeModels:
    def __init__(self, *, error: Exception | None = None) -> None:
        self.error = error

    async def retrieve(self, model: str):
        if self.error is not None:
            raise self.error
        return type("Model", (), {"id": model})()


class FakeClient:
    def __init__(self, *, error: Exception | None = None) -> None:
        self.models = FakeModels(error=error)
        self.closed = False

    async def close(self) -> None:
        self.closed = True


def service(tmp_path, *, credential: str | None = None, factory=None):
    credentials = MemoryCredentialStore(credential)
    preferences = ProviderPreferencesStore(tmp_path / "settings.json")
    clients = []

    def default_factory(**_kwargs):
        client = FakeClient()
        clients.append(client)
        return client

    instance = APISettingsService(
        credentials=credentials,
        preferences=preferences,
        client_factory=factory or default_factory,
    )
    return instance, credentials, preferences, clients


def test_provider_preferences_are_atomic_and_never_contain_secret(tmp_path) -> None:
    instance, credentials, preferences, _clients = service(tmp_path)

    instance.save("openai", "gpt-4o-mini", "sk-test-secret")

    assert credentials.read() == "sk-test-secret"
    assert instance.snapshot().credential_configured is True
    payload = preferences.path.read_text(encoding="utf-8")
    assert "sk-test-secret" not in payload
    assert json.loads(payload)["provider"] == "openai"
    assert not preferences.path.with_suffix(".json.tmp").exists()


def test_corrupt_preferences_fail_closed_to_mock(tmp_path) -> None:
    path = tmp_path / "settings.json"
    path.write_text("not-json", encoding="utf-8")

    assert ProviderPreferencesStore(path).load() == ProviderPreferences()


def test_runtime_settings_use_vault_secret_without_environment_leak(
    tmp_path, monkeypatch
) -> None:
    for name in (
        "JARVIS_API_KEY",
        "JARVIS_DEFAULT_PROVIDER",
        "JARVIS_DEFAULT_MODEL",
        "JARVIS_OPENAI_MODEL",
    ):
        monkeypatch.delenv(name, raising=False)
    instance, _credentials, _preferences, _clients = service(
        tmp_path, credential="vault-secret"
    )
    instance.save("openai", "gpt-4o-mini")

    settings = instance.build_runtime_settings()

    assert settings.default_provider == "openai"
    assert settings.default_model == "gpt-4o-mini"
    assert settings.openai_model == "gpt-4o-mini"
    assert settings.api_key == "vault-secret"


def test_gemini_runtime_uses_separate_vault_and_compatible_endpoint(
    tmp_path, monkeypatch
) -> None:
    for name in (
        "JARVIS_API_KEY",
        "JARVIS_DEFAULT_PROVIDER",
        "JARVIS_DEFAULT_MODEL",
        "JARVIS_GEMINI_API_KEY",
        "JARVIS_GEMINI_MODEL",
    ):
        monkeypatch.delenv(name, raising=False)
    gemini = MemoryCredentialStore("gemini-vault-secret")
    openai = MemoryCredentialStore("openai-vault-secret")
    instance = APISettingsService(
        credential_stores={"gemini": gemini, "openai": openai},
        preferences=ProviderPreferencesStore(tmp_path / "settings.json"),
        client_factory=lambda **_kwargs: FakeClient(),
    )
    instance.save("gemini", "gemini-3.7-flash")

    settings = instance.build_runtime_settings()

    assert settings.default_provider == "gemini"
    assert settings.gemini_model == "gemini-3.7-flash"
    assert settings.gemini_api_key == "gemini-vault-secret"
    assert settings.api_key is None


@pytest.mark.asyncio
async def test_connection_test_uses_candidate_key_and_closes_client(tmp_path) -> None:
    instance, _credentials, _preferences, clients = service(tmp_path)

    result = await instance.test_connection(
        "openai", "gpt-4o-mini", "candidate-secret"
    )

    assert result.ok is True
    assert "gpt-4o-mini" in result.message
    assert clients[0].closed is True


@pytest.mark.asyncio
async def test_gemini_connection_test_uses_google_compatible_base_url(tmp_path) -> None:
    captured: dict[str, object] = {}
    client = FakeClient()

    def factory(**kwargs):
        captured.update(kwargs)
        return client

    instance, _credentials, _preferences, _clients = service(
        tmp_path, factory=factory
    )

    result = await instance.test_connection(
        "gemini", "gemini-3.7-flash", "candidate-secret"
    )

    assert result.ok is True
    assert captured["base_url"] == (
        "https://generativelanguage.googleapis.com/v1beta/openai/"
    )
    assert client.closed is True


@pytest.mark.asyncio
async def test_connection_errors_redact_api_key(tmp_path) -> None:
    candidate = "secret-that-must-not-leak"
    client = FakeClient(error=RuntimeError(f"bad credential {candidate}"))
    instance, _credentials, _preferences, _clients = service(
        tmp_path,
        factory=lambda **_kwargs: client,
    )

    result = await instance.test_connection(
        "openai", "gpt-4o-mini", candidate
    )

    assert result.ok is False
    assert candidate not in result.message
    assert "[REDACTED]" in result.message
    assert client.closed is True


def test_removing_key_returns_profile_to_mock(tmp_path) -> None:
    instance, credentials, preferences, _clients = service(
        tmp_path, credential="stored"
    )
    instance.save("openai", "gpt-4o-mini")

    assert instance.delete_api_key() is True

    assert credentials.read() is None
    assert preferences.load().provider == "mock"


@pytest.mark.parametrize("provider", ["", "unknown", " open-ai "])
def test_provider_validation_rejects_unknown_values(provider: str) -> None:
    with pytest.raises(ValueError):
        validate_provider(provider)


@pytest.mark.parametrize("model", ["", "bad model", "../model", "x" * 129])
def test_model_validation_rejects_unsafe_values(model: str) -> None:
    with pytest.raises(ValueError):
        validate_model(model)
