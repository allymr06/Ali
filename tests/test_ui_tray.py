"""System tray: menu model, single-instance guard, shell wiring, pause gate."""

from __future__ import annotations

import json
import os
import sys
import threading
import time
from dataclasses import dataclass
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.bootstrap import create_application
from app.config.settings import Settings
from app.ui import nova as nova_package
from app.ui.controller import DesktopController
from app.ui.nova import shell
from app.ui.tray import (
    SingleInstanceGuard,
    TrayController,
    TrayItem,
    TrayService,
    TrayState,
    build_menu,
    resolve_icon_path,
    tooltip_for,
)

PUSH_PREFIX = "window.NOVA && window.NOVA.push("


def application():
    return create_application(
        Settings(
            default_provider="mock",
            default_model="mock-model",
            windows_integrations_enabled=False,
            memory_database_path=None,
            task_database_path=None,
            task_runtime_directory=None,
        )
    )


@dataclass
class RecordingActions:
    calls: list = None

    def __post_init__(self) -> None:
        self.calls = []

    def open(self) -> None:
        self.calls.append(("open",))

    def set_paused(self, paused: bool) -> None:
        self.calls.append(("set_paused", paused))

    def show_screen(self, screen: str) -> None:
        self.calls.append(("show_screen", screen))

    def exit(self) -> None:
        self.calls.append(("exit",))


class FakeBackend:
    def __init__(self, controller, icon_path) -> None:
        self.controller = controller
        self.icon_path = icon_path
        self.events: list[str] = []

    def start(self) -> None:
        self.events.append("start")

    def refresh(self) -> None:
        self.events.append("refresh")

    def notify(self, title, text) -> None:
        self.events.append(f"notify:{title}")

    def stop(self) -> None:
        self.events.append("stop")


# ---------------------------------------------------------------------------
# menu model and controller
# ---------------------------------------------------------------------------


def test_menu_labels_follow_state() -> None:
    labels = [entry.label for entry in build_menu(TrayState())]
    assert labels == ["Öne getir", "Duraklat", "Tanılama", "Ayarlar", "Çıkış"]

    hidden_paused = TrayState(paused=True, window_visible=False)
    labels = [entry.label for entry in build_menu(hidden_paused)]
    assert labels == ["Aç", "Devam", "Tanılama", "Ayarlar", "Çıkış"]
    assert [entry.item for entry in build_menu(hidden_paused)] == list(TrayItem)
    assert build_menu(TrayState())[-1].separator_before is True
    assert tooltip_for(TrayState()) == "JARVIS — çevrimiçi"
    assert tooltip_for(hidden_paused) == "JARVIS — duraklatıldı"


def test_controller_dispatches_actions_and_tracks_state() -> None:
    actions = RecordingActions()
    controller = TrayController(actions)

    assert controller.select("open") is True
    assert controller.select(TrayItem.PAUSE) is True
    assert controller.state.paused is True
    assert controller.select(TrayItem.PAUSE) is True
    assert controller.state.paused is False
    assert controller.select("diagnostics") is True
    assert controller.select("settings") is True
    assert controller.select("bogus") is False
    assert controller.select("exit") is True
    assert controller.state.exiting is True
    # After exit nothing else is dispatched.
    assert controller.select("open") is True

    assert actions.calls == [
        ("open",),
        ("set_paused", True),
        ("set_paused", False),
        ("show_screen", "diagnostics"),
        ("show_screen", "settings"),
        ("exit",),
    ]


def test_controller_survives_failing_actions() -> None:
    class BrokenActions(RecordingActions):
        def open(self) -> None:
            raise RuntimeError("no window")

    errors: list = []
    controller = TrayController(
        BrokenActions(), on_error=lambda item, exc: errors.append((item, type(exc).__name__))
    )

    assert controller.select("open") is True
    assert errors == [(TrayItem.OPEN, "RuntimeError")]


def test_service_without_backend_keeps_state_and_never_fails() -> None:
    actions = RecordingActions()
    service = TrayService(actions, backend_factory=None)
    service.start()
    service.set_paused(True)
    service.set_window_visible(False)
    service.notify("t", "x")
    service.stop()

    assert service.active is False
    assert service.controller.state.paused is True
    assert service.controller.state.window_visible is False


def test_service_drives_the_backend() -> None:
    created: list[FakeBackend] = []

    def factory(controller, icon_path):
        backend = FakeBackend(controller, icon_path)
        created.append(backend)
        return backend

    service = TrayService(RecordingActions(), backend_factory=factory)
    service.start()
    service.start()  # idempotent
    service.set_paused(True)
    service.notify("JARVIS", "merhaba")
    service.stop()
    service.stop()

    assert len(created) == 1
    assert created[0].events == ["start", "refresh", "notify:JARVIS", "stop"]
    assert service.active is False


def test_icon_resolution_prefers_the_frozen_bundle(monkeypatch, tmp_path) -> None:
    assert resolve_icon_path() is not None and resolve_icon_path().name == "jarvis.ico"

    bundle = tmp_path / "bundle"
    (bundle / "assets").mkdir(parents=True)
    (bundle / "assets" / "jarvis.ico").write_bytes(b"ico")
    monkeypatch.setattr(sys, "_MEIPASS", str(bundle), raising=False)
    assert resolve_icon_path() == bundle / "assets" / "jarvis.ico"


# ---------------------------------------------------------------------------
# single instance
# ---------------------------------------------------------------------------


@pytest.mark.skipif(sys.platform != "win32", reason="named kernel objects are Windows-only")
def test_single_instance_guard_detects_and_activates_the_running_instance() -> None:
    name = f"JARVIS.Test.{uuid4().hex}"
    first = SingleInstanceGuard(name)
    second = SingleInstanceGuard(name)
    activated = threading.Event()
    try:
        assert first.acquire() is True
        assert first.is_owner is True
        first.watch(activated.set)

        assert second.acquire() is False
        assert second.is_owner is False
        assert second.notify_existing() is True
        assert activated.wait(3.0), "the running instance was not activated"
    finally:
        first.release()
        second.release()

    third = SingleInstanceGuard(name)
    try:
        assert third.acquire() is True
    finally:
        third.release()


# ---------------------------------------------------------------------------
# bridge pause gate
# ---------------------------------------------------------------------------


class FakeWindow:
    def __init__(self) -> None:
        self.scripts: list[str] = []

    def evaluate_js(self, script: str) -> None:
        self.scripts.append(script)

    def events(self) -> list[tuple[str, object]]:
        out = []
        for script in self.scripts:
            event = json.loads(script[len(PUSH_PREFIX) : -1])
            out.append((event["kind"], event["payload"]))
        return out


def test_bridge_refuses_new_work_while_paused() -> None:
    app = application()
    controller = DesktopController(app)
    bridge = shell.NovaBridge(controller, None)
    window = FakeWindow()
    bridge._attach(window)
    bridge.boot()
    try:
        controller.set_paused(True)
        assert controller.state.status == "PAUSED"
        for result in (
            bridge.submit_command("merhaba"),
            bridge.start_voice() if app.voice is not None else {"ok": False, "error": shell.PAUSED_MESSAGE},
            bridge.run_vision("x") if app.vision is not None else {"ok": False, "error": shell.PAUSED_MESSAGE},
            bridge.run_research("x") if app.research is not None else {"ok": False, "error": shell.PAUSED_MESSAGE},
        ):
            assert result["ok"] is False
            assert result["error"] == shell.PAUSED_MESSAGE
        assert controller._runner is None  # nothing was submitted

        bridge._push_paused(True)
        assert ("paused", {"paused": True, "status": "PAUSED"}) in window.events()

        controller.set_paused(False)
        assert controller.state.status == "LOCAL CORE READY"
        assert bridge.submit_command("merhaba") == {"ok": True}
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and not any(k == "snapshot" for k, _ in window.events()):
            time.sleep(0.01)
        assert any(k == "reply" for k, _ in window.events())
    finally:
        bridge._shutdown()
        controller.close()


# ---------------------------------------------------------------------------
# Nova shell wiring
# ---------------------------------------------------------------------------


class _Hook:
    def __init__(self, sink: list) -> None:
        self.sink = sink

    def __iadd__(self, handler):
        self.sink.append(handler)
        return self


@pytest.mark.skipif(sys.platform != "win32", reason="the tray is a Windows feature")
def test_launch_nova_wires_the_tray_pause_navigation_and_exit(monkeypatch, tmp_path) -> None:
    app = application()
    controller = DesktopController(app)
    closing_handlers: list = []
    closed_handlers: list = []
    calls: list[str] = []
    scripts: list[str] = []
    backends: list[FakeBackend] = []
    fake_window = SimpleNamespace(
        events=SimpleNamespace(closing=_Hook(closing_handlers), closed=_Hook(closed_handlers)),
        evaluate_js=lambda script: scripts.append(script),
        show=lambda: calls.append("show"),
        hide=lambda: calls.append("hide"),
        destroy=lambda: calls.append("destroy"),
        native=None,
    )
    activation: list = []

    def factory(tray_controller, icon_path):
        backend = FakeBackend(tray_controller, icon_path)
        backends.append(backend)
        return backend

    def start(**options):
        backend = backends[0]
        tray = backend.controller
        # Boot the page so pushes are delivered.
        created["js_api"].boot()
        # 1. Closing the window hides it to the tray instead of exiting.
        assert closing_handlers[0]() is False
        assert calls[-1] == "hide"
        assert "notify:JARVIS" in backend.events
        assert tray.state.window_visible is False
        # 2. "Aç" shows the window again.
        tray.select(TrayItem.OPEN)
        assert calls[-1] == "show"
        assert tray.state.window_visible is True
        # 3. "Duraklat" gates the controller and tells the page.
        tray.select(TrayItem.PAUSE)
        assert controller.paused is True
        assert created["js_api"].submit_command("x")["ok"] is False
        assert any('"kind": "paused"' in script for script in scripts)
        tray.select(TrayItem.PAUSE)
        assert controller.paused is False
        # 4. "Tanılama" opens the window on that screen.
        tray.select(TrayItem.DIAGNOSTICS)
        assert calls[-1] == "show"
        assert any('"screen": "diagnostics"' in script for script in scripts)
        # 5. "Çıkış" destroys the window and lets the close proceed.
        tray.select(TrayItem.EXIT)
        assert calls[-1] == "destroy"
        assert closing_handlers[0]() is None
        for handler in closed_handlers:
            handler()

    created: dict = {}

    def create_window(title, **kwargs):
        created.update(kwargs, title=title)
        return fake_window

    monkeypatch.setattr(shell.webview, "create_window", create_window)
    monkeypatch.setattr(shell.webview, "start", start)
    monkeypatch.setattr(shell, "webview_storage_directory", lambda: tmp_path / "webview")

    shell.launch_nova(
        controller,
        None,
        settings=SimpleNamespace(tray_enabled=True, tray_close_to_tray=True),
        activation_watch=activation.append,
        tray_backend_factory=factory,
    )

    assert backends[0].events[0] == "start"
    assert backends[0].events[-1] == "stop"
    assert len(activation) == 1 and callable(activation[0])
    assert controller._runner is None


def test_launch_nova_without_tray_closes_normally(monkeypatch, tmp_path) -> None:
    app = application()
    controller = DesktopController(app)
    closing_handlers: list = []
    closed_handlers: list = []
    calls: list[str] = []
    fake_window = SimpleNamespace(
        events=SimpleNamespace(closing=_Hook(closing_handlers), closed=_Hook(closed_handlers)),
        evaluate_js=lambda script: None,
        hide=lambda: calls.append("hide"),
        show=lambda: calls.append("show"),
        destroy=lambda: calls.append("destroy"),
    )

    def start(**options):
        assert closing_handlers[0]() is None
        assert calls == []
        for handler in closed_handlers:
            handler()

    monkeypatch.setattr(shell.webview, "create_window", lambda title, **kwargs: fake_window)
    monkeypatch.setattr(shell.webview, "start", start)
    monkeypatch.setattr(shell, "webview_storage_directory", lambda: tmp_path / "webview")
    monkeypatch.setattr(
        shell, "default_backend_factory", lambda: pytest.fail("tray must stay off")
    )

    shell.launch_nova(controller, None, settings=SimpleNamespace(tray_enabled=False))

    assert controller._runner is None


@pytest.mark.skipif(sys.platform != "win32", reason="the tray is a Windows feature")
def test_launch_nova_continues_when_the_tray_cannot_start(monkeypatch, tmp_path) -> None:
    app = application()
    controller = DesktopController(app)
    closing_handlers: list = []
    closed_handlers: list = []
    fake_window = SimpleNamespace(
        events=SimpleNamespace(closing=_Hook(closing_handlers), closed=_Hook(closed_handlers)),
        evaluate_js=lambda script: None,
        hide=lambda: pytest.fail("must not hide without a tray"),
        show=lambda: None,
        destroy=lambda: None,
    )

    class BrokenBackend(FakeBackend):
        def start(self) -> None:
            raise RuntimeError("no shell")

    def start(**options):
        assert closing_handlers[0]() is None  # plain close: no tray to hide into
        for handler in closed_handlers:
            handler()

    monkeypatch.setattr(shell.webview, "create_window", lambda title, **kwargs: fake_window)
    monkeypatch.setattr(shell.webview, "start", start)
    monkeypatch.setattr(shell, "webview_storage_directory", lambda: tmp_path / "webview")

    shell.launch_nova(
        controller,
        None,
        settings=SimpleNamespace(tray_enabled=True, tray_close_to_tray=True),
        tray_backend_factory=lambda c, icon: BrokenBackend(c, icon),
    )

    events = app.diagnostics.events(component="ui")["events"]
    assert any(event["name"] == "tray.error" for event in events)


# ---------------------------------------------------------------------------
# desktop entry: single instance
# ---------------------------------------------------------------------------


@pytest.fixture
def desktop_entry(monkeypatch):
    import app.ui.desktop as desktop
    import app.ui.tray as tray_package

    calls: list[tuple[str, object]] = []
    guards: list = []
    runtime_settings = SimpleNamespace(single_instance_enabled=True, tray_enabled=False)
    monkeypatch.setattr(desktop, "enable_high_dpi_rendering", lambda: None)
    monkeypatch.setattr(
        desktop,
        "create_api_settings_service",
        lambda: SimpleNamespace(build_runtime_settings=lambda: runtime_settings),
    )
    monkeypatch.setattr(
        "app.bootstrap.create_application", lambda settings=None: application()
    )

    class FakeGuard:
        acquire_result = True

        def __init__(self, name="JARVIS.Desktop") -> None:
            self.name = name
            self.log: list[str] = []
            guards.append(self)

        def acquire(self) -> bool:
            self.log.append("acquire")
            return FakeGuard.acquire_result

        def notify_existing(self) -> bool:
            self.log.append("notify")
            return True

        def watch(self, callback) -> None:
            self.log.append("watch")

        def release(self) -> None:
            self.log.append("release")

    monkeypatch.setattr(tray_package, "SingleInstanceGuard", FakeGuard)

    class FakeClassicWindow:
        def __init__(self, controller, api_settings=None) -> None:
            calls.append(("classic", controller))

        def run(self) -> None:
            calls.append(("classic-run", None))

    monkeypatch.setattr(desktop, "DesktopWindow", FakeClassicWindow)
    monkeypatch.setattr(nova_package, "detect_webview2_runtime", lambda: "test-runtime")
    monkeypatch.setattr(
        nova_package,
        "launch_nova",
        lambda controller, api_settings=None, **kwargs: calls.append(("nova", kwargs)),
    )
    return desktop, calls, guards, FakeGuard


def test_second_launch_activates_the_first_and_exits(desktop_entry, capsys) -> None:
    desktop, calls, guards, FakeGuard = desktop_entry
    FakeGuard.acquire_result = False

    desktop.launch_desktop(classic=False)

    assert calls == []
    assert guards[0].log == ["acquire", "notify"]
    assert "zaten çalışıyor" in capsys.readouterr().err


def test_first_launch_watches_for_activation_and_releases(desktop_entry) -> None:
    desktop, calls, guards, FakeGuard = desktop_entry
    FakeGuard.acquire_result = True

    desktop.launch_desktop(classic=False)

    assert [name for name, _ in calls] == ["nova"]
    kwargs = calls[0][1]
    assert kwargs["settings"].single_instance_enabled is True
    assert kwargs["activation_watch"] == guards[0].watch
    assert guards[0].log == ["acquire", "release"]


def test_classic_launch_also_holds_the_instance(desktop_entry) -> None:
    desktop, calls, guards, FakeGuard = desktop_entry
    FakeGuard.acquire_result = True

    desktop.launch_desktop(classic=True)

    assert [name for name, _ in calls] == ["classic", "classic-run"]
    assert guards[0].log == ["acquire", "release"]


def test_tray_settings_parse(monkeypatch) -> None:
    monkeypatch.setenv("JARVIS_TRAY_ENABLED", "false")
    monkeypatch.setenv("JARVIS_TRAY_CLOSE_TO_TRAY", "false")
    monkeypatch.setenv("JARVIS_SINGLE_INSTANCE", "false")
    settings = Settings.from_environment()
    assert (settings.tray_enabled, settings.tray_close_to_tray, settings.single_instance_enabled) == (
        False,
        False,
        False,
    )
    for name in ("JARVIS_TRAY_ENABLED", "JARVIS_TRAY_CLOSE_TO_TRAY", "JARVIS_SINGLE_INSTANCE"):
        monkeypatch.delenv(name)
    defaults = Settings.from_environment()
    assert (defaults.tray_enabled, defaults.tray_close_to_tray, defaults.single_instance_enabled) == (
        True,
        True,
        True,
    )


# ---------------------------------------------------------------------------
# real WinForms backend (opt-in: shows an icon on the desktop)
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    os.environ.get("JARVIS_TRAY_LIVE_TESTS") != "1" or sys.platform != "win32",
    reason="set JARVIS_TRAY_LIVE_TESTS=1 on Windows to exercise the real tray icon",
)
def test_winforms_backend_starts_refreshes_and_stops() -> None:
    from app.ui.tray.winforms import WinFormsTrayBackend

    controller = TrayController(RecordingActions())
    backend = WinFormsTrayBackend(controller, icon_path=resolve_icon_path())
    backend.start()
    try:
        controller.set_paused(True)
        backend.refresh()
        backend.notify("JARVIS", "tepsi testi")
        time.sleep(0.5)
    finally:
        backend.stop()
    assert backend._icon is None and backend._form is None
