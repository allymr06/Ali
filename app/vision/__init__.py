"""Consent-bound, privacy-preserving image and screen understanding."""

from app.vision.capture import WindowsScreenSource
from app.vision.consent import VisionConsentGate, VisionConsentGrant, VisionConsentRequest
from app.vision.models import RedactionRegion, VisionDetail, VisionSessionState
from app.vision.service import VisionService

__all__ = [
    "RedactionRegion",
    "VisionConsentGate",
    "VisionConsentGrant",
    "VisionConsentRequest",
    "VisionDetail",
    "VisionService",
    "VisionSessionState",
    "WindowsScreenSource",
]
