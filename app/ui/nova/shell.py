"""Nova shell: a WebView2-hosted, compositor-animated JARVIS desktop.

The window renders ``web/index.html`` inside Microsoft Edge WebView2 via
pywebview. All motion runs on the browser compositor (transform/opacity
plus requestAnimationFrame canvases), so animation fluidity follows the
monitor refresh rate — 120 Hz panels get 120 fps — and canvases scale by
devicePixelRatio for crisp 4K output.

Python stays the source of truth: the existing :class:`DesktopController`
performs every operation, and results are pushed into the page through
``window.NOVA.push(...)``.

Honesty rules that this module enforces:

* The page never receives simulated data. If the bridge cannot be
  established the page shows an explicit error, never a demo.
* Approval decisions are bound to a single-use token and fail closed
  (denied) on timeout, on shutdown, or when the window is not ready.
* Secrets never cross the bridge: settings snapshots carry only whether
  a credential exists, and connection-test messages are already redacted
  by :class:`APISettingsService`.
"""

from __future__ import annotations

import asyncio
import json
import sys
import time
from collections.abc import Mapping
from concurrent.futures import Future
from dataclasses import asdict, is_dataclass
from datetime import date, datetime
from enum import Enum
from math import isfinite
from pathlib import Path, PurePath
from threading import Lock
from typing import Any
from uuid import UUID, uuid4

import webview

from app.config.paths import default_state_directory
from app.security.interactive import (
    InteractiveApprovalRequest,
    safe_approval_parameters,
)
from app.ui.api_settings import APISettingsService
from app.ui.controller import DesktopController

WEB_ASSETS: tuple[str, ...] = ("index.html", "nova.css", "nova.js")
WEB_RELATIVE_PATH = PurePath("app", "ui", "nova", "web")
SOURCE_WEB_ROOT = Path(__file__).resolve().parent / "web"
WINDOW_TITLE = "JARVIS"
READY_STATUS = "LOCAL CORE READY"
STREAM_FLUSH_SECONDS = 0.05
APPROVAL_SAFETY_MARGIN_SECONDS = 2.0
APPROVAL_MINIMUM_WAIT_SECONDS = 5.0
CONNECTION_TEST_TIMEOUT_SECONDS = 30.0
MAX_RESEARCH_SOURCES = 10


def resolve_web_root() -> Path:
    """Return the directory holding Nova's web assets.

    Works for a source checkout and for a PyInstaller bundle, where the
    assets are collected below ``sys._MEIPASS/app/ui/nova/web``. A
    missing asset set is reported explicitly instead of letting WebView2
    render a blank page.
    """
    candidates: list[Path] = []
    bundle_root = getattr(sys, "_MEIPASS", None)
    if bundle_root:
        candidates.append(Path(bundle_root) / WEB_RELATIVE_PATH)
    candidates.append(SOURCE_WEB_ROOT)
    for candidate in candidates:
        if all((candidate / name).is_file() for name in WEB_ASSETS):
            return candidate
    searched = ", ".join(str(candidate) for candidate in candidates)
    raise FileNotFoundError(
        "Nova web assets (" + ", ".join(WEB_ASSETS) + ") were not found in: "
        + searched
    )


def webview_storage_directory() -> Path:
    """Per-user WebView2 profile so theme/motion preferences persist."""
    return default_state_directory() / "webview"


def _jsonable(value: Any) -> Any:
    """Convert runtime objects into JSON-safe, predictable structures."""
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, float):
        return value if isfinite(value) else None
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, (UUID, PurePath)):
        return str(value)
    if isinstance(value, Enum):
        return _jsonable(value.value)
    if isinstance(value, (bytes, bytearray, memoryview)):
        return f"<{len(value)} bayt>"
    if is_dataclass(value) and not isinstance(value, type):
        return _jsonable(asdict(value))
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_jsonable(item) for item in value]
    return str(value)


def _active(future: Future[Any] | None) -> bool:
    return future is not None and not future.done()


class NovaBridge:
    """The ``pywebview`` JS API: every UI action lands here.

    pywebview invokes each public method on a worker thread, so blocking
    on a controller future never freezes the window. Long-lived
    operations (voice, streamed commands) run on the controller's async
    runner and report back through :meth:`_push`. Methods whose names
    start with an underscore are lifecycle hooks for :func:`launch_nova`
    and are not exposed to the page.
    """

    def __init__(
        self,
        controller: DesktopController,
        api_settings: APISettingsService | None,
    ) -> None:
        self.controller = controller
        self.api_settings = api_settings
        self._window: webview.Window | None = None
        self._ready = False
        self._closing = False
        self._lock = Lock()
        self._push_lock = Lock()
        self._stream_buffer: list[str] = []
        self._stream_last_flush = 0.0
        self._command_future: Future[Any] | None = None
        self._voice_future: Future[Any] | None = None
        self._approvals: dict[str, Future[bool]] = {}
        controller.approval_callback = self._request_approval

    # ------------------------------------------------------------------
    # Python -> JS
    # ------------------------------------------------------------------
    def _attach(self, window: webview.Window) -> None:
        self._window = window

    def _push(self, kind: str, payload: Any = None) -> None:
        window = self._window
        if window is None or not self._ready or self._closing:
            return
        message = json.dumps(
            {"kind": kind, "payload": _jsonable(payload)},
            ensure_ascii=True,
        )
        with self._push_lock:
            try:
                window.evaluate_js(
                    f"window.NOVA && window.NOVA.push({message})"
                )
            except Exception:
                # The window is closing; dropping a frame of telemetry
                # is preferable to killing the worker thread.
                pass

    def _push_snapshot(self) -> None:
        self._push("snapshot", self.controller.snapshot())

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------
    def boot(self) -> dict[str, Any]:
        """First call from the page: returns the full initial state."""
        self._ready = True
        snapshot = self.controller.snapshot()
        settings: dict[str, Any] | None = None
        if self.api_settings is not None:
            settings = _jsonable(self.api_settings.snapshot())
        return {
            "snapshot": _jsonable(snapshot),
            "settings": settings,
            "messages": _jsonable(self.controller.state.messages),
            "voiceMessages": _jsonable(self.controller.state.voice_messages),
            "status": self.controller.state.status,
        }

    def _shutdown(self) -> None:
        """Fail every pending decision closed and stop reporting."""
        with self._lock:
            self._closing = True
            self._ready = False
            pending = list(self._approvals.values())
            self._approvals.clear()
            voice_future = self._voice_future
            command_future = self._command_future
            self._voice_future = None
            self._command_future = None
        for decision in pending:
            if not decision.done():
                decision.set_result(False)
        for future in (voice_future, command_future):
            if _active(future):
                future.cancel()

    # ------------------------------------------------------------------
    # Chat
    # ------------------------------------------------------------------
    def submit_command(self, text: str) -> dict[str, Any]:
        normalized = str(text or "").strip()
        if not normalized:
            return {"ok": False, "error": "Komut boş olamaz."}

        def stream(chunk: str) -> None:
            if not chunk:
                return
            self._stream_buffer.append(chunk)
            now = time.monotonic()
            if now - self._stream_last_flush >= STREAM_FLUSH_SECONDS:
                self._flush_stream()

        def done(future: Future[Any]) -> None:
            with self._lock:
                if self._command_future is future:
                    self._command_future = None
            self._flush_stream()
            if future.cancelled():
                return
            try:
                message = future.result()
            except Exception as exc:  # Defensive: controller already guards.
                self._push(
                    "reply",
                    {
                        "role": "system",
                        "text": f"İstek tamamlanamadı ({type(exc).__name__}).",
                        "metadata": {},
                    },
                )
            else:
                self._push("reply", message)
            self._push("busy", {"busy": False, "status": READY_STATUS})
            self._push_snapshot()

        # The busy check and the submission happen under one lock, so two
        # rapid sends cannot both slip past the guard before the runner
        # marks the controller busy.
        with self._lock:
            if self._closing:
                return {"ok": False, "error": "JARVIS kapanıyor."}
            if _active(self._command_future) or self.controller.state.busy:
                return {
                    "ok": False,
                    "error": "JARVIS şu an başka bir istek işliyor.",
                }
            try:
                self._command_future = self.controller.submit_background(
                    self.controller.submit_command(
                        normalized, stream_callback=stream
                    ),
                    done,
                )
            except RuntimeError as exc:
                return {"ok": False, "error": f"İstek gönderilemedi ({exc})."}
        return {"ok": True}

    def _flush_stream(self) -> None:
        if not self._stream_buffer:
            return
        text = "".join(self._stream_buffer)
        self._stream_buffer.clear()
        self._stream_last_flush = time.monotonic()
        self._push("stream", {"text": text})

    # ------------------------------------------------------------------
    # Voice
    # ------------------------------------------------------------------
    def start_voice(self) -> dict[str, Any]:
        voice = self.controller.application.voice
        if voice is None:
            return {"ok": False, "error": "Sesli iletişim ayarlanmamış."}

        def deliver(message: Any) -> None:
            self._push("voice_message", message)

        def done(future: Future[Any]) -> None:
            with self._lock:
                if self._voice_future is future:
                    self._voice_future = None
            current = self.controller.application.voice
            if current is not None and hasattr(current, "state_callback"):
                current.state_callback = None
            if future.cancelled():
                return
            error: str | None = None
            try:
                future.result()
            except Exception as exc:
                error = f"Sesli oturum sonlandı ({type(exc).__name__})."
            self._push("voice_state", {"active": False, "error": error})
            self._push_snapshot()

        with self._lock:
            if self._closing:
                return {"ok": False, "error": "JARVIS kapanıyor."}
            if _active(self._voice_future):
                return {"ok": False, "error": "Sesli oturum zaten açık."}
            # Feed the pipeline's phase transitions (listening,
            # transcribing, processing, synthesizing, speaking) straight
            # into the HUD.
            if hasattr(voice, "state_callback"):
                voice.state_callback = lambda state: self._push(
                    "voice_phase",
                    {"phase": getattr(state, "value", str(state))},
                )
            try:
                self._voice_future = self.controller.submit_background(
                    self.controller.run_voice(message_callback=deliver),
                    done,
                )
            except RuntimeError as exc:
                return {
                    "ok": False,
                    "error": f"Sesli oturum başlatılamadı ({exc}).",
                }
        return {"ok": True}

    def stop_voice(self) -> dict[str, Any]:
        with self._lock:
            if not _active(self._voice_future):
                return {"ok": False, "error": "Açık sesli oturum yok."}

        def done(future: Future[Any]) -> None:
            if future.cancelled():
                return
            try:
                interrupted = bool(future.result())
            except Exception:
                interrupted = False
            if not interrupted and _active(self._voice_future):
                # Be explicit rather than leaving the HUD in limbo.
                self._push(
                    "voice_state",
                    {
                        "active": True,
                        "error": (
                            "Sesli oturum durdurulamadı; tur bitince kapanacak."
                        ),
                    },
                )

        try:
            self.controller.submit_background(
                self.controller.interrupt_voice(), done
            )
        except RuntimeError as exc:
            return {
                "ok": False,
                "error": f"Durdurma isteği gönderilemedi ({exc}).",
            }
        return {"ok": True}

    # ------------------------------------------------------------------
    # Vision / research
    # ------------------------------------------------------------------
    def run_vision(self, purpose: str) -> dict[str, Any]:
        goal = str(purpose or "").strip() or "Ekranda ne olduğunu açıkla."
        if self.controller.application.vision is None:
            return {"ok": False, "error": "Görüş özelliği ayarlanmamış."}

        def done(future: Future[Any]) -> None:
            if future.cancelled():
                return
            try:
                text = future.result()
            except Exception as exc:
                self._push(
                    "vision_result",
                    {
                        "ok": False,
                        "error": f"Analiz başarısız ({type(exc).__name__}).",
                    },
                )
            else:
                self._push("vision_result", {"ok": True, "text": text})

        try:
            self.controller.submit_background(
                self.controller.run_vision(goal), done
            )
        except RuntimeError as exc:
            return {"ok": False, "error": f"Analiz başlatılamadı ({exc})."}
        return {"ok": True}

    def run_research(self, query: str, max_sources: Any = 5) -> dict[str, Any]:
        normalized = str(query or "").strip()
        if not normalized:
            return {"ok": False, "error": "Araştırma sorgusu boş olamaz."}
        if self.controller.application.research is None:
            return {"ok": False, "error": "Web araştırması ayarlanmamış."}
        try:
            requested = int(max_sources)
        except (TypeError, ValueError):
            requested = 5
        bounded = max(1, min(requested, MAX_RESEARCH_SOURCES))

        def done(future: Future[Any]) -> None:
            if future.cancelled():
                return
            try:
                report = future.result()
            except Exception as exc:
                self._push(
                    "research_result",
                    {
                        "ok": False,
                        "error": f"Araştırma başarısız ({type(exc).__name__}).",
                    },
                )
            else:
                self._push("research_result", {"ok": True, "report": report})

        try:
            self.controller.submit_background(
                self.controller.run_research(normalized, max_sources=bounded),
                done,
            )
        except RuntimeError as exc:
            return {"ok": False, "error": f"Araştırma başlatılamadı ({exc})."}
        return {"ok": True}

    # ------------------------------------------------------------------
    # Approvals
    # ------------------------------------------------------------------
    async def _request_approval(
        self, request: InteractiveApprovalRequest
    ) -> bool:
        """Bridge the engine's approval request into the page and wait.

        Fails closed: no page, no decision, or an expired window all mean
        "denied". Each request gets a fresh single-use token so a stale
        click can never approve a later action.
        """
        if not self._ready or self._closing or self._window is None:
            return False
        token = str(uuid4())
        decision: Future[bool] = Future()
        with self._lock:
            if self._closing:
                return False
            self._approvals[token] = decision
        remaining = (
            request.expires_at - datetime.now(tz=request.expires_at.tzinfo)
        ).total_seconds() - APPROVAL_SAFETY_MARGIN_SECONDS
        wait_seconds = max(remaining, APPROVAL_MINIMUM_WAIT_SECONDS)
        self._push(
            "approval",
            {
                "token": token,
                "tool": request.tool_name,
                "operation": request.operation,
                "risk": request.risk_level.value,
                "reason": request.reason,
                "parameters": dict(safe_approval_parameters(request.parameters)),
                "seconds": max(int(wait_seconds), 1),
            },
        )
        try:
            return bool(
                await asyncio.wait_for(
                    asyncio.wrap_future(decision), timeout=wait_seconds
                )
            )
        except (asyncio.TimeoutError, asyncio.CancelledError):
            return False
        finally:
            with self._lock:
                self._approvals.pop(token, None)
            if not decision.done():
                decision.set_result(False)
            self._push("approval_closed", {"token": token})

    def resolve_approval(self, token: str, approved: bool) -> dict[str, Any]:
        with self._lock:
            decision = self._approvals.pop(str(token), None)
        if decision is None or decision.done():
            return {"ok": False, "error": "Onay isteği artık geçerli değil."}
        decision.set_result(approved is True)
        return {"ok": True}

    # ------------------------------------------------------------------
    # Data refresh
    # ------------------------------------------------------------------
    def refresh(self) -> dict[str, Any]:
        return {"snapshot": _jsonable(self.controller.snapshot())}

    # ------------------------------------------------------------------
    # Settings
    # ------------------------------------------------------------------
    def get_settings(self) -> dict[str, Any] | None:
        if self.api_settings is None:
            return None
        return _jsonable(self.api_settings.snapshot())

    def _rebuild_runtime(self) -> None:
        from app.bootstrap import create_application

        assert self.api_settings is not None
        application = create_application(
            self.api_settings.build_runtime_settings()
        )
        self.controller.replace_application(application)

    def save_settings(
        self, provider: str, model: str, api_key: str
    ) -> dict[str, Any]:
        if self.api_settings is None:
            return {"ok": False, "error": "Ayar hizmeti kullanılamıyor."}
        try:
            self.api_settings.save(provider, model, api_key or None)
            self._rebuild_runtime()
        except Exception as exc:
            return {"ok": False, "error": str(exc)}
        self._push_snapshot()
        return {
            "ok": True,
            "message": "Ayarlar kaydedildi. Canlı çalışma zamanı etkin.",
            "settings": self.get_settings(),
        }

    def test_connection(
        self, provider: str, model: str, api_key: str
    ) -> dict[str, Any]:
        if self.api_settings is None:
            return {"ok": False, "message": "Ayar hizmeti kullanılamıyor."}
        try:
            future = self.controller.submit_background(
                self.api_settings.test_connection(
                    provider, model, api_key or None
                ),
                lambda done: None,
            )
            result = future.result(timeout=CONNECTION_TEST_TIMEOUT_SECONDS)
        except Exception as exc:
            return {"ok": False, "message": f"Bağlantı sınanamadı: {exc}"}
        return {"ok": result.ok, "message": result.message}

    def delete_api_key(self, confirmed: bool = False) -> dict[str, Any]:
        """Delete the stored credential; requires an explicit confirmation.

        The page shows its own confirmation dialog before calling this
        with ``confirmed=True``. The flag makes an accidental or scripted
        direct call a no-op instead of a credential loss.
        """
        if confirmed is not True:
            return {"ok": False, "error": "Silme işlemi onaylanmadı."}
        if self.api_settings is None:
            return {"ok": False, "error": "Ayar hizmeti kullanılamıyor."}
        try:
            self.api_settings.delete_api_key()
            self._rebuild_runtime()
        except Exception as exc:
            return {"ok": False, "error": str(exc)}
        self._push_snapshot()
        return {
            "ok": True,
            "message": "Anahtar silindi. Deneme modu etkin.",
            "settings": self.get_settings(),
        }


def launch_nova(
    controller: DesktopController,
    api_settings: APISettingsService | None = None,
) -> None:
    """Open the Nova window and run it until the user closes it.

    Resources are released exactly once, whether the window reports the
    ``closed`` event or ``webview.start`` simply returns.
    """
    web_root = resolve_web_root()
    bridge = NovaBridge(controller, api_settings)

    start_options: dict[str, Any] = {"debug": False}
    storage = webview_storage_directory()
    try:
        storage.mkdir(parents=True, exist_ok=True)
    except OSError:
        # No writable profile directory: run in private mode; only the
        # theme/motion preferences are lost between launches.
        pass
    else:
        start_options.update(private_mode=False, storage_path=str(storage))

    window = webview.create_window(
        WINDOW_TITLE,
        url=str(web_root / "index.html"),
        js_api=bridge,
        width=1440,
        height=920,
        min_size=(1080, 680),
        background_color="#03060c",
        maximized=True,
        text_select=False,
    )
    bridge._attach(window)

    released = False

    def release() -> None:
        nonlocal released
        if released:
            return
        released = True
        bridge._shutdown()
        controller.close()

    window.events.closed += release
    try:
        webview.start(**start_options)
    finally:
        release()
