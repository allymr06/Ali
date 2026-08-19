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
    assert registry.get_default().name == "mock"
