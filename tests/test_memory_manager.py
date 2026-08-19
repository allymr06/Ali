from __future__ import annotations

import pytest

from app.memory.in_memory import InMemoryStore
from app.memory.manager import MemoryManager
from app.memory.models import MemoryType


@pytest.fixture
def manager() -> MemoryManager:
    return MemoryManager(InMemoryStore())


def test_manager_can_remember(manager: MemoryManager) -> None:
    memory = manager.remember(
        "Ali Python öğreniyor",
        importance=0.9,
    )

    assert memory.content == "Ali Python öğreniyor"
    assert memory.importance == 0.9
    assert manager.count() == 1


def test_manager_can_remember_typed_memory(
    manager: MemoryManager,
) -> None:
    memory = manager.remember(
        "Kullanıcı kısa cevapları tercih ediyor",
        memory_type=MemoryType.PREFERENCE,
    )

    assert memory.memory_type is MemoryType.PREFERENCE


def test_manager_can_recall(manager: MemoryManager) -> None:
    manager.remember("Python projesi")
    manager.remember("JARVIS projesi")

    results = manager.recall("Python")

    assert len(results) == 1
    assert results[0].content == "Python projesi"


def test_manager_can_forget(manager: MemoryManager) -> None:
    memory = manager.remember("Silinecek bilgi")

    deleted = manager.forget(memory.memory_id)

    assert deleted is memory
    assert manager.count() == 0


def test_manager_can_get_specific_memory(
    manager: MemoryManager,
) -> None:
    memory = manager.remember("Önemli bilgi")

    result = manager.get(memory.memory_id)

    assert result is memory


def test_manager_can_list_all_memories(
    manager: MemoryManager,
) -> None:
    manager.remember("Birinci")
    manager.remember("İkinci")

    memories = manager.all()

    assert len(memories) == 2


def test_manager_passes_search_limit(
    manager: MemoryManager,
) -> None:
    for index in range(5):
        manager.remember(f"JARVIS memory {index}")

    results = manager.recall("JARVIS", limit=2)

    assert len(results) == 2


def test_manager_rejects_invalid_importance(
    manager: MemoryManager,
) -> None:
    with pytest.raises(ValueError):
        manager.remember(
            "Geçersiz önem",
            importance=2.0,
        )
def test_manager_does_not_duplicate_identical_memory() -> None:
    manager = MemoryManager(
        InMemoryStore()
    )

    first = manager.remember(
        "Ali Python öğreniyor",
    )

    second = manager.remember(
        "Ali Python öğreniyor",
    )

    assert first.memory_id == second.memory_id
    assert manager.count() == 1
