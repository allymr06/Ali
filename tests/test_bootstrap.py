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
    assert application.provider_registry.get_default().name == "mock"
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


def test_bootstrap_registers_dedicated_vision_model() -> None:
    from app.config.settings import Settings
    from app.providers.models import TaskType

    application = create_application(Settings(windows_integrations_enabled=False))
    profile = application.model_catalog.get("openai", "gpt-4o")

    assert profile.task_types == frozenset({TaskType.VISION})


def test_create_application_uses_environment_settings(monkeypatch):
    monkeypatch.setenv("JARVIS_APP_NAME", "JARVIS-Test")
    monkeypatch.setenv("JARVIS_DEFAULT_PROVIDER", "mock")

    application = create_application()

    assert application.settings.app_name == "JARVIS-Test"
    assert application.provider_registry.get_default().name == "mock"

def test_bootstrap_registers_openai_provider(monkeypatch) -> None:
    from app.bootstrap import create_application

    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    application = create_application()

    registry = application.provider_registry

    assert registry.contains("mock")
    assert registry.contains("openai")
    assert registry.contains("gemini")
    assert registry.get_default().name == "mock"

def test_bootstrap_can_select_openai_provider(monkeypatch) -> None:
    from app.bootstrap import create_application
    from app.config.settings import Settings

    monkeypatch.setenv("JARVIS_DEFAULT_PROVIDER", "openai")
    monkeypatch.setenv("JARVIS_API_KEY", "test-secret")

    application = create_application(Settings.from_environment())

    assert application.provider_registry.contains("mock")
    assert application.provider_registry.contains("openai")
    assert application.provider_registry.get_default().name == "openai"


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
