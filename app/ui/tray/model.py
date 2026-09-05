"""Tray menu model and controller, independent of any GUI toolkit.

The tray is an adapter, not an authority: every entry maps to an action the
shell already offers. ``Duraklat`` is a user-interface gate (new commands,
voice, vision, and research requests are refused and an active voice
session is stopped); it is not a security control and does not touch the
permission engine. ``Çıkış`` performs the same clean shutdown as closing
the window normally.
"""

from __future__ import annotations

import sys
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Protocol

TRAY_TITLE = "JARVIS"
TOOLTIP_MAX_CHARACTERS = 63  # Shell_NotifyIcon limit


class TrayItem(StrEnum):
    OPEN = "open"
    PAUSE = "pause"
    DIAGNOSTICS = "diagnostics"
    SETTINGS = "settings"
    EXIT = "exit"


@dataclass(frozen=True, slots=True)
class TrayMenuEntry:
    item: TrayItem
    label: str
    enabled: bool = True
    separator_before: bool = False


@dataclass(slots=True)
class TrayState:
    paused: bool = False
    window_visible: bool = True
    exiting: bool = False


class TrayActions(Protocol):
    """What the shell lets the tray do."""

    def open(self) -> None: ...

    def set_paused(self, paused: bool) -> None: ...

    def show_screen(self, screen: str) -> None: ...

    def exit(self) -> None: ...


def build_menu(state: TrayState) -> tuple[TrayMenuEntry, ...]:
    """Turkish menu whose labels follow the current state."""
    return (
        TrayMenuEntry(TrayItem.OPEN, "Aç" if not state.window_visible else "Öne getir"),
        TrayMenuEntry(TrayItem.PAUSE, "Devam" if state.paused else "Duraklat"),
        TrayMenuEntry(TrayItem.DIAGNOSTICS, "Tanılama", separator_before=True),
        TrayMenuEntry(TrayItem.SETTINGS, "Ayarlar"),
        TrayMenuEntry(TrayItem.EXIT, "Çıkış", separator_before=True),
    )


def tooltip_for(state: TrayState) -> str:
    text = f"{TRAY_TITLE} — duraklatıldı" if state.paused else f"{TRAY_TITLE} — çevrimiçi"
    return text[:TOOLTIP_MAX_CHARACTERS]


def resolve_icon_path() -> Path | None:
    """The branded icon, from the frozen bundle or the source tree."""
    candidates: list[Path] = []
    bundle_root = getattr(sys, "_MEIPASS", None)
    if bundle_root:
        candidates.append(Path(bundle_root) / "assets" / "jarvis.ico")
    source_root = Path(__file__).resolve().parents[3]
    candidates.append(source_root / "assets" / "branding" / "jarvis.ico")
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return None


class TrayController:
    """Maps menu selections to shell actions and keeps the menu state.

    Selections arrive on the tray backend's own thread; the controller
    never raises into that thread. A failing action is reported through
    ``on_error`` and the tray keeps working.
    """

    def __init__(
        self,
        actions: TrayActions,
        *,
        state: TrayState | None = None,
        on_error: Callable[[TrayItem, BaseException], None] | None = None,
    ) -> None:
        self._actions = actions
        self.state = state or TrayState()
        self._on_error = on_error

    def menu(self) -> tuple[TrayMenuEntry, ...]:
        return build_menu(self.state)

    def tooltip(self) -> str:
        return tooltip_for(self.state)

    def set_paused(self, paused: bool) -> None:
        self.state.paused = bool(paused)

    def set_window_visible(self, visible: bool) -> None:
        self.state.window_visible = bool(visible)

    def select(self, item: TrayItem | str) -> bool:
        """Perform the action behind ``item``; returns whether it was known."""
        try:
            selected = TrayItem(str(item))
        except ValueError:
            return False
        if self.state.exiting and selected is not TrayItem.EXIT:
            return True
        try:
            if selected is TrayItem.OPEN:
                self._actions.open()
                self.state.window_visible = True
            elif selected is TrayItem.PAUSE:
                self.state.paused = not self.state.paused
                self._actions.set_paused(self.state.paused)
            elif selected is TrayItem.DIAGNOSTICS:
                self._actions.show_screen("diagnostics")
                self.state.window_visible = True
            elif selected is TrayItem.SETTINGS:
                self._actions.show_screen("settings")
                self.state.window_visible = True
            elif selected is TrayItem.EXIT:
                self.state.exiting = True
                self._actions.exit()
        except Exception as exc:  # the tray thread must survive a bad action
            if self._on_error is not None:
                self._on_error(selected, exc)
        return True


__all__ = [
    "TRAY_TITLE",
    "TrayActions",
    "TrayController",
    "TrayItem",
    "TrayMenuEntry",
    "TrayState",
    "build_menu",
    "resolve_icon_path",
    "tooltip_for",
]
