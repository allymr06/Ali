"""Windows notification-area icon on its own STA thread (WinForms).

pythonnet and WinForms are already part of the Nova shell (pywebview's
Windows backend), so the tray adds no dependency. The icon lives on a
dedicated STA thread with its own message loop; that keeps it independent
of whichever toolkit draws the main window and lets ``stop()`` end the
loop deterministically. Menu selections are handed to the
:class:`TrayController` on the tray thread; the shell's actions are
thread-safe pywebview calls.
"""

from __future__ import annotations

import sys
import threading
from pathlib import Path
from typing import Any

from app.ui.tray.model import TRAY_TITLE, TrayController, TrayItem

READY_TIMEOUT_SECONDS = 5.0
STOP_TIMEOUT_SECONDS = 5.0


class WinFormsTrayBackend:
    def __init__(
        self,
        controller: TrayController,
        *,
        icon_path: Path | None = None,
        title: str = TRAY_TITLE,
    ) -> None:
        if sys.platform != "win32":
            raise RuntimeError("The WinForms tray backend needs Windows.")
        self._controller = controller
        self._icon_path = icon_path
        self._title = title
        self._ready = threading.Event()
        self._thread: Any = None
        self._form: Any = None
        self._icon: Any = None
        self._forms: Any = None
        self._action_type: Any = None
        self._failure: BaseException | None = None
        self._stopped = False

    # ------------------------------------------------------------------
    # lifecycle (called from the shell's threads)
    # ------------------------------------------------------------------
    def start(self) -> None:
        import clr  # pythonnet, present with pywebview on Windows

        clr.AddReference("System.Windows.Forms")
        clr.AddReference("System.Drawing")
        from System.Threading import ApartmentState, Thread, ThreadStart

        thread = Thread(ThreadStart(self._run))
        thread.SetApartmentState(ApartmentState.STA)
        thread.IsBackground = True
        thread.Name = "jarvis-tray"
        self._thread = thread
        thread.Start()
        if not self._ready.wait(READY_TIMEOUT_SECONDS):
            raise RuntimeError("The tray icon did not become ready in time.")
        if self._failure is not None:
            raise RuntimeError(
                f"The tray icon could not be created ({type(self._failure).__name__})."
            )

    def refresh(self) -> None:
        """Rebuild labels and tooltip from the controller state."""
        self._invoke(self._apply_state)

    def notify(self, title: str, text: str) -> None:
        def show() -> None:
            if self._icon is not None:
                self._icon.ShowBalloonTip(4000, title, text, self._forms.ToolTipIcon.Info)

        self._invoke(show)

    def stop(self) -> None:
        if self._stopped:
            return
        self._stopped = True
        if self._form is None:
            return

        def exit_loop() -> None:
            self._forms.Application.ExitThread()

        self._invoke(exit_loop)
        thread = self._thread
        if thread is not None:
            thread.Join(int(STOP_TIMEOUT_SECONDS * 1000))

    # ------------------------------------------------------------------
    # tray thread
    # ------------------------------------------------------------------
    def _invoke(self, function) -> None:
        form = self._form
        if form is None or self._action_type is None:
            return
        try:
            form.BeginInvoke(self._action_type(function))
        except Exception:
            # The loop is gone (stop already ran); nothing to update.
            pass

    def _run(self) -> None:
        try:
            import System.Drawing as Drawing
            import System.Windows.Forms as WinForms
            from System import Action

            self._forms = WinForms
            self._action_type = Action
            form = WinForms.Form()
            form.ShowInTaskbar = False
            form.Opacity = 0
            form.FormBorderStyle = WinForms.FormBorderStyle.FixedToolWindow
            form.WindowState = WinForms.FormWindowState.Minimized
            form.Text = f"{self._title} tray host"
            _handle = form.Handle  # create the handle so BeginInvoke works
            self._form = form

            icon = WinForms.NotifyIcon()
            if self._icon_path is not None and self._icon_path.is_file():
                icon.Icon = Drawing.Icon(str(self._icon_path))
            else:
                icon.Icon = Drawing.SystemIcons.Application
            icon.DoubleClick += self._on_double_click
            self._icon = icon
            self._apply_state()
            icon.Visible = True
            self._ready.set()
            WinForms.Application.Run()
        except BaseException as exc:  # noqa: BLE001 - surfaced through start()
            self._failure = exc
            self._ready.set()
        finally:
            icon_object = self._icon
            self._icon = None
            if icon_object is not None:
                try:
                    icon_object.Visible = False
                    icon_object.Dispose()
                except Exception:
                    pass
            form_object = self._form
            self._form = None
            if form_object is not None:
                try:
                    form_object.Dispose()
                except Exception:
                    pass

    def _apply_state(self) -> None:
        if self._icon is None:
            return
        WinForms = self._forms
        menu = WinForms.ContextMenuStrip()
        for entry in self._controller.menu():
            if entry.separator_before:
                menu.Items.Add(WinForms.ToolStripSeparator())
            item = WinForms.ToolStripMenuItem(entry.label)
            item.Enabled = entry.enabled
            item.Click += self._click_handler(entry.item)
            menu.Items.Add(item)
        self._icon.ContextMenuStrip = menu
        self._icon.Text = self._controller.tooltip()

    def _click_handler(self, item: TrayItem):
        def handler(_sender, _args) -> None:
            self._controller.select(item)
            self._apply_state()

        return handler

    def _on_double_click(self, _sender, _args) -> None:
        self._controller.select(TrayItem.OPEN)
        self._apply_state()


__all__ = ["WinFormsTrayBackend"]
