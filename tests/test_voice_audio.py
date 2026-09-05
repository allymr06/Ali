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


@pytest.mark.asyncio
async def test_sounddevice_input_reports_levels_without_affecting_capture() -> None:
    module = FakeSoundDevice()
    adapter = SoundDeviceAudioInput(
        sample_rate=8_000,
        channels=1,
        device_id="1",
        chunk_milliseconds=50,
        vad_enabled=False,
        module=module,
    )
    levels = []
    adapter.level_callback = levels.append

    capture = await adapter.capture(max_duration_seconds=0.05)

    assert len(capture.data) == 800
    assert levels and all(0.0 <= level <= 1.0 for level in levels)

    def broken(level: float) -> None:
        raise RuntimeError("observer failure")

    adapter.level_callback = broken
    capture = await adapter.capture(max_duration_seconds=0.05)
    assert len(capture.data) == 800


def test_voice_service_forwards_the_level_observer_to_the_input() -> None:
    from app.voice.service import VoiceService

    class Input:
        def __init__(self) -> None:
            self.level_callback = None

        def list_devices(self):
            return ()

        async def capture(self, **_kwargs):
            raise AssertionError("not captured in this test")

    audio_input = Input()
    service = VoiceService(
        session_factory=lambda: None,
        audio_input=audio_input,
        audio_output=SimpleNamespace(),
    )
    observer = lambda level: None  # noqa: E731
    service.level_callback = observer
    assert audio_input.level_callback is observer
    assert service.level_callback is observer
    service.level_callback = None
    assert audio_input.level_callback is None

    plain = VoiceService(
        session_factory=lambda: None,
        audio_input=SimpleNamespace(list_devices=lambda: ()),
        audio_output=SimpleNamespace(),
    )
    plain.level_callback = observer  # no attribute on the input: a no-op
    assert plain.level_callback is None


# ---------------------------------------------------------------------------
# streamed playback
# ---------------------------------------------------------------------------


class _FakeRawOutputStream:
    instances: list["_FakeRawOutputStream"] = []

    def __init__(self, *, samplerate, channels, dtype):
        self.samplerate = samplerate
        self.channels = channels
        self.dtype = dtype
        self.written: list[bytes] = []
        self.closed = False
        _FakeRawOutputStream.instances.append(self)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.closed = True
        return False

    def write(self, data):
        self.written.append(bytes(data))


def _stream(chunks, rate=24_000):
    from app.voice.models import SpeechStream

    async def produce():
        for chunk in chunks:
            yield chunk, rate

    return SpeechStream(chunks=produce(), provider="p", model="m", voice="v", sample_rate=rate)


@pytest.mark.asyncio
async def test_windows_output_streams_pcm_through_sounddevice() -> None:
    from types import SimpleNamespace

    from app.voice.audio import WindowsWaveAudioOutput

    _FakeRawOutputStream.instances.clear()
    fake_sd = SimpleNamespace(RawOutputStream=_FakeRawOutputStream)
    output = WindowsWaveAudioOutput(module=SimpleNamespace(), stream_module=fake_sd)

    await output.play_stream(_stream([b"\x01\x00" * 10, b"", b"\x02\x00" * 10], rate=16_000))

    (device,) = _FakeRawOutputStream.instances
    assert device.samplerate == 16_000 and device.channels == 1 and device.dtype == "int16"
    assert device.written == [b"\x01\x00" * 10, b"\x02\x00" * 10]
    assert device.closed is True


@pytest.mark.asyncio
async def test_streamed_playback_stops_on_interruption() -> None:
    import asyncio
    from types import SimpleNamespace

    from app.voice.audio import WindowsWaveAudioOutput
    from app.voice.errors import VoiceInterrupted
    from app.voice.models import SpeechStream

    _FakeRawOutputStream.instances.clear()
    cancel = asyncio.Event()

    async def produce():
        yield b"\x01\x00" * 10, 24_000
        cancel.set()
        yield b"\x02\x00" * 10, 24_000

    output = WindowsWaveAudioOutput(
        module=SimpleNamespace(), stream_module=SimpleNamespace(RawOutputStream=_FakeRawOutputStream)
    )
    with pytest.raises(VoiceInterrupted):
        await output.play_stream(
            SpeechStream(chunks=produce(), provider="p", model="m", voice="v"), cancel_event=cancel
        )
    (device,) = _FakeRawOutputStream.instances
    assert device.written == [b"\x01\x00" * 10]
    assert device.closed is True


@pytest.mark.asyncio
async def test_streamed_playback_reports_device_failures() -> None:
    from types import SimpleNamespace

    from app.voice.audio import WindowsWaveAudioOutput
    from app.voice.errors import VoiceDeviceError

    class Broken:
        def __init__(self, **kwargs):
            raise RuntimeError("no device")

    output = WindowsWaveAudioOutput(
        module=SimpleNamespace(), stream_module=SimpleNamespace(RawOutputStream=Broken)
    )
    with pytest.raises(VoiceDeviceError):
        await output.play_stream(_stream([b"\x01\x00"]))


@pytest.mark.asyncio
async def test_outputs_without_sounddevice_buffer_the_stream_into_wav() -> None:
    from app.voice.base import AudioOutput
    from app.voice.models import AudioEncoding

    played = []

    class BufferingOutput(AudioOutput):
        def list_devices(self):
            return ()

        async def play(self, speech, *, cancel_event=None):
            played.append(speech)

        async def stop(self):
            pass

    await BufferingOutput().play_stream(_stream([b"\x01\x00", b"\x02\x00"], rate=8_000))
    (speech,) = played
    assert speech.encoding is AudioEncoding.WAV
    assert speech.data.startswith(b"RIFF") and speech.provider == "p"
    with pytest.raises(ValueError):
        await BufferingOutput().play_stream(_stream([]))
