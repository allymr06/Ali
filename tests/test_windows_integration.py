from __future__ import annotations

import os
from pathlib import Path

import pytest

from app.config.settings import Settings
from app.core.models import Request, ToolExecutionStatus, ToolResult
from app.main import create_application
from app.platform.windows import (
    WindowsApplication,
    WindowsApplicationLauncher,
    WindowsApplicationRegistry,
    WindowsIntegrationService,
    WindowsProcess,
    WindowsProcessInspector,
)
from app.platform.windows.models import WindowsLaunchOutcome
from app.providers.base import ModelResponse
from app.providers.mock import MockProvider
from app.tools.executor import ToolExecutor


def make_application(executable: str) -> WindowsApplication:
    return WindowsApplication(
        application_id="editor",
        display_name="Editor",
        executable=executable,
        aliases=frozenset({"text", "edit"}),
        arguments=("--safe",),
        process_names=frozenset({"editor.exe"}),
        capabilities=frozenset({"text"}),
        source="test",
    )


def test_windows_application_contract_normalizes_discovery_data() -> None:
    application = WindowsApplication(
        application_id=" Editor ",
        display_name=" Editor ",
        executable="editor.exe",
        aliases=frozenset({" TEXT ", "Edit"}),
        process_names=frozenset({"EDITOR.EXE"}),
        capabilities=frozenset({" TEXT "}),
    )

    assert application.application_id == "editor"
    assert application.display_name == "Editor"
    assert application.aliases == frozenset({"text", "edit"})
    assert application.expected_process_names == frozenset({"editor.exe"})


@pytest.mark.parametrize(
    "kwargs",
    [
        {"application_id": "bad id"},
        {"application_id": ""},
        {"display_name": ""},
        {"executable": ""},
        {"aliases": "alias"},
        {"arguments": ["not", "tuple"]},
    ],
)
def test_windows_application_rejects_invalid_contracts(kwargs) -> None:
    values = {
        "application_id": "editor",
        "display_name": "Editor",
        "executable": "editor.exe",
    }
    values.update(kwargs)

    with pytest.raises((TypeError, ValueError)):
        WindowsApplication(**values)


def test_application_registry_resolves_aliases_and_tracks_lifecycle() -> None:
    registry = WindowsApplicationRegistry()
    application = make_application("editor.exe")

    registry.register(application)

    assert registry.revision == 1
    assert registry.resolve(" TEXT ") is application
    assert registry.contains("edit")
    assert registry.list() == (application,)
    assert registry.unregister("text") is application
    assert registry.revision == 2
    assert registry.contains("editor") is False


def test_application_registry_rejects_alias_collision() -> None:
    registry = WindowsApplicationRegistry()
    registry.register(make_application("editor.exe"))

    with pytest.raises(ValueError, match="already registered"):
        registry.register(
            WindowsApplication(
                application_id="other",
                display_name="Other",
                executable="other.exe",
                aliases=frozenset({"text"}),
            )
        )


def test_registry_rejects_command_expression_as_executable() -> None:
    application = make_application("cmd.exe /c whoami")

    with pytest.raises(ValueError, match="absolute path or a bare"):
        WindowsApplicationRegistry.resolve_executable(application)


def test_registry_rejects_non_executable_absolute_file(tmp_path) -> None:
    script = tmp_path / "launch.cmd"
    script.write_text("echo unsafe", encoding="utf-8")

    with pytest.raises(ValueError, match=".exe"):
        WindowsApplicationRegistry.resolve_executable(
            make_application(str(script))
        )


def test_tasklist_parser_is_bounded_and_ignores_malformed_rows() -> None:
    lines = [
        '"notepad.exe","120","Console","1","12,345 K"',
        '"broken.exe","not-a-pid","Console","1","1 K"',
        '"python.exe","240","Console","1","5.432 K"',
    ]

    all_processes = WindowsProcessInspector.parse_tasklist_csv(lines, limit=1)
    python_only = WindowsProcessInspector.parse_tasklist_csv(
        lines,
        name="PYTHON.EXE",
        limit=10,
    )

    assert len(all_processes) == 1
    assert all_processes[0].memory_kb == 12345
    assert [process.pid for process in python_only] == [240]
    assert python_only[0].memory_kb == 5432


def test_launcher_uses_registry_command_and_verifies_new_process(tmp_path) -> None:
    executable = tmp_path / "editor.exe"
    executable.write_bytes(b"test")
    registry = WindowsApplicationRegistry()
    registry.register(make_application(str(executable)))
    commands: list[list[str]] = []

    class Observer:
        def get_process(self, pid: int) -> WindowsProcess | None:
            return WindowsProcess(
                pid=pid,
                name="editor.exe",
                executable_path=str(executable),
            )

    def spawn(command: list[str]) -> int:
        commands.append(command)
        return 321

    launcher = WindowsApplicationLauncher(
        registry,
        Observer(),
        spawner=spawn,
        verification_timeout_seconds=0.1,
        poll_interval_seconds=0.001,
    )

    result = launcher.launch("text")

    assert result.verified is True
    assert result.pid == 321
    assert commands == [[str(executable.resolve()), "--safe"]]


def test_launcher_rejects_unknown_application_without_spawning() -> None:
    spawned = False

    class Observer:
        def get_process(self, pid: int) -> WindowsProcess | None:
            return None

    def spawn(command: list[str]) -> int:
        nonlocal spawned
        spawned = True
        return 1

    launcher = WindowsApplicationLauncher(
        WindowsApplicationRegistry(),
        Observer(),
        spawner=spawn,
        verification_timeout_seconds=0.01,
        poll_interval_seconds=0.001,
    )

    result = launcher.launch("cmd.exe /c destructive-command")

    assert result.verified is False
    assert "unknown" in (result.error or "").lower()
    assert spawned is False


def test_launcher_fails_when_observed_process_identity_does_not_match(
    tmp_path,
) -> None:
    executable = tmp_path / "editor.exe"
    executable.write_bytes(b"test")
    registry = WindowsApplicationRegistry()
    registry.register(make_application(str(executable)))

    class Observer:
        def get_process(self, pid: int) -> WindowsProcess | None:
            return WindowsProcess(pid=pid, name="different.exe")

    launcher = WindowsApplicationLauncher(
        registry,
        Observer(),
        spawner=lambda _: 444,
        verification_timeout_seconds=0.1,
        poll_interval_seconds=0.001,
    )

    result = launcher.launch("editor")

    assert result.verified is False
    assert "does not match" in (result.error or "")


def test_windows_service_registers_verified_tools(tmp_path) -> None:
    executable = tmp_path / "editor.exe"
    executable.write_bytes(b"test")
    registry = WindowsApplicationRegistry()
    registry.register(make_application(str(executable)))

    class Processes:
        def list_processes(self, *, name=None, limit=100):
            return (WindowsProcess(pid=1, name=name or "system.exe"),)

    class Launcher:
        def launch(self, application: str) -> WindowsLaunchOutcome:
            return WindowsLaunchOutcome(
                application_id="editor",
                pid=321,
                verified=True,
                message="Editor verified.",
                process=WindowsProcess(pid=321, name="editor.exe"),
            )

    service = WindowsIntegrationService(registry, Processes(), Launcher())
    executor = ToolExecutor()
    service.register_tools(executor)

    applications = executor.execute("list_windows_applications")
    processes = executor.execute(
        "list_windows_processes",
        parameters={"limit": 1},
    )
    launched = executor.execute(
        "launch_windows_application",
        parameters={"application": "editor"},
    )

    assert applications.status is ToolExecutionStatus.SUCCESS
    assert applications.verified is True
    assert processes.verified is True
    assert launched.verified is True
    assert launched.data["pid"] == 321


def test_windows_service_reports_unverified_launch_as_failure() -> None:
    class Processes:
        def list_processes(self, *, name=None, limit=100):
            return ()

    class Launcher:
        def launch(self, application: str) -> WindowsLaunchOutcome:
            return WindowsLaunchOutcome(
                application_id=application,
                pid=99,
                verified=False,
                message="Not verified.",
                error="Process disappeared.",
            )

    service = WindowsIntegrationService(
        WindowsApplicationRegistry(),
        Processes(),
        Launcher(),
    )

    result = service.launch_application("missing")

    assert result.status is ToolExecutionStatus.FAILED
    assert result.verified is False
    assert result.error == "Process disappeared."


@pytest.mark.skipif(os.name != "nt", reason="Windows-only native observation")
def test_native_process_inspector_observes_current_process() -> None:
    process = WindowsProcessInspector().get_process(os.getpid())

    assert process is not None
    assert process.pid == os.getpid()
    assert process.executable_path


@pytest.mark.skipif(os.name != "nt", reason="Windows-only native observation")
def test_native_system_info_reports_local_windows_state() -> None:
    info = WindowsIntegrationService.system_info()

    assert info["system"] == "Windows"
    assert info["logical_cpu_count"]
    assert info["memory_total_bytes"] > 0
    assert info["disk_total_bytes"] > 0


@pytest.mark.skipif(os.name != "nt", reason="Windows-only native observation")
def test_native_system_info_exposes_deterministic_gib_values() -> None:
    info = WindowsIntegrationService.system_info()
    gibibyte = 1024 ** 3

    assert info["memory_total_gib"] == round(
        info["memory_total_bytes"] / gibibyte,
        2,
    )
    assert info["memory_available_gib"] == round(
        info["memory_available_bytes"] / gibibyte,
        2,
    )
    assert info["disk_total_gib"] == round(
        info["disk_total_bytes"] / gibibyte,
        2,
    )
    assert info["disk_free_gib"] == round(
        info["disk_free_bytes"] / gibibyte,
        2,
    )

    assert info["memory_available_gib"] <= info["memory_total_gib"]
    assert info["disk_free_gib"] <= info["disk_total_gib"]


def test_bootstrap_registers_windows_tools_on_windows() -> None:
    application = create_application()

    if os.name == "nt":
        assert application.windows is not None
        assert application.tool_executor.contains("launch_windows_application")
    else:
        assert application.windows is None


def test_windows_integrations_can_be_disabled() -> None:
    application = create_application(Settings(windows_integrations_enabled=False))

    assert application.windows is None
    assert not application.tool_executor.contains("launch_windows_application")


@pytest.mark.asyncio
async def test_core_executes_verified_windows_observation_tool() -> None:
    class WindowsToolProvider(MockProvider):
        def __init__(self) -> None:
            self.calls = 0

        async def generate(self, request, context, **kwargs):
            self.calls += 1
            if self.calls == 1:
                names = {
                    item["function"]["name"]
                    for item in (kwargs.get("tools") or [])
                }
                assert "list_windows_applications" in names
                return ModelResponse(
                    text="",
                    model="mock-model",
                    provider="mock",
                    finish_reason="tool_calls",
                    tool_calls=[
                        {
                            "id": "windows-call-1",
                            "type": "function",
                            "function": {
                                "name": "list_windows_applications",
                                "arguments": "{}",
                            },
                        }
                    ],
                )
            return ModelResponse(
                text="Windows applications inspected.",
                model="mock-model",
                provider="mock",
                finish_reason="stop",
            )

    application = create_application()
    application.provider_registry.unregister("mock")
    application.provider_registry.register(
        WindowsToolProvider(),
        make_default=True,
    )

    response = await application.engine.handle(Request("List Windows apps"))

    assert response.text == "Windows applications inspected."
    assert response.metadata["tool_calls"] == 1
    assert response.metadata["verified_tool_calls"] == 1
    assert response.metadata["completion_verified"] is True



def test_default_file_explorer_forces_visible_shell_window() -> None:
    registry = (
        WindowsApplicationRegistry
        .with_windows_defaults()
    )

    explorer = registry.resolve(
        "dosyagezgini"
    )

    assert (
        explorer.application_id
        == "file-explorer"
    )

    assert (
        explorer.arguments
        == (
            "shell:MyComputerFolder",
        )
    )


def test_console_launcher_uses_visible_new_console(
    monkeypatch,
) -> None:
    import app.platform.windows.launcher as launcher_module

    captured = {}

    class FakeProcess:
        pid = 4321

    def fake_popen(
        command,
        **kwargs,
    ):
        captured["command"] = command
        captured["kwargs"] = kwargs

        return FakeProcess()

    monkeypatch.setattr(
        launcher_module.subprocess,
        "Popen",
        fake_popen,
    )

    monkeypatch.setattr(
        launcher_module.subprocess,
        "CREATE_NEW_CONSOLE",
        0x00000010,
        raising=False,
    )

    pid = (
        launcher_module._spawn_process(
            [
                (
                    r"C:\Windows"
                    r"\System32\cmd.exe"
                )
            ]
        )
    )

    assert pid == 4321

    assert (
        captured["kwargs"][
            "creationflags"
        ]
        == 0x00000010
    )

    assert (
        "stdin"
        not in captured["kwargs"]
    )

    assert (
        "stdout"
        not in captured["kwargs"]
    )

    assert (
        "stderr"
        not in captured["kwargs"]
    )

    assert (
        captured["kwargs"][
            "shell"
        ]
        is False
    )
