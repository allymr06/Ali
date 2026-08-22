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


async def _create_interaction(
    client,
    **kwargs,
):
    result = await asyncio.to_thread(
        client.interactions.create,
        **kwargs,
    )

    if inspect.isawaitable(result):
        result = await result

    return result


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

    async def transcribe(
        self,
        capture: AudioCapture,
        *,
        language: str | None = None,
    ) -> TranscriptionResult:
        client = self._require_client()

        audio = base64.b64encode(
            capture.to_wav_bytes()
        ).decode("ascii")

        instruction = (
            "Transcribe only the spoken words "
            "in this audio. Return plain text "
            "only. Do not summarize, explain, "
            "or add commentary."
        )

        if language:
            instruction += (
                " The expected language is "
                f"{language}."
            )

        try:
            interaction = (
                await _create_interaction(
                    client,
                    model=self._model,
                    input=[
                        {
                            "type": "text",
                            "text": instruction,
                        },
                        {
                            "type": "audio",
                            "data": audio,
                            "mime_type": (
                                "audio/wav"
                            ),
                        },
                    ],
                    response_format={
                        "type": "text",
                    },
                    store=False,
                )
            )

        except Exception as exc:
            raise VoiceProviderError(
                "Gemini speech "
                "transcription failed."
            ) from exc

        text = getattr(
            interaction,
            "output_text",
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
            interaction = (
                await _create_interaction(
                    client,
                    model=self._model,
                    input=prompt,
                    response_format={
                        "type": "audio",
                        "mime_type": (
                            "audio/wav"
                        ),
                        "delivery": "inline",
                    },
                    generation_config={
                        "speech_config": [
                            {
                                "voice": (
                                    self._voice
                                )
                            }
                        ]
                    },
                    store=False,
                )
            )

        except Exception as exc:
            raise VoiceProviderError(
                "Gemini speech synthesis "
                "failed."
            ) from exc

        audio = getattr(
            interaction,
            "output_audio",
            None,
        )

        if audio is None:
            raise VoiceProviderError(
                "Gemini returned no "
                "speech audio."
            )

        raw_data = getattr(
            audio,
            "data",
            None,
        )

        if isinstance(
            raw_data,
            str,
        ):
            try:
                data = (
                    base64.b64decode(
                        raw_data
                    )
                )
            except Exception as exc:
                raise VoiceProviderError(
                    "Gemini returned invalid "
                    "speech audio."
                ) from exc

        elif isinstance(
            raw_data,
            (bytes, bytearray),
        ):
            data = bytes(raw_data)

        else:
            raise VoiceProviderError(
                "Gemini returned invalid "
                "speech audio."
            )

        raw_mime_type = getattr(
            audio,
            "mime_type",
            "audio/wav",
        )

        mime_type = getattr(
            raw_mime_type,
            "value",
            raw_mime_type,
        )

        mime_type = str(
            mime_type
        ).lower()

        if mime_type in {
            "audio/l16",
            "audio/pcm",
        }:
            sample_rate = int(
                getattr(
                    audio,
                    "sample_rate",
                    24_000,
                )
                or 24_000
            )

            channels = int(
                getattr(
                    audio,
                    "channels",
                    1,
                )
                or 1
            )

            data = self._wav_from_pcm(
                data,
                sample_rate=sample_rate,
                channels=channels,
            )

        elif (
            mime_type
            not in {
                "audio/wav",
                "audio/x-wav",
            }
        ):
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
