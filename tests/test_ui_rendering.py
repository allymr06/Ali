from __future__ import annotations

import time
import tkinter as tk
from concurrent.futures import Future
from dataclasses import replace
from datetime import timedelta
from threading import Thread, get_ident
from uuid import uuid4

import pytest

from app.bootstrap import create_application
from app.config.settings import Settings
from app.core.models import RiskLevel
from app.core.time import utc_now
from app.security.interactive import InteractiveApprovalRequest
from app.ui.controller import DesktopController
from app.ui.desktop import ApprovalPrompt, DesktopWindow, RoundedSurface
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


def _label_texts(widget: tk.Misc) -> list[str]:
    texts: list[str] = []
    for child in widget.winfo_children():
        if isinstance(child, tk.Label):
            texts.append(str(child.cget("text")))
        texts.extend(_label_texts(child))
    return texts


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
            surfaces: list[RoundedSurface] = []
            deadline = time.monotonic() + 1.0
            while time.monotonic() < deadline:
                root.update()
                surfaces = _rounded_surfaces(window.workspace)
                if surfaces and all(
                    surface.winfo_height() > 1
                    and surface.winfo_height()
                    >= surface.content.winfo_reqheight()
                    for surface in surfaces
                ):
                    break
                time.sleep(0.01)
            assert surfaces
            hidden = [
                (
                    surface.winfo_height(),
                    surface.content.winfo_reqheight(),
                    surface.content.winfo_ismapped(),
                )
                for surface in surfaces
                if surface.winfo_height() <= 1
                or surface.winfo_height()
                < surface.content.winfo_reqheight()
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


def test_assistant_bubble_renders_turkish_reasoning_and_assurance_labels() -> None:
    root = _tk_root()
    application = create_application(
        Settings(
            windows_integrations_enabled=False,
            task_runtime_directory=None,
        )
    )
    window: DesktopWindow | None = None
    try:
        controller = DesktopController(application)
        controller.state.messages.append(
            ChatMessage(
                "assistant",
                "Kaynaklarla desteklenen yanıt.",
                metadata={
                    "reasoning_level": "high",
                    "assurance_level": "research_supported",
                    "uncertainty_summary": "Yayın tarihi bilinmiyor.",
                },
            )
        )
        application.settings = replace(
            application.settings,
            default_provider="gemini",
        )
        window = DesktopWindow(controller, root=root)
        window.render(UIScreen.CHAT)
        for _ in range(3):
            root.update()

        texts = _label_texts(window.workspace)

        assert "DÜŞÜNME: DERİN  •  GÜVEN: KAYNAKLARLA DESTEKLENDİ" in texts
        assert "Not: Yayın tarihi bilinmiyor." in texts
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
        voice_operation_id = window._begin_voice_operation()
        assert voice_operation_id is not None
        operation_id = window._begin_operation("PROCESSING")
        assert operation_id is not None
        assert operation_id != voice_operation_id
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
        assert controller.state.voice_active is True
        assert window.send_button.cget("state") == "normal"
        assert window.status_label.cget("text") == "DİNLİYOR"

        window._queue_ui_event(operation_id, "command_stream", "stale")
        root.update()
        assert window._stream_target_text == ""
        assert window._complete_voice_operation(voice_operation_id)
        assert controller.state.voice_active is False
    finally:
        if window is not None:
            window.close()
        else:
            application.close()
            root.destroy()


def test_tool_approval_event_is_main_thread_only_and_stale_safe(
    monkeypatch,
) -> None:
    root = _tk_root()
    application = create_application(
        Settings(windows_integrations_enabled=False, task_runtime_directory=None)
    )
    window: DesktopWindow | None = None
    try:
        window = DesktopWindow(DesktopController(application), root=root)
        operation_id = window._begin_operation("PROCESSING")
        assert operation_id is not None
        request = InteractiveApprovalRequest(
            operation_id=uuid4(),
            request_id=uuid4(),
            conversation_id=uuid4(),
            request_source="text",
            tool_name="write_text_file",
            operation="write_text_file",
            risk_level=RiskLevel.MEDIUM,
            reason="confirmation",
            parameters={"path": "note.txt", "content": "<6 karakter>"},
            expires_at=utc_now() + timedelta(minutes=1),
        )
        main_thread = get_ident()
        observed_threads = []
        monkeypatch.setattr(
            "app.ui.desktop.messagebox.askyesno",
            lambda *args, **kwargs: observed_threads.append(get_ident()) or True,
        )
        approved: Future[bool] = Future()
        window._queue_ui_event(
            operation_id,
            "approval_request",
            ApprovalPrompt(request, approved),
        )
        window._drain_ui_events()
        assert approved.result(timeout=1) is True
        assert observed_threads == [main_thread]

        stale: Future[bool] = Future()
        window._queue_ui_event(
            operation_id + 99,
            "approval_request",
            ApprovalPrompt(request, stale),
        )
        window._drain_ui_events()
        assert stale.result(timeout=1) is False
        assert window._complete_operation(operation_id)
    finally:
        if window is not None:
            window.close()
        else:
            application.close()
            root.destroy()


def test_voice_message_updates_overlay_transcript() -> None:
    from app.ui.desktop import VoiceOverlay

    root = _tk_root()
    application = create_application(
        Settings(windows_integrations_enabled=False, task_runtime_directory=None)
    )
    window: DesktopWindow | None = None
    try:
        window = DesktopWindow(DesktopController(application), root=root)
        window._voice_operation_id = 7
        window.voice_overlay = VoiceOverlay(
            window.shell,
            window._colors,
            window.engine,
            on_dismiss=lambda: None,
        )
        window.voice_overlay.open()
        root.update()

        window._apply_voice_message(7, ChatMessage("user", "Merhaba Jarvis"))

        assert window.voice_overlay._transcript == "Merhaba Jarvis"

        window._apply_voice_message(7, ChatMessage("assistant", "Merhaba!"))
        assert window.voice_overlay._transcript == "Merhaba Jarvis"
    finally:
        if window is not None:
            window.close()
        else:
            application.close()
            root.destroy()
