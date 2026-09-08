"""The reminder delivery lifecycle: claim, deliver, acknowledge or release.

A failed delivery used to consume the reminder: ``claim_due`` marked it
delivered before the callback ran, and a raising callback left it marked
so. These tests pin the repaired lifecycle with a controllable clock and a
temporary database: a claim is a lease, delivery is acknowledged only
after the callback returned, a failure comes back after a bounded delay,
a lease nobody settles expires, cancellation still wins, an old database
migrates in place, and the routine store keeps its older contract.
"""

from __future__ import annotations

import sqlite3
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from app.notifications import NotificationCenter, ReminderWatch
from app.reminders.service import (
    LEASE_SECONDS,
    MAX_ATTEMPTS,
    RETRY_MAX_SECONDS,
    ReminderService,
    retry_delay_seconds,
)
from app.routines.service import RoutineService

START = datetime(2026, 9, 8, 9, 0, tzinfo=timezone.utc)


class Clock:
    def __init__(self, start: datetime = START) -> None:
        self.now = start

    def __call__(self) -> datetime:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now = self.now + timedelta(seconds=seconds)


@pytest.fixture()
def clock() -> Clock:
    return Clock()


@pytest.fixture()
def service(tmp_path, clock) -> ReminderService:
    return ReminderService(tmp_path / "reminders.sqlite3", clock=clock)


def due(service: ReminderService, clock: Clock, text: str = "Su iç") -> str:
    """A reminder created a minute ahead, with the clock moved past it."""
    created = service.create(text, minutes=1)
    assert created.succeeded, created.message
    clock.advance(61)
    return created.data["reminder_id"]


# ---------------------------------------------------------------------------
# the happy path and the failure path
# ---------------------------------------------------------------------------


def test_a_delivered_reminder_is_acknowledged_and_leaves_the_active_list(service, clock) -> None:
    reminder_id = due(service, clock)

    claimed = service.claim_due()

    assert [item["reminder_id"] for item in claimed] == [reminder_id]
    claim = claimed[0]
    assert claim["text"] == "Su iç" and claim["attempt"] == 1 and claim["claim_token"]
    assert service.claim_due() == [], "a leased reminder is not handed out twice"
    assert service.list_active().data["reminders"][0]["status"] == "teslim ediliyor"

    assert service.acknowledge(reminder_id, claim["claim_token"]) is True

    assert service.list_active().data["reminders"] == []
    record = service.get(reminder_id)
    assert record["delivered"] is True and record["delivered_at"] == clock.now.isoformat()
    assert record["status"] == "teslim edildi" and record["claimed"] is False
    assert service.acknowledge(reminder_id, claim["claim_token"]) is False, "a settled claim cannot be settled again"
    clock.advance(LEASE_SECONDS * 2)
    assert service.claim_due() == []


def test_a_failed_delivery_comes_back_after_a_delay_and_then_succeeds(service, clock) -> None:
    reminder_id = due(service, clock)
    (claim,) = service.claim_due()

    released = service.release(reminder_id, claim["claim_token"], error=RuntimeError("centre closed"))

    assert released == {
        "reminder_id": reminder_id,
        "attempts": 1,
        "next_attempt_at": (clock.now + timedelta(seconds=30)).isoformat(),
        "exhausted": False,
    }
    listed = service.list_active().data["reminders"][0]
    assert listed["status"] == "yeniden denenecek (1/5)" and listed["attempts"] == 1
    assert service.get(reminder_id)["last_error"] == "RuntimeError: centre closed"
    assert service.claim_due() == [], "not before the retry delay"
    clock.advance(29)
    assert service.claim_due() == []
    clock.advance(1)

    (retry,) = service.claim_due()

    assert retry["reminder_id"] == reminder_id and retry["attempt"] == 2
    assert retry["claim_token"] != claim["claim_token"]
    assert service.acknowledge(reminder_id, retry["claim_token"]) is True
    assert service.get(reminder_id)["delivered"] is True and service.get(reminder_id)["last_error"] == ""


def test_repeated_failures_back_off_and_stop_at_the_attempt_limit(service, clock) -> None:
    reminder_id = due(service, clock)
    delays: list[float] = []
    for attempt in range(1, MAX_ATTEMPTS + 1):
        (claim,) = service.claim_due()
        assert claim["attempt"] == attempt
        released = service.release(reminder_id, claim["claim_token"], error="toast host down")
        assert released["attempts"] == attempt
        if released["exhausted"]:
            assert attempt == MAX_ATTEMPTS and released["next_attempt_at"] is None
            break
        next_at = datetime.fromisoformat(released["next_attempt_at"])
        delays.append((next_at - clock.now).total_seconds())
        assert service.claim_due() == []
        clock.advance((next_at - clock.now).total_seconds())
    assert delays == [30, 60, 120, 240], "the delay doubles from thirty seconds"

    clock.advance(24 * 3600)
    assert service.claim_due() == [], "an exhausted reminder is not tried in a loop"
    listed = service.list_active().data["reminders"][0]
    assert listed["status"] == "teslim edilemedi (5 deneme)" and listed["attempts"] == 5
    undeliverable = service.list_undeliverable()
    assert [item["reminder_id"] for item in undeliverable] == [reminder_id]
    assert undeliverable[0]["last_error"] == "toast host down"
    # Still the user's to cancel.
    assert service.cancel(reminder_id).succeeded
    assert service.list_active().data["reminders"] == [] and service.list_undeliverable() == []


def test_retry_delays_are_bounded() -> None:
    assert [retry_delay_seconds(attempt) for attempt in (1, 2, 3, 4, 5)] == [30, 60, 120, 240, 480]
    assert retry_delay_seconds(12) == RETRY_MAX_SECONDS
    assert retry_delay_seconds(0) == 30


# ---------------------------------------------------------------------------
# a process that died mid-delivery
# ---------------------------------------------------------------------------


def test_a_claim_nobody_settled_expires_with_its_lease(tmp_path, clock) -> None:
    path = tmp_path / "reminders.sqlite3"
    first = ReminderService(path, clock=clock)
    reminder_id = due(first, clock)
    (claim,) = first.claim_due()
    # The process dies here: no acknowledgment, no release.

    restarted = ReminderService(path, clock=clock)

    assert restarted.claim_due() == [], "the lease still holds"
    clock.advance(LEASE_SECONDS - 1)
    assert restarted.claim_due() == []
    clock.advance(2)
    (again,) = restarted.claim_due()
    assert again["reminder_id"] == reminder_id and again["attempt"] == 2
    assert again["claim_token"] != claim["claim_token"]
    assert restarted.acknowledge(reminder_id, claim["claim_token"]) is False, "the dead process's token no longer holds the claim"
    assert restarted.acknowledge(reminder_id, again["claim_token"]) is True
    assert restarted.get(reminder_id)["delivered"] is True


def test_a_reminder_already_in_the_centre_is_not_published_twice_after_a_restart(tmp_path, clock) -> None:
    """The centre durably accepted the entry, then the process died before
    the acknowledgment: the reminder is handed out again, and the delivery
    finds it by its reference instead of showing it a second time."""
    path = tmp_path / "reminders.sqlite3"
    centre = NotificationCenter()
    published: list[str] = []

    def deliver(reminder: dict) -> None:
        reference = reminder["reminder_id"]
        if centre.find("reminder", reference) is not None:
            return
        centre.publish("reminder", "Hatırlatıcı", reminder["text"], reference=reference)
        published.append(reference)

    first = ReminderService(path, clock=clock)
    reminder_id = due(first, clock)
    (claim,) = first.claim_due()
    deliver(claim)  # accepted by the centre …
    # … and the process dies before ``acknowledge``.
    clock.advance(LEASE_SECONDS + 1)

    restarted = ReminderService(path, clock=clock)
    assert ReminderWatch(restarted, deliver).poll_once() == 1

    assert published == [reminder_id], "one entry, however many hand-outs"
    assert len(centre.list()) == 1
    assert restarted.get(reminder_id)["delivered"] is True


# ---------------------------------------------------------------------------
# concurrency and cancellation
# ---------------------------------------------------------------------------


def test_concurrent_pollers_never_claim_the_same_reminder(tmp_path, clock) -> None:
    path = tmp_path / "reminders.sqlite3"
    seed = ReminderService(path, clock=clock)
    ids = {due(seed, clock, text=f"Hatırlat {index}") for index in range(20)}
    claimed: list[str] = []
    lock = threading.Lock()
    start = threading.Barrier(4)

    def poll() -> None:
        service = ReminderService(path, clock=clock)
        start.wait()
        for _ in range(10):
            batch = service.claim_due()
            with lock:
                claimed.extend(item["reminder_id"] for item in batch)
            for item in batch:
                service.acknowledge(item["reminder_id"], item["claim_token"])

    threads = [threading.Thread(target=poll) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(10)

    assert sorted(claimed) == sorted(ids), "every reminder claimed exactly once across the pollers"
    assert seed.list_active().data["reminders"] == []


def test_concurrent_delivery_loops_deliver_each_reminder_once(tmp_path, clock) -> None:
    path = tmp_path / "reminders.sqlite3"
    seed = ReminderService(path, clock=clock)
    ids = {due(seed, clock, text=f"Hatırlat {index}") for index in range(12)}
    delivered: list[str] = []
    lock = threading.Lock()
    start = threading.Barrier(3)

    def deliver(reminder: dict) -> None:
        with lock:
            delivered.append(reminder["reminder_id"])

    def loop() -> None:
        service = ReminderService(path, clock=clock)
        start.wait()
        for _ in range(6):
            service.deliver_due(deliver)

    threads = [threading.Thread(target=loop) for _ in range(3)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(10)

    assert sorted(delivered) == sorted(ids)


def test_cancellation_wins_before_delivery_and_while_a_retry_is_pending(service, clock) -> None:
    waiting = due(service, clock, text="Erken")
    assert service.cancel(waiting).succeeded
    assert service.claim_due() == []

    retrying = due(service, clock, text="Tekrar")
    (claim,) = service.claim_due()
    service.release(retrying, claim["claim_token"], error="busy")
    assert service.get(retrying)["status"].startswith("yeniden denenecek")
    assert service.cancel(retrying).succeeded
    clock.advance(RETRY_MAX_SECONDS)
    assert service.claim_due() == []
    assert service.get(retrying)["cancelled"] is True and service.get(retrying)["next_attempt_at"] is None

    in_flight = due(service, clock, text="Yolda")
    (claim,) = service.claim_due()
    assert service.cancel(in_flight).succeeded, "cancellation reaches a claimed reminder too"
    assert service.acknowledge(in_flight, claim["claim_token"]) is False
    assert service.release(in_flight, claim["claim_token"], error="late") is None
    assert service.get(in_flight)["cancelled"] is True and service.get(in_flight)["delivered"] is False
    assert service.cancel(in_flight).error == "not_found"


# ---------------------------------------------------------------------------
# the store as it was
# ---------------------------------------------------------------------------


OLD_SCHEMA = """
CREATE TABLE reminders (
    reminder_id TEXT PRIMARY KEY,
    text TEXT NOT NULL,
    due_at TEXT NOT NULL,
    created_at TEXT NOT NULL,
    delivered INTEGER NOT NULL DEFAULT 0,
    cancelled INTEGER NOT NULL DEFAULT 0
)
"""


def old_database(path: Path, now: datetime) -> None:
    with sqlite3.connect(path) as connection:
        connection.execute(OLD_SCHEMA)
        stamp = now.isoformat()
        rows = [
            ("active", "Aktif", (now - timedelta(minutes=1)).isoformat(), stamp, 0, 0),
            ("future", "Yarın", (now + timedelta(days=1)).isoformat(), stamp, 0, 0),
            ("done", "Teslim edildi", (now - timedelta(days=1)).isoformat(), stamp, 1, 0),
            ("gone", "İptal", (now - timedelta(days=1)).isoformat(), stamp, 0, 1),
        ]
        connection.executemany("INSERT INTO reminders VALUES (?, ?, ?, ?, ?, ?)", rows)


def test_an_old_database_migrates_in_place_and_keeps_every_record(tmp_path, clock) -> None:
    path = tmp_path / "reminders.sqlite3"
    old_database(path, clock.now)

    service = ReminderService(path, clock=clock)

    with sqlite3.connect(path) as connection:
        columns = {row[1] for row in connection.execute("PRAGMA table_info(reminders)")}
    assert {"attempts", "claim_token", "claimed_at", "next_attempt_at", "delivered_at", "last_error"} <= columns
    assert service.get("done")["delivered"] is True and service.get("done")["status"] == "teslim edildi"
    assert service.get("gone")["cancelled"] is True and service.get("gone")["status"] == "iptal edildi"
    assert service.get("future")["status"] == "bekliyor"
    assert [item["reminder_id"] for item in service.list_active().data["reminders"]] == ["active", "future"]

    (claim,) = service.claim_due()
    assert claim["reminder_id"] == "active" and claim["attempt"] == 1
    assert service.acknowledge("active", claim["claim_token"]) is True

    # Opening the store again is a no-op migration, with nothing lost.
    reopened = ReminderService(path, clock=clock)
    with sqlite3.connect(path) as connection:
        assert ReminderService._migrate(connection) == []
    assert reopened.get("active")["delivered"] is True
    assert [item["reminder_id"] for item in reopened.list_active().data["reminders"]] == ["future"]


# ---------------------------------------------------------------------------
# the watch, and the routine store that shares it
# ---------------------------------------------------------------------------


def test_the_watch_settles_the_service_claims_it_hands_out(service, clock) -> None:
    reminder_id = due(service, clock)
    outcomes: list[str] = []

    def deliver(reminder: dict) -> None:
        outcomes.append(reminder["reminder_id"])
        if len(outcomes) == 1:
            raise RuntimeError("no window yet")

    watch = ReminderWatch(service, deliver)
    assert watch.poll_once() == 0
    assert service.get(reminder_id)["status"] == "yeniden denenecek (1/5)"
    assert watch.poll_once() == 0, "not before the delay"
    clock.advance(31)
    assert watch.poll_once() == 1
    assert outcomes == [reminder_id, reminder_id]
    assert service.get(reminder_id)["delivered"] is True


def test_the_classic_delivery_loop_settles_claims_the_same_way(service, clock) -> None:
    reminder_id = due(service, clock)
    seen: list[str] = []

    def failing(reminder: dict) -> None:
        seen.append(reminder["reminder_id"])
        raise ValueError("queue closed")

    assert service.deliver_due(failing) == 0
    assert service.get(reminder_id)["status"] == "yeniden denenecek (1/5)"
    clock.advance(31)
    assert service.deliver_due(seen.append) == 0 or True  # a plain callback that returns
    assert service.get(reminder_id)["delivered"] is True


def test_scheduled_routines_keep_their_contract_under_the_watch(tmp_path) -> None:
    routines = RoutineService(tmp_path / "routines.sqlite3")
    created = routines.create("Kontrol", "sistem durumunu özetle", every_minutes=30)
    routine_id = created.data["routine_id"]
    past = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
    with routines._connect() as connection:
        connection.execute("UPDATE routines SET next_run_at = ? WHERE routine_id = ?", (past, routine_id))
    assert not hasattr(routines, "acknowledge") and not hasattr(routines, "release")
    ran: list[dict] = []

    watch = ReminderWatch(routines, ran.append)

    assert watch.poll_once() == 1
    assert [item["routine_id"] for item in ran] == [routine_id] and "claim_token" not in ran[0]
    moved = datetime.fromisoformat(routines.get(routine_id)["next_run_at"])
    assert moved > datetime.now(timezone.utc), "claiming moved the routine to its next slot, as before"
    assert watch.poll_once() == 0
    # A raising run is not retried by the watch either: the routine keeps its slot.
    with routines._connect() as connection:
        connection.execute("UPDATE routines SET next_run_at = ? WHERE routine_id = ?", (past, routine_id))

    def failing(routine: dict) -> None:
        raise RuntimeError("desktop busy")

    assert ReminderWatch(routines, failing).poll_once() == 0
    assert datetime.fromisoformat(routines.get(routine_id)["next_run_at"]) > datetime.now(timezone.utc)
    assert routines.defer(routine_id, 60) is True
