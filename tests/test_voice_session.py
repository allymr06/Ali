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
    observed = []
    results = await service.run_continuous(
        max_turns=2,
        result_callback=observed.append,
    )
    assert len(results) == 2
    assert observed == list(results)
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
            default_provider="mock",
            default_model="mock-model",
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


def test_split_speech_chunks_packs_sentences() -> None:
    from app.voice.session import split_speech_chunks

    text = (
        "Elbette. Hava bugün güzel görünüyor! Sıcaklık yirmi altı "
        "dereceye çıkacak. Akşam hafif rüzgar var. Yarın yağmur "
        "ihtimali düşük, planlarını rahatça yapabilirsin. Başka bir "
        "şey ister misin?"
    )
    chunks = split_speech_chunks(text, max_characters=80)

    assert len(chunks) >= 2
    assert " ".join(chunks) == " ".join(text.split())
    assert all(len(chunk) <= 160 for chunk in chunks)
    assert chunks[0].endswith((".", "!", "?"))


def test_split_speech_chunks_handles_unpunctuated_text() -> None:
    from app.voice.session import split_speech_chunks

    text = "kelime " * 60
    chunks = split_speech_chunks(text.strip(), max_characters=70)

    assert len(chunks) >= 2
    assert " ".join(chunks) == " ".join(text.split())


class LongReplyEngine(FakeEngine):
    RESPONSE = (
        "Birinci cümle burada bitiyor. İkinci cümle de kayda değer "
        "uzunlukta sürüyor ve kendi başına bir parça oluşturuyor. "
        "Üçüncü cümle kapanışı yapmıyor, aksine metni uzatıyor. "
        "Dördüncü cümle ise toplamı kesin olarak sınırın üstüne taşıyor."
    )

    async def handle(self, request, context=None, *, cancel_event=None):
        self.requests.append((request, context, cancel_event))
        return Response(self.RESPONSE, request_id=request.request_id)


@pytest.mark.asyncio
async def test_session_pipelines_speech_chunk_synthesis() -> None:
    session, parts = make_session(engine=LongReplyEngine())
    synthesizer = parts["synthesizer"]

    result = await session.run_once()

    assert result.state is VoiceSessionState.COMPLETED
    assert len(synthesizer.texts) >= 2
    assert " ".join(synthesizer.texts) == LongReplyEngine.RESPONSE
    assert result.metadata["speech_chunks"] == len(synthesizer.texts)
    # Playback count matches synthesized chunk count.
    assert len(parts["audio_output"].played) == len(synthesizer.texts)


class FailingSynthesizer:
    def __init__(self):
        self.calls = 0

    async def synthesize(self, text):
        self.calls += 1
        from app.voice.errors import VoiceProviderError

        raise VoiceProviderError("quota exhausted")


@pytest.mark.asyncio
async def test_synthesis_failure_keeps_answer_and_uses_local_fallback(
    monkeypatch,
) -> None:
    spoken = []

    async def fake_local(text, **kwargs):
        spoken.append(text)
        return b"RIFFlocal-wav"

    monkeypatch.setattr(
        "app.voice.audio.synthesize_local_turkish",
        fake_local,
    )

    session, parts = make_session(synthesizer=FailingSynthesizer())
    result = await session.run_once()

    assert result.state is VoiceSessionState.COMPLETED
    assert result.response_text == "All systems operational."
    # Cloud synthesis failed, so the local Turkish voice carried the
    # reply and playback still went through the normal audio path.
    assert result.metadata["speech_fallback"] == "windows-local"
    assert spoken == ["All systems operational."]
    assert parts["audio_output"].played
    # The local voice follows the reply's language: this reply is
    # English, so the English Windows voice speaks it.
    assert (
        parts["audio_output"].played[0].voice == "Microsoft David"
    )


@pytest.mark.asyncio
async def test_synthesis_failure_without_fallback_preserves_text(
    monkeypatch,
) -> None:
    async def no_local(text, **kwargs):
        return None

    monkeypatch.setattr(
        "app.voice.audio.synthesize_local_turkish",
        no_local,
    )

    session, parts = make_session(synthesizer=FailingSynthesizer())
    result = await session.run_once()

    assert result.state is VoiceSessionState.FAILED
    assert result.error_code == "synthesis"
    # The core answer survives the speech failure.
    assert result.response_text == "All systems operational."


def test_pick_local_voice_follows_reply_language() -> None:
    from app.voice.audio import pick_local_voice

    assert pick_local_voice("Görev tamamlandı efendim.") == (
        "Microsoft Tolga",
        "tr",
    )
    # No special letters, but everyday Turkish words decide it.
    assert pick_local_voice("tamam ben sana haber veririm")[0] == (
        "Microsoft Tolga"
    )
    assert pick_local_voice("All systems operational, sir.") == (
        "Microsoft David",
        "en",
    )


class SlowSynthesizer:
    """Cloud synthesis that always loses the race."""

    def __init__(self, delay=0.4):
        self.delay = delay
        self.texts = []

    async def synthesize(self, text):
        self.texts.append(text)
        await asyncio.sleep(self.delay)
        return SynthesizedSpeech(
            b"RIFFcloud", AudioEncoding.WAV, "fake-tts", "tts-1", "voice-1"
        )


@pytest.mark.asyncio
async def test_local_voice_wins_race_when_cloud_is_slow(
    monkeypatch,
) -> None:
    async def fast_local(text, **kwargs):
        return b"RIFFlocal-wav"

    monkeypatch.setattr(
        "app.voice.audio.synthesize_local_turkish", fast_local
    )

    session, parts = make_session(
        synthesizer=SlowSynthesizer(),
        # Cloud is slower than its grace window, so the fast local
        # voice takes over.
        cloud_grace_seconds=0.05,
    )
    result = await session.run_once()

    assert result.state is VoiceSessionState.COMPLETED
    assert result.metadata["speech_race_winner"] == "local"
    assert parts["audio_output"].played[0].data == b"RIFFlocal-wav"


@pytest.mark.asyncio
async def test_cloud_voice_preferred_within_grace_window(
    monkeypatch,
) -> None:
    """A slower but high-quality cloud voice beats the instant local
    voice as long as it answers inside the grace window."""

    async def instant_local(text, **kwargs):
        return b"RIFFlocal-wav"

    monkeypatch.setattr(
        "app.voice.audio.synthesize_local_turkish", instant_local
    )

    session, parts = make_session(
        synthesizer=SlowSynthesizer(delay=0.1),
        cloud_grace_seconds=2.0,
    )
    result = await session.run_once()

    assert result.state is VoiceSessionState.COMPLETED
    assert result.metadata["speech_race_winner"] == "cloud"
    assert parts["audio_output"].played[0].provider == "fake-tts"


@pytest.mark.asyncio
async def test_cloud_voice_wins_race_when_it_is_ready_first(
    monkeypatch,
) -> None:
    async def slow_local(text, **kwargs):
        await asyncio.sleep(0.4)
        return b"RIFFlocal-wav"

    monkeypatch.setattr(
        "app.voice.audio.synthesize_local_turkish", slow_local
    )

    session, parts = make_session()
    result = await session.run_once()

    assert result.metadata["speech_race_winner"] == "cloud"
    assert parts["audio_output"].played[0].provider == "fake-tts"


# ---------------------------------------------------------------------------
# time to first audio: early first sentence, streamed opening chunk
# ---------------------------------------------------------------------------


class StreamingEngine(FakeEngine):
    """Streams the reply in pieces before returning it, like the core."""

    def __init__(self, *, partials, final, log):
        super().__init__()
        self.partials = partials
        self.final = final
        self.log = log

    async def handle(self, request, context=None, *, cancel_event=None, stream_callback=None, **_):
        self.requests.append((request, context, cancel_event))
        for partial in self.partials:
            if stream_callback is not None:
                stream_callback(partial)
                self.log.append(("stream", partial))
                await asyncio.sleep(0)
        self.log.append(("engine-done", self.final))
        return Response(self.final, request_id=request.request_id)


class LoggingSynthesizer(FakeSynthesizer):
    def __init__(self, log):
        super().__init__()
        self.log = log

    async def synthesize(self, text):
        self.log.append(("synthesize", text))
        return await super().synthesize(text)


@pytest.mark.asyncio
async def test_first_sentence_is_synthesized_while_the_reply_still_streams() -> None:
    log: list = []
    engine = StreamingEngine(
        partials=["Merhaba Ali.", "Merhaba Ali. Bugün hava", "Merhaba Ali. Bugün hava güzel."],
        final="Merhaba Ali. Bugün hava güzel. İyi günler dilerim.",
        log=log,
    )
    session, parts = make_session(
        engine=engine, synthesizer=LoggingSynthesizer(log), cloud_grace_seconds=0
    )

    result = await session.run_once(Context())

    assert result.state is VoiceSessionState.COMPLETED
    assert result.metadata["first_sentence_early"] is True
    # The opening chunk went to speech before the engine had finished.
    first_synthesis = next(i for i, entry in enumerate(log) if entry[0] == "synthesize")
    engine_done = next(i for i, entry in enumerate(log) if entry[0] == "engine-done")
    assert first_synthesis < engine_done
    assert log[first_synthesis][1] == "Merhaba Ali."
    assert [t for t in parts["synthesizer"].texts] == [
        "Merhaba Ali.", "Bugün hava güzel. İyi günler dilerim.",
    ]
    assert len(parts["audio_output"].played) == 2
    assert result.metadata["first_audio_latency_seconds"] >= 0


@pytest.mark.asyncio
async def test_early_sentence_is_discarded_when_the_final_text_differs() -> None:
    log: list = []
    engine = StreamingEngine(
        partials=["Şunu yapıyorum. Bir saniye", "Şunu yapıyorum. Bir saniye lütfen."],
        final="Bu isteği yerine getiremiyorum.",
        log=log,
    )
    session, parts = make_session(
        engine=engine, synthesizer=LoggingSynthesizer(log), cloud_grace_seconds=0
    )

    result = await session.run_once(Context())

    assert result.state is VoiceSessionState.COMPLETED
    assert result.metadata["first_sentence_early"] is False
    assert parts["synthesizer"].texts[-1] == "Bu isteği yerine getiremiyorum."
    spoken = [s.data for s in parts["audio_output"].played]
    assert len(spoken) == 1


@pytest.mark.asyncio
async def test_engines_without_streaming_support_still_work() -> None:
    session, parts = make_session(cloud_grace_seconds=0)
    result = await session.run_once(Context())
    assert result.state is VoiceSessionState.COMPLETED
    assert "first_sentence_early" not in result.metadata


class FakeStreamingSynthesizer(FakeSynthesizer):
    def __init__(self, chunks, *, gate=None):
        super().__init__(gate=gate)
        self.chunks = chunks
        self.stream_texts: list[str] = []

    async def synthesize_stream(self, text):
        from app.voice.models import SpeechStream

        self.stream_texts.append(text)

        async def produce():
            for chunk in self.chunks:
                yield chunk, 24_000

        return SpeechStream(chunks=produce(), provider="fake-tts", model="tts-1", voice="voice-1")


class StreamingOutput(FakeOutput):
    def __init__(self):
        super().__init__()
        self.streamed: list[list[bytes]] = []

    async def play_stream(self, stream, *, cancel_event=None):
        self.streamed.append([chunk async for chunk in stream])


@pytest.mark.asyncio
async def test_opening_chunk_is_played_as_a_stream_when_the_cloud_can_stream(monkeypatch) -> None:
    import app.voice.audio as audio_module

    async def no_local_voice(text, **kwargs):
        return None

    monkeypatch.setattr(audio_module, "synthesize_local_turkish", no_local_voice)
    synthesizer = FakeStreamingSynthesizer([b"\x01\x00" * 40, b"\x02\x00" * 40])
    output = StreamingOutput()
    session, parts = make_session(
        synthesizer=synthesizer, audio_output=output, cloud_grace_seconds=5
    )

    result = await session.run_once(Context())

    assert result.state is VoiceSessionState.COMPLETED
    assert synthesizer.stream_texts == ["All systems operational."]
    assert output.streamed == [[b"\x01\x00" * 40, b"\x02\x00" * 40]]
    assert output.played == []
    assert result.metadata["speech_race_winner"] == "cloud"
    assert result.metadata["synthesis_provider"] == "fake-tts"


@pytest.mark.asyncio
async def test_empty_cloud_stream_falls_back_to_the_local_voice(monkeypatch) -> None:
    import app.voice.audio as audio_module

    async def local_voice(text, **kwargs):
        return b"RIFFlocal"

    monkeypatch.setattr(audio_module, "synthesize_local_turkish", local_voice)
    synthesizer = FakeStreamingSynthesizer([])
    output = StreamingOutput()
    session, parts = make_session(
        synthesizer=synthesizer, audio_output=output, cloud_grace_seconds=5
    )

    result = await session.run_once(Context())

    assert result.state is VoiceSessionState.COMPLETED
    assert output.streamed == []
    assert [s.data for s in output.played] == [b"RIFFlocal"]
    assert result.metadata["speech_race_winner"] == "local_after_error"


def test_first_closed_sentence_waits_for_the_model_to_move_on() -> None:
    from app.voice.session import first_closed_sentence

    assert first_closed_sentence("Merhaba Ali.") is None
    assert first_closed_sentence("Merhaba Ali. Bugün") == "Merhaba Ali."
    assert first_closed_sentence("Sonuç 3.5 oldu. Tamam") == "Sonuç 3.5 oldu."
    assert first_closed_sentence("Evet. Hemen bakıyorum") is None
    assert first_closed_sentence("Hemen bakıyorum! Bir saniye") == "Hemen bakıyorum!"
    assert first_closed_sentence("   ") is None


@pytest.mark.asyncio
async def test_voice_service_warms_the_adapters_it_was_created_with() -> None:
    from app.voice.service import VoiceService

    class WarmRecognizer(FakeRecognizer):
        async def warm_up(self):
            return True

    class ColdSynthesizer(FakeSynthesizer):
        async def warm_up(self):
            raise RuntimeError("offline")

    service = VoiceService.create(
        engine=FakeEngine(),
        audio_input=FakeInput(),
        audio_output=FakeOutput(),
        recognizer=WarmRecognizer(),
        synthesizer=ColdSynthesizer(),
    )
    assert await service.warm_up() == {"voice_stt": True, "voice_tts": False}

    plain = VoiceService.create(
        engine=FakeEngine(),
        audio_input=FakeInput(),
        audio_output=FakeOutput(),
        recognizer=FakeRecognizer(),
        synthesizer=FakeSynthesizer(),
    )
    assert await plain.warm_up() == {}


# ---------------------------------------------------------------------------
# speculative transcription during the trailing silence
# ---------------------------------------------------------------------------


class ProvisionalInput(FakeInput):
    """Offers the audio so far, then returns a capture that adds more."""

    def __init__(self, early: bytes, final: bytes, *, trailing: float = 0.2):
        super().__init__(capture=AudioCapture(bytearray(final), sample_rate=8000))
        self.early = early
        self.trailing_silence_seconds = trailing

    async def capture(self, *, max_duration_seconds, cancel_event=None, provisional_callback=None):
        self.calls += 1
        if provisional_callback is not None:
            provisional_callback(AudioCapture(bytearray(self.early), sample_rate=8000))
            await asyncio.sleep(0)
        return self.capture_value


class CountingRecognizer(FakeRecognizer):
    def __init__(self):
        super().__init__(text="Jarvis, status")
        self.captures: list[int] = []

    async def transcribe(self, capture, *, language=None):
        self.captures.append(len(capture.data))
        await asyncio.sleep(0)
        return TranscriptionResult(self.text, "fake-stt", "stt-1", language)


@pytest.mark.asyncio
async def test_provisional_transcript_is_used_when_only_silence_followed() -> None:
    early = b"\x10\x00" * 800            # 0.1 s of speech at 8 kHz
    final = early + b"\x00\x00" * 1600    # + 0.2 s of trailing silence
    recognizer = CountingRecognizer()
    session, parts = make_session(
        audio_input=ProvisionalInput(early, final, trailing=0.2),
        recognizer=recognizer,
        cloud_grace_seconds=0,
    )

    result = await session.run_once(Context())

    assert result.state is VoiceSessionState.COMPLETED
    assert result.metadata["transcription_provisional"] is True
    # Only the early audio was transcribed; the final capture never was.
    assert recognizer.captures == [len(early)]


@pytest.mark.asyncio
async def test_provisional_transcript_is_discarded_when_speech_continued() -> None:
    early = b"\x10\x00" * 800
    final = early + b"\x10\x00" * 8000 + b"\x00\x00" * 1600  # a second later
    recognizer = CountingRecognizer()
    session, parts = make_session(
        audio_input=ProvisionalInput(early, final, trailing=0.2),
        recognizer=recognizer,
        cloud_grace_seconds=0,
    )

    result = await session.run_once(Context())

    assert result.state is VoiceSessionState.COMPLETED
    assert result.metadata["transcription_provisional"] is False
    assert recognizer.captures[-1] == len(final)


@pytest.mark.asyncio
async def test_inputs_without_provisional_support_are_unchanged() -> None:
    session, parts = make_session(cloud_grace_seconds=0)
    result = await session.run_once(Context())
    assert result.state is VoiceSessionState.COMPLETED
    assert "transcription_provisional" not in result.metadata
