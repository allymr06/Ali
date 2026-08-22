from __future__ import annotations

import inspect
from typing import Any

from openai import AsyncOpenAI

from app.config.settings import Settings
from app.voice.base import SpeechRecognizer, SpeechSynthesizer
from app.voice.errors import VoiceConfigurationError, VoiceProviderError
from app.voice.models import (
    AudioCapture,
    AudioEncoding,
    SynthesizedSpeech,
    TranscriptionResult,
)


class OpenAISpeechRecognizer(SpeechRecognizer):
    """OpenAI Audio API transcription adapter."""

    def __init__(
        self,
        settings: Settings,
        *,
        client: Any | None = None,
    ) -> None:
        self._model = settings.voice_stt_model
        self._client = client
        if self._client is None and settings.api_key:
            self._client = AsyncOpenAI(
                api_key=settings.api_key,
                base_url=settings.api_base_url or None,
            )

    def _require_client(self):
        if self._client is None:
            raise VoiceConfigurationError("OpenAI speech recognition is not configured.")
        return self._client

    async def transcribe(
        self,
        capture: AudioCapture,
        *,
        language: str | None = None,
    ) -> TranscriptionResult:
        client = self._require_client()
        arguments: dict[str, Any] = {
            "model": self._model,
            "file": ("jarvis-voice.wav", capture.to_wav_bytes(), "audio/wav"),
            "response_format": "json",
        }
        if language:
            arguments["language"] = language
        try:
            response = await client.audio.transcriptions.create(**arguments)
        except Exception as exc:
            raise VoiceProviderError("Speech transcription provider failed.") from exc
        text = getattr(response, "text", None)
        if text is None and isinstance(response, str):
            text = response
        if not isinstance(text, str) or not text.strip():
            raise VoiceProviderError("Speech provider returned an empty transcription.")
        return TranscriptionResult(
            text=text.strip(),
            provider="openai",
            model=self._model,
            language=language,
        )


class OpenAISpeechSynthesizer(SpeechSynthesizer):
    """OpenAI Audio API WAV speech synthesis adapter."""

    def __init__(
        self,
        settings: Settings,
        *,
        client: Any | None = None,
    ) -> None:
        self._model = settings.voice_tts_model
        self._voice = settings.voice_tts_voice
        self._instructions = settings.voice_tts_instructions
        self._max_text_characters = settings.voice_max_tts_characters
        self._max_audio_bytes = settings.voice_max_audio_bytes
        self._client = client
        if self._client is None and settings.api_key:
            self._client = AsyncOpenAI(
                api_key=settings.api_key,
                base_url=settings.api_base_url or None,
            )

    def _require_client(self):
        if self._client is None:
            raise VoiceConfigurationError("OpenAI speech synthesis is not configured.")
        return self._client

    async def synthesize(self, text: str) -> SynthesizedSpeech:
        normalized = text.strip()
        if not normalized:
            raise ValueError("Speech text cannot be empty.")
        if len(normalized) > self._max_text_characters:
            raise ValueError("Speech text exceeds the configured character limit.")
        arguments: dict[str, Any] = {
            "model": self._model,
            "voice": self._voice,
            "input": normalized,
            "response_format": "wav",
        }
        if self._instructions:
            arguments["instructions"] = self._instructions
        client = self._require_client()
        try:
            response = await client.audio.speech.create(**arguments)
            data = await self._read_response(response)
        except VoiceProviderError:
            raise
        except Exception as exc:
            raise VoiceProviderError("Speech synthesis provider failed.") from exc
        if not data:
            raise VoiceProviderError("Speech provider returned empty audio.")
        if len(data) > self._max_audio_bytes:
            raise VoiceProviderError("Synthesized audio exceeds the configured size limit.")
        return SynthesizedSpeech(
            data=data,
            encoding=AudioEncoding.WAV,
            provider="openai",
            model=self._model,
            voice=self._voice,
        )

    @staticmethod
    async def _read_response(response: Any) -> bytes:
        content = getattr(response, "content", None)
        if isinstance(content, bytes):
            return content
        read = getattr(response, "aread", None) or getattr(response, "read", None)
        if callable(read):
            value = read()
            if inspect.isawaitable(value):
                value = await value
            if isinstance(value, bytes):
                return value
        raise VoiceProviderError("Speech provider returned an invalid audio response.")
