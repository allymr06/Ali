"""Windows system tray for the JARVIS desktop.

The tray is an adapter over the shell: open the window, pause or resume
new work, jump to diagnostics or settings, and exit cleanly. It never
bypasses the permission engine and adds no dependency (WinForms via
pythonnet, which the Nova shell already loads).
"""

from app.ui.tray.model import (
    TrayActions,
    TrayController,
    TrayItem,
    TrayMenuEntry,
    TrayState,
    build_menu,
    resolve_icon_path,
    tooltip_for,
)
from app.ui.tray.service import TrayService, default_backend_factory
from app.ui.tray.single_instance import DEFAULT_INSTANCE_NAME, SingleInstanceGuard

__all__ = [
    "DEFAULT_INSTANCE_NAME",
    "SingleInstanceGuard",
    "TrayActions",
    "TrayController",
    "TrayItem",
    "TrayMenuEntry",
    "TrayService",
    "TrayState",
    "build_menu",
    "default_backend_factory",
    "resolve_icon_path",
    "tooltip_for",
]
