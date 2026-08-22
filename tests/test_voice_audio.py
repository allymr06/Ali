from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.voice.audio import SoundDeviceAudioInput, WindowsWaveAudioOutput
from app.voice.errors import VoiceConfigurationError, VoiceDeviceError
from app.voice.models import AudioEncoding, SynthesizedSpeech


class FakeStream:
    def __init__(self, *, overflow=False) -> None:
        self.overflow = overflow
        self.started = False
        self.stopped = False
        self.closed = False
        self.read_sizes = []

    def start(self):
        self.started = True

    def read(self, frames):
        self.read_sizes.append(frames)
        return b"\x01\x02" * frames, self.overflow

    def stop(self):
        self.stopped = True

    def close(self):
        self.closed = True


class FakeSoundDevice:
    def __init__(self, stream=None) -> None:
        self.stream = stream or FakeStream()
        self.default = SimpleNamespace(device=(1, 2))
        self.stream_arguments = None

    def query_devices(self):
        return [
            {"name": "Output", "max_input_channels": 0},
            {
                "name": "Microphone",
                "max_input_channels": 2,
                "default_samplerate": 48_000,
            },
        ]

    def RawInputStream(self, **kwargs):
        self.stream_arguments = kwargs
        return self.stream


def test_sounddevice_input_lists_only_microphones() -> None:
    adapter = SoundDeviceAudioInput(module=FakeSoundDevice())

    devices = adapter.list_devices()

    assert len(devices) == 1
    assert devices[0].device_id == "1"
    assert devices[0].is_default is True
    assert devices[0].channels == 2


@pytest.mark.asyncio
async def test_sounddevice_input_captures_bounded_pcm_and_closes_stream() -> None:
    module = FakeSoundDevice()
    adapter = SoundDeviceAudioInput(
        sample_rate=8_000,
        channels=1,
        device_id="1",
        chunk_milliseconds=50,
        module=module,
    )

    capture = await adapter.capture(max_duration_seconds=0.01)

    assert len(capture.data) == 160
    assert capture.duration_seconds == pytest.approx(0.01)
    assert module.stream_arguments["device"] == 1
    assert module.stream.started is True
    assert module.stream.stopped is True
    assert module.stream.closed is True


@pytest.mark.asyncio
async def test_sounddevice_overflow_is_classified_and_stream_is_closed() -> None:
    stream = FakeStream(overflow=True)
    adapter = SoundDeviceAudioInput(
        sample_rate=8_000,
        module=FakeSoundDevice(stream),
    )

    with pytest.raises(VoiceDeviceError, match="overflowed"):
        await adapter.capture(max_duration_seconds=0.01)
    assert stream.stopped is True
    assert stream.closed is True


class FakeWinSound:
    # Mirrors the real winsound module: SND_MEMORY and SND_PURGE exist,
    # synchronous playback is the default and has no constant.
    SND_MEMORY = 1
    SND_PURGE = 4

    def __init__(self):
        self.calls = []

    def PlaySound(self, data, flags):
        self.calls.append((data, flags))


@pytest.mark.asyncio
async def test_windows_output_plays_wav_and_can_stop() -> None:
    module = FakeWinSound()
    output = WindowsWaveAudioOutput(module=module)
    speech = SynthesizedSpeech(
        b"RIFFaudio", AudioEncoding.WAV, "test", "model", "voice"
    )

    await output.play(speech)
    await output.stop()

    assert module.calls[0] == (b"RIFFaudio", FakeWinSound.SND_MEMORY)
    assert module.calls[1] == (None, 4)


@pytest.mark.asyncio
async def test_windows_output_rejects_non_wav_audio() -> None:
    output = WindowsWaveAudioOutput(module=FakeWinSound())
    speech = SynthesizedSpeech(
        b"audio", AudioEncoding.MP3, "test", "model", "voice"
    )

    with pytest.raises(VoiceConfigurationError, match="requires WAV"):
        await output.play(speech)
