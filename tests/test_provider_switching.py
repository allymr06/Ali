from __future__ import annotations

from app.bootstrap import create_application
from app.config.settings import Settings


def mock_application():
    return create_application(
        Settings(
            default_provider="mock",
            default_model="mock-model",
            windows_integrations_enabled=False,
            memory_database_path=None,
            task_database_path=None,
            task_runtime_directory=None,
        )
    )


def test_bootstrap_keeps_gemini_as_default() -> None:
    application = create_application()

    assert application.provider_registry.get_default().name == "gemini"


def test_bootstrap_registers_gemini_without_making_it_default() -> None:
    application = mock_application()

    assert application.provider_registry.contains("gemini")
    assert application.provider_registry.get("gemini").name == "gemini"
    assert application.provider_registry.get_default().name == "mock"


def test_provider_can_be_switched_to_gemini() -> None:
    application = mock_application()

    application.provider_registry.set_default("gemini")

    assert application.provider_registry.get_default().name == "gemini"


def test_provider_can_be_switched_back_to_mock() -> None:
    application = mock_application()

    application.provider_registry.set_default("gemini")
    application.provider_registry.set_default("mock")

    assert application.provider_registry.get_default().name == "mock"


def test_custom_settings_control_default_provider() -> None:
    settings = Settings(
        default_provider="mock",
        default_model="mock-model",
        windows_integrations_enabled=False,
        memory_database_path=None,
        task_database_path=None,
        task_runtime_directory=None,
    )

    application = create_application(settings)

    assert application.provider_registry.get_default().name == "mock"

def test_bootstrap_shares_tool_executor_with_core_engine() -> None:
    application = create_application()

    assert application.engine._tool_executor is (
        application.tool_executor
    )
