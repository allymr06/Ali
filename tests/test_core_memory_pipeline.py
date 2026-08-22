from __future__ import annotations

import asyncio

from app.core.engine import CoreEngine
from app.core.models import Context, Request
from app.memory.in_memory import InMemoryStore
from app.memory.manager import MemoryManager
from app.providers.mock import MockProvider
from app.providers.registry import ProviderRegistry


def create_engine() -> tuple[CoreEngine, MemoryManager]:
    registry = ProviderRegistry()
    registry.register(
        MockProvider(),
        make_default=True,
    )

    memory_manager = MemoryManager(
        InMemoryStore()
    )

    engine = CoreEngine(
        registry,
        memory_manager,
    )

    return engine, memory_manager


def test_engine_remembers_memory_worthy_request() -> None:
    engine, memory_manager = create_engine()

    response = asyncio.run(
        engine.handle(
            Request("HATIRLA: Python Г¶Дџreniyorum")
        )
    )

    assert response.metadata["memory_decision"] is True
    assert memory_manager.count() == 1

    memories = memory_manager.active()

    assert memories[0].content == "Python Г¶Дџreniyorum"


def test_engine_does_not_remember_normal_request() -> None:
    engine, memory_manager = create_engine()

    response = asyncio.run(
        engine.handle(
            Request("Merhaba JARVIS")
        )
    )

    assert response.metadata["memory_decision"] is False
    assert memory_manager.count() == 0
def test_engine_injects_recalled_memories_into_context() -> None:
    registry = ProviderRegistry()
    provider = MockProvider()

    registry.register(
        provider,
        make_default=True,
    )

    memory_manager = MemoryManager(
        InMemoryStore()
    )

    engine = CoreEngine(
        registry,
        memory_manager,
    )

    context = Context()

    memory_manager.remember(
        "Ali Python Г¶Дџreniyor",
    )

    asyncio.run(
        engine.handle(
            Request("Python"),
            context,
        )
    )

    assert "Ali Python Г¶Дџreniyor" in context.memories

def test_engine_recalled_memory_is_available_during_generation() -> None:
    registry = ProviderRegistry()
    provider = MockProvider()

    registry.register(
        provider,
        make_default=True,
    )

    memory_manager = MemoryManager(
        InMemoryStore()
    )

    engine = CoreEngine(
        registry,
        memory_manager,
    )

    context = Context()

    memory_manager.remember(
        "Ali Python öğreniyor",
    )

    asyncio.run(
        engine.handle(
            Request("Python hakkında konuşalım"),
            context,
        )
    )

    assert context.memories == ["Ali Python öğreniyor"]
    assert context.values["memory_provenance"][0]["source"] == "user"
    assert context.values["memory_provenance"][0]["freshness"] == "current"
