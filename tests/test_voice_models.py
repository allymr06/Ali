from __future__ import annotations

import io
import wave

import pytest

from app.voice.models import (
    AudioCapture,
    AudioDevice,
    AudioDeviceKind,
    AudioEncoding,
    SynthesizedSpeech,
    TranscriptionResult,
)
from app.voice.wake import TextWakeWordDetector


def test_pcm_capture_converts_to_valid_wav_and_can_be_cleared() -> None:
    capture = AudioCapture(bytearray(b"\x01\x02" * 160), sample_rate=16_000)

    wav_data = capture.to_wav_bytes()

    with wave.open(io.BytesIO(wav_data), "rb") as stream:
        assert stream.getframerate() == 16_000
        assert stream.getnchannels() == 1
        assert stream.getsampwidth() == 2
        assert stream.readframes(160) == b"\x01\x02" * 160
    assert capture.duration_seconds == pytest.approx(0.01)
    capture.clear()
    assert capture.data == bytearray()


def test_audio_models_validate_boundaries() -> None:
    with pytest.raises(ValueError, match="empty"):
        AudioCapture(bytearray())
    with pytest.raises(ValueError, match="positive"):
        AudioDevice("1", "Mic", AudioDeviceKind.INPUT, channels=0)
    with pytest.raises(ValueError, match="confidence"):
        TranscriptionResult("hello", "test", "model", confidence=1.1)
    with pytest.raises(ValueError, match="empty"):
        SynthesizedSpeech(b"", AudioEncoding.WAV, "test", "model", "voice")


def test_non_pcm_capture_cannot_be_converted_to_wav() -> None:
    capture = AudioCapture(bytearray(b"audio"), encoding=AudioEncoding.MP3)

    assert capture.duration_seconds is None
    with pytest.raises(ValueError, match="Cannot convert"):
        capture.to_wav_bytes()


@pytest.mark.parametrize(
    ("text", "detected", "command"),
    [
        ("Jarvis, turn on the lights", True, "turn on the lights"),
        ("Please, JARVIS stop", True, "Please, stop"),
        ("jarvis", True, ""),
        ("jarvisian is not the wake word", False, ""),
    ],
)
def test_wake_word_matching_is_exact_and_case_insensitive(
    text: str,
    detected: bool,
    command: str,
) -> None:
    result = TextWakeWordDetector("jarvis").match(text)

    assert result.detected is detected
    assert result.command == command


def test_wake_word_cannot_be_empty() -> None:
    with pytest.raises(ValueError, match="empty"):
        TextWakeWordDetector(" ")
