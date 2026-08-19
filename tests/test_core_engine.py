from __future__ import annotations

import asyncio

from app.core.engine import CoreEngine
from app.core.models import Context, Request
from app.providers.mock import MockProvider
from app.providers.registry import ProviderRegistry


def create_engine() -> CoreEngine:
    registry = ProviderRegistry()
    registry.register(MockProvider(), make_default=True)
    return CoreEngine(registry)


def test_core_engine_handles_request():
    engine = create_engine()
    request = Request("Merhaba JARVIS")

    response = asyncio.run(engine.handle(request))

    assert response.text == "Mock yanıtı: Merhaba JARVIS"
    assert response.request_id == request.request_id
    assert response.metadata["provider"] == "mock"
    assert response.metadata["model"] == "mock-model"


def test_core_engine_creates_context_when_missing():
    engine = create_engine()
    request = Request("Test")

    response = asyncio.run(engine.handle(request))

    assert response.request_id == request.request_id
    assert response.text == "Mock yanıtı: Test"


def test_core_engine_accepts_existing_context():
    engine = create_engine()
    request = Request("Mevcut context testi")
    context = Context()

    response = asyncio.run(engine.handle(request, context))

    assert response.text == "Mock yanıtı: Mevcut context testi"


def test_core_engine_uses_registered_default_provider():
    registry = ProviderRegistry()
    provider = MockProvider()
    registry.register(provider, make_default=True)

    engine = CoreEngine(registry)
    request = Request("Provider testi")

    response = asyncio.run(engine.handle(request))

    assert response.metadata["provider"] == "mock"
    assert response.metadata["model"] == "mock-model"