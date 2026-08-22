from __future__ import annotations

import asyncio
import inspect
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from time import perf_counter

from app.diagnostics.models import HealthReport, HealthResult, HealthStatus
from app.diagnostics.privacy import sanitize_attributes, sanitize_text

HealthCallback = Callable[[], object]


@dataclass(frozen=True, slots=True)
class HealthCheck:
    name: str
    component: str
    callback: HealthCallback
    timeout_seconds: float = 2.0

    def __post_init__(self) -> None:
        if not self.name.strip() or not self.component.strip():
            raise ValueError("Health check name and component cannot be empty.")
        if not callable(self.callback):
            raise TypeError("Health check callback must be callable.")
        if self.timeout_seconds <= 0:
            raise ValueError("Health check timeout must be positive.")


class HealthRegistry:
    def __init__(self, max_checks: int = 100) -> None:
        if max_checks < 1:
            raise ValueError("Health check capacity must be positive.")
        self._checks: dict[str, HealthCheck] = {}
        self._max_checks = max_checks

    def register(self, check: HealthCheck) -> None:
        key = check.name.strip().casefold()
        if key in self._checks:
            raise ValueError(f"Health check '{check.name}' is already registered.")
        if len(self._checks) >= self._max_checks:
            raise RuntimeError("Health check capacity reached.")
        self._checks[key] = check

    async def run(self) -> HealthReport:
        results = await asyncio.gather(
            *(self._run_one(check) for check in self._checks.values())
        )
        statuses = {item.status for item in results}
        if HealthStatus.UNHEALTHY in statuses:
            overall = HealthStatus.UNHEALTHY
        elif statuses & {HealthStatus.DEGRADED, HealthStatus.UNKNOWN}:
            overall = HealthStatus.DEGRADED
        else:
            overall = HealthStatus.HEALTHY
        return HealthReport(overall, tuple(results))

    async def _run_one(self, check: HealthCheck) -> HealthResult:
        started = perf_counter()
        try:
            if inspect.iscoroutinefunction(check.callback):
                value = await asyncio.wait_for(
                    check.callback(), timeout=check.timeout_seconds
                )
            else:
                value = await asyncio.wait_for(
                    asyncio.to_thread(check.callback), timeout=check.timeout_seconds
                )
            status, message, details = self._normalize(value)
        except TimeoutError:
            status, message, details = HealthStatus.UNHEALTHY, "Health check timed out.", {}
        except Exception:
            status, message, details = HealthStatus.UNHEALTHY, "Health check failed.", {}
        return HealthResult(
            name=check.name,
            component=check.component,
            status=status,
            observed_at=datetime.now(UTC),
            latency_ms=round((perf_counter() - started) * 1_000, 3),
            message=sanitize_text(message, limit=500),
            details=sanitize_attributes(details),
        )

    @staticmethod
    def _normalize(
        value: object,
    ) -> tuple[HealthStatus, str, dict[str, object]]:
        if isinstance(value, HealthStatus):
            return value, value.value, {}
        if isinstance(value, bool):
            status = HealthStatus.HEALTHY if value else HealthStatus.UNHEALTHY
            return status, status.value, {}
        if isinstance(value, tuple) and 2 <= len(value) <= 3:
            status = value[0]
            if not isinstance(status, HealthStatus):
                status = HealthStatus(str(status))
            message = str(value[1])
            details = value[2] if len(value) == 3 and isinstance(value[2], dict) else {}
            return status, message, details
        raise TypeError("Health callback returned an unsupported value.")

    def __len__(self) -> int:
        return len(self._checks)
