from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Sequence

from app.voice.models import (
    AudioCapture,
    AudioDevice,
    SynthesizedSpeech,
    TranscriptionResult,
    AudioEncoding,
    SpeechStream,
    pcm16_to_wav,
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

    async def play_stream(self, stream: SpeechStream, *, cancel_event=None) -> None:
        """Play speech that arrives as PCM chunks.

        Outputs that can write samples progressively override this; the
        default gathers the chunks into one WAV and plays it whole, so a
        streaming synthesizer never needs a streaming output to work.
        """
        pcm = bytearray()
        async for chunk in stream:
            pcm.extend(chunk)
        if not pcm:
            raise ValueError("Speech stream produced no audio.")
        await self.play(
            SynthesizedSpeech(
                pcm16_to_wav(bytes(pcm), stream.sample_rate),
                AudioEncoding.WAV,
                stream.provider,
                stream.model,
                stream.voice,
            ),
            cancel_event=cancel_event,
        )

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
