"""The general state backups: consistent copies, rotation, honest refusals."""

from __future__ import annotations

import sqlite3

import pytest

from app.state_backup import (
    backup_state_now,
    list_state_backups,
    state_backup_due,
    state_backup_summary,
    state_databases,
)


def make_database(path, rows: int) -> None:
    connection = sqlite3.connect(path)
    try:
        connection.execute("CREATE TABLE items (value TEXT)")
        connection.executemany(
            "INSERT INTO items (value) VALUES (?)",
            [(f"row-{index}",) for index in range(rows)],
        )
        connection.commit()
    finally:
        connection.close()


def test_backup_copies_every_database_even_while_one_is_open(tmp_path) -> None:
    make_database(tmp_path / "jarvis_reminders.sqlite3", 5)
    make_database(tmp_path / "jarvis_routines.sqlite3", 3)
    (tmp_path / "not-a-db.txt").write_text("ignored", encoding="utf-8")
    (tmp_path / "sub").mkdir()
    make_database(tmp_path / "sub" / "nested.sqlite3", 1)  # never picked up

    assert [file.name for file in state_databases(tmp_path)] == [
        "jarvis_reminders.sqlite3",
        "jarvis_routines.sqlite3",
    ]

    # The app holds one database open mid-write: the copy must still be
    # consistent, because SQLite's backup API snapshots it.
    live = sqlite3.connect(tmp_path / "jarvis_reminders.sqlite3")
    try:
        live.execute("INSERT INTO items (value) VALUES ('uncommitted')")
        result = backup_state_now(tmp_path)
    finally:
        live.rollback()
        live.close()

    assert result["files"] == ["jarvis_reminders.sqlite3", "jarvis_routines.sqlite3"]
    assert result["bytes"] > 0 and result["removed"] == []

    copies = list_state_backups(tmp_path)
    assert len(copies) == 1 and copies[0]["files"] == 2
    copy = sqlite3.connect(
        tmp_path / "state-backups" / result["folder"] / "jarvis_reminders.sqlite3"
    )
    try:
        assert copy.execute("PRAGMA quick_check").fetchone()[0] == "ok"
        count = copy.execute("SELECT COUNT(*) FROM items").fetchone()[0]
    finally:
        copy.close()
    assert count == 5, "the uncommitted row of the live connection never leaks"


def test_rotation_keeps_the_newest_three_and_summary_reports_them(tmp_path) -> None:
    make_database(tmp_path / "jarvis_memory.sqlite3", 2)

    folders = [backup_state_now(tmp_path)["folder"] for _ in range(4)]

    remaining = [row["folder"] for row in list_state_backups(tmp_path)]
    assert remaining == sorted(folders, reverse=True)[:3]

    summary = state_backup_summary(tmp_path)
    assert summary["count"] == 3 and summary["databases"] == 1
    assert summary["newest_at"] is not None and summary["newest_bytes"] > 0


def test_due_logic_and_refusals_are_honest(tmp_path) -> None:
    assert state_backup_due(tmp_path) is True, "no copy yet means one is due"

    with pytest.raises(ValueError, match="bulunamadı"):
        backup_state_now(tmp_path)

    make_database(tmp_path / "jarvis_tasks.sqlite3", 1)
    backup_state_now(tmp_path)
    assert state_backup_due(tmp_path) is False, "a fresh copy postpones the weekly one"

    # An interrupted run leaves no half-copy in the rotation, and the
    # next run sweeps the leftover .tmp folder entirely.
    partial = tmp_path / "state-backups" / "state-99999999-999999-999999.tmp"
    partial.mkdir(parents=True)
    assert all(not row["folder"].endswith(".tmp") for row in list_state_backups(tmp_path))
    backup_state_now(tmp_path)
    assert not partial.exists(), "the stale half-copy is swept on the next run"


def test_backups_of_the_same_instant_never_fight_over_a_name(tmp_path, monkeypatch) -> None:
    """Windows ticks the clock in ~16 ms steps; a shared stamp must fork.

    This was the gate's 1-in-6 flake: the second same-instant backup hit
    WinError 5 replacing the folder the first had just made - and on a
    kinder filesystem it would have replaced it silently instead.
    """
    from app import state_backup as module

    make_database(tmp_path / "jarvis_memory.sqlite3", 2)
    frozen = module._utc_now()
    monkeypatch.setattr(module, "_utc_now", lambda: frozen)

    folders = [backup_state_now(tmp_path, keep=3)["folder"] for _ in range(3)]

    assert len(set(folders)) == 3, "one folder per backup, even inside one clock tick"
    stamp = frozen.strftime(module._STAMP_FORMAT)
    assert folders[0].endswith(stamp)
    assert folders[1].endswith(f"{stamp}b2") and folders[2].endswith(f"{stamp}b3")
    assert [row["folder"] for row in list_state_backups(tmp_path)] == sorted(folders, reverse=True)

    fourth = backup_state_now(tmp_path, keep=3)
    remaining = [row["folder"] for row in list_state_backups(tmp_path)]
    assert fourth["folder"] in remaining and folders[0] not in remaining, (
        "the plain name is the oldest of the shared instant"
    )
