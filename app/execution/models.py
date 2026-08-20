from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class RetryPolicy:
    """Controls retry behavior for executable plan steps."""

    max_attempts: int = 1
    backoff_seconds: float = 0.0

    def __post_init__(self) -> None:
        if self.max_attempts < 1:
            raise ValueError("max_attempts must be at least 1.")
        if self.backoff_seconds < 0:
            raise ValueError("backoff_seconds cannot be negative.")

    @property
    def retries(self) -> int:
        return self.max_attempts - 1


@dataclass(frozen=True, slots=True)
class VerificationResult:
    """Structured verification result."""

    passed: bool
    reason: str = ""
