from __future__ import annotations

import sqlite3

import pytest

from app.config import paths
from app.conversation.models import Conversation, ConversationTurn, MessageRole
from app.conversation.sqlite import (
    ConversationPersistenceError,
    SQLiteConversationStore,
)


def test_conversation_store_uses_versioned_absolute_database(tmp_path) -> None:
    store = SQLiteConversationStore(tmp_path / "nested" / "conversations.sqlite3")

    assert store.path.is_absolute()
    assert store.path.parent.is_dir()
    assert store._connection.execute("PRAGMA user_version").fetchone()[0] == 1
    assert store._connection.execute("PRAGMA busy_timeout").fetchone()[0] == 5000
    store.close()


def test_version_zero_migration_preserves_existing_conversations(tmp_path) -> None:
    database = tmp_path / "conversations.sqlite3"
    store = SQLiteConversationStore(database)
    expected = store.save(Conversation())
    store.close()
    connection = sqlite3.connect(database)
    connection.execute("PRAGMA user_version = 0")
    connection.commit()
    connection.close()

    reopened = SQLiteConversationStore(database)

    assert reopened.get(expected.conversation_id).conversation_id == (
        expected.conversation_id
    )
    assert reopened._connection.execute("PRAGMA user_version").fetchone()[0] == 1
    reopened.close()


def test_conversation_store_rejects_newer_or_incomplete_schema(tmp_path) -> None:
    future = tmp_path / "future.sqlite3"
    connection = sqlite3.connect(future)
    connection.execute("PRAGMA user_version = 99")
    connection.close()

    with pytest.raises(ConversationPersistenceError, match="newer"):
        SQLiteConversationStore(future)

    incomplete = tmp_path / "incomplete.sqlite3"
    connection = sqlite3.connect(incomplete)
    connection.execute("CREATE TABLE conversations (conversation_id TEXT)")
    connection.execute("PRAGMA user_version = 1")
    connection.close()

    with pytest.raises(ConversationPersistenceError, match="missing columns"):
        SQLiteConversationStore(incomplete)


def test_conversation_backup_round_trip_includes_live_wal_data(tmp_path) -> None:
    database = tmp_path / "live.sqlite3"
    backup = tmp_path / "backup.sqlite3"
    restored_path = tmp_path / "restored.sqlite3"
    store = SQLiteConversationStore(database)
    expected = store.save(Conversation())

    assert store.backup_to(backup) == backup.resolve()
    store.close()
    restored = SQLiteConversationStore.restore_from_backup(backup, restored_path)

    assert restored.get(expected.conversation_id).conversation_id == (
        expected.conversation_id
    )
    restored.close()


def test_assurance_metadata_survives_sqlite_round_trip(tmp_path) -> None:
    store = SQLiteConversationStore(tmp_path / "conversations.sqlite3")
    conversation = Conversation()
    conversation.add_turn(
        ConversationTurn(
            conversation_id=conversation.conversation_id,
            role=MessageRole.ASSISTANT,
            content="Kanıtlı yanıt",
            metadata={
                "reasoning_level": "high",
                "assurance_level": "tool_verified",
                "uncertainty_summary": None,
            },
        )
    )

    store.save(conversation)
    loaded = store.get(conversation.conversation_id)
    store.close()

    assert loaded.turns[0].metadata == {
        "reasoning_level": "high",
        "assurance_level": "tool_verified",
        "uncertainty_summary": None,
    }


def test_default_state_migration_copies_legacy_sqlite_without_deleting_it(
    monkeypatch,
    tmp_path,
) -> None:
    legacy_directory = tmp_path / "legacy"
    state_directory = tmp_path / "state"
    legacy_directory.mkdir()
    legacy = legacy_directory / "jarvis_conversations.sqlite3"
    store = SQLiteConversationStore(legacy)
    expected = store.save(Conversation())
    store.close()
    destination = state_directory / legacy.name
    monkeypatch.setattr(paths, "project_data_directory", lambda: legacy_directory)
    monkeypatch.setattr(paths, "default_state_directory", lambda: state_directory)

    assert paths.migrate_default_sqlite(destination, legacy.name) is True
    assert legacy.is_file()
    migrated = SQLiteConversationStore(destination)
    assert migrated.get(expected.conversation_id).conversation_id == (
        expected.conversation_id
    )
    migrated.close()
    assert paths.migrate_default_sqlite(destination, legacy.name) is False
