from __future__ import annotations

import asyncio

from app.core.engine import CoreEngine
from app.core.models import Context, Request
from app.memory.in_memory import InMemoryStore
from app.memory.manager import MemoryManager
from app.providers.mock import MockProvider
from app.providers.registry import ProviderRegistry


def create_engine() -> CoreEngine:
    registry = ProviderRegistry()
    registry.register(
        MockProvider(),
        make_default=True,
    )

    memory_manager = MemoryManager(
        InMemoryStore()
    )

    return CoreEngine(
        registry,
        memory_manager,
    )


def test_core_engine_handles_request() -> None:
    engine = create_engine()
    request = Request("Merhaba JARVIS")

    response = asyncio.run(
        engine.handle(request)
    )

    assert response.text == "Mock yanıtı: Merhaba JARVIS"
    assert response.metadata["provider"] == "mock"


def test_core_engine_creates_context_when_missing() -> None:
    engine = create_engine()
    request = Request("Test")

    response = asyncio.run(
        engine.handle(request)
    )

    assert response.request_id == request.request_id
    assert response.text == "Mock yanıtı: Test"


def test_core_engine_accepts_existing_context() -> None:
    engine = create_engine()
    request = Request("Mevcut context testi")
    context = Context()

    response = asyncio.run(
        engine.handle(request, context)
    )

    assert response.text == "Mock yanıtı: Mevcut context testi"


def test_core_engine_uses_registered_default_provider() -> None:
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

    request = Request("Provider testi")

    response = asyncio.run(
        engine.handle(request)
    )

    assert response.metadata["provider"] == "mock"