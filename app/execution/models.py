from __future__ import annotations

import time
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


@dataclass(frozen=True, slots=True)
class ExecutionLimits:
    """Hard limits applied to one Core execution."""

    max_plan_steps: int = 100
    max_tool_calls: int = 100
    max_model_iterations: int = 5
    max_model_tokens: int = 100_000
    timeout_seconds: float = 300.0

    def __post_init__(self) -> None:
        for name in (
            "max_plan_steps",
            "max_tool_calls",
            "max_model_iterations",
            "max_model_tokens",
        ):
            if getattr(self, name) < 1:
                raise ValueError(f"{name} must be at least 1.")

        if self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be greater than 0.")


@dataclass(slots=True)
class ExecutionUsage:
    """Mutable usage counters for one bounded execution."""

    model_iterations: int = 0
    tool_calls: int = 0
    plan_steps: int = 0
    retries: int = 0
    model_tokens: int = 0
    started_monotonic: float = 0.0

    def start(self) -> None:
        if self.started_monotonic == 0.0:
            self.started_monotonic = time.monotonic()

    @property
    def elapsed_seconds(self) -> float:
        if self.started_monotonic == 0.0:
            return 0.0

        return max(0.0, time.monotonic() - self.started_monotonic)

    def remaining_seconds(self, limits: ExecutionLimits) -> float:
        return max(0.0, limits.timeout_seconds - self.elapsed_seconds)

    def to_dict(self) -> dict[str, int | float]:
        return {
            "model_iterations": self.model_iterations,
            "tool_calls": self.tool_calls,
            "plan_steps": self.plan_steps,
            "retries": self.retries,
            "model_tokens": self.model_tokens,
            "elapsed_seconds": self.elapsed_seconds,
        }
