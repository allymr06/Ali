from __future__ import annotations

import asyncio
from collections.abc import Callable

from app.core.models import Context
from app.security.interactive import InteractiveApprovalCallback
from app.voice.base import AudioInput, AudioOutput, SpeechRecognizer, SpeechSynthesizer
from app.voice.models import (
    AudioCapture,
    AudioDevice,
    VoiceSessionResult,
    VoiceSessionState,
)
from app.voice.session import VoiceSession
from app.voice.wake import TextWakeWordDetector


class VoiceService:
    """Coordinates voice turns while enforcing one active session."""

    def __init__(
        self,
        *,
        session_factory: Callable[[], VoiceSession],
        audio_input: AudioInput,
        audio_output: AudioOutput,
        stt_provider: str | None = None,
        tts_provider: str | None = None,
        recognizer: SpeechRecognizer | None = None,
        synthesizer: SpeechSynthesizer | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._audio_input = audio_input
        self._audio_output = audio_output
        self._recognizer = recognizer
        self._synthesizer = synthesizer
        # Optional live state observer, forwarded to every session.
        self.state_callback = None
        self._stt_provider = (
            stt_provider.strip()
            if stt_provider
            else None
        )
        self._tts_provider = (
            tts_provider.strip()
            if tts_provider
            else None
        )
        self._lock = asyncio.Lock()
        self._active_session: VoiceSession | None = None
        self._last_session: VoiceSession | None = None

    @property
    def level_callback(self) -> Callable[[float], None] | None:
        """Live microphone level observer, when the input can report one."""
        return getattr(self._audio_input, "level_callback", None)

    @level_callback.setter
    def level_callback(
        self, callback: Callable[[float], None] | None
    ) -> None:
        if hasattr(self._audio_input, "level_callback"):
            self._audio_input.level_callback = callback

    @classmethod
    def create(
        cls,
        *,
        engine,
        audio_input: AudioInput,
        audio_output: AudioOutput,
        recognizer: SpeechRecognizer,
        synthesizer: SpeechSynthesizer,
        stt_provider: str | None = None,
        tts_provider: str | None = None,
        wake_word: str = "jarvis",
        max_recording_seconds: float = 30.0,
        operation_timeout_seconds: float = 60.0,
        language: str | None = None,
        require_wake_word: bool = False,
        retain_audio: bool = False,
        cloud_grace_seconds: float = 3.0,
    ) -> VoiceService:
        def factory() -> VoiceSession:
            return VoiceSession(
                engine=engine,
                audio_input=audio_input,
                audio_output=audio_output,
                recognizer=recognizer,
                synthesizer=synthesizer,
                wake_word_detector=TextWakeWordDetector(wake_word),
                max_recording_seconds=max_recording_seconds,
                operation_timeout_seconds=operation_timeout_seconds,
                language=language,
                require_wake_word=require_wake_word,
                retain_audio=retain_audio,
                cloud_grace_seconds=cloud_grace_seconds,
            )

        return cls(
            session_factory=factory,
            audio_input=audio_input,
            audio_output=audio_output,
            stt_provider=stt_provider,
            tts_provider=tts_provider,
            recognizer=recognizer,
            synthesizer=synthesizer,
        )

    async def warm_up(self) -> dict[str, bool]:
        """Warm the speech adapters that know how; never raises."""
        results: dict[str, bool] = {}
        for name, adapter in (
            ("voice_stt", self._recognizer),
            ("voice_tts", self._synthesizer),
        ):
            warm = getattr(adapter, "warm_up", None)
            if not callable(warm):
                continue
            try:
                results[name] = bool(await warm())
            except Exception:
                results[name] = False
        return results

    @property
    def stt_provider(self) -> str | None:
        return self._stt_provider

    @property
    def tts_provider(self) -> str | None:
        return self._tts_provider

    @property
    def is_active(self) -> bool:
        return self._active_session is not None and self._active_session.is_active

    @property
    def state(self) -> VoiceSessionState:
        if self._active_session is None:
            return VoiceSessionState.IDLE
        return self._active_session.state

    @property
    def last_capture(self) -> AudioCapture | None:
        if self._last_session is None:
            return None
        return self._last_session.last_capture

    def clear_retained_audio(self) -> bool:
        capture = self.last_capture
        if capture is None:
            return False
        capture.clear()
        self._last_session.last_capture = None
        return True

    def list_devices(self) -> tuple[AudioDevice, ...]:
        return tuple(self._audio_input.list_devices()) + tuple(
            self._audio_output.list_devices()
        )

    async def run_once(
        self,
        context: Context | None = None,
        *,
        approval_callback: InteractiveApprovalCallback | None = None,
    ) -> VoiceSessionResult:
        if self._lock.locked():
            raise RuntimeError("Another voice session is already active.")
        async with self._lock:
            self.clear_retained_audio()
            session = self._session_factory()
            if self.state_callback is not None:
                session.state_callback = self.state_callback
            self._active_session = session
            try:
                options = (
                    {"approval_callback": approval_callback}
                    if approval_callback is not None
                    else {}
                )
                return await session.run_once(context, **options)
            finally:
                self._last_session = session
                self._active_session = None

    async def run_continuous(
        self,
        *,
        max_turns: int,
        context: Context | None = None,
        max_consecutive_failures: int = 2,
        result_callback: Callable[[VoiceSessionResult], None] | None = None,
        approval_callback: InteractiveApprovalCallback | None = None,
    ) -> tuple[VoiceSessionResult, ...]:
        if max_turns < 1 or max_turns > 100:
            raise ValueError(
                "Continuous voice turns must be between 1 and 100."
            )
        if (
            max_consecutive_failures < 0
            or max_consecutive_failures > 10
        ):
            raise ValueError(
                "Continuous voice recovery limit must be between 0 and 10."
            )

        results = []
        consecutive_failures = 0

        for _ in range(max_turns):
            result = await self.run_once(
                context,
                approval_callback=approval_callback,
            )
            results.append(result)
            if result_callback is not None:
                result_callback(result)

            if (
                result.state
                is VoiceSessionState.INTERRUPTED
            ):
                break

            if (
                result.state
                is VoiceSessionState.IGNORED
            ):
                break

            if (
                result.state
                is VoiceSessionState.FAILED
            ):
                consecutive_failures += 1

                if (
                    consecutive_failures
                    > max_consecutive_failures
                ):
                    break

                continue

            consecutive_failures = 0

        return tuple(results)

    async def interrupt_active(self) -> bool:
        session = self._active_session
        if session is None:
            return False
        return await session.interrupt()
