from __future__ import annotations

from dataclasses import dataclass, replace

from app.core.models import RiskLevel, ToolExecutionStatus
from app.platform.windows.window_control import (
    AllowedWindowApplication,
    NativeWindowState,
    WindowsWindowControlService,
)
from app.tools.executor import ToolExecutor


def window(
    *,
    handle: int = 101,
    process_id: int = 10,
    process_name: str = "notepad.exe",
    title: str = "Notes",
    visible: bool = True,
    minimized: bool = False,
    active: bool = False,
    executable_path: str | None = None,
) -> NativeWindowState:
    return NativeWindowState(
        handle=handle,
        process_id=process_id,
        process_name=process_name,
        executable_path=executable_path,
        title=title,
        visible=visible,
        minimized=minimized,
        active=active,
    )


@dataclass
class FakeWindowBackend:
    windows: dict[int, NativeWindowState]
    fail_list: bool = False
    fail_get: bool = False
    fail_action: bool = False
    ignore_action: bool = False

    def __post_init__(self) -> None:
        self.actions: list[tuple[str, int]] = []

    def list_windows(self):
        if self.fail_list:
            raise RuntimeError("desktop unavailable")
        return tuple(self.windows.values())

    def get_window(self, handle: int):
        if self.fail_get:
            raise RuntimeError("window unavailable")
        return self.windows.get(handle)

    def _apply(self, operation: str, handle: int) -> bool:
        self.actions.append((operation, handle))
        if self.fail_action:
            raise RuntimeError("native action unavailable")
        current = self.windows.get(handle)
        if current is None:
            return False
        if self.ignore_action:
            return True
        if operation == "activate":
            self.windows[handle] = replace(current, active=True, minimized=False)
        elif operation == "minimize":
            self.windows[handle] = replace(current, minimized=True, active=False)
        else:
            self.windows[handle] = replace(current, minimized=False)
        return True

    def activate(self, handle: int) -> bool:
        return self._apply("activate", handle)

    def minimize(self, handle: int) -> bool:
        return self._apply("minimize", handle)

    def restore(self, handle: int) -> bool:
        return self._apply("restore", handle)


def service(backend: FakeWindowBackend) -> WindowsWindowControlService:
    return WindowsWindowControlService(
        backend,
        (
            AllowedWindowApplication(
                "notepad",
                process_names=frozenset({"notepad.exe"}),
            ),
        ),
    )


def listed_window_id(control: WindowsWindowControlService) -> str:
    result = control.list_allowed_windows()
    assert result.succeeded
    return result.data[0]["window_id"]


def test_window_listing_only_exposes_visible_allowlisted_windows_with_opaque_ids() -> None:
    backend = FakeWindowBackend(
        {
            101: window(handle=101),
            202: window(handle=202, process_name="evil.exe", title="Private"),
            303: window(handle=303, title="Hidden", visible=False),
            404: window(handle=404, title=""),
        }
    )

    result = service(backend).list_allowed_windows()

    assert result.status is ToolExecutionStatus.SUCCESS
    assert len(result.data) == 1
    exposed = result.data[0]
    assert exposed["application_id"] == "notepad"
    assert exposed["title"] == "Notes"
    assert exposed["window_id"] != "101"
    assert 101 not in exposed.values()
    assert "handle" not in exposed and "process_id" not in exposed


def test_window_action_requires_previously_listed_matching_opaque_identity() -> None:
    backend = FakeWindowBackend({101: window()})
    control = service(backend)

    unknown = control.activate("notepad", "101")
    window_id = listed_window_id(control)
    wrong_application = control.activate("calculator", window_id)

    assert unknown.status is ToolExecutionStatus.BLOCKED
    assert wrong_application.status is ToolExecutionStatus.BLOCKED
    assert backend.actions == []


def test_window_identity_is_revalidated_immediately_before_action() -> None:
    backend = FakeWindowBackend({101: window()})
    control = service(backend)
    window_id = listed_window_id(control)
    backend.windows[101] = window(process_name="evil.exe")

    result = control.minimize("notepad", window_id)

    assert result.status is ToolExecutionStatus.BLOCKED
    assert result.verified is False
    assert backend.actions == []


def test_activate_minimize_and_restore_are_verified_after_mutation() -> None:
    backend = FakeWindowBackend({101: window()})
    control = service(backend)
    window_id = listed_window_id(control)

    activated = control.activate("notepad", window_id)
    minimized = control.minimize("notepad", window_id)
    restored = control.restore("notepad", window_id)

    assert activated.status is ToolExecutionStatus.SUCCESS
    assert activated.data["active"] is True
    assert minimized.status is ToolExecutionStatus.SUCCESS
    assert minimized.data["minimized"] is True
    assert restored.status is ToolExecutionStatus.SUCCESS
    assert restored.data["minimized"] is False
    assert all(result.verified for result in (activated, minimized, restored))


def test_window_action_fails_when_native_postcondition_is_not_observed() -> None:
    backend = FakeWindowBackend({101: window()}, ignore_action=True)
    control = service(backend)
    window_id = listed_window_id(control)

    result = control.minimize("notepad", window_id)

    assert result.status is ToolExecutionStatus.FAILED
    assert result.verified is False


def test_window_backend_resource_errors_fail_closed() -> None:
    listing = service(FakeWindowBackend({}, fail_list=True)).list_allowed_windows()
    backend = FakeWindowBackend({101: window()})
    control = service(backend)
    window_id = listed_window_id(control)
    backend.fail_action = True

    mutation = control.activate("notepad", window_id)

    assert listing.status is ToolExecutionStatus.FAILED
    assert mutation.status is ToolExecutionStatus.FAILED
    assert not listing.verified and not mutation.verified


def test_executable_path_policy_does_not_downgrade_to_process_name() -> None:
    allowed = AllowedWindowApplication(
        "editor",
        process_names=frozenset({"editor.exe"}),
        executable_paths=frozenset({r"C:\Trusted\editor.exe"}),
    )

    assert allowed.matches(
        window(
            process_name="editor.exe",
            executable_path=r"C:\Trusted\editor.exe",
        )
    )
    assert not allowed.matches(
        window(
            process_name="editor.exe",
            executable_path=r"C:\Untrusted\editor.exe",
        )
    )
    assert not allowed.matches(window(process_name="editor.exe"))


def test_window_tools_only_register_bounded_operations_with_mutation_risk() -> None:
    executor = ToolExecutor()
    control = service(FakeWindowBackend({}))
    control.register_tools(executor)

    assert set(executor.list_names()) == {
        "activate_allowed_window",
        "list_allowed_windows",
        "minimize_allowed_window",
        "restore_allowed_window",
    }
    listing = executor.get("list_allowed_windows").definition
    assert listing.risk_level is RiskLevel.READ_ONLY
    assert listing.requires_confirmation is False
    for name in (
        "activate_allowed_window",
        "minimize_allowed_window",
        "restore_allowed_window",
    ):
        definition = executor.get(name).definition
        assert definition.risk_level is RiskLevel.MEDIUM
        assert definition.requires_confirmation is True
        assert definition.metadata["opaque_window_ids"] is True
    assert not any("terminate" in name or "close" in name for name in executor.list_names())


def test_allowed_window_application_rejects_missing_identity() -> None:
    try:
        AllowedWindowApplication("empty")
    except ValueError as exc:
        assert "identity" in str(exc)
    else:
        raise AssertionError("Expected empty window identity policy to be rejected.")
