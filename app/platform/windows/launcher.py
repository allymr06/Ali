from __future__ import annotations

import subprocess
import time
from collections.abc import Callable
from pathlib import Path
from typing import Protocol

from app.platform.windows.applications import WindowsApplicationRegistry
from app.platform.windows.models import (
    WindowsApplication,
    WindowsLaunchOutcome,
    WindowsProcess,
)


class ProcessObserver(Protocol):
    def get_process(self, pid: int) -> WindowsProcess | None: ...


ProcessSpawner = Callable[[list[str]], int]


def _spawn_process(command: list[str]) -> int:
    process = subprocess.Popen(
        command,
        shell=False,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        close_fds=True,
    )
    return process.pid


class WindowsApplicationLauncher:
    """Launch a registered application and independently observe its PID."""

    def __init__(
        self,
        registry: WindowsApplicationRegistry,
        observer: ProcessObserver,
        *,
        spawner: ProcessSpawner = _spawn_process,
        verification_timeout_seconds: float = 3.0,
        poll_interval_seconds: float = 0.05,
    ) -> None:
        if verification_timeout_seconds <= 0:
            raise ValueError("verification_timeout_seconds must be greater than 0.")
        if poll_interval_seconds <= 0:
            raise ValueError("poll_interval_seconds must be greater than 0.")
        if not callable(spawner):
            raise TypeError("spawner must be callable.")
        self._registry = registry
        self._observer = observer
        self._spawner = spawner
        self._verification_timeout_seconds = verification_timeout_seconds
        self._poll_interval_seconds = poll_interval_seconds

    def launch(self, application_name: str) -> WindowsLaunchOutcome:
        try:
            application = self._registry.resolve(application_name)
            executable = self._registry.resolve_executable(application)
        except (KeyError, TypeError, ValueError, FileNotFoundError) as exc:
            return WindowsLaunchOutcome(
                application_id=(
                    application_name.strip().lower()
                    if isinstance(application_name, str)
                    else ""
                ),
                pid=None,
                verified=False,
                message="Windows application could not be resolved.",
                error=str(exc),
            )

        command = [executable, *application.arguments]
        try:
            pid = self._spawner(command)
        except OSError as exc:
            return WindowsLaunchOutcome(
                application_id=application.application_id,
                pid=None,
                verified=False,
                message="Windows application failed to start.",
                error=str(exc),
            )
        if not isinstance(pid, int) or isinstance(pid, bool) or pid < 1:
            return WindowsLaunchOutcome(
                application_id=application.application_id,
                pid=None,
                verified=False,
                message="Windows launcher returned an invalid process identity.",
                error="Invalid process id.",
            )

        try:
            process = self._wait_for_process(pid)
        except OSError as exc:
            return WindowsLaunchOutcome(
                application_id=application.application_id,
                pid=pid,
                verified=False,
                message="Application launch observation failed.",
                error=str(exc),
            )
        if process is None:
            return WindowsLaunchOutcome(
                application_id=application.application_id,
                pid=pid,
                verified=False,
                message="Application launch could not be verified.",
                error="The launched process was not observable before timeout.",
            )
        if not self._matches_application(process, application):
            return WindowsLaunchOutcome(
                application_id=application.application_id,
                pid=pid,
                verified=False,
                message="Application launch verification failed.",
                process=process,
                error="Observed process identity does not match the application contract.",
            )
        return WindowsLaunchOutcome(
            application_id=application.application_id,
            pid=pid,
            verified=True,
            message=f"{application.display_name} started and was verified.",
            process=process,
        )

    def _wait_for_process(self, pid: int) -> WindowsProcess | None:
        deadline = time.monotonic() + self._verification_timeout_seconds
        while time.monotonic() < deadline:
            process = self._observer.get_process(pid)
            if process is not None:
                return process
            time.sleep(self._poll_interval_seconds)
        return None

    @staticmethod
    def _matches_application(
        process: WindowsProcess,
        application: WindowsApplication,
    ) -> bool:
        observed_names = {process.name.lower()}
        if process.executable_path:
            observed_names.add(Path(process.executable_path).name.lower())
        return bool(observed_names.intersection(application.expected_process_names))
