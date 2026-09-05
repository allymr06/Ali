"""Plugin security boundaries: no shadowing, no escape, no lowered risk."""

from __future__ import annotations

import json

import pytest

from app.core.models import RiskLevel, ToolDefinition
from app.diagnostics.service import DiagnosticsService
from app.plugins import PluginContext, PluginRuntime, PluginState
from app.security.permissions import PermissionEngine
from app.tools.executor import ToolExecutor
from tests.plugin_helpers import (
    EXTRA_TOOL_PLUGIN,
    MISSING_TOOL_PLUGIN,
    echo_manifest,
    fixture_manifest,
    install_plugin,
)


def runtime_for(root):
    executor = ToolExecutor(PermissionEngine())
    runtime = PluginRuntime(
        directory=root, tool_executor=executor, diagnostics=DiagnosticsService()
    )
    runtime.discover()
    runtime.start_enabled()
    return runtime, executor


def test_plugin_cannot_shadow_a_registered_tool(tmp_path) -> None:
    root = tmp_path / "plugins"
    manifest = fixture_manifest()
    manifest["tools"].append(
        {"name": "second", "description": "Another tool.", "parameters": []}
    )
    install_plugin(
        root,
        "echo",
        manifest=manifest,
        code=(
            "def create_plugin(context):\n"
            "    return {'echo': lambda text, repeat=None: {'echo': text},"
            " 'second': lambda: {'ok': True}}\n"
        ),
    )
    runtime, executor = runtime_for(root)
    executor.register(
        ToolDefinition(name="plugin_echo_second", description="Built-in first."),
        lambda: "builtin",
    )

    record = runtime.enable("echo")

    assert record.state is PluginState.FAILED
    assert "could not be registered" in record.error
    # Nothing partial survives: the first plugin tool was rolled back.
    assert executor.list_names() == ("plugin_echo_second",)
    assert executor.execute("plugin_echo_second").data == "builtin"
    runtime.stop_all()
    assert executor.list_names() == ("plugin_echo_second",)


def test_entry_module_link_or_escape_is_rejected(tmp_path, monkeypatch) -> None:
    root = tmp_path / "plugins"
    install_plugin(root, "echo")
    runtime, executor = runtime_for(root)
    monkeypatch.setattr(
        "app.plugins.runtime.is_reparse_point",
        lambda path: path.name == "plugin.py",
    )

    record = runtime.enable("echo")

    assert record.state is PluginState.FAILED
    assert "missing or is a link" in record.error
    assert executor.list_names() == ()
    runtime.stop_all()


def test_missing_entry_module_is_rejected(tmp_path) -> None:
    root = tmp_path / "plugins"
    directory = install_plugin(root, "echo")
    (directory / "plugin.py").unlink()
    runtime, executor = runtime_for(root)

    record = runtime.enable("echo")

    assert record.state is PluginState.FAILED
    assert "missing" in record.error
    runtime.stop_all()


def test_undeclared_or_missing_tools_are_rejected(tmp_path) -> None:
    root = tmp_path / "plugins"
    install_plugin(root, "extra", manifest=echo_manifest("extra"), code=EXTRA_TOOL_PLUGIN)
    install_plugin(root, "missing", manifest=echo_manifest("missing"), code=MISSING_TOOL_PLUGIN)
    runtime, executor = runtime_for(root)

    extra = runtime.enable("extra")
    missing = runtime.enable("missing")

    assert extra.state is PluginState.FAILED
    assert "not declared" in extra.error
    assert missing.state is PluginState.FAILED
    assert "no callable implementation" in missing.error
    assert executor.list_names() == ()
    runtime.stop_all()


def test_plugin_tools_never_drop_below_the_risk_floor(tmp_path) -> None:
    root = tmp_path / "plugins"
    install_plugin(root, "quiet", manifest=echo_manifest("quiet", risk_level="read_only"))
    install_plugin(
        root,
        "risky",
        manifest=echo_manifest("risky", risk_level="high", requires_confirmation=False),
    )
    runtime, executor = runtime_for(root)
    runtime.enable("quiet")
    runtime.enable("risky")

    contracts = {c.definition.name: c for c in executor.get_contract_objects()}
    assert contracts["plugin_quiet_echo"].definition.risk_level is RiskLevel.LOW
    assert contracts["plugin_risky_echo"].definition.risk_level is RiskLevel.HIGH
    assert contracts["plugin_risky_echo"].definition.requires_confirmation is True
    for contract in contracts.values():
        assert contract.source.startswith("plugin:")
        assert contract.definition.name.startswith("plugin_")
        json.dumps(contract.to_dict())
    runtime.stop_all()


def test_rejected_plugins_cannot_be_enabled(tmp_path) -> None:
    root = tmp_path / "plugins"
    install_plugin(root, "broken", manifest_text='{"schema_version": 1}')
    runtime, executor = runtime_for(root)

    assert runtime.get("broken").state is PluginState.REJECTED
    with pytest.raises(ValueError, match="cannot be enabled"):
        runtime.enable("broken")
    assert executor.list_names() == ()
    runtime.stop_all()


def test_plugin_context_exposes_no_secrets_or_services() -> None:
    fields = set(PluginContext.__dataclass_fields__)
    assert fields == {"plugin_id", "version", "data_directory", "log"}


def test_plugin_directory_is_isolated_per_plugin(tmp_path) -> None:
    root = tmp_path / "plugins"
    install_plugin(
        root,
        "echo",
        code=(
            "def create_plugin(context):\n"
            "    (context.data_directory / 'note.txt').write_text('hello')\n"
            "    return {'echo': lambda text, repeat=None: {'echo': text}}\n"
        ),
    )
    runtime, executor = runtime_for(root)
    runtime.enable("echo")

    assert (root / "echo" / "data" / "note.txt").read_text() == "hello"
    runtime.stop_all()
