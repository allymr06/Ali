from __future__ import annotations

import asyncio
import contextlib
import time
from collections import deque
from collections.abc import Awaitable
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
        self._events: deque[VoiceSessionEvent] = deque(maxlen=event_capacity)
        self._interrupt_event = asyncio.Event()
        self._active = False
        self._finished = False
        self._state = VoiceSessionState.IDLE
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

            self._record(VoiceSessionState.SYNTHESIZING)
            stage_started = time.monotonic()
            speech = await self._await_interruptible(
                self._synthesizer.synthesize(response.text)
            )
            metadata["synthesis_latency_seconds"] = (
                time.monotonic() - stage_started
            )
            metadata.update(
                synthesis_provider=speech.provider,
                synthesis_model=speech.model,
                synthesis_voice=speech.voice,
            )

            self._record(VoiceSessionState.SPEAKING)
            stage_started = time.monotonic()
            await self._await_interruptible(
                self._audio_output.play(
                    speech,
                    cancel_event=self._interrupt_event,
                )
            )
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
            return self._failed("configuration", transcript, wake_detected, metadata)
        except VoiceDeviceError:
            return self._failed("device", transcript, wake_detected, metadata)
        except VoiceProviderError:
            return self._failed("provider", transcript, wake_detected, metadata)
        except asyncio.CancelledError:
            self._interrupt_event.set()
            with contextlib.suppress(Exception):
                await self._audio_output.stop()
            self._record(VoiceSessionState.INTERRUPTED)
            raise
        except Exception:
            return self._failed("unexpected", transcript, wake_detected, metadata)
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
    ) -> VoiceSessionResult:
        self._record(VoiceSessionState.FAILED, error_code)
        return self._result(
            transcript=transcript,
            wake_word_detected=wake_word_detected,
            error_code=error_code,
            metadata=metadata,
        )

    def _record(self, state: VoiceSessionState, detail: str | None = None) -> None:
        self._state = state
        self._events.append(VoiceSessionEvent(state, self.session_id, detail=detail))

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
