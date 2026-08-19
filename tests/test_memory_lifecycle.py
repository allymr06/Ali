from __future__ import annotations

from app.memory.in_memory import InMemoryStore
from app.memory.manager import MemoryManager
from app.memory.models import MemoryType


def create_manager() -> MemoryManager:
    return MemoryManager(InMemoryStore())


def test_recall_touches_memory() -> None:
    manager = create_manager()

    memory = manager.remember("Python öğreniyorum")

    assert memory.last_used_at is None

    results = manager.recall("Python")

    assert results[0] is memory
    assert memory.last_used_at is not None


def test_update_changes_existing_memory() -> None:
    manager = create_manager()

    memory = manager.remember("Python öğreniyorum")

    updated = manager.update(
        memory.memory_id,
        "Rust öğreniyorum",
        importance=0.9,
    )

    assert updated is memory
    assert memory.content == "Rust öğreniyorum"
    assert memory.importance == 0.9


def test_forget_deactivates_memory() -> None:
    manager = create_manager()

    memory = manager.remember("Geçici bilgi")

    forgotten = manager.forget(memory.memory_id)

    assert forgotten is memory
    assert memory.active is False
    assert len(manager.active()) == 0


def test_supersede_replaces_old_memory() -> None:
    manager = create_manager()

    old_memory = manager.remember(
        "Python öğreniyorum",
        memory_type=MemoryType.GOAL,
    )

    new_memory = manager.supersede(
        old_memory.memory_id,
        "Rust öğreniyorum",
    )

    assert old_memory.active is False
    assert old_memory.superseded_by == new_memory.memory_id

    assert new_memory.active is True
    assert new_memory.supersedes == old_memory.memory_id
    assert new_memory.content == "Rust öğreniyorum"


def test_active_returns_only_active_memories() -> None:
    manager = create_manager()

    active_memory = manager.remember("Aktif bilgi")
    forgotten_memory = manager.remember("Eski bilgi")

    manager.forget(forgotten_memory.memory_id)

    active = manager.active()

    assert active_memory in active
    assert forgotten_memory not in active