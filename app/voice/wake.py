from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class WakeWordMatch:
    detected: bool
    command: str


class TextWakeWordDetector:
    """Deterministic wake-word gate applied to a completed transcript."""

    def __init__(self, wake_word: str = "jarvis") -> None:
        normalized = wake_word.strip()
        if not normalized:
            raise ValueError("Wake word cannot be empty.")
        self.wake_word = normalized
        self._pattern = re.compile(
            rf"(?<!\w){re.escape(normalized)}(?!\w)",
            re.IGNORECASE,
        )

    def match(self, transcript: str) -> WakeWordMatch:
        match = self._pattern.search(transcript)
        if match is None:
            return WakeWordMatch(False, "")
        command = (transcript[: match.start()] + " " + transcript[match.end() :]).strip()
        command = re.sub(r"^[\s,.:;!?-]+|[\s]+", " ", command).strip()
        return WakeWordMatch(True, command)
