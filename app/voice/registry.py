from __future__ import annotations

from collections.abc import Callable
from threading import RLock

from app.config.settings import Settings
from app.voice.base import (
    SpeechRecognizer,
    SpeechSynthesizer,
)
from app.voice.errors import (
    VoiceConfigurationError,
)


RecognizerFactory = Callable[
    [Settings],
    SpeechRecognizer,
]

SynthesizerFactory = Callable[
    [Settings],
    SpeechSynthesizer,
]


class VoiceProviderRegistry:
    """
    Registry for independently pluggable STT and TTS providers.

    A provider may expose recognition, synthesis, or both.
    The LLM provider remains completely independent.
    """

    def __init__(self) -> None:
        self._recognizers: dict[
            str,
            RecognizerFactory,
        ] = {}

        self._synthesizers: dict[
            str,
            SynthesizerFactory,
        ] = {}

        self._lock = RLock()

    @staticmethod
    def _normalize(
        name: str,
    ) -> str:
        if not isinstance(name, str):
            raise TypeError(
                "Voice provider name must be a string."
            )

        normalized = (
            name
            .strip()
            .casefold()
        )

        if not normalized:
            raise ValueError(
                "Voice provider name cannot be empty."
            )

        return normalized

    def register_recognizer(
        self,
        name: str,
        factory: RecognizerFactory,
    ) -> None:
        normalized = self._normalize(
            name
        )

        if not callable(factory):
            raise TypeError(
                "Recognizer factory must be callable."
            )

        with self._lock:
            if normalized in self._recognizers:
                raise ValueError(
                    "Speech recognition provider "
                    f"'{normalized}' is already registered."
                )

            self._recognizers[
                normalized
            ] = factory

    def register_synthesizer(
        self,
        name: str,
        factory: SynthesizerFactory,
    ) -> None:
        normalized = self._normalize(
            name
        )

        if not callable(factory):
            raise TypeError(
                "Synthesizer factory must be callable."
            )

        with self._lock:
            if normalized in self._synthesizers:
                raise ValueError(
                    "Speech synthesis provider "
                    f"'{normalized}' is already registered."
                )

            self._synthesizers[
                normalized
            ] = factory

    def contains_recognizer(
        self,
        name: str,
    ) -> bool:
        normalized = self._normalize(
            name
        )

        with self._lock:
            return (
                normalized
                in self._recognizers
            )

    def contains_synthesizer(
        self,
        name: str,
    ) -> bool:
        normalized = self._normalize(
            name
        )

        with self._lock:
            return (
                normalized
                in self._synthesizers
            )

    def list_recognizer_providers(
        self,
    ) -> tuple[str, ...]:
        with self._lock:
            return tuple(
                self._recognizers
            )

    def list_synthesizer_providers(
        self,
    ) -> tuple[str, ...]:
        with self._lock:
            return tuple(
                self._synthesizers
            )

    def create_recognizer(
        self,
        name: str,
        settings: Settings,
    ) -> SpeechRecognizer:
        normalized = self._normalize(
            name
        )

        with self._lock:
            factory = (
                self._recognizers
                .get(normalized)
            )

        if factory is None:
            raise VoiceConfigurationError(
                "Speech recognition provider "
                f"'{normalized}' is not registered."
            )

        adapter = factory(
            settings
        )

        if not isinstance(
            adapter,
            SpeechRecognizer,
        ):
            raise TypeError(
                "Recognizer factory returned "
                "an invalid adapter."
            )

        return adapter

    def create_synthesizer(
        self,
        name: str,
        settings: Settings,
    ) -> SpeechSynthesizer:
        normalized = self._normalize(
            name
        )

        with self._lock:
            factory = (
                self._synthesizers
                .get(normalized)
            )

        if factory is None:
            raise VoiceConfigurationError(
                "Speech synthesis provider "
                f"'{normalized}' is not registered."
            )

        adapter = factory(
            settings
        )

        if not isinstance(
            adapter,
            SpeechSynthesizer,
        ):
            raise TypeError(
                "Synthesizer factory returned "
                "an invalid adapter."
            )

        return adapter

    @staticmethod
    def _resolve_provider(
        requested: str | None,
        *,
        default_provider: str,
        fallback_provider: str | None,
        supported: frozenset[str],
        role: str,
    ) -> str:
        requested_name = (
            "auto"
            if requested is None
            else requested
        )

        requested_name = (
            requested_name
            .strip()
            .casefold()
        )

        if not requested_name:
            raise VoiceConfigurationError(
                f"{role} provider cannot be empty."
            )

        if requested_name != "auto":
            if requested_name not in supported:
                raise VoiceConfigurationError(
                    f"{role} provider "
                    f"'{requested_name}' "
                    "is not registered."
                )

            return requested_name

        default_name = (
            default_provider
            .strip()
            .casefold()
        )

        if (
            default_name
            and default_name in supported
        ):
            return default_name

        if fallback_provider is not None:
            fallback_name = (
                fallback_provider
                .strip()
                .casefold()
            )

            if fallback_name in supported:
                return fallback_name

        raise VoiceConfigurationError(
            f"No {role.lower()} provider "
            "can satisfy automatic selection."
        )

    def resolve_recognizer_provider(
        self,
        requested: str | None,
        *,
        default_provider: str,
        fallback_provider: str | None = None,
    ) -> str:
        with self._lock:
            supported = frozenset(
                self._recognizers
            )

        return self._resolve_provider(
            requested,
            default_provider=default_provider,
            fallback_provider=fallback_provider,
            supported=supported,
            role="Speech recognition",
        )

    def resolve_synthesizer_provider(
        self,
        requested: str | None,
        *,
        default_provider: str,
        fallback_provider: str | None = None,
    ) -> str:
        with self._lock:
            supported = frozenset(
                self._synthesizers
            )

        return self._resolve_provider(
            requested,
            default_provider=default_provider,
            fallback_provider=fallback_provider,
            supported=supported,
            role="Speech synthesis",
        )


def create_default_voice_provider_registry(
) -> VoiceProviderRegistry:
    """
    Create the built-in speech provider composition root.

    Adding another STT or TTS adapter no longer requires
    changing the JARVIS bootstrap routing logic.
    """
    from app.voice.elevenlabs import ElevenLabsSpeechSynthesizer
    from app.voice.gemini import (
        GeminiSpeechRecognizer,
        GeminiSpeechSynthesizer,
    )
    registry = VoiceProviderRegistry()

    registry.register_recognizer(
        "gemini",
        GeminiSpeechRecognizer,
    )
    registry.register_synthesizer(
        "gemini",
        GeminiSpeechSynthesizer,
    )
    registry.register_synthesizer(
        "elevenlabs",
        ElevenLabsSpeechSynthesizer,
    )

    return registry
