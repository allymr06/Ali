from __future__ import annotations

from app.memory.models import MemoryEntry, MemorySource, MemoryType


def test_memory_defaults_are_valid() -> None:
    memory = MemoryEntry("Ali Python öğreniyor")

    assert memory.memory_type is MemoryType.FACT
    assert memory.importance == 0.5
    assert memory.confidence == 1.0
    assert memory.source is MemorySource.USER
    assert memory.active is True
    assert memory.supersedes is None
    assert memory.superseded_by is None


def test_memory_supports_different_types() -> None:
    memory = MemoryEntry(
        "Kısa cevapları tercih ediyor",
        memory_type=MemoryType.PREFERENCE,
    )

    assert memory.memory_type is MemoryType.PREFERENCE


def test_memory_supports_confidence() -> None:
    memory = MemoryEntry(
        "Ali Rust öğreniyor",
        confidence=0.75,
    )

    assert memory.confidence == 0.75


def test_memory_rejects_invalid_importance() -> None:
    try:
        MemoryEntry("Test", importance=1.1)
        assert False
    except ValueError:
        pass


def test_memory_rejects_invalid_confidence() -> None:
    try:
        MemoryEntry("Test", confidence=-0.1)
        assert False
    except ValueError:
        pass


def test_memory_rejects_empty_content() -> None:
    try:
        MemoryEntry("   ")
        assert False
    except ValueError:
        pass


def test_memory_can_be_touched() -> None:
    memory = MemoryEntry("Test")

    assert memory.last_used_at is None

    memory.touch()

    assert memory.last_used_at is not None


def test_memory_can_update_content() -> None:
    memory = MemoryEntry("Python öğreniyor")

    memory.update_content("Rust öğreniyor")

    assert memory.content == "Rust öğreniyor"


def test_memory_can_be_deactivated() -> None:
    memory = MemoryEntry("Eski bilgi")

    memory.deactivate()

    assert memory.active is False


def test_memory_can_supersede_another_memory() -> None:
    old_memory = MemoryEntry("Python öğreniyor")
    new_memory = MemoryEntry("Rust öğreniyor")

    old_memory.supersede(new_memory)

    assert old_memory.active is False
    assert old_memory.superseded_by == new_memory.memory_id
    assert new_memory.supersedes == old_memory.memory_id