from __future__ import annotations

import io
import wave
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any
from uuid import UUID, uuid4

from app.core.time import utc_now


class AudioEncoding(str, Enum):
    PCM16 = "pcm16"
    WAV = "wav"
    MP3 = "mp3"
    OPUS = "opus"
    AAC = "aac"
    FLAC = "flac"


class AudioDeviceKind(str, Enum):
    INPUT = "input"
    OUTPUT = "output"


class VoiceSessionState(str, Enum):
    IDLE = "idle"
    LISTENING = "listening"
    TRANSCRIBING = "transcribing"
    PROCESSING = "processing"
    SYNTHESIZING = "synthesizing"
    SPEAKING = "speaking"
    COMPLETED = "completed"
    IGNORED = "ignored"
    INTERRUPTED = "interrupted"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class AudioDevice:
    device_id: str
    name: str
    kind: AudioDeviceKind
    is_default: bool = False
    channels: int | None = None
    sample_rate: int | None = None

    def __post_init__(self) -> None:
        if not self.device_id.strip() or not self.name.strip():
            raise ValueError("Audio device identity cannot be empty.")
        if self.channels is not None and self.channels < 1:
            raise ValueError("Audio device channels must be positive.")
        if self.sample_rate is not None and self.sample_rate < 1:
            raise ValueError("Audio device sample rate must be positive.")


@dataclass(slots=True)
class AudioCapture:
    data: bytearray
    sample_rate: int = 16_000
    channels: int = 1
    sample_width: int = 2
    encoding: AudioEncoding = AudioEncoding.PCM16
    device_id: str | None = None
    started_at: datetime = field(default_factory=utc_now)
    finished_at: datetime = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        if isinstance(self.data, bytes):
            self.data = bytearray(self.data)
        if not isinstance(self.data, bytearray):
            raise TypeError("Audio capture data must be mutable bytes.")
        if not self.data:
            raise ValueError("Audio capture cannot be empty.")
        if self.sample_rate < 1 or self.channels < 1 or self.sample_width < 1:
            raise ValueError("Audio format values must be positive.")
        if self.started_at.tzinfo is None or self.finished_at.tzinfo is None:
            raise ValueError("Audio timestamps must be timezone-aware.")
        if self.finished_at < self.started_at:
            raise ValueError("Audio finish time cannot precede its start time.")

    @property
    def duration_seconds(self) -> float | None:
        if self.encoding is not AudioEncoding.PCM16:
            return None
        frame_width = self.channels * self.sample_width
        return len(self.data) / (self.sample_rate * frame_width)

    def to_wav_bytes(self) -> bytes:
        if self.encoding is AudioEncoding.WAV:
            return bytes(self.data)
        if self.encoding is not AudioEncoding.PCM16:
            raise ValueError(f"Cannot convert {self.encoding.value} capture to WAV.")
        buffer = io.BytesIO()
        with wave.open(buffer, "wb") as output:
            output.setnchannels(self.channels)
            output.setsampwidth(self.sample_width)
            output.setframerate(self.sample_rate)
            output.writeframes(bytes(self.data))
        return buffer.getvalue()

    def clear(self) -> None:
        """Best-effort overwrite of in-memory microphone data."""
        self.data[:] = b"\x00" * len(self.data)
        self.data.clear()


@dataclass(frozen=True, slots=True)
class TranscriptionResult:
    text: str
    provider: str
    model: str
    language: str | None = None
    confidence: float | None = None

    def __post_init__(self) -> None:
        if not self.text.strip():
            raise ValueError("Transcription text cannot be empty.")
        if not self.provider.strip() or not self.model.strip():
            raise ValueError("Transcription provider and model cannot be empty.")
        if self.confidence is not None and not 0.0 <= self.confidence <= 1.0:
            raise ValueError("Transcription confidence must be between 0 and 1.")


@dataclass(frozen=True, slots=True)
class SynthesizedSpeech:
    data: bytes
    encoding: AudioEncoding
    provider: str
    model: str
    voice: str

    def __post_init__(self) -> None:
        if not self.data:
            raise ValueError("Synthesized speech cannot be empty.")
        if not self.provider.strip() or not self.model.strip() or not self.voice.strip():
            raise ValueError("Speech provenance cannot be empty.")


def pcm16_to_wav(data: bytes, sample_rate: int, channels: int = 1) -> bytes:
    """Wrap raw 16-bit PCM into a WAV container."""
    import io
    import wave

    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as handle:
        handle.setnchannels(channels)
        handle.setsampwidth(2)
        handle.setframerate(sample_rate)
        handle.writeframes(data)
    return buffer.getvalue()


@dataclass(slots=True)
class SpeechStream:
    """Speech that arrives as PCM16 chunks while it is being synthesized.

    ``chunks`` yields ``(pcm_bytes, sample_rate)``. :meth:`prime` pulls the
    first chunk so the caller knows the moment audio exists (and the real
    sample rate); iterating the stream afterwards replays that chunk first.
    """

    chunks: Any
    provider: str
    model: str
    voice: str
    sample_rate: int = 24_000
    head: list[bytes] = field(default_factory=list)
    primed: bool = False
    encoding: AudioEncoding = AudioEncoding.PCM16

    def __post_init__(self) -> None:
        if not self.provider.strip() or not self.model.strip() or not self.voice.strip():
            raise ValueError("Speech provenance cannot be empty.")
        if self.sample_rate < 1:
            raise ValueError("Sample rate must be positive.")

    async def prime(self) -> bytes:
        """Wait for the first audio chunk; raises when the stream has none."""
        if self.primed:
            return self.head[0] if self.head else b""
        async for data, rate in self.chunks:
            if not data:
                continue
            self.sample_rate = int(rate) if rate else self.sample_rate
            self.head.append(bytes(data))
            self.primed = True
            return self.head[0]
        self.primed = True
        raise ValueError("Speech stream produced no audio.")

    async def __aiter__(self):
        for data in self.head:
            yield data
        async for data, _rate in self.chunks:
            if data:
                yield bytes(data)


@dataclass(frozen=True, slots=True)
class VoiceSessionEvent:
    state: VoiceSessionState
    session_id: UUID
    created_at: datetime = field(default_factory=utc_now)
    detail: str | None = None


@dataclass(frozen=True, slots=True)
class VoiceSessionResult:
    session_id: UUID
    state: VoiceSessionState
    transcript: str | None = None
    response_text: str | None = None
    wake_word_detected: bool | None = None
    error_code: str | None = None
    metadata: dict[str, object] = field(default_factory=dict)


def new_session_id() -> UUID:
    return uuid4()
