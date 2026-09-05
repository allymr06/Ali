"""Plugin runtime: trust, lifecycle, executor integration, failure isolation."""

from __future__ import annotations

import json
import sys

import pytest

from app.core.models import RiskLevel, ToolExecutionStatus
from app.diagnostics.service import DiagnosticsService
from app.plugins import PluginRuntime, PluginState
from app.plugins.runtime import STATE_FILENAME
from app.security.permissions import PermissionEngine
from app.tools.executor import ToolExecutor
from tests.plugin_helpers import (
    BIG_OUTPUT_PLUGIN,
    FAILING_PLUGIN,
    IMPORT_ERROR_PLUGIN,
    NON_JSON_PLUGIN,
    SLOW_PLUGIN,
    echo_manifest,
    install_plugin,
)
from tests.security_helpers import bound_approval


def runtime_for(root, **options):
    executor = ToolExecutor(PermissionEngine())
    diagnostics = DiagnosticsService()
    runtime = PluginRuntime(
        directory=root,
        tool_executor=executor,
        diagnostics=diagnostics,
        **options,
    )
    return runtime, executor, diagnostics


def event_names(diagnostics) -> list[str]:
    events = diagnostics.events(component="plugins", limit=200)["events"]
    return [event["name"] for event in events]


@pytest.fixture
def plugins_root(tmp_path):
    root = tmp_path / "plugins"
    install_plugin(root, "echo")
    return root


def test_plugins_are_disabled_by_default(plugins_root) -> None:
    runtime, executor, diagnostics = runtime_for(plugins_root)

    records = runtime.discover()
    runtime.start_enabled()

    assert [(r.plugin_id, r.state, r.enabled) for r in records] == [
        ("echo", PluginState.DISABLED, False)
    ]
    assert executor.list_names() == ()
    assert "plugin.discovered" in event_names(diagnostics)
    assert "plugin.started" not in event_names(diagnostics)
    runtime.stop_all()


def test_enable_registers_namespaced_tools_and_persists_the_choice(plugins_root) -> None:
    runtime, executor, diagnostics = runtime_for(plugins_root)
    runtime.discover()
    runtime.start_enabled()

    record = runtime.enable("echo")

    assert record.state is PluginState.RUNNING
    assert record.registered_tools == ("plugin_echo_echo",)
    assert executor.list_names() == ("plugin_echo_echo",)
    contract = executor.get_contract_objects()[0]
    assert contract.source == "plugin:echo"
    assert contract.definition.risk_level is RiskLevel.LOW
    assert contract.definition.capabilities == frozenset({"plugin", "plugin:echo"})
    assert contract.definition.metadata["plugin_id"] == "echo"
    schema = contract.to_dict()["input_schema"]
    assert schema["additionalProperties"] is False
    assert schema["required"] == ["text"]
    assert schema["properties"]["repeat"]["default"] is None
    state = json.loads((plugins_root / STATE_FILENAME).read_text(encoding="utf-8"))
    assert state == {"schema_version": 1, "enabled": {"echo": True}}
    assert "plugin.log" in event_names(diagnostics)  # context.log reached the ledger
    runtime.stop_all()

    # A fresh runtime honours the persisted decision and starts the plugin.
    fresh, fresh_executor, _ = runtime_for(plugins_root)
    fresh.discover()
    assert fresh.get("echo").enabled is True
    fresh.start_enabled()
    assert fresh_executor.list_names() == ("plugin_echo_echo",)
    fresh.stop_all()


def test_tool_calls_go_through_the_executor_contract(plugins_root) -> None:
    runtime, executor, _ = runtime_for(plugins_root)
    runtime.discover()
    runtime.start_enabled()
    runtime.enable("echo")

    result = executor.execute("plugin_echo_echo", parameters={"text": "hi", "repeat": 2})
    assert result.status is ToolExecutionStatus.SUCCESS
    assert result.data == {"echo": "hihi", "plugin": "echo"}
    assert result.verified is False

    unknown = executor.execute("plugin_echo_echo", parameters={"text": "hi", "bogus": 1})
    assert unknown.status is ToolExecutionStatus.FAILED
    assert "Invalid tool arguments" in unknown.message

    wrong_type = executor.execute("plugin_echo_echo", parameters={"text": 5})
    assert wrong_type.status is ToolExecutionStatus.FAILED
    runtime.stop_all()


def test_disable_and_stop_release_tools_and_modules(plugins_root) -> None:
    runtime, executor, diagnostics = runtime_for(plugins_root)
    runtime.discover()
    runtime.start_enabled()
    runtime.enable("echo")
    assert "jarvis_plugins.echo" in sys.modules

    record = runtime.disable("echo")

    assert record.state is PluginState.DISABLED
    assert record.enabled is False
    assert executor.list_names() == ()
    assert "jarvis_plugins.echo" not in sys.modules
    state = json.loads((plugins_root / STATE_FILENAME).read_text(encoding="utf-8"))
    assert state["enabled"] == {}
    assert "plugin.stopped" in event_names(diagnostics)

    runtime.enable("echo")
    assert executor.list_names() == ("plugin_echo_echo",)
    runtime.stop_all()
    assert executor.list_names() == ()
    assert "jarvis_plugins.echo" not in sys.modules


def test_entry_point_failure_is_isolated(tmp_path) -> None:
    root = tmp_path / "plugins"
    install_plugin(root, "broken", code=IMPORT_ERROR_PLUGIN)
    install_plugin(root, "echo")
    runtime, executor, diagnostics = runtime_for(root)
    runtime.discover()
    runtime.start_enabled()

    runtime.enable("broken")
    runtime.enable("echo")

    broken = runtime.get("broken")
    assert broken.state is PluginState.FAILED
    assert broken.error == "RuntimeError"  # class name only, no plugin text
    assert "secret path" not in json.dumps(runtime.snapshot())
    assert "jarvis_plugins.broken" not in sys.modules
    assert executor.list_names() == ("plugin_echo_echo",)
    assert "plugin.failed" in event_names(diagnostics)
    runtime.stop_all()


def test_repeated_tool_failures_quarantine_the_plugin(tmp_path) -> None:
    root = tmp_path / "plugins"
    install_plugin(root, "echo", code=FAILING_PLUGIN)
    runtime, executor, diagnostics = runtime_for(root, max_consecutive_failures=2)
    runtime.discover()
    runtime.start_enabled()
    runtime.enable("echo")

    first = executor.execute("plugin_echo_echo", parameters={"text": "a"})
    assert first.status is ToolExecutionStatus.FAILED
    assert first.error == "Plugin tool raised RuntimeError."
    assert "secret detail" not in f"{first.message} {first.error} {first.data}"
    assert runtime.get("echo").state is PluginState.RUNNING

    second = executor.execute("plugin_echo_echo", parameters={"text": "b"})
    assert second.status is ToolExecutionStatus.FAILED

    record = runtime.get("echo")
    assert record.state is PluginState.FAILED
    assert record.enabled is False
    assert "auto-disabled after 2" in record.error
    assert executor.list_names() == ()  # disabled tools are hidden
    third = executor.execute("plugin_echo_echo", parameters={"text": "c"})
    assert third.status is ToolExecutionStatus.FAILED
    assert "not registered" in third.message
    state = json.loads((root / STATE_FILENAME).read_text(encoding="utf-8"))
    assert state["enabled"] == {}
    assert "plugin.auto_disabled" in event_names(diagnostics)

    # An explicit enable re-arms the registered tools and resets the count.
    rearmed = runtime.enable("echo")
    assert rearmed.state is PluginState.RUNNING
    assert rearmed.consecutive_failures == 0
    assert executor.list_names() == ("plugin_echo_echo",)
    runtime.stop_all()


def test_timeouts_are_honest_and_counted(tmp_path) -> None:
    root = tmp_path / "plugins"
    install_plugin(root, "echo", code=SLOW_PLUGIN)
    runtime, executor, _ = runtime_for(root, tool_timeout_seconds=0.2)
    runtime.discover()
    runtime.start_enabled()
    runtime.enable("echo")

    result = executor.execute("plugin_echo_echo", parameters={"text": "slow"})

    assert result.status is ToolExecutionStatus.TIMEOUT
    assert result.side_effects_may_continue is True
    assert runtime.get("echo").consecutive_failures == 1
    runtime.stop_all()


def test_invalid_and_oversized_outputs_are_rejected(tmp_path) -> None:
    root = tmp_path / "plugins"
    install_plugin(root, "nojson", manifest=echo_manifest("nojson"), code=NON_JSON_PLUGIN)
    install_plugin(root, "big", manifest=echo_manifest("big"), code=BIG_OUTPUT_PLUGIN)
    runtime, executor, _ = runtime_for(root, max_output_bytes=1024)
    runtime.discover()
    runtime.start_enabled()
    runtime.enable("nojson")
    runtime.enable("big")

    invalid = executor.execute("plugin_nojson_echo", parameters={"text": "x"})
    assert invalid.status is ToolExecutionStatus.FAILED
    assert "JSON" in invalid.error

    oversized = executor.execute("plugin_big_echo", parameters={"text": "x"})
    assert oversized.status is ToolExecutionStatus.FAILED
    assert "exceeds" in oversized.error
    runtime.stop_all()


def test_medium_risk_tool_needs_a_bound_approval(tmp_path) -> None:
    root = tmp_path / "plugins"
    install_plugin(root, "echo", manifest=echo_manifest(risk_level="medium"))
    runtime, executor, _ = runtime_for(root)
    runtime.discover()
    runtime.start_enabled()
    runtime.enable("echo")
    parameters = {"text": "careful"}

    blocked = executor.execute("plugin_echo_echo", parameters=parameters)
    assert blocked.status is ToolExecutionStatus.BLOCKED
    assert "confirmation required" in blocked.error.lower()

    flagged = executor.execute(
        "plugin_echo_echo", parameters=parameters, confirmation_granted=True
    )
    assert flagged.status is ToolExecutionStatus.BLOCKED

    approved = executor.execute(
        "plugin_echo_echo",
        parameters=parameters,
        **bound_approval("plugin_echo_echo", parameters=parameters),
    )
    assert approved.status is ToolExecutionStatus.SUCCESS
    assert approved.data == {"echo": "careful", "plugin": "echo"}
    runtime.stop_all()


def test_rediscovery_keeps_running_plugins_and_drops_removed_ones(plugins_root, tmp_path) -> None:
    runtime, executor, _ = runtime_for(plugins_root)
    runtime.discover()
    runtime.start_enabled()
    runtime.enable("echo")
    install_plugin(plugins_root, "later", manifest=echo_manifest("later"))

    records = runtime.discover()

    assert [(r.plugin_id, r.state) for r in records] == [
        ("echo", PluginState.RUNNING),
        ("later", PluginState.DISABLED),
    ]
    assert executor.list_names() == ("plugin_echo_echo",)

    import shutil

    shutil.rmtree(plugins_root / "echo")
    records = runtime.discover()
    assert [r.plugin_id for r in records] == ["later"]
    assert executor.list_names() == ()
    runtime.stop_all()


def test_corrupt_state_file_fails_closed(plugins_root) -> None:
    (plugins_root / STATE_FILENAME).write_text("{corrupt", encoding="utf-8")
    runtime, executor, diagnostics = runtime_for(plugins_root)

    runtime.discover()
    runtime.start_enabled()

    assert runtime.get("echo").enabled is False
    assert executor.list_names() == ()
    assert "plugin.state_invalid" in event_names(diagnostics)
    runtime.stop_all()


def test_runtime_rejects_unsafe_limits(plugins_root) -> None:
    executor = ToolExecutor(PermissionEngine())
    with pytest.raises(ValueError):
        PluginRuntime(directory=plugins_root, tool_executor=executor, tool_timeout_seconds=0)
    with pytest.raises(ValueError):
        PluginRuntime(directory=plugins_root, tool_executor=executor, tool_timeout_seconds=500)
    with pytest.raises(ValueError):
        PluginRuntime(directory=plugins_root, tool_executor=executor, max_consecutive_failures=0)
    with pytest.raises(KeyError):
        PluginRuntime(directory=plugins_root, tool_executor=executor).get("nope")


def test_rediscovery_after_quarantine_releases_registered_tools(tmp_path) -> None:
    root = tmp_path / "plugins"
    install_plugin(root, "echo", code=FAILING_PLUGIN)
    runtime, executor, _ = runtime_for(root, max_consecutive_failures=1)
    runtime.discover()
    runtime.start_enabled()
    runtime.enable("echo")
    executor.execute("plugin_echo_echo", parameters={"text": "a"})
    assert runtime.get("echo").state is PluginState.FAILED
    assert len(executor.get_contract_objects(include_disabled=True)) == 1

    runtime.discover()

    assert runtime.get("echo").state is PluginState.DISABLED
    assert executor.get_contract_objects(include_disabled=True) == ()
    assert "jarvis_plugins.echo" not in sys.modules
    rearmed = runtime.enable("echo")
    assert rearmed.state is PluginState.RUNNING
    assert executor.list_names() == ("plugin_echo_echo",)
    runtime.stop_all()
