from __future__ import annotations

import ctypes
import os
import platform
import shutil
from dataclasses import dataclass
from app.config.paths import (
    default_state_directory,
    migrate_default_file,
)

from app.core.models import (
    RiskLevel,
    ToolDefinition,
    ToolExecutionStatus,
    ToolResult,
)
from app.platform.windows.applications import WindowsApplicationRegistry
from app.platform.windows.clipboard import WindowsClipboardService
from app.platform.windows.filesystem import BoundedFilesystemService
from app.platform.windows.launcher import WindowsApplicationLauncher
from app.platform.windows.processes import WindowsProcessInspector
from app.platform.windows.root_grants import (
    FilesystemRootGrant,
    FilesystemRootGrantStore,
)
from app.platform.windows.window_control import (
    AllowedWindowApplication,
    WindowsWindowControlService,
)
from app.tools.executor import ToolExecutor


class _MemoryStatus(ctypes.Structure):
    _fields_ = [
        ("length", ctypes.c_ulong),
        ("memory_load", ctypes.c_ulong),
        ("total_physical", ctypes.c_ulonglong),
        ("available_physical", ctypes.c_ulonglong),
        ("total_page_file", ctypes.c_ulonglong),
        ("available_page_file", ctypes.c_ulonglong),
        ("total_virtual", ctypes.c_ulonglong),
        ("available_virtual", ctypes.c_ulonglong),
        ("available_extended_virtual", ctypes.c_ulonglong),
    ]


@dataclass(slots=True)
class WindowsIntegrationService:
    """Composition boundary for native Windows observations and actions."""

    applications: WindowsApplicationRegistry
    processes: WindowsProcessInspector
    launcher: WindowsApplicationLauncher
    filesystem: BoundedFilesystemService | None = None
    clipboard: WindowsClipboardService | None = None
    window_control: WindowsWindowControlService | None = None
    root_grants: FilesystemRootGrantStore | None = None

    @classmethod
    def create_default(
        cls,
        *,
        verification_timeout_seconds: float = 3.0,
    ) -> WindowsIntegrationService:
        if os.name != "nt":
            raise OSError("Windows integrations require Windows.")
        applications = WindowsApplicationRegistry.with_windows_defaults()

        snapshot = default_state_directory() / "windows_app_registry.local.json"
        migrate_default_file(snapshot, "windows_app_registry.local.json")
        applications.load_snapshot(snapshot)

        processes = WindowsProcessInspector()
        launcher = WindowsApplicationLauncher(
            applications,
            processes,
            verification_timeout_seconds=verification_timeout_seconds,
        )
        root_grants = FilesystemRootGrantStore.create_default()
        filesystem = BoundedFilesystemService()
        for grant in root_grants.list():
            try:
                filesystem.allow_root(grant.root_id, grant.path)
            except ValueError:
                continue

        clipboard = WindowsClipboardService.create_default()
        allowed_windows = []
        for application in applications.list():
            try:
                executable = applications.resolve_executable(application)
            except ValueError:
                executable_paths = frozenset()
            else:
                executable_paths = frozenset({executable})
            allowed_windows.append(
                AllowedWindowApplication(
                    application_id=application.application_id,
                    process_names=application.expected_process_names,
                    executable_paths=executable_paths,
                )
            )
        window_control = WindowsWindowControlService.create_default(
            allowed_windows
        )
        return cls(
            applications,
            processes,
            launcher,
            filesystem=filesystem,
            clipboard=clipboard,
            window_control=window_control,
            root_grants=root_grants,
        )

    def filesystem_root_grants(self) -> tuple[FilesystemRootGrant, ...]:
        if self.root_grants is None:
            return ()
        return self.root_grants.list()

    def grant_filesystem_root(self, path: str) -> FilesystemRootGrant:
        if self.root_grants is None or self.filesystem is None:
            raise RuntimeError("Bounded filesystem access is unavailable.")
        existing_ids = {
            item.root_id for item in self.root_grants.list()
        }
        grant = self.root_grants.grant(path)
        active = self.filesystem.list_allowed_roots().data or {}
        active_ids = {
            item.get("root_id")
            for item in active.get("roots", [])
            if isinstance(item, dict)
        }
        if grant.root_id in active_ids:
            return grant
        try:
            self.filesystem.allow_root(grant.root_id, grant.path)
        except Exception:
            if grant.root_id not in existing_ids:
                self.root_grants.revoke(grant.root_id)
            raise
        return grant

    def revoke_filesystem_root(self, root_id: str) -> bool:
        if self.root_grants is None or self.filesystem is None:
            return False
        if not self.root_grants.revoke(root_id):
            return False
        self.filesystem.revoke_root(root_id)
        return True

    def list_applications(self) -> list[dict[str, object]]:
        return [
            {
                "application_id": application.application_id,
                "display_name": application.display_name,
                "aliases": sorted(application.aliases),
                "capabilities": sorted(application.capabilities),
                "source": application.source,
            }
            for application in self.applications.list()
        ]

    def list_processes(
        self,
        name: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, object]]:
        return [
            process.to_dict()
            for process in self.processes.list_processes(name=name, limit=limit)
        ]

    def launch_application(self, application: str) -> ToolResult:
        outcome = self.launcher.launch(application)
        data = {
            "application_id": outcome.application_id,
            "pid": outcome.pid,
            "process": (
                outcome.process.to_dict() if outcome.process is not None else None
            ),
        }
        if not outcome.verified:
            return ToolResult(
                status=ToolExecutionStatus.FAILED,
                tool_name="launch_windows_application",
                message=outcome.message,
                data=data,
                error=outcome.error,
                verified=False,
            )
        return ToolResult(
            status=ToolExecutionStatus.SUCCESS,
            tool_name="launch_windows_application",
            message=outcome.message,
            data=data,
            verified=True,
        )

    @staticmethod
    def system_info() -> dict[str, object]:
        if os.name != "nt":
            raise OSError("Windows system information requires Windows.")
        system_drive = os.environ.get("SystemDrive", "C:") + os.sep
        disk = shutil.disk_usage(system_drive)
        memory = _MemoryStatus()
        memory.length = ctypes.sizeof(_MemoryStatus)
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        if not kernel32.GlobalMemoryStatusEx(ctypes.byref(memory)):
            raise OSError(ctypes.get_last_error(), "GlobalMemoryStatusEx failed.")
        gibibyte = 1024 ** 3

        return {
            "system": platform.system(),
            "release": platform.release(),
            "version": platform.version(),
            "machine": platform.machine(),
            "processor": platform.processor(),
            "hostname": platform.node(),
            "logical_cpu_count": os.cpu_count(),
            "memory_total_bytes": memory.total_physical,
            "memory_available_bytes": memory.available_physical,
            "memory_total_gib": round(
                memory.total_physical / gibibyte,
                2,
            ),
            "memory_available_gib": round(
                memory.available_physical / gibibyte,
                2,
            ),
            "system_drive": system_drive,
            "disk_total_bytes": disk.total,
            "disk_free_bytes": disk.free,
            "disk_total_gib": round(
                disk.total / gibibyte,
                2,
            ),
            "disk_free_gib": round(
                disk.free / gibibyte,
                2,
            ),
        }

    def register_tools(self, executor: ToolExecutor) -> None:
        def list_windows_applications() -> ToolResult:
            return ToolResult(
                status=ToolExecutionStatus.SUCCESS,
                tool_name="list_windows_applications",
                data=self.list_applications(),
                message="Registered Windows applications observed.",
                verified=True,
            )

        def list_windows_processes(
            name: str | None = None,
            limit: int = 100,
        ) -> ToolResult:
            return ToolResult(
                status=ToolExecutionStatus.SUCCESS,
                tool_name="list_windows_processes",
                data=self.list_processes(name=name, limit=limit),
                message="Windows processes observed.",
                verified=True,
            )

        def get_windows_system_info() -> ToolResult:
            return ToolResult(
                status=ToolExecutionStatus.SUCCESS,
                tool_name="get_windows_system_info",
                data=self.system_info(),
                message="Windows system information observed.",
                verified=True,
            )

        def launch_windows_application(application: str) -> ToolResult:
            return self.launch_application(application)

        executor.register(
            ToolDefinition(
                name="list_windows_applications",
                description="List Windows applications approved for JARVIS launch.",
                version="1.0.0",
                capabilities=frozenset({"windows", "applications", "observe"}),
                tags=frozenset({"windows", "read-only"}),
                metadata={"verification_strategy": "registry_snapshot"},
            ),
            list_windows_applications,
            source="platform:windows",
        )
        if self.filesystem is not None:
            self.filesystem.register_tools(executor)
        if self.clipboard is not None:
            self.clipboard.register_tools(executor)
        if self.window_control is not None:
            self.window_control.register_tools(executor)
        executor.register(
            ToolDefinition(
                name="list_windows_processes",
                description="List observed Windows processes without shell input.",
                version="1.0.0",
                capabilities=frozenset({"windows", "processes", "observe"}),
                tags=frozenset({"windows", "read-only"}),
                timeout_seconds=10.0,
                metadata={
                    "verification_strategy": "native_observation",
                    "sensitive_output": True,
                },
            ),
            list_windows_processes,
            source="platform:windows",
        )
        executor.register(
            ToolDefinition(
                name="get_windows_system_info",
                description="Read verified local Windows system information.",
                version="1.0.0",
                capabilities=frozenset({"windows", "system", "observe"}),
                tags=frozenset({"windows", "read-only"}),
                metadata={
                    "verification_strategy": "native_observation",
                    "sensitive_output": True,
                },
            ),
            get_windows_system_info,
            source="platform:windows",
        )
        executor.register(
            ToolDefinition(
                name="launch_windows_application",
                description="Launch an approved Windows application by registry ID.",
                risk_level=RiskLevel.MEDIUM,
                requires_confirmation=True,
                version="1.0.0",
                capabilities=frozenset({"windows", "applications", "launch"}),
                tags=frozenset({"windows", "action"}),
                max_concurrency=1,
                metadata={"verification_strategy": "new_process_identity"},
            ),
            launch_windows_application,
            source="platform:windows",
        )
