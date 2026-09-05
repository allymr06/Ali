"""Nova shell: a WebView2-hosted, compositor-animated JARVIS desktop.

The window renders ``web/index.html`` inside Microsoft Edge WebView2 via
pywebview. All motion runs on the browser compositor (transform/opacity
plus requestAnimationFrame canvases), so animation fluidity follows the
monitor refresh rate — 120 Hz panels get 120 fps — and canvases scale by
devicePixelRatio for crisp 4K output.

Python stays the source of truth: the existing :class:`DesktopController`
performs every operation, and results are pushed into the page through
``window.NOVA.push(...)``. The page additionally observes, read-only, the
tool executor (every execution start and outcome), the diagnostics
ledger (every sealed event), and the microphone input level, so the
interface can show what JARVIS is doing as it happens.

Honesty rules that this module enforces:

* The page never receives simulated data. If the bridge cannot be
  established the page shows an explicit error, never a demo.
* Approval decisions are bound to a single-use token and fail closed
  (denied) on timeout, on shutdown, or when the window is not ready.
* Secrets never cross the bridge: settings snapshots carry only whether
  a credential exists, runtime configuration is exported through an
  explicit allow-list, and connection-test messages are already redacted
  by :class:`APISettingsService`.
"""

from __future__ import annotations

import asyncio
import ctypes
import getpass
import importlib.metadata
import json
import os
import platform
import sys
import threading
import time
from collections.abc import Callable, Mapping
from concurrent.futures import Future
from dataclasses import asdict, is_dataclass
from datetime import date, datetime
from enum import Enum
from math import isfinite
from pathlib import Path, PurePath
from threading import Lock, RLock
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
from app.ui.tray.model import TrayItem
from app.ui.tray.service import TrayService, default_backend_factory

WEB_ASSETS: tuple[str, ...] = (
    "index.html",
    "css/tokens.css",
    "css/base.css",
    "css/shell.css",
    "css/components.css",
    "css/screens.css",
    "js/foundation.js",
    "js/bridge.js",
    "js/presence.js",
    "js/shell.js",
    "js/conversation.js",
    "js/activity.js",
    "js/panels.js",
    "js/main.js",
)
WEB_RELATIVE_PATH = PurePath("app", "ui", "nova", "web")
SOURCE_WEB_ROOT = Path(__file__).resolve().parent / "web"
WINDOW_TITLE = "JARVIS"
READY_STATUS = "LOCAL CORE READY"
PAUSED_STATUS = "PAUSED"
PAUSED_MESSAGE = (
    "JARVIS duraklatıldı; devam etmek için tepsi menüsünden Devam'ı seç."
)
TRAY_HIDDEN_NOTICE = (
    "JARVIS tepside çalışmaya devam ediyor. Pencereyi açmak için simgeye "
    "çift tıkla; çıkmak için menüden Çıkış'ı seç."
)
STREAM_FLUSH_SECONDS = 0.05
APPROVAL_SAFETY_MARGIN_SECONDS = 2.0
APPROVAL_MINIMUM_WAIT_SECONDS = 5.0
CONNECTION_TEST_TIMEOUT_SECONDS = 30.0
HEALTH_REPORT_TIMEOUT_SECONDS = 20.0
SHUTDOWN_LOCK_TIMEOUT_SECONDS = 2.0
MAX_RESEARCH_SOURCES = 10
MAX_DIAGNOSTIC_EVENTS = 500
MAX_MEMORY_LIST = 500
MAX_CONVERSATION_LIST = 100
MAX_APPLICATION_LIST = 80
# Microphone level pushes are throttled to keep the bridge quiet.
VOICE_LEVEL_INTERVAL_SECONDS = 0.04
# Compact ("mini JARVIS") window geometry in logical pixels.
COMPACT_WINDOW_SIZE = (460, 320)
COMPACT_WINDOW_MARGIN = 24
COMPACT_TASKBAR_ALLOWANCE = 96
# Microsoft's Evergreen WebView2 runtime client id (EdgeUpdate registry).
WEBVIEW2_CLIENT_ID = "{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}"
# Runtime configuration the page may display, read-only. This is an
# allow-list on purpose: fields not named here — API keys, base URLs,
# anything added later — never leave the process.
RUNTIME_SETTING_FIELDS: tuple[str, ...] = (
    "voice_enabled",
    "voice_wake_word",
    "voice_require_wake_word",
    "voice_language",
    "voice_gemini_tts_voice",
    "voice_gemini_stt_model",
    "voice_gemini_tts_model",
    "vision_enabled",
    "vision_detail",
    "vision_redact_taskbar",
    "research_enabled",
    "windows_integrations_enabled",
    "memory_auto_capture_enabled",
    "memory_extraction_model",
    "gemini_action_model",
    "gemini_reasoning_effort",
    "plugins_enabled",
    "tray_enabled",
    "tray_close_to_tray",
    "single_instance_enabled",
    "approval_ttl_seconds",
)
SECRET_SETTING_FIELDS = frozenset(
    {"api_key", "gemini_api_key", "api_base_url", "gemini_base_url"}
)


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


def _webview2_loader_path() -> Path | None:
    """Locate Microsoft's WebView2Loader.dll that pywebview ships."""
    if sys.platform != "win32":
        return None
    machine = platform.machine().lower()
    if "arm" in machine:
        arch = "win-arm64"
    elif machine in {"amd64", "x86_64"}:
        arch = "win-x64"
    else:
        arch = "win-x86"
    candidate = (
        Path(webview.__file__).resolve().parent
        / "lib"
        / "runtimes"
        / arch
        / "native"
        / "WebView2Loader.dll"
    )
    return candidate if candidate.is_file() else None


def _loader_reported_version(loader: Path) -> str | None:
    """Ask the loader which browser build WebView2 would use (None: none)."""
    library = ctypes.WinDLL(str(loader))
    version = ctypes.c_wchar_p()
    result = library.GetAvailableCoreWebView2BrowserVersionString(
        None, ctypes.byref(version)
    )
    text = version.value if result == 0 else None
    if version.value is not None:
        ctypes.windll.ole32.CoTaskMemFree(version)
    return str(text) if text else None


def _registry_webview2_version() -> str | None:
    """Evergreen runtime version from the EdgeUpdate registry, if present."""
    if sys.platform != "win32":
        return None
    import winreg

    locations = (
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients"),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\EdgeUpdate\Clients"),
        (winreg.HKEY_CURRENT_USER, r"Software\Microsoft\EdgeUpdate\Clients"),
    )
    for hive, prefix in locations:
        try:
            with winreg.OpenKey(hive, prefix + "\\" + WEBVIEW2_CLIENT_ID) as key:
                value, _kind = winreg.QueryValueEx(key, "pv")
        except OSError:
            continue
        text = str(value).strip()
        if text and text != "0.0.0.0":
            return text
    return None


def detect_webview2_runtime() -> str | None:
    """Return the WebView2 browser version Nova would run on, or None.

    Microsoft's loader is authoritative (it also accepts Edge Beta/Dev/
    Canary channels); the Evergreen registry entries are the fallback.
    Never raises: callers treat None as "open the classic shell".
    """
    if sys.platform != "win32":
        return None
    loader = _webview2_loader_path()
    if loader is not None:
        try:
            version = _loader_reported_version(loader)
        except (OSError, AttributeError, ValueError):
            version = None
        if version:
            return version
    try:
        return _registry_webview2_version()
    except Exception:
        return None


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


def _application_version() -> str | None:
    """Installed package version, or None when metadata is unavailable."""
    try:
        return importlib.metadata.version("jarvis")
    except importlib.metadata.PackageNotFoundError:
        return None


def _working_set_bytes() -> int | None:
    """Resident memory of this process (Windows), or None when unknown."""
    if sys.platform != "win32":
        return None

    class ProcessMemoryCounters(ctypes.Structure):
        _fields_ = [
            ("cb", ctypes.c_ulong),
            ("PageFaultCount", ctypes.c_ulong),
            ("PeakWorkingSetSize", ctypes.c_size_t),
            ("WorkingSetSize", ctypes.c_size_t),
            ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
            ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
            ("PagefileUsage", ctypes.c_size_t),
            ("PeakPagefileUsage", ctypes.c_size_t),
        ]

    try:
        kernel32 = ctypes.windll.kernel32
        psapi = ctypes.windll.psapi
        kernel32.GetCurrentProcess.restype = ctypes.c_void_p
        psapi.GetProcessMemoryInfo.argtypes = [
            ctypes.c_void_p,
            ctypes.POINTER(ProcessMemoryCounters),
            ctypes.c_ulong,
        ]
        counters = ProcessMemoryCounters()
        counters.cb = ctypes.sizeof(counters)
        handle = kernel32.GetCurrentProcess()
        if not psapi.GetProcessMemoryInfo(handle, ctypes.byref(counters), counters.cb):
            return None
        return int(counters.WorkingSetSize)
    except Exception:
        return None


class ProcessMonitor:
    """Real, cheap process figures for the diagnostics screen."""

    def __init__(self) -> None:
        self._started = time.monotonic()
        self._last_wall: float | None = None
        self._last_cpu: float | None = None

    def sample(self) -> dict[str, Any]:
        wall = time.monotonic()
        cpu = time.process_time()
        percent: float | None = None
        if (
            self._last_wall is not None
            and self._last_cpu is not None
            and wall > self._last_wall
        ):
            cores = max(1, os.cpu_count() or 1)
            percent = (cpu - self._last_cpu) / (wall - self._last_wall) / cores * 100
            percent = max(0.0, min(100.0, percent))
        self._last_wall = wall
        self._last_cpu = cpu
        return {
            "cpu_percent": round(percent, 1) if percent is not None else None,
            "memory_bytes": _working_set_bytes(),
            "threads": threading.active_count(),
            "uptime_seconds": round(wall - self._started, 1),
        }


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
        # Re-entrant on purpose: a background operation can finish before
        # its completion callback is registered, in which case
        # concurrent.futures runs the callback synchronously on the thread
        # that is still holding this lock inside submit_command/start_voice.
        self._lock = RLock()
        self._push_lock = Lock()
        self._stream_buffer: list[str] = []
        self._stream_last_flush = 0.0
        self._command_future: Future[Any] | None = None
        self._voice_future: Future[Any] | None = None
        self._approvals: dict[str, Future[bool]] = {}
        self._detachers: list[Callable[[], None]] = []
        self._voice_level_last = 0.0
        self._compact = False
        self._pause_handler: Callable[[bool], None] | None = None
        self._started_at = datetime.now().astimezone()
        self._webview2_version: str | None | bool = False  # False: not probed
        self._process = ProcessMonitor()
        controller.approval_callback = self._request_approval
        self._observe_application()

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

    def _push_paused(self, paused: bool) -> None:
        self._push(
            "paused",
            {
                "paused": bool(paused),
                "status": PAUSED_STATUS if paused else READY_STATUS,
            },
        )

    # ------------------------------------------------------------------
    # Read-only observation of the running core
    # ------------------------------------------------------------------
    def _observe_application(self) -> None:
        """Subscribe to the current application's executor and ledger.

        Called again after a settings save replaces the runtime, so the
        page keeps seeing the live core rather than a closed one.
        """
        self._detach_observers()
        application = self.controller.application
        executor = getattr(application, "tool_executor", None)
        subscribe = getattr(executor, "subscribe", None)
        if callable(subscribe):
            self._detachers.append(subscribe(self._on_tool_event))
        diagnostics = getattr(application, "diagnostics", None)
        subscribe = getattr(diagnostics, "subscribe", None)
        if callable(subscribe):
            self._detachers.append(subscribe(self._on_diagnostic_event))

    def _detach_observers(self) -> None:
        detachers, self._detachers = self._detachers, []
        for detach in detachers:
            try:
                detach()
            except Exception:
                pass

    def _on_tool_event(self, event: Any) -> None:
        payload: dict[str, Any] = {
            "phase": event.phase,
            "execution_id": str(event.execution_id),
            "tool": event.tool_name,
            "operation": event.operation,
            "at": int(time.time() * 1000),
        }
        result = event.result
        if result is not None:
            duration_ms = None
            if result.started_at is not None and result.finished_at is not None:
                duration_ms = round(
                    (result.finished_at - result.started_at).total_seconds() * 1000
                )
            payload.update(
                {
                    "status": getattr(result.status, "value", str(result.status)),
                    "verified": bool(result.verified),
                    "message": str(result.message or ""),
                    "failed": bool(result.error),
                    "duration_ms": duration_ms,
                }
            )
        self._push("tool_activity", payload)

    def _on_diagnostic_event(self, event: Any) -> None:
        self._push(
            "diagnostic_event",
            {
                "sequence": event.sequence,
                "observed_at": event.observed_at,
                "component": event.component,
                "name": event.name,
                "level": getattr(event.level, "value", str(event.level)),
                "message": event.message,
                "attributes": dict(event.attributes),
                "trace_id": event.trace_id,
            },
        )

    def _on_voice_level(self, level: float) -> None:
        now = time.monotonic()
        if now - self._voice_level_last < VOICE_LEVEL_INTERVAL_SECONDS:
            return
        self._voice_level_last = now
        try:
            value = max(0.0, min(1.0, float(level)))
        except (TypeError, ValueError):
            return
        self._push("voice_level", {"level": round(value, 3)})

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------
    def _runtime_info(self) -> dict[str, Any]:
        """Non-secret facts about this process for the page to display."""
        application = self.controller.application
        settings = getattr(application, "settings", None)
        configuration = {
            name: _jsonable(getattr(settings, name))
            for name in RUNTIME_SETTING_FIELDS
            if settings is not None and hasattr(settings, name)
        }
        if self._webview2_version is False:
            self._webview2_version = detect_webview2_runtime()
        applications: list[dict[str, str]] = []
        windows = getattr(application, "windows", None)
        registry = getattr(windows, "applications", None)
        if registry is not None:
            try:
                for item in list(registry.list())[:MAX_APPLICATION_LIST]:
                    applications.append(
                        {
                            "id": str(item.application_id),
                            "name": str(item.display_name),
                        }
                    )
            except Exception:
                applications = []
        try:
            user_name: str | None = getpass.getuser()
        except Exception:
            user_name = None
        return {
            "version": _application_version(),
            "python": platform.python_version(),
            "platform": f"{platform.system()} {platform.release()}".strip(),
            "webview2": self._webview2_version or None,
            "user_name": user_name,
            "started_at": self._started_at,
            "conversation_id": str(self.controller.context.conversation_id),
            "configuration": configuration,
            "applications": applications,
            "state_directory": default_state_directory(),
        }

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
            "paused": bool(self.controller.paused),
            "compact": self._compact,
            "runtime": _jsonable(self._runtime_info()),
            "conversations": _jsonable(
                self.controller.list_conversations(limit=MAX_CONVERSATION_LIST)
            ),
        }

    def _shutdown(self) -> None:
        """Fail every pending decision closed and stop reporting.

        Shutdown must never hang on a worker that is stuck inside the
        bridge, so the lock is acquired with a deadline and the closing
        flags are set even when it could not be taken.
        """
        acquired = self._lock.acquire(timeout=SHUTDOWN_LOCK_TIMEOUT_SECONDS)
        try:
            self._closing = True
            self._ready = False
            pending = list(self._approvals.values())
            self._approvals.clear()
            voice_future = self._voice_future
            command_future = self._command_future
            self._voice_future = None
            self._command_future = None
        finally:
            if acquired:
                self._lock.release()
        self._detach_observers()
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
        if self.controller.paused:
            return {"ok": False, "error": PAUSED_MESSAGE}

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
    # Conversations
    # ------------------------------------------------------------------
    def _conversation_switch_blocked(self) -> str | None:
        if self._closing:
            return "JARVIS kapanıyor."
        if _active(self._command_future) or self.controller.state.busy:
            return "Yanıt tamamlanmadan konuşma değiştirilemez."
        if _active(self._voice_future):
            return "Sesli oturum açıkken konuşma değiştirilemez."
        return None

    def list_conversations(self) -> dict[str, Any]:
        return {
            "ok": True,
            "active": str(self.controller.context.conversation_id),
            "conversations": _jsonable(
                self.controller.list_conversations(limit=MAX_CONVERSATION_LIST)
            ),
        }

    def open_conversation(self, conversation_id: str) -> dict[str, Any]:
        with self._lock:
            blocked = self._conversation_switch_blocked()
            if blocked is not None:
                return {"ok": False, "error": blocked}
            try:
                messages = self.controller.open_conversation(str(conversation_id))
            except KeyError:
                return {"ok": False, "error": "Konuşma bulunamadı."}
            except ValueError:
                return {"ok": False, "error": "Konuşma kimliği geçersiz."}
        return {
            "ok": True,
            "conversation_id": str(self.controller.context.conversation_id),
            "messages": _jsonable(messages),
        }

    def new_conversation(self) -> dict[str, Any]:
        with self._lock:
            blocked = self._conversation_switch_blocked()
            if blocked is not None:
                return {"ok": False, "error": blocked}
            conversation_id = self.controller.start_new_conversation()
        return {"ok": True, "conversation_id": conversation_id, "messages": []}

    def archive_conversation(self, conversation_id: str) -> dict[str, Any]:
        with self._lock:
            blocked = self._conversation_switch_blocked()
            if blocked is not None:
                return {"ok": False, "error": blocked}
            try:
                current = self.controller.archive_conversation(str(conversation_id))
            except KeyError:
                return {"ok": False, "error": "Konuşma bulunamadı."}
            except ValueError:
                return {"ok": False, "error": "Konuşma kimliği geçersiz."}
        return {
            "ok": True,
            "current_archived": current,
            "conversation_id": str(self.controller.context.conversation_id),
        }

    # ------------------------------------------------------------------
    # Voice
    # ------------------------------------------------------------------
    def start_voice(self) -> dict[str, Any]:
        voice = self.controller.application.voice
        if voice is None:
            return {"ok": False, "error": "Sesli iletişim ayarlanmamış."}
        if self.controller.paused:
            return {"ok": False, "error": PAUSED_MESSAGE}

        def deliver(message: Any) -> None:
            self._push("voice_message", message)

        def done(future: Future[Any]) -> None:
            with self._lock:
                if self._voice_future is future:
                    self._voice_future = None
            current = self.controller.application.voice
            if current is not None and hasattr(current, "state_callback"):
                current.state_callback = None
            if current is not None and hasattr(current, "level_callback"):
                try:
                    current.level_callback = None
                except Exception:
                    pass
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
            # into the core visualization, and the microphone level while
            # it listens.
            if hasattr(voice, "state_callback"):
                voice.state_callback = lambda state: self._push(
                    "voice_phase",
                    {"phase": getattr(state, "value", str(state))},
                )
            if hasattr(voice, "level_callback"):
                try:
                    voice.level_callback = self._on_voice_level
                except Exception:
                    pass
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
        if self.controller.paused:
            return {"ok": False, "error": PAUSED_MESSAGE}

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
        if self.controller.paused:
            return {"ok": False, "error": PAUSED_MESSAGE}
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
    def _tool_description(self, tool_name: str) -> str | None:
        executor = getattr(self.controller.application, "tool_executor", None)
        try:
            registered = executor.get(tool_name)
        except Exception:
            return None
        description = getattr(
            getattr(registered, "definition", None), "description", None
        )
        return str(description) if description else None

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
                "source": request.request_source,
                "description": self._tool_description(request.tool_name),
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

    def permission_audit(self, limit: Any = 50) -> dict[str, Any]:
        """The permission engine's own audit trail, newest first."""
        try:
            bounded = max(1, min(int(limit), 200))
        except (TypeError, ValueError):
            bounded = 50
        executor = getattr(self.controller.application, "tool_executor", None)
        engine = getattr(executor, "permission_engine", None)
        try:
            entries = list(engine.audit_log())
        except Exception:
            return {"ok": False, "error": "İzin denetim kaydı okunamadı."}
        recent = entries[-bounded:][::-1]
        return {
            "ok": True,
            "entries": [
                {
                    "decision": getattr(item.decision, "value", str(item.decision)),
                    "reason": item.reason,
                    "operation": item.operation,
                    "risk": getattr(item.risk_level, "value", str(item.risk_level)),
                    "tool": item.tool_name,
                    "evaluated_at": item.evaluated_at,
                }
                for item in recent
            ],
        }

    # ------------------------------------------------------------------
    # Data refresh and system status
    # ------------------------------------------------------------------
    def refresh(self) -> dict[str, Any]:
        return {"snapshot": _jsonable(self.controller.snapshot())}

    def system_status(self) -> dict[str, Any]:
        """Live health checks, bounded metrics, and process figures.

        Everything here is measured: health checks run for real on the
        controller's loop, metrics come from the registry the core writes
        to, and process figures are read from the operating system.
        Whatever cannot be measured is reported as ``None``.
        """
        application = self.controller.application
        diagnostics = getattr(application, "diagnostics", None)
        health: dict[str, Any] | None = None
        health_error: str | None = None
        if diagnostics is not None:
            try:
                future = self.controller.submit_background(
                    diagnostics.health_report(), lambda done: None
                )
                report = future.result(timeout=HEALTH_REPORT_TIMEOUT_SECONDS)
                health = report.to_dict()
            except Exception as exc:
                health_error = (
                    f"Sağlık denetimi tamamlanamadı ({type(exc).__name__})."
                )
        metrics: dict[str, Any] | None = None
        integrity: bool | None = None
        event_count: int | None = None
        if diagnostics is not None:
            try:
                metrics = diagnostics.metrics.snapshot()
                integrity = bool(diagnostics.ledger.verify_integrity())
                event_count = len(diagnostics.ledger)
            except Exception:
                metrics = None
        provider: dict[str, Any] | None = None
        try:
            registry = application.provider_registry
            name = registry.get_default().name
            status = application.provider_gateway.health(name)
            provider = {
                "name": name,
                "circuit": getattr(
                    status.circuit_state, "value", str(status.circuit_state)
                ),
                "error": bool(getattr(status, "last_error", None)),
            }
        except Exception:
            provider = None
        admission: dict[str, Any] | None = None
        try:
            snapshot = application.engine.admission.snapshot()
            admission = {
                "active": snapshot.active,
                "waiting": snapshot.waiting,
                "accepted": snapshot.accepted,
                "rejected": snapshot.rejected,
                "max_concurrent": snapshot.max_concurrent,
                "max_queue": snapshot.max_queue,
            }
        except Exception:
            admission = None
        return _jsonable(
            {
                "ok": True,
                "observed_at": datetime.now().astimezone(),
                "health": health,
                "health_error": health_error,
                "metrics": metrics,
                "integrity": integrity,
                "event_count": event_count,
                "provider": provider,
                "admission": admission,
                "process": self._process.sample(),
            }
        )

    def diagnostic_events(
        self, limit: Any = 100, level: Any = None, component: Any = None
    ) -> dict[str, Any]:
        diagnostics = getattr(self.controller.application, "diagnostics", None)
        if diagnostics is None:
            return {"ok": False, "error": "Tanılama hizmeti kullanılamıyor."}
        try:
            bounded = max(1, min(int(limit), MAX_DIAGNOSTIC_EVENTS))
        except (TypeError, ValueError):
            bounded = 100
        try:
            data = diagnostics.events(
                limit=bounded,
                level=str(level) if level else None,
                component=str(component) if component else None,
            )
        except ValueError:
            return {"ok": False, "error": "Tanılama süzgeci geçersiz."}
        return {
            "ok": True,
            "integrity_valid": bool(data["integrity_valid"]),
            "events": _jsonable(data["events"]),
        }

    # ------------------------------------------------------------------
    # Memory
    # ------------------------------------------------------------------
    def list_memories(self, limit: Any = 200) -> dict[str, Any]:
        try:
            bounded = max(1, min(int(limit), MAX_MEMORY_LIST))
        except (TypeError, ValueError):
            bounded = 200
        service = self.controller.application.memory_service
        return {
            "ok": True,
            "memories": _jsonable(service.list(active_only=True, limit=bounded)),
        }

    def search_memories(self, query: str, limit: Any = 20) -> dict[str, Any]:
        normalized = str(query or "").strip()
        try:
            bounded = max(1, min(int(limit), 100))
        except (TypeError, ValueError):
            bounded = 20
        service = self.controller.application.memory_service
        if not normalized:
            return self.list_memories(limit=MAX_MEMORY_LIST)
        try:
            memories = service.search(normalized, limit=bounded)
        except Exception as exc:
            return {
                "ok": False,
                "error": f"Hafıza araması başarısız ({type(exc).__name__}).",
            }
        return {"ok": True, "memories": _jsonable(memories)}

    def forget_memory(self, memory_id: str) -> dict[str, Any]:
        """Deactivate a memory (recoverable); the record stays on disk."""
        service = self.controller.application.memory_service
        try:
            result = service.forget(str(memory_id))
        except KeyError:
            return {"ok": False, "error": "Anı bulunamadı."}
        except ValueError:
            return {"ok": False, "error": "Anı kimliği geçersiz."}
        self._push_snapshot()
        if not result.verified:
            return {"ok": False, "error": "Anı devre dışı bırakılamadı."}
        return {"ok": True, "message": "Anı unutuldu; artık yanıtlarda kullanılmayacak."}

    def delete_memory(self, memory_id: str, confirmed: bool = False) -> dict[str, Any]:
        """Permanently delete a memory; requires the page's confirmation."""
        if confirmed is not True:
            return {"ok": False, "error": "Silme işlemi onaylanmadı."}
        service = self.controller.application.memory_service
        try:
            result = service.delete(str(memory_id))
        except KeyError:
            return {"ok": False, "error": "Anı bulunamadı."}
        except ValueError:
            return {"ok": False, "error": "Anı kimliği geçersiz."}
        self._push_snapshot()
        if not result.verified:
            return {"ok": False, "error": "Anı silinemedi."}
        return {"ok": True, "message": "Anı kalıcı olarak silindi."}

    def update_memory(self, memory_id: str, content: str) -> dict[str, Any]:
        text = " ".join(str(content or "").split())
        if not text:
            return {"ok": False, "error": "Anı içeriği boş olamaz."}
        manager = self.controller.application.memory_manager
        try:
            manager.update(UUID(str(memory_id)), text)
        except KeyError:
            return {"ok": False, "error": "Anı bulunamadı."}
        except ValueError:
            return {
                "ok": False,
                "error": "Anı güncellenemedi: içerik kabul edilmedi.",
            }
        except Exception as exc:
            return {
                "ok": False,
                "error": f"Anı güncellenemedi ({type(exc).__name__}).",
            }
        self._push_snapshot()
        return {"ok": True, "message": "Anı güncellendi."}

    # ------------------------------------------------------------------
    # Window and pause
    # ------------------------------------------------------------------
    def set_paused(self, paused: bool) -> dict[str, Any]:
        """Pause or resume from the page; the tray, when present, follows."""
        target = paused is True
        handler = self._pause_handler
        if handler is not None:
            handler(target)
        else:
            self.controller.set_paused(target)
            if target:
                self.stop_voice()
            self._push_paused(target)
        return {"ok": True, "paused": target}

    def set_compact(self, enabled: bool) -> dict[str, Any]:
        """Switch between the full window and the small always-on-top one.

        pywebview applies ``on_top`` and the window state on the calling
        thread, and this method runs on a bridge worker thread, so the
        whole layout change is marshalled onto the WinForms UI thread
        through the native form's ``Invoke``; otherwise the message loop
        can freeze. Without a native form (tests) it runs directly.
        """
        window = self._window
        if window is None:
            return {"ok": False, "error": "Pencere hazır değil."}
        compact = enabled is True

        def apply_layout() -> None:
            if compact:
                width, height = COMPACT_WINDOW_SIZE
                window.restore()
                window.resize(width, height)
                bounds = _logical_screen_bounds()
                if bounds is not None:
                    left, top, screen_width, screen_height = bounds
                    window.move(
                        left + screen_width - width - COMPACT_WINDOW_MARGIN,
                        top + screen_height - height - COMPACT_TASKBAR_ALLOWANCE,
                    )
                window.on_top = True
            else:
                window.on_top = False
                window.maximize()

        try:
            _run_on_ui_thread(window, apply_layout)
        except Exception as exc:
            return {
                "ok": False,
                "error": f"Pencere düzeni değiştirilemedi ({type(exc).__name__}).",
            }
        self._compact = compact
        return {"ok": True, "compact": compact}

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
        self._observe_application()

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


def _run_on_ui_thread(window: Any, operation: Callable[[], None]) -> None:
    """Execute ``operation`` on the window's WinForms thread when it has one."""
    native = getattr(window, "native", None)
    invoke = getattr(native, "Invoke", None)
    if native is None or not callable(invoke):
        operation()
        return
    from System import Action  # pythonnet; present with pywebview

    failure: list[BaseException] = []

    def guarded() -> None:
        try:
            operation()
        except BaseException as exc:  # surfaced on the calling thread
            failure.append(exc)

    invoke(Action(guarded))
    if failure:
        raise failure[0]


def _logical_screen_bounds() -> tuple[int, int, int, int] | None:
    """Primary monitor bounds in logical pixels: (x, y, width, height).

    pywebview moves windows in logical units and multiplies by the
    monitor's DPI scale itself, while the metrics a DPI-aware process
    reads from Windows are physical, so the physical size is divided by
    the system scale first. Elsewhere the screens pywebview reported at
    start are used as they are.
    """
    if sys.platform == "win32":
        try:
            user32 = ctypes.windll.user32
            physical_width = int(user32.GetSystemMetrics(0))
            physical_height = int(user32.GetSystemMetrics(1))
            dpi = (
                int(user32.GetDpiForSystem())
                if hasattr(user32, "GetDpiForSystem")
                else 96
            )
            scale = max(0.5, (dpi or 96) / 96.0)
            if physical_width > 0 and physical_height > 0:
                return (
                    0,
                    0,
                    int(physical_width / scale),
                    int(physical_height / scale),
                )
        except Exception:
            pass
    screens = getattr(webview, "screens", None)
    try:
        screen = screens[0] if screens else None
    except (TypeError, IndexError):
        screen = None
    if screen is None:
        return None
    return (int(screen.x), int(screen.y), int(screen.width), int(screen.height))


class NovaTrayActions:
    """What the tray may do to the Nova window; every call is thread-safe."""

    def __init__(
        self, window: Any, bridge: NovaBridge, controller: DesktopController
    ) -> None:
        self._window = window
        self._bridge = bridge
        self._controller = controller
        self.exiting = False
        self.service: TrayService | None = None

    def open(self) -> None:
        self._window.show()
        try:
            native = getattr(self._window, "native", None)
            if native is not None and hasattr(native, "BeginInvoke"):
                from System import Action  # pythonnet; present with pywebview

                native.BeginInvoke(Action(native.Activate))
        except Exception:
            pass
        if self.service is not None:
            self.service.set_window_visible(True)

    def hide(self) -> None:
        self._window.hide()
        if self.service is not None:
            self.service.set_window_visible(False)

    def start_voice(self) -> None:
        """Open the window on the voice screen and start listening."""
        self.open()
        self._bridge._push("navigate", {"screen": "voice"})
        result = self._bridge.start_voice()
        if result.get("ok"):
            self._bridge._push("voice_state", {"active": True, "error": None})
        elif self.service is not None:
            self.service.notify(
                WINDOW_TITLE,
                str(result.get("error") or "Sesli oturum başlatılamadı."),
            )

    def set_paused(self, paused: bool) -> None:
        self._controller.set_paused(paused)
        if paused:
            self._bridge.stop_voice()
        self._bridge._push_paused(paused)
        if self.service is not None:
            self.service.set_paused(paused)

    def show_screen(self, screen: str) -> None:
        self.open()
        self._bridge._push("navigate", {"screen": screen})

    def exit(self) -> None:
        self.exiting = True
        self._window.destroy()


def _record_tray_problem(
    controller: DesktopController, item: TrayItem | None, exc: BaseException
) -> None:
    diagnostics = getattr(controller.application, "diagnostics", None)
    if diagnostics is None:
        return
    try:
        from app.diagnostics.models import DiagnosticLevel

        diagnostics.record(
            "ui",
            "tray.error",
            "System tray action failed; the window keeps working.",
            level=DiagnosticLevel.WARNING,
            attributes={
                "item": item.value if item is not None else None,
                "error": type(exc).__name__,
            },
        )
    except Exception:
        pass


def launch_nova(
    controller: DesktopController,
    api_settings: APISettingsService | None = None,
    *,
    settings: Any | None = None,
    activation_watch: Callable[[Callable[[], None]], None] | None = None,
    tray_backend_factory: Any = None,
) -> None:
    """Open the Nova window and run it until the user exits.

    With the tray enabled, closing the window hides it to the
    notification area and the tray's "Çıkış" performs the real exit.
    Resources are released exactly once, whether the window reports the
    ``closed`` event or ``webview.start`` simply returns.
    """
    web_root = resolve_web_root()
    bridge = NovaBridge(controller, api_settings)
    runtime_settings = (
        settings
        if settings is not None
        else getattr(controller.application, "settings", None)
    )
    tray_enabled = (
        bool(getattr(runtime_settings, "tray_enabled", False))
        and sys.platform == "win32"
    )
    close_to_tray = tray_enabled and bool(
        getattr(runtime_settings, "tray_close_to_tray", True)
    )

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
        min_size=(420, 300),
        background_color="#05080f",
        maximized=True,
        text_select=False,
    )
    bridge._attach(window)

    actions = NovaTrayActions(window, bridge, controller)
    # The page's own pause control goes through the same path as the
    # tray's, so the tray menu and the window never disagree.
    bridge._pause_handler = actions.set_paused
    tray: TrayService | None = None
    if tray_enabled:
        factory = (
            tray_backend_factory
            if tray_backend_factory is not None
            else default_backend_factory()
        )
        tray = TrayService(
            actions,
            backend_factory=factory,
            on_error=lambda item, exc: _record_tray_problem(controller, item, exc),
        )
        actions.service = tray
    hidden_notice_shown = False

    def on_closing() -> bool | None:
        nonlocal hidden_notice_shown
        if (
            close_to_tray
            and tray is not None
            and tray.active
            and not actions.exiting
        ):
            actions.hide()
            if not hidden_notice_shown:
                hidden_notice_shown = True
                tray.notify(WINDOW_TITLE, TRAY_HIDDEN_NOTICE)
            return False  # cancel: the tray keeps JARVIS alive
        return None

    released = False

    def release() -> None:
        nonlocal released
        if released:
            return
        released = True
        if tray is not None:
            tray.stop()
        bridge._shutdown()
        controller.close()

    window.events.closing += on_closing
    window.events.closed += release
    try:
        if tray is not None:
            try:
                tray.start()
            except Exception as exc:
                # No icon is better than no window: continue without it.
                _record_tray_problem(controller, None, exc)
                tray = None
                actions.service = None
        if activation_watch is not None:
            activation_watch(actions.open)
        webview.start(**start_options)
    finally:
        release()
