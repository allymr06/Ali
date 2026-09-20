"""The mobile companion over real HTTP against a booted desktop core.

Every test drives the loopback server the way the phone does - cookies,
the page header, server-sent events - so what passes here is what the
PWA experiences. The core is the same mock-provider application the
desktop bridge tests use, so the conversation store, the task service
and the permission approvals are the real ones.
"""

from __future__ import annotations

import http.client
import json
import socket
import time
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest

from app.mobile.server import CLIENT_HEADER, WEB_ROOT, MobileServer
from app.mobile.sessions import (
    MobileSessionStore,
    PairingError,
    RateLimiter,
    format_pairing_code,
    normalize_pairing_code,
)
from app.ui.controller import DesktopController
from app.ui.nova import shell
from tests.test_ui_nova import FakeWindow, application, approval_request, settings_service


@pytest.fixture
def mobile(tmp_path):
    app = application()
    controller = DesktopController(app)
    service, _store = settings_service(tmp_path)
    bridge = shell.NovaBridge(controller, service)
    window = FakeWindow()
    bridge._attach(window)
    bridge.boot()
    store = MobileSessionStore(tmp_path / "mobile.sqlite3", pairing_ttl_seconds=60, session_days=2)
    server = MobileServer(controller, bridge, store, port=0)
    server.start()
    bridge._mobile = server
    yield SimpleNamespace(app=app, controller=controller, bridge=bridge, window=window, store=store, server=server, port=server.bound_port)
    server.stop()
    bridge._shutdown()
    controller.close()


class Client:
    """A phone: keeps the cookie, sends the page header, speaks JSON."""

    def __init__(self, port: int) -> None:
        self.port = port
        self.cookie: str | None = None

    def request(self, method: str, path: str, body=None, *, headers=None, keep_header: bool = True):
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=15)
        sent = {CLIENT_HEADER: "pwa"} if keep_header else {}
        if self.cookie:
            sent["Cookie"] = self.cookie
        if headers:
            sent.update(headers)
        data = None
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            sent["Content-Type"] = "application/json"
        connection.request(method, path, body=data, headers=sent)
        response = connection.getresponse()
        raw = response.read()
        cookie = response.getheader("Set-Cookie")
        if cookie:
            pair = cookie.split(";", 1)[0]
            self.cookie = pair if pair.split("=", 1)[1] else None
        connection.close()
        content_type = response.getheader("Content-Type") or ""
        payload = json.loads(raw) if raw and "json" in content_type else raw
        return response.status, payload

    def pair(self, code: str, label: str = "Test telefonu"):
        return self.request("POST", "/api/pair", {"code": code, "label": label})

    def events(self):
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=15)
        connection.request("GET", "/api/events", headers={"Cookie": self.cookie or "", CLIENT_HEADER: "pwa"})
        response = connection.getresponse()
        return connection, response


def next_event(response, timeout: float = 10.0, want=None):
    deadline = time.time() + timeout
    kind = None
    while time.time() < deadline:
        line = response.readline()
        if not line:
            break
        text = line.decode("utf-8").rstrip("\r\n")
        if text.startswith("event: "):
            kind = text[7:]
        elif text.startswith("data: ") and kind:
            # Desktop pushes mirrored as "push" events may interleave with
            # the lite channel's own kinds; a caller can wait for specific ones.
            if want is None or kind in want:
                return kind, json.loads(text[6:])
            kind = None
        elif not text:
            kind = None
    raise TimeoutError("no event arrived")


def wait_for(predicate, timeout: float = 10.0, interval: float = 0.05):
    deadline = time.time() + timeout
    while time.time() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(interval)
    raise TimeoutError("condition not met")


def paired(mobile) -> Client:
    code, _expires = mobile.store.create_pairing_code(label="test")
    client = Client(mobile.port)
    status, payload = client.pair(code)
    assert status == 200 and payload["ok"] is True, payload
    assert client.cookie and client.cookie.startswith("jarvis_mobile=")
    return client


# ---------------------------------------------------------------------------
# access control
# ---------------------------------------------------------------------------


def test_unauthenticated_access_is_rejected_everywhere(mobile) -> None:
    client = Client(mobile.port)
    for method, path in (("GET", "/api/state"), ("GET", "/api/events"), ("GET", "/api/conversations"), ("GET", "/api/tasks")):
        status, payload = client.request(method, path)
        assert status == 401, (path, status)
        assert payload["ok"] is False and "eşleştir" in payload["error"]
    status, payload = client.request("POST", "/api/chat", {"text": "merhaba", "client_id": "abcdefgh-1"})
    assert status == 401
    status, payload = client.request("POST", "/api/approvals/" + str(uuid4()), {"approved": True})
    assert status == 401, "an approval without a session is never counted"
    status, payload = client.request("GET", "/api/health")
    assert status == 200 and payload == {"ok": True, "backend": "running", "stamp": mobile.server.stamp}
    status, body = client.request("GET", "/")
    assert status == 200 and b"Telefonu ba" in body, "the shell itself is public; it only shows the pairing screen"


def test_pairing_codes_are_single_use_and_wrong_codes_fail(mobile) -> None:
    code, expires = mobile.store.create_pairing_code(label="test")
    assert len(code) == 8 and format_pairing_code(code) == f"{code[:4]}-{code[4:]}"
    assert expires > datetime.now(timezone.utc)
    client = Client(mobile.port)

    status, payload = client.pair("ZZZZ-ZZZZ")
    assert status == 401 and "geçersiz" in payload["error"] and client.cookie is None

    status, payload = client.pair(format_pairing_code(code).lower())
    assert status == 200 and payload["session"]["label"] == "Test telefonu"
    assert "Secure" not in str(client.cookie), "loopback over plain http gets a non-Secure cookie for local testing"

    again = Client(mobile.port)
    status, payload = again.pair(code)
    assert status == 401 and "daha önce kullanıldı" in payload["error"]
    assert again.cookie is None

    with pytest.raises(PairingError, match="8 karakter"):
        mobile.store.redeem_pairing_code("ABC", label="x")


def test_pairing_codes_expire_and_sessions_expire(tmp_path) -> None:
    now = datetime(2026, 9, 17, 12, 0, tzinfo=timezone.utc)
    early = MobileSessionStore(tmp_path / "s.sqlite3", pairing_ttl_seconds=60, session_days=1, clock=lambda: now)
    code, _expires = early.create_pairing_code()
    late = MobileSessionStore(tmp_path / "s.sqlite3", pairing_ttl_seconds=60, session_days=1, clock=lambda: now + timedelta(minutes=2))
    with pytest.raises(PairingError, match="süresi doldu"):
        late.redeem_pairing_code(code, label="phone")

    fresh, _expires = early.create_pairing_code()
    token, session = early.redeem_pairing_code(fresh, label="phone")
    assert early.authenticate(token) is not None
    expired = MobileSessionStore(tmp_path / "s.sqlite3", pairing_ttl_seconds=60, session_days=1, clock=lambda: now + timedelta(days=2))
    assert expired.authenticate(token) is None, "a session past its lifetime is dead on the next request"
    assert early.authenticate("not-a-token") is None
    assert normalize_pairing_code(" ab0c-d1ef ") == "ABOCDIEF"


def test_pairing_attempts_are_rate_limited(mobile) -> None:
    client = Client(mobile.port)
    statuses = [client.pair("AAAA-AAAA")[0] for _ in range(6)]
    assert statuses[:5] == [401] * 5 and statuses[5] == 429
    limiter = RateLimiter(2, 60.0, clock=lambda: 100.0)
    assert limiter.allow("k") and limiter.allow("k") and not limiter.allow("k")


def test_mutations_require_the_page_header_and_a_matching_origin(mobile) -> None:
    client = paired(mobile)
    status, payload = client.request("POST", "/api/conversations", {}, keep_header=False)
    assert status == 403 and "kaynağı" in payload["error"]
    status, _ = client.request("POST", "/api/conversations", {}, headers={"Origin": "https://evil.example"})
    assert status == 403
    status, _ = client.request("POST", "/api/conversations", {}, headers={"Sec-Fetch-Site": "cross-site"})
    assert status == 403
    status, payload = client.request("POST", "/api/conversations", {}, headers={"Origin": f"http://127.0.0.1:{mobile.port}", "Sec-Fetch-Site": "same-origin"})
    assert status == 200 and payload["ok"] is True
    # Behind Tailscale Serve the Host header may be rewritten; the forwarded host still matches.
    status, _ = client.request("POST", "/api/conversations", {}, headers={"Origin": "https://pc.tail.ts.net", "X-Forwarded-Host": "pc.tail.ts.net"})
    assert status == 200


def test_a_refused_post_leaves_the_connection_in_step(mobile) -> None:
    """Keep-alive is on, so a refusal still has to swallow the body it refused.

    Whatever a POST declared and no handler read stays in the socket, and
    the next request on that connection gets parsed out of the leftovers -
    the phone's following tap answers with someone else's garbage.
    """
    client = paired(mobile)
    body = json.dumps({"text": "x" * 4000, "client_id": "keepalive-01"}).encode("utf-8")
    page = {CLIENT_HEADER: "pwa", "Cookie": client.cookie or ""}
    refusals = (
        ("/api/chat", {}, 401),                        # no session
        ("/api/chat", {"Cookie": client.cookie}, 403),  # a session, but not the page
        ("/api/bridge/save_settings", page, 403),       # a bridge method the phone may not call
        ("/api/nope", page, 404),                       # no such route
        ("/nova/", page, 405),                          # not a POST surface at all
    )
    for path, headers, expected in refusals:
        connection = http.client.HTTPConnection("127.0.0.1", mobile.port, timeout=10)
        try:
            connection.request("POST", path, body=body, headers={"Content-Type": "application/json", **headers})
            refused = connection.getresponse()
            refused.read()
            assert refused.status == expected, (path, refused.status)
            connection.request("GET", "/api/health", headers={CLIENT_HEADER: "pwa"})
            following = connection.getresponse()
            payload = following.read()
            assert following.status == 200, (path, following.status, payload[:120])
            assert json.loads(payload)["ok"] is True, path
        finally:
            connection.close()

    # A GET may declare a body too, and one no handler read is left in the
    # socket exactly as a POST's is - the method never made it safe.
    connection = http.client.HTTPConnection("127.0.0.1", mobile.port, timeout=10)
    try:
        connection.request("GET", "/api/state", body=body, headers={"Content-Type": "application/json", **page})
        answered = connection.getresponse()
        answered.read()
        assert answered.status == 200
        connection.request("GET", "/api/health", headers={CLIENT_HEADER: "pwa"})
        following = connection.getresponse()
        payload = following.read()
        assert following.status == 200, (following.status, payload[:160])
        assert json.loads(payload)["ok"] is True
    finally:
        connection.close()

    # A body whose end is only marked in the stream cannot be drained on the
    # way out, so the connection is closed rather than handed on unparsed.
    connection = http.client.HTTPConnection("127.0.0.1", mobile.port, timeout=10)
    try:
        connection.request(
            "POST", "/api/bridge/save_settings", body=iter([body]),
            headers={**page, "Content-Type": "application/json"}, encode_chunked=True,
        )
        refused = connection.getresponse()
        refused.read()
        assert refused.status == 403 and refused.getheader("Connection") == "close"
    finally:
        connection.close()


def test_a_refusal_arrives_before_the_body_it_refuses(mobile) -> None:
    """The headers alone earned the 401, so the 401 need not wait on bytes.

    A phone that declares half a megabyte and then stalls - a tunnel that
    dropped mid-upload - must still be told at once that it has no
    session. Draining first means the answer is hostage to a body that
    may never arrive, and with no deadline on the socket the thread waits
    with it.
    """
    assert mobile.server._server.RequestHandlerClass.timeout, "a request with no deadline can park a thread forever"

    sock = socket.create_connection(("127.0.0.1", mobile.port), timeout=5)
    try:
        sock.sendall(
            b"POST /api/chat HTTP/1.1\r\nHost: 127.0.0.1\r\n"
            + f"{CLIENT_HEADER}: pwa\r\n".encode("ascii")
            + b"Content-Type: application/json\r\nContent-Length: 500000\r\n\r\n{"
        )
        sock.settimeout(3.0)
        answer = sock.recv(4096)  # socket.timeout here is the failure this pins
    finally:
        sock.close()
    assert answer.startswith(b"HTTP/1.1 401"), answer[:160]


def test_logout_and_desktop_revocation_end_the_session_and_its_channel(mobile) -> None:
    client = paired(mobile)
    connection, stream = client.events()
    assert stream.status == 200
    kind, hello = next_event(stream)
    assert kind == "hello" and hello["stamp"] == mobile.server.stamp

    status = mobile.bridge.mobile_status()
    assert status["ok"] and status["running"] and [row["label"] for row in status["sessions"]] == ["Test telefonu"]
    session_id = status["sessions"][0]["session_id"]

    assert mobile.bridge.mobile_revoke_session(session_id) == {"ok": False, "error": "İptal işlemi onaylanmadı."}
    revoked = mobile.bridge.mobile_revoke_session(session_id, True)
    assert revoked["ok"] is True and revoked["sessions"] == []
    kind, payload = next_event(stream, want={"session_ended"})
    assert kind == "session_ended" and payload["reason"] == "revoked"
    connection.close()
    status_code, payload = client.request("GET", "/api/state")
    assert status_code == 401, "the very next request after revocation is refused"

    other = paired(mobile)
    status_code, payload = other.request("POST", "/api/logout", {})
    assert status_code == 200 and other.cookie is None
    status_code, _ = other.request("GET", "/api/state", headers={"Cookie": "jarvis_mobile=stale"})
    assert status_code == 401


# ---------------------------------------------------------------------------
# chat through the shared pipeline
# ---------------------------------------------------------------------------


def test_a_message_reaches_the_shared_conversation_pipeline(mobile) -> None:
    client = paired(mobile)
    status, state = client.request("GET", "/api/state")
    assert status == 200 and state["conversation"] is None and state["pc"]

    status, payload = client.request("POST", "/api/chat", {"text": "Merhaba JARVIS, telefondan yazıyorum.", "client_id": "turn-0001-a"})
    assert status == 200 and payload["ok"] is True and payload["duplicate"] is False
    conversation_id = payload["turn"]["conversation_id"]

    record = wait_for(lambda: (lambda r: r if r and r["turn"]["status"] != "running" else None)(client.request("GET", "/api/turns/turn-0001-a")[1]))
    assert record["turn"]["status"] == "done" and record["turn"]["reply"]

    # The desktop sees the very same record, and its own state was never touched.
    conversation = mobile.app.conversation_engine.get(UUID(conversation_id))
    assert [turn.role.value for turn in conversation.turns] == ["user", "assistant"]
    assert conversation.turns[0].content == "Merhaba JARVIS, telefondan yazıyorum."
    assert conversation.turns[0].metadata.get("source") == "api"
    listed = mobile.controller.list_conversations()
    assert any(row["conversation_id"] == conversation_id and row["turn_count"] == 2 for row in listed)
    assert mobile.controller.state.messages == [] and mobile.controller.state.busy is False

    status, messages = client.request("GET", f"/api/conversations/{conversation_id}/messages")
    assert status == 200 and [m["role"] for m in messages["messages"]] == ["user", "assistant"]
    status, listing = client.request("GET", "/api/conversations")
    assert any(row["conversation_id"] == conversation_id and row["selected"] for row in listing["conversations"])


def test_retrying_the_same_client_id_never_runs_the_command_twice(mobile) -> None:
    client = paired(mobile)
    first = client.request("POST", "/api/chat", {"text": "Tekrar gönderim testi", "client_id": "retry-000001"})[1]
    wait_for(lambda: client.request("GET", "/api/turns/retry-000001")[1]["turn"]["status"] != "running")
    second = client.request("POST", "/api/chat", {"text": "Tekrar gönderim testi", "client_id": "retry-000001"})[1]
    assert second["duplicate"] is True and second["turn"]["client_id"] == "retry-000001"
    conversation = mobile.app.conversation_engine.get(UUID(first["turn"]["conversation_id"]))
    assert len(conversation.turns) == 2, "one user turn, one reply - the retry ran nothing"
    status, payload = client.request("GET", "/api/turns/never-sent-0001")
    assert status == 404, "a message the PC never received says so, and only an explicit resend repeats it"


def test_streaming_events_carry_the_turn_lifecycle(mobile) -> None:
    client = paired(mobile)
    connection, stream = client.events()
    assert next_event(stream)[0] == "hello"
    client.request("POST", "/api/chat", {"text": "Akış testi", "client_id": "stream-00001"})
    kinds = []
    while len(kinds) < 2:
        kind, payload = next_event(stream)
        if kind in {"turn_started", "turn_done"}:
            kinds.append(kind)
            assert payload["client_id"] == "stream-00001"
    assert kinds == ["turn_started", "turn_done"]
    connection.close()


def test_chat_input_is_validated_and_a_running_turn_blocks_a_second(mobile) -> None:
    client = paired(mobile)
    assert client.request("POST", "/api/chat", {"text": "", "client_id": "abcdefgh-1"})[0] == 400
    assert client.request("POST", "/api/chat", {"text": "x", "client_id": "bad id"})[0] == 400
    assert client.request("POST", "/api/chat", {"text": "x" * 9000, "client_id": "abcdefgh-1"})[0] == 400
    mobile.controller.set_paused(True)
    status, payload = client.request("POST", "/api/chat", {"text": "merhaba", "client_id": "abcdefgh-2"})
    assert status == 409 and "duraklatıldı" in payload["error"]
    mobile.controller.set_paused(False)


def test_a_message_into_an_archived_thread_is_refused_not_replaced(mobile) -> None:
    """The phone is told, never quietly handed a different conversation.

    The phone still shows the archived thread as the open one; filing the
    message in a fresh conversation would leave the user writing where
    nobody is reading and the old thread looking untouched.
    """
    client = paired(mobile)
    status, payload = client.request("POST", "/api/chat", {"text": "Arşivden önce", "client_id": "archive-0001"})
    assert status == 200, payload
    conversation_id = payload["turn"]["conversation_id"]
    wait_for(lambda: client.request("GET", "/api/turns/archive-0001")[1]["turn"]["status"] != "running")
    before = len(mobile.controller.list_conversations())

    mobile.controller.archive_conversation(conversation_id)

    status, payload = client.request("POST", "/api/chat", {"text": "Arşivden sonra", "client_id": "archive-0002"})
    assert status == 409 and "Arşivdeki" in payload["error"] and "masaüstünden" in payload["error"]
    assert client.request("GET", "/api/turns/archive-0002")[0] == 404, "nothing was started anywhere"
    assert len(mobile.controller.list_conversations()) == before, "no stand-in conversation was invented"
    assert len(mobile.app.conversation_engine.get(UUID(conversation_id)).turns) == 2, "the thread is as it was"

    # The desktop is what reopens it - and then the phone writes into the same thread.
    mobile.controller.unarchive_conversation(conversation_id)
    status, payload = client.request("POST", "/api/chat", {"text": "Arşivden çıkınca", "client_id": "archive-0003"})
    assert status == 200 and payload["turn"]["conversation_id"] == conversation_id
    wait_for(lambda: client.request("GET", "/api/turns/archive-0003")[1]["turn"]["status"] != "running")


def test_both_archived_entry_points_refuse_with_the_same_code(mobile) -> None:
    """Tapping the thread and writing into it are one refusal, not two.

    A malformed conversation id reaches the same route and is a different
    kind of wrong - the request itself is unusable - so it stays a 400.
    """
    client = paired(mobile)
    status, payload = client.request("POST", "/api/chat", {"text": "Seçimden önce", "client_id": "select-0001"})
    assert status == 200, payload
    conversation_id = payload["turn"]["conversation_id"]
    wait_for(lambda: client.request("GET", "/api/turns/select-0001")[1]["turn"]["status"] != "running")

    mobile.controller.archive_conversation(conversation_id)

    tapped, on_tap = client.request("POST", f"/api/conversations/{conversation_id}/select", {})
    written, on_write = client.request("POST", "/api/chat", {"text": "Seçimden sonra", "client_id": "select-0002"})
    assert tapped == written == 409, (tapped, written)
    assert on_tap["error"] == on_write["error"] and "Arşivdeki" in on_tap["error"]

    status, payload = client.request("POST", "/api/conversations/" + "-" * 36 + "/select", {})
    assert status == 400, "a malformed id is the request's fault, not the thread's"


def test_the_phone_learns_a_thread_is_archived_before_it_writes(mobile) -> None:
    """State carries the status, so the composer can lock instead of guess.

    Without it the phone adopts the conversation, leaves the composer
    live, and the user only finds out when the refusal turns the message
    they already sent into a system line.
    """
    client = paired(mobile)
    status, payload = client.request("POST", "/api/chat", {"text": "Durumdan önce", "client_id": "status-0001"})
    assert status == 200, payload
    conversation_id = payload["turn"]["conversation_id"]
    wait_for(lambda: client.request("GET", "/api/turns/status-0001")[1]["turn"]["status"] != "running")
    assert client.request("GET", "/api/state")[1]["conversation"]["status"] == "active"

    mobile.controller.archive_conversation(conversation_id)

    assert client.request("GET", "/api/state")[1]["conversation"]["status"] == "archived"
    assert client.request("GET", f"/api/conversations/{conversation_id}/messages")[1]["status"] == "archived"
    assert client.request("POST", "/api/conversations", {})[1]["status"] == "active", "a fresh thread is writable"


def test_the_channel_carries_the_status_of_the_thread_on_screen(mobile) -> None:
    """Archiving on the desktop reaches the phone while it is sitting there.

    The page reads a status only when it (re)connects, so a status the
    channel never carries leaves the composer live on a thread the PC has
    already closed - the exact case this whole lock exists for.
    """
    client = paired(mobile)
    status, payload = client.request("POST", "/api/chat", {"text": "Kanaldan önce", "client_id": "live-0001"})
    assert status == 200, payload
    conversation_id = payload["turn"]["conversation_id"]
    wait_for(lambda: client.request("GET", "/api/turns/live-0001")[1]["turn"]["status"] != "running")

    connection, stream = client.events()
    try:
        assert next_event(stream, want={"hello"})[0] == "hello"
        mobile.controller.archive_conversation(conversation_id)
        kind, data = next_event(stream, want={"conversation_status"})
        assert (kind, data) == ("conversation_status", {"conversation_id": conversation_id, "status": "archived"})

        mobile.controller.unarchive_conversation(conversation_id)
        assert next_event(stream, want={"conversation_status"})[1] == {
            "conversation_id": conversation_id,
            "status": "active",
        }
    finally:
        connection.close()


def test_reading_a_thread_costs_one_store_read(mobile) -> None:
    """The phone's hottest route reads the conversation once, not twice.

    /api/state asks for the same thread every time it runs, and behind
    the engine is a database file whose read builds the whole
    conversation: a second one per answer grows with the transcript for
    nothing.
    """
    client = paired(mobile)
    status, payload = client.request("POST", "/api/chat", {"text": "Okuma sayımı", "client_id": "reads-0001"})
    assert status == 200, payload
    conversation_id = payload["turn"]["conversation_id"]
    wait_for(lambda: client.request("GET", "/api/turns/reads-0001")[1]["turn"]["status"] != "running")
    session = mobile.store.list_sessions()[0]
    assert session.conversation_id == conversation_id

    store = mobile.app.conversation_engine.store
    reads: list[str] = []
    original = store.get

    def counted(identifier):
        reads.append(str(identifier))
        return original(identifier)

    store.get = counted
    try:
        mobile.server.messages(conversation_id)
        assert reads == [conversation_id], reads
        reads.clear()
        mobile.server.state(session)
        assert reads == [conversation_id], reads
    finally:
        store.get = original


# ---------------------------------------------------------------------------
# approvals and tasks
# ---------------------------------------------------------------------------


def test_approvals_are_bound_to_the_pending_action_and_never_repeat(mobile) -> None:
    client = paired(mobile)
    connection, stream = client.events()
    assert next_event(stream)[0] == "hello"

    future = mobile.controller.submit_background(mobile.bridge._request_approval(approval_request(seconds=30)), lambda _f: None)
    kind, payload = next_event(stream, want={"approval"})
    assert kind == "approval" and payload["tool"] == "fs.write" and payload["parameters"]["api_key"] == "<gizli>"
    token = payload["token"]
    assert [item["token"] for item in mobile.server.pending_approvals()] == [token]

    stranger = Client(mobile.port)
    assert stranger.request("POST", f"/api/approvals/{token}", {"approved": True})[0] == 401

    status, decided = client.request("POST", f"/api/approvals/{token}", {"approved": True})
    assert status == 200 and decided["ok"] is True
    assert future.result(timeout=5) is True
    kind, closed = next_event(stream, want={"approval_closed"})
    assert kind == "approval_closed" and closed["token"] == token

    status, repeated = client.request("POST", f"/api/approvals/{token}", {"approved": True})
    assert status == 409 and "geçerli değil" in repeated["error"]
    status, unknown = client.request("POST", f"/api/approvals/{uuid4()}", {"approved": True})
    assert status == 409
    connection.close()

    # A denial is a decision too, and a request nobody answers fails closed.
    denied = mobile.controller.submit_background(mobile.bridge._request_approval(approval_request(seconds=30)), lambda _f: None)
    token = wait_for(lambda: (mobile.server.pending_approvals() or [{}])[0].get("token"))
    assert client.request("POST", f"/api/approvals/{token}", {"approved": False})[1]["ok"] is True
    assert denied.result(timeout=5) is False


def test_tasks_are_listed_and_unsupported_actions_are_refused_honestly(mobile) -> None:
    client = paired(mobile)
    status, payload = client.request("GET", "/api/tasks")
    assert status == 200 and payload["ok"] is True and isinstance(payload["tasks"], list)
    status, payload = client.request("POST", f"/api/tasks/{uuid4()}/cancel", {})
    assert status == 409 and payload["ok"] is False and payload["error"]
    status, payload = client.request("POST", f"/api/tasks/{uuid4()}/explode", {})
    assert status == 404


# ---------------------------------------------------------------------------
# desktop control and static shell
# ---------------------------------------------------------------------------


def test_the_desktop_card_mints_codes_that_pair_and_revokes_everyone(mobile) -> None:
    minted = mobile.bridge.mobile_pairing_code()
    assert minted["ok"] is True and len(minted["code"]) == 9 and minted["code"][4] == "-"
    assert mobile.bridge.mobile_status()["pending_code"] is not None
    client = Client(mobile.port)
    assert client.pair(minted["code"])[0] == 200
    assert mobile.bridge.mobile_status()["pending_code"] is None, "a redeemed code is no longer pending"
    assert mobile.bridge.mobile_revoke_all() == {"ok": False, "error": "İptal işlemi onaylanmadı."}
    result = mobile.bridge.mobile_revoke_all(True)
    assert result["ok"] is True and result["revoked"] == 1
    assert client.request("GET", "/api/state")[0] == 401
    pushed = json.dumps(mobile.window.scripts)
    assert minted["code"] not in pushed and minted["code"].replace("-", "") not in pushed, "the code never travels to the page as a push"


def test_static_shell_is_served_versioned_without_leaking_anything(mobile) -> None:
    client = Client(mobile.port)
    status, body = client.request("GET", "/")
    assert status == 200 and b"__STAMP__" not in body and mobile.server.stamp.encode() in body
    status, body = client.request("GET", "/sw.js")
    assert status == 200 and (b'"' + mobile.server.stamp.encode() + b'"') in body and b"jarvis-mobile-" in body and b"/api/" in body
    status, manifest = client.request("GET", "/manifest.webmanifest")
    assert status == 200 and manifest["display"] == "standalone" and len(manifest["icons"]) == 3
    status, body = client.request("GET", "/icons/icon-192.png")
    assert status == 200 and body[:8] == b"\x89PNG\r\n\x1a\n"
    assert client.request("GET", "/tokens.css")[0] == 200
    assert client.request("GET", "/offline.html")[0] == 200
    assert client.request("GET", "/../app/config/settings.py")[0] == 404
    assert client.request("GET", "/api/nope")[0] == 401, "unknown API paths still need a session"


# ---------------------------------------------------------------------------
# the offline shell, executed in QuickJS
# ---------------------------------------------------------------------------


# A service worker's world, small enough to reason about: a cache keyed by
# string, a fetch that can be switched offline or made to answer with a
# redirect, and a driver that fires one navigation.
SW_ENVIRONMENT = r"""
var CACHES = {};
var HANDLERS = {};
var self = {
  location: { origin: "https://pc.tail.ts.net" },
  addEventListener: function (kind, fn) { HANDLERS[kind] = fn; },
  skipWaiting: function () { return Promise.resolve(true); },
  clients: { claim: function () { return Promise.resolve(true); } }
};
function URL(href) {
  var parts = /^([a-z]+:)\/\/([^\/?#]*)([^?#]*)(\?[^#]*)?/.exec(href);
  this.origin = parts[1] + "//" + parts[2];
  this.pathname = parts[3] || "/";
  this.search = parts[4] || "";
}
function Response(body, init) {
  this.body = body;
  this.status = (init && init.status) || 200;
  this.ok = this.status >= 200 && this.status < 300;
}
Response.prototype.clone = function () { return new Response(this.body, { status: this.status }); };
var caches = {
  open: function (name) {
    if (!CACHES[name]) { CACHES[name] = {}; }
    var store = CACHES[name];
    return Promise.resolve({
      addAll: function (urls) { urls.forEach(function (u) { store[u] = "shell:" + u; }); return Promise.resolve(true); },
      put: function (key, response) { store[typeof key === "string" ? key : key.url] = response.body; return Promise.resolve(true); }
    });
  },
  keys: function () { return Promise.resolve(Object.keys(CACHES)); },
  delete: function (name) { delete CACHES[name]; return Promise.resolve(true); },
  match: function (key) {
    var wanted = typeof key === "string" ? key : key.url;
    var names = Object.keys(CACHES);
    for (var i = 0; i < names.length; i += 1) {
      if (Object.prototype.hasOwnProperty.call(CACHES[names[i]], wanted)) {
        return Promise.resolve(new Response(CACHES[names[i]][wanted]));
      }
    }
    return Promise.resolve(undefined);
  }
};
var OFFLINE = false;
var STATUS = 200;
function fetch(request) {
  if (OFFLINE) { return Promise.reject(new Error("offline")); }
  return Promise.resolve(new Response("live:" + request.url, { status: STATUS }));
}
var SERVED = {};
function install() { HANDLERS.install({ waitUntil: function (_kept) {} }); }
function navigate(name, path) {
  var served = null;
  HANDLERS.fetch({
    request: { url: "https://pc.tail.ts.net" + path, mode: "navigate", method: "GET" },
    respondWith: function (promise) { served = promise; }
  });
  served.then(
    function (response) { SERVED[name] = response ? response.body : null; },
    function () { SERVED[name] = "REJECTED"; }
  );
}
function dump() { return JSON.stringify({ caches: CACHES, served: SERVED }); }
"""


def service_worker(stamp: str = "teststamp"):
    """The real sw.js in QuickJS; each call runs a step and returns the world."""
    quickjs = pytest.importorskip("quickjs")
    context = quickjs.Context()
    context.eval(SW_ENVIRONMENT)
    context.eval((WEB_ROOT / "sw.js").read_text(encoding="utf-8").replace("__STAMP__", stamp))

    def step(script: str) -> dict:
        context.eval(script)
        for _ in range(10_000):  # promises only settle when their jobs run
            if not context.execute_pending_job():
                break
        return json.loads(context.eval("dump()"))

    return step


def test_each_navigable_page_keeps_its_own_offline_shell() -> None:
    """Two pages are navigable here, and neither may be cached as the other."""
    step = service_worker()
    step("install();")
    step("navigate('phone', '/');")
    cached = step("navigate('nova', '/nova/');")["caches"]["jarvis-mobile-teststamp"]
    assert cached["/"] == "live:https://pc.tail.ts.net/"
    assert cached["/nova/"] == "live:https://pc.tail.ts.net/nova/"
    assert cached["/index.html"] == "shell:/index.html", "the Nova page never lands on the phone shell's key"

    step("OFFLINE = true;")
    step("navigate('phone_offline', '/');")
    served = step("navigate('nova_offline', '/nova/');")["served"]
    assert served["phone_offline"] == "live:https://pc.tail.ts.net/", "the phone gets its own page back"
    assert served["nova_offline"] == "live:https://pc.tail.ts.net/nova/", "and Nova gets its own"


def test_a_redirect_is_never_kept_as_a_shell() -> None:
    """An unpaired phone is sent to the pairing screen; that answer is not a page."""
    step = service_worker()
    step("install();")
    step("STATUS = 302;")
    cached = step("navigate('nova', '/nova/');")["caches"]["jarvis-mobile-teststamp"]
    assert "/nova/" not in cached

    step("OFFLINE = true;")
    served = step("navigate('nova_offline', '/nova/');")["served"]
    assert served["nova_offline"] == "shell:/offline.html", "with nothing cached, the offline page says so"


# Enough of a browser for the page to boot: one node per selector, a fetch
# that answers from a table of payloads, and timers that never fire.
PAGE_ENVIRONMENT = r"""
var NODES = {};
function Node(name) {
  this.name = name;
  this.className = ""; this.textContent = ""; this.innerHTML = ""; this.value = "";
  this.hidden = false; this.disabled = false; this.placeholder = ""; this.dataset = {};
  this.style = {}; this.scrollHeight = 0; this.scrollTop = 0; this.clientHeight = 0; this.handlers = {};
  this.classList = { add: function () {}, remove: function () {}, toggle: function () {}, contains: function () { return false; } };
}
Node.prototype.addEventListener = function (kind, handler) {
  if (!this.handlers[kind]) { this.handlers[kind] = []; }
  this.handlers[kind].push(handler);
};
Node.prototype.appendChild = function () {};
Node.prototype.querySelector = function () { return null; };
Node.prototype.querySelectorAll = function () { return []; };
Node.prototype.scrollTo = function () {};
Node.prototype.focus = function () {};
function node(name) { if (!NODES[name]) { NODES[name] = new Node(name); } return NODES[name]; }
var document = {
  hidden: false,
  querySelector: function (selector) { return node(selector); },
  querySelectorAll: function () { return []; },
  createElement: function () { return new Node("created"); },
  addEventListener: function () {}
};
var location = { search: "?lite=1", host: "pc.tail.ts.net" };
var window = {
  innerHeight: 800,
  location: { replace: function () {} },
  matchMedia: function () { return { matches: false }; },
  addEventListener: function () {}
};
var navigator = { onLine: true, vibrate: function () {} };
var crypto = { randomUUID: function () { return "11111111-2222-3333-4444-555555555555"; } };
function setTimeout() { return 0; }
function clearTimeout() {}
function setInterval() { return 0; }
function clearInterval() {}
var CHANNEL = null;
function EventSource() {
  var self = this;
  this.listeners = {};
  this.close = function () {};
  this.addEventListener = function (kind, handler) {
    if (!self.listeners[kind]) { self.listeners[kind] = []; }
    self.listeners[kind].push(handler);
  };
  CHANNEL = this;
}
function URLSearchParams(search) {
  this.get = function (key) {
    var found = new RegExp("[?&]" + key + "=([^&]*)").exec(search || "");
    return found ? found[1] : null;
  };
}
var REPLIES = {};
function fetch(path) {
  var body = REPLIES[path.split("?")[0]];
  if (body === undefined) { body = { ok: true }; }
  return Promise.resolve({ ok: true, status: 200, json: function () { return Promise.resolve(body); } });
}
function answer(path, body) { REPLIES[path] = body; }
function composer() {
  var input = node("#composer-input");
  return JSON.stringify({ disabled: input.disabled, placeholder: input.placeholder });
}
function channel(kind, data) {
  var handlers = (CHANNEL && CHANNEL.listeners[kind]) || [];
  for (var i = 0; i < handlers.length; i += 1) { handlers[i]({ data: data }); }
  return handlers.length;
}
function press(selector, kind) {
  var handlers = node(selector).handlers[kind] || [];
  for (var i = 0; i < handlers.length; i += 1) { handlers[i]({ preventDefault: function () {}, target: node(selector) }); }
  return handlers.length;
}
function html(selector) { return node(selector).innerHTML; }
"""


def booted_phone(status: str, tasks: list | None = None):
    """The real app.js booted onto a conversation with this status.

    The page is handed back still running, so a test can push an event
    down its channel or press one of its controls and then read what the
    page made of it.
    """
    quickjs = pytest.importorskip("quickjs")
    conversation = {"conversation_id": "c-1", "title": "Sohbet", "status": status}
    state = {
        "ok": True,
        "session": {"session_id": "s", "label": "Telefon", "created_at": "", "expires_at": ""},
        "pc": "PC", "paused": False, "pending_approvals": [], "turns": [], "stamp": "x",
        "conversation": conversation,
    }
    messages = {"ok": True, **conversation, "messages": []}
    context = quickjs.Context()
    context.eval(PAGE_ENVIRONMENT)
    context.eval(f"answer('/api/state', {json.dumps(state)});")
    context.eval(f"answer('/api/conversations/c-1/messages', {json.dumps(messages)});")
    context.eval(f"answer('/api/tasks', {json.dumps({'ok': True, 'tasks': tasks or []})});")
    context.eval((WEB_ROOT / "app.js").read_text(encoding="utf-8"))
    settle(context)
    return context


def settle(page) -> None:
    """Let every promise the page is holding finish."""
    while page.execute_pending_job():
        pass


def composer(page) -> dict:
    """What the phone's composer looks like at this moment."""
    return json.loads(page.eval("composer()"))


def test_the_composer_locks_itself_on_an_archived_thread() -> None:
    """The phone warns before the send, not after the refusal.

    With the composer live the user only learns the thread is closed when
    the message they already wrote comes back as a system line.
    """
    assert composer(booted_phone("active")) == {"disabled": False, "placeholder": "JARVIS'e yaz…"}
    locked = composer(booted_phone("archived"))
    assert locked["disabled"] is True
    assert "Arşiv" in locked["placeholder"] and "masaüstünden" in locked["placeholder"]


def test_the_open_thread_locks_the_moment_the_channel_says_archived() -> None:
    """Archiving from the desktop reaches the phone that is sitting there.

    The page reads a status only when it (re)connects, so without this
    listener the user keeps a live composer on a thread the PC has
    already closed, and learns of it from the refusal of a message
    already sent.
    """
    page = booted_phone("active")
    assert composer(page)["disabled"] is False

    heard = page.eval('channel("conversation_status", JSON.stringify({conversation_id: "c-1", status: "archived"}));')
    assert heard == 1, "the page does not listen to the channel for the status of its own thread"
    settle(page)
    assert composer(page)["disabled"] is True

    page.eval('channel("conversation_status", JSON.stringify({conversation_id: "c-1", status: "active"}));')
    settle(page)
    assert composer(page)["disabled"] is False, "unarchiving hands the thread back"

    page.eval('channel("conversation_status", JSON.stringify({conversation_id: "c-2", status: "archived"}));')
    settle(page)
    assert composer(page)["disabled"] is False, "another thread's status is not this thread's"


def test_the_task_card_says_in_turkish_why_a_step_failed() -> None:
    """The phone shows the failure, and shows it in its own language.

    A task that stopped with only its status on screen tells the user
    nothing about what to do next, and the engine's English machine
    strings are not the phone's voice.
    """
    page = booted_phone(
        "active",
        tasks=[
            {
                "task_id": "t-1",
                "goal": "Raporu hazırla",
                "status": "failed",
                "error": "Plan could not be persisted safely.",
                "steps": [
                    {"step_id": "s-1", "name": "Dosyayı oku", "status": "completed", "error": None},
                    {"step_id": "s-2", "name": "Aracı çağır", "status": "failed", "error": "Invalid tool_name."},
                    {"step_id": "s-3", "name": "Kaydet", "status": "failed", "error": "Disk çöktü"},
                ],
            }
        ],
    )
    assert page.eval('press("#tasks-refresh", "click");') == 1
    settle(page)

    markup = page.eval('html("#tasks-list");')
    assert "Plan güvenle kaydedilemedi." in markup, "the task's own failure is never shown"
    assert "Adımın aracı tanımsız." in markup, "the step's failure is never shown"
    assert "Invalid tool_name." not in markup and "persisted" not in markup, "English machine strings reach the phone"
    assert "Disk çöktü" in markup, "an unmapped failure is shown as it came rather than swallowed"


# ---------------------------------------------------------------------------
# the whole desktop page on the phone
# ---------------------------------------------------------------------------


def test_the_nova_page_is_served_only_to_paired_phones_with_the_shim(mobile) -> None:
    stranger = Client(mobile.port)
    status, _ = stranger.request("GET", "/nova/")
    assert status == 302, "an unpaired phone is sent to the pairing screen"
    assert stranger.request("GET", "/nova/js/foundation.js")[0] == 302

    client = paired(mobile)
    status, body = client.request("GET", "/nova/")
    assert status == 200
    assert b'js/nova-shim.js?v=' in body and body.index(b"nova-shim.js") < body.index(b"js/foundation.js"), "the shim loads before every Nova script"
    assert b"css/phone.css" in body and b'href="/manifest.webmanifest"' in body
    assert b"Content-Security-Policy" in body, "the desktop page's own CSP travels with it"
    for path in ("/nova/js/nova-shim.js", "/nova/js/foundation.js", "/nova/css/tokens.css", "/nova/css/phone.css", "/nova/js/medical.js"):
        assert client.request("GET", path)[0] == 200, path
    assert client.request("GET", "/nova/js/../../mobile/server.py")[0] == 404
    assert client.request("GET", "/nova/css/nope.css")[0] == 404
    assert client.request("GET", "/nova/index.html/extra")[0] == 404


def test_bridge_calls_from_the_phone_reach_the_real_bridge_and_denied_ones_do_not(mobile) -> None:
    client = paired(mobile)
    stranger = Client(mobile.port)
    assert stranger.request("POST", "/api/bridge/list_conversations", {"args": []})[0] == 401

    status, payload = client.request("POST", "/api/bridge/list_conversations", {"args": []})
    assert status == 200 and payload["ok"] is True and "conversations" in payload

    status, payload = client.request("POST", "/api/bridge/search_conversations", {"args": ["merhaba"]})
    assert status == 200 and payload["ok"] is True and payload["query"] == "merhaba"

    for denied in ("save_settings", "delete_api_key", "mobile_pairing_code", "pick_folder", "start_voice", "set_compact", "run_vision", "open_external"):
        status, payload = client.request("POST", f"/api/bridge/{denied}", {"args": []})
        assert status == 403 and "telefondan yapılamaz" in payload["error"], denied

    for unknown in ("_shutdown", "_push", "nonexistent", "__class__"):
        status, _ = client.request("POST", f"/api/bridge/{unknown}", {"args": []})
        assert status in (404, 400), unknown
    assert client.request("POST", "/api/bridge/list_conversations", {"args": "x"})[0] == 400
    assert client.request("POST", "/api/bridge/list_conversations", {"args": [1, 2, 3]})[0] == 400, "wrong arity is a bad request, not a crash"

    # A phone message runs the desktop's own submit path: same conversation, same state.
    status, payload = client.request("POST", "/api/bridge/submit_command", {"args": ["Telefondan tam arayüz mesajı"]})
    assert status == 200 and payload == {"ok": True}
    wait_for(lambda: not mobile.controller.state.busy and any(m.role == "assistant" for m in mobile.controller.state.messages))
    assert [m.role for m in mobile.controller.state.messages][:2] == ["user", "assistant"]
    assert "reply" in [json.loads(script.split("push(", 1)[1].rstrip(")"))["kind"] for script in mobile.window.scripts if "push(" in script]


def test_desktop_pushes_are_mirrored_to_the_phone(mobile) -> None:
    client = paired(mobile)
    connection, stream = client.events()
    assert next_event(stream)[0] == "hello"

    mobile.bridge._push("busy", {"busy": True, "status": "PROCESSING"})
    kind, payload = next_event(stream, want={"push"})
    assert kind == "push" and payload == {"kind": "busy", "payload": {"busy": True, "status": "PROCESSING"}}
    assert any("busy" in script for script in mobile.window.scripts), "the desktop window still gets it too"

    status, refreshed = client.request("POST", "/api/bridge/refresh", {"args": []})
    assert status == 200 and "snapshot" in refreshed, "reconnecting phones re-read the live snapshot"
    connection.close()


# ---------------------------------------------------------------------------
# phone voice: the phone records, the PC listens and speaks
# ---------------------------------------------------------------------------


class FakeRecognizer:
    def __init__(self) -> None:
        self.calls: list[tuple[int, int, str | None]] = []
        self.fail: Exception | None = None

    async def transcribe(self, capture, *, language=None):
        from app.voice.errors import VoiceNoSpeech
        from app.voice.models import TranscriptionResult

        self.calls.append((len(capture.data), capture.sample_rate, language))
        if self.fail is not None:
            raise self.fail
        if len(capture.data) < 3200:
            raise VoiceNoSpeech("silence")
        return TranscriptionResult(text="Telefondan sesli mesaj", provider="fake", model="fake")


class FakeSynthesizer:
    def __init__(self) -> None:
        self.texts: list[str] = []
        self.fail = False

    async def synthesize(self, text: str):
        from app.voice.models import AudioEncoding, SynthesizedSpeech, pcm16_to_wav

        self.texts.append(text)
        if self.fail:
            raise RuntimeError("quota")
        return SynthesizedSpeech(data=pcm16_to_wav(b"\x10\x00" * 800, 24_000), encoding=AudioEncoding.WAV, provider="fake", model="fake", voice="fake")


def wav_bytes(seconds: float = 0.5, rate: int = 16_000, channels: int = 1, width: int = 2) -> bytes:
    import io
    import math
    import struct
    import wave

    frames = int(rate * seconds)
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as handle:
        handle.setnchannels(channels)
        handle.setsampwidth(width)
        handle.setframerate(rate)
        if width == 2:
            handle.writeframes(b"".join(struct.pack("<h", int(3000 * math.sin(i / 8))) * channels for i in range(frames)))
        else:
            handle.writeframes(bytes(frames * channels))
    return buffer.getvalue()


def post_raw(client: Client, path: str, data: bytes, content_type: str, *, header: bool = True):
    connection = http.client.HTTPConnection("127.0.0.1", client.port, timeout=15)
    headers = {"Content-Type": content_type, "Content-Length": str(len(data))}
    if header:
        headers[CLIENT_HEADER] = "pwa"
    if client.cookie:
        headers["Cookie"] = client.cookie
    connection.request("POST", path, body=data, headers=headers)
    response = connection.getresponse()
    body = response.read()
    result = (response.status, body, dict(response.getheaders()))
    connection.close()
    return result


def test_voice_transcribe_runs_the_pcs_recognizer_and_says_silence_honestly(mobile) -> None:
    client = paired(mobile)
    mobile.app.voice = None
    status, body, _ = post_raw(client, "/api/voice/transcribe", wav_bytes(), "audio/wav")
    assert status == 409 and "ayarlanmamış" in json.loads(body)["error"]

    recognizer = FakeRecognizer()
    mobile.app.voice = SimpleNamespace(recognizer=recognizer, synthesizer=FakeSynthesizer())

    stranger = Client(mobile.port)
    assert post_raw(stranger, "/api/voice/transcribe", wav_bytes(), "audio/wav")[0] == 401
    assert post_raw(client, "/api/voice/transcribe", wav_bytes(), "audio/wav", header=False)[0] == 403
    assert post_raw(client, "/api/voice/transcribe", b"{}", "application/json")[0] == 400
    assert post_raw(client, "/api/voice/transcribe", b"not a wav at all", "audio/wav")[0] == 400
    assert post_raw(client, "/api/voice/transcribe", wav_bytes(channels=2), "audio/wav")[0] == 400, "stereo is refused, not guessed"
    status, body, _ = post_raw(client, "/api/voice/transcribe", wav_bytes(seconds=70), "audio/wav")
    assert status == 413 and "2 MB" in json.loads(body)["error"]
    assert recognizer.calls == [], "nothing malformed ever reaches the recognizer"

    status, body, _ = post_raw(client, "/api/voice/transcribe", wav_bytes(seconds=0.05), "audio/wav")
    assert status == 200 and json.loads(body) == {"ok": True, "text": ""}, "silence is an empty text, not an error"

    status, body, _ = post_raw(client, "/api/voice/transcribe", wav_bytes(seconds=0.5), "audio/wav")
    payload = json.loads(body)
    assert status == 200 and payload["text"] == "Telefondan sesli mesaj" and payload["provider"] == "fake"
    assert recognizer.calls[-1] == (16_000, 16_000, "tr"), "16 kHz mono PCM in the configured language"

    from app.voice.errors import VoiceProviderError

    recognizer.fail = VoiceProviderError("down")
    status, body, _ = post_raw(client, "/api/voice/transcribe", wav_bytes(), "audio/wav")
    assert status == 502 and "sağlayıcı" in json.loads(body)["error"]


def test_voice_speak_uses_the_cloud_voice_and_falls_back_to_the_local_one(mobile) -> None:
    client = paired(mobile)
    synthesizer = FakeSynthesizer()
    mobile.app.voice = SimpleNamespace(recognizer=FakeRecognizer(), synthesizer=synthesizer)

    status, audio, headers = post_raw(client, "/api/voice/speak", json.dumps({"text": "**Kalın** bir `cevap`; # başlık"}).encode("utf-8"), "application/json")
    assert status == 200 and headers["Content-Type"] == "audio/wav" and audio[:4] == b"RIFF"
    assert headers["X-JARVIS-Voice"] == "cloud" and headers["Cache-Control"] == "no-store"
    assert synthesizer.texts == ["Kalın bir cevap; başlık"], "markup never reaches the voice"

    assert client.request("POST", "/api/voice/speak", {"text": "   "})[0] == 400
    long_text = "kelime " * 1500
    post_raw(client, "/api/voice/speak", json.dumps({"text": long_text}).encode("utf-8"), "application/json")
    assert len(synthesizer.texts[-1]) <= 4000, "the desktop's own character limit applies"

    synthesizer.fail = True
    calls: list[str] = []

    async def local(text: str) -> bytes | None:
        calls.append(text)
        return wav_bytes(seconds=0.1)

    mobile.server.local_tts = local
    status, audio, headers = post_raw(client, "/api/voice/speak", json.dumps({"text": "Bulut sesi düştü"}).encode("utf-8"), "application/json")
    assert status == 200 and headers["X-JARVIS-Voice"] == "local" and audio[:4] == b"RIFF" and calls == ["Bulut sesi düştü"]

    async def missing(_text: str) -> bytes | None:
        return None

    mobile.server.local_tts = missing
    status, body, _ = post_raw(client, "/api/voice/speak", json.dumps({"text": "Hiç ses yok"}).encode("utf-8"), "application/json")
    assert status == 502 and "metin olarak" in json.loads(body)["error"]

    mobile.app.voice = None
    assert client.request("POST", "/api/voice/speak", {"text": "x"})[0] == 409


def test_a_spoken_submit_from_the_phone_is_recorded_as_a_voice_request(mobile) -> None:
    client = paired(mobile)
    status, payload = client.request("POST", "/api/bridge/submit_command", {"args": ["Sesle söylenen mesaj", True]})
    assert status == 200 and payload == {"ok": True}
    wait_for(lambda: not mobile.controller.state.busy and any(m.role == "assistant" for m in mobile.controller.state.messages))
    conversation = mobile.app.conversation_engine.get(mobile.controller.context.conversation_id)
    assert conversation.turns[0].content == "Sesle söylenen mesaj"
    assert conversation.turns[0].metadata.get("source") == "voice", "the record knows it was spoken"
    assert mobile.controller.state.messages[0].role == "user", "the desktop chat shows the transcript like any message"


def test_the_asset_stamp_follows_the_shim(tmp_path, mobile) -> None:
    shim = tmp_path / "shim.js"
    shim.write_text("// v1", encoding="utf-8")
    first = MobileServer(mobile.controller, mobile.bridge, mobile.store, port=0, nova_shim=shim).stamp
    shim.write_text("// v2", encoding="utf-8")
    second = MobileServer(mobile.controller, mobile.bridge, mobile.store, port=0, nova_shim=shim).stamp
    assert first != second, "a changed shim invalidates the immutable cache"
    assert mobile.server.stamp != first


def test_a_task_action_reports_in_turkish_what_actually_happened(mobile) -> None:
    """A resume that parks again must not read as a completion on the phone."""
    from types import SimpleNamespace

    from app.core.models import ToolExecutionStatus

    client = paired(mobile)

    class FakeTaskService:
        def __init__(self) -> None:
            self.calls: list[str] = []

        def _make(self, status, task_status, message, verified):
            async def run(_task_id):
                return SimpleNamespace(
                    status=status, verified=verified, message=message,
                    error=None, data={"status": task_status},
                    succeeded=status is ToolExecutionStatus.SUCCESS,
                )
            return run

        def __getattr__(self, name):
            raise AttributeError(name)

    service = FakeTaskService()
    service.resume = service._make(ToolExecutionStatus.PARTIAL, "paused", "Task resume reached paused.", False)
    service.cancel = service._make(ToolExecutionStatus.SUCCESS, "cancelled", "Task cancelled.", True)
    mobile.app.task_service = service

    identifier = str(uuid4())
    status, payload = client.request("POST", f"/api/tasks/{identifier}/resume", {})
    assert status == 409 and payload["ok"] is False, "a paused resume is not a success"
    assert payload["error"] == "Görev yeniden duraklatıldı; henüz bitmedi."
    assert "Task resume reached" not in json.dumps(payload), "no English machine string reaches the phone"

    status, payload = client.request("POST", f"/api/tasks/{identifier}/cancel", {})
    assert status == 200 and payload["ok"] is True and payload["message"] == "Görev iptal edildi."

    service.resume = service._make(ToolExecutionStatus.FAILED, "failed", "Task resume reached failed.", False)
    status, payload = client.request("POST", f"/api/tasks/{identifier}/resume", {})
    assert status == 409 and payload["error"] == "Görev sürdürülemedi ve başarısız oldu."
