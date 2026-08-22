class VoiceError(RuntimeError):
    """Base error for classified voice failures."""


class VoiceConfigurationError(VoiceError):
    """Voice capability is unavailable or incorrectly configured."""


class VoiceDeviceError(VoiceError):
    """An audio device failed or disappeared."""


class VoiceProviderError(VoiceError):
    """A speech provider failed without exposing sensitive details."""


class VoiceTimeoutError(VoiceError, TimeoutError):
    """A bounded voice operation exceeded its deadline."""


class VoiceInterrupted(VoiceError):
    """The active voice operation was intentionally interrupted."""
