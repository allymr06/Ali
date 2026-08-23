from __future__ import annotations

import asyncio
import contextlib
import time
from collections import deque
from collections.abc import Awaitable, Callable
from typing import TypeVar

from app.core.engine import CoreEngine
from app.core.models import Context, Request, RequestSource
from app.security.interactive import InteractiveApprovalCallback
from app.voice.base import AudioInput, AudioOutput, SpeechRecognizer, SpeechSynthesizer
from app.voice.errors import (
    VoiceConfigurationError,
    VoiceDeviceError,
    VoiceInterrupted,
    VoiceNoSpeech,
    VoiceProviderError,
    VoiceTimeoutError,
)
from app.voice.models import (
    AudioCapture,
    VoiceSessionEvent,
    VoiceSessionResult,
    VoiceSessionState,
    new_session_id,
)
from app.voice.wake import TextWakeWordDetector


T = TypeVar("T")

_SENTENCE_ENDINGS = (".", "!", "?", "…", ":", ";")


def split_speech_chunks(
    text: str,
    *,
    max_characters: int = 120,
    first_chunk_max: int = 90,
) -> list[str]:
    """Split a reply into speakable chunks along sentence boundaries.

    Synthesis time grows with text length, so the FIRST chunk is kept
    deliberately small — ideally a single sentence — to minimize time
    to first audio; later chunks pack sentences up to the cap while
    their predecessors are already playing. The concatenation of all
    chunks always equals the normalized input.
    """
    normalized = " ".join(text.split())
    if not normalized:
        return []

    sentences: list[str] = []
    start = 0
    for index, character in enumerate(normalized):
        if character in _SENTENCE_ENDINGS and (
            index + 1 == len(normalized)
            or normalized[index + 1] == " "
        ):
            sentences.append(normalized[start : index + 1].strip())
            start = index + 1
    remainder = normalized[start:].strip()
    if remainder:
        sentences.append(remainder)

    chunks: list[str] = []
    current = ""
    for position, sentence in enumerate(sentences):
        limit = first_chunk_max if not chunks else max_characters
        candidate = f"{current} {sentence}".strip()
        if current and len(candidate) > limit:
            chunks.append(current)
            current = sentence
        else:
            current = candidate
        while len(current) > limit * 2:
            cut = current.rfind(" ", 0, limit)
            if cut <= 0:
                break
            chunks.append(current[:cut])
            current = current[cut + 1 :]
    if current:
        chunks.append(current)
    return chunks


async def _await_task(task: "asyncio.Task[T]") -> T:
    return await task


class VoiceSession:
    """One bounded, interruptible microphone-to-Core-to-speaker turn."""

    def __init__(
        self,
        *,
        engine: CoreEngine,
        audio_input: AudioInput,
        audio_output: AudioOutput,
        recognizer: SpeechRecognizer,
        synthesizer: SpeechSynthesizer,
        wake_word_detector: TextWakeWordDetector,
        max_recording_seconds: float = 30.0,
        operation_timeout_seconds: float = 60.0,
        language: str | None = None,
        require_wake_word: bool = False,
        retain_audio: bool = False,
        event_capacity: int = 100,
        cloud_grace_seconds: float = 3.0,
    ) -> None:
        if not 0 < max_recording_seconds <= 300:
            raise ValueError("Recording duration must be between 0 and 300 seconds.")
        if operation_timeout_seconds <= 0:
            raise ValueError("Voice operation timeout must be positive.")
        if event_capacity < 1:
            raise ValueError("Voice event capacity must be positive.")
        self.session_id = new_session_id()
        self._engine = engine
        self._audio_input = audio_input
        self._audio_output = audio_output
        self._recognizer = recognizer
        self._synthesizer = synthesizer
        self._wake_word_detector = wake_word_detector
        self._max_recording_seconds = max_recording_seconds
        self._operation_timeout_seconds = operation_timeout_seconds
        self._language = language
        self._require_wake_word = require_wake_word
        self._retain_audio = retain_audio
        self._cloud_grace_seconds = max(0.0, cloud_grace_seconds)
        self._events: deque[VoiceSessionEvent] = deque(maxlen=event_capacity)
        # Optional observer for live state changes (e.g. the desktop
        # voice HUD). Set after construction; called on the session's
        # event loop and must never raise into the pipeline.
        self.state_callback: (
            Callable[[VoiceSessionState], None] | None
        ) = None
        self._interrupt_event = asyncio.Event()
        self._active = False
        self._finished = False
        self._state = VoiceSessionState.IDLE
        # Whichever synthesis source wins the first chunk speaks the
        # whole reply, so the voice never changes mid-answer.
        self._speech_source: str | None = None
        self.last_capture: AudioCapture | None = None
        self._started_monotonic: float | None = None
        self._record(VoiceSessionState.IDLE)

    @property
    def state(self) -> VoiceSessionState:
        return self._state

    @property
    def events(self) -> tuple[VoiceSessionEvent, ...]:
        return tuple(self._events)

    @property
    def is_active(self) -> bool:
        return self._active

    async def interrupt(self) -> bool:
        if not self._active:
            return False
        self._interrupt_event.set()
        with contextlib.suppress(Exception):
            await self._audio_output.stop()
        return True

    async def run_once(
        self,
        context: Context | None = None,
        *,
        approval_callback: InteractiveApprovalCallback | None = None,
    ) -> VoiceSessionResult:
        if self._active or self._finished:
            raise VoiceConfigurationError("A voice session can only be run once.")
        self._active = True
        self._started_monotonic = time.monotonic()
        capture: AudioCapture | None = None
        transcript: str | None = None
        wake_detected: bool | None = None
        response = None
        metadata: dict[str, object] = {}
        try:
            self._record(VoiceSessionState.LISTENING)
            stage_started = time.monotonic()
            capture = await self._await_interruptible(
                self._audio_input.capture(
                    max_duration_seconds=self._max_recording_seconds,
                    cancel_event=self._interrupt_event,
                )
            )
            metadata["capture_latency_seconds"] = (
                time.monotonic() - stage_started
            )
            metadata["capture_duration_seconds"] = capture.duration_seconds

            self._record(VoiceSessionState.TRANSCRIBING)
            stage_started = time.monotonic()
            transcription = await self._await_interruptible(
                self._recognizer.transcribe(capture, language=self._language)
            )
            metadata["transcription_latency_seconds"] = (
                time.monotonic() - stage_started
            )
            transcript = transcription.text
            metadata.update(
                transcription_provider=transcription.provider,
                transcription_model=transcription.model,
                transcription_language=transcription.language,
            )
            if self._retain_audio:
                self.last_capture = capture
            else:
                capture.clear()

            wake = self._wake_word_detector.match(transcript)
            wake_detected = wake.detected
            command = wake.command if wake.detected else transcript.strip()
            if self._require_wake_word and (not wake.detected or not command):
                self._record(VoiceSessionState.IGNORED)
                return self._result(
                    transcript=transcript,
                    wake_word_detected=wake_detected,
                    metadata=metadata,
                )

            self._record(VoiceSessionState.PROCESSING)
            stage_started = time.monotonic()
            approval_options = (
                {"approval_callback": approval_callback}
                if approval_callback is not None
                else {}
            )
            response = await self._await_interruptible(
                self._engine.handle(
                    Request(
                        command,
                        source=RequestSource.VOICE,
                        metadata={"voice_session_id": str(self.session_id)},
                    ),
                    context,
                    cancel_event=self._interrupt_event,
                    **approval_options,
                )
            )
            metadata["core_latency_seconds"] = (
                time.monotonic() - stage_started
            )
            provider_latency = (
                response.metadata
                .get("provider_metadata", {})
            )
            if isinstance(provider_latency, dict):
                value = provider_latency.get(
                    "provider_latency_seconds"
                )
                if isinstance(value, (int, float)):
                    metadata[
                        "provider_latency_seconds"
                    ] = float(value)

            if not response.text.strip():
                raise VoiceProviderError("Core returned no text for speech output.")

            # Sentence-pipelined speech: the first chunk reaches the
            # speakers while later chunks are still being synthesized,
            # so time-to-first-audio no longer scales with reply length.
            # If cloud synthesis fails (for example an exhausted quota),
            # the reply is never lost: the local Windows voice speaks it
            # and the text still reaches the conversation.
            chunks = split_speech_chunks(response.text)
            metadata["speech_chunks"] = len(chunks)
            self._record(VoiceSessionState.SYNTHESIZING)
            stage_started = time.monotonic()
            speech = await self._await_interruptible(
                self._synthesize_chunk(chunks[0], metadata, first=True)
            )
            metadata["synthesis_latency_seconds"] = (
                time.monotonic() - stage_started
            )
            if speech is None:
                metadata["speech_error"] = "provider"
                return self._failed(
                    "synthesis",
                    transcript,
                    wake_detected,
                    metadata,
                    response_text=response.text,
                )
            metadata.update(
                synthesis_provider=speech.provider,
                synthesis_model=speech.model,
                synthesis_voice=speech.voice,
            )

            self._record(VoiceSessionState.SPEAKING)
            stage_started = time.monotonic()
            pending: asyncio.Task | None = None
            try:
                for index in range(len(chunks)):
                    if index + 1 < len(chunks):
                        pending = asyncio.create_task(
                            self._synthesize_chunk(
                                chunks[index + 1], metadata
                            )
                        )
                    if speech is not None:
                        await self._await_interruptible(
                            self._audio_output.play(
                                speech,
                                cancel_event=self._interrupt_event,
                            )
                        )
                    if pending is not None:
                        speech = await self._await_interruptible(
                            _await_task(pending)
                        )
                        pending = None
            finally:
                if pending is not None:
                    pending.cancel()
                    with contextlib.suppress(
                        asyncio.CancelledError, Exception
                    ):
                        await pending
            metadata["playback_latency_seconds"] = (
                time.monotonic() - stage_started
            )
            self._record(VoiceSessionState.COMPLETED)
            return self._result(
                transcript=transcript,
                response_text=response.text,
                wake_word_detected=wake_detected,
                metadata=metadata,
            )
        except VoiceNoSpeech:
            metadata["ignored_reason"] = "no_speech"
            self._record(
                VoiceSessionState.IGNORED,
                "no_speech",
            )
            return self._result(
                transcript=transcript,
                wake_word_detected=wake_detected,
                metadata=metadata,
            )
        except VoiceInterrupted:
            self._record(VoiceSessionState.INTERRUPTED)
            return self._result(
                transcript=transcript,
                wake_word_detected=wake_detected,
                metadata=metadata,
            )
        except VoiceTimeoutError:
            self._record(VoiceSessionState.FAILED, "timeout")
            return self._result(
                transcript=transcript,
                wake_word_detected=wake_detected,
                error_code="timeout",
                metadata=metadata,
            )
        except VoiceConfigurationError:
            return self._failed(
                "configuration",
                transcript,
                wake_detected,
                metadata,
                response_text=(
                    response.text if response is not None else None
                ),
            )
        except VoiceDeviceError:
            return self._failed(
                "device",
                transcript,
                wake_detected,
                metadata,
                response_text=(
                    response.text if response is not None else None
                ),
            )
        except VoiceProviderError:
            return self._failed(
                "provider",
                transcript,
                wake_detected,
                metadata,
                response_text=(
                    response.text if response is not None else None
                ),
            )
        except asyncio.CancelledError:
            self._interrupt_event.set()
            with contextlib.suppress(Exception):
                await self._audio_output.stop()
            self._record(VoiceSessionState.INTERRUPTED)
            raise
        except Exception:
            return self._failed(
                "unexpected",
                transcript,
                wake_detected,
                metadata,
                response_text=(
                    response.text if response is not None else None
                ),
            )
        finally:
            if capture is not None and not self._retain_audio:
                capture.clear()
            self._active = False
            self._finished = True

    def _failed(
        self,
        error_code: str,
        transcript: str | None,
        wake_word_detected: bool | None,
        metadata: dict[str, object],
        *,
        response_text: str | None = None,
    ) -> VoiceSessionResult:
        self._record(VoiceSessionState.FAILED, error_code)
        return self._result(
            transcript=transcript,
            response_text=response_text,
            wake_word_detected=wake_word_detected,
            error_code=error_code,
            metadata=metadata,
        )

    @staticmethod
    def _wrap_local_speech(data: bytes, text: str = ""):
        from app.voice.audio import pick_local_voice
        from app.voice.models import AudioEncoding, SynthesizedSpeech

        voice, _language = pick_local_voice(text or "merhaba")
        return SynthesizedSpeech(
            data=data,
            encoding=AudioEncoding.WAV,
            provider="windows-local",
            model="winrt-speech",
            voice=voice,
        )

    async def _synthesize_chunk(
        self,
        text: str,
        metadata: dict[str, object],
        *,
        first: bool = False,
    ):
        """Return speech for one chunk from the fastest usable source.

        The opening chunk races cloud synthesis against the local
        Turkish voice, because whichever answers first decides how soon
        the user hears anything. Whichever source wins then speaks the
        whole reply: switching voices between sentences would be jarring.
        Returns None only when no source can produce audio.
        """
        from app.voice.audio import synthesize_local_turkish

        async def local_speech():
            data = await synthesize_local_turkish(text)
            return self._wrap_local_speech(data, text) if data else None

        async def cloud_speech():
            try:
                return await self._synthesizer.synthesize(text)
            except (
                VoiceProviderError,
                VoiceConfigurationError,
            ):
                return None

        if not first:
            if self._speech_source == "local":
                return await local_speech()
            speech = await cloud_speech()
            if speech is not None:
                return speech
            self._speech_source = "local"
            metadata["speech_fallback"] = "windows-local"
            return await local_speech()

        cloud = asyncio.create_task(
            self._synthesizer.synthesize(text)
        )
        local = asyncio.create_task(synthesize_local_turkish(text))
        try:
            # The high-quality cloud voice gets a bounded head start:
            # within the grace window it wins even when the robotic
            # local voice finished first. The local task keeps warming
            # in the background as the outage/quota parachute.
            if self._cloud_grace_seconds > 0:
                await asyncio.wait(
                    {cloud}, timeout=self._cloud_grace_seconds
                )
            if cloud.done() and cloud.exception() is None:
                self._speech_source = "cloud"
                metadata["speech_race_winner"] = "cloud"
                return cloud.result()

            if cloud.done():
                # Cloud failed outright; the local voice carries the
                # whole reply and the user is told why.
                self._speech_source = "local"
                metadata["speech_race_winner"] = "local_after_error"
                metadata["speech_fallback"] = "windows-local"
                metadata["speech_error"] = "provider"
                data = await local
                return (
                    self._wrap_local_speech(data, text)
                    if data
                    else None
                )

            # Cloud is slow past its grace: fastest source wins now.
            done, _pending = await asyncio.wait(
                {cloud, local},
                return_when=asyncio.FIRST_COMPLETED,
            )
            if cloud in done and cloud.exception() is None:
                self._speech_source = "cloud"
                metadata["speech_race_winner"] = "cloud"
                return cloud.result()

            if cloud in done:
                self._speech_source = "local"
                metadata["speech_race_winner"] = "local_after_error"
                metadata["speech_fallback"] = "windows-local"
                metadata["speech_error"] = "provider"
                data = await local
                return (
                    self._wrap_local_speech(data, text)
                    if data
                    else None
                )

            data = local.result()
            if data:
                self._speech_source = "local"
                metadata["speech_race_winner"] = "local"
                return self._wrap_local_speech(data, text)

            # No local voice on this machine: wait for the cloud.
            self._speech_source = "cloud"
            metadata["speech_race_winner"] = "cloud_only"
            try:
                return await cloud
            except (
                VoiceProviderError,
                VoiceConfigurationError,
            ):
                metadata["speech_error"] = "provider"
                return None
        finally:
            for task in (cloud, local):
                if not task.done():
                    task.cancel()
                    with contextlib.suppress(
                        asyncio.CancelledError, Exception
                    ):
                        await task

    def _record(self, state: VoiceSessionState, detail: str | None = None) -> None:
        self._state = state
        self._events.append(VoiceSessionEvent(state, self.session_id, detail=detail))
        if self.state_callback is not None:
            try:
                self.state_callback(state)
            except Exception:
                # Observer failures must never break the voice turn.
                pass

    def _result(
        self,
        *,
        transcript: str | None = None,
        response_text: str | None = None,
        wake_word_detected: bool | None = None,
        error_code: str | None = None,
        metadata: dict[str, object],
    ) -> VoiceSessionResult:
        result_metadata = dict(metadata)

        if self._started_monotonic is not None:
            result_metadata[
                "total_latency_seconds"
            ] = (
                time.monotonic()
                - self._started_monotonic
            )

        return VoiceSessionResult(
            session_id=self.session_id,
            state=self._state,
            transcript=transcript,
            response_text=response_text,
            wake_word_detected=wake_word_detected,
            error_code=error_code,
            metadata=result_metadata,
        )

    async def _await_interruptible(self, operation: Awaitable[T]) -> T:
        operation_task = asyncio.create_task(operation)
        interrupt_task = asyncio.create_task(self._interrupt_event.wait())
        try:
            done, _ = await asyncio.wait(
                {operation_task, interrupt_task},
                timeout=self._operation_timeout_seconds,
                return_when=asyncio.FIRST_COMPLETED,
            )
            if interrupt_task in done and self._interrupt_event.is_set():
                operation_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await operation_task
                raise VoiceInterrupted("Voice session interrupted.")
            if operation_task in done:
                return operation_task.result()
            operation_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await operation_task
            raise VoiceTimeoutError("Voice operation timed out.")
        finally:
            if not operation_task.done():
                operation_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await operation_task
            interrupt_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await interrupt_task
