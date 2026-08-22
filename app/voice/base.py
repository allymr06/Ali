from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Sequence

from app.voice.models import (
    AudioCapture,
    AudioDevice,
    SynthesizedSpeech,
    TranscriptionResult,
)


class AudioInput(ABC):
    @abstractmethod
    def list_devices(self) -> Sequence[AudioDevice]: ...

    @abstractmethod
    async def capture(
        self,
        *,
        max_duration_seconds: float,
        cancel_event=None,
    ) -> AudioCapture: ...


class AudioOutput(ABC):
    @abstractmethod
    def list_devices(self) -> Sequence[AudioDevice]: ...

    @abstractmethod
    async def play(self, speech: SynthesizedSpeech, *, cancel_event=None) -> None: ...

    @abstractmethod
    async def stop(self) -> None: ...


class SpeechRecognizer(ABC):
    @abstractmethod
    async def transcribe(
        self,
        capture: AudioCapture,
        *,
        language: str | None = None,
    ) -> TranscriptionResult: ...


class SpeechSynthesizer(ABC):
    @abstractmethod
    async def synthesize(self, text: str) -> SynthesizedSpeech: ...
