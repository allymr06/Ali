from __future__ import annotations

import pytest

from app.diagnostics.metrics import MetricRegistry


def test_metric_registry_records_bounded_low_cardinality_values() -> None:
    metrics = MetricRegistry(max_metrics=3)
    metrics.increment("core.requests")
    metrics.increment("core.requests", 2)
    metrics.gauge("tasks.active", 4)
    metrics.observe("core.duration", 0.2)
    metrics.observe("core.duration", 0.4)

    snapshot = metrics.snapshot()

    assert snapshot["counters"]["core.requests"] == 3
    assert snapshot["gauges"]["tasks.active"] == 4
    assert snapshot["timers"]["core.duration"] == {
        "count": 2,
        "total_seconds": pytest.approx(0.6),
        "average_seconds": pytest.approx(0.3),
        "minimum_seconds": 0.2,
        "maximum_seconds": 0.4,
    }


def test_metric_registry_rejects_invalid_values_and_capacity_overflow() -> None:
    metrics = MetricRegistry(max_metrics=1)
    with pytest.raises(ValueError, match="name"):
        metrics.increment("INVALID METRIC")
    with pytest.raises(ValueError, match="negative"):
        metrics.increment("valid", -1)
    with pytest.raises(ValueError, match="negative"):
        metrics.observe("duration", -0.1)
    metrics.increment("one")
    with pytest.raises(RuntimeError, match="capacity"):
        metrics.gauge("two", 2)
