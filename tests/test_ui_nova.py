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
from concurrent.futures import Future
from dataclasses import dataclass, replace
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
from app.conversation.models import ConversationTurn, MessageRole
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
    assert booted.bridge._command_future is None
    # Boot's own warm-up ledger line is the only thing the page may hear.
    assert [kind for kind in booted.window.kinds() if kind != "diagnostic_event"] == []


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
        (target / name).parent.mkdir(parents=True, exist_ok=True)
        (target / name).write_text("bundled", encoding="utf-8")
    monkeypatch.setattr(sys, "_MEIPASS", str(bundle), raising=False)

    assert shell.resolve_web_root() == target


def test_missing_assets_are_reported_explicitly(monkeypatch, tmp_path) -> None:
    incomplete = tmp_path / "bundle" / "app" / "ui" / "nova" / "web"
    incomplete.mkdir(parents=True)
    (incomplete / "index.html").write_text("only html", encoding="utf-8")
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path / "bundle"), raising=False)
    monkeypatch.setattr(shell, "SOURCE_WEB_ROOT", tmp_path / "nowhere")

    with pytest.raises(FileNotFoundError, match="css/tokens.css"):
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


# ---------------------------------------------------------------------------
# cinematic shell: runtime facts, observation, conversations, memory, status
# ---------------------------------------------------------------------------


def test_boot_carries_runtime_facts_without_secrets(tmp_path) -> None:
    app = create_application(
        Settings(
            default_provider="mock",
            default_model="mock-model",
            windows_integrations_enabled=False,
            memory_database_path=None,
            task_database_path=None,
            task_runtime_directory=None,
            gemini_api_key="sk-runtime-secret-9876543210",
        )
    )
    controller = DesktopController(app)
    bridge = shell.NovaBridge(controller, None)
    bridge._attach(FakeWindow())
    try:
        boot = bridge.boot()
    finally:
        bridge._shutdown()
        controller.close()

    runtime = boot["runtime"]
    assert set(runtime["configuration"]) <= set(shell.RUNTIME_SETTING_FIELDS)
    assert runtime["configuration"]["voice_wake_word"] == "jarvis"
    assert runtime["python"] and runtime["platform"]
    assert runtime["conversation_id"] == str(controller.context.conversation_id)
    assert runtime["applications"] == []
    serialized = json.dumps(boot)
    assert "sk-runtime-secret" not in serialized
    for field in shell.SECRET_SETTING_FIELDS:
        assert field not in serialized, field
    assert boot["paused"] is False and boot["compact"] is False
    assert boot["conversations"] == []


def test_tool_executions_are_pushed_as_activity(booted) -> None:
    result = booted.app.tool_executor.execute("list_memories")

    assert result.status.value == "success"
    payloads = booted.window.payloads("tool_activity")
    assert [item["phase"] for item in payloads] == ["started", "finished"]
    assert payloads[0]["tool"] == "list_memories"
    assert payloads[0]["execution_id"] == payloads[1]["execution_id"]
    assert payloads[1]["status"] == "success"
    assert payloads[1]["verified"] is True
    assert payloads[1]["failed"] is False
    assert isinstance(payloads[1]["duration_ms"], int)
    assert "data" not in payloads[1]


def test_diagnostic_events_are_pushed_live(booted) -> None:
    booted.app.diagnostics.record(
        "ui", "unit.event", "merhaba", attributes={"api_key": "sk-very-secret"}
    )

    payloads = booted.window.payloads("diagnostic_event")
    assert payloads[-1]["name"] == "unit.event"
    assert payloads[-1]["component"] == "ui"
    assert payloads[-1]["level"] == "info"
    assert payloads[-1]["message"] == "merhaba"
    assert "sk-very-secret" not in json.dumps(payloads)


def test_observers_follow_a_runtime_rebuild(booted, monkeypatch) -> None:
    replacement = application()
    monkeypatch.setattr(
        "app.bootstrap.create_application", lambda settings=None: replacement
    )
    previous = booted.app

    assert booted.bridge.save_settings("gemini", DEFAULT_GEMINI_MODEL, "")["ok"] is True
    assert booted.controller.application is replacement

    before = len(booted.window.payloads("tool_activity"))
    replacement.tool_executor.execute("list_memories")
    assert len(booted.window.payloads("tool_activity")) == before + 2
    previous.tool_executor.execute("list_memories")
    assert len(booted.window.payloads("tool_activity")) == before + 2


def test_conversation_lifecycle_through_the_bridge(booted) -> None:
    engine = booted.app.conversation_engine
    stored = engine.create()
    first = stored.conversation_id
    stored.turns.append(ConversationTurn(first, MessageRole.USER, "merhaba  dünya"))
    stored.turns.append(ConversationTurn(first, MessageRole.ASSISTANT, "Merhaba."))
    stored.turns.append(ConversationTurn(first, MessageRole.SYSTEM, "sistem notu"))
    engine.store.save(stored)

    opened = booted.bridge.open_conversation(str(first))
    assert opened["ok"] is True and opened["conversation_id"] == str(first)
    assert [item["role"] for item in opened["messages"]] == ["user", "assistant"]
    assert booted.controller.context.conversation_id == first

    listing = booted.bridge.list_conversations()
    assert listing["ok"] is True and listing["active"] == str(first)
    assert listing["conversations"][0]["title"] == "merhaba dünya"
    assert listing["conversations"][0]["turn_count"] == 2
    assert listing["conversations"][0]["active"] is True

    fresh = booted.bridge.new_conversation()
    assert fresh["ok"] is True and fresh["messages"] == []
    assert fresh["conversation_id"] != str(first)
    assert booted.controller.state.messages == []
    assert booted.bridge.list_conversations()["active"] == fresh["conversation_id"]
    assert all(
        not item["active"] for item in booted.bridge.list_conversations()["conversations"]
    )

    assert booted.bridge.open_conversation(str(first))["ok"] is True
    assert booted.controller.context.conversation_id == first

    assert booted.bridge.open_conversation("not-a-uuid") == {
        "ok": False,
        "error": "Konuşma kimliği geçersiz.",
    }
    assert booted.bridge.open_conversation(str(uuid4())) == {
        "ok": False,
        "error": "Konuşma bulunamadı.",
    }

    archived = booted.bridge.archive_conversation(str(first))
    assert archived["ok"] is True and archived["current_archived"] is True
    assert booted.controller.context.conversation_id != first
    assert booted.controller.state.messages == []
    assert booted.bridge.list_conversations()["conversations"][0]["status"] == "archived"


def test_conversation_switches_are_refused_while_busy(booted) -> None:
    pending: Future = Future()
    booted.bridge._command_future = pending
    try:
        assert booted.bridge.new_conversation() == {
            "ok": False,
            "error": "Yanıt tamamlanmadan konuşma değiştirilemez.",
        }
        assert booted.bridge.open_conversation(str(uuid4()))["ok"] is False
    finally:
        booted.bridge._command_future = None


def test_memory_actions_require_confirmation_and_report(booted) -> None:
    entry = booted.app.memory_manager.remember("Ali kısa raporları tercih ediyor.")
    memory_id = str(entry.memory_id)

    listed = booted.bridge.list_memories()
    assert listed["ok"] is True and len(listed["memories"]) == 1
    assert booted.bridge.search_memories("kısa")["memories"][0]["memory_id"] == memory_id
    assert booted.bridge.search_memories("")["ok"] is True

    assert booted.bridge.update_memory(memory_id, "  ") == {
        "ok": False,
        "error": "Anı içeriği boş olamaz.",
    }
    assert booted.bridge.update_memory(memory_id, "Ali uzun raporları tercih ediyor.")["ok"] is True
    assert "uzun" in booted.bridge.list_memories()["memories"][0]["content"]

    assert booted.bridge.delete_memory(memory_id) == {
        "ok": False,
        "error": "Silme işlemi onaylanmadı.",
    }
    assert booted.bridge.forget_memory("not-a-uuid") == {
        "ok": False,
        "error": "Anı kimliği geçersiz.",
    }
    assert booted.bridge.forget_memory(str(uuid4())) == {
        "ok": False,
        "error": "Anı bulunamadı.",
    }
    assert booted.bridge.forget_memory(memory_id)["ok"] is True
    assert booted.bridge.list_memories()["memories"] == []
    assert booted.bridge.delete_memory(memory_id, True)["ok"] is True
    assert booted.bridge.delete_memory(memory_id, True)["ok"] is False
    assert "snapshot" in booted.window.kinds()


def test_system_status_reports_measured_values(booted) -> None:
    status = booted.bridge.system_status()

    assert status["ok"] is True
    assert status["health"]["status"] in {"healthy", "degraded"}
    assert {check["name"] for check in status["health"]["checks"]} >= {"core", "event_ledger"}
    assert status["health_error"] is None
    assert "counters" in status["metrics"] and "timers" in status["metrics"]
    assert status["integrity"] is True
    assert status["event_count"] >= 1
    assert status["provider"]["name"] == "mock"
    assert status["provider"]["circuit"] == "closed"
    assert set(status["admission"]) == {
        "active", "waiting", "accepted", "rejected", "max_concurrent", "max_queue",
    }
    assert status["process"]["threads"] >= 1
    assert status["process"]["uptime_seconds"] >= 0
    time.sleep(0.05)  # a second sample on a later clock tick yields a CPU figure
    again = booted.bridge.system_status()
    assert isinstance(again["process"]["cpu_percent"], float)
    assert 0.0 <= again["process"]["cpu_percent"] <= 100.0
    assert again["process"]["memory_bytes"] is None or again["process"]["memory_bytes"] > 0


def test_diagnostic_events_are_bounded_and_filterable(booted) -> None:
    result = booted.bridge.diagnostic_events(limit=5)

    assert result["ok"] is True and result["integrity_valid"] is True
    assert 1 <= len(result["events"]) <= 5
    assert {"sequence", "component", "name", "level", "message"} <= set(result["events"][0])
    assert booted.bridge.diagnostic_events(level="nope") == {
        "ok": False,
        "error": "Tanılama süzgeci geçersiz.",
    }
    assert len(booted.bridge.diagnostic_events(limit=10_000)["events"]) <= shell.MAX_DIAGNOSTIC_EVENTS


def test_permission_audit_lists_engine_decisions(booted) -> None:
    booted.app.tool_executor.execute("list_memories")

    audit = booted.bridge.permission_audit(5)
    assert audit["ok"] is True and audit["entries"]
    entry = audit["entries"][0]
    assert entry["tool"] == "list_memories"
    assert entry["decision"] in {"allow", "confirm", "deny"}
    assert entry["risk"] and entry["evaluated_at"]


def test_set_paused_from_the_page(booted) -> None:
    assert booted.bridge.set_paused(True) == {"ok": True, "paused": True}
    assert booted.controller.paused is True
    assert booted.window.payloads("paused")[-1] == {"paused": True, "status": "PAUSED"}
    assert booted.bridge.submit_command("x")["ok"] is False

    assert booted.bridge.set_paused(False) == {"ok": True, "paused": False}
    assert booted.controller.paused is False

    handled: list[bool] = []
    booted.bridge._pause_handler = handled.append
    assert booted.bridge.set_paused("yes") == {"ok": True, "paused": False}
    assert handled == [False]


def test_set_compact_resizes_and_pins_the_window(booted, monkeypatch) -> None:
    calls: list[tuple] = []

    class FakeNativeWindow(FakeWindow):
        on_top = False

        def restore(self) -> None:
            calls.append(("restore",))

        def resize(self, width, height) -> None:
            calls.append(("resize", width, height))

        def move(self, x, y) -> None:
            calls.append(("move", x, y))

        def maximize(self) -> None:
            calls.append(("maximize",))

    window = FakeNativeWindow()
    booted.bridge._attach(window)
    monkeypatch.setattr(shell, "_logical_screen_bounds", lambda: (0, 0, 1920, 1080))

    assert booted.bridge.set_compact(True) == {"ok": True, "compact": True}
    width, height = shell.COMPACT_WINDOW_SIZE
    assert calls == [
        ("restore",),
        ("resize", width, height),
        ("move", 1920 - width - shell.COMPACT_WINDOW_MARGIN,
         1080 - height - shell.COMPACT_TASKBAR_ALLOWANCE),
    ]
    assert window.on_top is True
    assert booted.bridge.boot()["compact"] is True

    assert booted.bridge.set_compact(False) == {"ok": True, "compact": False}
    assert calls[-1] == ("maximize",) and window.on_top is False

    def explode(width, height) -> None:
        raise RuntimeError("no window")

    window.resize = explode
    assert booted.bridge.set_compact(True) == {
        "ok": False,
        "error": "Pencere düzeni değiştirilemedi (RuntimeError).",
    }
    assert booted.bridge.boot()["compact"] is False


def test_voice_levels_are_pushed_while_listening(booted) -> None:
    class LevelVoice(FakeVoice):
        def __init__(self) -> None:
            super().__init__()
            self.level_callback = None

        async def run_continuous(self, **kwargs):
            if self.level_callback is not None:
                self.level_callback(0.4)
                self.level_callback(0.9)  # inside the throttle window: dropped
            return await super().run_continuous(**kwargs)

    voice = LevelVoice()
    booted.app.voice = voice

    assert booted.bridge.start_voice() == {"ok": True}
    wait_until(lambda: booted.window.payloads("voice_level") != [])
    assert booted.window.payloads("voice_level")[0] == {"level": 0.4}
    assert voice.level_callback is not None
    assert booted.bridge.stop_voice() == {"ok": True}
    wait_until(lambda: {"active": False, "error": None} in booted.window.payloads("voice_state"))
    assert voice.level_callback is None


def test_approval_payload_carries_source_and_tool_description(booted) -> None:
    request = InteractiveApprovalRequest(
        operation_id=uuid4(),
        request_id=uuid4(),
        conversation_id=uuid4(),
        request_source="voice",
        tool_name="list_memories",
        operation="list_memories",
        risk_level=RiskLevel.LOW,
        reason="Hafıza listelenecek.",
        parameters={},
        expires_at=utc_now() + timedelta(seconds=30),
    )
    future = booted.controller.submit_background(
        booted.bridge._request_approval(request), lambda f: None
    )
    wait_until(lambda: booted.window.payloads("approval") != [])
    payload = booted.window.payloads("approval")[0]

    assert payload["source"] == "voice"
    assert payload["description"]
    assert payload["parameters"] == {}
    assert booted.bridge.resolve_approval(payload["token"], False) == {"ok": True}
    assert future.result(timeout=5) is False


def test_tray_voice_action_starts_the_session_and_reports_failures() -> None:
    app = application()
    controller = DesktopController(app)
    bridge = shell.NovaBridge(controller, None)
    window = FakeWindow()
    bridge._attach(window)
    bridge.boot()
    notices: list[str] = []
    try:
        actions = shell.NovaTrayActions(
            SimpleNamespace(show=lambda: None, hide=lambda: None, destroy=lambda: None, native=None),
            bridge,
            controller,
        )
        actions.service = SimpleNamespace(
            set_window_visible=lambda visible: None,
            notify=lambda title, text: notices.append(text),
            set_paused=lambda paused: None,
        )
        actions.start_voice()
        assert notices == ["Sesli iletişim ayarlanmamış."]
        assert {"screen": "voice"} in window.payloads("navigate")

        app.voice = FakeVoice()
        actions.start_voice()
        assert {"active": True, "error": None} in window.payloads("voice_state")
        assert bridge.stop_voice() == {"ok": True}
        wait_until(lambda: {"active": False, "error": None} in window.payloads("voice_state"))
    finally:
        bridge._shutdown()
        controller.close()


def test_logical_screen_bounds_fall_back_to_pywebview_screens(monkeypatch) -> None:
    monkeypatch.setattr(shell.sys, "platform", "linux")
    monkeypatch.setattr(
        shell.webview,
        "screens",
        [SimpleNamespace(x=10, y=20, width=1536, height=864)],
        raising=False,
    )
    assert shell._logical_screen_bounds() == (10, 20, 1536, 864)
    monkeypatch.setattr(shell.webview, "screens", [], raising=False)
    assert shell._logical_screen_bounds() is None


# ---------------------------------------------------------------------------
# file access: root grants and snapshot restores from the settings screen
# ---------------------------------------------------------------------------


class FakeWindows:
    """The slice of WindowsIntegrationService the bridge relies on."""

    def __init__(self, tmp_path):
        from app.platform.windows.filesystem import BoundedFilesystemService
        from app.platform.windows.snapshots import FilesystemSnapshotStore

        self.grants = {}
        self.filesystem = BoundedFilesystemService(
            snapshots=FilesystemSnapshotStore(tmp_path / "snapshots"),
            critical_paths=(),
        )
        self.fail_with: Exception | None = None

    def filesystem_root_grants(self):
        return tuple(sorted(self.grants.values(), key=lambda item: item.root_id))

    def grant_filesystem_root(self, path):
        if self.fail_with is not None:
            raise self.fail_with
        from pathlib import Path as _Path

        resolved = _Path(path).resolve(strict=True)
        root_id = "root-" + resolved.name.lower()
        grant = SimpleNamespace(root_id=root_id, path=resolved, display_name=resolved.name)
        if root_id not in self.grants:
            self.filesystem.allow_root(root_id, resolved)
        self.grants[root_id] = grant
        return grant

    def revoke_filesystem_root(self, root_id):
        self.filesystem.revoke_root(root_id)
        return self.grants.pop(root_id, None) is not None


def test_file_access_is_reported_unavailable_without_windows(booted) -> None:
    assert booted.boot["fileRoots"] == {"available": False, "roots": []}
    assert booted.bridge.list_file_roots() == {"ok": True, "available": False, "roots": []}
    assert booted.bridge.pick_file_root()["ok"] is False
    assert booted.bridge.grant_file_root("C:/x", True)["ok"] is False
    assert booted.bridge.revoke_file_root("x")["ok"] is False
    assert booted.bridge.list_snapshots()["available"] is False
    assert booted.bridge.restore_snapshot("x", True)["ok"] is False


def test_file_roots_are_granted_and_revoked_with_confirmation(booted, tmp_path) -> None:
    windows = FakeWindows(tmp_path)
    booted.app.windows = windows
    folder = tmp_path / "Belgeler"
    folder.mkdir()

    assert booted.bridge.grant_file_root(str(folder)) == {
        "ok": False,
        "error": "Klasör erişimi onaylanmadı.",
    }
    assert booted.bridge.grant_file_root("   ", True)["error"] == "Klasör yolu boş olamaz."
    granted = booted.bridge.grant_file_root(str(folder), True)

    assert granted["ok"] is True and granted["available"] is True
    assert granted["roots"] == [
        {"root_id": "root-belgeler", "name": "Belgeler", "path": str(folder.resolve())}
    ]
    assert booted.bridge.list_file_roots()["roots"] == granted["roots"]
    events = [event.name for event in booted.app.diagnostics.ledger.list(component="ui", limit=10)]
    assert "filesystem.root_granted" in events
    assert "snapshot" in booted.window.kinds()

    assert booted.bridge.revoke_file_root("root-belgeler") == {
        "ok": True,
        "available": True,
        "roots": [],
    }
    assert booted.bridge.revoke_file_root("root-belgeler")["error"] == (
        "Bu kimlikle bir klasör erişimi yok."
    )

    windows.fail_with = ValueError("A critical system directory cannot be granted.")
    refused = booted.bridge.grant_file_root(str(folder), True)
    assert refused["ok"] is False
    assert refused["error"].startswith("Kritik bir sistem klasörü")


def test_pick_file_root_uses_the_native_folder_dialog(booted, tmp_path) -> None:
    booted.app.windows = FakeWindows(tmp_path)
    calls: list[tuple] = []

    class DialogWindow(FakeWindow):
        native = None
        answer: object = ("C:/Users/Ali/Projeler",)

        def create_file_dialog(self, kind, directory=""):
            calls.append((kind, directory))
            return self.answer

    window = DialogWindow()
    booted.bridge._attach(window)

    assert booted.bridge.pick_file_root() == {"ok": True, "path": "C:/Users/Ali/Projeler"}
    assert calls and calls[0][0] == shell.webview.FOLDER_DIALOG
    window.answer = None
    assert booted.bridge.pick_file_root() == {"ok": True, "path": None}

    def explode(kind, directory=""):
        raise RuntimeError("no dialog")

    window.create_file_dialog = explode
    assert booted.bridge.pick_file_root() == {
        "ok": False,
        "error": "Klasör seçici açılamadı (RuntimeError).",
    }


def test_snapshots_are_listed_and_restored_with_confirmation(booted, tmp_path) -> None:
    windows = FakeWindows(tmp_path)
    booted.app.windows = windows
    folder = tmp_path / "Notlar"
    folder.mkdir()
    (folder / "a.txt").write_text("v1", encoding="utf-8")
    assert booted.bridge.grant_file_root(str(folder), True)["ok"] is True
    written = windows.filesystem.write_text_file("root-notlar", "a.txt", "v2", overwrite=True)
    snapshot_id = written.data["snapshot"]["snapshot_id"]

    listing = booted.bridge.list_snapshots(10)
    assert listing["ok"] is True and listing["available"] is True
    assert listing["total"] == 1
    assert listing["snapshots"][0]["snapshot_id"] == snapshot_id
    assert listing["snapshots"][0]["path"] == "a.txt"
    assert listing["usage"]["entries"] == 1

    assert booted.bridge.restore_snapshot(snapshot_id) == {
        "ok": False,
        "error": "Geri yükleme onaylanmadı.",
    }
    restored = booted.bridge.restore_snapshot(snapshot_id, True)
    assert restored["ok"] is True and restored["path"] == "a.txt"
    assert (folder / "a.txt").read_text(encoding="utf-8") == "v1"
    assert booted.bridge.restore_snapshot("0" * 32, True) == {
        "ok": False,
        "error": "Anlık görüntü bulunamadı.",
    }
    events = [event.name for event in booted.app.diagnostics.ledger.list(component="ui", limit=10)]
    assert "filesystem.snapshot_restored" in events


# ---------------------------------------------------------------------------
# notifications: reminders, the unattended window, the centre API
# ---------------------------------------------------------------------------


class RecordingReminders:
    """Stands in for ReminderService: claim_due hands out scripted batches."""

    def __init__(self, batches=None) -> None:
        self.batches = list(batches or [])
        self.claims = 0

    def claim_due(self):
        self.claims += 1
        return self.batches.pop(0) if self.batches else []


def test_boot_reports_the_centre_and_starts_reminder_delivery(booted) -> None:
    assert booted.boot["notifications"] == {"items": [], "unread": 0, "total": 0}
    watch = booted.bridge._reminder_watch
    assert watch is not None and watch.running is True
    # Delivery runs on its own daemon thread, not on the async runner.
    assert not watch._thread or watch._thread.name == "jarvis-reminder-watch"
    booted.bridge._shutdown()
    assert booted.bridge._reminder_watch is None
    assert watch.running is False


def test_due_reminders_reach_the_page_as_notifications() -> None:
    app = application()
    app.reminders = RecordingReminders([[{"reminder_id": "r1", "text": "Toplantı 15:00"}]])
    controller = DesktopController(app)
    bridge = shell.NovaBridge(controller, None)
    window = FakeWindow()
    bridge._attach(window)
    try:
        assert bridge.boot()["notifications"]["items"] == []
        wait_until(lambda: window.payloads("notification") != [])
        payload = window.payloads("notification")[0]
        assert payload["attended"] is True and payload["unread"] == 1
        entry = payload["notification"]
        assert entry["kind"] == "reminder" and entry["title"] == "Hatırlatıcı"
        assert entry["body"] == "Toplantı 15:00" and entry["reference"] == "r1"
        assert entry["read"] is False and entry["target"] is None
        listing = bridge.list_notifications()
        assert listing["ok"] is True and listing["unread"] == 1
        assert [item["notification_id"] for item in listing["items"]] == [
            entry["notification_id"]
        ]
        # A settings save replaces the runtime: the watch follows the new one.
        replacement = RecordingReminders([[{"reminder_id": "r2", "text": "Su iç"}]])
        controller.application.reminders = replacement
        bridge._observe_application()
        wait_until(lambda: len(window.payloads("notification")) == 2)
        assert window.payloads("notification")[1]["notification"]["body"] == "Su iç"
        assert bridge._reminder_watch is not None and bridge._reminder_watch.running
    finally:
        bridge._shutdown()
        controller.close()
    assert bridge._reminder_watch is None


def test_notification_centre_api_marks_dismisses_and_clears(booted) -> None:
    bridge = booted.bridge
    first = bridge._notifications.publish("system", "Bir", "ilk")
    second = bridge._notifications.publish("system", "İki", "ikinci")
    assert bridge.list_notifications(1)["items"][0]["notification_id"] == second.notification_id
    assert bridge.list_notifications("çok")["unread"] == 2
    assert bridge.mark_notifications_read(first.notification_id) == {
        "ok": True, "changed": 1, "unread": 1,
    }
    assert bridge.mark_notifications_read([second.notification_id, "nope"]) == {
        "ok": True, "changed": 1, "unread": 0,
    }
    assert bridge.mark_notifications_read(None) == {"ok": True, "changed": 0, "unread": 0}
    assert bridge.dismiss_notification("nope") == {
        "ok": False, "error": "Bildirim bulunamadı.", "unread": 0,
    }
    assert bridge.dismiss_notification(first.notification_id) == {"ok": True, "unread": 0}
    assert bridge.clear_notifications() == {"ok": True, "cleared": 1, "unread": 0}
    assert bridge.list_notifications() == {"ok": True, "items": [], "unread": 0, "total": 0}
    # Every publication reached the page with the live unread count.
    assert [p["unread"] for p in booted.window.payloads("notification")] == [1, 2]


def test_unattended_window_routes_alerts_to_the_os_notifier(booted) -> None:
    sent: list[tuple[str, str]] = []
    booted.bridge._os_notifier = lambda title, body: sent.append((title, body))
    booted.bridge._deliver_reminder({"reminder_id": "r1", "text": "Su iç"})
    time.sleep(0.05)
    assert sent == []  # attended: the page shows it itself
    assert booted.bridge.set_visible(False) == {"ok": True, "visible": False}
    booted.bridge._deliver_reminder({"reminder_id": "r2", "text": "Ayağa kalk"})
    wait_until(lambda: sent == [("Hatırlatıcı", "Ayağa kalk")])
    assert booted.window.payloads("notification")[-1]["attended"] is False
    wait_until(
        lambda: any(
            event.name == "notification.native"
            for event in booted.app.diagnostics.ledger.list(component="ui", limit=20)
        )
    )
    native = [
        event
        for event in booted.app.diagnostics.ledger.list(component="ui", limit=20)
        if event.name == "notification.native"
    ]
    assert native[0].attributes == {"delivered": False, "via": None, "title": "Hatırlatıcı"}
    assert "Ayağa kalk" not in json.dumps(native[0].attributes)
    booted.bridge._deliver_reminder({"reminder_id": "r3", "text": "   "})
    # Ledger warnings never alert the OS; they only enter the centre.
    booted.bridge._on_diagnostic_event(
        SimpleNamespace(
            sequence=1, observed_at="t", component="ui", name="tray.error",
            level="warning", message="icon failed", attributes={}, trace_id=None,
        )
    )
    time.sleep(0.05)
    assert len(sent) == 1
    assert len(booted.window.payloads("notification")) == 3
    assert booted.bridge.set_visible("true") == {"ok": True, "visible": True}
    assert booted.bridge.set_visible("yes")["visible"] is False
    assert booted.bridge.set_visible(1)["visible"] is False


def test_os_notifications_respect_the_setting_and_the_in_flight_bound(booted) -> None:
    sent: list[str] = []
    booted.bridge._os_notifier = lambda title, body: sent.append(body)
    booted.bridge.set_visible(False)
    booted.app.settings = replace(booted.app.settings, notifications_os_enabled=False)
    booted.bridge._deliver_reminder({"reminder_id": "r", "text": "sessiz"})
    time.sleep(0.05)
    assert sent == []
    assert booted.bridge.list_notifications()["unread"] == 1  # still recorded

    booted.app.settings = replace(booted.app.settings, notifications_os_enabled=True)
    gate = threading.Event()
    started: list[str] = []

    def slow(title: str, body: str) -> None:
        started.append(body)
        gate.wait(5)

    booted.bridge._os_notifier = slow
    results = [booted.bridge._notify_os("t", str(i)) for i in range(6)]
    assert results == [True] * 4 + [False] * 2
    wait_until(lambda: len(started) == 4)
    gate.set()
    wait_until(lambda: booted.bridge._os_in_flight == 0)
    assert booted.bridge._notify_os("t", "again") is True
    booted.bridge._os_notifier = None
    assert booted.bridge._notify_os("t", "nobody") is False


def test_reply_on_a_hidden_window_becomes_a_notification(booted) -> None:
    class Engine:
        async def handle(self, request, context, **_kwargs):
            return Response("Merhaba Ali", request_id=request.request_id)

    booted.app.engine = Engine()
    booted.bridge.set_visible(False)
    assert booted.bridge.submit_command("merhaba") == {"ok": True}
    wait_until(lambda: "snapshot" in booted.window.kinds())
    notes = [p["notification"] for p in booted.window.payloads("notification")]
    assert [n["kind"] for n in notes] == ["reply"]
    assert notes[0]["title"] == "Yanıt hazır" and notes[0]["target"] == "chat"
    assert notes[0]["body"] == "Merhaba Ali"
    # While attended, replies are shown in place and not collected.
    booted.bridge.set_visible(True)
    assert booted.bridge.submit_command("tekrar") == {"ok": True}
    wait_until(lambda: booted.window.kinds().count("snapshot") == 2)
    assert len(booted.window.payloads("notification")) == 1


def test_approval_on_a_hidden_window_is_collected_and_alerted(booted) -> None:
    sent: list[tuple[str, str]] = []
    booted.bridge._os_notifier = lambda title, body: sent.append((title, body))
    booted.bridge.set_visible(False)
    future = booted.controller.submit_background(
        booted.bridge._request_approval(approval_request()), lambda f: None
    )
    wait_until(lambda: booted.window.payloads("approval") != [])
    wait_until(lambda: sent != [])
    assert sent == [("Onay bekleniyor", "fs.write")]
    note = booted.window.payloads("notification")[0]["notification"]
    assert note["kind"] == "approval" and note["severity"] == "warning"
    assert note["data"] == {"tool": "fs.write", "risk": "high"}
    assert "sk-hidden-value" not in json.dumps(booted.window.payloads("notification"))
    token = booted.window.payloads("approval")[0]["token"]
    assert booted.bridge.resolve_approval(token, False) == {"ok": True}
    assert future.result(timeout=5) is False


def test_vision_and_research_results_on_a_hidden_window_are_collected(booted) -> None:
    class Vision:
        def request_consent(self, purpose):
            return SimpleNamespace(request_id="req-1")

        def approve_consent(self, request_id):
            return SimpleNamespace(request_id=request_id)

        async def analyze(self, purpose, grant, context=None):
            return SimpleNamespace(
                response=SimpleNamespace(text="Ekranda bir tablo var."), error_code=None
            )

    class Research:
        def research(self, query, *, max_sources):
            return SimpleNamespace(
                to_dict=lambda: {"query": query, "summary": "özet", "sources": []}
            )

    booted.bridge.set_visible(False)
    booted.app.vision = Vision()
    booted.app.research = Research()
    assert booted.bridge.run_vision("ne var")["ok"] is True
    assert booted.bridge.run_research("pywebview")["ok"] is True
    wait_until(lambda: len(booted.window.payloads("notification")) == 2)
    notes = {p["notification"]["kind"]: p["notification"] for p in booted.window.payloads("notification")}
    assert set(notes) == {"task"} or len(notes) == 1
    titles = sorted(p["notification"]["title"] for p in booted.window.payloads("notification"))
    assert titles == ["Araştırma tamamlandı", "Görüş sonucu hazır"]
    targets = sorted(p["notification"]["target"] for p in booted.window.payloads("notification"))
    assert targets == ["research", "vision"]
    bodies = [p["notification"]["body"] for p in booted.window.payloads("notification")]
    assert "Ekranda bir tablo var." in bodies
    assert any("pywebview" in body for body in bodies)


def test_screen_observations_and_ledger_warnings_enter_the_centre(booted) -> None:
    from app.diagnostics.models import DiagnosticLevel

    watcher = SimpleNamespace(_notify=None)
    booted.app.screen_watcher = watcher
    booted.bridge._observe_application()
    assert watcher._notify is not None
    watcher._notify({"at": "12:00:00", "state": "completed", "text": "Tarayıcıda yeni sekme açıldı."})
    watcher._notify({"text": "   "})
    watcher._notify("not a mapping")
    notes = [p["notification"] for p in booted.window.payloads("notification")]
    assert [n["kind"] for n in notes] == ["observation"]
    assert notes[0]["title"] == "Ekran gözlemi" and notes[0]["target"] == "vision"
    assert notes[0]["data"] == {"at": "12:00:00", "state": "completed"}

    diagnostics = booted.app.diagnostics
    diagnostics.record(
        "providers", "circuit.opened", "Provider circuit opened.", level=DiagnosticLevel.WARNING
    )
    diagnostics.record(
        "providers", "circuit.opened", "Provider circuit opened again.", level=DiagnosticLevel.WARNING
    )
    diagnostics.record("core", "request.completed", "fine")
    wait_until(lambda: len(booted.window.payloads("notification")) == 3)
    warning = booted.window.payloads("notification")[-1]["notification"]
    assert warning["kind"] == "diagnostic" and warning["title"] == "Uyarı"
    assert warning["target"] == "diagnostics" and warning["count"] == 2
    assert warning["body"].endswith("Provider circuit opened again.")
    assert warning["data"] == {"component": "providers", "name": "circuit.opened"}
    assert booted.bridge.list_notifications()["total"] == 2
    booted.bridge._detach_observers()
    assert watcher._notify is None


def test_tray_actions_track_whether_the_window_is_attended(booted) -> None:
    window = SimpleNamespace(show=lambda: None, hide=lambda: None, destroy=lambda: None)
    actions = shell.NovaTrayActions(window, booted.bridge, booted.controller)
    actions.hide()
    assert booted.bridge._attended is False
    actions.open()
    assert booted.bridge._attended is True


def test_launch_nova_installs_an_os_notifier_and_window_visibility_hooks(monkeypatch, tmp_path) -> None:
    app = application()
    controller = DesktopController(app)
    handlers: dict[str, list] = {
        "closed": [], "closing": [], "minimized": [], "restored": [], "maximized": [], "shown": [],
    }
    fake_window = SimpleNamespace(
        events=SimpleNamespace(**{name: _Hook(sink) for name, sink in handlers.items()}),
        evaluate_js=lambda script: None,
        hide=lambda: None,
        show=lambda: None,
        destroy=lambda: None,
    )
    created: dict[str, object] = {}
    toasts: list[tuple[str, str]] = []

    def start(**options):
        bridge = created["js_api"]
        for handler in handlers["minimized"]:
            handler()
        assert bridge._attended is False
        for handler in handlers["restored"]:
            handler()
        assert bridge._attended is True
        for handler in handlers["minimized"]:
            handler()
        for handler in handlers["maximized"]:
            handler()
        assert bridge._attended is True
        assert bridge._os_notifier("Başlık", "Gövde") == "toast"
        for handler in handlers["closed"]:
            handler()

    monkeypatch.setattr(shell.webview, "create_window", lambda title, **kwargs: created.update(kwargs) or fake_window)
    monkeypatch.setattr(shell.webview, "start", start)
    monkeypatch.setattr(shell, "webview_storage_directory", lambda: tmp_path / "webview")
    monkeypatch.setattr(shell, "show_windows_toast", lambda title, body: toasts.append((title, body)) or True)
    monkeypatch.setattr(shell, "default_backend_factory", lambda: None)

    shell.launch_nova(controller, None)

    # Without a tray icon the plain Windows toast carries the alert.
    assert toasts == [("Başlık", "Gövde")]
    assert created["js_api"]._closing is True
    visibility = [
        event.attributes["attended"]
        for event in app.diagnostics.ledger.list(component="ui", limit=20)
        if event.name == "window.visibility"
    ]
    assert visibility == [True, False, True, False]


def test_boot_warms_the_provider_connection_on_the_runner(booted) -> None:
    warmed: list[str] = []

    class Gateway:
        async def warm_up(self):
            warmed.append("gemini")
            return {"gemini": True}

    class Voice:
        async def warm_up(self):
            warmed.append("voice")
            return {"voice_stt": True, "voice_tts": False}

    booted.app.provider_gateway = Gateway()  # type: ignore[assignment]
    booted.app.voice = Voice()  # type: ignore[assignment]
    booted.bridge._warm_up_provider()
    wait_until(lambda: warmed == ["gemini", "voice"])
    wait_until(
        lambda: any(
            event.name == "provider.warm_up"
            for event in booted.app.diagnostics.ledger.list(component="ui", limit=20)
        )
    )
    event = next(
        e for e in booted.app.diagnostics.ledger.list(component="ui", limit=20)
        if e.name == "provider.warm_up"
    )
    assert event.attributes["results"] == {
        "gemini": True, "voice_stt": True, "voice_tts": False,
    }
    assert event.attributes["seconds"] >= 0


# ---------------------------------------------------------------------------
# routines: due prompts run through the core, outcome to the centre
# ---------------------------------------------------------------------------


def test_boot_lists_routines_and_starts_their_watch(booted) -> None:
    assert booted.boot["routines"] == {"available": True, "routines": []}
    assert booted.bridge._routine_watch is not None
    assert booted.bridge._routine_watch.running is True
    assert booted.bridge.list_routines() == {"ok": True, "available": True, "routines": []}
    booted.bridge._shutdown()
    assert booted.bridge._routine_watch is None


def test_due_routine_runs_through_the_core_and_lands_in_the_centre(booted) -> None:
    seen: list = []

    class Engine:
        async def handle(self, request, context, *, approval_callback=None, **_kwargs):
            seen.append((request, context, approval_callback))
            return Response("Hava güneşli, 24 derece.", request_id=request.request_id,
                            metadata={"outcome": "completed"})

    booted.app.engine = Engine()
    created = booted.app.routines.create("Hava", "bugün hava nasıl", every_minutes=30)
    routine = booted.app.routines.get(created.data["routine_id"])

    booted.bridge._run_routine(routine)
    wait_until(lambda: booted.window.payloads("notification") != [])

    request, context, approval = seen[0]
    assert request.source.value == "system"
    assert request.metadata["routine_id"] == routine["routine_id"]
    assert approval == booted.bridge._request_approval
    note = booted.window.payloads("notification")[0]["notification"]
    assert note["kind"] == "task" and note["title"] == "Rutin · Hava"
    assert note["body"] == "Hava güneşli, 24 derece."
    assert note["target"] == "chat"
    assert note["data"]["conversation_id"] == str(context.conversation_id)
    wait_until(lambda: booted.app.routines.get(routine["routine_id"])["run_count"] == 1)
    stored = booted.app.routines.get(routine["routine_id"])
    assert stored["last_outcome"] == "completed"
    assert stored["conversation_id"] == str(context.conversation_id)
    events = [e.name for e in booted.app.diagnostics.ledger.list(component="ui", limit=20)]
    assert "routine.started" in events and "routine.completed" in events

    # The second run reuses the routine's own conversation.
    booted.bridge._run_routine(booted.app.routines.get(routine["routine_id"]))
    wait_until(lambda: len(seen) == 2)
    assert str(seen[1][1].conversation_id) == stored["conversation_id"]


def test_routine_failures_are_reported_not_hidden(booted) -> None:
    class Engine:
        async def handle(self, request, context, **_kwargs):
            raise RuntimeError("gizli ayrıntı")

    booted.app.engine = Engine()
    created = booted.app.routines.create("Kırık", "patla", every_minutes=30)
    booted.bridge._run_routine(booted.app.routines.get(created.data["routine_id"]))
    wait_until(lambda: booted.window.payloads("notification") != [])
    note = booted.window.payloads("notification")[0]["notification"]
    assert note["severity"] == "error"
    assert note["body"] == "Rutin çalıştırılamadı (RuntimeError)."
    assert "gizli ayrıntı" not in json.dumps(booted.window.events())


def test_paused_or_busy_desktop_defers_a_due_routine(booted) -> None:
    created = booted.app.routines.create("Sabah", "özetle", at="09:00")
    routine = booted.app.routines.get(created.data["routine_id"])
    before = routine["next_run_at"]
    booted.controller.set_paused(True)
    booted.bridge._run_routine(routine)
    assert booted.window.payloads("notification") == []
    after = booted.app.routines.get(routine["routine_id"])["next_run_at"]
    assert after < before  # tried again soon, not skipped to tomorrow
    events = [e.name for e in booted.app.diagnostics.ledger.list(component="ui", limit=20)]
    assert "routine.deferred" in events


def test_deleting_a_routine_needs_confirmation_and_is_recorded(booted) -> None:
    created = booted.app.routines.create("Sabah", "özetle", at="09:00")
    routine_id = created.data["routine_id"]
    assert booted.bridge.delete_routine(routine_id) == {"ok": False, "error": "Rutin silme onaylanmadı."}
    assert booted.bridge.delete_routine("nope", True)["ok"] is False
    result = booted.bridge.delete_routine(routine_id, True)
    assert result["ok"] is True and result["routines"] == []
    events = [e.name for e in booted.app.diagnostics.ledger.list(component="ui", limit=20)]
    assert "routine.deleted" in events
    booted.app.routines = None
    assert booted.bridge.list_routines()["ok"] is False
    assert booted.bridge.delete_routine("x", True)["ok"] is False
