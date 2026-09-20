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
import hashlib
import importlib.metadata
import itertools
import json
import os
import platform
import re as _re
import shutil
import sys
import threading
import time
from collections.abc import Callable, Iterable, Mapping
from concurrent.futures import Future
from dataclasses import asdict, is_dataclass
from datetime import date, datetime
from enum import Enum
from math import isfinite
from pathlib import Path, PurePath
from queue import SimpleQueue
from threading import Lock, RLock
from typing import Any
from uuid import UUID, uuid4

import webview

from app.config.paths import default_state_directory
from app.core.models import Context, Request, RequestSource
from app.notifications import NotificationCenter, NotificationStore, ReminderWatch
from app.research.sources import SOURCE_CATALOG, normalize_site, parse_sources
from app.reminders.service import show_windows_toast
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
    "css/medical.css",
    "css/study.css",
    "css/academy.css",
    "css/research.css",
    "css/phone.css",
    "js/foundation.js",
    "js/toolbox.js",
    "js/bridge.js",
    "js/presence.js",
    "js/shell.js",
    "js/conversation.js",
    "js/activity.js",
    "js/panels.js",
    "js/medical.js",
    "js/study.js",
    "js/medcalc.js",
    "js/rooms.js",
    "js/academy.js",
    "js/research.js",
    "js/main.js",
)
WEB_RELATIVE_PATH = PurePath("app", "ui", "nova", "web")
SOURCE_WEB_ROOT = Path(__file__).resolve().parent / "web"
# Markup stripped before speech, the same set the phone strips
# (app/mobile/server.py): the voice reads prose, not asterisks.
_MARKDOWN_NOISE = _re.compile(r"[*_`#>]+")

WINDOW_TITLE = "JARVIS"
READY_STATUS = "LOCAL CORE READY"
WORKING_STATUS = "PROCESSING"
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
NOTIFICATION_LIST_LIMIT = 100
ROUTINE_POLL_SECONDS = 30.0
# A due routine found while the desktop is paused or busy is tried again
# after this long rather than skipped to its next slot.
ROUTINE_DEFER_SECONDS = 90.0
ROUTINE_UNAVAILABLE = "Rutinler bu ortamda kullanılamıyor."
MEDICAL_UNAVAILABLE = "Tıp Akademisi bu ortamda kullanılamıyor."


def _narration_error() -> type[Exception]:
    from app.medical.narration import NarrationError

    return NarrationError

# Study-layer operations that run long enough (model calls, PDF work) to
# belong on the async runner; the page hears the outcome as a push.
MEDICAL_BACKGROUND_ACTIONS: frozenset[str] = frozenset(
    {
        "process_document",
        "analyze_document",
        "compare_document",
        "create_note",
        "create_exam",
        "backup_now",
        "import_questions",
        "import_folder",
        "process_lecture_set",
        "mine_questions",
        "narration_start",
        "prepare_narration",
        "continue_processing",
    }
)
# Destructive study-layer operations; the page must pass confirmed=True.
MEDICAL_CONFIRMED_ACTIONS: frozenset[str] = frozenset(
    {
        "delete_document",
        "delete_note",
        "delete_exam",
        "delete_question",
        "delete_professor",
        "reset_professor",
        "delete_lecture_set",
    }
)
MEDICAL_DOCUMENT_TYPES = ("Ders materyali (*.pdf;*.ppt;*.pptx;*.txt;*.md)", "Tüm dosyalar (*.*)")
MEDICAL_QUESTION_TYPES = (
    "Sınav dosyası (*.pdf;*.txt;*.md;*.png;*.jpg;*.jpeg;*.webp)",
    "Tüm dosyalar (*.*)",
)
OS_NOTIFICATION_MAX_IN_FLIGHT = 4
# User-visible titles of the notifications the bridge raises itself.
NOTIFICATION_TITLES: Mapping[str, str] = {
    "reminder": "Hatırlatıcı",
    "approval": "Onay bekleniyor",
    "reply": "Yanıt hazır",
    "vision": "Görüş sonucu hazır",
    "research": "Araştırma tamamlandı",
    "observation": "Ekran gözlemi",
    "brief": "Günün özeti",
}
DIAGNOSTIC_NOTIFICATION_TITLES: Mapping[str, str] = {
    "warning": "Uyarı",
    "error": "Hata",
    "critical": "Kritik olay",
}
SHUTDOWN_LOCK_TIMEOUT_SECONDS = 2.0
MAX_RESEARCH_SOURCES = 10


def research_source_limit(requested: object, service_maximum: object) -> int:
    """How many sources a research request may ask for.

    The page offers up to ten; the service refuses more than it was
    configured for. Asking is clamped rather than refused, so "8 kaynak"
    on a service capped at 5 gives five sources instead of an error.
    """
    try:
        wanted = int(requested)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        wanted = 5
    try:
        ceiling = int(service_maximum)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        ceiling = MAX_RESEARCH_SOURCES
    return max(1, min(wanted, MAX_RESEARCH_SOURCES, max(1, ceiling)))
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
    "voice_trailing_silence_seconds",
    "voice_provisional_silence_seconds",
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
    "notifications_os_enabled",
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


WINDOW_GEOMETRY_FILE = "window.json"
DAILY_BRIEF_STAMP_FILE = "daily-brief.json"
DAILY_BRIEF_POLL_SECONDS = 30.0


def daily_brief_due(now: datetime, target: str, stamp: str | None) -> bool:
    """True when the local clock passed today's target and nothing was sent today.

    A malformed target disables the feature rather than guessing a time.
    """
    parts = str(target or "").strip().split(":")
    if len(parts) != 2:
        return False
    try:
        hour, minute = int(parts[0]), int(parts[1])
    except ValueError:
        return False
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return False
    if stamp == now.date().isoformat():
        return False
    return (now.hour, now.minute) >= (hour, minute)


# The home screen's remote (and the phone, which loads the same page)
# may run only these: media transport and the delegation's own status
# and stop. Every one is LOW risk or read-only and needs no approval;
# anything else on the executor stays a chat request.
REMOTE_TOOLS: frozenset[str] = frozenset({
    "spotify_now_playing",
    "spotify_play_pause",
    "spotify_next_track",
    "spotify_previous_track",
    "spotify_set_volume",
    "spotify_seek",
    "spotify_shuffle",
    "spotify_repeat",
    "spotify_like_track",
    "spotify_library",
    "spotify_play_library",
    "spotify_queue_track",
    "spotify_sleep_timer",
    "spotify_cancel_sleep_timer",
    "whatsapp_delegation_status",
    "whatsapp_stop_delegation",
})


def compose_voice_state_callback(push: Callable[[Any], None], ducker: Any | None) -> Callable[[Any], None]:
    """The page hears every voice phase; the music ducks on SPEAKING and
    comes back after. A failing ducker must never break the voice turn."""

    def callback(state: Any) -> None:
        push(state)
        if ducker is not None:
            try:
                ducker.on_voice_state(state)
            except Exception:
                pass

    return callback


def brief_notification_body(brief: Mapping[str, Any]) -> str:
    """The day's summary as one honest notification line.

    Counts what exists, in words; a day with nothing pending says so
    instead of inventing urgency.
    """
    parts: list[str] = []
    reminders = brief.get("reminders") or []
    if reminders:
        parts.append(f"{len(reminders)} hatırlatıcı")
    medical = brief.get("medical") or {}
    countdown = medical.get("countdown") or None
    if countdown:
        parts.append(f"{countdown.get('name')}: {countdown.get('days_left')} gün kaldı")
    next_activity = medical.get("next_activity") or None
    if next_activity:
        parts.append(f"sırada {next_activity.get('title')}")
    cards = int(medical.get("cards_waiting") or 0)
    if cards:
        parts.append(f"{cards} kart tekrar bekliyor")
    findings = int(medical.get("findings_open") or 0)
    if findings:
        parts.append(f"{findings} açık bulgu")
    tasks = int(brief.get("tasks_open") or 0)
    if tasks:
        parts.append(f"{tasks} açık görev")
    # The almanac rides along when it answered; its absence stays silent
    # here because the brief page already prints the reason in full.
    ambient: list[str] = []
    almanac = brief.get("almanac") or {}
    weather = almanac.get("weather") or {}
    if weather.get("available") and weather.get("temperature") is not None:
        label = str(weather.get("label") or "").strip()
        ambient.append(f"{weather.get('city')} {weather.get('temperature')}°" + (f", {label}" if label else ""))
    rates = almanac.get("rates") or {}
    if rates.get("available") and rates.get("usd_try") is not None:
        lira = str(rates.get("usd_try")).replace(".", ",")
        ambient.append(f"1 $ = {lira} ₺")
    if not parts and not ambient:
        return "Bugün için bekleyen bir şey görünmüyor."
    if not parts:
        parts.append("Bekleyen iş yok")
    return " · ".join(parts + ambient)[:220]

MIN_REMEMBERED_SIZE = (900, 600)


def load_window_geometry(storage: Path) -> dict[str, int] | None:
    """The window frame the user last left, when it still makes sense.

    A frame smaller than the minimum or thrown far off any screen falls back
    to the defaults rather than opening an unusable window.
    """
    try:
        raw = json.loads((storage / WINDOW_GEOMETRY_FILE).read_text(encoding="utf-8"))
        frame = {key: int(raw[key]) for key in ("x", "y", "width", "height")}
    except (OSError, ValueError, KeyError, TypeError):
        return None
    if frame["width"] < MIN_REMEMBERED_SIZE[0] or frame["height"] < MIN_REMEMBERED_SIZE[1]:
        return None
    if not (-32 <= frame["x"] <= 20000 and -32 <= frame["y"] <= 20000):
        return None
    return frame


def save_window_geometry(storage: Path, window: Any) -> bool:
    """Remember the frame; never let a failure here touch the close path."""
    try:
        frame = {"x": int(window.x), "y": int(window.y), "width": int(window.width), "height": int(window.height)}
        if frame["width"] < MIN_REMEMBERED_SIZE[0] or frame["height"] < MIN_REMEMBERED_SIZE[1]:
            return False
        storage.mkdir(parents=True, exist_ok=True)
        (storage / WINDOW_GEOMETRY_FILE).write_text(json.dumps(frame), encoding="utf-8")
        return True
    except Exception:
        return False


ASSET_STAMP_FILE = "assets.stamp"
WEBVIEW_CACHE_DIRECTORIES = ("Cache", "Code Cache")


def asset_stamp(web_root: Path) -> str:
    """A digest of every page asset's path, size and mtime."""
    digest = hashlib.sha256()
    for path in sorted(p for p in web_root.rglob("*") if p.is_file()):
        stat = path.stat()
        digest.update(
            f"{path.relative_to(web_root).as_posix()}|{stat.st_size}|{stat.st_mtime_ns}\n".encode()
        )
    return digest.hexdigest()[:24]


def refresh_webview_cache(storage: Path, web_root: Path) -> bool:
    """Drop the profile's HTTP and code caches when the assets changed.

    WebView2 caches file:// scripts and styles inside the profile, so an
    updated build could otherwise keep running the previous page. Runs
    before the window exists (nothing holds the files); a stamp of the
    asset tree decides, so an unchanged build keeps its warm cache.
    Returns True when the caches were cleared.
    """
    if not web_root.is_dir():
        return False
    try:
        current = asset_stamp(web_root)
    except OSError:
        return False
    stamp_path = storage / ASSET_STAMP_FILE
    try:
        previous = stamp_path.read_text(encoding="utf-8").strip()
    except OSError:
        previous = None
    if previous == current:
        return False
    profile = storage / "EBWebView" / "Default"
    for name in WEBVIEW_CACHE_DIRECTORIES:
        shutil.rmtree(profile / name, ignore_errors=True)
    try:
        stamp_path.write_text(current, encoding="utf-8")
    except OSError:
        pass
    return True


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


def _plain_text(text: str) -> str:
    """Chat Markdown as the notification centre shows it: plain.

    A quiz question is written for the chat, where ``**Soru 1**`` renders
    bold; the notification list renders text as it is, so the markers
    come off here rather than showing as literal asterisks.
    """
    plain = _re.sub(r"\*\*(.+?)\*\*", r"\1", text)
    plain = _re.sub(r"__(.+?)__", r"\1", plain)
    plain = _re.sub(r"`(.+?)`", r"\1", plain)
    plain = _re.sub(r"^#{1,6}\s*", "", plain, flags=_re.MULTILINE)
    return plain.strip()


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


def _message_field(message: Any, name: str) -> Any:
    if isinstance(message, Mapping):
        return message.get(name)
    return getattr(message, name, None)


class WindowWorker:
    """One thread for work that must not run on the window's UI thread.

    pywebview delivers ``closing``, ``minimized`` and the other window events
    on the WinForms UI thread, and a bridge push has to evaluate JavaScript on
    that same thread. Doing either inline means the UI thread waits for itself:
    the window disappears into the tray and never comes back, and the next
    launch hands its activation signal to an instance that can no longer
    answer. Everything that touches the window from a UI callback is left
    here instead.

    One thread and one queue, so a hide and a show can never overtake each
    other, and a job already waiting under the same key is not queued twice —
    hammering the close button schedules one hide.
    """

    def __init__(self, on_error: Callable[[str, BaseException], None] | None = None) -> None:
        self._queue: "SimpleQueue[tuple[str, Callable[[], None]] | None]" = SimpleQueue()
        self._on_error = on_error
        self._lock = Lock()
        self._pending: set[str] = set()
        self._thread: threading.Thread | None = None
        self._stopped = False
        self._idle = threading.Event()
        self._idle.set()

    def submit(self, key: str, job: Callable[[], None]) -> bool:
        """Queue ``job``; ``False`` when one under ``key`` is already waiting."""
        with self._lock:
            if self._stopped or key in self._pending:
                return False
            self._pending.add(key)
            self._idle.clear()
            if self._thread is None:
                self._thread = threading.Thread(target=self._run, name="nova-window", daemon=True)
                self._thread.start()
        self._queue.put((key, job))
        return True

    def _run(self) -> None:
        while True:
            item = self._queue.get()
            if item is None:
                return
            key, job = item
            try:
                job()
            except Exception as exc:  # never kill the thread: report and go on
                if self._on_error is not None:
                    try:
                        self._on_error(key, exc)
                    except Exception:
                        pass
            finally:
                with self._lock:
                    self._pending.discard(key)
                    if not self._pending:
                        self._idle.set()

    def wait_idle(self, timeout: float = 5.0) -> bool:
        return self._idle.wait(timeout)

    def stop(self, timeout: float = 5.0) -> None:
        with self._lock:
            if self._stopped:
                return
            self._stopped = True
            thread = self._thread
        if thread is None:
            return
        self._queue.put(None)
        thread.join(timeout)


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
        # that is still holding this lock inside start_voice or the routine
        # runner, and that callback pushes to the window. Both of them keep
        # the lock across their submission, so a window round-trip under it
        # is rare but real; submit_command is the one that hands its
        # coroutine over with the lock released, because its bracket push
        # blocks on WebView2 every single turn while done() waits on this
        # same lock from the core's event-loop thread.
        self._lock = RLock()
        self._push_lock = Lock()
        # The window's UI thread, asked of the window itself (see
        # :meth:`_note_ui_thread`). A push raised on that thread is deferred
        # instead of evaluated, or the thread would wait for the JavaScript
        # result it has to deliver.
        self._ui_thread_id: int | None = None
        # False until the form itself marshalled the answer back.
        self._ui_thread_confirmed = False
        # Pushes carry news, so no two of them are interchangeable; the
        # counter keeps the worker's dedupe from mistaking one for another.
        self._push_serial = itertools.count()
        self._window_worker = WindowWorker(
            lambda key, exc: self._record_ui_event(
                "window.worker_failed",
                "A window job failed off the UI thread.",
                job=key,
                error=type(exc).__name__,
            )
        )
        # The engine hands the callback the CUMULATIVE answer so far, not
        # a delta, and the page replaces the bubble with whatever arrives.
        # Only the newest snapshot is worth sending; joining them printed
        # "BuBu birBu bir deneme" while JARVIS was typing.
        self._stream_latest: str = ""
        # The previous GetSystemTimes sample; the pulse's CPU figure
        # is the delta between two beats.
        self._pulse_times: tuple[int, int, int] | None = None
        self._stream_last_flush = 0.0
        self._command_future: Future[Any] | None = None
        # A turn claimed by submit_command but not yet handed to the runner;
        # see :meth:`_command_active`.
        self._command_opening = False
        self._voice_future: Future[Any] | None = None
        self._approvals: dict[str, Future[bool]] = {}
        # Other surfaces (the mobile companion) watch the same approval
        # requests; a decision from any of them resolves the one future.
        self._approval_watchers: list[Callable[[str, dict[str, Any]], None]] = []
        self._detachers: list[Callable[[], None]] = []
        self._voice_level_last = 0.0
        self._compact = False
        self._pause_handler: Callable[[bool], None] | None = None
        self._uri_launcher: Any | None = None
        # The mobile companion server, when the desktop started one.
        self._mobile: Any | None = None
        self._started_at = datetime.now().astimezone()
        self._webview2_version: str | None | bool = False  # False: not probed
        self._process = ProcessMonitor()
        # Attention: the in-session notification centre, whether the
        # window is currently attended (visible and not minimized), the
        # native notifier launch_nova installs for unattended moments,
        # and the daemon watch that delivers due reminders.
        self._notifications = NotificationCenter(store=self._notification_store())
        self._notifications.subscribe(self._on_notification)
        self._attended = True
        self._os_notifier: Callable[[str, str], Any] | None = None
        self._os_lock = Lock()
        self._os_in_flight = 0
        self._reminder_watch: ReminderWatch | None = None
        self._routine_watch: ReminderWatch | None = None
        self._routine_future: Future[Any] | None = None
        controller.approval_callback = self._request_approval
        self._observe_application()

    # ------------------------------------------------------------------
    # Python -> JS
    # ------------------------------------------------------------------
    def _attach(self, window: webview.Window) -> None:
        self._window = window

    def _note_ui_thread(self) -> None:
        """Remember the thread the window's message loop runs on.

        pywebview delivers a non-blocking window event (``shown``,
        ``minimized``, ``restored``) on a thread it starts for that one
        callback and then drops, so the ident read here is usually a thread
        that is already gone by the time :meth:`_push` compares against it.
        The window is asked instead: an operation marshalled through the
        native form runs on the message loop itself, and that is the only
        answer taken as final. Without a native form — the tests, any
        backend that is not WinForms — the calling thread is the only
        answer there is, and it is the one the blocking ``closing`` event
        arrives on.

        Every other answer stays provisional, because WinForms
        ``Control.InvokeRequired`` is false on *every* thread while no
        handle exists anywhere in the parent chain: a window event that
        arrives before the handle would otherwise brand a throwaway thread
        as the message loop for the rest of the session. Such an answer is
        still taken — a guess beats no guard at all — but nothing is
        marshalled for it, so the unconditional assignment above is the
        whole defence: every later event overwrites the unconfirmed ident,
        and deleting that overwrite brings the permanent wrong ident back.
        The form is asked only by an event it answers ``InvokeRequired``
        true for, which cannot happen before the handle exists; that one
        marshalled answer settles the ident for good, and from then on only
        a thread the form answers false for rewrites it, which once the
        handle exists is the message loop itself.

        ``Control.Invoke`` has no timeout, so the marshalled ask blocks for
        as long as the message loop is stopped while the handle still
        lives; a pywebview backend whose event thread is not a daemon can
        pin the process there.
        """
        window = self._window
        native = getattr(window, "native", None)
        if native is None or not getattr(native, "InvokeRequired", False):
            self._ui_thread_id = threading.get_ident()
            return
        if self._ui_thread_confirmed:
            return

        def confirm() -> None:
            # Reached on whatever thread the form marshalled to, so the form
            # is asked once more there: an ask that was never marshalled at
            # all would otherwise confirm the asking thread.
            if getattr(native, "InvokeRequired", False):
                raise RuntimeError("the window did not marshal the question")
            self._ui_thread_id = threading.get_ident()
            self._ui_thread_confirmed = True

        try:
            _run_on_ui_thread(window, confirm)
        except Exception as exc:
            # Nothing was learned, and the guard in _push is only as good as
            # this ident: say so instead of leaving the application without
            # one and no trace of why.
            self._record_ui_event(
                "window.ui_thread_unknown",
                "The window did not say which thread its message loop runs on.",
                error=type(exc).__name__,
                ident=self._ui_thread_id,
            )

    def _defer(self, key: str, job: Callable[[], None]) -> bool:
        """Run ``job`` off the UI thread; ``False`` if one is already queued.

        A lifecycle hook for :func:`launch_nova`, not part of the page's API.
        """
        return self._window_worker.submit(key, job)

    def _push(self, kind: str, payload: Any = None) -> None:
        window = self._window
        if window is None or not self._ready or self._closing:
            return
        message = json.dumps(
            {"kind": kind, "payload": _jsonable(payload)},
            ensure_ascii=True,
        )
        mobile = self._mobile
        if mobile is not None:
            # A paired phone shows the same page: it gets every push the
            # window gets. Queue puts only, safe from any thread.
            try:
                mobile.broadcast_push(kind, _jsonable(payload))
            except Exception:
                pass
        if self._ui_thread_id is not None and threading.get_ident() == self._ui_thread_id:
            # Evaluating here would block the thread that has to run the
            # script. A window event that records a diagnostic — hiding to
            # the tray, minimising — reaches this line, and inline it hangs
            # the whole application. The key is serial because the worker
            # drops a job whose key is already waiting: two notifications
            # that merely share a kind are still two notifications.
            self._window_worker.submit(
                f"push:{kind}:{next(self._push_serial)}",
                lambda: self._evaluate_push(message),
            )
            return
        self._evaluate_push(message)

    def _evaluate_push(self, message: str) -> None:
        window = self._window
        if window is None or self._closing:
            return
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
        watcher = getattr(application, "screen_watcher", None)
        if watcher is not None and hasattr(watcher, "_notify"):
            watcher._notify = self._on_screen_observation
            self._detachers.append(lambda: setattr(watcher, "_notify", None))
        academy = getattr(application, "medical", None)
        subscribe = getattr(academy, "subscribe", None)
        if callable(subscribe):
            self._detachers.append(subscribe(self._on_medical_event))
        if self._ready:
            self._start_reminder_watch()

    def _detach_observers(self) -> None:
        detachers, self._detachers = self._detachers, []
        for detach in detachers:
            try:
                detach()
            except Exception:
                pass
        self._stop_reminder_watch()

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
        # A finished web research hands the page its sources, so the next
        # assistant reply can wear them. Only what the tool actually
        # returned is forwarded; no result, no chips.
        if (
            event.tool_name == "research_web"
            and result is not None
            and not result.error
            and isinstance(result.data, Mapping)
        ):
            sources = [
                {
                    "title": str(item.get("title") or ""),
                    "url": str(item.get("url") or ""),
                }
                for item in list(result.data.get("sources") or [])[:5]
                if isinstance(item, Mapping)
            ]
            if sources:
                self._push(
                    "research_sources",
                    {
                        "query": str(result.data.get("question") or ""),
                        "sources": sources,
                    },
                )

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
        level = str(getattr(event.level, "value", event.level)).lower()
        title = DIAGNOSTIC_NOTIFICATION_TITLES.get(level)
        if title is None:
            return
        self._publish(
            "diagnostic",
            title,
            f"{event.component} · {event.name}: {event.message}",
            severity="warning" if level == "warning" else "error",
            target="diagnostics",
            data={"component": event.component, "name": event.name},
            dedupe_key=f"diagnostic:{event.component}:{event.name}",
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
    # Attention: notifications, reminders and the unattended window
    # ------------------------------------------------------------------
    def _publish(
        self,
        kind: str,
        title: str,
        body: str = "",
        *,
        severity: str = "info",
        target: str | None = None,
        reference: str | None = None,
        data: Mapping[str, Any] | None = None,
        dedupe_key: str | None = None,
        alert: bool = False,
    ) -> None:
        """Record a notification; ``alert`` also reaches the OS when
        nobody is looking at the window."""
        try:
            entry = self._notifications.publish(
                kind,
                title,
                body,
                severity=severity,
                target=target,
                reference=reference,
                data=dict(data) if data else None,
                dedupe_key=dedupe_key,
            )
        except ValueError:
            return
        if alert and not self._attended:
            self._notify_os(entry.title, entry.body)

    def _notification_store(self) -> NotificationStore | None:
        """The centre's store below the state directory; None when the
        settings clear the path or the file cannot be opened."""
        settings = getattr(self.controller.application, "settings", None)
        path = getattr(settings, "notifications_database_path", None)
        if path is None:
            path = str(default_state_directory() / "jarvis_notifications.sqlite3")
        if not str(path).strip():
            return None
        try:
            return NotificationStore(path)
        except Exception:
            return None

    def _on_notification(self, entry: Any, unread: int) -> None:
        self._push(
            "notification",
            {
                "notification": entry.to_dict(),
                "unread": int(unread),
                "attended": self._attended,
            },
        )

    def _os_notifications_enabled(self) -> bool:
        settings = getattr(self.controller.application, "settings", None)
        return bool(getattr(settings, "notifications_os_enabled", True))

    def _notify_os(self, title: str, body: str) -> bool:
        """Best-effort native notification on its own thread.

        Publishers run on core threads (the reminder watch, the
        diagnostics ledger, the screen watcher), and a toast can block
        for seconds, so it never runs inline; a small in-flight bound
        keeps a burst from spawning threads without end.
        """
        notifier = self._os_notifier
        if notifier is None or not self._os_notifications_enabled():
            return False
        with self._os_lock:
            if self._os_in_flight >= OS_NOTIFICATION_MAX_IN_FLIGHT:
                return False
            self._os_in_flight += 1

        def run() -> None:
            outcome: Any = False
            try:
                outcome = notifier(title, body)
            except Exception:
                outcome = False
            finally:
                with self._os_lock:
                    self._os_in_flight -= 1
            # The body is user content and stays out of the ledger.
            self._record_ui_event(
                "notification.native",
                "A native notification was attempted for an unattended window.",
                delivered=bool(outcome),
                via=outcome if isinstance(outcome, str) else None,
                title=title,
            )

        threading.Thread(
            target=run, name="jarvis-os-notification", daemon=True
        ).start()
        return True

    def _set_window_visible(self, visible: bool) -> None:
        attended = bool(visible)
        if attended == self._attended:
            return
        self._attended = attended
        self._record_ui_event(
            "window.visibility",
            "The Nova window became attended."
            if attended
            else "The Nova window became unattended.",
            attended=attended,
        )

    def _warm_up_provider(self) -> None:
        """Pay the first connection's set-up now, not on the first command.

        Runs on the controller's runner loop because the provider's pooled
        connection belongs to the loop that will use it.
        """
        application = self.controller.application
        warmers = [
            warm
            for warm in (
                getattr(getattr(application, "provider_gateway", None), "warm_up", None),
                getattr(getattr(application, "voice", None), "warm_up", None),
            )
            if callable(warm)
        ]
        if not warmers:
            return
        started = time.monotonic()

        async def warm_all() -> dict[str, bool]:
            results: dict[str, bool] = {}
            for warm in warmers:
                try:
                    outcome = await warm()
                except Exception:
                    continue
                if isinstance(outcome, dict):
                    results.update(outcome)
            return results

        def done(future: Future[Any]) -> None:
            if future.cancelled():
                return
            try:
                results = future.result()
            except Exception as exc:
                results = {"error": type(exc).__name__}
            self._record_ui_event(
                "provider.warm_up",
                "Provider connection warm-up finished.",
                seconds=round(time.monotonic() - started, 3),
                results=dict(results) if isinstance(results, dict) else {},
            )

        try:
            self.controller.submit_background(warm_all(), done)
        except RuntimeError:
            pass

    def _start_reminder_watch(self) -> None:
        """(Re)start delivery for the current application's reminders."""
        self._stop_reminder_watch()
        reminders = getattr(self.controller.application, "reminders", None)
        if reminders is None or not callable(getattr(reminders, "claim_due", None)):
            return
        watch = ReminderWatch(reminders, self._deliver_reminder)
        self._reminder_watch = watch
        watch.start()
        self._start_routine_watch()

    def _stop_reminder_watch(self) -> None:
        watch, self._reminder_watch = self._reminder_watch, None
        if watch is not None:
            watch.stop()
        self._stop_routine_watch()

    # ------------------------------------------------------------------
    # Routines: prompts JARVIS runs on its own schedule
    # ------------------------------------------------------------------
    def _start_routine_watch(self) -> None:
        self._stop_routine_watch()
        routines = getattr(self.controller.application, "routines", None)
        if routines is None or not callable(getattr(routines, "claim_due", None)):
            return
        watch = ReminderWatch(
            routines, self._run_routine, interval_seconds=ROUTINE_POLL_SECONDS
        )
        self._routine_watch = watch
        watch.start()

    def _stop_routine_watch(self) -> None:
        watch, self._routine_watch = self._routine_watch, None
        if watch is not None:
            watch.stop()

    def _run_routine(self, routine: Mapping[str, Any]) -> None:
        """Run one due routine through the core like a typed command.

        The prompt goes through the same engine, permission engine and
        approval overlay; an approval nobody answers fails closed. The
        outcome lands in the notification centre (and reaches the OS when
        the window is unattended). A paused or busy desktop defers the
        routine instead of dropping it.
        """
        routines = getattr(self.controller.application, "routines", None)
        routine_id = str(routine.get("routine_id", ""))
        name = str(routine.get("name", "")).strip() or "Rutin"
        prompt = str(routine.get("prompt", "")).strip()
        if routines is None or not routine_id or not prompt:
            return
        with self._lock:
            blocked = (
                self._closing
                or self.controller.paused
                or self._command_active()
                or _active(self._routine_future)
                or self.controller.state.busy
            )
            if not blocked:
                stored_conversation = routine.get("conversation_id")
                try:
                    context = (
                        Context(conversation_id=UUID(str(stored_conversation)))
                        if stored_conversation
                        else Context()
                    )
                except ValueError:
                    context = Context()
                request = Request(
                    prompt,
                    source=RequestSource.SYSTEM,
                    metadata={"routine_id": routine_id, "routine_name": name},
                )
                started = time.monotonic()

                def done(future: Future[Any]) -> None:
                    with self._lock:
                        if self._routine_future is future:
                            self._routine_future = None
                    if future.cancelled():
                        return
                    try:
                        response = future.result()
                    except Exception as exc:
                        outcome, text = "failed", (
                            f"Rutin çalıştırılamadı ({type(exc).__name__})."
                        )
                        severity = "error"
                    else:
                        metadata = _message_field(response, "metadata")
                        outcome = str(
                            metadata.get("outcome", "completed")
                            if isinstance(metadata, Mapping)
                            else "completed"
                        )
                        text = str(_message_field(response, "text") or "").strip()
                        severity = "info" if outcome == "completed" else "warning"
                    try:
                        routines.record_run(
                            routine_id,
                            outcome=outcome,
                            summary=text,
                            conversation_id=str(context.conversation_id),
                        )
                    except Exception:
                        pass
                    self._record_ui_event(
                        "routine.completed",
                        "A routine finished.",
                        routine_id=routine_id,
                        outcome=outcome,
                        seconds=round(time.monotonic() - started, 3),
                    )
                    self._publish(
                        "task",
                        f"Rutin · {name}",
                        text or "Rutin tamamlandı.",
                        severity=severity,
                        target="chat",
                        reference=routine_id,
                        data={
                            "routine_id": routine_id,
                            "conversation_id": str(context.conversation_id),
                            "outcome": outcome,
                        },
                        alert=True,
                    )
                    self._push_snapshot()

                try:
                    self._routine_future = self.controller.submit_background(
                        self.controller.application.engine.handle(
                            request,
                            context,
                            approval_callback=self._request_approval,
                        ),
                        done,
                    )
                except RuntimeError:
                    try:
                        routines.defer(routine_id, ROUTINE_DEFER_SECONDS)
                    except Exception:
                        pass
        if blocked:
            # Reported with the lock released: the ledger calls its
            # listeners on the recording thread, so this diagnostic travels
            # straight into a blocking window round-trip, and the very turn
            # the routine is waiting for finishes by taking this same lock.
            try:
                routines.defer(routine_id, ROUTINE_DEFER_SECONDS)
            except Exception:
                pass
            self._record_ui_event(
                "routine.deferred",
                "A due routine waits because the desktop is paused or busy.",
                routine_id=routine_id,
            )
            return
        self._record_ui_event(
            "routine.started", "A routine started.", routine_id=routine_id
        )

    def _routines_payload(self) -> dict[str, Any]:
        routines = getattr(self.controller.application, "routines", None)
        if routines is None:
            return {"available": False, "routines": []}
        try:
            items = routines.list()
        except Exception:
            return {"available": False, "routines": []}
        return {"available": True, "routines": _jsonable(items)}

    def list_routines(self) -> dict[str, Any]:
        payload = self._routines_payload()
        if not payload["available"]:
            return {"ok": False, "error": ROUTINE_UNAVAILABLE, **payload}
        return {"ok": True, **payload}

    def create_routine(
        self,
        name: str,
        prompt: str,
        at: Any = "",
        every_minutes: Any = 0,
    ) -> dict[str, Any]:
        """The Tasks screen's editor: the same bounded service call the
        create_routine tool makes, with the same validation messages."""
        routines = getattr(self.controller.application, "routines", None)
        if routines is None:
            return {"ok": False, "error": ROUTINE_UNAVAILABLE}
        try:
            minutes = int(every_minutes or 0)
        except (TypeError, ValueError):
            minutes = 0
        result = routines.create(
            str(name or ""), str(prompt or ""), at=str(at or ""), every_minutes=minutes
        )
        if not result.succeeded:
            return {"ok": False, "error": result.message or "Rutin kurulamadı."}
        self._record_ui_event(
            "routine.created",
            "A routine was created from the desktop.",
            routine_id=str((result.data or {}).get("routine_id", "")),
            schedule=str((result.data or {}).get("schedule", "")),
        )
        return {"ok": True, "message": result.message, **self._routines_payload()}

    def delete_routine(self, routine_id: str, confirmed: Any = False) -> dict[str, Any]:
        if confirmed is not True:
            return {"ok": False, "error": "Rutin silme onaylanmadı."}
        routines = getattr(self.controller.application, "routines", None)
        if routines is None:
            return {"ok": False, "error": ROUTINE_UNAVAILABLE}
        result = routines.delete(str(routine_id or ""))
        if not result.succeeded:
            return {"ok": False, "error": result.message or "Rutin silinemedi."}
        self._record_ui_event(
            "routine.deleted",
            "A routine was deleted from the desktop.",
            routine_id=str(routine_id),
        )
        return {"ok": True, **self._routines_payload()}

    # ------------------------------------------------------------------
    # Medical Academy
    # ------------------------------------------------------------------
    def _medical(self) -> Any | None:
        return getattr(self.controller.application, "medical", None)

    def _on_medical_event(self, event: Mapping[str, Any]) -> None:
        """Forward one study-layer event to the page, and to the user when
        a long pipeline they started has finished while they looked away."""
        payload = dict(event)
        self._push("medical", payload)
        kind = str(payload.get("kind", ""))
        if payload.get("quiet"):
            # One document of a batch: the batch notifies once when it ends.
            return
        titles = {
            "document_ready": ("Belge hazır", "{title} işlendi ve dizinlendi."),
            "document_analyzed": ("Belge analiz edildi", "{title}: konular ve terimler çıkarıldı."),
            "comparison_ready": ("Karşılaştırma hazır", "{title}: {findings} bulgu işaretlendi."),
            "exam_ready": ("Sınav hazır", "{title}: {count} soru."),
            "exam_finished": ("Sınav bitti", "{title}: %{percent}."),
            "note_ready": ("Not hazır", "{title}"),
            # The tutor answers a chat quiz with "hazır olunca ilk soruyu bildirim
            # merkezine bırakırım", so the first question is the body -- a title
            # alone would leave the quiz waiting for an answer to an unseen question.
            "quiz_ready": ("Quiz hazır", "{question}"),
            "job_failed": ("İş tamamlanamadı", "{message}"),
            "lecture_set_imported": ("Ders seti içe aktarıldı", "{name}: {imported} belge eklendi, {duplicates} zaten kayıtlıydı, {failed} eklenemedi."),
            "lecture_set_processed": ("Ders seti işlendi", "{name}: {processed} belge hazır, {failed} işlenemedi."),
            "professors_mined": ("Hocalar ayrıldı", "{documents} belge {professors} hocaya ayrıldı; {questions} soru çıkarıldı."),
            "vision_resumed": ("Şekil incelemesi", "{transcribed} taranmış sayfa okundu, {described} şekil betimlendi, {analysed} belge analiz edildi. {stopped}"),
        }
        entry = titles.get(kind)
        if entry is None:
            return
        title, template = entry
        try:
            body = _plain_text(template.format(**{key: payload.get(key, "") for key in ("title", "findings", "count", "percent", "question", "message", "name", "imported", "duplicates", "failed", "processed", "documents", "professors", "questions", "described", "analysed", "stopped", "transcribed")}))
        except (KeyError, IndexError):
            body = str(payload.get("title", ""))
        self._publish(
            "task",
            f"Tıp Akademisi · {title}",
            body,
            target="medical",
            data={key: value for key, value in payload.items() if isinstance(value, (str, int, float, bool))},
            dedupe_key=f"medical:{kind}:{payload.get('document_id') or payload.get('exam_id') or payload.get('job') or ''}",
            alert=True,
        )

    def _medical_payload(self) -> dict[str, Any]:
        academy = self._medical()
        if academy is None:
            return {"available": False, "reason": MEDICAL_UNAVAILABLE}
        try:
            return {"available": True, **_jsonable(academy.dashboard())}
        except Exception as exc:
            return {"available": False, "reason": f"Tıp Akademisi okunamadı ({type(exc).__name__})."}

    def open_external(self, url: Any) -> dict[str, Any]:
        """Open one validated http(s) URL in the default browser.

        Serves direct clicks on source links the page shows; the same
        validation as the system-control tool, nothing else accepted.
        """
        from app.integrations.system_control import _validate_web_url

        validated, problem = _validate_web_url(str(url or ""))
        if validated is None:
            return {"ok": False, "error": problem or "Geçersiz URL."}
        launcher = self._uri_launcher
        if launcher is None:
            from app.integrations.runtime import UriLauncher

            launcher = UriLauncher()
        if not launcher.open(validated):
            return {"ok": False, "error": "Varsayılan tarayıcı açılamadı."}
        self._record_ui_event("external.opened", "A source link was opened in the browser.")
        return {"ok": True, "url": validated}

    def pick_folder(self) -> dict[str, Any]:
        """The native folder picker, for any surface that saves a file."""
        window = self._window
        if window is None:
            return {"ok": False, "error": "Pencere hazır değil."}
        selection: list[Any] = []

        def choose() -> None:
            selection.append(window.create_file_dialog(webview.FOLDER_DIALOG, directory=str(Path.home())))

        try:
            _run_on_ui_thread(window, choose)
        except Exception as exc:
            return {"ok": False, "error": f"Klasör seçici açılamadı ({type(exc).__name__})."}
        chosen = selection[0] if selection else None
        if isinstance(chosen, (list, tuple)):
            chosen = chosen[0] if chosen else None
        return {"ok": True, "path": str(chosen) if chosen else None}

    def export_conversation(self, conversation_id: Any, directory: Any) -> dict[str, Any]:
        """One stored conversation as a Markdown file in the chosen folder."""
        target_dir = Path(str(directory or ""))
        if not target_dir.is_dir():
            return {"ok": False, "error": "Klasör bulunamadı; önce bir klasör seç."}
        try:
            title, created, messages = self.controller.conversation_export(str(conversation_id))
        except (KeyError, ValueError):
            return {"ok": False, "error": "Konuşma bulunamadı."}
        lines = [f"# {title}", "", f"*JARVIS konuşması · {created}*", ""]
        for message in messages:
            speaker = "Sen" if message.role == "user" else "JARVIS"
            lines.append(f"**{speaker}:**")
            lines.append(message.text.strip())
            lines.append("")
        import re as _re

        stem = _re.sub(r"[^0-9A-Za-zÇĞİÖŞÜçğıöşü _.-]+", "", title).strip().replace(" ", "-")[:80] or "konusma"
        target = target_dir / f"{stem}.md"
        counter = 2
        while target.exists():
            target = target_dir / f"{stem}-{counter}.md"
            counter += 1
        newline = chr(10)
        target.write_text(newline.join(lines) + newline, encoding="utf-8")
        self._record_ui_event("conversation.exported", "A conversation was exported.")
        return {"ok": True, "path": str(target), "file": target.name, "messages": len(messages)}

    def speak_text(self, text: Any) -> dict[str, Any]:
        """One reply as audio, through the same voices the phone uses.

        Cloud first, the local Turkish voice as the fallback, and an
        honest refusal when neither can speak - the page then keeps the
        text and says why. The audio returns as base64 WAV/MP3 for a
        plain <audio> element; nothing is written to disk.
        """
        import base64

        from app.voice.models import AudioEncoding, pcm16_to_wav

        voice = getattr(self.controller.application, "voice", None)
        synthesizer = getattr(voice, "synthesizer", None) if voice is not None else None
        if synthesizer is None:
            return {"ok": False, "error": "Sesli iletişim bu bilgisayarda ayarlanmamış."}
        settings = getattr(self.controller.application, "settings", None)
        limit = int(getattr(settings, "voice_max_tts_characters", 4000) or 4000)
        timeout = float(getattr(settings, "voice_operation_timeout_seconds", 60.0) or 60.0)
        spoken = " ".join(_MARKDOWN_NOISE.sub("", str(text or "")).split())
        if not spoken:
            return {"ok": False, "error": "Seslendirilecek metin boş."}
        if len(spoken) > limit:
            spoken = spoken[:limit].rsplit(" ", 1)[0]

        def run(operation: Any) -> Any:
            future = self.controller.submit_background(operation, lambda done: None)
            return future.result(timeout=timeout)

        try:
            speech = run(synthesizer.synthesize(spoken))
            encoding = getattr(speech.encoding, "value", str(speech.encoding))
            if encoding == AudioEncoding.PCM16.value:
                body, mime = pcm16_to_wav(bytes(speech.data), 24_000), "audio/wav"
            else:
                body, mime = bytes(speech.data), {"mp3": "audio/mpeg", "wav": "audio/wav"}.get(encoding, "application/octet-stream")
            return {"ok": True, "mime": mime, "source": "cloud", "audio": base64.b64encode(body).decode("ascii")}
        except Exception:
            pass
        try:
            from app.voice.audio import synthesize_local_turkish

            local = run(synthesize_local_turkish(spoken))
        except Exception:
            local = None
        if local:
            return {"ok": True, "mime": "audio/wav", "source": "local", "audio": base64.b64encode(bytes(local)).decode("ascii")}
        return {"ok": False, "error": "Ses üretilemedi; yanıt metin olarak duruyor."}

    def save_markdown(self, directory: Any, filename: Any, content: Any) -> dict[str, Any]:
        """One Markdown file the page composed, into a folder the user picked.

        The page owns the words (a research report, a summary); this side
        owns the boundaries: the folder must exist and be one the picker
        returned, the name is flattened to a safe .md basename, the size
        is bounded, and an existing file is never overwritten.
        """
        target_dir = Path(str(directory or ""))
        if not target_dir.is_dir():
            return {"ok": False, "error": "Klasör bulunamadı; önce bir klasör seç."}
        body = str(content or "")
        if not body.strip():
            return {"ok": False, "error": "Kaydedilecek içerik boş."}
        if len(body) > 512_000:
            return {"ok": False, "error": "İçerik bir Markdown dosyası için fazla büyük."}
        stem = _re.sub(r"[^\w\sçğıöşüÇĞİÖŞÜ-]", "", str(filename or "")).strip()
        stem = _re.sub(r"\s+", "-", stem)[:80] or "jarvis-notu"
        target = target_dir / f"{stem}.md"
        counter = 2
        while target.exists():
            target = target_dir / f"{stem}-{counter}.md"
            counter += 1
        try:
            target.write_text(body, encoding="utf-8")
        except OSError as exc:
            return {"ok": False, "error": f"Dosya yazılamadı ({type(exc).__name__})."}
        return {"ok": True, "path": str(target), "file": target.name}

    def daily_brief(self) -> dict[str, Any]:
        """The day at a glance, read from the services that hold it.

        Every section reports independently; one that cannot answer says so
        instead of hiding the rest. Nothing here is estimated or generated.
        """
        application = self.controller.application
        from app.core.interaction_policy import turkish_date

        brief: dict[str, Any] = {"ok": True, "date": turkish_date()}
        reminders = getattr(application, "reminders", None)
        try:
            result = reminders.list_active() if reminders is not None else None
            brief["reminders"] = list((result.data or {}).get("reminders", []))[:4] if result is not None and result.succeeded else []
            brief["reminders_available"] = reminders is not None
        except Exception:
            brief["reminders"], brief["reminders_available"] = [], False
        routines = getattr(application, "routines", None)
        try:
            rows = routines.list() if routines is not None else []
            brief["routines"] = [
                {"name": row.get("name") or row.get("prompt", "")[:40], "schedule": row.get("schedule", ""), "next_run_local": row.get("next_run_local", "")}
                for row in rows[:3]
            ]
            brief["routines_available"] = routines is not None
        except Exception:
            brief["routines"], brief["routines_available"] = [], False
        try:
            tasks = application.task_service.list(limit=100)
            open_states = {"running", "waiting_for_input", "waiting_for_approval", "pending", "queued"}
            brief["tasks_open"] = sum(1 for task in tasks if str(task.get("status")) in open_states)
        except Exception:
            brief["tasks_open"] = 0
        try:
            brief["notifications_unread"] = int(self._notifications.summary()["unread"])
        except Exception:
            brief["notifications_unread"] = 0
        academy = self._medical()
        medical: dict[str, Any] = {"available": academy is not None}
        if academy is not None:
            try:
                block = academy.study.dashboard_block()
                today = block.get("today") or {}
                next_item = today.get("next") or None
                medical["next_activity"] = {"title": next_item["title"], "kind_label": next_item.get("kind_label", "")} if next_item else None
                medical["plan_message"] = str(today.get("message") or "")
                medical["cards_waiting"] = int(block.get("cards_due") or 0) + int(block.get("cards_new") or 0)
                medical["findings_open"] = int(block.get("findings_open") or 0)
                countdowns = sorted(
                    (academy.study.planner.summary(plan["plan_id"]) for plan in academy.study.planner.plans()),
                    key=lambda summary: summary["days_left"],
                )
                medical["countdown"] = {"name": countdowns[0]["name"], "days_left": countdowns[0]["days_left"]} if countdowns else None
            except Exception:
                medical["available"] = False
        brief["medical"] = medical
        almanac = getattr(application, "almanac", None)
        if almanac is not None:
            try:
                brief["almanac"] = _jsonable(almanac.snapshot())
            except Exception as exc:
                reason = f"Almanak okunamadı ({type(exc).__name__})."
                brief["almanac"] = {
                    "weather": {"available": False, "reason": reason},
                    "rates": {"available": False, "reason": reason},
                }
        return brief

    def define_word(self, word: str = "") -> dict[str, Any]:
        """One TDK dictionary entry for the palette's lookup card.

        The service already answers with honest reasons; the bridge only
        translates its refusals into the card's error shape.
        """
        dictionary = getattr(self.controller.application, "dictionary", None)
        if dictionary is None:
            return {"ok": False, "error": "Sözlük servisi hazır değil."}
        try:
            result = dictionary.lookup(str(word or ""))
        except Exception as exc:
            return {"ok": False, "error": f"Sözlük okunamadı ({type(exc).__name__})."}
        if not result.get("ok"):
            return {"ok": False, "error": str(result.get("reason") or "Sözlükte bulunamadı.")}
        return _jsonable(result)

    def run_remote_tool(self, name: str, payload: Any = None) -> dict[str, Any]:
        """One control from the home remote, run through the tool executor.

        The executor keeps its policy, timeout and verification in the
        loop; the bridge never calls an integration directly. The result
        is the tool's own honest report - PARTIAL and BLOCKED included.
        (The parameter is not called ``arguments``: pywebview builds the
        page-side wrapper from these names, and ``arguments`` is
        JavaScript's own reserved object - the wrapper then sends nothing.)
        """
        tool = str(name or "").strip()
        if tool not in REMOTE_TOOLS:
            return {"ok": False, "error": "Bu araç kumandadan çalıştırılamaz."}
        if self.controller.paused:
            return {"ok": False, "error": PAUSED_MESSAGE}
        executor = self.controller.application.tool_executor
        if not executor.contains(tool):
            return {"ok": False, "error": "Bu araç bu yapılandırmada kayıtlı değil."}
        parameters = dict(payload) if isinstance(payload, Mapping) else {}
        # Tool handlers may be coroutines, and the executor runs those only
        # inside an event loop: the controller's runner is that loop, and
        # this thread waits for the verified result, as the settings
        # connection test does.
        timeout = float(getattr(executor.get(tool).definition, "timeout_seconds", 30.0) or 30.0) + 5.0

        async def run() -> Any:
            outcome = executor.execute(tool, parameters=parameters)
            if hasattr(outcome, "__await__"):
                outcome = await outcome
            return outcome

        try:
            future = self.controller.submit_background(run(), lambda done: None)
            result = future.result(timeout=timeout)
        except Exception as exc:
            return {"ok": False, "error": f"Araç çalıştırılamadı ({type(exc).__name__})."}
        status = getattr(getattr(result, "status", None), "value", str(getattr(result, "status", "")))
        return {
            "ok": bool(getattr(result, "succeeded", False)),
            "status": status,
            "message": str(getattr(result, "message", "") or ""),
            "data": _jsonable(getattr(result, "data", None) or {}),
            "verified": bool(getattr(result, "verified", False)),
            "error": getattr(result, "error", None),
        }

    def medical_pick_file(self, kind: str = "document") -> dict[str, Any]:
        """Open the native picker for a lecture document, an exam file or a
        folder of course material (``kind="folder"``)."""
        window = self._window
        if window is None:
            return {"ok": False, "error": "Pencere hazır değil."}
        if self._medical() is None:
            return {"ok": False, "error": MEDICAL_UNAVAILABLE}
        wanted = str(kind or "").strip()
        file_types = (
            MEDICAL_QUESTION_TYPES
            if wanted == "questions"
            else MEDICAL_DOCUMENT_TYPES
        )
        selection: list[Any] = []

        def choose() -> None:
            if wanted == "folder":
                selection.append(
                    window.create_file_dialog(
                        webview.FOLDER_DIALOG, directory=str(Path.home())
                    )
                )
                return
            selection.append(
                window.create_file_dialog(
                    webview.OPEN_DIALOG,
                    allow_multiple=False,
                    directory=str(Path.home()),
                    file_types=file_types,
                )
            )

        try:
            _run_on_ui_thread(window, choose)
        except Exception as exc:
            noun = "Klasör" if wanted == "folder" else "Dosya"
            return {"ok": False, "error": f"{noun} seçici açılamadı ({type(exc).__name__})."}
        chosen = selection[0] if selection else None
        if isinstance(chosen, (list, tuple)):
            chosen = chosen[0] if chosen else None
        if not chosen:
            return {"ok": True, "path": None}
        return {"ok": True, "path": str(chosen)}

    def medical_call(self, action: str, params: Any = None) -> dict[str, Any]:
        """One entry point for every Medical Academy operation.

        Read-only views answer immediately; model-backed pipelines are
        started on the controller's async runner and report back through
        the ``medical`` push channel. Destructive operations refuse
        without an explicit ``confirmed`` flag, exactly like the file and
        memory surfaces do.
        """
        academy = self._medical()
        if academy is None:
            return {"ok": False, "available": False, "error": MEDICAL_UNAVAILABLE}
        name = str(action or "").strip()
        payload: Mapping[str, Any] = params if isinstance(params, Mapping) else {}
        # The connected study workflow names its own operations (app/medical/study.py).
        study = getattr(academy, "study", None)
        confirmed = MEDICAL_CONFIRMED_ACTIONS | (study.CONFIRMED_ACTIONS if study is not None else frozenset())
        if name in confirmed and payload.get("confirmed") is not True:
            return {"ok": False, "error": "Bu işlem onaylanmadı."}
        if name in MEDICAL_BACKGROUND_ACTIONS or (study is not None and name in study.ASYNC_ACTIONS):
            return self._medical_background(academy, name, payload)
        try:
            if study is not None and name in study.SYNC_ACTIONS:
                return {"ok": True, **_jsonable(study.call(name, payload))}
            return self._medical_view(academy, name, payload)
        except KeyError:
            return {"ok": False, "error": f"Bilinmeyen işlem: {name}"}
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}
        except Exception as exc:
            self._record_ui_event(
                "medical.failed",
                "A Medical Academy operation failed.",
                action=name,
                error_type=type(exc).__name__,
            )
            return {"ok": False, "error": f"İşlem tamamlanamadı ({type(exc).__name__})."}

    def _medical_view(
        self,
        academy: Any,
        name: str,
        payload: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Synchronous study-layer operations. Raises KeyError when the
        action is unknown, so the caller can fail closed."""
        text = lambda key, default="": str(payload.get(key, default) or "").strip()  # noqa: E731
        number = lambda key, default=0: int(payload.get(key) or default)  # noqa: E731

        if name == "state":
            return {"ok": True, **self._medical_payload()}
        if name == "session":
            return {"ok": True, **_jsonable(academy.update_session(dict(payload.get("fields") or {})))}
        if name == "subjects":
            return {"ok": True, "subjects": _jsonable(academy.subjects())}
        if name == "topic":
            topic = academy.topic(text("topic_id"))
            if topic is None:
                return {"ok": False, "error": "Konu bulunamadı."}
            return {"ok": True, "topic": _jsonable(topic)}
        if name == "search":
            return {"ok": True, **_jsonable(academy.search(text("query"), limit=min(50, number("limit", 20))))}
        if name == "term":
            return {"ok": True, **_jsonable(academy.term(text("query")))}
        if name == "documents":
            return {"ok": True, "documents": _jsonable(academy.documents())}
        if name == "document":
            document = academy.document(text("document_id"))
            if document is None:
                return {"ok": False, "error": "Belge bulunamadı."}
            return {"ok": True, "document": _jsonable(document)}
        if name == "page":
            page = academy.page(text("document_id"), number("page_number", 1), image=payload.get("image") is not False)
            if page is None:
                return {"ok": False, "error": "Sayfa bulunamadı."}
            return {"ok": True, "page": _jsonable(page)}
        if name == "import_document":
            document, created = academy.import_document(
                text("path"),
                title=text("title") or None,
                subject=text("subject") or None,
            )
            # Filing the document is local work and stays available while
            # the desktop is paused, but processing it is the very pipeline
            # "process_document" refuses in that state: the pause gate
            # cannot depend on which button started the job. The document
            # is left pending and the message says so instead of claiming
            # work that is not happening.
            paused = self.controller.paused
            started = False
            if created and not paused:
                started = self._medical_start(academy.process_document(document.document_id))
            if not created:
                message = "Bu belge zaten kayıtlı."
            elif started:
                message = "Belge içe aktarıldı, işleniyor."
            elif paused:
                message = (
                    f"Belge içe aktarıldı ama işlenmedi. {PAUSED_MESSAGE} "
                    "Sonra Kütüphane'den “Yeniden işle” ile başlatabilirsin."
                )
            else:
                message = (
                    "Belge içe aktarıldı ama işlem başlatılamadı; "
                    "Kütüphane'den “Yeniden işle” ile dene."
                )
            return {
                "ok": True,
                "created": created,
                "started": started,
                "document": _jsonable(academy.pipeline.payload(document)),
                "documents": _jsonable(academy.documents()),
                "message": message,
            }
        if name == "delete_document":
            removed = academy.delete_document(text("document_id"))
            return {"ok": removed, "documents": _jsonable(academy.documents()), "error": None if removed else "Belge bulunamadı."}
        if name == "lecture_sets":
            return {"ok": True, "lecture_sets": _jsonable(academy.lecture_sets())}
        if name == "narration":
            return {"ok": True, "narration": _jsonable(academy.narration.state())}
        if name == "narration_script":
            return {"ok": True, "script": _jsonable(academy.narration.cached_script(text("document_id")))}
        if name == "narration_command":
            command = text("command")
            argument = payload.get("text") if command == "ask" else payload.get("value")
            try:
                state = academy.narration.command(command, argument)
            except _narration_error() as exc:
                return {"ok": False, "error": str(exc), "narration": _jsonable(academy.narration.state())}
            return {"ok": True, "narration": _jsonable(state)}
        if name == "lecture_set":
            record = academy.lecture_set(text("set_id"))
            if record is None:
                return {"ok": False, "error": "Ders seti bulunamadı."}
            return {"ok": True, "lecture_set": _jsonable(record)}
        if name == "delete_lecture_set":
            removed = academy.delete_lecture_set(text("set_id"))
            return {"ok": removed, "lecture_sets": _jsonable(academy.lecture_sets()), "error": None if removed else "Ders seti bulunamadı."}
        if name == "comparison":
            return {"ok": True, "comparison": _jsonable(academy.comparison(text("document_id")))}
        if name == "analysis":
            return {"ok": True, "analysis": _jsonable(academy.document_analysis(text("document_id")))}
        if name == "notes":
            return {"ok": True, "notes": _jsonable(academy.notes())}
        if name == "delete_note":
            removed = academy.delete_note(text("note_id"))
            return {"ok": removed, "notes": _jsonable(academy.notes()), "error": None if removed else "Not bulunamadı."}
        if name == "exams":
            return {"ok": True, "exams": _jsonable(academy.exams())}
        if name == "exam":
            exam = academy.exam(text("exam_id"))
            if exam is None:
                return {"ok": False, "error": "Sınav bulunamadı."}
            return {"ok": True, "exam": _jsonable(exam)}
        if name == "start_exam":
            exam = academy.start_exam(text("exam_id"))
            if exam is None:
                return {"ok": False, "error": "Sınav bulunamadı."}
            return {"ok": True, "exam": _jsonable(exam)}
        if name == "answer":
            result = academy.answer(
                text("exam_id"),
                text("question_id"),
                text("answer_key") or None,
                flagged=payload.get("flagged"),
                current_index=payload.get("current_index"),
                confidence=text("confidence") or None,
                reasoning=text("reasoning"),
                submission_id=text("submission_id") or None,
            )
            if result is None:
                return {"ok": False, "error": "Soru bu sınavda yok."}
            return {"ok": True, **_jsonable(result)}
        if name == "finish_exam":
            exam = academy.finish_exam(text("exam_id"))
            if exam is None:
                return {"ok": False, "error": "Sınav bulunamadı."}
            return {"ok": True, "exam": _jsonable(exam)}
        if name == "delete_exam":
            removed = academy.delete_exam(text("exam_id"))
            return {"ok": removed, "exams": _jsonable(academy.exams()), "error": None if removed else "Sınav bulunamadı."}
        if name == "bank":
            return {"ok": True, **_jsonable(academy.question_bank(dict(payload.get("filters") or {})))}
        if name == "delete_question":
            removed = academy.delete_question(text("question_id"))
            return {"ok": removed, "error": None if removed else "Soru bulunamadı."}
        if name == "set_answer_key":
            question = academy.set_answer_key(text("question_id"), text("answer_key") or None)
            if question is None:
                return {"ok": False, "error": "Soru bulunamadı."}
            return {"ok": True, "question": _jsonable(question)}
        if name == "professors":
            return {"ok": True, "professors": _jsonable(academy.professors())}
        if name == "professor":
            profile = academy.professor(text("profile_id"))
            if profile is None:
                return {"ok": False, "error": "Hoca profili bulunamadı."}
            return {"ok": True, "professor": _jsonable(profile)}
        if name == "create_professor":
            if not text("name"):
                return {"ok": False, "error": "Hoca adı gerekli."}
            return {"ok": True, "professor": _jsonable(academy.create_professor(text("name"), text("subject") or None)), "professors": _jsonable(academy.professors())}
        if name == "reset_professor":
            profile = academy.reset_professor(text("profile_id"))
            if profile is None:
                return {"ok": False, "error": "Hoca profili bulunamadı."}
            return {"ok": True, "professor": _jsonable(profile), "professors": _jsonable(academy.professors())}
        if name == "delete_professor":
            removed = academy.delete_professor(text("profile_id"), delete_questions=payload.get("delete_questions") is True)
            return {"ok": removed, "professors": _jsonable(academy.professors()), "error": None if removed else "Hoca profili bulunamadı."}
        if name == "progress":
            return {"ok": True, **_jsonable(academy.progress())}
        if name == "backups":
            return {"ok": True, **_jsonable(academy.backups())}
        if name == "export_markdown":
            directory = Path(text("directory"))
            if not directory.is_dir():
                return {"ok": False, "error": "Klasör bulunamadı; önce bir klasör seç."}
            kind = text("kind")
            if kind == "note":
                filename, content = academy.export_note_markdown(text("note_id"))
            elif kind in ("exam", "exam_key"):
                filename, content = academy.export_exam_markdown(text("exam_id"), include_answers=kind == "exam_key")
            elif kind == "week":
                filename, content = academy.export_week_markdown()
            else:
                return {"ok": False, "error": f"Bilinmeyen dışa aktarma türü: {kind}"}
            target = directory / filename
            counter = 2
            while target.exists():
                target = directory / f"{Path(filename).stem}-{counter}{Path(filename).suffix}"
                counter += 1
            target.write_text(content, encoding="utf-8")
            self._record_ui_event("medical.exported", "A study artefact was exported.", kind=kind)
            return {"ok": True, "path": str(target), "file": target.name}
        if name == "anatomy":
            return {"ok": True, **_jsonable(academy.anatomy_structures())}
        if name == "structure":
            structure = academy.anatomy_structure(text("structure_id"))
            if structure is None:
                return {"ok": False, "error": "Yapı bulunamadı."}
            return {"ok": True, "structure": _jsonable(structure)}
        if name == "mesh":
            return {"ok": True, "mesh": _jsonable(academy.anatomy_mesh(text("structure_id")))}
        if name == "anatomy_quiz":
            return {"ok": True, "questions": _jsonable(academy.anatomy_quiz(text("structure_id"), count=number("count", 5)))}
        if name == "anatomy_answer":
            return {"ok": True, **_jsonable(academy.record_anatomy_answer(text("structure_id"), text("landmark_id") or None, payload.get("correct") is True, submission_id=text("submission_id") or None, confidence=text("confidence") or None))}
        raise KeyError(name)

    def _medical_start(self, operation: Any, *, report: str = "") -> bool:
        """Run one academy coroutine on the controller's async runner.

        ``report`` names a block of the job's result the page has to see.
        An import counts what it could not parse, what it skipped as a
        duplicate and how many questions arrived without an answer key;
        that account has no other surface, so it travels with the
        completion push instead of dying with the discarded result.
        """

        def done(future: Future[Any]) -> None:
            if future.cancelled():
                return
            try:
                outcome = future.result()
            except Exception as exc:
                self._push("medical", {"kind": "job_failed", "error": f"{type(exc).__name__}", "message": str(exc)[:300]})
                self._record_ui_event(
                    "medical.job_failed",
                    "A Medical Academy background job failed.",
                    error_type=type(exc).__name__,
                )
            else:
                block = outcome.get(report) if report and isinstance(outcome, Mapping) else None
                if isinstance(block, Mapping):
                    self._push(
                        "medical",
                        {
                            **_jsonable(dict(block)),
                            "kind": "job_report",
                            "job": report,
                            "profile_id": str(outcome.get("profile_id") or ""),
                        },
                    )
            self._push("medical", {"kind": "refresh"})

        try:
            self.controller.submit_background(operation, done)
        except RuntimeError:
            operation.close()
            return False
        return True

    def _medical_background(
        self,
        academy: Any,
        name: str,
        payload: Mapping[str, Any],
    ) -> dict[str, Any]:
        text = lambda key, default="": str(payload.get(key, default) or "").strip()  # noqa: E731
        number = lambda key, default=0: int(payload.get(key) or default)  # noqa: E731
        if self.controller.paused:
            return {"ok": False, "error": PAUSED_MESSAGE}
        operation: Any
        report = ""
        if name == "process_document":
            if academy.store.get_document(text("document_id")) is None:
                return {"ok": False, "error": "Belge bulunamadı."}
            operation = academy.process_document(text("document_id"))
            message = "Belge işleniyor."
        elif name == "analyze_document":
            operation = academy.analyze_document(text("document_id"), page_from=number("page_from"), page_to=number("page_to"))
            message = "Belge analiz ediliyor."
        elif name == "compare_document":
            operation = academy.compare_document(text("document_id"), page_from=number("page_from"), page_to=number("page_to"))
            message = "Belge standart bilgiyle karşılaştırılıyor."
        elif name == "create_note":
            request = {
                "mode": text("mode", "medical.short_notes"),
                "subject": text("subject") or None,
                "topic_id": text("topic_id") or None,
                "document_ids": [str(item) for item in (payload.get("document_ids") or [])],
                "page_from": number("page_from"),
                "page_to": number("page_to"),
                "depth": text("depth", "standard"),
            }
            running = academy.running_job("create_note", request)
            if running is not None:
                return {"ok": True, "started": False, "duplicate": True, "job": _jsonable(running), "message": "Bu not zaten hazırlanıyor; bitince listede görünecek."}
            operation = academy.generate_notes_job(**request)
            message = "Not hazırlanıyor."
        elif name == "create_exam":
            request = dict(payload.get("config") or {})
            running = academy.running_job("create_exam", request)
            if running is not None:
                return {"ok": True, "started": False, "duplicate": True, "job": _jsonable(running), "message": "Bu sınav zaten hazırlanıyor; bitince açılacak."}
            operation = academy.generate_exam_job(request)
            message = "Sınav hazırlanıyor."
        elif name == "import_questions":
            operation = academy.import_questions(
                professor_id=text("profile_id") or None,
                name=text("name") or None,
                subject=text("subject") or None,
                text=str(payload.get("text") or "") or None,
                path=text("path") or None,
                image_path=text("image_path") or None,
            )
            message = "Sorular içe aktarılıyor."
            report = "import"
        elif name == "import_folder":
            folder = text("path")
            if not folder or not Path(folder).is_dir():
                return {"ok": False, "error": "Klasör bulunamadı."}
            operation = academy.import_folder_job(
                folder,
                name=text("name") or None,
                source=text("source") or None,
                vision=payload.get("vision") is not False,
                analysis=payload.get("analysis") is not False,
            )
            message = "Klasör içe aktarılıyor; belgeler sırayla işlenecek."
            report = "folder_import"
        elif name == "process_lecture_set":
            if academy.lecture_set(text("set_id")) is None:
                return {"ok": False, "error": "Ders seti bulunamadı."}
            operation = academy.process_lecture_set(
                text("set_id"),
                vision=payload.get("vision") is not False,
                analysis=payload.get("analysis") is not False,
                retry_failed=payload.get("retry_failed") is True,
            )
            message = "Ders setindeki bekleyen belgeler işleniyor."
        elif name == "mine_questions":
            set_id = text("set_id") or None
            if set_id and academy.lecture_set(set_id) is None:
                return {"ok": False, "error": "Ders seti bulunamadı."}
            document_ids = [str(item) for item in (payload.get("document_ids") or [])] or None
            operation = academy.mine_questions_job(set_id=set_id, document_ids=document_ids)
            message = "Belgeler hocalara ayrılıyor, sorular çıkarılıyor."
            report = "mine"
        elif name == "continue_processing":
            set_id = text("set_id") or None
            document_id = text("document_id") or None
            if set_id and academy.lecture_set(set_id) is None:
                return {"ok": False, "error": "Ders seti bulunamadı."}
            if document_id and academy.store.get_document(document_id) is None:
                return {"ok": False, "error": "Belge bulunamadı."}
            operation = academy.continue_processing(set_id=set_id, document_id=document_id, vision=payload.get("vision") is not False, analysis=payload.get("analysis") is not False)
            message = "Taranmış sayfalar okunuyor, bekleyen şekiller inceleniyor, eksik analizler tamamlanıyor; model durursa kaldığı yerde bekler."
            report = "continue"
        elif name == "backup_now":
            operation = academy.backup_job()
            message = "Yedek alınıyor; bitince bildirilir."
        elif name == "prepare_narration":
            if academy.store.get_document(text("document_id")) is None:
                return {"ok": False, "error": "Belge bulunamadı."}
            operation = academy.narration.script(text("document_id"), rebuild=payload.get("rebuild") is True)
            message = "Anlatım metni hazırlanıyor."
            report = "narration_script"
        elif name == "narration_start":
            document_id = text("document_id")
            if academy.store.get_document(document_id) is None:
                return {"ok": False, "error": "Belge bulunamadı."}
            with self._lock:
                voice_busy = _active(self._voice_future)
            if voice_busy:
                return {"ok": False, "error": "Sesli oturum açıkken anlatım başlatılamaz; önce oturumu durdur."}
            if academy.narration.active:
                return {"ok": False, "error": "Zaten açık bir anlatım var; önce onu durdur."}
            operation = academy.narration.play(
                document_id,
                voice=self.controller.application.voice,
                from_segment=number("segment"),
                checkpoints=payload.get("checkpoints") is not False,
                prefer_cloud=(True if payload.get("voice") == "cloud" else False if payload.get("voice") == "local" else None),
            )
            message = "Sesli anlatım başlıyor."
            study = getattr(academy, "study", None)
            if study is not None:
                try:
                    study.planner.log_study(document_id=document_id, activity="narration")
                except Exception:
                    pass
        elif getattr(academy, "study", None) is not None and name in academy.study.ASYNC_ACTIONS:
            try:
                operation = academy.study.start(name, payload)
            except (KeyError, ValueError) as exc:
                return {"ok": False, "error": str(exc) or f"Bilinmeyen işlem: {name}"}
            report = "study"
            message = academy.study.ASYNC_MESSAGES_TR.get(name, "İşlem sürüyor.")
        else:  # pragma: no cover - guarded by MEDICAL_BACKGROUND_ACTIONS
            return {"ok": False, "error": f"Bilinmeyen işlem: {name}"}
        if not self._medical_start(operation, report=report):
            return {"ok": False, "error": "İşlem başlatılamadı."}
        self._record_ui_event("medical.started", "A Medical Academy job started.", action=name)
        return {"ok": True, "started": True, "message": message}

    def _deliver_reminder(self, reminder: Mapping[str, Any]) -> None:
        """Put a due reminder in the notification centre.

        Delivery means the centre accepted the entry (written through to
        its store when it has one); the native toast is a courtesy on top,
        and its absence is not a failed delivery. The reminder's id is the
        entry's reference, so a reminder handed out again — its
        acknowledgment lost to a crash or an expired lease — is found and
        not shown twice. Whatever stops the centre taking the entry
        propagates, and the watch returns the claim for a later retry.
        """
        text = str(reminder.get("text", "")).strip()
        if not text:
            return
        reference = str(reminder.get("reminder_id", "")).strip() or None
        centre = self._notifications
        if reference is not None and centre.find("reminder", reference) is not None:
            self._record_ui_event(
                "reminder.repeated",
                "A reminder handed out again was already in the centre.",
                reminder_id=reference,
            )
            return
        entry = centre.publish(
            "reminder",
            NOTIFICATION_TITLES["reminder"],
            text,
            reference=reference,
        )
        if not self._attended:
            self._notify_os(entry.title, entry.body)

    def _on_screen_observation(self, entry: Any) -> None:
        if not isinstance(entry, Mapping):
            return
        text = str(entry.get("text", "")).strip()
        if not text:
            return
        self._publish(
            "observation",
            NOTIFICATION_TITLES["observation"],
            text,
            target="vision",
            data={"at": entry.get("at"), "state": entry.get("state")},
            alert=True,
        )

    def _notify_reply(self, message: Any) -> None:
        """A reply that lands on a hidden window becomes a notification."""
        if self._attended:
            return
        role = _message_field(message, "role")
        text = str(_message_field(message, "text") or "").strip()
        if str(role) != "assistant" or not text:
            return
        self._publish(
            "reply", NOTIFICATION_TITLES["reply"], text, target="chat", alert=True
        )

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
        payload = {
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
            "fileRoots": self._file_roots_payload(),
            "notifications": self._notification_state(),
            "routines": self._routines_payload(),
            "medical": self._medical_payload(),
        }
        # Started after the payload is built so a reminder that is due
        # right now reaches the page as a push it merges after boot.
        self._start_reminder_watch()
        self._warm_up_provider()
        return payload

    def _start_daily_brief(self, storage: Path) -> None:
        """The morning-summary clock; reads services, never the model.

        A daemon thread wakes twice a minute, and past the configured
        local time it sends today's summary once - the sent date is
        stamped to disk first, so a crash can skip a morning but never
        double-send one.
        """

        def loop() -> None:
            stamp_path = storage / DAILY_BRIEF_STAMP_FILE
            while not self._closing:
                time.sleep(DAILY_BRIEF_POLL_SECONDS)
                if self._closing:
                    return
                settings = getattr(self.controller.application, "settings", None)
                if settings is None or not bool(
                    getattr(settings, "daily_brief_notification", False)
                ):
                    continue
                now = datetime.now().astimezone()
                try:
                    stamp = json.loads(
                        stamp_path.read_text(encoding="utf-8")
                    ).get("sent")
                except (OSError, ValueError):
                    stamp = None
                if not daily_brief_due(
                    now, str(getattr(settings, "daily_brief_time", "")), stamp
                ):
                    continue
                try:
                    body = brief_notification_body(self.daily_brief())
                except Exception:
                    continue
                try:
                    stamp_path.write_text(
                        json.dumps({"sent": now.date().isoformat()}),
                        encoding="utf-8",
                    )
                except OSError:
                    # Nowhere to remember the send: skip rather than
                    # risk a notification every half minute all day.
                    continue
                self._publish(
                    "brief",
                    NOTIFICATION_TITLES["brief"],
                    body,
                    target="home",
                    alert=True,
                    dedupe_key=f"brief:{now.date().isoformat()}",
                )

        threading.Thread(
            target=loop, name="nova-daily-brief", daemon=True
        ).start()

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
            routine_future = self._routine_future
            self._voice_future = None
            self._command_future = None
            self._routine_future = None
        finally:
            if acquired:
                self._lock.release()
        self._detach_observers()
        self._window_worker.stop()
        for decision in pending:
            if not decision.done():
                decision.set_result(False)
        for future in (voice_future, command_future, routine_future):
            if _active(future):
                future.cancel()

    # ------------------------------------------------------------------
    # Chat
    # ------------------------------------------------------------------
    def _command_active(self) -> bool:
        """A chat turn is running, or is on its way to the runner.

        Read under :attr:`_lock`. ``_command_opening`` covers the gap where
        submit_command has claimed the turn but leaves the lock to raise the
        busy bracket, which blocks until the browser answers.
        """
        return self._command_opening or _active(self._command_future)

    def submit_command(self, text: str, spoken: Any = False) -> dict[str, Any]:
        """One chat turn. ``spoken`` marks a transcript (the phone's voice
        loop): the record and the core see it as a voice request."""
        normalized = str(text or "").strip()
        if not normalized:
            return {"ok": False, "error": "Komut boş olamaz."}
        source = RequestSource.VOICE if spoken is True else RequestSource.TEXT
        if self.controller.paused:
            return {"ok": False, "error": PAUSED_MESSAGE}

        def stream(chunk: str) -> None:
            if not chunk:
                return
            self._stream_latest = chunk
            now = time.monotonic()
            if now - self._stream_last_flush >= STREAM_FLUSH_SECONDS:
                self._flush_stream()

        def done(future: Future[Any]) -> None:
            with self._lock:
                if self._command_future is future:
                    self._command_future = None
            self._flush_stream()
            if future.cancelled():
                # No answer exists, so none is reported; the bracket still
                # closes, because a surface that only watched the turn has
                # no other way to learn that it ended. During shutdown the
                # push is already a no-op — _ready is false before anything
                # is cancelled — so this is the invariant, not a new frame.
                self._push("busy", {"busy": False, "status": READY_STATUS})
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
                self._notify_reply(message)
            self._push("busy", {"busy": False, "status": READY_STATUS})
            self._push_snapshot()

        # The turn is claimed under the lock, so two rapid sends cannot both
        # slip past the guard before the runner marks the controller busy.
        # The work below happens with the lock released: a push blocks until
        # WebView2 answers and done() waits on this lock from the core's
        # event-loop thread, so an immediate answer — the direct clock
        # reply, a cached one — would park the core on a browser round-trip.
        with self._lock:
            if self._closing:
                return {"ok": False, "error": "JARVIS kapanıyor."}
            if self._command_active() or self.controller.state.busy:
                return {
                    "ok": False,
                    "error": "JARVIS şu an başka bir istek işliyor.",
                }
            self._command_opening = True
        try:
            # Every surface has to learn that a turn started, not only the
            # one that started it: a phone submit reaches this method through
            # the same bridge and otherwise leaves the desktop sitting at
            # READY with a bubble filling itself in. Raised before the
            # submission, because a callback that fires synchronously pushes
            # the matching busy:false and must not be overtaken.
            self._push(
                "busy",
                {
                    "busy": True,
                    "status": WORKING_STATUS,
                    "text": normalized,
                    "spoken": spoken is True,
                },
            )
            future = self.controller.submit_background(
                self.controller.submit_command(
                    normalized, stream_callback=stream, source=source
                ),
                done,
            )
            with self._lock:
                # done() may have run already and found nothing to clear;
                # the future it belongs to is recorded either way, before
                # the claim is dropped.
                self._command_future = future
                if self._closing:
                    # _shutdown ran while the bracket push was waiting on
                    # the browser. It cancels what it finds and never looks
                    # again, and this turn was not recorded yet, so it has
                    # to cancel itself or outlive the window.
                    future.cancel()
                    return {"ok": False, "error": "JARVIS kapanıyor."}
        except RuntimeError as exc:
            # Still claimed: the closing half of the bracket cannot be
            # overtaken by the next turn's opening half.
            self._push("busy", {"busy": False, "status": READY_STATUS})
            return {"ok": False, "error": f"İstek gönderilemedi ({exc})."}
        finally:
            with self._lock:
                self._command_opening = False
        return {"ok": True}

    def _flush_stream(self) -> None:
        text = self._stream_latest
        if not text:
            return
        self._stream_latest = ""
        self._stream_last_flush = time.monotonic()
        self._push("stream", {"text": text})

    # ------------------------------------------------------------------
    # Conversations
    # ------------------------------------------------------------------
    def _conversation_switch_blocked(self) -> str | None:
        if self._closing:
            return "JARVIS kapanıyor."
        if self._command_active() or self.controller.state.busy:
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

    def search_conversations(self, query: Any) -> dict[str, Any]:
        """Find stored conversations by what was said in them."""
        normalized = " ".join(str(query or "").split())
        if len(normalized) < 2:
            return {"ok": False, "error": "Arama için en az 2 karakter yaz."}
        results = self.controller.search_conversations(normalized)
        return {"ok": True, "query": normalized, "results": _jsonable(results)}

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

    def unarchive_conversation(self, conversation_id: str) -> dict[str, Any]:
        """The reverse of archiving; nothing else about the record changes."""
        try:
            self.controller.unarchive_conversation(str(conversation_id))
        except KeyError:
            return {"ok": False, "error": "Konuşma bulunamadı."}
        except ValueError:
            return {"ok": False, "error": "Konuşma kimliği geçersiz."}
        return {"ok": True, "conversations": self.list_conversations()["conversations"]}

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
        academy = self._medical()
        if academy is not None and getattr(getattr(academy, "narration", None), "active", False):
            # One microphone and one speaker: a lecture being narrated would
            # talk over the session and hear itself.
            return {"ok": False, "error": "Sesli anlatım açıkken sesli oturum başlatılamaz; önce anlatımı bitir."}

        # A person turns the music down to talk: the voice phases also
        # drive a Spotify ducker when the desktop app is around.
        ducker = self._spotify_ducker()

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
            if ducker is not None:
                ducker.restore()
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
                voice.state_callback = compose_voice_state_callback(
                    lambda state: self._push(
                        "voice_phase",
                        {"phase": getattr(state, "value", str(state))},
                    ),
                    ducker,
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
                if not self._attended:
                    self._publish(
                        "task",
                        NOTIFICATION_TITLES["vision"],
                        str(text or ""),
                        target="vision",
                        alert=True,
                    )

        try:
            self.controller.submit_background(
                self.controller.run_vision(goal), done
            )
        except RuntimeError as exc:
            return {"ok": False, "error": f"Analiz başlatılamadı ({exc})."}
        return {"ok": True}

    def research_history(self) -> dict[str, Any]:
        """Past research questions from the cache, newest first."""
        research = self.controller.application.research
        cache = getattr(research, "cache", None) if research is not None else None
        if cache is None:
            return {"ok": True, "items": []}
        try:
            return {"ok": True, "items": _jsonable(cache.recent(8))}
        except Exception as exc:
            return {"ok": False, "error": f"Geçmiş okunamadı ({type(exc).__name__})."}

    def research_sources(self) -> dict[str, Any]:
        """The places research may look, as configured: the page builds its chips from this."""
        research = self.controller.application.research
        provider = (
            (getattr(research, "sources", None) or getattr(research, "search_provider", None))
            if research is not None
            else None
        )
        available = tuple(getattr(provider, "available", ()) or ()) if provider is not None else ()
        if research is not None and not available:
            available = ("web",)
        return {
            "ok": True,
            "sources": [
                {"id": spec.id, "label": spec.label, "kind": spec.kind, "description": spec.description}
                for spec in SOURCE_CATALOG
                if spec.id in available
            ],
        }

    def run_research(
        self,
        query: str,
        max_sources: Any = 5,
        sources: Any = None,
        site: Any = None,
    ) -> dict[str, Any]:
        normalized = str(query or "").strip()
        if not normalized:
            return {"ok": False, "error": "Araştırma sorgusu boş olamaz."}
        if self.controller.application.research is None:
            return {"ok": False, "error": "Web araştırması ayarlanmamış."}
        if self.controller.paused:
            return {"ok": False, "error": PAUSED_MESSAGE}
        bounded = research_source_limit(
            max_sources,
            getattr(self.controller.application.research, "max_sources", MAX_RESEARCH_SOURCES),
        )
        try:
            chosen = parse_sources(sources)
            host = normalize_site(str(site) if site else None)
        except ValueError:
            return {"ok": False, "error": "Kaynak seçimi geçersiz."}
        if host is not None and "site" not in chosen:
            chosen = chosen + ("site",)
        if "site" in chosen and host is None:
            return {"ok": False, "error": "Belirli site araması için bir alan adı yaz."}

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
                if not self._attended:
                    self._publish(
                        "task",
                        NOTIFICATION_TITLES["research"],
                        f"“{normalized}” için rapor hazır.",
                        target="research",
                        alert=True,
                    )

        try:
            self.controller.submit_background(
                self.controller.run_research(
                    normalized, max_sources=bounded, sources=chosen, site=host
                ),
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
        approval_payload = {
            "token": token,
            "tool": request.tool_name,
            "operation": request.operation,
            "risk": request.risk_level.value,
            "reason": request.reason,
            "parameters": dict(safe_approval_parameters(request.parameters)),
            "seconds": max(int(wait_seconds), 1),
            "source": request.request_source,
            "description": self._tool_description(request.tool_name),
        }
        self._push("approval", approval_payload)
        self._notify_approval_watchers("approval", approval_payload)
        if not self._attended:
            self._publish(
                "approval",
                NOTIFICATION_TITLES["approval"],
                self._tool_description(request.tool_name) or request.tool_name,
                severity="warning",
                data={"tool": request.tool_name, "risk": request.risk_level.value},
                dedupe_key=f"approval:{request.tool_name}",
                alert=True,
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
            self._notify_approval_watchers("approval_closed", {"token": token})

    def _notify_approval_watchers(self, kind: str, payload: dict[str, Any]) -> None:
        for watcher in list(self._approval_watchers):
            try:
                watcher(kind, payload)
            except Exception:
                # A watcher's failure must never decide an approval.
                pass

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

    def system_pulse(self) -> dict[str, Any]:
        """CPU, memory and disk right now, measured, never estimated.

        The CPU figure is the busy share between this call and the
        previous one, so the very first reading honestly answers None
        and the page shows a dash until the second beat.
        """
        try:
            from app.platform.windows.service import (
                WindowsIntegrationService,
                cpu_percent_between,
            )

            times = WindowsIntegrationService.read_cpu_times()
            info = WindowsIntegrationService.system_info()
        except OSError as exc:
            return {"ok": False, "error": f"Sistem ölçülemedi ({type(exc).__name__})."}
        previous = self._pulse_times
        self._pulse_times = times
        cpu = cpu_percent_between(previous, times) if previous is not None else None
        total = int(info.get("memory_total_bytes") or 0)
        available = int(info.get("memory_available_bytes") or 0)
        return {
            "ok": True,
            "cpu_percent": cpu,
            "memory_percent": round(100.0 * (total - available) / total, 1) if total else None,
            "memory_used_gib": round((total - available) / 1024 ** 3, 1) if total else None,
            "memory_total_gib": info.get("memory_total_gib"),
            "disk_free_gib": info.get("disk_free_gib"),
            "disk_total_gib": info.get("disk_total_gib"),
        }

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
    # Notifications
    # ------------------------------------------------------------------
    def _notification_state(
        self, limit: int = NOTIFICATION_LIST_LIMIT
    ) -> dict[str, Any]:
        summary = self._notifications.summary()
        return {
            "items": [
                entry.to_dict() for entry in self._notifications.list(limit=limit)
            ],
            "unread": summary["unread"],
            "total": summary["total"],
        }

    def list_notifications(self, limit: Any = NOTIFICATION_LIST_LIMIT) -> dict[str, Any]:
        try:
            bound = int(limit)
        except (TypeError, ValueError):
            bound = NOTIFICATION_LIST_LIMIT
        bound = max(1, min(bound, NOTIFICATION_LIST_LIMIT))
        return {"ok": True, **self._notification_state(bound)}

    def mark_notifications_read(self, notification_ids: Any = None) -> dict[str, Any]:
        """Mark the given entries read; ``None`` marks every entry."""
        ids: list[str] | None = None
        if notification_ids is not None:
            if isinstance(notification_ids, (str, bytes)) or not isinstance(
                notification_ids, Iterable
            ):
                ids = [str(notification_ids)]
            else:
                ids = [str(item) for item in notification_ids][:NOTIFICATION_LIST_LIMIT]
        changed = self._notifications.mark_read(ids)
        return {
            "ok": True,
            "changed": changed,
            "unread": self._notifications.unread_count(),
        }

    def dismiss_notification(self, notification_id: str) -> dict[str, Any]:
        removed = self._notifications.dismiss(str(notification_id or ""))
        unread = self._notifications.unread_count()
        if not removed:
            return {"ok": False, "error": "Bildirim bulunamadı.", "unread": unread}
        return {"ok": True, "unread": unread}

    def clear_notifications(self) -> dict[str, Any]:
        cleared = self._notifications.clear()
        return {"ok": True, "cleared": cleared, "unread": 0}

    def set_visible(self, visible: Any) -> dict[str, Any]:
        """The page reports document visibility; hidden means unattended."""
        attended = visible is True or (
            isinstance(visible, str) and visible.strip().lower() == "true"
        )
        self._set_window_visible(attended)
        return {"ok": True, "visible": self._attended}

    # ------------------------------------------------------------------
    # File access: roots the user grants, snapshots the user restores
    # ------------------------------------------------------------------
    def _filesystem(self) -> tuple[Any | None, Any | None]:
        """(windows service, bounded filesystem) or (None, None)."""
        windows = getattr(self.controller.application, "windows", None)
        filesystem = getattr(windows, "filesystem", None)
        if windows is None or filesystem is None:
            return None, None
        return windows, filesystem

    def _file_roots_payload(self) -> dict[str, Any]:
        windows, filesystem = self._filesystem()
        if windows is None:
            return {"available": False, "roots": []}
        try:
            grants = windows.filesystem_root_grants()
        except Exception:
            grants = ()
        return {
            "available": True,
            "roots": [
                {
                    "root_id": grant.root_id,
                    "name": grant.display_name,
                    "path": str(grant.path),
                }
                for grant in grants
            ],
        }

    def _record_ui_event(self, name: str, message: str, **attributes: Any) -> None:
        diagnostics = getattr(self.controller.application, "diagnostics", None)
        if diagnostics is None:
            return
        try:
            diagnostics.record("ui", name, message, attributes=attributes)
        except Exception:
            pass

    def list_file_roots(self) -> dict[str, Any]:
        return {"ok": True, **self._file_roots_payload()}

    def pick_file_root(self) -> dict[str, Any]:
        """Open the native folder picker; the choice is granted only after
        the page confirms it through :meth:`grant_file_root`."""
        window = self._window
        if window is None:
            return {"ok": False, "error": "Pencere hazır değil."}
        if self._filesystem()[0] is None:
            return {"ok": False, "error": FILE_ACCESS_UNAVAILABLE}
        selection: list[Any] = []

        def choose() -> None:
            selection.append(
                window.create_file_dialog(
                    webview.FOLDER_DIALOG, directory=str(Path.home())
                )
            )

        try:
            _run_on_ui_thread(window, choose)
        except Exception as exc:
            return {
                "ok": False,
                "error": f"Klasör seçici açılamadı ({type(exc).__name__}).",
            }
        chosen = selection[0] if selection else None
        if isinstance(chosen, (list, tuple)):
            chosen = chosen[0] if chosen else None
        if not chosen:
            return {"ok": True, "path": None}
        return {"ok": True, "path": str(chosen)}

    def grant_file_root(self, path: str, confirmed: bool = False) -> dict[str, Any]:
        if confirmed is not True:
            return {"ok": False, "error": "Klasör erişimi onaylanmadı."}
        windows, _filesystem = self._filesystem()
        if windows is None:
            return {"ok": False, "error": FILE_ACCESS_UNAVAILABLE}
        candidate = str(path or "").strip()
        if not candidate:
            return {"ok": False, "error": "Klasör yolu boş olamaz."}
        try:
            grant = windows.grant_filesystem_root(candidate)
        except Exception as exc:
            return {"ok": False, "error": _grant_failure_message(exc)}
        self._record_ui_event(
            "filesystem.root_granted",
            "A filesystem root was granted from the desktop.",
            root_id=grant.root_id,
        )
        self._push_snapshot()
        return {"ok": True, "root_id": grant.root_id, **self._file_roots_payload()}

    def revoke_file_root(self, root_id: str) -> dict[str, Any]:
        windows, _filesystem = self._filesystem()
        if windows is None:
            return {"ok": False, "error": FILE_ACCESS_UNAVAILABLE}
        try:
            removed = windows.revoke_filesystem_root(str(root_id or ""))
        except Exception as exc:
            return {
                "ok": False,
                "error": f"Klasör erişimi kaldırılamadı ({type(exc).__name__}).",
            }
        if not removed:
            return {"ok": False, "error": "Bu kimlikle bir klasör erişimi yok."}
        self._record_ui_event(
            "filesystem.root_revoked",
            "A filesystem root was revoked from the desktop.",
            root_id=str(root_id),
        )
        self._push_snapshot()
        return {"ok": True, **self._file_roots_payload()}

    def list_snapshots(self, limit: Any = 50) -> dict[str, Any]:
        _windows, filesystem = self._filesystem()
        if filesystem is None:
            return {"ok": True, "available": False, "snapshots": [], "usage": None}
        try:
            bounded = max(1, min(int(limit), 200))
        except (TypeError, ValueError):
            bounded = 50
        result = filesystem.list_filesystem_snapshots(limit=bounded)
        if not result.succeeded:
            return {"ok": False, "error": f"Anlık görüntüler okunamadı ({result.error})."}
        data = result.data or {}
        return {
            "ok": True,
            "available": bool(data.get("available")),
            "snapshots": _jsonable(data.get("snapshots") or []),
            "total": data.get("total_snapshots", 0),
            "usage": _jsonable(data.get("usage")),
        }

    def restore_snapshot(self, snapshot_id: str, confirmed: bool = False) -> dict[str, Any]:
        """Write a snapshot back; the page confirms first, like a deletion."""
        if confirmed is not True:
            return {"ok": False, "error": "Geri yükleme onaylanmadı."}
        _windows, filesystem = self._filesystem()
        if filesystem is None:
            return {"ok": False, "error": FILE_ACCESS_UNAVAILABLE}
        result = filesystem.undo_filesystem_change(str(snapshot_id or ""))
        if not result.succeeded:
            return {"ok": False, "error": _restore_failure_message(result.error)}
        data = result.data or {}
        self._record_ui_event(
            "filesystem.snapshot_restored",
            "A filesystem snapshot was restored from the desktop.",
            root_id=str(data.get("root_id")),
            snapshot_id=str(snapshot_id),
        )
        return {
            "ok": True,
            "message": "Dosya anlık görüntüden geri yüklendi.",
            "root_id": data.get("root_id"),
            "path": data.get("path"),
        }

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

    def _reminders(self) -> Any | None:
        return getattr(self.controller.application, "reminders", None)

    def _spotify_ducker(self) -> Any | None:
        spotify = getattr(self.controller.application, "spotify", None)
        if spotify is None:
            return None
        from app.integrations.spotify_desktop import SpotifyDucker

        return SpotifyDucker(spotify)

    def list_reminders(self) -> dict[str, Any]:
        """Active reminders straight from the service, waiting ones first."""
        service = self._reminders()
        if service is None:
            return {"ok": False, "error": "Hatırlatıcı hizmeti kapalı."}
        result = service.list_active()
        if not result.succeeded:
            return {"ok": False, "error": str(result.message or "Hatırlatıcılar okunamadı.")}
        return {"ok": True, "reminders": _jsonable((result.data or {}).get("reminders", []))}

    def create_reminder(self, text: Any, when: Any) -> dict[str, Any]:
        """One reminder from the page: '+25' minutes from now or 'HH:MM' today.

        The service owns the parsing rules and answers in Turkish; the
        page only decides which of its two forms the input takes.
        """
        service = self._reminders()
        if service is None:
            return {"ok": False, "error": "Hatırlatıcı hizmeti kapalı."}
        body = str(text or "").strip()
        moment = str(when or "").strip()
        minutes = 0
        at = ""
        if moment.startswith("+") and moment[1:].isdigit():
            minutes = int(moment[1:])
        elif moment.isdigit():
            minutes = int(moment)
        else:
            at = moment
        result = service.create(body, minutes=minutes, at=at)
        if not result.succeeded:
            return {"ok": False, "error": str(result.message or "Hatırlatıcı kurulamadı.")}
        due = (result.data or {}).get("due_local", "")
        self._record_ui_event("reminder.created", "A reminder was created from the page.")
        return {"ok": True, "message": f"Hatırlatıcı kuruldu: {due}.", "due_local": str(due)}

    def cancel_reminder(self, reminder_id: Any, confirmed: Any = False) -> dict[str, Any]:
        """Cancel one reminder; the page asks the user first, then says so."""
        if confirmed is not True:
            return {"ok": False, "error": "İptal işlemi onaylanmadı."}
        service = self._reminders()
        if service is None:
            return {"ok": False, "error": "Hatırlatıcı hizmeti kapalı."}
        result = service.cancel(str(reminder_id or ""))
        if not result.succeeded:
            return {"ok": False, "error": str(result.message or "Hatırlatıcı iptal edilemedi.")}
        self._record_ui_event("reminder.cancelled", "A reminder was cancelled from the page.")
        return {"ok": True, "message": "Hatırlatıcı iptal edildi."}

    # ------------------------------------------------------------------
    # Mobile companion (Ayarlar > Telefon)
    # ------------------------------------------------------------------
    def mobile_status(self) -> dict[str, Any]:
        """What the desktop card shows: server state, paired devices, no secrets."""
        server = self._mobile
        if server is None:
            return {"ok": True, "enabled": False, "running": False, "port": None, "sessions": [], "pending_code": None}
        try:
            sessions = server.sessions_overview()
            pending = server.store.pending_pairing()
        except Exception as exc:
            return {"ok": False, "error": f"Telefon oturumları okunamadı ({type(exc).__name__})."}
        return {
            "ok": True,
            "enabled": True,
            "running": bool(server.running),
            "port": server.bound_port,
            "local_url": f"http://127.0.0.1:{server.bound_port}/",
            "sessions": sessions,
            "pending_code": pending,
        }

    def mobile_pairing_code(self) -> dict[str, Any]:
        """Mint a fresh single-use pairing code; shown once, never logged."""
        server = self._mobile
        if server is None:
            return {"ok": False, "error": "Telefon eşleştirme sunucusu bu oturumda çalışmıyor."}
        try:
            code, expires = server.store.create_pairing_code(label="desktop")
        except Exception as exc:
            return {"ok": False, "error": f"Kod üretilemedi ({type(exc).__name__})."}
        from app.mobile.sessions import format_pairing_code

        self._record_ui_event("mobile.pairing_code", "A mobile pairing code was issued.")
        return {"ok": True, "code": format_pairing_code(code), "expires_at": expires.isoformat()}

    def mobile_revoke_session(self, session_id: Any, confirmed: Any = False) -> dict[str, Any]:
        """Revoke one phone; its next request and its live channel both end."""
        if confirmed is not True:
            return {"ok": False, "error": "İptal işlemi onaylanmadı."}
        server = self._mobile
        if server is None:
            return {"ok": False, "error": "Telefon eşleştirme sunucusu bu oturumda çalışmıyor."}
        identifier = str(session_id or "")
        if not server.store.revoke(identifier):
            return {"ok": False, "error": "Oturum bulunamadı ya da zaten kapalı."}
        server.close_session_channels(identifier)
        self._record_ui_event("mobile.session_revoked", "A mobile session was revoked from the desktop.")
        return {"ok": True, "sessions": server.sessions_overview()}

    def mobile_revoke_all(self, confirmed: Any = False) -> dict[str, Any]:
        if confirmed is not True:
            return {"ok": False, "error": "İptal işlemi onaylanmadı."}
        server = self._mobile
        if server is None:
            return {"ok": False, "error": "Telefon eşleştirme sunucusu bu oturumda çalışmıyor."}
        for session in server.store.list_sessions():
            server.close_session_channels(session.session_id)
        count = server.store.revoke_all()
        self._record_ui_event("mobile.sessions_revoked", "All mobile sessions were revoked.", count=count)
        return {"ok": True, "revoked": count, "sessions": []}

    def state_backup_summary(self) -> dict[str, Any]:
        """What the settings card shows about the general state backups."""
        from app.config.paths import default_state_directory
        from app.state_backup import state_backup_summary

        try:
            return {"ok": True, **state_backup_summary(default_state_directory())}
        except OSError as exc:
            return {"ok": False, "error": f"Yedek dizini okunamadı ({type(exc).__name__})."}

    def state_backup_now(self) -> dict[str, Any]:
        """One safety copy of every state database, right now."""
        from app.config.paths import default_state_directory
        from app.state_backup import backup_state_now

        try:
            result = backup_state_now(default_state_directory())
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}
        except OSError as exc:
            return {"ok": False, "error": f"Yedek alınamadı ({type(exc).__name__})."}
        self._record_ui_event("state.backup", "State databases were backed up.")
        size_mb = result["bytes"] / (1024 * 1024)
        return {
            "ok": True,
            "message": f"Yedek alındı: {len(result['files'])} veritabanı, {size_mb:.1f} MB.",
            "summary": self.state_backup_summary(),
        }

    def _start_state_backup(self) -> None:
        """The weekly clock for the general state copies.

        Checks shortly after launch and then every six hours; a copy is
        made only when the newest one is older than a week, and the
        result lands in the notification centre, OS toast included when
        nobody is looking.
        """

        def loop() -> None:
            from app.config.paths import default_state_directory
            from app.state_backup import backup_state_now, state_backup_due

            delay = 120.0
            while not self._closing:
                time.sleep(min(delay, 30.0) if delay < 30.0 else 30.0)
                delay -= 30.0
                if delay > 0 or self._closing:
                    if self._closing:
                        return
                    continue
                delay = 6 * 3600.0
                settings = getattr(self.controller.application, "settings", None)
                if settings is None or not bool(
                    getattr(settings, "state_auto_backup", False)
                ):
                    continue
                state_dir = default_state_directory()
                try:
                    if not state_backup_due(state_dir):
                        continue
                    result = backup_state_now(state_dir)
                except Exception:
                    # Next window tries again; a failed backup must never
                    # take the desktop down with it.
                    continue
                size_mb = result["bytes"] / (1024 * 1024)
                self._publish(
                    "system",
                    "Haftalık durum yedeği alındı",
                    f"{len(result['files'])} veritabanı, {size_mb:.1f} MB.",
                    target="settings",
                    dedupe_key=f"state-backup:{result['folder']}",
                )

        threading.Thread(
            target=loop, name="nova-state-backup", daemon=True
        ).start()

    def save_desktop_settings(self, payload: Any) -> dict[str, Any]:
        """Non-secret assistant preferences: morning brief, web research,
        screen vision."""
        if self.api_settings is None:
            return {"ok": False, "error": "Ayar hizmeti kullanılamıyor."}
        data = payload if isinstance(payload, Mapping) else {}
        # A caller that does not mention vision keeps the stored choice:
        # an older page must not switch the screen off by omission.
        stored_vision = self.api_settings.preferences.load().vision_enabled
        try:
            self.api_settings.save_desktop(
                daily_brief_notification=bool(data.get("daily_brief_notification")),
                daily_brief_time=str(data.get("daily_brief_time") or ""),
                research_enabled=bool(data.get("research_enabled")),
                almanac_city=str(data.get("almanac_city") or ""),
                vision_enabled=bool(data.get("vision_enabled", stored_vision)),
            )
        except ValueError:
            return {"ok": False, "error": "Saat biçimi SS:DD olmalı (örn. 08:30)."}
        try:
            self._rebuild_runtime()
        except Exception as exc:
            return {"ok": False, "error": str(exc)}
        self._push_snapshot()
        return {
            "ok": True,
            "message": "Asistan ayarları kaydedildi ve uygulandı.",
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


FILE_ACCESS_UNAVAILABLE = "Dosya erişimi bu ortamda kullanılamıyor."
_GRANT_MESSAGES = (
    ("critical", "Kritik bir sistem klasörü verilemez; bir çalışma klasörü seç."),
    ("drive root", "Sürücü kökü çok geniş; içindeki bir klasörü seç."),
    ("Network", "Ağ klasörleri desteklenmiyor."),
    ("must exist", "Klasör bulunamadı."),
    ("must be a directory", "Seçim bir klasör değil."),
    ("snapshot store", "Anlık görüntü deposu bu klasörün içinde; başka bir klasör seç."),
    ("cannot be replaced", "Bu klasör zaten ekli."),
)
_RESTORE_MESSAGES = {
    "SNAPSHOT_NOT_FOUND": "Anlık görüntü bulunamadı.",
    "INVALID_SNAPSHOT_ID": "Anlık görüntü kimliği geçersiz.",
    "SNAPSHOT_CORRUPT": "Anlık görüntü doğrulanamadı; geri yükleme reddedildi.",
    "SNAPSHOT_STORE_UNAVAILABLE": "Anlık görüntü deposu kullanılamıyor.",
    "ROOT_NOT_ALLOWED": "Dosyanın klasörüne artık erişim yok; önce klasörü yeniden ekle.",
    "ROOT_UNAVAILABLE": "Dosyanın klasörüne şu an ulaşılamıyor.",
    "PATH_NOT_FOUND": "Dosyanın üst klasörü artık yok.",
}


def _grant_failure_message(exc: BaseException) -> str:
    detail = str(exc)
    for needle, message in _GRANT_MESSAGES:
        if needle in detail:
            return message
    return f"Klasör erişimi eklenemedi ({type(exc).__name__})."


def _restore_failure_message(code: str | None) -> str:
    return _RESTORE_MESSAGES.get(str(code or ""), f"Geri yükleme başarısız ({code or 'bilinmeyen hata'}).")


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
        # show() makes a minimised window visible without lifting it off the
        # taskbar, so the shortcut looked dead when JARVIS had been minimised
        # rather than closed. restore() puts it back to Normal.
        restore = getattr(self._window, "restore", None)
        if callable(restore):
            try:
                restore()
            except Exception as exc:
                self._bridge._record_ui_event(
                    "window.restore_failed",
                    "The window could not be restored from its minimised state.",
                    error=type(exc).__name__,
                )
        try:
            native = getattr(self._window, "native", None)
            if native is not None and hasattr(native, "BeginInvoke"):
                from System import Action  # pythonnet; present with pywebview

                native.BeginInvoke(Action(native.Activate))
        except Exception:
            pass
        self._bridge._set_window_visible(True)
        if self.service is not None:
            self.service.set_window_visible(True)

    def hide(self) -> None:
        self._window.hide()
        self._bridge._set_window_visible(False)
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
        if refresh_webview_cache(storage, web_root):
            diagnostics = getattr(controller.application, "diagnostics", None)
            if diagnostics is not None:
                try:
                    diagnostics.record(
                        "ui",
                        "webview.cache_cleared",
                        "Page assets changed; the WebView2 caches were cleared.",
                    )
                except Exception:
                    pass

    geometry = load_window_geometry(storage)
    window = webview.create_window(
        WINDOW_TITLE,
        url=str(web_root / "index.html"),
        js_api=bridge,
        width=geometry["width"] if geometry else 1440,
        height=geometry["height"] if geometry else 920,
        x=geometry["x"] if geometry else None,
        y=geometry["y"] if geometry else None,
        min_size=(420, 300),
        background_color="#05080f",
        # The first run opens maximized; after that the window comes back
        # exactly where and how the user left it.
        maximized=geometry is None,
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

    def notify_os(title: str, body: str) -> str | bool:
        # Unattended-window notifications: the tray balloon when the icon
        # exists, otherwise a plain Windows toast. Both are best effort;
        # the returned channel lands in the ledger for later inspection.
        if tray is not None and tray.active:
            tray.notify(title, body)
            return "tray"
        return "toast" if show_windows_toast(title, body) else False

    bridge._os_notifier = notify_os
    try:
        bridge._start_daily_brief(storage)
        bridge._start_state_backup()
    except Exception:
        # These are conveniences; launching matters more.
        pass
    mobile_server = None
    if bool(getattr(runtime_settings, "mobile_enabled", False)):
        try:
            from app.config.paths import default_state_path
            from app.mobile.server import MobileServer
            from app.mobile.sessions import MobileSessionStore

            mobile_store = MobileSessionStore(
                default_state_path("jarvis_mobile.sqlite3"),
                pairing_ttl_seconds=int(getattr(runtime_settings, "mobile_pairing_ttl_seconds", 600)),
                session_days=int(getattr(runtime_settings, "mobile_session_days", 30)),
            )
            mobile_server = MobileServer(
                controller, bridge, mobile_store,
                port=int(getattr(runtime_settings, "mobile_port", 8765)),
            )
            mobile_server.start()
            bridge._mobile = mobile_server
        except Exception as exc:
            # A busy port or a broken store must not keep the desktop from
            # opening; the Telefon card says the server is not running.
            mobile_server = None
            _record_tray_problem(controller, None, exc)
    # pywebview fires ``restored`` only for a return to the Normal state;
    # a maximized window that was minimized comes back as ``maximized``.
    for event_name, visible in (
        ("minimized", False),
        ("restored", True),
        ("maximized", True),
        ("shown", True),
    ):
        hook = getattr(window.events, event_name, None)
        if hook is None:
            continue

        def note(*_args: Any, visible: bool = visible) -> None:
            # These arrive on the UI thread; the bridge needs to know which
            # one that is before it evaluates anything on it.
            bridge._note_ui_thread()
            bridge._set_window_visible(visible)

        try:
            hook += note
        except Exception:
            pass
    hidden_notice_shown = False

    def hide_to_tray() -> None:
        nonlocal hidden_notice_shown
        actions.hide()
        if not hidden_notice_shown:
            hidden_notice_shown = True
            tray.notify(WINDOW_TITLE, TRAY_HIDDEN_NOTICE)

    def on_closing() -> bool | None:
        # Runs on the window's UI thread. Remember the frame first: both the
        # hide-to-tray and the real close should reopen where the user was.
        save_window_geometry(storage, window)
        # Hiding here would take the bridge
        # through a diagnostic push and back into JavaScript on this very
        # thread, which then waits for itself: the window goes to the tray
        # and never returns, and the next launch's activation signal reaches
        # an instance that can no longer answer it. Cancel first, hide after.
        bridge._note_ui_thread()
        if (
            close_to_tray
            and tray is not None
            and tray.active
            and not actions.exiting
        ):
            bridge._defer("window:hide", hide_to_tray)
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
        if mobile_server is not None:
            try:
                mobile_server.stop()
            except Exception:
                pass
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
            # A second launch signals this one to come back. The show goes
            # through the same queue as the hide, so a relaunch that arrives
            # while the window is still on its way to the tray cannot be
            # overtaken by it and leave nothing on screen.
            activation_watch(lambda: bridge._defer("window:show", actions.open))
        webview.start(**start_options)
    finally:
        release()
