"""Plugin runtime wiring: settings, bootstrap, and the acceptance invariants."""

from __future__ import annotations

import json

import pytest

from app.bootstrap import create_application
from app.config.settings import Settings
from app.core.models import RiskLevel
from app.plugins import PluginState
from app.plugins.runtime import STATE_FILENAME
from tests.plugin_helpers import install_plugin


def settings_for(tmp_path, **overrides) -> Settings:
    options = dict(
        default_provider="mock",
        default_model="mock-model",
        windows_integrations_enabled=False,
        memory_database_path=None,
        task_database_path=None,
        task_runtime_directory=None,
    )
    options.update(overrides)
    return Settings(**options)


def test_plugins_are_off_by_default(tmp_path) -> None:
    application = create_application(settings_for(tmp_path))
    try:
        assert application.settings.plugins_enabled is False
        assert application.plugins is None
        assert not any(
            name.startswith("plugin_") for name in application.tool_executor.list_names()
        )
    finally:
        application.close()


def test_enabled_runtime_discovers_but_starts_nothing_until_trusted(tmp_path) -> None:
    root = tmp_path / "plugins"
    install_plugin(root, "echo")
    application = create_application(
        settings_for(tmp_path, plugins_enabled=True, plugins_directory=str(root))
    )
    try:
        assert application.plugins is not None
        record = application.plugins.get("echo")
        assert record.state is PluginState.DISABLED
        assert record.enabled is False
        assert not any(
            name.startswith("plugin_") for name in application.tool_executor.list_names()
        )
        events = application.diagnostics.events(component="plugins")["events"]
        assert [event["name"] for event in events] == ["plugin.discovered"]
    finally:
        application.close()


def test_previously_trusted_plugin_starts_at_bootstrap_and_stops_on_close(tmp_path) -> None:
    root = tmp_path / "plugins"
    install_plugin(root, "echo")
    root.mkdir(exist_ok=True)
    (root / STATE_FILENAME).write_text(
        json.dumps({"schema_version": 1, "enabled": {"echo": True}}), encoding="utf-8"
    )
    application = create_application(
        settings_for(tmp_path, plugins_enabled=True, plugins_directory=str(root))
    )
    try:
        assert application.plugins.get("echo").state is PluginState.RUNNING
        assert "plugin_echo_echo" in application.tool_executor.list_names()
        contract = next(
            c
            for c in application.tool_executor.get_contract_objects()
            if c.definition.name == "plugin_echo_echo"
        )
        assert contract.source == "plugin:echo"
        assert contract.definition.risk_level is RiskLevel.LOW
        result = application.tool_executor.execute(
            "plugin_echo_echo", parameters={"text": "boot"}
        )
        assert result.data == {"echo": "boot", "plugin": "echo"}
    finally:
        application.close()
    assert "plugin_echo_echo" not in application.tool_executor.list_names()


def test_plugin_settings_parse_and_validate(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("JARVIS_PLUGINS_ENABLED", "true")
    monkeypatch.setenv("JARVIS_PLUGINS_DIRECTORY", str(tmp_path / "plugins"))
    monkeypatch.setenv("JARVIS_PLUGIN_TOOL_TIMEOUT_SECONDS", "2.5")
    monkeypatch.setenv("JARVIS_PLUGIN_MAX_CONSECUTIVE_FAILURES", "5")

    settings = Settings.from_environment()

    assert settings.plugins_enabled is True
    assert settings.plugins_directory == str(tmp_path / "plugins")
    assert settings.plugin_tool_timeout_seconds == 2.5
    assert settings.plugin_max_consecutive_failures == 5

    monkeypatch.delenv("JARVIS_PLUGINS_ENABLED")
    monkeypatch.delenv("JARVIS_PLUGINS_DIRECTORY")
    defaults = Settings.from_environment()
    assert defaults.plugins_enabled is False
    assert defaults.plugins_directory is not None
    assert defaults.plugins_directory.endswith("plugins")

    with pytest.raises(ValueError):
        settings_for(tmp_path, plugin_tool_timeout_seconds=0)
    with pytest.raises(ValueError):
        settings_for(tmp_path, plugin_max_consecutive_failures=0)
    with pytest.raises(ValueError):
        settings_for(tmp_path, plugins_directory="   ")


def test_plugin_tools_satisfy_the_global_contract_invariants(tmp_path) -> None:
    root = tmp_path / "plugins"
    install_plugin(root, "echo")
    (root / STATE_FILENAME).write_text(
        json.dumps({"schema_version": 1, "enabled": {"echo": True}}), encoding="utf-8"
    )
    application = create_application(
        settings_for(tmp_path, plugins_enabled=True, plugins_directory=str(root))
    )
    try:
        contracts = application.tool_executor.get_contract_objects(include_disabled=True)
        names = [contract.definition.name for contract in contracts]
        assert len(names) == len(set(names))
        for contract in contracts:
            json.dumps(contract.to_dict())
            assert contract.input_schema.get("additionalProperties") is False
            if contract.definition.risk_level in {
                RiskLevel.MEDIUM,
                RiskLevel.HIGH,
                RiskLevel.CRITICAL,
            }:
                assert contract.definition.requires_confirmation is True
    finally:
        application.close()
