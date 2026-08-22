from __future__ import annotations

from app.bootstrap import create_application


def test_project_imports():
    import app


def test_python_version():
    import sys

    assert sys.version_info >= (3, 12)
    assert sys.version_info < (3, 13)


def test_create_application_wires_core_components():
    application = create_application()

    assert application.settings.app_name == "JARVIS"
    assert application.provider_registry.get_default().name == "gemini"
    assert application.memory_manager is not None
    assert application.tool_executor is not None
    assert application.engine is not None
    assert application.voice is None
    assert application.vision is None
    assert application.research is None
    assert application.diagnostics is not None
    assert application.tool_executor.contains("diagnostics_health")


def test_create_application_can_wire_optional_voice_pipeline(monkeypatch):
    from app.config.settings import Settings
    from app.voice.service import VoiceService

    monkeypatch.setenv("JARVIS_VOICE_ENABLED", "true")
    application = create_application(Settings.from_environment())

    assert isinstance(application.voice, VoiceService)


def test_create_application_can_wire_optional_vision_pipeline(monkeypatch):
    from app.config.settings import Settings
    from app.vision.service import VisionService

    monkeypatch.setenv("JARVIS_VISION_ENABLED", "true")
    application = create_application(Settings.from_environment())

    assert isinstance(application.vision, VisionService)


def test_create_application_can_wire_optional_research_pipeline() -> None:
    from app.config.settings import Settings
    from app.research.service import ResearchService

    application = create_application(
        Settings(
            windows_integrations_enabled=False,
            research_enabled=True,
            research_searxng_url="https://search.example",
        )
    )

    assert isinstance(application.research, ResearchService)
    assert application.tool_executor.contains("research_web")


def test_bootstrap_registers_only_gemini_in_production() -> None:
    from app.config.settings import Settings

    application = create_application(
        Settings(windows_integrations_enabled=False)
    )

    registry = application.provider_registry

    assert registry.contains("gemini")
    assert not registry.contains("mock")
    assert not registry.contains("openai")
    assert not registry.contains("ollama")
    assert registry.get_default().name == "gemini"


def test_create_application_uses_environment_settings(monkeypatch):
    monkeypatch.setenv("JARVIS_APP_NAME", "JARVIS-Test")
    monkeypatch.setenv("JARVIS_DEFAULT_PROVIDER", "mock")

    application = create_application()

    assert application.settings.app_name == "JARVIS-Test"
    assert application.provider_registry.get_default().name == "mock"


def test_bootstrap_registers_mock_only_when_explicitly_requested() -> None:
    from app.config.settings import Settings

    application = create_application(
        Settings(
            default_provider="mock",
            default_model="mock-model",
            windows_integrations_enabled=False,
        )
    )

    registry = application.provider_registry

    assert registry.contains("mock")
    assert registry.contains("gemini")
    assert registry.get_default().name == "mock"
    assert application.model_catalog.contains("mock", "mock-model")


def test_bootstrap_can_select_gemini_provider() -> None:
    from app.config.settings import Settings

    application = create_application(
        Settings(
            default_provider="gemini",
            default_model="gemini-3.7-flash",
            gemini_model="gemini-3.7-flash",
            gemini_api_key="test-secret",
            windows_integrations_enabled=False,
        )
    )

    assert application.provider_registry.get_default().name == "gemini"
    assert application.model_catalog.contains("gemini", "gemini-3.7-flash")


def test_bootstrap_routes_vision_to_a_dedicated_model_when_configured() -> None:
    from app.config.settings import Settings
    from app.providers.models import TaskType

    application = create_application(
        Settings(
            gemini_model="gemini-3.5-flash-lite",
            vision_model="gemini-3.7-flash",
            windows_integrations_enabled=False,
        )
    )

    catalog = application.model_catalog
    general = catalog.get("gemini", "gemini-3.5-flash-lite")
    vision = catalog.get("gemini", "gemini-3.7-flash")

    assert TaskType.VISION not in general.task_types
    assert vision.task_types == frozenset({TaskType.VISION})


def test_bootstrap_keeps_one_model_when_no_vision_override() -> None:
    from app.config.settings import Settings
    from app.providers.models import TaskType

    application = create_application(
        Settings(
            gemini_model="gemini-3.5-flash-lite",
            windows_integrations_enabled=False,
        )
    )

    profile = application.model_catalog.get("gemini", "gemini-3.5-flash-lite")

    assert TaskType.VISION in profile.task_types


def test_bootstrap_voice_falls_back_to_gemini_for_stale_text_provider(
    monkeypatch,
) -> None:
    from app.config.settings import Settings

    monkeypatch.setenv("JARVIS_VOICE_ENABLED", "true")
    monkeypatch.setenv("JARVIS_DEFAULT_PROVIDER", "ollama")

    application = create_application(Settings.from_environment())

    assert application.provider_registry.get_default().name == "gemini"
    assert application.voice is not None
    assert application.voice.stt_provider == "gemini"
    assert application.voice.tts_provider == "gemini"
