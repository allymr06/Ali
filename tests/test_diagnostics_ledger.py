from __future__ import annotations

import pytest

from app.diagnostics.ledger import DiagnosticLedger
from app.diagnostics.models import DiagnosticLevel


def test_diagnostic_ledger_builds_verifiable_hash_chain() -> None:
    ledger = DiagnosticLedger(capacity=3)
    first = ledger.append("core", "request.started", "Started", trace_id="trace-1")
    second = ledger.append(
        "core",
        "request.completed",
        "Completed",
        attributes={"outcome": "completed"},
    )

    assert second.previous_hash == first.event_hash
    assert first.trace_id != "trace-1"
    assert len(first.trace_id or "") == 32
    assert ledger.verify_integrity() is True
    assert [event.sequence for event in ledger.list()] == [2, 1]


def test_diagnostic_ledger_preserves_integrity_after_bounded_eviction() -> None:
    ledger = DiagnosticLedger(capacity=2)
    for index in range(5):
        ledger.append("worker", f"event.{index}", "value")

    assert len(ledger) == 2
    assert [event.sequence for event in ledger.list()] == [5, 4]
    assert ledger.verify_integrity() is True


def test_diagnostic_ledger_filters_and_detects_tampering() -> None:
    ledger = DiagnosticLedger()
    ledger.append("core", "one", "ok")
    warning = ledger.append(
        "tools", "two", "warn", level=DiagnosticLevel.WARNING
    )

    assert ledger.list(level=DiagnosticLevel.WARNING) == (warning,)
    assert ledger.list(component="CORE")[0].name == "one"
    warning.attributes["tampered"] = True
    assert ledger.verify_integrity() is False


def test_diagnostic_ledger_sanitizes_before_storage() -> None:
    event = DiagnosticLedger().append(
        "provider",
        "failed",
        "Bearer abcdefghijklmnop",
        attributes={"password": "secret", "reason": "safe"},
    )
    assert event.message == "[REDACTED]"
    assert event.attributes["password"] == "[REDACTED]"
    assert event.attributes["reason"] == "safe"


def test_diagnostic_ledger_validates_capacity_and_query_limit() -> None:
    with pytest.raises(ValueError, match="capacity"):
        DiagnosticLedger(0)
    with pytest.raises(ValueError, match="between"):
        DiagnosticLedger().list(limit=501)
