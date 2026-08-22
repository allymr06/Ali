from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.config.settings import Settings
from app.voice.errors import VoiceConfigurationError, VoiceProviderError
from app.voice.models import AudioCapture, AudioEncoding
from app.voice.providers import OpenAISpeechRecognizer, OpenAISpeechSynthesizer


class RecordingEndpoint:
    def __init__(self, response=None, error: Exception | None = None) -> None:
        self.response = response
        self.error = error
        self.calls = []

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return self.response


def make_client(*, transcription=None, speech=None):
    transcriptions = RecordingEndpoint(transcription)
    speech_endpoint = RecordingEndpoint(speech)
    client = SimpleNamespace(
        audio=SimpleNamespace(
            transcriptions=transcriptions,
            speech=speech_endpoint,
        )
    )
    return client, transcriptions, speech_endpoint


@pytest.mark.asyncio
async def test_openai_recognizer_sends_wav_with_provenance() -> None:
    client, endpoint, _ = make_client(transcription=SimpleNamespace(text=" Hello "))
    recognizer = OpenAISpeechRecognizer(Settings(), client=client)

    result = await recognizer.transcribe(
        AudioCapture(bytearray(b"\x00\x00" * 20)),
        language="en",
    )

    assert result.text == "Hello"
    assert result.provider == "openai"
    assert result.model == "gpt-4o-mini-transcribe"
    call = endpoint.calls[0]
    assert call["language"] == "en"
    assert call["response_format"] == "json"
    assert call["file"][0].endswith(".wav")
    assert call["file"][2] == "audio/wav"


@pytest.mark.asyncio
async def test_openai_synthesizer_requests_wav_and_reads_content() -> None:
    client, _, endpoint = make_client(speech=SimpleNamespace(content=b"RIFFaudio"))
    settings = Settings(voice_tts_instructions="Speak clearly.")
    synthesizer = OpenAISpeechSynthesizer(settings, client=client)

    result = await synthesizer.synthesize(" Hello ")

    assert result.data == b"RIFFaudio"
    assert result.encoding is AudioEncoding.WAV
    assert result.provider == "openai"
    assert endpoint.calls[0] == {
        "model": "gpt-4o-mini-tts",
        "voice": "alloy",
        "input": "Hello",
        "response_format": "wav",
        "instructions": "Speak clearly.",
    }


@pytest.mark.asyncio
async def test_openai_audio_adapters_require_configuration() -> None:
    settings = Settings(api_key=None)

    with pytest.raises(VoiceConfigurationError):
        await OpenAISpeechRecognizer(settings).transcribe(
            AudioCapture(bytearray(b"\x00\x00"))
        )
    with pytest.raises(VoiceConfigurationError):
        await OpenAISpeechSynthesizer(settings).synthesize("hello")


@pytest.mark.asyncio
async def test_provider_failures_are_classified_without_leaking_details() -> None:
    client, _, _ = make_client(transcription=SimpleNamespace(text=""))
    with pytest.raises(VoiceProviderError, match="empty transcription"):
        await OpenAISpeechRecognizer(Settings(), client=client).transcribe(
            AudioCapture(bytearray(b"\x00\x00"))
        )

    failing = RecordingEndpoint(error=RuntimeError("secret upstream detail"))
    client.audio.transcriptions = failing
    with pytest.raises(VoiceProviderError, match="provider failed") as caught:
        await OpenAISpeechRecognizer(Settings(), client=client).transcribe(
            AudioCapture(bytearray(b"\x00\x00"))
        )
    assert "secret" not in str(caught.value)


@pytest.mark.asyncio
async def test_synthesis_enforces_text_and_audio_limits() -> None:
    client, _, _ = make_client(speech=SimpleNamespace(content=b"12345"))
    synthesizer = OpenAISpeechSynthesizer(
        Settings(voice_max_tts_characters=4, voice_max_audio_bytes=4),
        client=client,
    )

    with pytest.raises(ValueError, match="character limit"):
        await synthesizer.synthesize("12345")
    with pytest.raises(VoiceProviderError, match="size limit"):
        await synthesizer.synthesize("1234")
