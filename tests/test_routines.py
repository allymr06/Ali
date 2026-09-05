"""Scheduled routines: persistent store, atomic due claims, tools."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.core.models import ToolExecutionStatus
from app.routines import (
    MAX_ROUTINES,
    RoutineService,
    describe_schedule,
    next_daily_run,
    parse_clock,
)
from app.tools.executor import ToolExecutor


def test_clock_parsing_and_schedule_descriptions() -> None:
    assert parse_clock("9:05") == (9, 5)
    assert parse_clock("09.30") == (9, 30)
    assert parse_clock("24:00") is None
    assert parse_clock("sabah") is None
    assert parse_clock("") is None
    assert describe_schedule("daily", "09:00") == "her gün 09:00"
    assert describe_schedule("interval", "30") == "her 30 dakikada"
    assert describe_schedule("interval", "60") == "her saat"
    assert describe_schedule("interval", "180") == "her 3 saatte"


def test_next_daily_run_is_the_next_local_occurrence() -> None:
    now = datetime.now(timezone.utc)
    later = now + timedelta(hours=1)
    local_later = later.astimezone()
    if local_later.date() != now.astimezone().date():
        pytest.skip("too close to midnight for a same-day assertion")
    run = next_daily_run(local_later.hour, local_later.minute, now=now)
    assert run.tzinfo is not None
    assert now < run <= later + timedelta(minutes=1)
    earlier = (now - timedelta(hours=1)).astimezone()
    if earlier.date() != now.astimezone().date():
        pytest.skip("too close to midnight for a same-day assertion")
    tomorrow = next_daily_run(earlier.hour, earlier.minute, now=now)
    assert timedelta(hours=22) < tomorrow - now < timedelta(hours=24)


def test_create_list_and_delete_routines(tmp_path) -> None:
    service = RoutineService(tmp_path / "routines.sqlite3")

    assert service.create("", "özetle", at="09:00").error == "empty_name"
    assert service.create("Sabah", "", at="09:00").error == "empty_prompt"
    assert service.create("Sabah", "x" * 501, at="09:00").error == "prompt_too_long"
    assert service.create("Sabah", "özetle", at="9 gibi").error == "invalid_schedule"
    assert service.create("Sabah", "özetle").error == "invalid_schedule"
    assert service.create("Sabah", "özetle", every_minutes=1).error == "invalid_schedule"
    assert service.create("Sabah", "özetle", every_minutes=10**6).error == "invalid_schedule"

    daily = service.create("  Sabah   özeti ", "bugünkü hatırlatıcıları özetle", at="9:00")
    assert daily.status is ToolExecutionStatus.SUCCESS and daily.verified is True
    assert daily.data["schedule"] == "her gün 09:00"
    assert "Sabah özeti" in daily.message
    interval = service.create("Kontrol", "sistem durumunu özetle", every_minutes=30)
    assert interval.data["schedule"] == "her 30 dakikada"

    listed = service.list_active()
    assert listed.status is ToolExecutionStatus.SUCCESS and listed.verified is True
    names = {item["name"] for item in listed.data["routines"]}
    assert names == {"Sabah özeti", "Kontrol"}
    stored = service.get(daily.data["routine_id"])
    assert stored["prompt"] == "bugünkü hatırlatıcıları özetle"
    assert stored["run_count"] == 0 and stored["next_run_local"]

    assert service.delete("nope").error == "not_found"
    assert service.delete(daily.data["routine_id"]).status is ToolExecutionStatus.SUCCESS
    assert [item["name"] for item in service.list()] == ["Kontrol"]


def test_routine_count_is_bounded(tmp_path) -> None:
    service = RoutineService(tmp_path / "routines.sqlite3")
    for index in range(MAX_ROUTINES):
        assert service.create(f"r{index}", "özetle", every_minutes=10).succeeded
    assert service.create("fazla", "özetle", every_minutes=10).error == "too_many_routines"


def test_due_routines_are_claimed_once_and_moved_to_their_next_slot(tmp_path) -> None:
    service = RoutineService(tmp_path / "routines.sqlite3")
    created = service.create("Kontrol", "sistem durumunu özetle", every_minutes=30)
    routine_id = created.data["routine_id"]
    now = datetime.now(timezone.utc)
    assert service.claim_due(now=now) == []

    later = now + timedelta(minutes=31)
    claimed = service.claim_due(now=later)
    assert [item["routine_id"] for item in claimed] == [routine_id]
    assert service.claim_due(now=later) == []
    moved = datetime.fromisoformat(service.get(routine_id)["next_run_at"])
    assert moved - later == timedelta(minutes=30)

    # A deferred routine comes back sooner than its next slot, never later.
    assert service.defer(routine_id, 60) is True
    deferred = datetime.fromisoformat(service.get(routine_id)["next_run_at"])
    assert deferred < moved
    assert service.defer(routine_id, 3600) is True  # already sooner: unchanged
    assert datetime.fromisoformat(service.get(routine_id)["next_run_at"]) == deferred
    assert service.defer("missing", 60) is False

    assert service.record_run(
        routine_id, outcome="completed", summary="  Her şey   yolunda. ", conversation_id="abc"
    )
    stored = service.get(routine_id)
    assert stored["run_count"] == 1 and stored["last_outcome"] == "completed"
    assert stored["last_summary"] == "Her şey yolunda." and stored["conversation_id"] == "abc"
    assert service.record_run(routine_id, outcome="failed", summary="", conversation_id=None)
    assert service.get(routine_id)["conversation_id"] == "abc"


def test_routine_tools_are_registered_with_bounded_risk(tmp_path) -> None:
    service = RoutineService(tmp_path / "routines.sqlite3")
    executor = ToolExecutor()
    service.register_tools(executor)
    names = set(executor.list_names())
    assert {"create_routine", "list_routines", "delete_routine"} <= names
    create = executor.get("create_routine").definition
    assert create.risk_level.value == "low"
    assert executor.get("list_routines").definition.risk_level.value == "read_only"
    assert "routines" in create.capabilities
