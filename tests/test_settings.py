from __future__ import annotations

from app.config.settings import Settings


def test_settings_defaults() -> None:
    settings = Settings.from_environment()

    assert settings.app_name == "JARVIS"
    assert settings.environment == "development"
    assert settings.debug is False
    assert settings.default_provider == "mock"
    assert settings.default_model == "mock-model"
    assert settings.provider_timeout_seconds == 30.0
    assert settings.provider_max_retries == 2


def test_settings_reads_environment(monkeypatch) -> None:
    monkeypatch.setenv("JARVIS_APP_NAME", "JARVIS-Test")
    monkeypatch.setenv("JARVIS_ENVIRONMENT", "testing")
    monkeypatch.setenv("JARVIS_DEBUG", "true")
    monkeypatch.setenv("JARVIS_DEFAULT_PROVIDER", "mock-test")
    monkeypatch.setenv("JARVIS_DEFAULT_MODEL", "test-model")
    monkeypatch.setenv("JARVIS_PROVIDER_TIMEOUT", "15.5")
    monkeypatch.setenv("JARVIS_PROVIDER_MAX_RETRIES", "5")

    settings = Settings.from_environment()

    assert settings.app_name == "JARVIS-Test"
    assert settings.environment == "testing"
    assert settings.debug is True
    assert settings.default_provider == "mock-test"
    assert settings.default_model == "test-model"
    assert settings.provider_timeout_seconds == 15.5
    assert settings.provider_max_retries == 5