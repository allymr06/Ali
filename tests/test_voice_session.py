from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from app.core.models import Context, RequestSource, Response
from app.voice.errors import VoiceDeviceError
from app.voice.models import (
    AudioCapture,
    AudioDevice,
    AudioDeviceKind,
    AudioEncoding,
    SynthesizedSpeech,
    TranscriptionResult,
    VoiceSessionState,
)
from app.voice.service import VoiceService
from app.voice.session import VoiceSession
from app.voice.wake import TextWakeWordDetector


class FakeInput:
    def __init__(self, capture=None, *, gate: asyncio.Event | None = None, error=None):
        self.capture_value = capture or AudioCapture(bytearray(b"\x01\x02" * 80))
        self.gate = gate
        self.error = error
        self.calls = 0

    def list_devices(self):
        return (AudioDevice("mic", "Test mic", AudioDeviceKind.INPUT),)

    async def capture(self, *, max_duration_seconds, cancel_event=None):
        self.calls += 1
        if self.error:
            raise self.error
        if self.gate:
            await self.gate.wait()
        return self.capture_value


class FakeOutput:
    def __init__(self, *, gate: asyncio.Event | None = None):
        self.gate = gate
        self.played = []
        self.stop_calls = 0

    def list_devices(self):
        return (AudioDevice("speaker", "Test speaker", AudioDeviceKind.OUTPUT),)

    async def play(self, speech, *, cancel_event=None):
        self.played.append(speech)
        if self.gate:
            await self.gate.wait()

    async def stop(self):
        self.stop_calls += 1


class FakeRecognizer:
    def __init__(self, text="Jarvis, status", *, gate=None):
        self.text = text
        self.gate = gate
        self.capture = None

    async def transcribe(self, capture, *, language=None):
        self.capture = capture
        if self.gate:
            await self.gate.wait()
        return TranscriptionResult(self.text, "fake-stt", "stt-1", language)


class FakeSynthesizer:
    def __init__(self, *, gate=None):
        self.gate = gate
        self.texts = []

    async def synthesize(self, text):
        self.texts.append(text)
        if self.gate:
            await self.gate.wait()
        return SynthesizedSpeech(
            b"RIFFaudio", AudioEncoding.WAV, "fake-tts", "tts-1", "voice-1"
        )


class FakeEngine:
    def __init__(self, *, gate=None):
        self.gate = gate
        self.requests = []

    async def handle(self, request, context=None, *, cancel_event=None):
        self.requests.append((request, context, cancel_event))
        if self.gate:
            await self.gate.wait()
        return Response("All systems operational.", request_id=request.request_id)


def make_session(**overrides):
    components = {
        "engine": FakeEngine(),
        "audio_input": FakeInput(),
        "audio_output": FakeOutput(),
        "recognizer": FakeRecognizer(),
        "synthesizer": FakeSynthesizer(),
        "wake_word_detector": TextWakeWordDetector("jarvis"),
        "max_recording_seconds": 1,
        "operation_timeout_seconds": 1,
        "require_wake_word": True,
    }
    components.update(overrides)
    return VoiceSession(**components), components


@pytest.mark.asyncio
async def test_complete_voice_turn_uses_voice_source_and_records_states() -> None:
    context = Context()
    session, parts = make_session()

    result = await session.run_once(context)

    assert result.state is VoiceSessionState.COMPLETED
    assert result.transcript == "Jarvis, status"
    assert result.response_text == "All systems operational."
    assert result.wake_word_detected is True
    request, passed_context, cancel_event = parts["engine"].requests[0]
    assert request.text == "status"
    assert request.source is RequestSource.VOICE
    assert request.metadata["voice_session_id"] == str(session.session_id)
    assert passed_context is context
    assert cancel_event is not None
    assert parts["synthesizer"].texts == ["All systems operational."]
    assert len(parts["audio_output"].played) == 1
    assert [event.state for event in session.events] == [
        VoiceSessionState.IDLE,
        VoiceSessionState.LISTENING,
        VoiceSessionState.TRANSCRIBING,
        VoiceSessionState.PROCESSING,
        VoiceSessionState.SYNTHESIZING,
        VoiceSessionState.SPEAKING,
        VoiceSessionState.COMPLETED,
    ]


@pytest.mark.asyncio
async def test_missing_or_empty_wake_command_is_ignored() -> None:
    for transcript, detected in (("status", False), ("Jarvis", True)):
        session, parts = make_session(recognizer=FakeRecognizer(transcript))
        result = await session.run_once()
        assert result.state is VoiceSessionState.IGNORED
        assert result.wake_word_detected is detected
        assert parts["engine"].requests == []
        assert parts["audio_output"].played == []


@pytest.mark.asyncio
async def test_wake_word_is_optional_but_removed_when_present() -> None:
    session, parts = make_session(require_wake_word=False)
    result = await session.run_once()
    assert result.state is VoiceSessionState.COMPLETED
    assert parts["engine"].requests[0][0].text == "status"


@pytest.mark.asyncio
async def test_audio_is_cleared_by_default_and_can_be_explicitly_retained() -> None:
    capture = AudioCapture(bytearray(b"\x01\x02" * 10))
    session, _ = make_session(audio_input=FakeInput(capture))
    await session.run_once()
    assert capture.data == bytearray()
    assert session.last_capture is None

    retained = AudioCapture(bytearray(b"\x01\x02" * 10))
    session, _ = make_session(audio_input=FakeInput(retained), retain_audio=True)
    await session.run_once()
    assert session.last_capture is retained
    assert retained.data


@pytest.mark.asyncio
async def test_device_failure_returns_sanitized_classification() -> None:
    session, _ = make_session(
        audio_input=FakeInput(error=VoiceDeviceError("private device detail"))
    )
    result = await session.run_once()
    assert result.state is VoiceSessionState.FAILED
    assert result.error_code == "device"
    assert "private" not in repr(result.metadata)


@pytest.mark.asyncio
async def test_operation_timeout_is_bounded_and_classified() -> None:
    session, _ = make_session(
        audio_input=FakeInput(gate=asyncio.Event()),
        operation_timeout_seconds=0.01,
    )
    result = await session.run_once()
    assert result.state is VoiceSessionState.FAILED
    assert result.error_code == "timeout"


@pytest.mark.asyncio
@pytest.mark.parametrize("stage", ["listening", "processing", "speaking"])
async def test_active_session_can_be_interrupted_at_key_stages(stage: str) -> None:
    gate = asyncio.Event()
    output = FakeOutput(gate=gate if stage == "speaking" else None)
    overrides = {"audio_output": output}
    if stage == "listening":
        overrides["audio_input"] = FakeInput(gate=gate)
    elif stage == "processing":
        overrides["engine"] = FakeEngine(gate=gate)
    session, _ = make_session(**overrides)
    task = asyncio.create_task(session.run_once())
    target = VoiceSessionState(stage)
    for _ in range(100):
        if session.state is target:
            break
        await asyncio.sleep(0)
    assert session.state is target
    assert await session.interrupt() is True
    result = await task
    assert result.state is VoiceSessionState.INTERRUPTED
    assert output.stop_calls >= 1


@pytest.mark.asyncio
async def test_session_rejects_reuse() -> None:
    session, _ = make_session()
    await session.run_once()
    with pytest.raises(Exception, match="only be run once"):
        await session.run_once()


@pytest.mark.asyncio
async def test_voice_service_serializes_sessions_lists_devices_and_runs_bounded() -> None:
    created = []
    input_device = FakeInput()
    output_device = FakeOutput()

    def factory():
        session, _ = make_session(
            audio_input=input_device,
            audio_output=output_device,
            require_wake_word=False,
            recognizer=FakeRecognizer("status"),
        )
        created.append(session)
        return session

    service = VoiceService(
        session_factory=factory,
        audio_input=input_device,
        audio_output=output_device,
    )
    results = await service.run_continuous(max_turns=2)
    assert len(results) == 2
    assert all(result.state is VoiceSessionState.COMPLETED for result in results)
    assert {device.kind for device in service.list_devices()} == {
        AudioDeviceKind.INPUT,
        AudioDeviceKind.OUTPUT,
    }
    assert service.state is VoiceSessionState.IDLE
    assert await service.interrupt_active() is False


@pytest.mark.asyncio
async def test_voice_service_exposes_and_clears_explicitly_retained_audio() -> None:
    capture = AudioCapture(bytearray(b"\x01\x02" * 10))
    audio_input = FakeInput(capture)
    audio_output = FakeOutput()
    service = VoiceService.create(
        engine=FakeEngine(),
        audio_input=audio_input,
        audio_output=audio_output,
        recognizer=FakeRecognizer("status"),
        synthesizer=FakeSynthesizer(),
        require_wake_word=False,
        retain_audio=True,
        max_recording_seconds=1,
        operation_timeout_seconds=1,
    )

    await service.run_once()

    assert service.last_capture is capture
    replacement = AudioCapture(bytearray(b"\x03\x04" * 10))
    audio_input.capture_value = replacement
    await service.run_once()
    assert capture.data == bytearray()
    assert service.last_capture is replacement
    assert service.clear_retained_audio() is True
    assert replacement.data == bytearray()
    assert service.last_capture is None
    assert service.clear_retained_audio() is False


@pytest.mark.asyncio
async def test_voice_service_rejects_a_second_active_turn() -> None:
    gate = asyncio.Event()
    input_device = FakeInput(gate=gate)

    def factory():
        return make_session(audio_input=input_device)[0]

    service = VoiceService(
        session_factory=factory,
        audio_input=input_device,
        audio_output=FakeOutput(),
    )
    first = asyncio.create_task(service.run_once())
    await asyncio.sleep(0)
    with pytest.raises(RuntimeError, match="already active"):
        await service.run_once()
    assert await service.interrupt_active() is True
    assert (await first).state is VoiceSessionState.INTERRUPTED


@pytest.mark.asyncio
async def test_voice_vertical_slice_runs_through_real_core() -> None:
    from app.bootstrap import create_application
    from app.config.settings import Settings

    application = create_application(
        Settings(
            memory_database_path=None,
            task_database_path=None,
            task_runtime_directory=None,
            windows_integrations_enabled=False,
        )
    )
    audio_input = FakeInput()
    audio_output = FakeOutput()
    service = VoiceService.create(
        engine=application.engine,
        audio_input=audio_input,
        audio_output=audio_output,
        recognizer=FakeRecognizer("Jarvis, status"),
        synthesizer=FakeSynthesizer(),
        require_wake_word=True,
        max_recording_seconds=1,
        operation_timeout_seconds=1,
    )

    result = await service.run_once(Context())

    assert result.state is VoiceSessionState.COMPLETED
    assert result.response_text == "Mock yanıtı: status"
    assert len(audio_output.played) == 1
