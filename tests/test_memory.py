from __future__ import annotations

import pytest

from app.memory.in_memory import InMemoryStore
from app.memory.models import MemoryEntry, MemoryType


def test_memory_entry_defaults() -> None:
    memory = MemoryEntry("JARVIS test memory")

    assert memory.content == "JARVIS test memory"
    assert memory.memory_type is MemoryType.FACT
    assert memory.importance == 0.5


def test_memory_entry_normalizes_content() -> None:
    memory = MemoryEntry("   hello JARVIS   ")

    assert memory.content == "hello JARVIS"


def test_memory_entry_rejects_empty_content() -> None:
    with pytest.raises(ValueError):
        MemoryEntry("   ")


def test_memory_entry_rejects_invalid_importance() -> None:
    with pytest.raises(ValueError):
        MemoryEntry("test", importance=1.5)


def test_memory_entry_can_update() -> None:
    memory = MemoryEntry("old")

    memory.update("new", importance=0.9)

    assert memory.content == "new"
    assert memory.importance == 0.9


def test_memory_store_can_save_and_get() -> None:
    store = InMemoryStore()
    memory = MemoryEntry("remember this")

    store.save(memory)

    assert len(store) == 1
    assert store.get(memory.memory_id) is memory


def test_memory_store_can_delete() -> None:
    store = InMemoryStore()
    memory = MemoryEntry("delete me")

    store.save(memory)
    deleted = store.delete(memory.memory_id)

    assert deleted is memory
    assert len(store) == 0


def test_memory_store_searches_content() -> None:
    store = InMemoryStore()

    store.save(MemoryEntry("Ali Python öğreniyor"))
    store.save(MemoryEntry("JARVIS bir yapay zeka asistanıdır"))

    results = store.search("python")

    assert len(results) == 1
    assert results[0].content == "Ali Python öğreniyor"


def test_memory_store_search_is_case_insensitive() -> None:
    store = InMemoryStore()

    store.save(MemoryEntry("JARVIS CORE"))

    results = store.search("jarvis")

    assert len(results) == 1


def test_memory_store_search_respects_limit() -> None:
    store = InMemoryStore()

    for index in range(5):
        store.save(MemoryEntry(f"JARVIS memory {index}"))

    results = store.search("JARVIS", limit=2)

    assert len(results) == 2


def test_memory_store_returns_empty_for_unknown_query() -> None:
    store = InMemoryStore()

    store.save(MemoryEntry("hello"))

    assert store.search("does-not-exist") == []


def test_memory_store_rejects_invalid_limit() -> None:
    store = InMemoryStore()

    with pytest.raises(ValueError):
        store.search("test", limit=0)


def test_memory_store_get_unknown_memory_raises() -> None:
    store = InMemoryStore()

    with pytest.raises(KeyError):
        store.get(MemoryEntry("temporary").memory_id)


def test_memory_store_delete_unknown_memory_raises() -> None:
    store = InMemoryStore()

    with pytest.raises(KeyError):
        store.delete(MemoryEntry("temporary").memory_id)