from __future__ import annotations

from dataclasses import dataclass

import pytest

from app.config.provider_preferences import (
    DEFAULT_OLLAMA_MODEL,
    ProviderPreferencesStore,
)
from app.ui.api_settings import (
    APISettingsService,
)


@dataclass
class MemoryCredentialStore:
    value: str | None = None

    def read(self):
        return self.value

    def write(
        self,
        value,
    ):
        self.value = value

    def delete(self):
        existed = (
            self.value is not None
        )

        self.value = None
        return existed


class FakeModels:
    async def retrieve(
        self,
        model,
    ):
        return type(
            "Model",
            (),
            {"id": model},
        )()


class FakeClient:
    def __init__(self) -> None:
        self.models = FakeModels()
        self.closed = False

    async def close(self):
        self.closed = True


def make_service(
    tmp_path,
):
    openai = MemoryCredentialStore()
    gemini = MemoryCredentialStore()
    captured = {}

    def factory(
        **kwargs,
    ):
        captured.update(kwargs)
        return FakeClient()

    service = APISettingsService(
        credential_stores={
            "openai": openai,
            "gemini": gemini,
        },
        preferences=(
            ProviderPreferencesStore(
                tmp_path
                / "settings.json"
            )
        ),
        client_factory=factory,
    )

    return (
        service,
        captured,
        openai,
        gemini,
    )


def test_ollama_save_requires_no_api_key(
    tmp_path,
) -> None:
    service, _, _, _ = make_service(
        tmp_path
    )

    service.save(
        "ollama",
        DEFAULT_OLLAMA_MODEL,
    )

    snapshot = service.snapshot()

    assert snapshot.provider == "ollama"
    assert snapshot.model == DEFAULT_OLLAMA_MODEL
    assert snapshot.credential_required is False
    assert snapshot.credential_configured is False


def test_ollama_rejects_accidental_api_key_storage(
    tmp_path,
) -> None:
    (
        service,
        _,
        openai,
        gemini,
    ) = make_service(
        tmp_path
    )

    with pytest.raises(
        ValueError,
        match="does not use an API key",
    ):
        service.save(
            "ollama",
            DEFAULT_OLLAMA_MODEL,
            "must-not-be-stored",
        )

    assert openai.read() is None
    assert gemini.read() is None


def test_ollama_runtime_settings_activate_local_provider(
    tmp_path,
    monkeypatch,
) -> None:
    for name in (
        "JARVIS_DEFAULT_PROVIDER",
        "JARVIS_DEFAULT_MODEL",
        "JARVIS_OLLAMA_MODEL",
        "JARVIS_OLLAMA_ENABLED",
        "JARVIS_API_KEY",
        "JARVIS_GEMINI_API_KEY",
    ):
        monkeypatch.delenv(
            name,
            raising=False,
        )

    service, _, _, _ = make_service(
        tmp_path
    )

    service.save(
        "ollama",
        DEFAULT_OLLAMA_MODEL,
    )

    settings = (
        service
        .build_runtime_settings()
    )

    assert settings.default_provider == "ollama"
    assert settings.default_model == DEFAULT_OLLAMA_MODEL
    assert settings.ollama_model == DEFAULT_OLLAMA_MODEL
    assert settings.ollama_enabled is True
    assert settings.ollama_hybrid_enabled is True
    assert settings.ollama_chat_model == DEFAULT_OLLAMA_MODEL
    assert settings.ollama_warm_enabled is True


@pytest.mark.asyncio
async def test_ollama_connection_test_uses_local_endpoint(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.delenv(
        "JARVIS_OLLAMA_BASE_URL",
        raising=False,
    )

    service, captured, _, _ = make_service(
        tmp_path
    )

    result = (
        await service.test_connection(
            "ollama",
            DEFAULT_OLLAMA_MODEL,
        )
    )

    assert result.ok is True

    assert captured["api_key"] == "ollama"

    assert (
        captured["base_url"]
        == "http://localhost:11434/v1/"
    )
