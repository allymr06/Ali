from __future__ import annotations

import time
import tkinter as tk
from concurrent.futures import Future
from threading import Thread

import pytest

from app.bootstrap import create_application
from app.config.settings import Settings
from app.ui.controller import DesktopController
from app.ui.desktop import DesktopWindow, RoundedSurface
from app.ui.models import ChatMessage, UIScreen


def _tk_root() -> tk.Tk:
    try:
        root = tk.Tk()
    except tk.TclError as exc:
        pytest.skip(f"Tk display is unavailable: {exc}")
    root.withdraw()
    return root


def _rounded_surfaces(widget: tk.Misc) -> list[RoundedSurface]:
    surfaces: list[RoundedSurface] = []
    for child in widget.winfo_children():
        if isinstance(child, RoundedSurface):
            surfaces.append(child)
        surfaces.extend(_rounded_surfaces(child))
    return surfaces


def test_rounded_surfaces_expand_to_show_nested_content() -> None:
    root = _tk_root()
    try:
        outer = RoundedSurface(
            root,
            fill="#111111",
            outline="#333333",
            padx=12,
            pady=10,
        )
        outer.pack(fill="x")
        tk.Label(
            outer.content,
            text="Visible heading",
            bg="#111111",
        ).pack()
        inner = RoundedSurface(
            outer.content,
            fill="#181818",
            outline="#444444",
            padx=8,
            pady=6,
        )
        inner.pack(fill="x")
        tk.Label(
            inner.content,
            text="Nested content must not be clipped",
            bg="#181818",
        ).pack()

        root.after(30, root.quit)
        root.mainloop()

        assert inner.winfo_reqheight() >= (
            inner.content.winfo_reqheight() + 12
        )
        assert outer.winfo_reqheight() >= (
            outer.content.winfo_reqheight() + 20
        )
    finally:
        root.destroy()


def test_primary_screens_render_cards_and_composer_on_first_layout() -> None:
    root = _tk_root()
    application = create_application(
        Settings(
            windows_integrations_enabled=False,
            task_runtime_directory=None,
        )
    )
    window: DesktopWindow | None = None
    try:
        root.geometry("1200x800+0+0")
        root.deiconify()
        root.update()
        window = DesktopWindow(
            DesktopController(application),
            root=root,
        )

        for screen in (UIScreen.HOME, UIScreen.CHAT, UIScreen.SETTINGS):
            window.render(screen)
            for _ in range(5):
                root.update()
                time.sleep(0.005)
            surfaces = _rounded_surfaces(window.workspace)
            assert surfaces
            hidden = [
                (
                    surface.winfo_height(),
                    surface.content.winfo_reqheight(),
                    surface.content.winfo_ismapped(),
                )
                for surface in surfaces
                if surface.winfo_height() <= 1
                or not surface.content.winfo_ismapped()
            ]
            assert not hidden, (screen, hidden)

        assert window.composer_host.winfo_height() > 1
        assert window.composer.winfo_ismapped()
    finally:
        if window is not None:
            window.close()
        else:
            application.close()
            root.destroy()


def test_worker_completion_is_applied_by_the_ui_event_pump() -> None:
    root = _tk_root()
    application = create_application(
        Settings(
            windows_integrations_enabled=False,
            task_runtime_directory=None,
        )
    )
    window: DesktopWindow | None = None
    try:
        root.geometry("1200x800+0+0")
        root.deiconify()
        root.update()
        controller = DesktopController(application)
        controller.state.reduced_motion = True
        window = DesktopWindow(controller, root=root)
        operation_id = window._begin_operation("PROCESSING")
        assert operation_id is not None
        window._command_operation_id = operation_id
        window._streaming_text = ""
        controller.state.messages.append(ChatMessage("user", "Merhaba"))
        future: Future[ChatMessage] = Future()
        future.set_result(ChatMessage("assistant", "Merhaba!"))

        worker = Thread(
            target=window._queue_ui_event,
            args=(operation_id, "command_done", future),
        )
        worker.start()
        worker.join(timeout=1)
        deadline = time.monotonic() + 1
        while window._active_operation_id is not None:
            root.update()
            time.sleep(0.005)
            assert time.monotonic() < deadline

        assert [message.role for message in controller.state.messages] == [
            "user",
            "assistant",
        ]
        assert controller.state.busy is False
        assert window.send_button.cget("state") == "normal"

        window._queue_ui_event(operation_id, "command_stream", "stale")
        root.update()
        assert window._stream_target_text == ""
    finally:
        if window is not None:
            window.close()
        else:
            application.close()
            root.destroy()
