from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.bootstrap import (
    create_application,
)
from app.config.provider_preferences import (
    DEFAULT_OLLAMA_MODEL,
    ProviderPreferences,
    ProviderPreferencesStore,
    validate_provider,
)
from app.config.settings import Settings
from app.core.models import (
    Context,
    Request,
)
from app.providers.ollama import (
    OllamaProvider,
)


class RecordingCompletions:
    def __init__(self) -> None:
        self.calls = []

    async def create(
        self,
        **kwargs,
    ):
        self.calls.append(kwargs)

        message = SimpleNamespace(
            content="local response",
            tool_calls=[],
        )

        choice = SimpleNamespace(
            message=message,
            finish_reason="stop",
        )

        return SimpleNamespace(
            choices=[choice],
            usage=None,
        )


class FakeClient:
    def __init__(self) -> None:
        self.completions = (
            RecordingCompletions()
        )

        self.chat = SimpleNamespace(
            completions=self.completions
        )


def test_ollama_provider_identity_and_capabilities(
) -> None:
    provider = OllamaProvider(
        Settings(
            default_provider="ollama",
            default_model=DEFAULT_OLLAMA_MODEL,
            ollama_model=DEFAULT_OLLAMA_MODEL,
        ),
        client=FakeClient(),
    )

    assert provider.name == "ollama"
    assert provider.provider_label == "Ollama"
    assert provider.is_configured is True

    capabilities = provider.capabilities

    assert capabilities.text is True
    assert capabilities.streaming is True
    assert capabilities.structured_output is True
    assert capabilities.tool_calling is True
    assert capabilities.vision is False


def test_ollama_is_not_automatic_fallback_unless_enabled(
) -> None:
    disabled = OllamaProvider(
        Settings(),
        client=FakeClient(),
    )

    enabled = OllamaProvider(
        Settings(
            ollama_enabled=True,
        ),
        client=FakeClient(),
    )

    assert disabled.is_configured is False
    assert enabled.is_configured is True


@pytest.mark.asyncio
async def test_ollama_generate_uses_provider_neutral_contract(
) -> None:
    client = FakeClient()

    provider = OllamaProvider(
        Settings(
            default_provider="ollama",
            default_model=DEFAULT_OLLAMA_MODEL,
            ollama_model=DEFAULT_OLLAMA_MODEL,
        ),
        client=client,
    )

    tools = [
        {
            "type": "function",
            "function": {
                "name": "status",
                "description": "status",
                "parameters": {
                    "type": "object",
                    "properties": {},
                },
            },
        }
    ]

    result = await provider.generate(
        Request("hello"),
        Context(),
        tools=tools,
    )

    assert result.provider == "ollama"
    assert result.model == DEFAULT_OLLAMA_MODEL
    assert result.text == "local response"

    call = client.completions.calls[0]

    assert call["model"] == DEFAULT_OLLAMA_MODEL
    assert call["tools"] == tools


def test_provider_preferences_accept_ollama(
    tmp_path,
) -> None:
    assert validate_provider("ollama") == "ollama"

    store = ProviderPreferencesStore(
        tmp_path / "settings.json"
    )

    store.save(
        ProviderPreferences(
            provider="ollama",
            model=DEFAULT_OLLAMA_MODEL,
        )
    )

    loaded = store.load()

    assert loaded.provider == "ollama"
    assert loaded.model == DEFAULT_OLLAMA_MODEL


def test_ollama_environment_settings(
    monkeypatch,
) -> None:
    monkeypatch.setenv(
        "JARVIS_OLLAMA_MODEL",
        "llama3.2:latest",
    )

    monkeypatch.setenv(
        "JARVIS_OLLAMA_BASE_URL",
        "http://localhost:11434/v1/",
    )

    monkeypatch.setenv(
        "JARVIS_OLLAMA_ENABLED",
        "true",
    )

    settings = Settings.from_environment()

    assert settings.ollama_model == "llama3.2:latest"

    assert (
        settings.ollama_base_url
        == "http://localhost:11434/v1/"
    )

    assert settings.ollama_enabled is True


def test_bootstrap_registers_ollama_without_network_call(
) -> None:
    application = create_application(
        Settings(
            default_provider="ollama",
            default_model=DEFAULT_OLLAMA_MODEL,
            ollama_model=DEFAULT_OLLAMA_MODEL,
            ollama_enabled=True,
            windows_integrations_enabled=False,
            memory_database_path=None,
            task_database_path=None,
            task_runtime_directory=None,
            conversation_database_path=None,
        )
    )

    try:
        assert application.provider_registry.contains(
            "ollama"
        )

        assert (
            application.provider_registry
            .get_default()
            .name
            == "ollama"
        )

        assert application.model_catalog.contains(
            "ollama",
            DEFAULT_OLLAMA_MODEL,
        )

        provider = (
            application.provider_registry
            .get("ollama")
        )

        assert provider.is_configured is True

    finally:
        application.close()
