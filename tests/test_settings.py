from __future__ import annotations

from app.config.provider_preferences import DEFAULT_GEMINI_MODEL
from app.config.settings import (
    DEFAULT_CONVERSATION_SYSTEM_PROMPT,
    Settings,
)


def test_settings_defaults(monkeypatch, tmp_path) -> None:
    monkeypatch.delenv("JARVIS_STATE_DIRECTORY", raising=False)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.delenv("JARVIS_CONVERSATION_DATABASE_PATH", raising=False)
    monkeypatch.delenv("JARVIS_MEMORY_DATABASE_PATH", raising=False)
    monkeypatch.delenv("JARVIS_TASK_DATABASE_PATH", raising=False)
    monkeypatch.delenv("JARVIS_TASK_RUNTIME_DIRECTORY", raising=False)
    monkeypatch.delenv("JARVIS_RESEARCH_CACHE_DATABASE_PATH", raising=False)
    settings = Settings.from_environment()

    assert settings.app_name == "JARVIS"
    assert settings.environment == "development"
    assert settings.debug is False
    assert settings.default_provider == "gemini"
    assert settings.default_model == DEFAULT_GEMINI_MODEL
    assert settings.openai_model is None
    assert settings.gemini_model == DEFAULT_GEMINI_MODEL
    assert settings.gemini_api_key is None
    assert settings.gemini_base_url == (
        "https://generativelanguage.googleapis.com/v1beta/openai/"
    )
    assert settings.provider_timeout_seconds == 15.0
    assert settings.provider_max_retries == 1
    assert settings.provider_retry_backoff_seconds == 0.25
    assert settings.conversation_max_messages == 50
    assert settings.conversation_max_characters == 50_000
    assert settings.conversation_summary_max_characters == 4_000
    assert (
        settings.conversation_system_prompt
        == DEFAULT_CONVERSATION_SYSTEM_PROMPT
    )
    state = tmp_path / "JARVIS"
    assert settings.conversation_database_path == str(
        state / "jarvis_conversations.sqlite3"
    )
    assert settings.memory_database_path == str(state / "jarvis_memory.sqlite3")
    assert settings.task_database_path == str(state / "jarvis_tasks.sqlite3")
    assert settings.task_runtime_directory == str(state / "tasks")
    assert settings.research_cache_database_path == str(
        state / "jarvis_research.sqlite3"
    )
    assert settings.research_cache_ttl_seconds == 86_400.0
    assert settings.approval_ttl_seconds == 300.0
    assert settings.permission_audit_capacity == 1000
    assert settings.windows_integrations_enabled is True
    assert settings.windows_launch_verification_timeout_seconds == 3.0
    assert settings.voice_enabled is False
    assert settings.voice_max_recording_seconds == 30.0
    assert settings.voice_operation_timeout_seconds == 60.0
    assert settings.voice_sample_rate == 16_000
    assert settings.voice_channels == 1
    assert settings.voice_require_wake_word is False
    assert settings.voice_wake_word == "jarvis"
    assert settings.voice_retain_last_audio is False
    assert settings.vision_enabled is False
    assert settings.vision_model is None
    assert settings.vision_detail == "high"
    assert settings.vision_redact_taskbar is True


def test_settings_reads_environment(monkeypatch) -> None:
    monkeypatch.setenv("JARVIS_APP_NAME", "JARVIS-Test")
    monkeypatch.setenv("JARVIS_ENVIRONMENT", "testing")
    monkeypatch.setenv("JARVIS_DEBUG", "true")
    monkeypatch.setenv("JARVIS_DEFAULT_PROVIDER", "mock-test")
    monkeypatch.setenv("JARVIS_DEFAULT_MODEL", "test-model")
    monkeypatch.setenv("JARVIS_GEMINI_MODEL", "gemini-test-model")
    monkeypatch.setenv("JARVIS_GEMINI_API_KEY", "gemini-test-key")
    monkeypatch.setenv(
        "JARVIS_GEMINI_BASE_URL", "https://gemini.example/openai/"
    )
    monkeypatch.setenv("JARVIS_PROVIDER_TIMEOUT", "15.5")
    monkeypatch.setenv("JARVIS_PROVIDER_MAX_RETRIES", "5")
    monkeypatch.setenv("JARVIS_PROVIDER_RETRY_BACKOFF", "0.5")
    monkeypatch.setenv("JARVIS_CONVERSATION_MAX_MESSAGES", "20")
    monkeypatch.setenv("JARVIS_CONVERSATION_MAX_CHARACTERS", "2000")
    monkeypatch.setenv("JARVIS_CONVERSATION_SUMMARY_MAX_CHARACTERS", "500")
    monkeypatch.setenv("JARVIS_CONVERSATION_SYSTEM_PROMPT", "Be concise.")
    monkeypatch.setenv("JARVIS_MEMORY_DATABASE_PATH", "state/test-memory.sqlite3")
    monkeypatch.setenv("JARVIS_TASK_DATABASE_PATH", "state/test-tasks.sqlite3")
    monkeypatch.setenv("JARVIS_TASK_RUNTIME_DIRECTORY", "state/tasks")
    monkeypatch.setenv("JARVIS_APPROVAL_TTL_SECONDS", "120")
    monkeypatch.setenv("JARVIS_PERMISSION_AUDIT_CAPACITY", "250")
    monkeypatch.setenv("JARVIS_WINDOWS_INTEGRATIONS", "false")
    monkeypatch.setenv("JARVIS_WINDOWS_LAUNCH_VERIFICATION_TIMEOUT", "1.5")
    monkeypatch.setenv("JARVIS_VOICE_ENABLED", "true")
    monkeypatch.setenv("JARVIS_VOICE_MAX_RECORDING_SECONDS", "12")
    monkeypatch.setenv("JARVIS_VOICE_OPERATION_TIMEOUT_SECONDS", "20")
    monkeypatch.setenv("JARVIS_VOICE_SAMPLE_RATE", "48000")
    monkeypatch.setenv("JARVIS_VOICE_CHANNELS", "2")
    monkeypatch.setenv("JARVIS_VOICE_INPUT_DEVICE_ID", "3")
    monkeypatch.setenv("JARVIS_VOICE_REQUIRE_WAKE_WORD", "true")
    monkeypatch.setenv("JARVIS_VOICE_WAKE_WORD", "computer")
    monkeypatch.setenv("JARVIS_VOICE_LANGUAGE", "en")
    monkeypatch.setenv("JARVIS_VOICE_RETAIN_LAST_AUDIO", "true")
    monkeypatch.setenv("JARVIS_VISION_ENABLED", "true")
    monkeypatch.setenv("JARVIS_VISION_MODEL", "vision-test")
    monkeypatch.setenv("JARVIS_VISION_DETAIL", "low")
    monkeypatch.setenv("JARVIS_VISION_MAX_FRAME_AGE_SECONDS", "2")
    monkeypatch.setenv("JARVIS_VISION_REDACT_TASKBAR", "false")
    monkeypatch.setenv("JARVIS_VISION_TASKBAR_HEIGHT", "32")
    monkeypatch.setenv(
        "JARVIS_RESEARCH_CACHE_DATABASE_PATH",
        "state/test-research.sqlite3",
    )
    monkeypatch.setenv("JARVIS_RESEARCH_CACHE_TTL_SECONDS", "3600")

    settings = Settings.from_environment()

    assert settings.app_name == "JARVIS-Test"
    assert settings.environment == "testing"
    assert settings.debug is True
    assert settings.default_provider == "mock-test"
    assert settings.default_model == "test-model"
    assert settings.gemini_model == "gemini-test-model"
    assert settings.gemini_api_key == "gemini-test-key"
    assert settings.gemini_base_url == "https://gemini.example/openai/"
    assert settings.provider_timeout_seconds == 15.5
    assert settings.provider_max_retries == 5
    assert settings.provider_retry_backoff_seconds == 0.5
    assert settings.conversation_max_messages == 20
    assert settings.conversation_max_characters == 2000
    assert settings.conversation_summary_max_characters == 500
    assert settings.conversation_system_prompt == "Be concise."
    assert settings.memory_database_path == "state/test-memory.sqlite3"
    assert settings.task_database_path == "state/test-tasks.sqlite3"
    assert settings.task_runtime_directory == "state/tasks"
    assert settings.approval_ttl_seconds == 120.0
    assert settings.permission_audit_capacity == 250
    assert settings.windows_integrations_enabled is False
    assert settings.windows_launch_verification_timeout_seconds == 1.5
    assert settings.voice_enabled is True
    assert settings.voice_max_recording_seconds == 12.0
    assert settings.voice_operation_timeout_seconds == 20.0
    assert settings.voice_sample_rate == 48_000
    assert settings.voice_channels == 2
    assert settings.voice_input_device_id == "3"
    assert settings.voice_require_wake_word is True
    assert settings.voice_wake_word == "computer"
    assert settings.voice_language == "en"
    assert settings.voice_retain_last_audio is True
    assert settings.vision_enabled is True
    assert settings.vision_model == "vision-test"
    assert settings.vision_detail == "low"
    assert settings.vision_max_frame_age_seconds == 2.0
    assert settings.vision_redact_taskbar is False
    assert settings.vision_taskbar_height == 32
    assert settings.research_cache_database_path == "state/test-research.sqlite3"
    assert settings.research_cache_ttl_seconds == 3600.0


def test_settings_rejects_invalid_voice_configuration() -> None:
    import pytest

    with pytest.raises(ValueError, match="recording"):
        Settings(voice_max_recording_seconds=0)
    with pytest.raises(ValueError, match="sample_rate"):
        Settings(voice_sample_rate=4_000)
    with pytest.raises(ValueError, match="channels"):
        Settings(voice_channels=3)
    with pytest.raises(ValueError, match="wake_word"):
        Settings(voice_wake_word=" ")


def test_settings_rejects_invalid_vision_configuration() -> None:
    import pytest

    with pytest.raises(ValueError, match="vision_detail"):
        Settings(vision_detail="ultra")
    with pytest.raises(ValueError, match="time limits"):
        Settings(vision_max_frame_age_seconds=0)
    with pytest.raises(ValueError, match="image limits"):
        Settings(vision_max_images=0)
    with pytest.raises(ValueError, match="taskbar_height"):
        Settings(vision_taskbar_height=-1)


def test_settings_rejects_invalid_security_limits(monkeypatch) -> None:
    import pytest

    monkeypatch.setenv("JARVIS_APPROVAL_TTL_SECONDS", "0")
    with pytest.raises(ValueError):
        Settings.from_environment()

    monkeypatch.setenv("JARVIS_APPROVAL_TTL_SECONDS", "300")
    monkeypatch.setenv("JARVIS_WINDOWS_LAUNCH_VERIFICATION_TIMEOUT", "0")
    with pytest.raises(ValueError):
        Settings.from_environment()

    monkeypatch.setenv("JARVIS_WINDOWS_LAUNCH_VERIFICATION_TIMEOUT", "3")
    monkeypatch.setenv("JARVIS_PERMISSION_AUDIT_CAPACITY", "0")
    with pytest.raises(ValueError):
        Settings.from_environment()

def test_settings_ignores_retired_openai_environment(monkeypatch) -> None:
    monkeypatch.setenv("JARVIS_API_KEY", "stale-openai-key")
    monkeypatch.setenv("JARVIS_API_BASE_URL", "https://example.test/v1")
    monkeypatch.setenv("JARVIS_OPENAI_MODEL", "gpt-4o-mini")

    settings = Settings.from_environment()

    assert settings.api_key is None
    assert settings.api_base_url is None
    assert settings.openai_model is None


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

    monkeypatch.setenv("JARVIS_DEBUG", "sometimes")

    with pytest.raises(ValueError, match="must be a boolean"):
        Settings.from_environment()


def test_settings_rejects_non_finite_timeout(monkeypatch) -> None:
    import pytest

    monkeypatch.setenv("JARVIS_PROVIDER_TIMEOUT", "NaN")

    with pytest.raises(ValueError, match="finite"):
        Settings.from_environment()

def test_settings_reads_gemini_api_key(monkeypatch) -> None:
    monkeypatch.setenv("JARVIS_GEMINI_API_KEY", "test-secret")
    settings = Settings.from_environment()
    assert settings.gemini_api_key == "test-secret"


def test_settings_reads_research_configuration(monkeypatch) -> None:
    monkeypatch.setenv("JARVIS_RESEARCH_ENABLED", "true")
    monkeypatch.setenv("JARVIS_RESEARCH_SEARXNG_URL", "https://search.example")
    monkeypatch.setenv("JARVIS_RESEARCH_MAX_SOURCES", "7")
    monkeypatch.setenv("JARVIS_RESEARCH_ALLOW_HTTP", "true")

    settings = Settings.from_environment()

    assert settings.research_enabled is True
    assert settings.research_searxng_url == "https://search.example"
    assert settings.research_max_sources == 7
    assert settings.research_allow_http is True


def test_settings_requires_search_endpoint_when_research_enabled() -> None:
    import pytest

    with pytest.raises(ValueError, match="searxng"):
        Settings(research_enabled=True)
    with pytest.raises(ValueError, match="between 1 and 10"):
        Settings(research_max_sources=11)
    with pytest.raises(ValueError, match="redirects"):
        Settings(research_max_redirects=11)


def test_settings_reads_and_validates_diagnostics_configuration(monkeypatch) -> None:
    import pytest

    monkeypatch.setenv("JARVIS_DIAGNOSTICS_EVENT_CAPACITY", "300")
    monkeypatch.setenv("JARVIS_DIAGNOSTICS_METRIC_CAPACITY", "40")
    monkeypatch.setenv("JARVIS_DIAGNOSTICS_HEALTH_TIMEOUT_SECONDS", "1.5")
    settings = Settings.from_environment()
    assert settings.diagnostics_event_capacity == 300
    assert settings.diagnostics_metric_capacity == 40
    assert settings.diagnostics_health_timeout_seconds == 1.5

    with pytest.raises(ValueError, match="capacities"):
        Settings(diagnostics_event_capacity=0)
    with pytest.raises(ValueError, match="health_timeout"):
        Settings(diagnostics_health_timeout_seconds=0)


def test_settings_reads_and_validates_reliability_configuration(monkeypatch) -> None:
    import pytest

    monkeypatch.setenv("JARVIS_CORE_MAX_CONCURRENT_REQUESTS", "4")
    monkeypatch.setenv("JARVIS_CORE_MAX_QUEUED_REQUESTS", "12")
    monkeypatch.setenv("JARVIS_CORE_ADMISSION_TIMEOUT_SECONDS", "0.5")
    monkeypatch.setenv("JARVIS_PROVIDER_CIRCUIT_FAILURE_THRESHOLD", "3")
    monkeypatch.setenv("JARVIS_PROVIDER_CIRCUIT_RECOVERY_SECONDS", "15")
    settings = Settings.from_environment()
    assert settings.core_max_concurrent_requests == 4
    assert settings.core_max_queued_requests == 12
    assert settings.core_admission_timeout_seconds == 0.5
    assert settings.provider_circuit_failure_threshold == 3
    assert settings.provider_circuit_recovery_seconds == 15

    with pytest.raises(ValueError, match="concurrent"):
        Settings(core_max_concurrent_requests=0)
    with pytest.raises(ValueError, match="queued"):
        Settings(core_max_queued_requests=-1)
    with pytest.raises(ValueError, match="circuit"):
        Settings(provider_circuit_failure_threshold=0)


def test_settings_ignores_retired_vision_model_default(monkeypatch) -> None:
    monkeypatch.setenv("JARVIS_VISION_MODEL", "gpt-4o")

    settings = Settings.from_environment()

    assert settings.vision_model is None


def test_settings_strips_whitespace_from_model_overrides(monkeypatch) -> None:
    monkeypatch.setenv("JARVIS_GEMINI_MODEL", "  gemini-3.7-flash  ")
    monkeypatch.setenv("JARVIS_DEFAULT_MODEL", "   ")
    monkeypatch.setenv("JARVIS_VISION_MODEL", "  ")

    settings = Settings.from_environment()

    assert settings.gemini_model == "gemini-3.7-flash"
    assert settings.default_model == DEFAULT_GEMINI_MODEL
    assert settings.vision_model is None


def test_os_notifications_setting_reads_environment(monkeypatch) -> None:
    assert Settings().notifications_os_enabled is True
    monkeypatch.setenv("JARVIS_NOTIFICATIONS_OS_ENABLED", "false")
    assert Settings.from_environment().notifications_os_enabled is False


def test_action_model_escalation_is_opt_in(monkeypatch) -> None:
    monkeypatch.delenv("JARVIS_GEMINI_ACTION_MODEL", raising=False)
    assert Settings().gemini_action_model == ""
    assert Settings.from_environment().gemini_action_model == ""
    monkeypatch.setenv("JARVIS_GEMINI_ACTION_MODEL", "  gemini-3.5-flash ")
    assert Settings.from_environment().gemini_action_model == "gemini-3.5-flash"


def test_routines_database_path_reads_environment(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("JARVIS_ROUTINES_DATABASE_PATH", str(tmp_path / "r.sqlite3"))
    assert Settings.from_environment().routines_database_path == str(tmp_path / "r.sqlite3")


def test_notifications_database_path_reads_environment(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("JARVIS_NOTIFICATIONS_DATABASE_PATH", str(tmp_path / "n.sqlite3"))
    assert Settings.from_environment().notifications_database_path == str(tmp_path / "n.sqlite3")
