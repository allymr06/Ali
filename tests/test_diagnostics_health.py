from __future__ import annotations

import asyncio

import pytest

from app.diagnostics.health import HealthCheck, HealthRegistry
from app.diagnostics.models import HealthStatus


@pytest.mark.asyncio
async def test_health_registry_combines_sync_async_and_degraded_checks() -> None:
    registry = HealthRegistry()
    registry.register(HealthCheck("sync", "core", lambda: True))

    async def degraded():
        return HealthStatus.DEGRADED, "Limited", {"token": "hidden"}

    registry.register(HealthCheck("async", "provider", degraded))

    report = await registry.run()

    assert report.status is HealthStatus.DEGRADED
    assert [item.status for item in report.checks] == [
        HealthStatus.HEALTHY,
        HealthStatus.DEGRADED,
    ]
    assert report.checks[1].details["token"] == "[REDACTED]"


@pytest.mark.asyncio
async def test_health_registry_contains_timeout_and_exception_details() -> None:
    registry = HealthRegistry()

    async def slow():
        await asyncio.sleep(0.1)
        return True

    def failed():
        raise RuntimeError("secret upstream details")

    registry.register(HealthCheck("slow", "one", slow, timeout_seconds=0.01))
    registry.register(HealthCheck("failed", "two", failed))

    report = await registry.run()

    assert report.status is HealthStatus.UNHEALTHY
    assert {item.message for item in report.checks} == {
        "Health check timed out.",
        "Health check failed.",
    }
    assert all(not item.details for item in report.checks)


def test_health_registry_rejects_duplicates_and_invalid_checks() -> None:
    registry = HealthRegistry(max_checks=1)
    check = HealthCheck("core", "core", lambda: True)
    registry.register(check)
    with pytest.raises(ValueError, match="already"):
        registry.register(check)
    with pytest.raises(ValueError, match="timeout"):
        HealthCheck("bad", "core", lambda: True, timeout_seconds=0)
