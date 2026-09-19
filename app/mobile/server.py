"""The loopback HTTP surface of the mobile companion.

One small server inside the desktop process: static PWA files, a narrow
JSON API and a server-sent-events channel. It binds to 127.0.0.1 only;
Tailscale Serve carries it to the phone over the private network with
HTTPS. Every API call needs a paired device session (an HttpOnly cookie),
every mutation needs the page's own header and a matching origin, and a
message from the phone enters the exact same core, permission and
conversation pipeline a desktop message does.
"""

from __future__ import annotations

import hashlib
import io
import json
import platform
import queue
import re
import threading
import time
import wave
from concurrent.futures import Future
from dataclasses import asdict, dataclass, field, is_dataclass
from datetime import date, datetime
from enum import Enum
from http import HTTPStatus
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path, PurePath
from typing import Any, Callable
from urllib.parse import urlsplit
from uuid import UUID

from app.core.models import Context, RequestSource
from app.mobile.sessions import (
    DeviceSession,
    MobileSessionStore,
    PairingError,
    RateLimiter,
)

WEB_ROOT = Path(__file__).resolve().parent / "web"
TOKENS_CSS = Path(__file__).resolve().parents[1] / "ui" / "nova" / "web" / "css" / "tokens.css"
COOKIE_NAME = "jarvis_mobile"
CLIENT_HEADER = "X-JARVIS-Client"
MAX_BODY_BYTES = 256 * 1024
MAX_MESSAGE_CHARS = 8000
MAX_TURNS_PER_SESSION = 50
EVENT_HEARTBEAT_SECONDS = 15.0
TASK_ACTION_TIMEOUT_SECONDS = 30.0
PAIRING_ATTEMPTS_PER_MINUTE = 5
PAIRING_ATTEMPTS_PER_TEN_MINUTES = 30
STATIC_FILES: dict[str, tuple[str, str]] = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/index.html": ("index.html", "text/html; charset=utf-8"),
    "/app.css": ("app.css", "text/css; charset=utf-8"),
    "/app.js": ("app.js", "text/javascript; charset=utf-8"),
    "/manifest.webmanifest": ("manifest.webmanifest", "application/manifest+json"),
    "/sw.js": ("sw.js", "text/javascript; charset=utf-8"),
    "/offline.html": ("offline.html", "text/html; charset=utf-8"),
    "/icons/icon.svg": ("icons/icon.svg", "image/svg+xml"),
    "/icons/icon-192.png": ("icons/icon-192.png", "image/png"),
    "/icons/icon-512.png": ("icons/icon-512.png", "image/png"),
}
TEMPLATED_FILES = {"index.html", "sw.js"}
NOVA_WEB_ROOT = Path(__file__).resolve().parents[1] / "ui" / "nova" / "web"
NOVA_SHIM = WEB_ROOT / "nova-shim.js"
NOVA_CONTENT_TYPES = {".html": "text/html; charset=utf-8", ".css": "text/css; charset=utf-8", ".js": "text/javascript; charset=utf-8"}
# Kept in step with the DENIED set in nova-shim.js: window and native
# dialogs, the PC microphone and screen, credential and pairing management
# stay on the PC. Everything else the desktop page can do, the phone can.
PHONE_DENIED = frozenset({
    "delete_api_key", "save_settings", "test_connection",
    "mobile_pairing_code", "mobile_revoke_all", "mobile_revoke_session", "mobile_status",
    "medical_pick_file", "pick_file_root", "pick_folder", "export_conversation", "open_external",
    "grant_file_root", "revoke_file_root", "restore_snapshot",
    "set_compact", "set_visible", "start_voice", "stop_voice", "run_vision",
})
PHONE_DENIED_MESSAGE = "Bu işlem telefondan yapılamaz; bilgisayardaki JARVIS'te yap."
# Phone voice: the phone records, the PC's own speech providers listen and
# speak. 16 kHz mono PCM16 for 30 s is under a megabyte; two is plenty.
MAX_VOICE_UPLOAD_BYTES = 2 * 1024 * 1024
VOICE_SAMPLE_RATES = range(8_000, 48_001)
VOICE_MIME_BY_ENCODING = {
    "wav": "audio/wav", "mp3": "audio/mpeg", "opus": "audio/ogg", "aac": "audio/aac", "flac": "audio/flac",
}
_MARKDOWN_NOISE = re.compile(r"[*_`#>]+")
BRIDGE_METHOD_PATTERN = re.compile(r"^[a-z][a-z0-9_]{1,60}$")
NL_BYTES = b'\n'
CLIENT_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{8,64}$")
CANCELLABLE_STATUSES = {"queued", "running", "paused", "waiting_for_input", "waiting_for_approval"}
PAUSED_MESSAGE = "JARVIS duraklatıldı; masaüstünden sürdürülene kadar komut almıyor."
# What a task action actually achieved, said in the phone's own language.
TASK_ACTION_MESSAGES_TR = {
    ("resume", "completed"): "Görev tamamlandı.",
    ("resume", "paused"): "Görev yeniden duraklatıldı; henüz bitmedi.",
    ("resume", "failed"): "Görev sürdürülemedi ve başarısız oldu.",
    ("resume", "cancelled"): "Görev sürdürülürken iptal edildi.",
    ("pause", "paused"): "Görev duraklatıldı.",
    ("cancel", "cancelled"): "Görev iptal edildi.",
    # Requested but not confirmed at the boundary: say that, do not imply it stopped.
    ("pause", "partial"): "Duraklatma istendi; görev henüz durmadı.",
    ("cancel", "partial"): "İptal istendi; görev henüz durmadı.",
}


def _json_default(value: Any) -> Any:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, UUID):
        return str(value)
    if is_dataclass(value) and not isinstance(value, type):
        return asdict(value)
    if isinstance(value, (set, frozenset, tuple)):
        return list(value)
    if isinstance(value, (PurePath, bytes)):
        return str(value)
    return str(value)


def _dumps(payload: Any) -> bytes:
    return json.dumps(payload, ensure_ascii=False, default=_json_default).encode("utf-8")


@dataclass
class TurnRecord:
    """One message from a phone and what became of it.

    Keyed by the phone's own client id, so a retry after a dropped
    connection finds this record instead of running the command again.
    """

    client_id: str
    conversation_id: str
    text: str
    status: str = "running"  # running | done | failed
    reply: str = ""
    role: str = "assistant"
    error: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    started_at: float = field(default_factory=time.time)
    finished_at: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "client_id": self.client_id,
            "conversation_id": self.conversation_id,
            "text": self.text,
            "status": self.status,
            "reply": self.reply,
            "role": self.role,
            "error": self.error,
            "metadata": dict(self.metadata),
            "started_at": self.started_at,
            "finished_at": self.finished_at,
        }


class MobileServer:
    """Owns the listening socket, the sessions and the live channels."""

    def __init__(
        self,
        controller: Any,
        bridge: Any,
        store: MobileSessionStore,
        *,
        host: str = "127.0.0.1",
        port: int = 8765,
        web_root: Path = WEB_ROOT,
        tokens_css: Path = TOKENS_CSS,
        nova_shim: Path | None = None,
    ) -> None:
        self.controller = controller
        self.bridge = bridge
        self.store = store
        self.host = host
        self.port = int(port)
        self.web_root = Path(web_root)
        self.tokens_css = Path(tokens_css)
        self.nova_shim = Path(nova_shim) if nova_shim is not None else NOVA_SHIM
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None
        self._lock = threading.RLock()
        self._stopping = False
        self._streams: dict[str, set[queue.Queue[Any]]] = {}
        self._turns: dict[str, dict[str, TurnRecord]] = {}
        self._running: dict[str, str] = {}  # session_id -> client_id of the running turn
        self._pending_approvals: dict[str, dict[str, Any]] = {}
        self._pair_limiter = RateLimiter(PAIRING_ATTEMPTS_PER_MINUTE, 60.0)
        # The desktop's own offline voice; tests swap in a stub.
        from app.voice.audio import synthesize_local_turkish

        self.local_tts: Callable[[str], Any] = synthesize_local_turkish
        self._pair_global = RateLimiter(PAIRING_ATTEMPTS_PER_TEN_MINUTES, 600.0)
        self.stamp = self._compute_stamp()
        self.started_at = datetime.now().astimezone()
        watchers = getattr(bridge, "_approval_watchers", None)
        if isinstance(watchers, list):
            watchers.append(self._on_approval)

    # ------------------------------------------------------------ lifecycle
    def _compute_stamp(self) -> str:
        digest = hashlib.sha256()
        for relative, _content_type in sorted(set(STATIC_FILES.values())):
            candidate = self.web_root / relative
            if candidate.is_file():
                digest.update(relative.encode("utf-8"))
                digest.update(candidate.read_bytes())
        if self.tokens_css.is_file():
            digest.update(self.tokens_css.read_bytes())
        # The shim is served under the same immutable version query as the
        # shell, so a change to it must change the stamp too.
        if self.nova_shim.is_file():
            digest.update(self.nova_shim.read_bytes())
        return digest.hexdigest()[:12]

    @property
    def bound_port(self) -> int:
        return self._server.server_address[1] if self._server is not None else self.port

    @property
    def running(self) -> bool:
        return self._server is not None and not self._stopping

    def start(self) -> int:
        """Bind and serve on a daemon thread; returns the bound port."""
        if self._server is not None:
            return self.bound_port
        handler = _make_handler(self)
        server = ThreadingHTTPServer((self.host, self.port), handler)
        server.daemon_threads = True
        self._server = server
        self._stopping = False
        self._thread = threading.Thread(target=server.serve_forever, name="jarvis-mobile-http", daemon=True)
        self._thread.start()
        return self.bound_port

    def stop(self) -> None:
        server = self._server
        if server is None:
            return
        self._stopping = True
        with self._lock:
            queues = [item for group in self._streams.values() for item in group]
        for item in queues:
            try:
                item.put_nowait(None)
            except queue.Full:
                pass
        server.shutdown()
        server.server_close()
        self._server = None
        watchers = getattr(self.bridge, "_approval_watchers", None)
        if isinstance(watchers, list) and self._on_approval in watchers:
            watchers.remove(self._on_approval)

    # -------------------------------------------------------------- channels
    def subscribe(self, session_id: str) -> queue.Queue[Any]:
        item: queue.Queue[Any] = queue.Queue(maxsize=1000)
        with self._lock:
            self._streams.setdefault(session_id, set()).add(item)
        return item

    def unsubscribe(self, session_id: str, item: queue.Queue[Any]) -> None:
        with self._lock:
            group = self._streams.get(session_id)
            if group is not None:
                group.discard(item)
                if not group:
                    self._streams.pop(session_id, None)

    def emit(self, session_id: str | None, kind: str, payload: dict[str, Any]) -> None:
        """Send one event to a session's channels, or to every session."""
        with self._lock:
            if session_id is None:
                targets = [item for group in self._streams.values() for item in group]
            else:
                targets = list(self._streams.get(session_id, ()))
        for item in targets:
            try:
                item.put_nowait((kind, payload))
            except queue.Full:
                pass

    def broadcast_push(self, kind: str, payload: Any) -> None:
        """Mirror one desktop push (window.NOVA.push) to every phone."""
        if not self._streams:
            return
        try:
            data = json.loads(_dumps({"kind": kind, "payload": payload}))
        except (TypeError, ValueError):
            return
        self.emit(None, "push", data)

    # ----------------------------------------------------------------- voice
    def _voice(self) -> Any | None:
        return getattr(self.controller.application, "voice", None)

    def _run_on_loop(self, coroutine: Any, timeout: float) -> Any:
        """One coroutine on the controller's loop, awaited from a request thread."""
        future = self.controller.submit_background(coroutine, lambda _done: None)
        return future.result(timeout=timeout)

    def transcribe_wav(self, payload: bytes) -> dict[str, Any]:
        """Text for one WAV recording from the phone, through the PC's recognizer.

        Silence is not an error: it comes back as an empty text so the
        phone can simply listen again. Provider failures are said in words.
        """
        from app.voice.errors import VoiceConfigurationError, VoiceNoSpeech, VoiceProviderError, VoiceTimeoutError
        from app.voice.models import AudioCapture

        voice = self._voice()
        recognizer = getattr(voice, "recognizer", None) if voice is not None else None
        if recognizer is None:
            return {"ok": False, "status": 409, "error": "Sesli iletişim bu bilgisayarda ayarlanmamış."}
        try:
            with wave.open(io.BytesIO(payload)) as handle:
                channels, width, rate = handle.getnchannels(), handle.getsampwidth(), handle.getframerate()
                frames = handle.readframes(handle.getnframes())
        except (wave.Error, EOFError, ValueError):
            return {"ok": False, "status": 400, "error": "Ses kaydı okunamadı (WAV bekleniyor)."}
        if channels != 1 or width != 2 or rate not in VOICE_SAMPLE_RATES:
            return {"ok": False, "status": 400, "error": "Ses kaydı tek kanallı 16 bit PCM olmalı."}
        if not frames:
            return {"ok": True, "text": ""}
        settings = getattr(self.controller.application, "settings", None)
        timeout = float(getattr(settings, "voice_operation_timeout_seconds", 60.0) or 60.0)
        language = getattr(settings, "voice_language", None)
        capture = AudioCapture(data=bytearray(frames), sample_rate=rate, channels=1, sample_width=2)
        try:
            result = self._run_on_loop(recognizer.transcribe(capture, language=language), timeout)
        except VoiceNoSpeech:
            return {"ok": True, "text": ""}
        except VoiceConfigurationError as exc:
            return {"ok": False, "status": 409, "error": f"Ses tanıma ayarlanmamış ({exc})."}
        except (VoiceProviderError, VoiceTimeoutError, TimeoutError):
            return {"ok": False, "status": 502, "error": "Konuşma çözümlenemedi; sağlayıcı yanıt vermedi. Tekrar dene."}
        except Exception as exc:
            return {"ok": False, "status": 502, "error": f"Konuşma çözümlenemedi ({type(exc).__name__})."}
        text = str(getattr(result, "text", "") or "").strip()
        return {"ok": True, "text": text, "provider": getattr(result, "provider", None)}

    @staticmethod
    def speakable(text: str) -> str:
        """What the synthesizer is given: prose, not markup."""
        return " ".join(_MARKDOWN_NOISE.sub("", str(text or "")).split())

    def speak(self, text: str) -> tuple[bytes, str, str] | dict[str, Any]:
        """(audio bytes, mime type, source) for a reply, through the PC's synthesizer.

        When the cloud voice fails the same local voice the desktop falls
        back to is tried; when that is missing too the caller gets words,
        and the phone shows the reply as text instead of pretending.
        """
        from app.voice.models import AudioEncoding, pcm16_to_wav

        voice = self._voice()
        synthesizer = getattr(voice, "synthesizer", None) if voice is not None else None
        if synthesizer is None:
            return {"ok": False, "status": 409, "error": "Sesli iletişim bu bilgisayarda ayarlanmamış."}
        settings = getattr(self.controller.application, "settings", None)
        limit = int(getattr(settings, "voice_max_tts_characters", 4000) or 4000)
        timeout = float(getattr(settings, "voice_operation_timeout_seconds", 60.0) or 60.0)
        spoken = self.speakable(text)
        if not spoken:
            return {"ok": False, "status": 400, "error": "Seslendirilecek metin boş."}
        if len(spoken) > limit:
            spoken = spoken[:limit].rsplit(" ", 1)[0]
        try:
            speech = self._run_on_loop(synthesizer.synthesize(spoken), timeout)
            encoding = getattr(speech.encoding, "value", str(speech.encoding))
            if encoding == AudioEncoding.PCM16.value:
                return pcm16_to_wav(bytes(speech.data), 24_000), "audio/wav", "cloud"
            return bytes(speech.data), VOICE_MIME_BY_ENCODING.get(encoding, "application/octet-stream"), "cloud"
        except Exception:
            pass
        try:
            local = self._run_on_loop(self.local_tts(spoken), timeout)
        except Exception:
            local = None
        if local:
            return bytes(local), "audio/wav", "local"
        return {"ok": False, "status": 502, "error": "Ses üretilemedi; yanıt metin olarak duruyor."}

    def bridge_method(self, name: str) -> Callable[..., Any] | None:
        """The NovaBridge method a phone may call, or None."""
        if not BRIDGE_METHOD_PATTERN.match(name) or name in PHONE_DENIED:
            return None
        attribute = getattr(type(self.bridge), name, None)
        if attribute is None or not callable(attribute):
            return None
        return getattr(self.bridge, name)

    def nova_index(self) -> bytes | None:
        """The desktop page with the phone shim and stylesheet injected."""
        index = NOVA_WEB_ROOT / "index.html"
        if not index.is_file():
            return None
        stamp = self.stamp.encode("ascii")
        body = index.read_bytes()
        body = body.replace(
            b"</head>",
            b'<link rel="manifest" href="/manifest.webmanifest">' + NL_BYTES + b"</head>",
            1,
        )
        body = body.replace(
            b'<script src="js/foundation.js"></script>',
            b'<script src="js/nova-shim.js?v=' + stamp + b'"></script>' + NL_BYTES + b'<script src="js/foundation.js"></script>',
            1,
        )
        return body

    def close_session_channels(self, session_id: str) -> None:
        with self._lock:
            targets = list(self._streams.pop(session_id, ()))
        for item in targets:
            try:
                item.put_nowait(("session_ended", {"reason": "revoked"}))
                item.put_nowait(None)
            except queue.Full:
                pass

    # ------------------------------------------------------------- approvals
    def _on_approval(self, kind: str, payload: dict[str, Any]) -> None:
        token = str(payload.get("token") or "")
        with self._lock:
            if kind == "approval":
                self._pending_approvals[token] = dict(payload)
            elif kind == "approval_closed":
                self._pending_approvals.pop(token, None)
        self.emit(None, kind, dict(payload))

    def pending_approvals(self) -> list[dict[str, Any]]:
        with self._lock:
            return [dict(item) for item in self._pending_approvals.values()]

    def decide_approval(self, token: str, approved: bool) -> dict[str, Any]:
        """A phone's decision resolves the same single-use future the desktop uses."""
        with self._lock:
            known = token in self._pending_approvals
        if not known:
            return {"ok": False, "error": "Onay isteği artık geçerli değil."}
        result = self.bridge.resolve_approval(token, approved is True)
        if result.get("ok"):
            with self._lock:
                self._pending_approvals.pop(token, None)
        return result

    # ----------------------------------------------------------------- turns
    def turn(self, session_id: str, client_id: str) -> TurnRecord | None:
        with self._lock:
            return self._turns.get(session_id, {}).get(client_id)

    def recent_turns(self, session_id: str, limit: int = 10) -> list[dict[str, Any]]:
        with self._lock:
            records = list(self._turns.get(session_id, {}).values())
        records.sort(key=lambda record: record.started_at, reverse=True)
        return [record.to_dict() for record in records[:limit]]

    def start_turn(self, session: DeviceSession, client_id: str, text: str, conversation_id: str | None) -> tuple[TurnRecord, bool]:
        """Run one message; a repeated client id returns the existing record.

        Returns (record, duplicate). Raises ValueError with a Turkish message
        when the turn cannot start; the caller maps it to a status code.
        """
        if getattr(self.controller, "paused", False):
            raise ValueError(PAUSED_MESSAGE)
        with self._lock:
            existing = self._turns.get(session.session_id, {}).get(client_id)
            if existing is not None:
                return existing, True
            running = self._running.get(session.session_id)
            if running is not None:
                raise ValueError("Önceki yanıt tamamlanmadan yeni mesaj gönderilemez.")
            target = self._resolve_conversation(session, conversation_id)
            record = TurnRecord(client_id=client_id, conversation_id=target, text=text)
            bucket = self._turns.setdefault(session.session_id, {})
            bucket[client_id] = record
            if len(bucket) > MAX_TURNS_PER_SESSION:
                oldest = sorted(bucket.values(), key=lambda item: item.started_at)[: len(bucket) - MAX_TURNS_PER_SESSION]
                for stale in oldest:
                    if stale.status != "running":
                        bucket.pop(stale.client_id, None)
            self._running[session.session_id] = client_id
        session_id = session.session_id

        def stream(delta: str) -> None:
            if not delta:
                return
            record.reply += delta
            self.emit(session_id, "delta", {"client_id": client_id, "text": delta})

        def done(future: Future[Any]) -> None:
            try:
                message = future.result()
            except Exception as exc:  # the controller already wraps most failures
                record.status = "failed"
                record.error = f"İstek tamamlanamadı ({type(exc).__name__})."
            else:
                role = getattr(message, "role", "assistant")
                text_out = getattr(message, "text", "") or ""
                record.role = role
                record.reply = text_out
                record.metadata = dict(getattr(message, "metadata", {}) or {})
                if role == "system":
                    record.status = "failed"
                    record.error = text_out
                else:
                    record.status = "done"
            record.finished_at = time.time()
            with self._lock:
                if self._running.get(session_id) == client_id:
                    self._running.pop(session_id, None)
            self.emit(session_id, "turn_done", record.to_dict())

        context = Context(conversation_id=UUID(record.conversation_id))
        try:
            self.controller.submit_background(
                self.controller.submit_command(
                    text, context=context, stream_callback=stream, source=RequestSource.API
                ),
                done,
            )
        except RuntimeError as exc:
            with self._lock:
                self._running.pop(session_id, None)
                self._turns.get(session_id, {}).pop(client_id, None)
            raise ValueError(f"İstek gönderilemedi ({exc}).") from exc
        self.emit(session_id, "turn_started", record.to_dict())
        return record, False

    def _resolve_conversation(self, session: DeviceSession, conversation_id: str | None) -> str:
        engine = self.controller.application.conversation_engine
        chosen = conversation_id or session.conversation_id
        if chosen:
            try:
                conversation = engine.get(UUID(str(chosen)))
            except (KeyError, ValueError):
                conversation = None
            if conversation is not None and getattr(conversation.status, "value", conversation.status) == "active":
                if chosen != session.conversation_id:
                    self.store.set_conversation(session.session_id, str(chosen))
                return str(chosen)
        created = engine.create()
        self.store.set_conversation(session.session_id, str(created.conversation_id))
        return str(created.conversation_id)

    # --------------------------------------------------------- conversations
    def conversations(self, session: DeviceSession) -> list[dict[str, Any]]:
        rows = self.controller.list_conversations(limit=50)
        for row in rows:
            row["selected"] = row.get("conversation_id") == session.conversation_id
            row.pop("active", None)  # the desktop's own open conversation is not the phone's
        return rows

    def messages(self, conversation_id: str) -> dict[str, Any]:
        title, created, messages = self.controller.conversation_export(str(conversation_id))
        return {
            "conversation_id": str(conversation_id),
            "title": title,
            "created": created,
            "messages": [
                {"role": message.role, "text": message.text, "metadata": dict(message.metadata)}
                for message in messages
            ],
        }

    def select_conversation(self, session: DeviceSession, conversation_id: str) -> dict[str, Any]:
        engine = self.controller.application.conversation_engine
        conversation = engine.get(UUID(str(conversation_id)))  # KeyError/ValueError bubble up
        if getattr(conversation.status, "value", conversation.status) != "active":
            raise ValueError("Arşivdeki bir konuşmaya yazılamaz; önce masaüstünden arşivden çıkar.")
        self.store.set_conversation(session.session_id, str(conversation_id))
        return self.messages(str(conversation_id))

    def new_conversation(self, session: DeviceSession) -> dict[str, Any]:
        created = self.controller.application.conversation_engine.create()
        self.store.set_conversation(session.session_id, str(created.conversation_id))
        return {"conversation_id": str(created.conversation_id), "title": "Yeni konuşma", "messages": []}

    # ----------------------------------------------------------------- tasks
    def tasks(self) -> list[dict[str, Any]]:
        service = getattr(self.controller.application, "task_service", None)
        if service is None:
            return []
        rows = service.list(limit=50)
        for row in rows:
            status = str(row.get("status") or "")
            row["actions"] = {
                "cancel": status in CANCELLABLE_STATUSES,
                "pause": status == "running",
                "resume": status == "paused",
            }
        return rows

    def task_action(self, task_id: str, action: str) -> dict[str, Any]:
        service = getattr(self.controller.application, "task_service", None)
        if service is None or action not in {"cancel", "pause", "resume"}:
            return {"ok": False, "error": "Bu işlem desteklenmiyor."}
        operation = getattr(service, action)
        try:
            future = self.controller.submit_background(operation(str(task_id)), lambda _done: None)
            result = future.result(timeout=TASK_ACTION_TIMEOUT_SECONDS)
        except RuntimeError as exc:
            return {"ok": False, "error": f"Görev çalışma zamanı kullanılamıyor ({exc})."}
        except Exception as exc:
            return {"ok": False, "error": f"İşlem tamamlanamadı ({type(exc).__name__})."}
        succeeded = bool(getattr(result, "succeeded", False))
        # The service answers in English machine strings; the phone is
        # Turkish, and the outcome it reports must be the one that
        # happened - a resume that parks again is not a completion.
        status = getattr(getattr(result, "status", None), "value", "")
        task_status = str((getattr(result, "data", None) or {}).get("status") or "")
        spoken = TASK_ACTION_MESSAGES_TR.get((action, task_status)) or TASK_ACTION_MESSAGES_TR.get((action, status))
        if not spoken:
            spoken = "İşlem tamamlandı." if succeeded else str(getattr(result, "error", "") or getattr(result, "message", "") or "İşlem başarısız.")
        return {
            "ok": succeeded,
            "message": spoken,
            "verified": bool(getattr(result, "verified", False)),
            "error": None if succeeded else spoken,
        }

    # ----------------------------------------------------------------- state
    def state(self, session: DeviceSession) -> dict[str, Any]:
        conversation = None
        if session.conversation_id:
            try:
                summary = self.messages(session.conversation_id)
                conversation = {"conversation_id": summary["conversation_id"], "title": summary["title"]}
            except (KeyError, ValueError):
                conversation = None
        return {
            "ok": True,
            "session": {
                "session_id": session.session_id,
                "label": session.label,
                "created_at": session.created_at.isoformat(),
                "expires_at": session.expires_at.isoformat(),
            },
            "pc": platform.node(),
            "paused": bool(getattr(self.controller, "paused", False)),
            "conversation": conversation,
            "pending_approvals": self.pending_approvals(),
            "turns": self.recent_turns(session.session_id),
            "stamp": self.stamp,
        }

    def sessions_overview(self) -> list[dict[str, Any]]:
        return [item.to_dict() for item in self.store.list_sessions(include_revoked=False)]


# ---------------------------------------------------------------------------
# request handling
# ---------------------------------------------------------------------------


def _make_handler(server: MobileServer) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        server_version = "JARVIS-Mobile"
        sys_version = ""

        # ----------------------------------------------------------- utils
        def log_message(self, format: str, *args: Any) -> None:  # noqa: A002 - stdlib signature
            # Nothing from a request line is worth writing to a console the
            # desktop does not have; failures surface as responses.
            return

        def _client_key(self) -> str:
            forwarded = self.headers.get("X-Forwarded-For", "")
            first = forwarded.split(",")[0].strip() if forwarded else ""
            return first or self.client_address[0]

        def _secure(self) -> bool:
            proto = self.headers.get("X-Forwarded-Proto", "").lower()
            if proto == "https":
                return True
            host = (self.headers.get("Host") or "").split(":")[0].lower()
            return host not in {"127.0.0.1", "localhost", "[::1]", "::1"}

        def _cookie_token(self) -> str | None:
            raw = self.headers.get("Cookie")
            if not raw:
                return None
            jar = SimpleCookie()
            try:
                jar.load(raw)
            except Exception:
                return None
            morsel = jar.get(COOKIE_NAME)
            return morsel.value if morsel is not None else None

        def _session(self) -> DeviceSession | None:
            return server.store.authenticate(self._cookie_token())

        def _cookie_header(self, token: str | None, max_age: int) -> str:
            parts = [f"{COOKIE_NAME}={token or ''}", "Path=/", "HttpOnly", "SameSite=Strict", f"Max-Age={max_age}"]
            if self._secure():
                parts.append("Secure")
            return "; ".join(parts)

        def _same_origin(self) -> bool:
            """Mutations must come from the page itself.

            Three independent checks: the page's own header (a foreign
            origin cannot send it without a CORS preflight this server
            never answers), Sec-Fetch-Site when the browser provides it,
            and the Origin host against the host the request was made to
            (Host, or X-Forwarded-Host behind Tailscale Serve).
            """
            if not self.headers.get(CLIENT_HEADER):
                return False
            fetch_site = self.headers.get("Sec-Fetch-Site", "").lower()
            if fetch_site and fetch_site not in {"same-origin", "none"}:
                return False
            origin = self.headers.get("Origin")
            if origin:
                origin_host = (urlsplit(origin).netloc or "").lower()
                hosts = {
                    (self.headers.get("Host") or "").lower(),
                    (self.headers.get("X-Forwarded-Host") or "").lower(),
                }
                if origin_host not in hosts:
                    return False
            return True

        def _read_json(self) -> dict[str, Any]:
            length = int(self.headers.get("Content-Length") or 0)
            if length < 0 or length > MAX_BODY_BYTES:
                raise ValueError("İstek gövdesi çok büyük.")
            raw = self.rfile.read(length) if length else b""
            if not raw:
                return {}
            payload = json.loads(raw.decode("utf-8"))
            if not isinstance(payload, dict):
                raise ValueError("İstek gövdesi bir nesne olmalı.")
            return payload

        def _read_raw(self, limit: int) -> bytes:
            length = int(self.headers.get("Content-Length") or 0)
            if length <= 0:
                raise ValueError("İstek gövdesi boş.")
            if length > limit:
                raise OverflowError("İstek gövdesi çok büyük.")
            return self.rfile.read(length)

        def _send(self, status: int, body: bytes, content_type: str, extra: dict[str, str] | None = None) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            for key, value in (extra or {}).items():
                self.send_header(key, value)
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)

        def _json(self, status: int, payload: dict[str, Any], extra: dict[str, str] | None = None) -> None:
            headers = {"Cache-Control": "no-store"}
            headers.update(extra or {})
            self._send(status, _dumps(payload), "application/json; charset=utf-8", headers)

        def _unauthorized(self) -> None:
            self._json(
                HTTPStatus.UNAUTHORIZED,
                {"ok": False, "error": "Oturum yok ya da sona erdi; telefonu yeniden eşleştir."},
                {"Set-Cookie": self._cookie_header(None, 0)},
            )

        # --------------------------------------------------------- routing
        def do_GET(self) -> None:  # noqa: N802 - stdlib naming
            self._dispatch("GET")

        def do_HEAD(self) -> None:  # noqa: N802
            self._dispatch("GET")

        def do_POST(self) -> None:  # noqa: N802
            self._dispatch("POST")

        def _dispatch(self, method: str) -> None:
            path = urlsplit(self.path).path
            try:
                if path.startswith("/api/"):
                    self._api(method, path)
                elif path == "/nova" or path.startswith("/nova/"):
                    self._nova(method, path)
                elif method == "GET":
                    self._static(path)
                else:
                    self._json(HTTPStatus.METHOD_NOT_ALLOWED, {"ok": False, "error": "Yöntem desteklenmiyor."})
            except (BrokenPipeError, ConnectionResetError):
                return
            except Exception:
                try:
                    self._json(HTTPStatus.INTERNAL_SERVER_ERROR, {"ok": False, "error": "Sunucu hatası."})
                except Exception:
                    return

        def _static(self, path: str) -> None:
            entry = STATIC_FILES.get(path)
            if path == "/tokens.css":
                if not server.tokens_css.is_file():
                    self._send(HTTPStatus.NOT_FOUND, b"", "text/plain")
                    return
                self._send(
                    HTTPStatus.OK,
                    server.tokens_css.read_bytes(),
                    "text/css; charset=utf-8",
                    {"Cache-Control": "public, max-age=31536000, immutable" if "v=" in self.path else "no-cache"},
                )
                return
            if entry is None:
                self._send(HTTPStatus.NOT_FOUND, "Bulunamadı.".encode("utf-8"), "text/plain; charset=utf-8")
                return
            relative, content_type = entry
            candidate = server.web_root / relative
            if not candidate.is_file():
                self._send(HTTPStatus.NOT_FOUND, "Bulunamadı.".encode("utf-8"), "text/plain; charset=utf-8")
                return
            body = candidate.read_bytes()
            if relative in TEMPLATED_FILES:
                body = body.replace(b"__STAMP__", server.stamp.encode("ascii"))
            cache = "public, max-age=31536000, immutable" if ("v=" in self.path and relative not in TEMPLATED_FILES) else "no-cache"
            extra = {"Cache-Control": cache}
            if relative == "sw.js":
                extra["Service-Worker-Allowed"] = "/"
            self._send(HTTPStatus.OK, body, content_type, extra)

        def _nova(self, method: str, path: str) -> None:
            """The full desktop page for a paired phone; strangers get the pairing screen."""
            if method != "GET":
                self._json(HTTPStatus.METHOD_NOT_ALLOWED, {"ok": False, "error": "Yöntem desteklenmiyor."})
                return
            if self._session() is None:
                self._send(HTTPStatus.FOUND, b"", "text/plain; charset=utf-8", {"Location": "/?expired=1", "Cache-Control": "no-store"})
                return
            relative = path[len("/nova"):].lstrip("/")
            if relative in ("", "index.html"):
                body = server.nova_index()
                if body is None:
                    self._send(HTTPStatus.NOT_FOUND, "Nova sayfası bulunamadı.".encode("utf-8"), "text/plain; charset=utf-8")
                    return
                self._send(HTTPStatus.OK, body, "text/html; charset=utf-8", {"Cache-Control": "no-store"})
                return
            if relative == "js/nova-shim.js":
                candidate = server.nova_shim
            else:
                parts = PurePath(relative).parts
                if len(parts) != 2 or parts[0] not in ("css", "js") or ".." in parts or not parts[1]:
                    self._send(HTTPStatus.NOT_FOUND, b"", "text/plain; charset=utf-8")
                    return
                candidate = NOVA_WEB_ROOT / parts[0] / parts[1]
            suffix = candidate.suffix.lower()
            if suffix not in NOVA_CONTENT_TYPES or not candidate.is_file():
                self._send(HTTPStatus.NOT_FOUND, b"", "text/plain; charset=utf-8")
                return
            cache = "public, max-age=31536000, immutable" if "v=" in self.path else "no-cache"
            self._send(HTTPStatus.OK, candidate.read_bytes(), NOVA_CONTENT_TYPES[suffix], {"Cache-Control": cache})

        def _bridge(self, session: DeviceSession, name: str) -> None:
            """One NovaBridge call from the phone: the very method the desktop page calls."""
            if name in PHONE_DENIED:
                self._json(HTTPStatus.FORBIDDEN, {"ok": False, "error": PHONE_DENIED_MESSAGE})
                return
            target = server.bridge_method(name)
            if target is None:
                self._json(HTTPStatus.NOT_FOUND, {"ok": False, "error": "Bilinmeyen köprü yöntemi."})
                return
            try:
                body = self._read_json()
            except (ValueError, json.JSONDecodeError):
                self._json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": "İstek gövdesi okunamadı."})
                return
            args = body.get("args") or []
            if not isinstance(args, list) or len(args) > 8:
                self._json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": "Köprü argümanları geçersiz."})
                return
            try:
                result = target(*args)
            except TypeError:
                self._json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": "Köprü argümanları uyuşmuyor."})
                return
            except Exception as exc:
                self._json(HTTPStatus.INTERNAL_SERVER_ERROR, {"ok": False, "error": f"Köprü hatası ({type(exc).__name__})."})
                return
            if result is None:
                result = {"ok": True, "result": None}
            elif not isinstance(result, dict):
                result = {"ok": True, "result": result}
            self._json(HTTPStatus.OK, result)

        def _api(self, method: str, path: str) -> None:
            if path == "/api/health" and method == "GET":
                self._json(HTTPStatus.OK, {"ok": True, "backend": "running", "stamp": server.stamp})
                return
            if path == "/api/pair":
                self._pair(method)
                return
            session = self._session()
            if session is None:
                self._unauthorized()
                return
            if method == "POST" and not self._same_origin():
                self._json(HTTPStatus.FORBIDDEN, {"ok": False, "error": "İstek kaynağı doğrulanamadı."})
                return
            if path == "/api/events" and method == "GET":
                self._events(session)
                return
            if path == "/api/state" and method == "GET":
                self._json(HTTPStatus.OK, server.state(session))
                return
            if path == "/api/logout" and method == "POST":
                server.store.revoke(session.session_id)
                server.close_session_channels(session.session_id)
                self._json(HTTPStatus.OK, {"ok": True}, {"Set-Cookie": self._cookie_header(None, 0)})
                return
            if path == "/api/conversations" and method == "GET":
                self._json(HTTPStatus.OK, {"ok": True, "conversations": server.conversations(session)})
                return
            if path == "/api/conversations" and method == "POST":
                self._json(HTTPStatus.OK, {"ok": True, **server.new_conversation(session)})
                return
            match = re.fullmatch(r"/api/conversations/([0-9a-fA-F-]{36})/(messages|select)", path)
            if match:
                conversation_id, verb = match.group(1), match.group(2)
                try:
                    if verb == "messages" and method == "GET":
                        self._json(HTTPStatus.OK, {"ok": True, **server.messages(conversation_id)})
                    elif verb == "select" and method == "POST":
                        self._json(HTTPStatus.OK, {"ok": True, **server.select_conversation(session, conversation_id)})
                    else:
                        self._json(HTTPStatus.METHOD_NOT_ALLOWED, {"ok": False, "error": "Yöntem desteklenmiyor."})
                except KeyError:
                    self._json(HTTPStatus.NOT_FOUND, {"ok": False, "error": "Konuşma bulunamadı."})
                except ValueError as exc:
                    self._json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": str(exc) or "Konuşma kimliği geçersiz."})
                return
            if path == "/api/chat" and method == "POST":
                self._chat(session)
                return
            match = re.fullmatch(r"/api/bridge/([A-Za-z0-9_]{2,60})", path)
            if match and method == "POST":
                self._bridge(session, match.group(1))
                return
            if path == "/api/voice/transcribe" and method == "POST":
                self._voice_transcribe()
                return
            if path == "/api/voice/speak" and method == "POST":
                self._voice_speak()
                return
            match = re.fullmatch(r"/api/turns/([A-Za-z0-9_-]{8,64})", path)
            if match and method == "GET":
                record = server.turn(session.session_id, match.group(1))
                if record is None:
                    self._json(HTTPStatus.NOT_FOUND, {"ok": False, "error": "Bu gönderim bulunamadı."})
                else:
                    self._json(HTTPStatus.OK, {"ok": True, "turn": record.to_dict()})
                return
            if path == "/api/tasks" and method == "GET":
                self._json(HTTPStatus.OK, {"ok": True, "tasks": server.tasks()})
                return
            match = re.fullmatch(r"/api/tasks/([0-9a-fA-F-]{36})/(cancel|pause|resume)", path)
            if match and method == "POST":
                result = server.task_action(match.group(1), match.group(2))
                self._json(HTTPStatus.OK if result.get("ok") else HTTPStatus.CONFLICT, result)
                return
            match = re.fullmatch(r"/api/approvals/([0-9a-fA-F-]{36})", path)
            if match and method == "POST":
                body = self._read_json()
                result = server.decide_approval(match.group(1), body.get("approved") is True)
                self._json(HTTPStatus.OK if result.get("ok") else HTTPStatus.CONFLICT, result)
                return
            self._json(HTTPStatus.NOT_FOUND, {"ok": False, "error": "Bulunamadı."})

        # ---------------------------------------------------------- pairing
        def _pair(self, method: str) -> None:
            if method != "POST":
                self._json(HTTPStatus.METHOD_NOT_ALLOWED, {"ok": False, "error": "Yöntem desteklenmiyor."})
                return
            if not self._same_origin():
                self._json(HTTPStatus.FORBIDDEN, {"ok": False, "error": "İstek kaynağı doğrulanamadı."})
                return
            key = self._client_key()
            if not server._pair_limiter.allow(key) or not server._pair_global.allow("*"):
                self._json(HTTPStatus.TOO_MANY_REQUESTS, {"ok": False, "error": "Çok fazla deneme; bir dakika sonra tekrar dene."})
                return
            try:
                body = self._read_json()
            except (ValueError, json.JSONDecodeError):
                self._json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": "İstek gövdesi okunamadı."})
                return
            label = str(body.get("label") or "Telefon")[:80]
            try:
                token, session = server.store.redeem_pairing_code(
                    str(body.get("code") or ""), label=label, user_agent=self.headers.get("User-Agent", "")
                )
            except PairingError as exc:
                self._json(HTTPStatus.UNAUTHORIZED, {"ok": False, "error": str(exc)})
                return
            server._pair_limiter.reset(key)
            max_age = max(60, int((session.expires_at - session.created_at).total_seconds()))
            self._json(
                HTTPStatus.OK,
                {"ok": True, "session": {"session_id": session.session_id, "label": session.label, "expires_at": session.expires_at.isoformat()}},
                {"Set-Cookie": self._cookie_header(token, max_age)},
            )

        # ------------------------------------------------------------- chat
        def _chat(self, session: DeviceSession) -> None:
            try:
                body = self._read_json()
            except (ValueError, json.JSONDecodeError):
                self._json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": "İstek gövdesi okunamadı."})
                return
            text = str(body.get("text") or "").strip()
            client_id = str(body.get("client_id") or "")
            conversation_id = body.get("conversation_id")
            if not text:
                self._json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": "Mesaj boş olamaz."})
                return
            if len(text) > MAX_MESSAGE_CHARS:
                self._json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": "Mesaj çok uzun."})
                return
            if not CLIENT_ID_PATTERN.match(client_id):
                self._json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": "Gönderim kimliği geçersiz."})
                return
            try:
                record, duplicate = server.start_turn(
                    session, client_id, text, str(conversation_id) if conversation_id else None
                )
            except ValueError as exc:
                self._json(HTTPStatus.CONFLICT, {"ok": False, "error": str(exc)})
                return
            self._json(HTTPStatus.OK, {"ok": True, "duplicate": duplicate, "turn": record.to_dict()})

        # ------------------------------------------------------------ voice
        def _voice_transcribe(self) -> None:
            content_type = (self.headers.get("Content-Type") or "").split(";")[0].strip().lower()
            if content_type not in {"audio/wav", "audio/x-wav", "audio/wave"}:
                self._json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": "Ses kaydı audio/wav olarak gönderilmeli."})
                return
            try:
                payload = self._read_raw(MAX_VOICE_UPLOAD_BYTES)
            except OverflowError:
                self._json(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, {"ok": False, "error": "Ses kaydı çok büyük (en çok 2 MB)."})
                return
            except ValueError as exc:
                self._json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": str(exc)})
                return
            result = server.transcribe_wav(payload)
            status = int(result.pop("status", 200))
            self._json(status, result)

        def _voice_speak(self) -> None:
            try:
                body = self._read_json()
            except (ValueError, json.JSONDecodeError):
                self._json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": "İstek gövdesi okunamadı."})
                return
            outcome = server.speak(str(body.get("text") or ""))
            if isinstance(outcome, dict):
                status = int(outcome.pop("status", 502))
                self._json(status, outcome)
                return
            audio, mime, source = outcome
            self._send(HTTPStatus.OK, audio, mime, {"Cache-Control": "no-store", "X-JARVIS-Voice": source})

        # ----------------------------------------------------------- events
        def _events(self, session: DeviceSession) -> None:
            token = self._cookie_token()
            channel = server.subscribe(session.session_id)
            try:
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", "text/event-stream; charset=utf-8")
                self.send_header("Cache-Control", "no-store")
                self.send_header("X-Accel-Buffering", "no")
                self.send_header("Connection", "close")
                self.end_headers()
                self._write_event("hello", {"stamp": server.stamp, "pending_approvals": server.pending_approvals()})
                while not server._stopping:
                    try:
                        item = channel.get(timeout=EVENT_HEARTBEAT_SECONDS)
                    except queue.Empty:
                        item = ("ping", None)
                    if item is None:
                        break
                    if server.store.authenticate(token) is None:
                        self._write_event("session_ended", {"reason": "revoked"})
                        break
                    kind, payload = item
                    if kind == "ping":
                        self.wfile.write(b": ping\n\n")
                        self.wfile.flush()
                        continue
                    self._write_event(kind, payload)
            except (BrokenPipeError, ConnectionResetError, OSError):
                return
            finally:
                server.unsubscribe(session.session_id, channel)

        def _write_event(self, kind: str, payload: Any) -> None:
            data = _dumps(payload).decode("utf-8").replace("\n", " ")
            self.wfile.write(f"event: {kind}\ndata: {data}\n\n".encode("utf-8"))
            self.wfile.flush()

    return Handler


__all__ = ["MobileServer", "TurnRecord", "COOKIE_NAME", "CLIENT_HEADER"]
