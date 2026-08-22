"""Privacy-conscious, optional voice input and output pipeline."""

from app.voice.audio import SoundDeviceAudioInput, WindowsWaveAudioOutput
from app.voice.models import VoiceSessionResult, VoiceSessionState
from app.voice.registry import (
    VoiceProviderRegistry,
    create_default_voice_provider_registry,
)
from app.voice.service import VoiceService
from app.voice.session import VoiceSession
from app.voice.wake import TextWakeWordDetector

__all__ = [
    "SoundDeviceAudioInput",
    "TextWakeWordDetector",
    "VoiceProviderRegistry",
    "VoiceService",
    "VoiceSession",
    "create_default_voice_provider_registry",
    "VoiceSessionResult",
    "VoiceSessionState",
    "WindowsWaveAudioOutput",
]
