"""Nova shell: the pywebview bridge between the page and the Python core.

These tests never open a WebView2 window. A fake window records every
``window.NOVA.push(...)`` script so the exact events the page would
receive can be asserted, and the real DesktopController runs on its own
async runner exactly as it does in production.
"""

from __future__ import annotations

import asyncio
import json
import sys
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
from pathlib import PurePath
from types import MappingProxyType, SimpleNamespace
from uuid import uuid4

import pytest

from app.bootstrap import JARVISApplication, create_application
from app.config.provider_preferences import (
    DEFAULT_GEMINI_MODEL,
    ProviderPreferencesStore,
)
from app.config.settings import Settings
from app.core.models import Response, RiskLevel
from app.core.time import utc_now
from app.security.interactive import InteractiveApprovalRequest
from app.ui import nova as nova_package
from app.ui.api_settings import APISettingsService
from app.ui.controller import DesktopController
from app.ui.models import ChatMessage
from app.ui.nova import shell

SECRET = "sk-nova-test-secret-0123456789"
PUSH_PREFIX = "window.NOVA && window.NOVA.push("


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def application() -> JARVISApplication:
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


class FakeWindow:
    """Records what Python would evaluate inside the page."""

    def __init__(self) -> None:
        self.scripts: list[str] = []
        self._lock = threading.Lock()

    def evaluate_js(self, script: str) -> None:
        with self._lock:
            self.scripts.append(script)

    def events(self) -> list[tuple[str, object]]:
        with self._lock:
            scripts = list(self.scripts)
        events: list[tuple[str, object]] = []
        for script in scripts:
            assert script.startswith(PUSH_PREFIX) and script.endswith(")")
            event = json.loads(script[len(PUSH_PREFIX) : -1])
            events.append((event["kind"], event["payload"]))
        return events

    def kinds(self) -> list[str]:
        return [kind for kind, _ in self.events()]

    def payloads(self, kind: str) -> list[object]:
        return [payload for event, payload in self.events() if event == kind]


def wait_until(predicate, timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.01)
    raise AssertionError("condition was not met in time")


@dataclass
class MemoryCredentialStore:
    value: str | None = None

    def read(self) -> str | None:
        return self.value

    def write(self, secret: str) -> None:
        self.value = secret

    def delete(self) -> bool:
        existed = self.value is not None
        self.value = None
        return existed


def settings_service(tmp_path, secret: str | None = SECRET):
    store = MemoryCredentialStore(secret)
    service = APISettingsService(
        credentials=store,
        preferences=ProviderPreferencesStore(tmp_path / "prefs.json"),
        client_factory=lambda **_kwargs: None,
    )
    return service, store


def approval_request(seconds: float = 30.0) -> InteractiveApprovalRequest:
    return InteractiveApprovalRequest(
        operation_id=uuid4(),
        request_id=uuid4(),
        conversation_id=uuid4(),
        request_source="text",
        tool_name="fs.write",
        operation="write_file",
        risk_level=RiskLevel.HIGH,
        reason="Dosya yazılacak.",
        parameters={
            "path": "C:/tmp/x.txt",
            "api_key": "sk-hidden-value",
            "content": "x" * 300,
        },
        expires_at=utc_now() + timedelta(seconds=seconds),
    )


class FakeVoice:
    def __init__(self, *, fail: bool = False) -> None:
        self.state_callback = None
        self.stop = threading.Event()
        self.interrupts = 0
        self.fail = fail

    async def run_continuous(
        self,
        *,
        max_turns,
        context,
        max_consecutive_failures,
        result_callback,
        approval_callback=None,
    ):
        if self.fail:
            raise RuntimeError("mikrofon yok")
        if self.state_callback is not None:
            self.state_callback(SimpleNamespace(value="listening"))
        result_callback(
            SimpleNamespace(
                transcript="Jarvis, saat kaç?",
                response_text="Saat üç.",
                metadata={},
                error_code=None,
            )
        )
        while not self.stop.is_set():
            await asyncio.sleep(0.01)
        return ()

    async def interrupt_active(self) -> bool:
        self.interrupts += 1
        self.stop.set()
        return True


@pytest.fixture
def booted(tmp_path):
    app = application()
    controller = DesktopController(app)
    service, store = settings_service(tmp_path)
    bridge = shell.NovaBridge(controller, service)
    window = FakeWindow()
    bridge._attach(window)
    boot = bridge.boot()
    yield SimpleNamespace(
        app=app,
        controller=controller,
        bridge=bridge,
        window=window,
        boot=boot,
        service=service,
        store=store,
    )
    bridge._shutdown()
    controller.close()


# ---------------------------------------------------------------------------
# import & serialization
# ---------------------------------------------------------------------------


def test_importing_nova_opens_no_window() -> None:
    assert nova_package.launch_nova is shell.launch_nova
    assert shell.webview.windows == []


def test_jsonable_is_safe_and_predictable() -> None:
    class Color(Enum):
        RED = "red"

    @dataclass(frozen=True)
    class Point:
        x: int
        y: float

    when = datetime(2026, 9, 5, 12, 0, tzinfo=timezone.utc)
    ident = uuid4()
    value = {
        1: Point(1, 2.5),
        "enum": Color.RED,
        "when": when,
        "uuid": ident,
        "path": PurePath("a") / "b",
        "proxy": MappingProxyType({"k": (1, 2)}),
        "set": frozenset({3}),
        "nan": float("nan"),
        "inf": float("inf"),
        "bytes": b"\x00\x01",
        "obj": object(),
        "none": None,
        "bool": True,
        "nested": [{"deep": {"deeper": (Color.RED,)}}],
    }

    out = shell._jsonable(value)

    assert out["1"] == {"x": 1, "y": 2.5}
    assert out["enum"] == "red"
    assert out["when"] == when.isoformat()
    assert out["uuid"] == str(ident)
    assert out["path"] == str(PurePath("a") / "b")
    assert out["proxy"] == {"k": [1, 2]}
    assert out["set"] == [3]
    assert out["nan"] is None
    assert out["inf"] is None
    assert out["bytes"] == "<2 bayt>"
    assert isinstance(out["obj"], str)
    assert out["none"] is None
    assert out["bool"] is True
    assert out["nested"] == [{"deep": {"deeper": ["red"]}}]
    json.dumps(out)
    assert shell._jsonable(out) == out


# ---------------------------------------------------------------------------
# boot & settings
# ---------------------------------------------------------------------------


def test_boot_returns_live_state_without_secrets(booted) -> None:
    boot = booted.boot

    assert boot["snapshot"]["provider"] == "mock"
    assert boot["snapshot"]["model"] == "mock-model"
    assert boot["status"] == "LOCAL CORE READY"
    assert boot["messages"] == []
    assert boot["voiceMessages"] == []
    assert boot["settings"] == {
        "provider": "gemini",
        "model": DEFAULT_GEMINI_MODEL,
        "credential_configured": True,
        "credential_required": True,
    }
    assert SECRET not in json.dumps(boot)
    assert SECRET not in json.dumps(booted.bridge.refresh())
    assert SECRET not in json.dumps(booted.bridge.get_settings())


def test_boot_reflects_real_memory_tasks_and_history(tmp_path) -> None:
    app = application()
    app.memory_manager.remember("Ali kısa raporları tercih ediyor.")
    app.task_manager.create("Nova kabuğunu doğrula")
    controller = DesktopController(app)
    controller.state.messages.append(ChatMessage("assistant", "Önceki yanıt"))
    bridge = shell.NovaBridge(controller, None)
    bridge._attach(FakeWindow())
    try:
        boot = bridge.boot()
    finally:
        bridge._shutdown()
        controller.close()

    assert boot["settings"] is None
    assert boot["snapshot"]["memory_count"] == 1
    assert boot["snapshot"]["task_count"] == 1
    assert boot["snapshot"]["tasks"][0]["goal"] == "Nova kabuğunu doğrula"
    assert boot["snapshot"]["tool_count"] == boot["snapshot"]["enabled_tools"] > 0
    assert boot["messages"] == [
        {"role": "assistant", "text": "Önceki yanıt", "metadata": {}}
    ]


def test_settings_snapshot_carries_only_non_secret_fields(booted) -> None:
    settings = booted.bridge.get_settings()

    assert set(settings) == {
        "provider",
        "model",
        "credential_configured",
        "credential_required",
    }
    assert settings["credential_configured"] is True


def test_delete_api_key_requires_explicit_confirmation(booted, monkeypatch) -> None:
    monkeypatch.setattr(
        "app.bootstrap.create_application", lambda settings=None: application()
    )

    assert booted.bridge.delete_api_key() == {
        "ok": False,
        "error": "Silme işlemi onaylanmadı.",
    }
    assert booted.bridge.delete_api_key("true")["ok"] is False
    assert booted.bridge.delete_api_key(1)["ok"] is False
    assert booted.store.read() == SECRET

    result = booted.bridge.delete_api_key(True)

    assert result["ok"] is True
    assert booted.store.read() is None
    assert result["settings"]["credential_configured"] is False
    assert SECRET not in json.dumps(result)
    assert "snapshot" in booted.window.kinds()


def test_save_settings_keeps_secret_out_of_preferences_and_replies(
    booted, monkeypatch, tmp_path
) -> None:
    monkeypatch.setattr(
        "app.bootstrap.create_application", lambda settings=None: application()
    )

    result = booted.bridge.save_settings(
        "gemini", "gemini-3.5-flash-lite", "  sk-new-secret-value  "
    )

    assert result["ok"] is True
    assert result["settings"]["model"] == "gemini-3.5-flash-lite"
    assert booted.store.read() == "sk-new-secret-value"
    assert "sk-new-secret-value" not in json.dumps(result)
    assert "sk-new-secret-value" not in (tmp_path / "prefs.json").read_text(
        encoding="utf-8"
    )


def test_settings_methods_report_missing_service(tmp_path) -> None:
    controller = DesktopController(application())
    bridge = shell.NovaBridge(controller, None)
    try:
        assert bridge.get_settings() is None
        assert bridge.save_settings("gemini", "m", "k")["ok"] is False
        assert bridge.test_connection("gemini", "m", "k")["ok"] is False
        assert bridge.delete_api_key(True)["ok"] is False
    finally:
        controller.close()


# ---------------------------------------------------------------------------
# chat
# ---------------------------------------------------------------------------


def test_empty_command_is_rejected_before_reaching_the_core(booted) -> None:
    assert booted.bridge.submit_command("   ") == {
        "ok": False,
        "error": "Komut boş olamaz.",
    }
    assert booted.bridge.submit_command(None)["ok"] is False
    assert booted.controller._runner is None
    assert booted.window.events() == []


def test_second_command_is_rejected_while_the_first_runs(booted) -> None:
    release = threading.Event()

    class GatedEngine:
        async def handle(self, request, context, **_kwargs):
            while not release.is_set():
                await asyncio.sleep(0.01)
            return Response("Bitti: " + request.text, request_id=request.request_id)

    booted.app.engine = GatedEngine()

    assert booted.bridge.submit_command("ilk") == {"ok": True}
    second = booted.bridge.submit_command("ikinci")
    assert second["ok"] is False
    assert "başka bir istek" in second["error"]

    release.set()
    # "snapshot" is the last event the completion callback pushes; waiting
    # for "busy" alone races the final push (seen once on CI).
    wait_until(lambda: "snapshot" in booted.window.kinds())

    kinds = booted.window.kinds()
    assert kinds.index("reply") < kinds.index("busy") < kinds.index("snapshot")
    reply = booted.window.payloads("reply")[0]
    assert reply["role"] == "assistant"
    assert reply["text"] == "Bitti: ilk"
    assert booted.window.payloads("busy") == [
        {"busy": False, "status": "LOCAL CORE READY"}
    ]
    assert booted.controller.state.busy is False
    assert [m.role for m in booted.controller.state.messages] == [
        "user",
        "assistant",
    ]

    # The guard releases once the first command has finished.
    assert booted.bridge.submit_command("üçüncü") == {"ok": True}
    wait_until(lambda: booted.window.kinds().count("snapshot") == 2)


def test_streamed_chunks_reach_the_page_in_order(booted) -> None:
    class StreamingEngine:
        async def handle(self, request, context, *, stream_callback=None, **_):
            for chunk in ("Mer", "ha", "ba"):
                stream_callback(chunk)
                await asyncio.sleep(0.06)
            return Response("Merhaba", request_id=request.request_id)

    booted.app.engine = StreamingEngine()

    assert booted.bridge.submit_command("selam") == {"ok": True}
    wait_until(lambda: "busy" in booted.window.kinds())

    kinds = booted.window.kinds()
    streamed = "".join(p["text"] for p in booted.window.payloads("stream"))
    assert streamed == "Merhaba"
    assert kinds.index("stream") < kinds.index("reply")


def test_core_failure_is_reported_as_a_system_message(booted) -> None:
    class BrokenEngine:
        async def handle(self, request, context, **_kwargs):
            raise RuntimeError("çok gizli ayrıntı")

    booted.app.engine = BrokenEngine()

    assert booted.bridge.submit_command("patla") == {"ok": True}
    wait_until(lambda: "busy" in booted.window.kinds())

    reply = booted.window.payloads("reply")[0]
    assert reply["role"] == "system"
    assert "RuntimeError" in reply["text"]
    assert "çok gizli ayrıntı" not in json.dumps(booted.window.events())


# ---------------------------------------------------------------------------
# voice
# ---------------------------------------------------------------------------


def test_voice_session_start_and_stop_flow(booted) -> None:
    voice = FakeVoice()
    booted.app.voice = voice

    assert booted.bridge.start_voice() == {"ok": True}
    assert booted.bridge.start_voice() == {
        "ok": False,
        "error": "Sesli oturum zaten açık.",
    }
    wait_until(lambda: len(booted.window.payloads("voice_message")) == 2)

    assert {"phase": "listening"} in booted.window.payloads("voice_phase")
    roles = [m["role"] for m in booted.window.payloads("voice_message")]
    assert roles == ["user", "assistant"]

    assert booted.bridge.stop_voice() == {"ok": True}
    wait_until(
        lambda: {"active": False, "error": None}
        in booted.window.payloads("voice_state")
    )

    assert voice.interrupts == 1
    assert voice.state_callback is None
    assert booted.bridge._voice_future is None
    assert booted.bridge.stop_voice()["ok"] is False
    assert [m.role for m in booted.controller.state.voice_messages] == [
        "user",
        "assistant",
    ]
    # Voice and text share one conversation.
    assert [m.role for m in booted.controller.state.messages] == [
        "user",
        "assistant",
    ]


def test_voice_failure_is_reported_honestly(booted) -> None:
    booted.app.voice = FakeVoice(fail=True)

    assert booted.bridge.start_voice() == {"ok": True}
    wait_until(lambda: booted.window.payloads("voice_state") != [])

    assert booted.window.payloads("voice_state") == [
        {"active": False, "error": "Sesli oturum sonlandı (RuntimeError)."}
    ]
    assert "mikrofon yok" not in json.dumps(booted.window.events())
    assert booted.bridge.start_voice() == {"ok": True}  # can start again


def test_voice_unavailable_is_reported(booted) -> None:
    booted.app.voice = None

    assert booted.bridge.start_voice() == {
        "ok": False,
        "error": "Sesli iletişim ayarlanmamış.",
    }
    assert booted.bridge.stop_voice() == {
        "ok": False,
        "error": "Açık sesli oturum yok.",
    }


# ---------------------------------------------------------------------------
# vision & research
# ---------------------------------------------------------------------------


def test_vision_failure_reaches_the_page_without_internals(booted) -> None:
    class FailingVision:
        def request_consent(self, purpose):
            raise RuntimeError("hassas ayrıntı")

    booted.app.vision = FailingVision()

    assert booted.bridge.run_vision("") == {"ok": True}
    wait_until(lambda: booted.window.payloads("vision_result") != [])

    assert booted.window.payloads("vision_result") == [
        {"ok": False, "error": "Analiz başarısız (RuntimeError)."}
    ]
    assert "hassas ayrıntı" not in json.dumps(booted.window.events())


def test_vision_unavailable_is_reported(booted) -> None:
    booted.app.vision = None
    assert booted.bridge.run_vision("x") == {
        "ok": False,
        "error": "Görüş özelliği ayarlanmamış.",
    }


def test_research_failure_and_bounds(booted) -> None:
    class RecordingResearch:
        def __init__(self) -> None:
            self.calls: list[int] = []
            self.fail = False

        def research(self, query, *, max_sources):
            self.calls.append(max_sources)
            if self.fail:
                raise ValueError("gizli hata")
            return SimpleNamespace(
                to_dict=lambda: {"query": query, "summary": "ok", "sources": []}
            )

    research = RecordingResearch()
    booted.app.research = research

    assert booted.bridge.run_research("   ") == {
        "ok": False,
        "error": "Araştırma sorgusu boş olamaz.",
    }
    assert booted.bridge.run_research("q", 99) == {"ok": True}
    wait_until(lambda: len(booted.window.payloads("research_result")) == 1)
    assert booted.bridge.run_research("q", 0) == {"ok": True}
    wait_until(lambda: len(booted.window.payloads("research_result")) == 2)
    assert booted.bridge.run_research("q", "abc") == {"ok": True}
    wait_until(lambda: len(booted.window.payloads("research_result")) == 3)
    assert research.calls == [10, 1, 5]
    assert booted.window.payloads("research_result")[0] == {
        "ok": True,
        "report": {"query": "q", "summary": "ok", "sources": []},
    }

    research.fail = True
    assert booted.bridge.run_research("q") == {"ok": True}
    wait_until(lambda: len(booted.window.payloads("research_result")) == 4)
    assert booted.window.payloads("research_result")[-1] == {
        "ok": False,
        "error": "Araştırma başarısız (ValueError).",
    }
    assert "gizli hata" not in json.dumps(booted.window.events())

    booted.app.research = None
    assert booted.bridge.run_research("q")["ok"] is False


# ---------------------------------------------------------------------------
# approvals
# ---------------------------------------------------------------------------


def test_approval_is_token_bound_and_single_use(booted) -> None:
    future = booted.controller.submit_background(
        booted.bridge._request_approval(approval_request()), lambda f: None
    )
    wait_until(lambda: booted.window.payloads("approval") != [])
    payload = booted.window.payloads("approval")[0]

    assert payload["tool"] == "fs.write"
    assert payload["operation"] == "write_file"
    assert payload["risk"] == "high"
    assert payload["parameters"]["api_key"] == "<gizli>"
    assert payload["parameters"]["content"] == "<300 karakter>"
    assert payload["parameters"]["path"] == "C:/tmp/x.txt"
    assert payload["seconds"] >= 5
    assert "sk-hidden-value" not in json.dumps(payload)

    assert booted.bridge.resolve_approval("not-a-token", True)["ok"] is False
    assert booted.bridge.resolve_approval(payload["token"], True) == {"ok": True}
    assert future.result(timeout=5) is True
    assert booted.bridge.resolve_approval(payload["token"], True)["ok"] is False
    wait_until(
        lambda: {"token": payload["token"]}
        in booted.window.payloads("approval_closed")
    )
    assert booted.bridge._approvals == {}


def test_approval_only_accepts_a_real_boolean_yes(booted) -> None:
    future = booted.controller.submit_background(
        booted.bridge._request_approval(approval_request()), lambda f: None
    )
    wait_until(lambda: booted.window.payloads("approval") != [])
    token = booted.window.payloads("approval")[0]["token"]

    assert booted.bridge.resolve_approval(token, "yes") == {"ok": True}
    assert future.result(timeout=5) is False


def test_approval_times_out_as_denied(booted, monkeypatch) -> None:
    monkeypatch.setattr(shell, "APPROVAL_MINIMUM_WAIT_SECONDS", 0.05)
    future = booted.controller.submit_background(
        booted.bridge._request_approval(approval_request(seconds=1)),
        lambda f: None,
    )

    assert future.result(timeout=5) is False
    wait_until(lambda: booted.window.payloads("approval_closed") != [])
    assert booted.bridge._approvals == {}


def test_approval_fails_closed_without_a_ready_page() -> None:
    controller = DesktopController(application())
    bridge = shell.NovaBridge(controller, None)
    window = FakeWindow()
    bridge._attach(window)
    try:
        # Not booted: the page cannot show anything, so the answer is "no".
        assert asyncio.run(bridge._request_approval(approval_request())) is False
        assert window.events() == []
        assert controller.approval_callback == bridge._request_approval
    finally:
        controller.close()


def test_shutdown_denies_pending_approvals_and_silences_pushes(booted) -> None:
    future = booted.controller.submit_background(
        booted.bridge._request_approval(approval_request()), lambda f: None
    )
    wait_until(lambda: booted.window.payloads("approval") != [])
    booted.app.voice = FakeVoice()
    assert booted.bridge.start_voice() == {"ok": True}

    booted.bridge._shutdown()

    assert future.result(timeout=5) is False
    assert booted.bridge._approvals == {}
    assert booted.bridge._voice_future is None
    before = len(booted.window.scripts)
    booted.bridge._push("snapshot", {"ignored": True})
    assert len(booted.window.scripts) == before
    assert booted.bridge.submit_command("x") == {
        "ok": False,
        "error": "JARVIS kapanıyor.",
    }
    assert booted.bridge.start_voice() == {"ok": False, "error": "JARVIS kapanıyor."}


# ---------------------------------------------------------------------------
# assets, launch and cleanup
# ---------------------------------------------------------------------------


def test_source_web_root_contains_every_asset() -> None:
    root = shell.resolve_web_root()
    assert root == shell.SOURCE_WEB_ROOT
    for name in shell.WEB_ASSETS:
        assert (root / name).is_file()


def test_frozen_bundle_web_root_is_preferred(monkeypatch, tmp_path) -> None:
    bundle = tmp_path / "bundle"
    target = bundle / "app" / "ui" / "nova" / "web"
    target.mkdir(parents=True)
    for name in shell.WEB_ASSETS:
        (target / name).write_text("bundled", encoding="utf-8")
    monkeypatch.setattr(sys, "_MEIPASS", str(bundle), raising=False)

    assert shell.resolve_web_root() == target


def test_missing_assets_are_reported_explicitly(monkeypatch, tmp_path) -> None:
    incomplete = tmp_path / "bundle" / "app" / "ui" / "nova" / "web"
    incomplete.mkdir(parents=True)
    (incomplete / "index.html").write_text("only html", encoding="utf-8")
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path / "bundle"), raising=False)
    monkeypatch.setattr(shell, "SOURCE_WEB_ROOT", tmp_path / "nowhere")

    with pytest.raises(FileNotFoundError, match="nova.css"):
        shell.resolve_web_root()


def test_webview_storage_lives_in_the_state_directory(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("JARVIS_STATE_DIRECTORY", str(tmp_path / "state"))
    assert shell.webview_storage_directory() == (tmp_path / "state" / "webview").resolve()


class _Hook:
    def __init__(self, sink: list) -> None:
        self.sink = sink

    def __iadd__(self, handler):
        self.sink.append(handler)
        return self


def test_launch_nova_releases_resources_exactly_once(monkeypatch, tmp_path) -> None:
    app = application()
    controller = DesktopController(app)
    closed: list[object] = []
    original_close = JARVISApplication.close
    monkeypatch.setattr(
        JARVISApplication,
        "close",
        lambda self: (closed.append(self), original_close(self)),
    )
    handlers: list = []
    created: dict[str, object] = {}
    fake_window = SimpleNamespace(
        events=SimpleNamespace(closed=_Hook(handlers), closing=_Hook([])),
        evaluate_js=lambda script: None,
        hide=lambda: None,
        show=lambda: None,
        destroy=lambda: None,
    )

    def create_window(title, **kwargs):
        created.update(kwargs, title=title)
        return fake_window

    def start(**options):
        created["start_options"] = options
        # A background operation is still running when the window closes.
        controller.submit_background(asyncio.sleep(60), lambda f: None)
        for handler in handlers:
            handler()

    monkeypatch.setattr(shell.webview, "create_window", create_window)
    monkeypatch.setattr(shell.webview, "start", start)
    monkeypatch.setattr(
        shell, "webview_storage_directory", lambda: tmp_path / "webview"
    )
    tray_events: list[str] = []

    class FakeTrayBackend:
        def __init__(self, tray_controller, icon_path) -> None:
            tray_events.append("created")

        def start(self) -> None:
            tray_events.append("start")

        def refresh(self) -> None:
            tray_events.append("refresh")

        def notify(self, title, text) -> None:
            tray_events.append("notify")

        def stop(self) -> None:
            tray_events.append("stop")

    monkeypatch.setattr(
        shell, "default_backend_factory", lambda: FakeTrayBackend
    )

    shell.launch_nova(controller, None)

    assert created["title"] == "JARVIS"
    assert str(created["url"]).endswith("index.html")
    assert "?" not in str(created["url"])
    assert isinstance(created["js_api"], shell.NovaBridge)
    assert created["start_options"] == {
        "debug": False,
        "private_mode": False,
        "storage_path": str(tmp_path / "webview"),
    }
    assert (tmp_path / "webview").is_dir()
    assert controller._runner is None
    assert closed == [app]
    assert created["js_api"]._closing is True
    # The tray icon lives exactly as long as the window.
    assert tray_events[:2] == ["created", "start"]
    assert tray_events[-1] == "stop"


def test_launch_nova_reports_missing_assets_before_opening(monkeypatch) -> None:
    controller = DesktopController(application())
    monkeypatch.setattr(sys, "_MEIPASS", "C:/definitely/missing", raising=False)
    monkeypatch.setattr(shell, "SOURCE_WEB_ROOT", shell.SOURCE_WEB_ROOT / "missing")
    monkeypatch.setattr(
        shell.webview,
        "create_window",
        lambda *a, **k: pytest.fail("window must not be created"),
    )
    try:
        with pytest.raises(FileNotFoundError):
            shell.launch_nova(controller, None)
    finally:
        controller.close()


# ---------------------------------------------------------------------------
# desktop entry points
# ---------------------------------------------------------------------------


@pytest.fixture
def desktop_entry(monkeypatch):
    import app.ui.desktop as desktop

    calls: list[tuple[str, object]] = []
    monkeypatch.setattr(desktop, "enable_high_dpi_rendering", lambda: None)
    monkeypatch.setattr(
        desktop,
        "create_api_settings_service",
        lambda: SimpleNamespace(build_runtime_settings=lambda: None),
    )
    monkeypatch.setattr(
        "app.bootstrap.create_application", lambda settings=None: application()
    )

    class FakeClassicWindow:
        def __init__(self, controller, api_settings=None) -> None:
            calls.append(("classic", controller))

        def run(self) -> None:
            calls.append(("classic-run", None))

    monkeypatch.setattr(desktop, "DesktopWindow", FakeClassicWindow)
    monkeypatch.setattr(
        nova_package,
        "launch_nova",
        lambda controller, api_settings=None, **kwargs: calls.append(("nova", controller)),
    )
    monkeypatch.setattr(
        nova_package, "detect_webview2_runtime", lambda: "test-runtime"
    )
    return desktop, calls


def test_launch_desktop_prefers_nova(desktop_entry) -> None:
    desktop, calls = desktop_entry
    desktop.launch_desktop(classic=False)
    assert [name for name, _ in calls] == ["nova"]
    assert isinstance(calls[0][1], DesktopController)


def test_launch_desktop_honours_explicit_classic_request(desktop_entry) -> None:
    desktop, calls = desktop_entry
    desktop.launch_desktop(classic=True)
    assert [name for name, _ in calls] == ["classic", "classic-run"]


def test_launch_desktop_reads_classic_flag_from_argv(desktop_entry, monkeypatch) -> None:
    desktop, calls = desktop_entry
    monkeypatch.setattr(sys, "argv", ["jarvis", "--classic"])
    desktop.launch_desktop()
    assert [name for name, _ in calls] == ["classic", "classic-run"]


def test_launch_desktop_falls_back_when_nova_cannot_import(
    desktop_entry, monkeypatch, capsys
) -> None:
    desktop, calls = desktop_entry
    monkeypatch.setitem(sys.modules, "app.ui.nova", None)
    desktop.launch_desktop(classic=False)
    assert [name for name, _ in calls] == ["classic", "classic-run"]
    assert "Nova shell unavailable" in capsys.readouterr().err


def test_launch_desktop_falls_back_when_webview2_runtime_is_missing(
    desktop_entry, monkeypatch, capsys
) -> None:
    desktop, calls = desktop_entry
    monkeypatch.setattr(nova_package, "detect_webview2_runtime", lambda: None)

    desktop.launch_desktop(classic=False)

    assert [name for name, _ in calls] == ["classic", "classic-run"]
    controller = calls[0][1]
    notice = controller.state.messages[-1]
    assert notice.role == "system"
    assert "WebView2" in notice.text and "Klasik arayüz" in notice.text
    events = controller.application.diagnostics.events(component="ui")["events"]
    assert any(
        event["name"] == "nova.unavailable" and event["level"] == "warning"
        for event in events
    )
    assert "webview2_runtime_missing" in capsys.readouterr().err


def test_detect_webview2_runtime_never_raises() -> None:
    version = shell.detect_webview2_runtime()
    assert version is None or (isinstance(version, str) and version)


def test_detection_prefers_the_loader_then_the_registry(monkeypatch) -> None:
    monkeypatch.setattr(shell, "_webview2_loader_path", lambda: None)
    monkeypatch.setattr(shell, "_registry_webview2_version", lambda: "1.2.3")
    assert shell.detect_webview2_runtime() == "1.2.3"

    monkeypatch.setattr(shell, "_registry_webview2_version", lambda: None)
    assert shell.detect_webview2_runtime() is None


def test_detection_survives_a_broken_loader(monkeypatch, tmp_path) -> None:
    bogus = tmp_path / "WebView2Loader.dll"
    bogus.write_bytes(b"not a library")
    monkeypatch.setattr(shell, "_webview2_loader_path", lambda: bogus)
    monkeypatch.setattr(shell, "_registry_webview2_version", lambda: None)

    assert shell.detect_webview2_runtime() is None


def test_packaged_entrypoint_accepts_classic_flag(monkeypatch, tmp_path) -> None:
    import installer.entrypoint as entrypoint

    calls: list[object] = []
    monkeypatch.setattr(
        "app.ui.desktop.launch_desktop",
        lambda *, classic=None: calls.append(classic),
    )
    state = str(tmp_path / "state")

    assert entrypoint.main(["--classic", "--state-dir", state]) == 0
    assert entrypoint.main(["--state-dir", state]) == 0
    assert calls == [True, False]


# ---------------------------------------------------------------------------
# regression: completion callbacks that fire synchronously
# ---------------------------------------------------------------------------


def _immediate_submit_background(self, operation, callback):
    """Mimic concurrent.futures when the work finishes before the callback
    is registered: the callback then runs synchronously on the caller's
    thread, while submit_command/start_voice still hold the bridge lock."""
    from concurrent.futures import Future as ConcurrentFuture

    future: ConcurrentFuture = ConcurrentFuture()
    try:
        result = asyncio.run(operation)
    except BaseException as exc:  # noqa: BLE001 - mirrors the real runner
        future.set_exception(exc)
    else:
        future.set_result(result)
    callback(future)
    return future


def _run_with_deadline(function, timeout: float = 5.0):
    outcome: dict[str, object] = {}

    def target() -> None:
        try:
            outcome["result"] = function()
        except BaseException as exc:  # noqa: BLE001
            outcome["error"] = exc

    worker = threading.Thread(target=target, daemon=True)
    worker.start()
    worker.join(timeout)
    assert not worker.is_alive(), "bridge call deadlocked"
    if "error" in outcome:
        raise outcome["error"]
    return outcome["result"]


def test_command_callback_firing_synchronously_does_not_deadlock(
    booted, monkeypatch
) -> None:
    class InstantEngine:
        async def handle(self, request, context, **_kwargs):
            return Response("anında", request_id=request.request_id)

    booted.app.engine = InstantEngine()
    monkeypatch.setattr(
        DesktopController, "submit_background", _immediate_submit_background
    )

    assert _run_with_deadline(lambda: booted.bridge.submit_command("hızlı")) == {
        "ok": True
    }
    assert booted.window.payloads("reply")[0]["text"] == "anında"
    assert booted.bridge._command_future is not None
    assert booted.bridge._command_future.done()
    # The busy guard is released, so the next command is accepted.
    assert _run_with_deadline(lambda: booted.bridge.submit_command("yine")) == {
        "ok": True
    }


def test_voice_callback_firing_synchronously_does_not_deadlock(
    booted, monkeypatch
) -> None:
    booted.app.voice = FakeVoice(fail=True)
    monkeypatch.setattr(
        DesktopController, "submit_background", _immediate_submit_background
    )

    assert _run_with_deadline(lambda: booted.bridge.start_voice()) == {"ok": True}
    assert booted.window.payloads("voice_state") == [
        {"active": False, "error": "Sesli oturum sonlandı (RuntimeError)."}
    ]
    assert booted.bridge._voice_future is not None
    assert booted.bridge._voice_future.done()
    assert _run_with_deadline(lambda: booted.bridge.start_voice()) == {"ok": True}
