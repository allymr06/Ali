from __future__ import annotations

import asyncio
import base64
import inspect
import io
import wave
from typing import Any

from app.config.settings import Settings
from app.voice.base import (
    SpeechRecognizer,
    SpeechSynthesizer,
)
from app.voice.errors import (
    VoiceConfigurationError,
    VoiceProviderError,
)
from app.voice.models import (
    AudioCapture,
    AudioEncoding,
    SynthesizedSpeech,
    TranscriptionResult,
)


def _create_google_client(
    api_key: str | None,
):
    if not api_key:
        return None

    try:
        from google import genai
    except ImportError:
        return None

    return genai.Client(
        api_key=api_key
    )


def _provider_error(
    message: str, cause: Exception
) -> VoiceProviderError:
    """Wrap a provider failure, marking transient rate/availability
    errors so callers can retry once before degrading."""
    error = VoiceProviderError(message)
    detail = f"{type(cause).__name__} {cause}"
    error.transient = any(
        marker in detail
        for marker in ("429", "RESOURCE_EXHAUSTED", "503", "502")
    )
    return error


async def _generate_content(
    client,
    **kwargs,
):
    """Call models.generate_content off the event loop.

    The configured speech models expose only the generateContent
    action, so both adapters speak that surface.
    """
    result = await asyncio.to_thread(
        client.models.generate_content,
        **kwargs,
    )

    if inspect.isawaitable(result):
        result = await result

    return result


def _decode_inline_audio(part) -> tuple[bytes, str] | None:
    """Extract (raw_bytes, mime_type) from a response part, if audio."""
    inline = getattr(part, "inline_data", None)
    if inline is None:
        return None
    mime = str(
        getattr(
            getattr(inline, "mime_type", ""), "value",
            getattr(inline, "mime_type", ""),
        )
    ).lower()
    if not mime.startswith("audio/"):
        return None
    data = getattr(inline, "data", None)
    if isinstance(data, str):
        try:
            return base64.b64decode(data), mime
        except Exception:
            return None
    if isinstance(data, (bytes, bytearray)):
        return bytes(data), mime
    return None


def _pcm_rate_from_mime(mime: str, default: int = 24_000) -> int:
    for parameter in mime.split(";"):
        key, _, value = parameter.strip().partition("=")
        if key == "rate" and value.isdigit():
            return int(value)
    return default


class GeminiSpeechRecognizer(
    SpeechRecognizer
):
    """
    Gemini audio-understanding adapter.

    Voice audio is sent inline and storage is
    explicitly disabled for this interaction.
    """

    def __init__(
        self,
        settings: Settings,
        *,
        client: Any | None = None,
    ) -> None:
        self._model = (
            settings.voice_gemini_stt_model
        )

        self._client = (
            client
            if client is not None
            else _create_google_client(
                settings.gemini_api_key
            )
        )

    def _require_client(self):
        if self._client is None:
            raise VoiceConfigurationError(
                "Gemini speech recognition "
                "requires google-genai and "
                "a Gemini API key."
            )

        return self._client

    _THINKING_MODEL_PREFIXES = (
        "gemini-3.7",
        "gemini-2.5-pro",
    )

    def _transcription_config(self) -> dict:
        config: dict = {
            "automatic_function_calling": {
                "disable": True,
            },
        }
        # Thinking models reason for many seconds before answering,
        # which is wasted latency for verbatim transcription.
        if self._model.strip().casefold().startswith(
            self._THINKING_MODEL_PREFIXES
        ):
            config["thinking_config"] = {"thinking_budget": 0}
        return config

    async def transcribe(
        self,
        capture: AudioCapture,
        *,
        language: str | None = None,
    ) -> TranscriptionResult:
        client = self._require_client()

        instruction = (
            "Transcribe only the spoken words "
            "in this audio. Return plain text "
            "only. Do not summarize, explain, "
            "or add commentary. The assistant's "
            "name 'Jarvis' may be spoken; write "
            "it exactly as 'Jarvis'."
        )

        if language:
            instruction += (
                " The expected language is "
                f"{language}."
            )

        try:
            response = await _generate_content(
                client,
                model=self._model,
                contents=[
                    {
                        "role": "user",
                        "parts": [
                            {"text": instruction},
                            {
                                "inline_data": {
                                    "mime_type": "audio/wav",
                                    "data": (
                                        capture.to_wav_bytes()
                                    ),
                                }
                            },
                        ],
                    }
                ],
                config=self._transcription_config(),
            )

        except Exception as exc:
            raise _provider_error(
                "Gemini speech transcription failed.", exc
            ) from exc

        text = getattr(
            response,
            "text",
            None,
        )

        if (
            not isinstance(text, str)
            or not text.strip()
        ):
            raise VoiceProviderError(
                "Gemini returned an "
                "empty transcription."
            )

        return TranscriptionResult(
            text=text.strip(),
            provider="gemini",
            model=self._model,
            language=language,
        )


class GeminiSpeechSynthesizer(
    SpeechSynthesizer
):
    """
    Gemini Interactions API TTS adapter.

    WAV output is requested so Windows playback
    can consume the response without transcoding.
    """

    def __init__(
        self,
        settings: Settings,
        *,
        client: Any | None = None,
    ) -> None:
        self._model = (
            settings.voice_gemini_tts_model
        )
        self._voice = (
            settings.voice_gemini_tts_voice
        )
        self._instructions = (
            settings.voice_tts_instructions
        )
        self._max_text_characters = (
            settings.voice_max_tts_characters
        )
        self._max_audio_bytes = (
            settings.voice_max_audio_bytes
        )

        self._client = (
            client
            if client is not None
            else _create_google_client(
                settings.gemini_api_key
            )
        )

    def _require_client(self):
        if self._client is None:
            raise VoiceConfigurationError(
                "Gemini speech synthesis "
                "requires google-genai and "
                "a Gemini API key."
            )

        return self._client

    @staticmethod
    def _wav_from_pcm(
        data: bytes,
        *,
        sample_rate: int,
        channels: int,
    ) -> bytes:
        output = io.BytesIO()

        with wave.open(
            output,
            "wb",
        ) as wav:
            wav.setnchannels(channels)
            wav.setsampwidth(2)
            wav.setframerate(
                sample_rate
            )
            wav.writeframes(data)

        return output.getvalue()

    async def synthesize(
        self,
        text: str,
    ) -> SynthesizedSpeech:
        normalized = text.strip()

        if not normalized:
            raise ValueError(
                "Speech text cannot be empty."
            )

        if (
            len(normalized)
            > self._max_text_characters
        ):
            raise ValueError(
                "Speech text exceeds the "
                "configured character limit."
            )

        prompt = normalized

        if self._instructions:
            prompt = (
                f"{self._instructions.strip()}\n\n"
                "Speak the following response "
                "naturally and faithfully:\n"
                f"{normalized}"
            )

        client = self._require_client()

        try:
            response = await _generate_content(
                client,
                model=self._model,
                contents=prompt,
                config={
                    "response_modalities": ["AUDIO"],
                    "speech_config": {
                        "voice_config": {
                            "prebuilt_voice_config": {
                                "voice_name": self._voice,
                            }
                        }
                    },
                    "automatic_function_calling": {
                        "disable": True,
                    },
                },
            )

        except Exception as exc:
            raise _provider_error(
                "Gemini speech synthesis failed.", exc
            ) from exc

        decoded: tuple[bytes, str] | None = None
        for candidate in (
            getattr(response, "candidates", None) or []
        ):
            content = getattr(candidate, "content", None)
            for part in (
                getattr(content, "parts", None) or []
            ):
                decoded = _decode_inline_audio(part)
                if decoded is not None:
                    break
            if decoded is not None:
                break

        if decoded is None:
            raise VoiceProviderError(
                "Gemini returned no "
                "speech audio."
            )

        data, mime_type = decoded

        base_mime = mime_type.split(";")[0].strip()

        if base_mime in {
            "audio/l16",
            "audio/pcm",
        }:
            data = self._wav_from_pcm(
                data,
                sample_rate=_pcm_rate_from_mime(mime_type),
                channels=1,
            )

        elif base_mime not in {
            "audio/wav",
            "audio/x-wav",
        }:
            raise VoiceProviderError(
                "Gemini returned an "
                "unsupported speech format."
            )

        if not data:
            raise VoiceProviderError(
                "Gemini returned empty "
                "speech audio."
            )

        if (
            len(data)
            > self._max_audio_bytes
        ):
            raise VoiceProviderError(
                "Synthesized audio exceeds "
                "the configured size limit."
            )

        return SynthesizedSpeech(
            data=data,
            encoding=AudioEncoding.WAV,
            provider="gemini",
            model=self._model,
            voice=self._voice,
        )
