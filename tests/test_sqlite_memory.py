from __future__ import annotations

import asyncio
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

import pytest

from app.bootstrap import create_application
from app.config.settings import Settings
from app.core.models import Request
from app.core.time import utc_now
from app.memory.manager import MemoryManager
from app.memory.models import (
    MemoryEntry,
    MemoryFreshness,
    MemorySensitivity,
    MemorySource,
    MemoryType,
)
from app.memory.sqlite import MemoryCorruptionError, SQLiteMemoryStore


def test_sqlite_store_survives_restart(tmp_path) -> None:
    database = tmp_path / "memory.sqlite3"
    first = SQLiteMemoryStore(database)
    memory = MemoryEntry(
        "The user's editor is VS Code",
        memory_type=MemoryType.PREFERENCE,
        source_reference="request:abc",
        metadata={"subject": "editor"},
    )
    first.save(memory)
    first.close()

    second = SQLiteMemoryStore(database)
    restored = second.get(memory.memory_id)

    assert restored.content == memory.content
    assert restored.source_reference == "request:abc"
    assert restored.metadata == {"subject": "editor"}
    assert second.search("editor")[0].memory_id == memory.memory_id
    second.close()


def test_sqlite_store_round_trips_all_provenance_fields(tmp_path) -> None:
    store = SQLiteMemoryStore(tmp_path / "memory.sqlite3")
    now = utc_now()
    memory = MemoryEntry(
        "Verified imported fact",
        memory_type=MemoryType.PROJECT,
        importance=0.8,
        confidence=0.7,
        source=MemorySource.IMPORTED,
        source_reference="file:notes.md",
        sensitivity=MemorySensitivity.PERSONAL,
        last_verified_at=now,
        expires_at=now + timedelta(days=30),
        metadata={"freshness_policy": "monthly"},
    )

    store.save(memory)
    restored = store.get(memory.memory_id)

    assert restored == memory
    store.close()


def test_sqlite_search_excludes_inactive_and_expired_memories(tmp_path) -> None:
    store = SQLiteMemoryStore(tmp_path / "memory.sqlite3")
    inactive = MemoryEntry("Python inactive", active=False)
    expired = MemoryEntry(
        "Python expired",
        created_at=utc_now() - timedelta(days=2),
        updated_at=utc_now() - timedelta(days=2),
        expires_at=utc_now() - timedelta(days=1),
    )
    current = MemoryEntry("Python current")
    for memory in (inactive, expired, current):
        store.save(memory)

    assert store.search("Python") == [current]
    store.close()


def test_sqlite_search_ranks_more_relevant_memory_first(tmp_path) -> None:
    store = SQLiteMemoryStore(tmp_path / "memory.sqlite3")
    broad = MemoryEntry("Python is one of several programming languages")
    exact = MemoryEntry("Python project", importance=0.9)
    store.save(broad)
    store.save(exact)

    results = store.search("Python project")

    assert [item.memory_id for item in results] == [exact.memory_id, broad.memory_id]
    store.close()


def test_recall_touch_is_persisted(tmp_path) -> None:
    database = tmp_path / "memory.sqlite3"
    manager = MemoryManager(SQLiteMemoryStore(database))
    memory = manager.remember("Persistent recall marker")

    manager.recall("recall marker")
    manager.close()

    reopened = SQLiteMemoryStore(database)
    assert reopened.get(memory.memory_id).last_used_at is not None
    reopened.close()


def test_forget_and_permanent_delete_are_distinct(tmp_path) -> None:
    store = SQLiteMemoryStore(tmp_path / "memory.sqlite3")
    manager = MemoryManager(store)
    memory = manager.remember("User-controlled deletion")

    manager.forget(memory.memory_id)
    assert manager.get(memory.memory_id).active is False

    deleted = manager.delete(memory.memory_id)
    assert deleted.memory_id == memory.memory_id
    with pytest.raises(KeyError):
        manager.get(memory.memory_id)
    manager.close()


def test_sqlite_store_is_safe_for_concurrent_writers(tmp_path) -> None:
    store = SQLiteMemoryStore(tmp_path / "memory.sqlite3")

    def save(index: int) -> None:
        store.save(MemoryEntry(f"Concurrent memory {index}"))

    with ThreadPoolExecutor(max_workers=8) as executor:
        list(executor.map(save, range(100)))

    assert len(store) == 100
    assert len(store.search("Concurrent", limit=100)) == 100
    store.close()


def test_database_backup_and_restore(tmp_path) -> None:
    database = tmp_path / "memory.sqlite3"
    backup = tmp_path / "backups" / "memory.backup.sqlite3"
    restored_database = tmp_path / "restored.sqlite3"
    store = SQLiteMemoryStore(database)
    memory = MemoryEntry("Recoverable memory")
    store.save(memory)

    assert store.backup_to(backup) == backup.resolve()
    store.close()

    restored = SQLiteMemoryStore.restore_from_backup(backup, restored_database)
    assert restored.get(memory.memory_id).content == "Recoverable memory"
    restored.close()


def test_corrupt_database_fails_closed(tmp_path) -> None:
    database = tmp_path / "memory.sqlite3"
    database.write_bytes(b"not a sqlite database")

    with pytest.raises(MemoryCorruptionError, match="unreadable"):
        SQLiteMemoryStore(database)


def test_new_database_records_schema_version(tmp_path) -> None:
    database = tmp_path / "memory.sqlite3"
    store = SQLiteMemoryStore(database)
    store.close()

    connection = sqlite3.connect(database)
    try:
        version = connection.execute("PRAGMA user_version").fetchone()[0]
    finally:
        connection.close()

    assert version == SQLiteMemoryStore.SCHEMA_VERSION


def test_application_uses_durable_memory_across_restarts(tmp_path) -> None:
    database = tmp_path / "jarvis.sqlite3"
    settings = Settings(
        default_provider="mock",
        default_model="mock-model",
        memory_database_path=str(database),
    )
    first = create_application(settings)
    memory = first.memory_manager.remember("JARVIS restart proof")
    first.memory_manager.close()

    second = create_application(settings)

    assert second.memory_manager.get(memory.memory_id).content == "JARVIS restart proof"
    second.memory_manager.close()


def test_core_refuses_to_persist_a_labeled_secret(tmp_path) -> None:
    application = create_application(
        Settings(
            default_provider="mock",
            default_model="mock-model",
            memory_database_path=str(tmp_path / "jarvis.sqlite3"),
        )
    )

    response = asyncio.run(
        application.engine.handle(Request("Remember: password=correct-horse"))
    )

    assert response.metadata["memory_decision"] is True
    assert response.metadata["memory_saved"] is False
    assert "Credential-like" in response.metadata["memory_write_reason"]
    assert application.memory_manager.count() == 0
    application.memory_manager.close()


def test_memory_freshness_reports_current_stale_and_expired() -> None:
    now = utc_now()
    current = MemoryEntry("Current", updated_at=now)
    stale = MemoryEntry(
        "Stale",
        created_at=now - timedelta(days=200),
        updated_at=now - timedelta(days=100),
    )
    expired = MemoryEntry(
        "Expired",
        created_at=now - timedelta(days=2),
        updated_at=now - timedelta(days=2),
        expires_at=now - timedelta(days=1),
    )

    assert current.freshness(now=now) is MemoryFreshness.CURRENT
    assert stale.freshness(now=now) is MemoryFreshness.STALE
    assert expired.freshness(now=now) is MemoryFreshness.EXPIRED


def test_conflicts_are_visible_by_declared_subject(tmp_path) -> None:
    manager = MemoryManager(SQLiteMemoryStore(tmp_path / "memory.sqlite3"))
    first = manager.remember("Preferred editor is VS Code", metadata={"subject": "editor"})
    second = manager.remember("Preferred editor is Neovim", metadata={"subject": "editor"})
    manager.remember("Favorite shell is PowerShell", metadata={"subject": "shell"})

    assert [item.memory_id for item in manager.conflicts(first.memory_id)] == [
        second.memory_id
    ]
    manager.close()


def test_explicit_retention_purge_removes_expired_records(tmp_path) -> None:
    manager = MemoryManager(SQLiteMemoryStore(tmp_path / "memory.sqlite3"))
    now = utc_now()
    expired = manager.remember(
        "Temporary context",
        expires_at=now + timedelta(seconds=1),
    )
    retained = manager.remember("Permanent context")

    purged = manager.purge_expired(now=now + timedelta(seconds=2))

    assert [item.memory_id for item in purged] == [expired.memory_id]
    assert [item.memory_id for item in manager.all()] == [retained.memory_id]
    manager.close()
