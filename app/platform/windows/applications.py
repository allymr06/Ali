from __future__ import annotations

import ctypes
import hashlib
import json
import os
import re
import shutil
import unicodedata
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
    def _discovery_slug(
        value: str,
    ) -> str:
        decomposed = unicodedata.normalize(
            "NFKD",
            value.casefold(),
        )

        ascii_value = "".join(
            character
            for character in decomposed
            if ord(character) < 128
        )

        slug = re.sub(
            r"[^a-z0-9]+",
            "-",
            ascii_value,
        ).strip("-")

        return slug or "app"

    @staticmethod
    def _windows_arguments(
        raw: str,
    ) -> tuple[str, ...]:
        if not isinstance(raw, str):
            raise TypeError(
                "Shortcut arguments must be a string."
            )

        raw = raw.strip()

        if not raw:
            return ()

        if os.name != "nt":
            raise OSError(
                "Windows argument parsing "
                "requires Windows."
            )

        shell32 = ctypes.WinDLL(
            "shell32",
            use_last_error=True,
        )

        kernel32 = ctypes.WinDLL(
            "kernel32",
            use_last_error=True,
        )

        shell32.CommandLineToArgvW.argtypes = [
            ctypes.c_wchar_p,
            ctypes.POINTER(
                ctypes.c_int
            ),
        ]

        shell32.CommandLineToArgvW.restype = (
            ctypes.POINTER(
                ctypes.c_wchar_p
            )
        )

        kernel32.LocalFree.argtypes = [
            ctypes.c_void_p,
        ]

        kernel32.LocalFree.restype = (
            ctypes.c_void_p
        )

        argc = ctypes.c_int()

        command_line = (
            "jarvis-shortcut.exe "
            + raw
        )

        argv = (
            shell32.CommandLineToArgvW(
                command_line,
                ctypes.byref(
                    argc
                ),
            )
        )

        if not argv:
            raise OSError(
                ctypes.get_last_error(),
                "CommandLineToArgvW failed.",
            )

        try:
            return tuple(
                argv[index]
                for index
                in range(
                    1,
                    argc.value,
                )
            )

        finally:
            kernel32.LocalFree(
                ctypes.cast(
                    argv,
                    ctypes.c_void_p,
                )
            )

    def load_snapshot(
        self,
        path: str | Path,
    ) -> int:
        """
        Load machine-local applications discovered from
        verified Windows shortcuts.

        Stale, malformed, non-executable and network
        entries are ignored.
        """
        snapshot = Path(
            path
        )

        if not snapshot.is_file():
            return 0

        payload = json.loads(
            snapshot.read_text(
                encoding="utf-8-sig",
            )
        )

        if not isinstance(
            payload,
            list,
        ):
            raise ValueError(
                "Windows application snapshot "
                "must contain a JSON array."
            )

        loaded = 0

        for record in payload:
            if not isinstance(
                record,
                dict,
            ):
                continue

            try:
                name = str(
                    record.get(
                        "name",
                        "",
                    )
                ).strip()

                executable = os.path.expandvars(
                    str(
                        record.get(
                            "executable",
                            "",
                        )
                    ).strip()
                )

                shortcut = str(
                    record.get(
                        "shortcut",
                        "",
                    )
                ).strip()

                if (
                    not name
                    or not executable
                ):
                    continue

                executable_path = Path(
                    executable
                )

                if (
                    not executable_path.is_absolute()
                    or str(
                        executable_path
                    ).startswith(
                        (
                            r"\\",
                            "//",
                        )
                    )
                    or executable_path.suffix.casefold()
                    != ".exe"
                    or not executable_path.is_file()
                ):
                    continue

                explicit_arguments = (
                    record.get(
                        "arguments"
                    )
                )

                if explicit_arguments is not None:
                    if (
                        not isinstance(
                            explicit_arguments,
                            list,
                        )
                        or not all(
                            isinstance(
                                argument,
                                str,
                            )
                            for argument
                            in explicit_arguments
                        )
                    ):
                        continue

                    arguments = tuple(
                        explicit_arguments
                    )

                else:
                    arguments = (
                        self._windows_arguments(
                            str(
                                record.get(
                                    "arguments_raw",
                                    "",
                                )
                            )
                        )
                    )

                resolved = str(
                    executable_path.resolve()
                )

                fingerprint = "\0".join(
                    (
                        name,
                        resolved,
                        "\0".join(
                            arguments
                        ),
                        shortcut,
                    )
                )

                digest = hashlib.sha256(
                    fingerprint.encode(
                        "utf-8"
                    )
                ).hexdigest()[:10]

                slug = (
                    self._discovery_slug(
                        name
                    )
                )

                application_id = (
                    f"{slug[:48]}-{digest}"
                )

                aliases = set()

                candidates = (
                    name.casefold(),
                    slug,
                )

                for candidate in candidates:
                    candidate = (
                        candidate.strip()
                    )

                    if (
                        candidate
                        and candidate
                        != application_id
                        and not self.contains(
                            candidate
                        )
                    ):
                        aliases.add(
                            candidate
                        )

                application = (
                    WindowsApplication(
                        application_id=(
                            application_id
                        ),
                        display_name=name,
                        executable=resolved,
                        aliases=frozenset(
                            aliases
                        ),
                        arguments=arguments,
                        process_names=frozenset(
                            {
                                executable_path
                                .name
                                .casefold()
                            }
                        ),
                        capabilities=frozenset(
                            {
                                "applications",
                                "launch",
                                "discovered",
                                "shortcut",
                            }
                        ),
                        source=(
                            "windows:shortcut"
                        ),
                    )
                )

                self.register(
                    application
                )

            except (
                OSError,
                TypeError,
                ValueError,
            ):
                continue

            loaded += 1

        return loaded

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
