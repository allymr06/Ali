from __future__ import annotations

import pytest

from app.bootstrap import create_application
from app.config.settings import Settings
from app.core.models import Context, Request, ToolExecutionStatus
from app.diagnostics.ledger import DiagnosticLedger
from app.diagnostics.metrics import MetricRegistry
from app.diagnostics.models import DiagnosticLevel, HealthStatus
from app.diagnostics.service import DiagnosticsService
from app.security.permissions import PermissionEngine
from app.tools.executor import ToolExecutor


def settings() -> Settings:
    return Settings(
        windows_integrations_enabled=False,
        memory_database_path=None,
        task_database_path=None,
        task_runtime_directory=None,
    )


@pytest.mark.asyncio
async def test_diagnostics_service_runs_health_and_records_sanitized_event() -> None:
    service = DiagnosticsService(DiagnosticLedger(10), MetricRegistry(10))
    service.register_health_check(
        "core", "core", lambda: (HealthStatus.HEALTHY, "Ready")
    )

    report = await service.health_report()

    assert report.status is HealthStatus.HEALTHY
    assert service.ledger.verify_integrity() is True
    assert service.ledger.list()[0].name == "health.completed"
    assert service.metrics.snapshot()["gauges"]["health.checks"] == 1


@pytest.mark.asyncio
async def test_diagnostics_tools_are_read_only_bounded_and_verified() -> None:
    service = DiagnosticsService()
    service.register_health_check("core", "core", lambda: True)
    service.record("core", "ready", "Ready", level=DiagnosticLevel.INFO)
    executor = ToolExecutor(PermissionEngine())
    service.register_tools(executor)

    names = {"diagnostics_health", "diagnostics_events", "diagnostics_metrics"}
    contracts = executor.get_contract_objects(names=names)
    assert len(contracts) == 3
    assert all(item.definition.risk_level.value == "read_only" for item in contracts)
    assert all("diagnostics" in item.definition.tags for item in contracts)

    health = await executor.execute("diagnostics_health")
    events = await executor.execute(
        "diagnostics_events", parameters={"limit": 10, "component": "core"}
    )
    metrics = await executor.execute("diagnostics_metrics")

    assert health.status is ToolExecutionStatus.SUCCESS and health.verified
    assert events.status is ToolExecutionStatus.SUCCESS and events.verified
    assert events.data["integrity_valid"] is True
    assert metrics.status is ToolExecutionStatus.SUCCESS and metrics.verified


@pytest.mark.asyncio
async def test_bootstrap_wires_live_health_and_core_observability() -> None:
    application = create_application(settings())

    before = len(application.diagnostics.ledger)
    response = await application.engine.handle(Request("hello"), Context())
    report = await application.diagnostics.health_report()
    events = application.diagnostics.events(limit=20)

    assert response.text == "Mock yanıtı: hello"
    assert report.status is HealthStatus.HEALTHY
    assert len(report.checks) == 7
    assert len(application.diagnostics.ledger) >= before + 3
    assert events["integrity_valid"] is True
    assert {item["name"] for item in events["events"]} >= {
        "request.started",
        "request.completed",
        "health.completed",
    }
    metrics = application.diagnostics.metrics.snapshot()
    assert metrics["counters"]["core.requests"] == 1
    assert metrics["timers"]["core.request.duration"]["count"] == 1


def test_bootstrap_uses_configured_diagnostics_bounds() -> None:
    application = create_application(
        Settings(
            windows_integrations_enabled=False,
            memory_database_path=None,
            task_database_path=None,
            task_runtime_directory=None,
            diagnostics_event_capacity=3,
            diagnostics_metric_capacity=4,
        )
    )

    assert application.diagnostics.ledger.capacity == 3
    assert application.tool_executor.contains("diagnostics_health")


@pytest.mark.asyncio
async def test_core_records_sanitized_provider_failure(monkeypatch) -> None:
    application = create_application(settings())

    async def fail(*_args, **_kwargs):
        raise RuntimeError("Bearer highly-sensitive-upstream-token")

    monkeypatch.setattr(application.provider_gateway, "generate", fail)
    with pytest.raises(RuntimeError):
        await application.engine.handle(Request("fail safely"), Context())

    failure = application.diagnostics.ledger.list(
        component="core", limit=10
    )[0]
    assert failure.name == "request.failed"
    assert failure.level is DiagnosticLevel.ERROR
    assert "sensitive" not in failure.message
    assert failure.attributes == {"error_type": "RuntimeError"}
