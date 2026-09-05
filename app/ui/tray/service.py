"""Tray service: controller plus an optional backend, safe when headless."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any, Protocol

from app.ui.tray.model import TrayActions, TrayController, TrayItem, resolve_icon_path


class TrayBackend(Protocol):
    def start(self) -> None: ...

    def refresh(self) -> None: ...

    def notify(self, title: str, text: str) -> None: ...

    def stop(self) -> None: ...


BackendFactory = Callable[[TrayController, Path | None], TrayBackend]


class TrayService:
    """Own the tray for one desktop session.

    ``backend_factory`` receives the controller and the icon path and
    returns a backend; ``None`` means no icon is shown (tests, disabled
    setting, unsupported platform) while pause/open bookkeeping still
    works, so the shell can call the service unconditionally.
    """

    def __init__(
        self,
        actions: TrayActions,
        *,
        backend_factory: BackendFactory | None = None,
        icon_path: Path | None = None,
        on_error: Callable[[TrayItem, BaseException], None] | None = None,
    ) -> None:
        self.controller = TrayController(actions, on_error=on_error)
        self._backend: TrayBackend | None = None
        self._factory = backend_factory
        self._icon_path = icon_path if icon_path is not None else resolve_icon_path()
        self._started = False

    @property
    def active(self) -> bool:
        return self._backend is not None

    def start(self) -> None:
        if self._started:
            return
        self._started = True
        if self._factory is None:
            return
        backend = self._factory(self.controller, self._icon_path)
        backend.start()
        self._backend = backend

    def set_paused(self, paused: bool) -> None:
        self.controller.set_paused(paused)
        self._refresh()

    def set_window_visible(self, visible: bool) -> None:
        self.controller.set_window_visible(visible)
        self._refresh()

    def notify(self, title: str, text: str) -> None:
        if self._backend is not None:
            try:
                self._backend.notify(title, text)
            except Exception:
                pass

    def stop(self) -> None:
        backend = self._backend
        self._backend = None
        if backend is not None:
            try:
                backend.stop()
            except Exception:
                pass

    def _refresh(self) -> None:
        if self._backend is not None:
            try:
                self._backend.refresh()
            except Exception:
                pass


def default_backend_factory() -> BackendFactory | None:
    """The real Windows backend when it can exist, otherwise None."""
    import sys

    if sys.platform != "win32":
        return None
    try:
        from app.ui.tray.winforms import WinFormsTrayBackend
    except Exception:  # pragma: no cover - pythonnet missing
        return None

    def factory(controller: TrayController, icon_path: Path | None) -> Any:
        return WinFormsTrayBackend(controller, icon_path=icon_path)

    return factory


__all__ = ["BackendFactory", "TrayBackend", "TrayService", "default_backend_factory"]
