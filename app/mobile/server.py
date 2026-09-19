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
import json
import platform
import queue
import re
import threading
import time
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
MAX_BODY_BYTES = 64 * 1024
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
CLIENT_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{8,64}$")
CANCELLABLE_STATUSES = {"queued", "running", "paused", "waiting_for_input", "waiting_for_approval"}
PAUSED_MESSAGE = "JARVIS duraklatıldı; masaüstünden sürdürülene kadar komut almıyor."


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
    ) -> None:
        self.controller = controller
        self.bridge = bridge
        self.store = store
        self.host = host
        self.port = int(port)
        self.web_root = Path(web_root)
        self.tokens_css = Path(tokens_css)
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None
        self._lock = threading.RLock()
        self._stopping = False
        self._streams: dict[str, set[queue.Queue[Any]]] = {}
        self._turns: dict[str, dict[str, TurnRecord]] = {}
        self._running: dict[str, str] = {}  # session_id -> client_id of the running turn
        self._pending_approvals: dict[str, dict[str, Any]] = {}
        self._pair_limiter = RateLimiter(PAIRING_ATTEMPTS_PER_MINUTE, 60.0)
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
        return {
            "ok": succeeded,
            "message": str(getattr(result, "message", "") or ""),
            "verified": bool(getattr(result, "verified", False)),
            "error": None if succeeded else str(getattr(result, "message", "") or "İşlem başarısız."),
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
