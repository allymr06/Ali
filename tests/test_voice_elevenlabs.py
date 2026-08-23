from __future__ import annotations

import wave
import io

import pytest

from app.config.settings import Settings
from app.voice.elevenlabs import ElevenLabsSpeechSynthesizer
from app.voice.errors import (
    VoiceConfigurationError,
    VoiceProviderError,
)
from app.voice.registry import create_default_voice_provider_registry


class FakeResponse:
    def __init__(self, status_code: int, content: bytes = b""):
        self.status_code = status_code
        self.content = content


class FakeHttpClient:
    def __init__(self, response: FakeResponse):
        self.response = response
        self.requests = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def post(self, url, **kwargs):
        self.requests.append((url, kwargs))
        return self.response


def _synthesizer(response: FakeResponse, **overrides):
    settings = Settings(
        voice_elevenlabs_api_key=overrides.pop("api_key", "test-key"),
        **overrides,
    )
    client = FakeHttpClient(response)
    return (
        ElevenLabsSpeechSynthesizer(
            settings, http_client_factory=lambda **_: client
        ),
        client,
    )


@pytest.mark.asyncio
async def test_elevenlabs_wraps_pcm_into_wav() -> None:
    pcm = b"\x00\x01" * 2_000
    synthesizer, client = _synthesizer(FakeResponse(200, pcm))

    speech = await synthesizer.synthesize("Merhaba efendim.")

    assert speech.provider == "elevenlabs"
    assert speech.data.startswith(b"RIFF")
    with wave.open(io.BytesIO(speech.data), "rb") as wav:
        assert wav.getframerate() == 22_050
        assert wav.getnchannels() == 1
    url, kwargs = client.requests[0]
    assert "text-to-speech" in url
    assert kwargs["headers"]["xi-api-key"] == "test-key"


@pytest.mark.asyncio
async def test_elevenlabs_bad_key_is_configuration_error() -> None:
    synthesizer, _ = _synthesizer(FakeResponse(401))
    with pytest.raises(VoiceConfigurationError):
        await synthesizer.synthesize("test")


@pytest.mark.asyncio
async def test_elevenlabs_quota_is_provider_error() -> None:
    synthesizer, _ = _synthesizer(FakeResponse(429))
    with pytest.raises(VoiceProviderError):
        await synthesizer.synthesize("test")


@pytest.mark.asyncio
async def test_elevenlabs_requires_api_key() -> None:
    synthesizer, _ = _synthesizer(
        FakeResponse(200, b"xx"), api_key=None
    )
    with pytest.raises(VoiceConfigurationError):
        await synthesizer.synthesize("test")


def test_registry_offers_elevenlabs_synthesizer() -> None:
    registry = create_default_voice_provider_registry()
    assert registry.contains_synthesizer("elevenlabs")
    adapter = registry.create_synthesizer(
        "elevenlabs",
        Settings(voice_elevenlabs_api_key="k"),
    )
    assert isinstance(adapter, ElevenLabsSpeechSynthesizer)
