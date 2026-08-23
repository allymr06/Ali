from __future__ import annotations

import os
from dataclasses import dataclass

import pytest

from app.bootstrap import (
    create_application,
)
from app.config.provider_preferences import (
    ProviderPreferences,
    ProviderPreferencesStore,
)
from app.config.settings import Settings
from app.ui.api_settings import (
    APISettingsService,
)
from app.voice.base import (
    SpeechRecognizer,
    SpeechSynthesizer,
)
from app.voice.errors import (
    VoiceConfigurationError,
)
from app.voice.registry import (
    VoiceProviderRegistry,
    create_default_voice_provider_registry,
)
from app.voice.service import (
    VoiceService,
)


class DummyRecognizer(
    SpeechRecognizer
):
    def __init__(
        self,
        settings: Settings,
    ) -> None:
        self.settings = settings

    async def transcribe(
        self,
        capture,
        *,
        language=None,
    ):
        raise NotImplementedError


class DummySynthesizer(
    SpeechSynthesizer
):
    def __init__(
        self,
        settings: Settings,
    ) -> None:
        self.settings = settings

    async def synthesize(
        self,
        text,
    ):
        raise NotImplementedError


def test_registry_supports_independent_stt_and_tts_providers(
) -> None:
    registry = VoiceProviderRegistry()

    registry.register_recognizer(
        "alpha",
        DummyRecognizer,
    )

    registry.register_synthesizer(
        "beta",
        DummySynthesizer,
    )

    settings = Settings()

    recognizer = registry.create_recognizer(
        "alpha",
        settings,
    )

    synthesizer = registry.create_synthesizer(
        "beta",
        settings,
    )

    assert isinstance(
        recognizer,
        DummyRecognizer,
    )

    assert isinstance(
        synthesizer,
        DummySynthesizer,
    )

    assert (
        registry.contains_recognizer(
            "alpha"
        )
        is True
    )

    assert (
        registry.contains_synthesizer(
            "alpha"
        )
        is False
    )


def test_registry_rejects_duplicate_role_registration(
) -> None:
    registry = VoiceProviderRegistry()

    registry.register_recognizer(
        "alpha",
        DummyRecognizer,
    )

    with pytest.raises(
        ValueError,
        match="already registered",
    ):
        registry.register_recognizer(
            "alpha",
            DummyRecognizer,
        )

    registry.register_synthesizer(
        "alpha",
        DummySynthesizer,
    )


def test_explicit_unknown_voice_provider_fails_closed(
) -> None:
    registry = VoiceProviderRegistry()

    registry.register_recognizer(
        "alpha",
        DummyRecognizer,
    )

    with pytest.raises(
        VoiceConfigurationError,
        match="not registered",
    ):
        registry.resolve_recognizer_provider(
            "missing",
            default_provider="alpha",
        )


def test_auto_voice_provider_inherits_supported_llm_provider(
) -> None:
    registry = VoiceProviderRegistry()

    registry.register_recognizer(
        "gemini",
        DummyRecognizer,
    )

    registry.register_synthesizer(
        "gemini",
        DummySynthesizer,
    )

    assert (
        registry.resolve_recognizer_provider(
            "auto",
            default_provider="gemini",
        )
        == "gemini"
    )

    assert (
        registry.resolve_synthesizer_provider(
            "auto",
            default_provider="gemini",
        )
        == "gemini"
    )


def test_default_registry_exposes_built_in_providers_for_both_roles(
) -> None:
    registry = (
        create_default_voice_provider_registry()
    )

    assert set(
        registry.list_recognizer_providers()
    ) == {"gemini"}

    assert set(
        registry.list_synthesizer_providers()
    ) == {"gemini", "elevenlabs"}


def test_settings_load_independent_voice_provider_environment(
    monkeypatch,
) -> None:
    monkeypatch.setenv(
        "JARVIS_VOICE_STT_PROVIDER",
        "gemini",
    )

    monkeypatch.setenv(
        "JARVIS_VOICE_TTS_PROVIDER",
        "gemini",
    )

    settings = (
        Settings.from_environment()
    )

    assert (
        settings.voice_stt_provider
        == "gemini"
    )

    assert (
        settings.voice_tts_provider
        == "gemini"
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


def test_runtime_loads_gemini_voice_credential(
    tmp_path,
    monkeypatch,
) -> None:
    for name in (
        "JARVIS_API_KEY",
        "JARVIS_GEMINI_API_KEY",
        "JARVIS_DEFAULT_PROVIDER",
        "JARVIS_DEFAULT_MODEL",
        "JARVIS_GEMINI_MODEL",
    ):
        monkeypatch.delenv(
            name,
            raising=False,
        )

    monkeypatch.setenv("JARVIS_VOICE_STT_PROVIDER", "gemini")

    monkeypatch.setenv(
        "JARVIS_VOICE_TTS_PROVIDER",
        "gemini",
    )

    gemini = MemoryCredentialStore(
        "gemini-secret"
    )

    preferences = (
        ProviderPreferencesStore(
            tmp_path
            / "settings.json"
        )
    )

    preferences.save(
        ProviderPreferences(
            provider="gemini",
            model="gemini-3.7-flash",
        )
    )

    service = APISettingsService(
        credential_stores={
            "gemini": gemini,
        },
        preferences=preferences,
    )

    settings = (
        service.build_runtime_settings()
    )

    assert (
        settings.default_provider
        == "gemini"
    )

    assert (
        settings.gemini_api_key
        == "gemini-secret"
    )


class BareInput:
    def list_devices(self):
        return ()


class BareOutput:
    def list_devices(self):
        return ()


def test_voice_service_exposes_resolved_provider_selection(
) -> None:
    service = VoiceService(
        session_factory=lambda: None,
        audio_input=BareInput(),
        audio_output=BareOutput(),
        stt_provider="alpha",
        tts_provider="beta",
    )

    assert (
        service.stt_provider
        == "alpha"
    )

    assert (
        service.tts_provider
        == "beta"
    )


@pytest.mark.skipif(
    os.name != "nt",
    reason=(
        "Current physical voice bootstrap "
        "uses Windows audio output."
    ),
)
def test_bootstrap_wires_split_stt_and_tts_without_api_calls(
) -> None:
    application = create_application(
        Settings(
            default_provider="gemini",
            default_model=(
                "gemini-3.7-flash"
            ),
            gemini_model=(
                "gemini-3.7-flash"
            ),
            voice_enabled=True,
            voice_stt_provider="gemini",
            voice_tts_provider="gemini",
            windows_integrations_enabled=False,
            memory_database_path=None,
            task_database_path=None,
            task_runtime_directory=None,
            conversation_database_path=None,
        )
    )

    try:
        assert (
            application.voice
            is not None
        )

        assert (
            application
            .voice
            .stt_provider
            == "gemini"
        )

        assert (
            application
            .voice
            .tts_provider
            == "gemini"
        )

        assert (
            application
            .voice_provider_registry
            is not None
        )

    finally:
        application.close()
