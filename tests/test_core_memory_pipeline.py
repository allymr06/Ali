from __future__ import annotations

import asyncio

from app.core.engine import CoreEngine
from app.core.models import Request
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
            Request("HATIRLA: Python öğreniyorum")
        )
    )

    assert response.metadata["memory_decision"] is True
    assert memory_manager.count() == 1

    memories = memory_manager.active()

    assert memories[0].content == "HATIRLA: Python öğreniyorum"


def test_engine_does_not_remember_normal_request() -> None:
    engine, memory_manager = create_engine()

    response = asyncio.run(
        engine.handle(
            Request("Merhaba JARVIS")
        )
    )

    assert response.metadata["memory_decision"] is False
    assert memory_manager.count() == 0