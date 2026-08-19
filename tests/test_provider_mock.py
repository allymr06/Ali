from __future__ import annotations

import asyncio

from app.core.models import Context, Request
from app.providers.mock import MockProvider


def test_mock_provider_identity() -> None:
    provider = MockProvider()

    assert provider.name == "mock"
    assert provider.capabilities.text is True
    assert provider.capabilities.streaming is False


def test_mock_provider_generate() -> None:
    provider = MockProvider()
    request = Request("Merhaba JARVIS")
    context = Context()

    response = asyncio.run(
        provider.generate(request, context)
    )

    assert response.provider == "mock"
    assert response.model == "mock-model"
    assert response.finish_reason == "stop"
    assert response.text == "Mock yanıtı: Merhaba JARVIS"