"""One JARVIS desktop per user session, with activation of the running one.

A named mutex in the ``Local\\`` namespace marks the running instance; a
named auto-reset event lets a second launch ask the first instance to
bring its window to the front and then exit. Both objects are per logon
session, so different users on one machine do not interfere. Outside
Windows the guard is a no-op that always acquires.
"""

from __future__ import annotations

import ctypes
import sys
import threading
from collections.abc import Callable

DEFAULT_INSTANCE_NAME = "JARVIS.Desktop"
_ERROR_ALREADY_EXISTS = 183
_WAIT_OBJECT_0 = 0
_POLL_MILLISECONDS = 500


class SingleInstanceGuard:
    def __init__(self, name: str = DEFAULT_INSTANCE_NAME) -> None:
        self.name = name
        self._mutex_name = f"Local\\{name}"
        self._event_name = f"Local\\{name}.Activate"
        self._mutex: int | None = None
        self._event: int | None = None
        self._owner = False
        self._watcher: threading.Thread | None = None
        self._stop = threading.Event()

    @property
    def is_owner(self) -> bool:
        return self._owner

    @staticmethod
    def _kernel32():  # pragma: no cover - trivial accessor
        return ctypes.WinDLL("kernel32", use_last_error=True)

    def acquire(self) -> bool:
        """Claim the instance; False means another instance already runs."""
        if sys.platform != "win32":
            self._owner = True
            return True
        kernel32 = self._kernel32()
        kernel32.CreateMutexW.restype = ctypes.c_void_p
        handle = kernel32.CreateMutexW(None, True, self._mutex_name)
        if not handle:
            # Cannot tell; never block the desktop because of it.
            self._owner = True
            return True
        if ctypes.get_last_error() == _ERROR_ALREADY_EXISTS:
            kernel32.CloseHandle(ctypes.c_void_p(handle))
            self._owner = False
            return False
        self._mutex = handle
        self._owner = True
        self._event = self._open_event()
        return True

    def _open_event(self) -> int | None:
        kernel32 = self._kernel32()
        kernel32.CreateEventW.restype = ctypes.c_void_p
        handle = kernel32.CreateEventW(None, False, False, self._event_name)
        return handle or None

    def notify_existing(self) -> bool:
        """Ask the running instance to show itself."""
        if sys.platform != "win32":
            return False
        handle = self._open_event()
        if handle is None:
            return False
        kernel32 = self._kernel32()
        try:
            return bool(kernel32.SetEvent(ctypes.c_void_p(handle)))
        finally:
            kernel32.CloseHandle(ctypes.c_void_p(handle))

    def watch(self, callback: Callable[[], None]) -> None:
        """Run ``callback`` (on a helper thread) each time a launch signals."""
        if sys.platform != "win32" or not self._owner or self._event is None:
            return
        if self._watcher is not None:
            return
        event = self._event

        def run() -> None:
            kernel32 = self._kernel32()
            while not self._stop.is_set():
                result = kernel32.WaitForSingleObject(
                    ctypes.c_void_p(event), _POLL_MILLISECONDS
                )
                if result == _WAIT_OBJECT_0 and not self._stop.is_set():
                    try:
                        callback()
                    except Exception:
                        pass

        self._watcher = threading.Thread(
            target=run, name="jarvis-instance-activate", daemon=True
        )
        self._watcher.start()

    def release(self) -> None:
        self._stop.set()
        watcher = self._watcher
        if watcher is not None:
            watcher.join(timeout=2.0)
            self._watcher = None
        if sys.platform != "win32":
            self._owner = False
            return
        kernel32 = self._kernel32()
        if self._event is not None:
            kernel32.CloseHandle(ctypes.c_void_p(self._event))
            self._event = None
        if self._mutex is not None:
            kernel32.ReleaseMutex(ctypes.c_void_p(self._mutex))
            kernel32.CloseHandle(ctypes.c_void_p(self._mutex))
            self._mutex = None
        self._owner = False


__all__ = ["DEFAULT_INSTANCE_NAME", "SingleInstanceGuard"]
