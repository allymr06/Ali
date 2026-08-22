from __future__ import annotations

import ctypes
import os
from dataclasses import dataclass
from typing import Protocol

from app.core.models import (
    RiskLevel,
    ToolDefinition,
    ToolExecutionStatus,
    ToolResult,
)
from app.tools.executor import ToolExecutor


class ClipboardBackend(Protocol):
    """Small injectable boundary around the native clipboard resource."""

    def read_text(self) -> str: ...

    def write_text(self, text: str) -> None: ...

    def clear(self) -> None: ...


class ClipboardAccessError(RuntimeError):
    """Raised when the Windows clipboard cannot be accessed safely."""


class NativeWindowsClipboardBackend:
    """CF_UNICODETEXT clipboard access without a shell or hidden Tk window."""

    _CF_UNICODETEXT = 13
    _GMEM_MOVEABLE = 0x0002

    def __init__(self) -> None:
        if os.name != "nt":
            raise OSError("Native clipboard access requires Windows.")
        self._user32 = ctypes.WinDLL("user32", use_last_error=True)
        self._kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        self._configure_signatures()

    def _configure_signatures(self) -> None:
        from ctypes import wintypes

        self._user32.OpenClipboard.argtypes = [wintypes.HWND]
        self._user32.OpenClipboard.restype = wintypes.BOOL
        self._user32.CloseClipboard.argtypes = []
        self._user32.CloseClipboard.restype = wintypes.BOOL
        self._user32.EmptyClipboard.argtypes = []
        self._user32.EmptyClipboard.restype = wintypes.BOOL
        self._user32.IsClipboardFormatAvailable.argtypes = [wintypes.UINT]
        self._user32.IsClipboardFormatAvailable.restype = wintypes.BOOL
        self._user32.GetClipboardData.argtypes = [wintypes.UINT]
        self._user32.GetClipboardData.restype = wintypes.HANDLE
        self._user32.SetClipboardData.argtypes = [wintypes.UINT, wintypes.HANDLE]
        self._user32.SetClipboardData.restype = wintypes.HANDLE
        self._kernel32.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
        self._kernel32.GlobalAlloc.restype = wintypes.HGLOBAL
        self._kernel32.GlobalFree.argtypes = [wintypes.HGLOBAL]
        self._kernel32.GlobalFree.restype = wintypes.HGLOBAL
        self._kernel32.GlobalLock.argtypes = [wintypes.HGLOBAL]
        self._kernel32.GlobalLock.restype = ctypes.c_void_p
        self._kernel32.GlobalUnlock.argtypes = [wintypes.HGLOBAL]
        self._kernel32.GlobalUnlock.restype = wintypes.BOOL

    @staticmethod
    def _raise(operation: str) -> None:
        code = ctypes.get_last_error()
        raise ClipboardAccessError(f"{operation} failed with Windows error {code}.")

    def _open(self) -> None:
        if not self._user32.OpenClipboard(None):
            self._raise("OpenClipboard")

    def read_text(self) -> str:
        if not self._user32.IsClipboardFormatAvailable(self._CF_UNICODETEXT):
            return ""
        self._open()
        try:
            handle = self._user32.GetClipboardData(self._CF_UNICODETEXT)
            if not handle:
                self._raise("GetClipboardData")
            pointer = self._kernel32.GlobalLock(handle)
            if not pointer:
                self._raise("GlobalLock")
            try:
                return ctypes.wstring_at(pointer)
            finally:
                self._kernel32.GlobalUnlock(handle)
        finally:
            self._user32.CloseClipboard()

    def write_text(self, text: str) -> None:
        encoded = (text + "\0").encode("utf-16-le")
        handle = self._kernel32.GlobalAlloc(self._GMEM_MOVEABLE, len(encoded))
        if not handle:
            self._raise("GlobalAlloc")

        ownership_transferred = False
        try:
            pointer = self._kernel32.GlobalLock(handle)
            if not pointer:
                self._raise("GlobalLock")
            try:
                ctypes.memmove(pointer, encoded, len(encoded))
            finally:
                self._kernel32.GlobalUnlock(handle)

            self._open()
            try:
                if not self._user32.EmptyClipboard():
                    self._raise("EmptyClipboard")
                if not self._user32.SetClipboardData(self._CF_UNICODETEXT, handle):
                    self._raise("SetClipboardData")
                ownership_transferred = True
            finally:
                self._user32.CloseClipboard()
        finally:
            if not ownership_transferred:
                self._kernel32.GlobalFree(handle)

    def clear(self) -> None:
        self._open()
        try:
            if not self._user32.EmptyClipboard():
                self._raise("EmptyClipboard")
        finally:
            self._user32.CloseClipboard()


@dataclass(slots=True)
class WindowsClipboardService:
    """Bounded clipboard tools with explicit mutation and read-back checks."""

    backend: ClipboardBackend
    max_text_characters: int = 100_000

    def __post_init__(self) -> None:
        if self.max_text_characters < 1:
            raise ValueError("max_text_characters must be positive.")

    @classmethod
    def create_default(
        cls,
        *,
        max_text_characters: int = 100_000,
    ) -> WindowsClipboardService:
        return cls(
            NativeWindowsClipboardBackend(),
            max_text_characters=max_text_characters,
        )

    @staticmethod
    def _failure(tool_name: str, message: str) -> ToolResult:
        return ToolResult(
            status=ToolExecutionStatus.FAILED,
            tool_name=tool_name,
            message=message,
            error="Clipboard resource unavailable or verification failed.",
            verified=False,
        )

    def read(self) -> ToolResult:
        tool_name = "read_windows_clipboard"
        try:
            text = self.backend.read_text()
        except Exception:
            return self._failure(tool_name, "Clipboard text could not be read.")
        if not isinstance(text, str):
            return self._failure(tool_name, "Clipboard returned an invalid value.")
        if len(text) > self.max_text_characters:
            return self._failure(
                tool_name,
                "Clipboard text exceeds the configured safe size limit.",
            )
        return ToolResult(
            status=ToolExecutionStatus.SUCCESS,
            tool_name=tool_name,
            message="Clipboard text read.",
            data={"text": text, "character_count": len(text)},
            verified=True,
        )

    def write(self, text: str) -> ToolResult:
        tool_name = "write_windows_clipboard"
        if not isinstance(text, str):
            return self._failure(tool_name, "Clipboard text must be a string.")
        if "\0" in text:
            return self._failure(tool_name, "Clipboard text contains an invalid character.")
        if len(text) > self.max_text_characters:
            return self._failure(
                tool_name,
                "Clipboard text exceeds the configured safe size limit.",
            )
        try:
            self.backend.write_text(text)
            observed = self.backend.read_text()
        except Exception:
            return self._failure(tool_name, "Clipboard text could not be written safely.")
        if observed != text:
            return self._failure(tool_name, "Clipboard write could not be verified.")
        return ToolResult(
            status=ToolExecutionStatus.SUCCESS,
            tool_name=tool_name,
            message="Clipboard text written and verified.",
            data={"character_count": len(text)},
            verified=True,
        )

    def clear(self) -> ToolResult:
        tool_name = "clear_windows_clipboard"
        try:
            self.backend.clear()
            observed = self.backend.read_text()
        except Exception:
            return self._failure(tool_name, "Clipboard could not be cleared safely.")
        if observed:
            return self._failure(tool_name, "Clipboard clear could not be verified.")
        return ToolResult(
            status=ToolExecutionStatus.SUCCESS,
            tool_name=tool_name,
            message="Clipboard cleared and verified.",
            data={"character_count": 0},
            verified=True,
        )

    def register_tools(self, executor: ToolExecutor) -> None:
        executor.register(
            ToolDefinition(
                name="read_windows_clipboard",
                description="Read bounded text from the Windows clipboard.",
                risk_level=RiskLevel.READ_ONLY,
                capabilities=frozenset({"windows", "clipboard", "read"}),
                tags=frozenset({"windows", "clipboard", "read-only", "sensitive"}),
                metadata={
                    "verification_strategy": "native_read",
                    "sensitive_output": True,
                    "max_text_characters": self.max_text_characters,
                },
            ),
            self.read,
            source="platform:windows:clipboard",
        )
        executor.register(
            ToolDefinition(
                name="write_windows_clipboard",
                description="Replace Windows clipboard text after explicit approval.",
                risk_level=RiskLevel.MEDIUM,
                requires_confirmation=True,
                capabilities=frozenset({"windows", "clipboard", "write"}),
                tags=frozenset({"windows", "clipboard", "action", "sensitive-input"}),
                max_concurrency=1,
                metadata={
                    "verification_strategy": "exact_readback",
                    "sensitive_parameters": ("text",),
                    "max_text_characters": self.max_text_characters,
                },
            ),
            self.write,
            source="platform:windows:clipboard",
        )
        executor.register(
            ToolDefinition(
                name="clear_windows_clipboard",
                description="Clear Windows clipboard contents after explicit approval.",
                risk_level=RiskLevel.MEDIUM,
                requires_confirmation=True,
                capabilities=frozenset({"windows", "clipboard", "clear"}),
                tags=frozenset({"windows", "clipboard", "action"}),
                max_concurrency=1,
                metadata={"verification_strategy": "empty_readback"},
            ),
            self.clear,
            source="platform:windows:clipboard",
        )
