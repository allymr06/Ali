from __future__ import annotations

from app.config.settings import Settings


def test_settings_defaults() -> None:
    settings = Settings.from_environment()

    assert settings.app_name == "JARVIS"
    assert settings.environment == "development"
    assert settings.debug is False
    assert settings.default_provider == "mock"
    assert settings.default_model == "mock-model"
    assert settings.openai_model is None
    assert settings.provider_timeout_seconds == 30.0
    assert settings.provider_max_retries == 2
    assert settings.provider_retry_backoff_seconds == 0.25
    assert settings.provider_fallback_enabled is True
    assert settings.conversation_max_messages == 50
    assert settings.conversation_max_characters == 50_000
    assert settings.conversation_summary_max_characters == 4_000
    assert settings.conversation_system_prompt is None
    assert settings.approval_ttl_seconds == 300.0
    assert settings.permission_audit_capacity == 1000


def test_settings_reads_environment(monkeypatch) -> None:
    monkeypatch.setenv("JARVIS_APP_NAME", "JARVIS-Test")
    monkeypatch.setenv("JARVIS_ENVIRONMENT", "testing")
    monkeypatch.setenv("JARVIS_DEBUG", "true")
    monkeypatch.setenv("JARVIS_DEFAULT_PROVIDER", "mock-test")
    monkeypatch.setenv("JARVIS_DEFAULT_MODEL", "test-model")
    monkeypatch.setenv("JARVIS_OPENAI_MODEL", "openai-test-model")
    monkeypatch.setenv("JARVIS_PROVIDER_TIMEOUT", "15.5")
    monkeypatch.setenv("JARVIS_PROVIDER_MAX_RETRIES", "5")
    monkeypatch.setenv("JARVIS_PROVIDER_RETRY_BACKOFF", "0.5")
    monkeypatch.setenv("JARVIS_PROVIDER_FALLBACK", "false")
    monkeypatch.setenv("JARVIS_CONVERSATION_MAX_MESSAGES", "20")
    monkeypatch.setenv("JARVIS_CONVERSATION_MAX_CHARACTERS", "2000")
    monkeypatch.setenv("JARVIS_CONVERSATION_SUMMARY_MAX_CHARACTERS", "500")
    monkeypatch.setenv("JARVIS_CONVERSATION_SYSTEM_PROMPT", "Be concise.")
    monkeypatch.setenv("JARVIS_APPROVAL_TTL_SECONDS", "120")
    monkeypatch.setenv("JARVIS_PERMISSION_AUDIT_CAPACITY", "250")

    settings = Settings.from_environment()

    assert settings.app_name == "JARVIS-Test"
    assert settings.environment == "testing"
    assert settings.debug is True
    assert settings.default_provider == "mock-test"
    assert settings.default_model == "test-model"
    assert settings.openai_model == "openai-test-model"
    assert settings.provider_timeout_seconds == 15.5
    assert settings.provider_max_retries == 5
    assert settings.provider_retry_backoff_seconds == 0.5
    assert settings.provider_fallback_enabled is False
    assert settings.conversation_max_messages == 20
    assert settings.conversation_max_characters == 2000
    assert settings.conversation_summary_max_characters == 500
    assert settings.conversation_system_prompt == "Be concise."
    assert settings.approval_ttl_seconds == 120.0
    assert settings.permission_audit_capacity == 250


def test_settings_rejects_invalid_security_limits(monkeypatch) -> None:
    import pytest

    monkeypatch.setenv("JARVIS_APPROVAL_TTL_SECONDS", "0")
    with pytest.raises(ValueError):
        Settings.from_environment()

    monkeypatch.setenv("JARVIS_APPROVAL_TTL_SECONDS", "300")
    monkeypatch.setenv("JARVIS_PERMISSION_AUDIT_CAPACITY", "0")
    with pytest.raises(ValueError):
        Settings.from_environment()

def test_settings_reads_api_configuration(monkeypatch) -> None:
    monkeypatch.setenv(
        "JARVIS_API_KEY",
        "test-api-key",
    )
    monkeypatch.setenv(
        "JARVIS_API_BASE_URL",
        "https://example.test/v1",
    )

    settings = Settings.from_environment()

    assert settings.api_key == "test-api-key"
    assert settings.api_base_url == "https://example.test/v1"


def test_settings_api_configuration_defaults_to_none() -> None:
    settings = Settings.from_environment()

    assert settings.api_key is None
    assert settings.api_base_url is None


def test_settings_rejects_negative_timeout(monkeypatch) -> None:
    monkeypatch.setenv(
        "JARVIS_PROVIDER_TIMEOUT",
        "-1",
    )

    import pytest

    with pytest.raises(ValueError):
        Settings.from_environment()


def test_settings_rejects_negative_retries(monkeypatch) -> None:
    monkeypatch.setenv(
        "JARVIS_PROVIDER_MAX_RETRIES",
        "-1",
    )

    import pytest

    with pytest.raises(ValueError):
        Settings.from_environment()


def test_settings_rejects_invalid_boolean(monkeypatch) -> None:
    import pytest

    monkeypatch.setenv("JARVIS_PROVIDER_FALLBACK", "sometimes")

    with pytest.raises(ValueError, match="must be a boolean"):
        Settings.from_environment()


def test_settings_rejects_non_finite_timeout(monkeypatch) -> None:
    import pytest

    monkeypatch.setenv("JARVIS_PROVIDER_TIMEOUT", "NaN")

    with pytest.raises(ValueError, match="finite"):
        Settings.from_environment()

def test_settings_reads_api_key(monkeypatch) -> None:
    monkeypatch.setenv("JARVIS_API_KEY", "test-secret")
    settings = Settings.from_environment()
    assert settings.api_key == "test-secret"

