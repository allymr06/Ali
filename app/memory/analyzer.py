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

        return None
