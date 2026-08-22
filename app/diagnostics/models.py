from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from uuid import UUID, uuid4


class DiagnosticLevel(StrEnum):
    DEBUG = "debug"
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"


class HealthStatus(StrEnum):
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNHEALTHY = "unhealthy"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class DiagnosticEvent:
    sequence: int
    component: str
    name: str
    message: str
    level: DiagnosticLevel
    attributes: dict[str, object]
    previous_hash: str
    event_hash: str
    event_id: UUID = field(default_factory=uuid4)
    observed_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    trace_id: str | None = None


@dataclass(frozen=True, slots=True)
class HealthResult:
    name: str
    component: str
    status: HealthStatus
    observed_at: datetime
    latency_ms: float
    message: str
    details: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class HealthReport:
    status: HealthStatus
    checks: tuple[HealthResult, ...]
    observed_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def to_dict(self) -> dict[str, object]:
        return {
            "status": self.status.value,
            "observed_at": self.observed_at.isoformat(),
            "checks": [
                {
                    "name": item.name,
                    "component": item.component,
                    "status": item.status.value,
                    "observed_at": item.observed_at.isoformat(),
                    "latency_ms": item.latency_ms,
                    "message": item.message,
                    "details": dict(item.details),
                }
                for item in self.checks
            ],
        }
