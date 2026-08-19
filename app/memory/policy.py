from __future__ import annotations

from dataclasses import dataclass

from app.core.models import Request
from app.memory.models import MemoryType


@dataclass(slots=True, frozen=True)
class MemoryDecision:
    """Result of a memory retention decision."""

    should_remember: bool
    memory_type: MemoryType = MemoryType.FACT
    importance: float = 0.5
    reason: str = ""


class MemoryPolicy:
    """
    Deterministic policy for deciding whether a request
    contains information worth remembering.

    This is intentionally conservative. A future intelligent
    policy layer can replace or extend these rules.
    """

    _EXPLICIT_MARKERS = (
        "hatırla",
        "unutma",
        "remember",
        "don't forget",
        "do not forget",
    )

    _PREFERENCE_MARKERS = (
        "seviyorum",
        "sevmiyorum",
        "tercih ediyorum",
        "tercih ederim",
        "prefer",
        "like",
        "don't like",
    )

    @staticmethod
    def _normalize(text: str) -> str:
        """
        Normalize text for case-insensitive marker matching.

        Turkish dotted and dotless I characters are normalized
        to a common representation so policy matching remains
        predictable.
        """
        return (
            text.strip()
            .casefold()
            .replace("ı", "i")
            .replace("İ", "i")
        )

    def evaluate(self, request: Request) -> MemoryDecision:
        """Evaluate whether a request should become a memory."""
        normalized = self._normalize(request.text)

        if not normalized:
            return MemoryDecision(
                should_remember=False,
                reason="Empty request.",
            )

        explicit_markers = tuple(
            self._normalize(marker)
            for marker in self._EXPLICIT_MARKERS
        )

        preference_markers = tuple(
            self._normalize(marker)
            for marker in self._PREFERENCE_MARKERS
        )

        if any(marker in normalized for marker in explicit_markers):
            return MemoryDecision(
                should_remember=True,
                memory_type=MemoryType.FACT,
                importance=0.9,
                reason="Explicit memory request.",
            )

        if any(marker in normalized for marker in preference_markers):
            return MemoryDecision(
                should_remember=True,
                memory_type=MemoryType.PREFERENCE,
                importance=0.8,
                reason="Detected user preference.",
            )

        return MemoryDecision(
            should_remember=False,
            reason="No memory-worthy signal detected.",
        )