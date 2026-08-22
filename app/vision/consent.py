from __future__ import annotations

import hashlib
import hmac
import json
import secrets
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from threading import RLock
from uuid import UUID, uuid4

from app.core.time import utc_now
from app.vision.errors import VisionConsentError
from app.vision.models import RedactionRegion, VisionSourceKind


@dataclass(frozen=True, slots=True)
class VisionConsentRequest:
    request_id: UUID
    purpose: str
    source_kind: VisionSourceKind
    source_id: str
    redactions: tuple[RedactionRegion, ...]
    disclosure: str
    created_at: datetime
    expires_at: datetime
    fingerprint: str


@dataclass(frozen=True, slots=True)
class VisionConsentGrant:
    grant_id: UUID
    request_id: UUID
    fingerprint: str
    granted_at: datetime
    expires_at: datetime


@dataclass(slots=True)
class _ConsentRecord:
    request: VisionConsentRequest
    approved: bool = False
    consumed: bool = False
    grant_id: UUID | None = None


class VisionConsentGate:
    """Issues short-lived, one-use consent bound to the exact capture purpose."""

    def __init__(self, *, ttl_seconds: float = 60.0, capacity: int = 100) -> None:
        if ttl_seconds <= 0 or capacity < 1:
            raise ValueError("Consent limits must be positive.")
        self._ttl = ttl_seconds
        self._capacity = capacity
        self._secret = secrets.token_bytes(32)
        self._records: dict[UUID, _ConsentRecord] = {}
        self._lock = RLock()

    def _fingerprint(
        self,
        purpose: str,
        source_kind: VisionSourceKind,
        source_id: str,
        redactions: tuple[RedactionRegion, ...],
    ) -> str:
        payload = json.dumps(
            {
                "purpose": purpose.strip(),
                "source_kind": source_kind.value,
                "source_id": source_id.strip(),
                "redactions": [item.canonical() for item in redactions],
            },
            separators=(",", ":"),
            ensure_ascii=True,
        ).encode()
        return hmac.new(self._secret, payload, hashlib.sha256).hexdigest()

    def request(
        self,
        *,
        purpose: str,
        source_kind: VisionSourceKind,
        source_id: str,
        redactions: tuple[RedactionRegion, ...] = (),
    ) -> VisionConsentRequest:
        normalized = purpose.strip()
        if not normalized or not source_id.strip():
            raise ValueError("Consent purpose and source cannot be empty.")
        now = utc_now()
        request = VisionConsentRequest(
            request_id=uuid4(),
            purpose=normalized,
            source_kind=source_kind,
            source_id=source_id.strip(),
            redactions=tuple(redactions),
            disclosure=(
                f"JARVIS requests one screen capture for: {normalized}. "
                f"Source: {source_kind.value}/{source_id.strip()}. "
                f"User-selected redacted regions: {len(redactions)}. "
                "Configured automatic privacy masks are also applied. "
                "The processed image may be sent to the configured vision "
                "provider; the raw capture is discarded by default."
            ),
            created_at=now,
            expires_at=now + timedelta(seconds=self._ttl),
            fingerprint=self._fingerprint(
                normalized, source_kind, source_id, tuple(redactions)
            ),
        )
        with self._lock:
            while len(self._records) >= self._capacity:
                self._records.pop(next(iter(self._records)))
            self._records[request.request_id] = _ConsentRecord(request)
        return request

    def approve(self, request_id: UUID) -> VisionConsentGrant:
        with self._lock:
            record = self._get_current(request_id)
            if record.approved or record.consumed:
                raise VisionConsentError("Vision consent is no longer approvable.")
            record.approved = True
            now = utc_now()
            record.grant_id = uuid4()
            return VisionConsentGrant(
                grant_id=record.grant_id,
                request_id=request_id,
                fingerprint=record.request.fingerprint,
                granted_at=now,
                expires_at=record.request.expires_at,
            )

    def validate_and_consume(
        self,
        grant: VisionConsentGrant,
        *,
        purpose: str,
        source_kind: VisionSourceKind,
        source_id: str,
        redactions: tuple[RedactionRegion, ...],
    ) -> UUID:
        with self._lock:
            record = self._get_current(grant.request_id)
            expected = self._fingerprint(purpose, source_kind, source_id, redactions)
            if not record.approved or record.consumed:
                raise VisionConsentError("Vision consent is unavailable or already used.")
            if grant.grant_id != record.grant_id:
                raise VisionConsentError("Vision consent grant identity is invalid.")
            if grant.fingerprint != expected or record.request.fingerprint != expected:
                raise VisionConsentError("Vision consent does not match this capture.")
            if grant.expires_at <= utc_now():
                raise VisionConsentError("Vision consent expired.")
            record.consumed = True
            return grant.grant_id

    def deny(self, request_id: UUID) -> None:
        with self._lock:
            record = self._get_current(request_id)
            record.consumed = True

    def _get_current(self, request_id: UUID) -> _ConsentRecord:
        try:
            record = self._records[request_id]
        except KeyError as exc:
            raise VisionConsentError("Unknown vision consent request.") from exc
        if record.request.expires_at <= utc_now():
            raise VisionConsentError("Vision consent request expired.")
        return record
