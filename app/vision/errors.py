class VisionError(RuntimeError):
    """Base error for classified vision failures."""


class VisionConfigurationError(VisionError):
    """Vision is unavailable or incorrectly configured."""


class VisionCaptureError(VisionError):
    """The selected image source could not be captured."""


class VisionConsentError(VisionError):
    """Image capture lacks valid, current user consent."""


class VisionPrivacyError(VisionError):
    """A privacy transformation could not be applied safely."""


class VisionTimeoutError(VisionError, TimeoutError):
    """A bounded vision operation exceeded its deadline."""


class VisionInterrupted(VisionError):
    """The active vision operation was intentionally interrupted."""
