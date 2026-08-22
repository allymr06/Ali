from __future__ import annotations

import ctypes
import os
import secrets
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol, Sequence

from app.core.models import (
    RiskLevel,
    ToolDefinition,
    ToolExecutionStatus,
    ToolResult,
)
from app.tools.executor import ToolExecutor


@dataclass(frozen=True, slots=True)
class NativeWindowState:
    """Backend-only window identity and observable state."""

    handle: int
    process_id: int
    process_name: str
    executable_path: str | None
    title: str
    visible: bool
    minimized: bool
    active: bool

    def __post_init__(self) -> None:
        if self.handle < 1 or self.process_id < 1:
            raise ValueError("Window handle and process_id must be positive.")
        if not self.process_name.strip():
            raise ValueError("Window process_name cannot be empty.")


class WindowControlBackend(Protocol):
    """Injectable native window boundary used by the policy service."""

    def list_windows(self) -> Sequence[NativeWindowState]: ...

    def get_window(self, handle: int) -> NativeWindowState | None: ...

    def activate(self, handle: int) -> bool: ...

    def minimize(self, handle: int) -> bool: ...

    def restore(self, handle: int) -> bool: ...


@dataclass(frozen=True, slots=True)
class AllowedWindowApplication:
    """Identity policy for windows JARVIS may expose and manipulate."""

    application_id: str
    process_names: frozenset[str] = field(default_factory=frozenset)
    executable_paths: frozenset[str] = field(default_factory=frozenset)

    def __post_init__(self) -> None:
        application_id = self.application_id.strip().lower()
        if not application_id or not all(
            character.isalnum() or character in "-_."
            for character in application_id
        ):
            raise ValueError("application_id contains invalid characters.")
        process_names = frozenset(
            Path(value.strip()).name.casefold()
            for value in self.process_names
            if isinstance(value, str) and value.strip()
        )
        executable_paths = frozenset(
            self._normalize_path(value)
            for value in self.executable_paths
            if isinstance(value, str) and value.strip()
        )
        if not process_names and not executable_paths:
            raise ValueError("At least one executable identity is required.")
        object.__setattr__(self, "application_id", application_id)
        object.__setattr__(self, "process_names", process_names)
        object.__setattr__(self, "executable_paths", executable_paths)

    @staticmethod
    def _normalize_path(value: str) -> str:
        return os.path.normcase(os.path.abspath(os.path.expandvars(value))).casefold()

    def matches(self, window: NativeWindowState) -> bool:
        if self.executable_paths:
            if not window.executable_path:
                return False
            return self._normalize_path(window.executable_path) in self.executable_paths
        return Path(window.process_name).name.casefold() in self.process_names


class NativeWindowsWindowBackend:
    """Minimal Win32 top-level window enumeration and state transitions."""

    _PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    _SW_MINIMIZE = 6
    _SW_RESTORE = 9

    def __init__(self) -> None:
        if os.name != "nt":
            raise OSError("Native window control requires Windows.")
        self._user32 = ctypes.WinDLL("user32", use_last_error=True)
        self._kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        self._configure_signatures()

    def _configure_signatures(self) -> None:
        from ctypes import wintypes

        self._user32.IsWindow.argtypes = [wintypes.HWND]
        self._user32.IsWindow.restype = wintypes.BOOL
        self._user32.IsWindowVisible.argtypes = [wintypes.HWND]
        self._user32.IsWindowVisible.restype = wintypes.BOOL
        self._user32.IsIconic.argtypes = [wintypes.HWND]
        self._user32.IsIconic.restype = wintypes.BOOL
        self._user32.GetForegroundWindow.argtypes = []
        self._user32.GetForegroundWindow.restype = wintypes.HWND
        self._user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
        self._user32.GetWindowTextLengthW.restype = ctypes.c_int
        self._user32.GetWindowTextW.argtypes = [
            wintypes.HWND,
            wintypes.LPWSTR,
            ctypes.c_int,
        ]
        self._user32.GetWindowTextW.restype = ctypes.c_int
        self._user32.GetWindowThreadProcessId.argtypes = [
            wintypes.HWND,
            ctypes.POINTER(wintypes.DWORD),
        ]
        self._user32.GetWindowThreadProcessId.restype = wintypes.DWORD
        self._user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
        self._user32.ShowWindow.restype = wintypes.BOOL
        self._user32.SetForegroundWindow.argtypes = [wintypes.HWND]
        self._user32.SetForegroundWindow.restype = wintypes.BOOL
        self._kernel32.OpenProcess.argtypes = [
            wintypes.DWORD,
            wintypes.BOOL,
            wintypes.DWORD,
        ]
        self._kernel32.OpenProcess.restype = wintypes.HANDLE
        self._kernel32.QueryFullProcessImageNameW.argtypes = [
            wintypes.HANDLE,
            wintypes.DWORD,
            wintypes.LPWSTR,
            ctypes.POINTER(wintypes.DWORD),
        ]
        self._kernel32.QueryFullProcessImageNameW.restype = wintypes.BOOL
        self._kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        self._kernel32.CloseHandle.restype = wintypes.BOOL

    def _process_path(self, process_id: int) -> str | None:
        from ctypes import wintypes

        process = self._kernel32.OpenProcess(
            self._PROCESS_QUERY_LIMITED_INFORMATION,
            False,
            process_id,
        )
        if not process:
            return None
        try:
            buffer = ctypes.create_unicode_buffer(32_768)
            length = wintypes.DWORD(len(buffer))
            if not self._kernel32.QueryFullProcessImageNameW(
                process,
                0,
                buffer,
                ctypes.byref(length),
            ):
                return None
            return buffer.value
        finally:
            self._kernel32.CloseHandle(process)

    def _observe(self, handle: int) -> NativeWindowState | None:
        from ctypes import wintypes

        if not self._user32.IsWindow(handle):
            return None
        process_id = wintypes.DWORD()
        self._user32.GetWindowThreadProcessId(handle, ctypes.byref(process_id))
        if process_id.value < 1:
            return None
        length = min(max(self._user32.GetWindowTextLengthW(handle), 0), 8_192)
        title_buffer = ctypes.create_unicode_buffer(length + 1)
        self._user32.GetWindowTextW(handle, title_buffer, len(title_buffer))
        executable_path = self._process_path(process_id.value)
        if not executable_path:
            return None
        return NativeWindowState(
            handle=int(handle),
            process_id=process_id.value,
            process_name=Path(executable_path).name,
            executable_path=executable_path,
            title=title_buffer.value,
            visible=bool(self._user32.IsWindowVisible(handle)),
            minimized=bool(self._user32.IsIconic(handle)),
            active=int(self._user32.GetForegroundWindow() or 0) == int(handle),
        )

    def list_windows(self) -> Sequence[NativeWindowState]:
        from ctypes import wintypes

        observed: list[NativeWindowState] = []
        callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

        @callback_type
        def visit(handle, _parameter):
            window = self._observe(int(handle))
            if window is not None:
                observed.append(window)
            return True

        if not self._user32.EnumWindows(visit, 0):
            raise OSError("EnumWindows failed.")
        return tuple(observed)

    def get_window(self, handle: int) -> NativeWindowState | None:
        return self._observe(handle)

    def activate(self, handle: int) -> bool:
        if not self._user32.IsWindow(handle):
            return False
        if self._user32.IsIconic(handle):
            self._user32.ShowWindow(handle, self._SW_RESTORE)
        return bool(self._user32.SetForegroundWindow(handle))

    def minimize(self, handle: int) -> bool:
        if not self._user32.IsWindow(handle):
            return False
        self._user32.ShowWindow(handle, self._SW_MINIMIZE)
        return True

    def restore(self, handle: int) -> bool:
        if not self._user32.IsWindow(handle):
            return False
        self._user32.ShowWindow(handle, self._SW_RESTORE)
        return True


@dataclass(frozen=True, slots=True)
class _WindowRecord:
    application_id: str
    handle: int
    process_id: int


@dataclass(slots=True)
class WindowsWindowControlService:
    """Allowlist and opaque-ID boundary for limited window state changes."""

    backend: WindowControlBackend
    allowed_applications: tuple[AllowedWindowApplication, ...]
    _records: dict[str, _WindowRecord] = field(default_factory=dict, init=False)
    _ids_by_identity: dict[tuple[str, int, int], str] = field(
        default_factory=dict,
        init=False,
    )

    def __post_init__(self) -> None:
        identifiers = [item.application_id for item in self.allowed_applications]
        if len(identifiers) != len(set(identifiers)):
            raise ValueError("Allowed application IDs must be unique.")

    @classmethod
    def create_default(
        cls,
        allowed_applications: Sequence[AllowedWindowApplication],
    ) -> WindowsWindowControlService:
        return cls(NativeWindowsWindowBackend(), tuple(allowed_applications))

    @staticmethod
    def _failure(
        tool_name: str,
        message: str,
        *,
        blocked: bool = False,
    ) -> ToolResult:
        return ToolResult(
            status=(
                ToolExecutionStatus.BLOCKED
                if blocked
                else ToolExecutionStatus.FAILED
            ),
            tool_name=tool_name,
            message=message,
            error="Window identity, resource access, or verification failed.",
            verified=False,
        )

    def _application(self, application_id: str) -> AllowedWindowApplication | None:
        if not isinstance(application_id, str):
            return None
        normalized = application_id.strip().lower()
        return next(
            (
                application
                for application in self.allowed_applications
                if application.application_id == normalized
            ),
            None,
        )

    def list_allowed_windows(self) -> ToolResult:
        tool_name = "list_allowed_windows"
        try:
            observed = self.backend.list_windows()
        except Exception:
            return self._failure(tool_name, "Allowed windows could not be observed.")

        records: dict[str, _WindowRecord] = {}
        public_windows: list[dict[str, object]] = []
        for window in observed:
            if not window.visible or not window.title.strip():
                continue
            application = next(
                (
                    item
                    for item in self.allowed_applications
                    if item.matches(window)
                ),
                None,
            )
            if application is None:
                continue
            identity = (
                application.application_id,
                window.process_id,
                window.handle,
            )
            window_id = self._ids_by_identity.get(identity)
            if window_id is None:
                window_id = secrets.token_urlsafe(18)
                self._ids_by_identity[identity] = window_id
            records[window_id] = _WindowRecord(
                application.application_id,
                window.handle,
                window.process_id,
            )
            public_windows.append(
                {
                    "application_id": application.application_id,
                    "window_id": window_id,
                    "title": window.title,
                    "minimized": window.minimized,
                    "active": window.active,
                }
            )
        self._records = records
        return ToolResult(
            status=ToolExecutionStatus.SUCCESS,
            tool_name=tool_name,
            message="Allowed Windows application windows observed.",
            data=public_windows,
            verified=True,
        )

    @staticmethod
    def _postcondition(operation: str, window: NativeWindowState) -> bool:
        if operation == "activate":
            return window.active and not window.minimized
        if operation == "minimize":
            return window.minimized
        return not window.minimized

    def _change_state(
        self,
        operation: str,
        application_id: str,
        window_id: str,
    ) -> ToolResult:
        tool_name = f"{operation}_allowed_window"
        application = self._application(application_id)
        record = self._records.get(window_id) if isinstance(window_id, str) else None
        if (
            application is None
            or record is None
            or record.application_id != application.application_id
        ):
            return self._failure(
                tool_name,
                "Window action blocked because its allowlisted identity is unknown.",
                blocked=True,
            )
        try:
            before = self.backend.get_window(record.handle)
        except Exception:
            return self._failure(tool_name, "Window identity could not be revalidated.")
        if (
            before is None
            or before.process_id != record.process_id
            or not application.matches(before)
        ):
            self._records.pop(window_id, None)
            return self._failure(
                tool_name,
                "Window action blocked because its identity changed.",
                blocked=True,
            )
        try:
            changed = getattr(self.backend, operation)(record.handle)
            after = self.backend.get_window(record.handle)
        except Exception:
            return self._failure(tool_name, "Window state could not be changed safely.")
        if (
            not changed
            or after is None
            or after.process_id != record.process_id
            or not application.matches(after)
            or not self._postcondition(operation, after)
        ):
            return self._failure(tool_name, "Window state change could not be verified.")
        return ToolResult(
            status=ToolExecutionStatus.SUCCESS,
            tool_name=tool_name,
            message=f"Window {operation} action completed and verified.",
            data={
                "application_id": application.application_id,
                "window_id": window_id,
                "active": after.active,
                "minimized": after.minimized,
            },
            verified=True,
        )

    def activate(self, application_id: str, window_id: str) -> ToolResult:
        return self._change_state("activate", application_id, window_id)

    def minimize(self, application_id: str, window_id: str) -> ToolResult:
        return self._change_state("minimize", application_id, window_id)

    def restore(self, application_id: str, window_id: str) -> ToolResult:
        return self._change_state("restore", application_id, window_id)

    def register_tools(self, executor: ToolExecutor) -> None:
        executor.register(
            ToolDefinition(
                name="list_allowed_windows",
                description="List visible windows belonging to approved applications.",
                risk_level=RiskLevel.READ_ONLY,
                capabilities=frozenset({"windows", "window-management", "observe"}),
                tags=frozenset({"windows", "read-only", "sensitive"}),
                metadata={
                    "verification_strategy": "allowlisted_native_observation",
                    "sensitive_output": True,
                },
            ),
            self.list_allowed_windows,
            source="platform:windows:window-control",
        )
        for operation, handler in (
            ("activate", self.activate),
            ("minimize", self.minimize),
            ("restore", self.restore),
        ):
            executor.register(
                ToolDefinition(
                    name=f"{operation}_allowed_window",
                    description=(
                        f"{operation.title()} an allowlisted window after explicit approval."
                    ),
                    risk_level=RiskLevel.MEDIUM,
                    requires_confirmation=True,
                    capabilities=frozenset(
                        {"windows", "window-management", operation}
                    ),
                    tags=frozenset({"windows", "window-management", "action"}),
                    max_concurrency=1,
                    metadata={
                        "verification_strategy": "native_state_postcondition",
                        "opaque_window_ids": True,
                    },
                ),
                handler,
                source="platform:windows:window-control",
            )
