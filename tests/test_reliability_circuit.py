from __future__ import annotations

import pytest

from app.reliability.circuit import CircuitBreaker, CircuitState


def test_circuit_opens_half_opens_and_recovers_with_one_probe() -> None:
    now = [100.0]
    circuit = CircuitBreaker(2, 10, clock=lambda: now[0])

    assert circuit.allow() is True
    circuit.failure()
    assert circuit.snapshot().state is CircuitState.CLOSED
    assert circuit.allow() is True
    circuit.failure()
    assert circuit.snapshot().state is CircuitState.OPEN
    assert circuit.allow() is False

    now[0] += 10
    assert circuit.allow() is True
    assert circuit.snapshot().state is CircuitState.HALF_OPEN
    assert circuit.allow() is False
    circuit.success()
    assert circuit.snapshot().state is CircuitState.CLOSED
    assert circuit.snapshot().consecutive_failures == 0


def test_failed_half_open_probe_reopens_circuit() -> None:
    now = [0.0]
    circuit = CircuitBreaker(1, 5, clock=lambda: now[0])
    circuit.failure()
    now[0] = 5
    assert circuit.allow() is True
    circuit.failure()
    assert circuit.snapshot().state is CircuitState.OPEN
    assert circuit.snapshot().opened_at == 5


def test_circuit_validates_limits() -> None:
    with pytest.raises(ValueError, match="limits"):
        CircuitBreaker(0, 1)
    with pytest.raises(ValueError, match="limits"):
        CircuitBreaker(1, 0)
