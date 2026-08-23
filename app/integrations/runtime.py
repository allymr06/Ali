"""Shared helpers for application integrations.

Everything here is bounded and injectable so unit tests can replace
the operating-system surface with fakes.
"""

from __future__ import annotations

import asyncio
import ctypes
import subprocess
import sys
from typing import Any


class PowerShellRunner:
    """Run short PowerShell snippets with a strict timeout."""

    def __init__(self, timeout_seconds: float = 12.0) -> None:
        self.timeout_seconds = timeout_seconds

    async def run(
        self,
        script: str,
        *,
        timeout_seconds: float | None = None,
    ) -> tuple[int, str]:
        limit = timeout_seconds or self.timeout_seconds

        def execute() -> tuple[int, str]:
            completed = subprocess.run(
                [
                    "powershell",
                    "-NoProfile",
                    "-NonInteractive",
                    "-Command",
                    script,
                ],
                capture_output=True,
                timeout=limit,
                creationflags=getattr(
                    subprocess, "CREATE_NO_WINDOW", 0
                ),
            )
            output = completed.stdout.decode(
                "utf-8", errors="replace"
            ).strip()
            return completed.returncode, output

        return await asyncio.to_thread(execute)


class MediaKeySender:
    """Send global media keys through SendInput (no window focus)."""

    _KEYEVENTF_EXTENDEDKEY = 0x0001
    _KEYEVENTF_KEYUP = 0x0002

    PLAY_PAUSE = 0xB3
    NEXT_TRACK = 0xB0
    PREVIOUS_TRACK = 0xB1

    def send(self, virtual_key: int) -> bool:
        if sys.platform != "win32":
            return False
        try:
            user32 = ctypes.WinDLL("user32", use_last_error=True)
            user32.keybd_event(
                virtual_key, 0, self._KEYEVENTF_EXTENDEDKEY, 0
            )
            user32.keybd_event(
                virtual_key,
                0,
                self._KEYEVENTF_EXTENDEDKEY | self._KEYEVENTF_KEYUP,
                0,
            )
            return True
        except OSError:
            return False


class UriLauncher:
    """Open registered URI schemes (spotify:, whatsapp:) detached."""

    def open(self, uri: str) -> bool:
        if sys.platform != "win32":
            return False
        try:
            import os

            os.startfile(uri)  # noqa: S606 - registered URI scheme
            return True
        except OSError:
            return False


def tool_data(payload: Any) -> Any:
    return payload
