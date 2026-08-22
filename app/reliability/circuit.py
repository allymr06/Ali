from __future__ import annotations

import time
from dataclasses import dataclass
from enum import StrEnum
from threading import RLock


class CircuitState(StrEnum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


@dataclass(frozen=True, slots=True)
class CircuitSnapshot:
    state: CircuitState
    consecutive_failures: int
    opened_at: float | None


class CircuitBreaker:
    """Thread-safe closed/open/half-open breaker with one recovery probe."""

    def __init__(
        self,
        failure_threshold: int = 5,
        recovery_seconds: float = 30.0,
        *,
        clock=time.monotonic,
    ) -> None:
        if failure_threshold < 1 or recovery_seconds <= 0:
            raise ValueError("Circuit breaker limits are invalid.")
        self._threshold = failure_threshold
        self._recovery_seconds = recovery_seconds
        self._clock = clock
        self._state = CircuitState.CLOSED
        self._failures = 0
        self._opened_at: float | None = None
        self._probe_active = False
        self._lock = RLock()

    def allow(self) -> bool:
        with self._lock:
            if self._state is CircuitState.CLOSED:
                return True
            now = self._clock()
            if (
                self._state is CircuitState.OPEN
                and self._opened_at is not None
                and now - self._opened_at >= self._recovery_seconds
            ):
                self._state = CircuitState.HALF_OPEN
                self._probe_active = False
            if self._state is CircuitState.HALF_OPEN and not self._probe_active:
                self._probe_active = True
                return True
            return False

    def success(self) -> None:
        with self._lock:
            self._state = CircuitState.CLOSED
            self._failures = 0
            self._opened_at = None
            self._probe_active = False

    def failure(self) -> None:
        with self._lock:
            self._failures += 1
            self._probe_active = False
            if (
                self._state is CircuitState.HALF_OPEN
                or self._failures >= self._threshold
            ):
                self._state = CircuitState.OPEN
                self._opened_at = self._clock()

    def snapshot(self) -> CircuitSnapshot:
        with self._lock:
            return CircuitSnapshot(
                self._state, self._failures, self._opened_at
            )
