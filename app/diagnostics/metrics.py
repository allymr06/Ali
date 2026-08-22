from __future__ import annotations

import re
from dataclasses import dataclass
from threading import RLock

_NAME = re.compile(r"^[a-z][a-z0-9_.-]{0,99}$")


@dataclass(slots=True)
class _Timer:
    count: int = 0
    total: float = 0.0
    minimum: float | None = None
    maximum: float | None = None


class MetricRegistry:
    """Bounded low-cardinality counters, gauges, and duration summaries."""

    def __init__(self, max_metrics: int = 200) -> None:
        if max_metrics < 1:
            raise ValueError("Metric capacity must be positive.")
        self._max_metrics = max_metrics
        self._counters: dict[str, float] = {}
        self._gauges: dict[str, float] = {}
        self._timers: dict[str, _Timer] = {}
        self._lock = RLock()

    @staticmethod
    def _validate(name: str) -> str:
        normalized = name.strip().casefold()
        if not _NAME.fullmatch(normalized):
            raise ValueError("Metric name is invalid.")
        return normalized

    def _reserve(self, name: str) -> None:
        names = set(self._counters) | set(self._gauges) | set(self._timers)
        if name not in names and len(names) >= self._max_metrics:
            raise RuntimeError("Metric registry capacity reached.")

    def increment(self, name: str, amount: float = 1.0) -> None:
        if amount < 0:
            raise ValueError("Counter increments cannot be negative.")
        key = self._validate(name)
        with self._lock:
            self._reserve(key)
            self._counters[key] = self._counters.get(key, 0.0) + amount

    def gauge(self, name: str, value: float) -> None:
        key = self._validate(name)
        with self._lock:
            self._reserve(key)
            self._gauges[key] = float(value)

    def observe(self, name: str, seconds: float) -> None:
        if seconds < 0:
            raise ValueError("Observed duration cannot be negative.")
        key = self._validate(name)
        with self._lock:
            self._reserve(key)
            timer = self._timers.setdefault(key, _Timer())
            timer.count += 1
            timer.total += seconds
            timer.minimum = seconds if timer.minimum is None else min(timer.minimum, seconds)
            timer.maximum = seconds if timer.maximum is None else max(timer.maximum, seconds)

    def snapshot(self) -> dict[str, object]:
        with self._lock:
            return {
                "counters": dict(self._counters),
                "gauges": dict(self._gauges),
                "timers": {
                    name: {
                        "count": value.count,
                        "total_seconds": value.total,
                        "average_seconds": value.total / value.count,
                        "minimum_seconds": value.minimum,
                        "maximum_seconds": value.maximum,
                    }
                    for name, value in self._timers.items()
                },
            }
