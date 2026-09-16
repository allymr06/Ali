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
import time
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest

from app.mobile.server import CLIENT_HEADER, MobileServer
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


def next_event(response, timeout: float = 10.0):
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
            return kind, json.loads(text[6:])
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
    kind, payload = next_event(stream)
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


# ---------------------------------------------------------------------------
# approvals and tasks
# ---------------------------------------------------------------------------


def test_approvals_are_bound_to_the_pending_action_and_never_repeat(mobile) -> None:
    client = paired(mobile)
    connection, stream = client.events()
    assert next_event(stream)[0] == "hello"

    future = mobile.controller.submit_background(mobile.bridge._request_approval(approval_request(seconds=30)), lambda _f: None)
    kind, payload = next_event(stream)
    assert kind == "approval" and payload["tool"] == "fs.write" and payload["parameters"]["api_key"] == "<gizli>"
    token = payload["token"]
    assert [item["token"] for item in mobile.server.pending_approvals()] == [token]

    stranger = Client(mobile.port)
    assert stranger.request("POST", f"/api/approvals/{token}", {"approved": True})[0] == 401

    status, decided = client.request("POST", f"/api/approvals/{token}", {"approved": True})
    assert status == 200 and decided["ok"] is True
    assert future.result(timeout=5) is True
    kind, closed = next_event(stream)
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
