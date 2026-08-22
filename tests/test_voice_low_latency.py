from __future__ import annotations

import base64
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.config.provider_preferences import (
    ProviderPreferencesStore,
)
from app.config.settings import Settings
from app.core.models import Response
from app.ui.api_settings import APISettingsService
from app.voice.audio import (
    SoundDeviceAudioInput,
)
from app.voice.errors import (
    VoiceDeviceError,
    VoiceNoSpeech,
)
from app.voice.gemini import (
    GeminiSpeechRecognizer,
    GeminiSpeechSynthesizer,
)
from app.voice.models import (
    AudioCapture,
    AudioDevice,
    AudioDeviceKind,
    AudioEncoding,
    VoiceSessionResult,
    VoiceSessionState,
)
from app.voice.service import VoiceService
from app.voice.session import VoiceSession
from app.voice.wake import (
    TextWakeWordDetector,
)


def pcm(
    value: int,
    frames: int,
) -> bytes:
    sample = int(value).to_bytes(
        2,
        "little",
        signed=True,
    )

    return sample * frames


class ScriptedStream:
    def __init__(
        self,
        chunks,
    ) -> None:
        self.chunks = list(chunks)
        self.started = False
        self.stopped = False
        self.closed = False
        self.read_count = 0

    def start(self):
        self.started = True

    def read(self, frames):
        self.read_count += 1

        if self.chunks:
            data = self.chunks.pop(0)
        else:
            data = pcm(
                0,
                frames,
            )

        expected = frames * 2

        if len(data) < expected:
            data += b"\x00" * (
                expected - len(data)
            )

        return data[:expected], False

    def stop(self):
        self.stopped = True

    def close(self):
        self.closed = True


class FakeSoundDevice:
    def __init__(
        self,
        stream,
    ) -> None:
        self.stream = stream
        self.default = (
            SimpleNamespace(
                device=(0, 0)
            )
        )

    def query_devices(self):
        return [
            {
                "name": "Mic",
                "max_input_channels": 1,
                "default_samplerate": 8000,
            }
        ]

    def RawInputStream(
        self,
        **_kwargs,
    ):
        return self.stream


@pytest.mark.asyncio
async def test_vad_stops_after_trailing_silence(
) -> None:
    frames = 400

    stream = ScriptedStream(
        [
            pcm(2000, frames),
            pcm(0, frames),
            pcm(0, frames),
            pcm(0, frames),
        ]
    )

    adapter = SoundDeviceAudioInput(
        sample_rate=8000,
        channels=1,
        chunk_milliseconds=50,
        silence_threshold_rms=500,
        min_speech_seconds=0.05,
        trailing_silence_seconds=0.10,
        start_timeout_seconds=1.0,
        module=FakeSoundDevice(
            stream
        ),
    )

    capture = await adapter.capture(
        max_duration_seconds=5.0
    )

    assert (
        capture.duration_seconds
        == pytest.approx(
            0.15
        )
    )

    assert stream.read_count == 3
    assert stream.stopped is True
    assert stream.closed is True


@pytest.mark.asyncio
async def test_vad_exits_when_user_never_speaks(
) -> None:
    frames = 400

    stream = ScriptedStream(
        [
            pcm(0, frames),
            pcm(0, frames),
            pcm(0, frames),
        ]
    )

    adapter = SoundDeviceAudioInput(
        sample_rate=8000,
        channels=1,
        chunk_milliseconds=50,
        silence_threshold_rms=500,
        start_timeout_seconds=0.10,
        module=FakeSoundDevice(
            stream
        ),
    )

    with pytest.raises(
        VoiceNoSpeech
    ):
        await adapter.capture(
            max_duration_seconds=5.0
        )

    assert stream.read_count == 2
    assert stream.stopped is True
    assert stream.closed is True


class FakeModels:
    def __init__(
        self,
        responses,
    ) -> None:
        self.responses = list(
            responses
        )
        self.calls = []

    def generate_content(
        self,
        **kwargs,
    ):
        self.calls.append(
            kwargs
        )

        return self.responses.pop(0)


class FakeGeminiClient:
    def __init__(
        self,
        responses,
    ) -> None:
        self.models = FakeModels(
            responses
        )


@pytest.mark.asyncio
async def test_gemini_recognizer_sends_inline_wav_audio() -> None:
    client = FakeGeminiClient(
        [SimpleNamespace(text=" Merhaba JARVIS ")]
    )

    recognizer = GeminiSpeechRecognizer(
        Settings(),
        client=client,
    )

    result = await recognizer.transcribe(
        AudioCapture(bytearray(b"\x00\x01" * 80)),
        language="tr",
    )

    assert result.text == "Merhaba JARVIS"
    assert result.provider == "gemini"

    call = client.models.calls[0]

    assert call["model"] == "gemini-3.7-flash"

    parts = call["contents"][0]["parts"]
    assert "Transcribe" in parts[0]["text"]

    audio = parts[1]["inline_data"]
    assert audio["mime_type"] == "audio/wav"
    assert bytes(audio["data"]).startswith(b"RIFF")

    afc = call["config"]["automatic_function_calling"]
    assert afc["disable"] is True


@pytest.mark.asyncio
async def test_gemini_tts_wraps_inline_pcm_into_wav() -> None:
    pcm_bytes = b"\x01\x02" * 2_000

    client = FakeGeminiClient(
        [
            SimpleNamespace(
                candidates=[
                    SimpleNamespace(
                        content=SimpleNamespace(
                            parts=[
                                SimpleNamespace(
                                    inline_data=SimpleNamespace(
                                        mime_type=(
                                            "audio/L16;codec=pcm;rate=24000"
                                        ),
                                        data=pcm_bytes,
                                    )
                                )
                            ]
                        )
                    )
                ]
            )
        ]
    )

    synthesizer = GeminiSpeechSynthesizer(
        Settings(),
        client=client,
    )

    result = await synthesizer.synthesize("Merhaba.")

    assert result.encoding is AudioEncoding.WAV
    assert result.provider == "gemini"
    assert result.data.startswith(b"RIFF")
    assert pcm_bytes in result.data

    import io
    import wave

    with wave.open(io.BytesIO(result.data)) as reader:
        assert reader.getframerate() == 24_000
        assert reader.getnchannels() == 1

    call = client.models.calls[0]

    assert call["model"] == "gemini-3.1-flash-tts-preview"
    assert call["config"]["response_modalities"] == ["AUDIO"]

    voice = call["config"]["speech_config"]["voice_config"][
        "prebuilt_voice_config"
    ]["voice_name"]
    assert voice == "Kore"


class DummyInput:
    def list_devices(self):
        return (
            AudioDevice(
                "mic",
                "Mic",
                AudioDeviceKind.INPUT,
            ),
        )


class DummyOutput:
    def list_devices(self):
        return (
            AudioDevice(
                "speaker",
                "Speaker",
                AudioDeviceKind.OUTPUT,
            ),
        )

    async def stop(self):
        return None


class ResultSession:
    def __init__(
        self,
        result,
    ) -> None:
        self.result = result
        self.state = (
            VoiceSessionState.IDLE
        )
        self.is_active = False
        self.last_capture = None

    async def run_once(
        self,
        _context=None,
    ):
        self.state = (
            self.result.state
        )
        return self.result

    async def interrupt(self):
        return False


@pytest.mark.asyncio
async def test_continuous_voice_recovers_from_transient_failure(
) -> None:
    outcomes = [
        VoiceSessionResult(
            uuid4(),
            VoiceSessionState.FAILED,
            error_code="provider",
        ),
        VoiceSessionResult(
            uuid4(),
            VoiceSessionState.COMPLETED,
            transcript="status",
            response_text="ready",
        ),
        VoiceSessionResult(
            uuid4(),
            VoiceSessionState.IGNORED,
        ),
    ]

    def factory():
        return ResultSession(
            outcomes.pop(0)
        )

    service = VoiceService(
        session_factory=factory,
        audio_input=DummyInput(),
        audio_output=DummyOutput(),
    )

    results = (
        await service.run_continuous(
            max_turns=10,
            max_consecutive_failures=2,
        )
    )

    assert [
        result.state
        for result
        in results
    ] == [
        VoiceSessionState.FAILED,
        VoiceSessionState.COMPLETED,
        VoiceSessionState.IGNORED,
    ]


class NoSpeechInput:
    def list_devices(self):
        return ()

    async def capture(
        self,
        *,
        max_duration_seconds,
        cancel_event=None,
    ):
        raise VoiceNoSpeech(
            "nothing"
        )


class SilentOutput:
    def list_devices(self):
        return ()

    async def play(
        self,
        speech,
        *,
        cancel_event=None,
    ):
        return None

    async def stop(self):
        return None


class NeverRecognizer:
    async def transcribe(
        self,
        capture,
        *,
        language=None,
    ):
        raise AssertionError(
            "Recognizer must not run."
        )


class NeverSynthesizer:
    async def synthesize(
        self,
        text,
    ):
        raise AssertionError(
            "Synthesizer must not run."
        )


class NeverEngine:
    async def handle(
        self,
        request,
        context=None,
        *,
        cancel_event=None,
    ):
        raise AssertionError(
            "Core must not run."
        )


@pytest.mark.asyncio
async def test_no_speech_is_ignored_not_failed(
) -> None:
    session = VoiceSession(
        engine=NeverEngine(),
        audio_input=NoSpeechInput(),
        audio_output=SilentOutput(),
        recognizer=NeverRecognizer(),
        synthesizer=NeverSynthesizer(),
        wake_word_detector=(
            TextWakeWordDetector(
                "jarvis"
            )
        ),
        max_recording_seconds=1,
        operation_timeout_seconds=1,
    )

    result = await session.run_once()

    assert (
        result.state
        is VoiceSessionState.IGNORED
    )

    assert (
        result.metadata[
            "ignored_reason"
        ]
        == "no_speech"
    )

    assert (
        result.metadata[
            "total_latency_seconds"
        ]
        >= 0
    )


class MemoryCredentials:
    def __init__(
        self,
    ) -> None:
        self.value = None

    def read(self):
        return self.value

    def write(
        self,
        secret,
    ):
        self.value = secret

    def delete(self):
        existed = (
            self.value is not None
        )
        self.value = None
        return existed


def test_real_provider_auto_enables_voice_unless_environment_overrides(
    tmp_path,
    monkeypatch,
) -> None:
    for name in (
        "JARVIS_DEFAULT_PROVIDER",
        "JARVIS_GEMINI_MODEL",
        "JARVIS_GEMINI_API_KEY",
        "JARVIS_VOICE_ENABLED",
    ):
        monkeypatch.delenv(
            name,
            raising=False,
        )

    credentials = (
        MemoryCredentials()
    )

    service = APISettingsService(
        credential_stores={
            "gemini": credentials,
            "openai": (
                MemoryCredentials()
            ),
        },
        preferences=(
            ProviderPreferencesStore(
                tmp_path
                / "settings.json"
            )
        ),
    )

    service.save(
        "gemini",
        "gemini-3.7-flash",
        "secret",
    )

    settings = (
        service
        .build_runtime_settings()
    )

    assert (
        settings.voice_enabled
        is True
    )

    monkeypatch.setenv(
        "JARVIS_VOICE_ENABLED",
        "false",
    )

    settings = (
        service
        .build_runtime_settings()
    )

    assert (
        settings.voice_enabled
        is False
    )


@pytest.mark.parametrize(
    "field,value",
    [
        (
            "voice_silence_threshold_rms",
            0,
        ),
        (
            "voice_min_speech_seconds",
            0,
        ),
        (
            "voice_trailing_silence_seconds",
            0,
        ),
        (
            "voice_start_timeout_seconds",
            0,
        ),
    ],
)
def test_invalid_vad_settings_fail_closed(
    field,
    value,
) -> None:
    kwargs = {
        field: value,
    }

    with pytest.raises(
        ValueError
    ):
        Settings(**kwargs)
