"""ElevenLabs text-to-speech adapter.

The user's purchased voice: any voice id from their ElevenLabs
account — library, designed, or cloned — speaks JARVIS's replies.
PCM output is requested and wrapped into WAV locally so the Windows
playback path consumes it without transcoding, exactly like the
Gemini adapter's output.
"""

from __future__ import annotations

import io
import wave
from typing import Any, Callable

from app.config.settings import Settings
from app.voice.base import SpeechSynthesizer
from app.voice.errors import (
    VoiceConfigurationError,
    VoiceProviderError,
)
from app.voice.models import AudioEncoding, SynthesizedSpeech

_API_BASE = "https://api.elevenlabs.io/v1"
_PCM_RATE = 22_050


class ElevenLabsSpeechSynthesizer(SpeechSynthesizer):
    def __init__(
        self,
        settings: Settings,
        *,
        http_client_factory: Callable[..., Any] | None = None,
    ) -> None:
        self._api_key = (
            settings.voice_elevenlabs_api_key or ""
        ).strip()
        self._voice_id = settings.voice_elevenlabs_voice_id.strip()
        self._model = settings.voice_elevenlabs_model.strip()
        self._max_text_characters = settings.voice_max_tts_characters
        self._max_audio_bytes = settings.voice_max_audio_bytes
        self._http_client_factory = http_client_factory

    def _http_client(self, **kwargs: Any):
        if self._http_client_factory is not None:
            return self._http_client_factory(**kwargs)
        import httpx

        kwargs.setdefault("timeout", 20.0)
        return httpx.AsyncClient(**kwargs)

    @staticmethod
    def _wav_from_pcm(data: bytes) -> bytes:
        output = io.BytesIO()
        with wave.open(output, "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(_PCM_RATE)
            wav.writeframes(data)
        return output.getvalue()

    async def synthesize(self, text: str) -> SynthesizedSpeech:
        normalized = text.strip()
        if not normalized:
            raise ValueError("Speech text cannot be empty.")
        if len(normalized) > self._max_text_characters:
            raise ValueError(
                "Speech text exceeds the configured character limit."
            )
        if not self._api_key:
            raise VoiceConfigurationError(
                "ElevenLabs requires JARVIS_ELEVENLABS_API_KEY."
            )
        async with self._http_client() as client:
            try:
                response = await client.post(
                    f"{_API_BASE}/text-to-speech/{self._voice_id}",
                    params={"output_format": f"pcm_{_PCM_RATE}"},
                    headers={"xi-api-key": self._api_key},
                    json={
                        "text": normalized,
                        "model_id": self._model,
                    },
                )
            except Exception as exc:
                raise VoiceProviderError(
                    f"ElevenLabs request failed: {type(exc).__name__}"
                ) from exc
        if response.status_code in (401, 403):
            raise VoiceConfigurationError(
                "ElevenLabs rejected the API key."
            )
        if response.status_code != 200:
            raise VoiceProviderError(
                f"ElevenLabs HTTP {response.status_code}."
            )
        pcm = response.content
        if not pcm:
            raise VoiceProviderError(
                "ElevenLabs returned empty speech audio."
            )
        data = self._wav_from_pcm(pcm)
        if len(data) > self._max_audio_bytes:
            raise VoiceProviderError(
                "Synthesized audio exceeds the configured size limit."
            )
        return SynthesizedSpeech(
            data=data,
            encoding=AudioEncoding.WAV,
            provider="elevenlabs",
            model=self._model,
            voice=self._voice_id,
        )
