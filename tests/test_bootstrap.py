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



def test_bootstrap_starts_ollama_warm_keeper_in_background(
    monkeypatch,
) -> None:
    from app.config.settings import Settings

    instances = []

    class FakeWarmKeeper:
        def __init__(
            self,
            **kwargs,
        ):
            self.kwargs = kwargs
            self.started = False
            self.closed = False
            instances.append(self)

        def start(self):
            self.started = True
            return True

        def close(self):
            self.closed = True

    monkeypatch.setattr(
        "app.bootstrap.OllamaWarmKeeper",
        FakeWarmKeeper,
    )

    application = create_application(
        Settings(
            default_provider="ollama",
            default_model="llama3.2:latest",
            ollama_model="llama3.2:latest",
            ollama_enabled=True,
            ollama_warm_enabled=True,
            ollama_keep_alive_seconds=1800,
            ollama_warm_refresh_seconds=120,
            ollama_warm_retry_seconds=15,
            ollama_warmup_timeout_seconds=30,
            windows_integrations_enabled=False,
            memory_database_path=None,
            conversation_database_path=None,
            task_database_path=None,
            task_runtime_directory=None,
        )
    )

    try:
        assert len(instances) == 1

        keeper = instances[0]

        assert keeper.started is True

        assert (
            application.ollama_warm_keeper
            is keeper
        )

        assert keeper.kwargs == {
            "base_url": (
                "http://localhost:11434/v1/"
            ),
            "model": "llama3.2:latest",
            "keep_alive_seconds": 1800,
            "refresh_seconds": 120,
            "retry_seconds": 15,
            "timeout_seconds": 30,
        }

    finally:
        application.close()

    assert instances[0].closed is True


def test_bootstrap_does_not_start_warmer_when_disabled(
) -> None:
    from app.config.settings import Settings

    application = create_application(
        Settings(
            windows_integrations_enabled=False,
            memory_database_path=None,
            conversation_database_path=None,
            task_database_path=None,
            task_runtime_directory=None,
        )
    )

    try:
        assert (
            application.ollama_warm_keeper
            is None
        )
    finally:
        application.close()
