from __future__ import annotations

import os
import shutil
from pathlib import Path
from threading import RLock

from app.platform.windows.models import WindowsApplication


class WindowsApplicationRegistry:
    """Thread-safe registry resolving trusted application IDs and aliases."""

    def __init__(self) -> None:
        self._applications: dict[str, WindowsApplication] = {}
        self._aliases: dict[str, str] = {}
        self._lock = RLock()
        self._revision = 0

    @property
    def revision(self) -> int:
        with self._lock:
            return self._revision

    def register(self, application: WindowsApplication) -> None:
        if not isinstance(application, WindowsApplication):
            raise TypeError("application must be a WindowsApplication.")
        with self._lock:
            collisions = application.all_names.intersection(self._aliases)
            if application.application_id in self._applications or collisions:
                conflict = sorted(collisions)[0] if collisions else application.application_id
                raise ValueError(f"Windows application name '{conflict}' is already registered.")
            self._applications[application.application_id] = application
            for name in application.all_names:
                self._aliases[name] = application.application_id
            self._revision += 1

    def unregister(self, application_id: str) -> WindowsApplication:
        normalized = self._normalize_name(application_id)
        with self._lock:
            resolved_id = self._aliases.get(normalized, normalized)
            try:
                application = self._applications.pop(resolved_id)
            except KeyError as exc:
                raise KeyError(f"Unknown Windows application: {normalized}") from exc
            for name in application.all_names:
                self._aliases.pop(name, None)
            self._revision += 1
            return application

    def resolve(self, name: str) -> WindowsApplication:
        normalized = self._normalize_name(name)
        with self._lock:
            application_id = self._aliases.get(normalized)
            if application_id is None:
                raise KeyError(f"Unknown Windows application: {normalized}")
            return self._applications[application_id]

    def contains(self, name: str) -> bool:
        try:
            normalized = self._normalize_name(name)
        except (TypeError, ValueError):
            return False
        with self._lock:
            return normalized in self._aliases

    def list(self) -> tuple[WindowsApplication, ...]:
        with self._lock:
            return tuple(self._applications.values())

    @staticmethod
    def _normalize_name(name: str) -> str:
        if not isinstance(name, str):
            raise TypeError("Windows application name must be a string.")
        normalized = name.strip().lower()
        if not normalized:
            raise ValueError("Windows application name cannot be empty.")
        return normalized

    @staticmethod
    def resolve_executable(application: WindowsApplication) -> str:
        expanded = os.path.expandvars(application.executable)
        path = Path(expanded)
        if path.is_absolute():
            if str(path).startswith((r"\\", "//")):
                raise ValueError("Network executable paths are not allowed.")
            if path.suffix.lower() != ".exe":
                raise ValueError("Registered executable must be an .exe file.")
            if not path.is_file():
                raise FileNotFoundError(
                    f"Registered executable does not exist: {path}"
                )
            return str(path.resolve())

        if path.name != expanded or path.suffix.lower() != ".exe":
            raise ValueError(
                "Registered executable must be an absolute path or a bare .exe name."
            )
        resolved = shutil.which(expanded)
        if resolved is None:
            raise FileNotFoundError(
                f"Registered executable was not found on PATH: {expanded}"
            )
        return str(Path(resolved).resolve())

    @classmethod
    def with_windows_defaults(cls) -> WindowsApplicationRegistry:
        registry = cls()
        system_root = os.environ.get("SystemRoot", r"C:\Windows")
        definitions = (
            WindowsApplication(
                application_id="notepad",
                display_name="Notepad",
                executable=str(Path(system_root) / "System32" / "notepad.exe"),
                aliases=frozenset({"editor", "text-editor", "not-defteri"}),
                process_names=frozenset({"notepad.exe"}),
                capabilities=frozenset({"text", "files"}),
            ),
            WindowsApplication(
                application_id="file-explorer",
                display_name="File Explorer",
                executable=str(Path(system_root) / "explorer.exe"),
                aliases=frozenset({"explorer", "files", "dosya-gezgini"}),
                process_names=frozenset({"explorer.exe"}),
                capabilities=frozenset({"files", "navigation"}),
            ),
            WindowsApplication(
                application_id="calculator",
                display_name="Calculator",
                executable=str(Path(system_root) / "System32" / "calc.exe"),
                aliases=frozenset({"calc", "hesap-makinesi"}),
                process_names=frozenset({"calculatorapp.exe", "calc.exe"}),
                capabilities=frozenset({"calculation"}),
            ),
        )
        for definition in definitions:
            registry.register(definition)
        return registry
