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


def test_engine_remembers_preferences_without_explicit_prefix() -> None:
    """The policy's preference path must actually write: it used to be
    dead code because the engine also required an analyzer candidate."""
    engine, memory_manager = create_engine()

    response = asyncio.run(
        engine.handle(
            Request("Kahvemi sütsüz istiyorum, öyle tercih ederim")
        )
    )

    assert response.metadata["memory_decision"] is True
    assert response.metadata["memory_saved"] is True
    assert memory_manager.count() == 1


def test_engine_remembers_identity_statements() -> None:
    engine, memory_manager = create_engine()

    asyncio.run(
        engine.handle(
            Request("Benim adım Ali ve 20 yaşındayım")
        )
    )

    memories = memory_manager.active()
    assert len(memories) == 1
    assert "Ali" in memories[0].content


def test_engine_remembers_unutma_prefix() -> None:
    engine, memory_manager = create_engine()

    asyncio.run(
        engine.handle(
            Request("Unutma: yarın saat 15'te toplantım var")
        )
    )

    memories = memory_manager.active()
    assert len(memories) == 1
    assert "toplantım" in memories[0].content


def test_auto_extractor_runs_for_unremembered_turns() -> None:
    """Voice or text turns with no deterministic signal still get a
    model-assisted memory pass in the background."""
    from app.memory.auto import AutoMemoryExtractor

    class FakeGateway:
        def __init__(self, answer: str):
            self.answer = answer
            self.calls = []

        async def generate(self, request, context, **kwargs):
            self.calls.append((request.text, kwargs))
            return type("R", (), {"text": self.answer})()

    memory_manager = MemoryManager(InMemoryStore())
    gateway = FakeGateway("Kullanıcı rock müzik seviyor.")
    extractor = AutoMemoryExtractor(
        provider_gateway=gateway,
        memory_manager=memory_manager,
        model="test-lite",
    )

    stored = asyncio.run(
        extractor.extract("bana biraz rock çalar mısın", "req-1")
    )

    assert stored is True
    assert memory_manager.count() == 1
    assert memory_manager.active()[0].content == (
        "Kullanıcı rock müzik seviyor."
    )
    assert gateway.calls[0][1]["model"] == "test-lite"

    # Same fact again: deduplicated, not stored twice.
    stored_again = asyncio.run(
        extractor.extract("rock müzik çok iyiydi", "req-2")
    )
    assert stored_again is False
    assert memory_manager.count() == 1


def test_auto_extractor_ignores_transient_requests() -> None:
    from app.memory.auto import AutoMemoryExtractor

    class NoGateway:
        async def generate(self, request, context, **kwargs):
            return type("R", (), {"text": "YOK"})()

    memory_manager = MemoryManager(InMemoryStore())
    extractor = AutoMemoryExtractor(
        provider_gateway=NoGateway(),
        memory_manager=memory_manager,
        model="test-lite",
    )

    stored = asyncio.run(
        extractor.extract("şu şarkıyı bir sonrakine geçir", "req-3")
    )

    assert stored is False
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
