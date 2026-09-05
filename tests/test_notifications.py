"""Notification centre and reminder watch: bounded, honest, thread-safe."""

from __future__ import annotations

import threading
import time
from datetime import datetime, timedelta, timezone

import pytest

from app.notifications import (
    NOTIFICATION_KINDS,
    Notification,
    NotificationCenter,
    ReminderWatch,
)
from app.notifications.service import MAX_BODY_LENGTH, MAX_TITLE_LENGTH


class Clock:
    def __init__(self) -> None:
        self.now = datetime(2026, 9, 5, 12, 0, tzinfo=timezone.utc)

    def __call__(self) -> datetime:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += timedelta(seconds=seconds)


# ---------------------------------------------------------------------------
# publishing and reading
# ---------------------------------------------------------------------------


def test_publish_records_a_bounded_entry_and_reports_unread() -> None:
    centre = NotificationCenter(clock=Clock())
    seen: list[tuple[Notification, int]] = []
    detach = centre.subscribe(lambda entry, unread: seen.append((entry, unread)))

    entry = centre.publish(
        "reminder",
        "  Hatırlatıcı  ",
        "Toplantı\n  notları",
        reference="r1",
        data={"reminder_id": "r1", "nested": {"x": 1}, "flag": True, "n": 2},
    )

    assert entry.kind == "reminder"
    assert entry.title == "Hatırlatıcı"
    assert entry.body == "Toplantı notları"
    assert entry.severity == "info"
    assert entry.read is False and entry.count == 1
    assert entry.data == {"reminder_id": "r1", "nested": "{'x': 1}", "flag": True, "n": 2}
    assert centre.unread_count() == 1
    assert centre.summary() == {"total": 1, "unread": 1}
    assert seen == [(entry, 1)]
    payload = entry.to_dict()
    assert payload["notification_id"] == entry.notification_id
    assert payload["created_at"].startswith("2026-09-05T12:00")
    assert "dedupe_key" not in payload
    detach()
    centre.publish("system", "Sonra")
    assert len(seen) == 1


def test_titles_and_bodies_are_bounded_and_kinds_validated() -> None:
    centre = NotificationCenter()
    entry = centre.publish("system", "b" * 500, "c" * 5000)
    assert len(entry.title) == MAX_TITLE_LENGTH and entry.title.endswith("…")
    assert len(entry.body) == MAX_BODY_LENGTH and entry.body.endswith("…")
    with pytest.raises(ValueError):
        centre.publish("weather", "x")
    with pytest.raises(ValueError):
        centre.publish("system", "x", severity="loud")
    with pytest.raises(ValueError):
        centre.publish("system", "   ")
    assert {"reminder", "approval", "reply", "task", "diagnostic", "observation", "system"} == set(
        NOTIFICATION_KINDS
    )


def test_list_is_newest_first_bounded_and_filterable() -> None:
    centre = NotificationCenter(max_entries=3)
    for index in range(5):
        centre.publish("system", f"n{index}")
    titles = [entry.title for entry in centre.list()]
    assert titles == ["n4", "n3", "n2"]
    assert centre.summary() == {"total": 3, "unread": 3}
    assert [e.title for e in centre.list(limit=1)] == ["n4"]
    assert [e.title for e in centre.list(limit=0)] == ["n4"]
    centre.mark_read([centre.list()[0].notification_id])
    assert [e.title for e in centre.list(unread_only=True)] == ["n3", "n2"]


def test_mark_read_dismiss_and_clear() -> None:
    centre = NotificationCenter()
    first = centre.publish("system", "bir")
    second = centre.publish("system", "iki")
    assert centre.mark_read([first.notification_id, "missing"]) == 1
    assert centre.get(first.notification_id).read is True
    assert centre.mark_read([first.notification_id]) == 0
    assert centre.unread_count() == 1
    assert centre.mark_read(None) == 1
    assert centre.unread_count() == 0
    assert centre.dismiss(second.notification_id) is True
    assert centre.dismiss(second.notification_id) is False
    assert centre.get("nope") is None
    assert centre.clear() == 1
    assert centre.summary() == {"total": 0, "unread": 0}


def test_dedupe_collapses_repeats_within_the_window_only() -> None:
    clock = Clock()
    centre = NotificationCenter(dedupe_seconds=60, clock=clock)
    first = centre.publish("diagnostic", "Uyarı", "one", dedupe_key="diag:x")
    clock.advance(10)
    again = centre.publish("diagnostic", "Uyarı", "two", dedupe_key="diag:x")
    assert again is first
    assert first.count == 2 and first.body == "two"
    assert first.updated_at == clock.now and first.created_at != clock.now
    assert centre.summary() == {"total": 1, "unread": 1}

    # A different key, a read entry, or an old entry each start a new one.
    other = centre.publish("diagnostic", "Uyarı", "three", dedupe_key="diag:y")
    assert other is not first
    centre.mark_read([first.notification_id])
    after_read = centre.publish("diagnostic", "Uyarı", "four", dedupe_key="diag:x")
    assert after_read is not first
    clock.advance(61)
    later = centre.publish("diagnostic", "Uyarı", "five", dedupe_key="diag:x")
    assert later is not after_read
    assert NotificationCenter(dedupe_seconds=0).publish("system", "a", dedupe_key="k") is not None


def test_broken_listeners_do_not_stop_publishing() -> None:
    centre = NotificationCenter()
    calls: list[str] = []

    def bad(entry, unread):
        raise RuntimeError("boom")

    centre.subscribe(bad)
    centre.subscribe(lambda entry, unread: calls.append(entry.title))
    centre.publish("system", "ok")
    assert calls == ["ok"]


def test_centre_is_safe_under_concurrent_publishing() -> None:
    centre = NotificationCenter(max_entries=50)
    barrier = threading.Barrier(4)

    def worker(tag: str) -> None:
        barrier.wait()
        for index in range(100):
            centre.publish("system", f"{tag}{index}", dedupe_key=tag if index % 2 else None)

    threads = [threading.Thread(target=worker, args=(t,)) for t in "abcd"]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(5)
    summary = centre.summary()
    assert 1 <= summary["total"] <= 50
    assert summary["unread"] == summary["total"]


def test_bounds_are_validated() -> None:
    with pytest.raises(ValueError):
        NotificationCenter(max_entries=0)
    with pytest.raises(ValueError):
        NotificationCenter(dedupe_seconds=-1)


# ---------------------------------------------------------------------------
# reminder watch
# ---------------------------------------------------------------------------


class FakeReminders:
    def __init__(self, batches: list[list[dict[str, str]]] | None = None, fail: bool = False) -> None:
        self.batches = list(batches or [])
        self.fail = fail
        self.claims = 0

    def claim_due(self) -> list[dict[str, str]]:
        self.claims += 1
        if self.fail:
            raise RuntimeError("store unavailable")
        return self.batches.pop(0) if self.batches else []


def test_poll_once_delivers_each_claimed_reminder_and_survives_errors() -> None:
    delivered: list[str] = []

    def deliver(reminder: dict[str, str]) -> None:
        if reminder["text"] == "bad":
            raise ValueError("cannot show")
        delivered.append(reminder["text"])

    watch = ReminderWatch(
        FakeReminders([[{"reminder_id": "1", "text": "a"}, {"reminder_id": "2", "text": "bad"}, {"reminder_id": "3", "text": "c"}]]),
        deliver,
        interval_seconds=0.05,
    )
    assert watch.poll_once() == 2
    assert delivered == ["a", "c"]
    assert watch.poll_once() == 0
    assert ReminderWatch(FakeReminders(fail=True), deliver).poll_once() == 0
    with pytest.raises(ValueError):
        ReminderWatch(FakeReminders(), deliver, interval_seconds=0)


def test_watch_thread_polls_immediately_then_periodically_and_stops() -> None:
    reminders = FakeReminders([[{"reminder_id": "1", "text": "şimdi"}]])
    delivered: list[str] = []
    watch = ReminderWatch(reminders, lambda r: delivered.append(r["text"]), interval_seconds=0.02)
    assert watch.running is False
    watch.start()
    watch.start()  # idempotent
    assert watch.running is True
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline and reminders.claims < 3:
        time.sleep(0.01)
    assert delivered == ["şimdi"]
    assert reminders.claims >= 3
    watch.stop()
    assert watch.running is False
    claims = reminders.claims
    time.sleep(0.08)
    assert reminders.claims == claims
    watch.stop()  # idempotent
    # A stopped watch can be started again with a fresh thread.
    watch.start()
    assert watch.running is True
    watch.stop()
