from __future__ import annotations

import json
from collections import deque
from datetime import UTC, datetime
from hashlib import sha256
from threading import RLock
from uuid import UUID, uuid4

from app.diagnostics.models import DiagnosticEvent, DiagnosticLevel
from app.diagnostics.privacy import (
    sanitize_attributes,
    sanitize_text,
    sanitize_trace_id,
)


def _event_hash(
    *,
    sequence: int,
    component: str,
    name: str,
    message: str,
    level: DiagnosticLevel,
    attributes: dict[str, object],
    previous_hash: str,
    event_id: UUID,
    observed_at: datetime,
    trace_id: str | None,
) -> str:
    payload = {
        "sequence": sequence,
        "component": component,
        "name": name,
        "message": message,
        "level": level.value,
        "attributes": attributes,
        "previous_hash": previous_hash,
        "event_id": str(event_id),
        "observed_at": observed_at.isoformat(),
        "trace_id": trace_id,
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return sha256(encoded).hexdigest()


class DiagnosticLedger:
    """Thread-safe bounded, tamper-evident in-memory diagnostic ledger."""

    def __init__(self, capacity: int = 2_000) -> None:
        if capacity < 1:
            raise ValueError("Diagnostic ledger capacity must be positive.")
        self._events: deque[DiagnosticEvent] = deque()
        self._capacity = capacity
        self._anchor_hash = "0" * 64
        self._sequence = 0
        self._lock = RLock()

    @property
    def capacity(self) -> int:
        return self._capacity

    def append(
        self,
        component: str,
        name: str,
        message: str,
        *,
        level: DiagnosticLevel = DiagnosticLevel.INFO,
        attributes: dict[str, object] | None = None,
        trace_id: str | None = None,
    ) -> DiagnosticEvent:
        normalized_component = sanitize_text(component, limit=100).strip()
        normalized_name = sanitize_text(name, limit=100).strip()
        if not normalized_component or not normalized_name:
            raise ValueError("Diagnostic component and name cannot be empty.")
        clean_attributes = sanitize_attributes(attributes)
        clean_message = sanitize_text(message)
        clean_trace = sanitize_trace_id(trace_id)
        with self._lock:
            self._sequence += 1
            previous = self._events[-1].event_hash if self._events else self._anchor_hash
            event_id = uuid4()
            observed_at = datetime.now(UTC)
            digest = _event_hash(
                sequence=self._sequence,
                component=normalized_component,
                name=normalized_name,
                message=clean_message,
                level=level,
                attributes=clean_attributes,
                previous_hash=previous,
                event_id=event_id,
                observed_at=observed_at,
                trace_id=clean_trace,
            )
            event = DiagnosticEvent(
                sequence=self._sequence,
                component=normalized_component,
                name=normalized_name,
                message=clean_message,
                level=level,
                attributes=clean_attributes,
                previous_hash=previous,
                event_hash=digest,
                event_id=event_id,
                observed_at=observed_at,
                trace_id=clean_trace,
            )
            self._events.append(event)
            if len(self._events) > self._capacity:
                self._anchor_hash = self._events.popleft().event_hash
            return event

    def list(
        self,
        *,
        limit: int = 100,
        level: DiagnosticLevel | None = None,
        component: str | None = None,
    ) -> tuple[DiagnosticEvent, ...]:
        if not 1 <= limit <= 500:
            raise ValueError("Diagnostic event limit must be between 1 and 500.")
        normalized_component = component.strip().casefold() if component else None
        with self._lock:
            selected = [
                event
                for event in reversed(self._events)
                if (level is None or event.level is level)
                and (
                    normalized_component is None
                    or event.component.casefold() == normalized_component
                )
            ]
            return tuple(selected[:limit])

    def verify_integrity(self) -> bool:
        with self._lock:
            previous = self._anchor_hash
            for event in self._events:
                if event.previous_hash != previous:
                    return False
                expected = _event_hash(
                    sequence=event.sequence,
                    component=event.component,
                    name=event.name,
                    message=event.message,
                    level=event.level,
                    attributes=event.attributes,
                    previous_hash=event.previous_hash,
                    event_id=event.event_id,
                    observed_at=event.observed_at,
                    trace_id=event.trace_id,
                )
                if expected != event.event_hash:
                    return False
                previous = event.event_hash
            return True

    def __len__(self) -> int:
        with self._lock:
            return len(self._events)
