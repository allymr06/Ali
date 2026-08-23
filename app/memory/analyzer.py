from __future__ import annotations

from dataclasses import dataclass

from app.core.models import Request
from app.memory.models import MemoryType


EXPLICIT_MEMORY_PREFIXES = (
    "hatırla:",
    "hatırla ",
    "hatirla:",
    "hatirla ",
    "remember:",
    "remember ",
    "unutma:",
    "unutma ",
    "not al:",
    "not al ",
    "aklında tut:",
    "aklında tut ",
    "aklinda tut:",
    "aklinda tut ",
    "kaydet:",
)

# First-person identity statements are durable by nature: whoever
# says "benim adım X" expects X to be known tomorrow.
IDENTITY_MEMORY_PREFIXES = (
    "benim adım",
    "benim adim",
    "adım ",
    "adim ",
    "ben ",
    "my name is",
)
_IDENTITY_MARKERS = (
    "adım",
    "adim",
    "yaşındayım",
    "yasindayim",
    "yaşıyorum",
    "yasiyorum",
    "çalışıyorum",
    "calisiyorum",
    "okuyorum",
    "öğrenciyim",
    "ogrenciyim",
    "doğum günüm",
    "dogum gunum",
    "mesleğim",
    "meslegim",
    "my name is",
)


@dataclass(frozen=True, slots=True)
class MemoryCandidate:
    """A possible memory extracted from a user request."""

    content: str
    memory_type: MemoryType
    confidence: float
    reason: str


class MemoryAnalyzer:
    """
    Analyze user requests and extract possible memory candidates.

    This first implementation is deterministic by design.
    An AI-powered analyzer can be connected later without changing
    the rest of the memory pipeline.
    """

    def analyze(self, request: Request) -> MemoryCandidate | None:
        """Return a memory candidate when the request contains one."""

        normalized = request.text.strip()

        if not normalized:
            return None

        lowered = normalized.casefold()

        for prefix in EXPLICIT_MEMORY_PREFIXES:
            if lowered.startswith(prefix):
                content = normalized[len(prefix):].strip()

                if not content:
                    return None

                return MemoryCandidate(
                    content=content,
                    memory_type=MemoryType.FACT,
                    confidence=1.0,
                    reason="Explicit user memory request.",
                )

        if any(marker in lowered for marker in _IDENTITY_MARKERS) and (
            lowered.startswith(IDENTITY_MEMORY_PREFIXES)
        ):
            return MemoryCandidate(
                content=normalized,
                memory_type=MemoryType.FACT,
                confidence=0.8,
                reason="Identity statement.",
            )

        return None
