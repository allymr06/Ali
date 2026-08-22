from __future__ import annotations

import csv
import ctypes
import os
import re
from collections.abc import Sequence
from ctypes import wintypes

from app.platform.windows.models import WindowsProcess


class WindowsProcessInspector:
    """Observe Windows processes without accepting shell expressions."""

    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    STILL_ACTIVE = 259
    TH32CS_SNAPPROCESS = 0x00000002
    INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value

    class _ProcessEntry(ctypes.Structure):
        _fields_ = [
            ("size", wintypes.DWORD),
            ("usage", wintypes.DWORD),
            ("process_id", wintypes.DWORD),
            ("default_heap_id", wintypes.WPARAM),
            ("module_id", wintypes.DWORD),
            ("threads", wintypes.DWORD),
            ("parent_process_id", wintypes.DWORD),
            ("priority_base", wintypes.LONG),
            ("flags", wintypes.DWORD),
            ("executable_name", wintypes.WCHAR * 260),
        ]

    def __init__(self) -> None:
        if os.name != "nt":
            raise OSError("Windows process inspection requires Windows.")
        self._kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        self._kernel32.OpenProcess.argtypes = [
            ctypes.c_ulong,
            ctypes.c_bool,
            ctypes.c_ulong,
        ]
        self._kernel32.OpenProcess.restype = ctypes.c_void_p
        self._kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
        self._kernel32.CloseHandle.restype = ctypes.c_bool
        self._kernel32.GetExitCodeProcess.argtypes = [
            ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_ulong),
        ]
        self._kernel32.GetExitCodeProcess.restype = ctypes.c_bool
        self._kernel32.QueryFullProcessImageNameW.argtypes = [
            ctypes.c_void_p,
            ctypes.c_ulong,
            ctypes.c_wchar_p,
            ctypes.POINTER(ctypes.c_ulong),
        ]
        self._kernel32.QueryFullProcessImageNameW.restype = ctypes.c_bool
        self._kernel32.CreateToolhelp32Snapshot.argtypes = [
            wintypes.DWORD,
            wintypes.DWORD,
        ]
        self._kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
        self._kernel32.Process32FirstW.argtypes = [
            wintypes.HANDLE,
            ctypes.POINTER(self._ProcessEntry),
        ]
        self._kernel32.Process32FirstW.restype = wintypes.BOOL
        self._kernel32.Process32NextW.argtypes = [
            wintypes.HANDLE,
            ctypes.POINTER(self._ProcessEntry),
        ]
        self._kernel32.Process32NextW.restype = wintypes.BOOL

    def get_process(self, pid: int) -> WindowsProcess | None:
        if not isinstance(pid, int) or isinstance(pid, bool) or pid < 1:
            raise ValueError("pid must be a positive integer.")
        handle = self._kernel32.OpenProcess(
            self.PROCESS_QUERY_LIMITED_INFORMATION,
            False,
            pid,
        )
        if not handle:
            return None
        try:
            exit_code = ctypes.c_ulong()
            if not self._kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
                return None
            if exit_code.value != self.STILL_ACTIVE:
                return None

            size = ctypes.c_ulong(32768)
            buffer = ctypes.create_unicode_buffer(size.value)
            executable_path = None
            if self._kernel32.QueryFullProcessImageNameW(
                handle,
                0,
                buffer,
                ctypes.byref(size),
            ):
                executable_path = buffer.value
                name = os.path.basename(executable_path)
            else:
                name = f"pid-{pid}"
            return WindowsProcess(
                pid=pid,
                name=name,
                executable_path=executable_path,
            )
        finally:
            self._kernel32.CloseHandle(handle)

    def list_processes(
        self,
        *,
        name: str | None = None,
        limit: int = 100,
        timeout_seconds: float = 5.0,
    ) -> tuple[WindowsProcess, ...]:
        if not isinstance(limit, int) or isinstance(limit, bool) or limit < 1:
            raise ValueError("limit must be a positive integer.")
        if limit > 1000:
            raise ValueError("limit cannot exceed 1000.")
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be greater than 0.")
        normalized_name = None
        if name is not None:
            if not isinstance(name, str) or not name.strip():
                raise ValueError("name must be a non-empty string when provided.")
            normalized_name = name.strip().lower()

        snapshot = self._kernel32.CreateToolhelp32Snapshot(
            self.TH32CS_SNAPPROCESS,
            0,
        )
        if snapshot == self.INVALID_HANDLE_VALUE:
            raise OSError(ctypes.get_last_error(), "Process snapshot failed.")
        try:
            entry = self._ProcessEntry()
            entry.size = ctypes.sizeof(self._ProcessEntry)
            has_entry = self._kernel32.Process32FirstW(
                snapshot,
                ctypes.byref(entry),
            )
            processes: list[WindowsProcess] = []
            while has_entry:
                process_name = entry.executable_name
                if (
                    process_name
                    and (
                        normalized_name is None
                        or process_name.lower() == normalized_name
                    )
                    and entry.process_id > 0
                ):
                    processes.append(
                        WindowsProcess(
                            pid=int(entry.process_id),
                            name=process_name,
                        )
                    )
                    if len(processes) >= limit:
                        break
                has_entry = self._kernel32.Process32NextW(
                    snapshot,
                    ctypes.byref(entry),
                )
            return tuple(processes)
        finally:
            self._kernel32.CloseHandle(snapshot)

    @staticmethod
    def parse_tasklist_csv(
        lines: Sequence[str],
        *,
        name: str | None = None,
        limit: int = 100,
    ) -> tuple[WindowsProcess, ...]:
        processes: list[WindowsProcess] = []
        for row in csv.reader(lines):
            if len(row) < 5:
                continue
            process_name = row[0].strip()
            if name is not None and process_name.lower() != name.lower():
                continue
            try:
                pid = int(row[1])
                session_number = int(row[3])
            except ValueError:
                continue
            memory_digits = re.sub(r"[^0-9]", "", row[4])
            memory_kb = int(memory_digits) if memory_digits else None
            processes.append(
                WindowsProcess(
                    pid=pid,
                    name=process_name,
                    session_name=row[2].strip() or None,
                    session_number=session_number,
                    memory_kb=memory_kb,
                )
            )
            if len(processes) >= limit:
                break
        return tuple(processes)
