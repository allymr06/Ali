from __future__ import annotations

from dataclasses import dataclass

from app.core.models import Request
from app.memory.analyzer import EXPLICIT_MEMORY_PREFIXES, MemoryCandidate
from app.memory.models import MemoryType


CONTEXTUAL_MEMORY_PREFIXES = (
    "bunu hatırla:",
    "bunu hatırla ",
    "bunu hatirla:",
    "bunu hatirla ",
)


@dataclass(frozen=True, slots=True)
class MemoryDecision:
    """Decision made by the memory policy."""

    should_remember: bool
    memory_type: MemoryType
    importance: float
    reason: str


class MemoryPolicy:
    """
    Decide whether information should become durable memory.

    Explicit user instructions always have the highest priority.
    """

    def evaluate(
        self,
        request: Request,
        candidate: MemoryCandidate | None = None,
    ) -> MemoryDecision:
        text = request.text.strip()
        normalized = text.casefold()

        # Explicit analyzer candidate has priority.
        if candidate is not None:
            if candidate.confidence < 0.5:
                return MemoryDecision(
                    should_remember=False,
                    memory_type=candidate.memory_type,
                    importance=0.5,
                    reason="Memory candidate confidence is too low.",
                )

            if candidate.reason == "Explicit user memory request.":
                return MemoryDecision(
                    should_remember=True,
                    memory_type=candidate.memory_type,
                    importance=0.9,
                    reason=candidate.reason,
                )

            return MemoryDecision(
                should_remember=True,
                memory_type=candidate.memory_type,
                importance=min(
                    max(candidate.confidence, 0.0),
                    1.0,
                ),
                reason=candidate.reason,
            )

        if normalized.startswith(
            EXPLICIT_MEMORY_PREFIXES + CONTEXTUAL_MEMORY_PREFIXES
        ):
            return MemoryDecision(
                should_remember=True,
                memory_type=MemoryType.FACT,
                importance=0.9,
                reason="Explicit user memory request.",
            )

        # Preference detection.
        preference_signals = (
            "tercih ediyorum",
            "tercih ederim",
            "seviyorum",
            "sevmiyorum",
            "istemiyorum",
            "istiyorum",
            "hoşuma gidiyor",
            "hoşuma gitmiyor",
        )

        if any(signal in normalized for signal in preference_signals):
            return MemoryDecision(
                should_remember=True,
                memory_type=MemoryType.PREFERENCE,
                importance=0.8,
                reason="User preference detected.",
            )

        return MemoryDecision(
            should_remember=False,
            memory_type=MemoryType.FACT,
            importance=0.5,
            reason="No memory-worthy signal detected.",
        )
