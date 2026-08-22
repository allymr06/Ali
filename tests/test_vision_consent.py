from __future__ import annotations

import pytest

from app.vision.consent import VisionConsentGate
from app.vision.errors import VisionConsentError
from app.vision.models import RedactionRegion, VisionSourceKind


def request(gate, *, purpose="Inspect the screen", redactions=()):
    return gate.request(
        purpose=purpose,
        source_kind=VisionSourceKind.VIRTUAL_SCREEN,
        source_id="screen",
        redactions=redactions,
    )


def consume(gate, grant, *, purpose="Inspect the screen", redactions=()):
    return gate.validate_and_consume(
        grant,
        purpose=purpose,
        source_kind=VisionSourceKind.VIRTUAL_SCREEN,
        source_id="screen",
        redactions=redactions,
    )


def test_consent_disclosure_is_explicit_and_grant_is_single_use() -> None:
    gate = VisionConsentGate()
    consent = request(gate, redactions=(RedactionRegion(0, 0, 1, 1),))

    assert "one screen capture" in consent.disclosure
    assert "User-selected redacted regions: 1" in consent.disclosure
    assert "configured vision provider" in consent.disclosure
    assert "raw capture is discarded" in consent.disclosure
    grant = gate.approve(consent.request_id)
    assert consume(
        gate, grant, redactions=(RedactionRegion(0, 0, 1, 1),)
    ) == grant.grant_id
    with pytest.raises(VisionConsentError, match="already used"):
        consume(gate, grant, redactions=(RedactionRegion(0, 0, 1, 1),))


@pytest.mark.parametrize(
    "changed",
    [
        {"purpose": "Different purpose"},
        {"redactions": (RedactionRegion(1, 1, 1, 1),)},
    ],
)
def test_consent_is_bound_to_exact_capture(changed) -> None:
    gate = VisionConsentGate()
    consent = request(gate)
    grant = gate.approve(consent.request_id)

    with pytest.raises(VisionConsentError, match="does not match"):
        consume(gate, grant, **changed)


def test_denied_or_unknown_consent_fails_closed() -> None:
    gate = VisionConsentGate()
    consent = request(gate)
    gate.deny(consent.request_id)
    with pytest.raises(VisionConsentError):
        gate.approve(consent.request_id)

    from uuid import uuid4

    with pytest.raises(VisionConsentError, match="Unknown"):
        gate.approve(uuid4())


def test_consent_capacity_is_bounded() -> None:
    gate = VisionConsentGate(capacity=1)
    first = request(gate)
    request(gate, purpose="Second")
    with pytest.raises(VisionConsentError, match="Unknown"):
        gate.approve(first.request_id)


def test_consent_rejects_altered_grant_identity() -> None:
    from dataclasses import replace
    from uuid import uuid4

    gate = VisionConsentGate()
    consent = request(gate)
    grant = gate.approve(consent.request_id)

    with pytest.raises(VisionConsentError, match="identity"):
        consume(gate, replace(grant, grant_id=uuid4()))


def test_consent_expiry_fails_closed(monkeypatch) -> None:
    from datetime import timedelta

    import app.vision.consent as consent_module
    from app.core.time import utc_now

    current = [utc_now()]
    monkeypatch.setattr(consent_module, "utc_now", lambda: current[0])
    gate = VisionConsentGate(ttl_seconds=1)
    consent = request(gate)
    current[0] += timedelta(seconds=2)

    with pytest.raises(VisionConsentError, match="expired"):
        gate.approve(consent.request_id)
