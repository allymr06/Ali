from __future__ import annotations

from app.bootstrap import create_application
from app.config.settings import Settings


def test_bootstrap_keeps_mock_as_default() -> None:
    application = create_application()

    assert application.provider_registry.get_default().name == "mock"


def test_bootstrap_registers_openai_without_making_it_default() -> None:
    application = create_application()

    assert application.provider_registry.contains("openai")
    assert application.provider_registry.get("openai").name == "openai"


def test_provider_can_be_switched_to_openai() -> None:
    application = create_application()

    application.provider_registry.set_default("openai")

    assert application.provider_registry.get_default().name == "openai"


def test_provider_can_be_switched_back_to_mock() -> None:
    application = create_application()

    application.provider_registry.set_default("openai")
    application.provider_registry.set_default("mock")

    assert application.provider_registry.get_default().name == "mock"


def test_custom_settings_control_default_provider() -> None:
    settings = Settings(
        default_provider="openai",
        default_model="test-model",
    )

    application = create_application(settings)

    assert application.provider_registry.get_default().name == "openai"
