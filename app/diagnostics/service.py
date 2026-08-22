from __future__ import annotations

from dataclasses import dataclass, field

from app.core.models import ToolDefinition, ToolExecutionStatus, ToolResult
from app.diagnostics.health import HealthCheck, HealthRegistry
from app.diagnostics.ledger import DiagnosticLedger
from app.diagnostics.metrics import MetricRegistry
from app.diagnostics.models import DiagnosticEvent, DiagnosticLevel, HealthReport
from app.tools.executor import ToolExecutor


@dataclass(slots=True)
class DiagnosticsService:
    ledger: DiagnosticLedger = field(default_factory=DiagnosticLedger)
    metrics: MetricRegistry = field(default_factory=MetricRegistry)
    health: HealthRegistry = field(default_factory=HealthRegistry)

    def record(
        self,
        component: str,
        name: str,
        message: str,
        *,
        level: DiagnosticLevel = DiagnosticLevel.INFO,
        attributes: dict[str, object] | None = None,
        trace_id: str | None = None,
    ) -> DiagnosticEvent:
        event = self.ledger.append(
            component,
            name,
            message,
            level=level,
            attributes=attributes,
            trace_id=trace_id,
        )
        self.metrics.increment(f"events.{level.value}")
        return event

    def register_health_check(
        self,
        name: str,
        component: str,
        callback,
        *,
        timeout_seconds: float = 2.0,
    ) -> None:
        self.health.register(
            HealthCheck(name, component, callback, timeout_seconds)
        )

    async def health_report(self) -> HealthReport:
        report = await self.health.run()
        self.metrics.gauge("health.checks", len(report.checks))
        self.record(
            "diagnostics",
            "health.completed",
            "Runtime health checks completed.",
            attributes={"status": report.status.value, "checks": len(report.checks)},
        )
        return report

    @staticmethod
    def _serialize_event(event: DiagnosticEvent) -> dict[str, object]:
        return {
            "sequence": event.sequence,
            "event_id": str(event.event_id),
            "observed_at": event.observed_at.isoformat(),
            "component": event.component,
            "name": event.name,
            "level": event.level.value,
            "message": event.message,
            "attributes": dict(event.attributes),
            "trace_id": event.trace_id,
            "previous_hash": event.previous_hash,
            "event_hash": event.event_hash,
        }

    def events(
        self,
        limit: int = 100,
        level: str | None = None,
        component: str | None = None,
    ) -> dict[str, object]:
        parsed_level = DiagnosticLevel(level) if level is not None else None
        values = self.ledger.list(
            limit=limit, level=parsed_level, component=component
        )
        return {
            "integrity_valid": self.ledger.verify_integrity(),
            "events": [self._serialize_event(event) for event in values],
        }

    def register_tools(self, executor: ToolExecutor) -> None:
        async def diagnostics_health() -> ToolResult:
            report = await self.health_report()
            return ToolResult(
                status=ToolExecutionStatus.SUCCESS,
                tool_name="diagnostics_health",
                message="Live component health observed.",
                data=report.to_dict(),
                verified=True,
            )

        def diagnostics_events(
            limit: int = 100,
            level: str | None = None,
            component: str | None = None,
        ) -> ToolResult:
            data = self.events(limit=limit, level=level, component=component)
            return ToolResult(
                status=(
                    ToolExecutionStatus.SUCCESS
                    if data["integrity_valid"]
                    else ToolExecutionStatus.FAILED
                ),
                tool_name="diagnostics_events",
                message="Sanitized diagnostic events observed.",
                data=data,
                verified=bool(data["integrity_valid"]),
            )

        def diagnostics_metrics() -> ToolResult:
            return ToolResult(
                status=ToolExecutionStatus.SUCCESS,
                tool_name="diagnostics_metrics",
                message="Bounded runtime metrics observed.",
                data=self.metrics.snapshot(),
                verified=True,
            )

        definitions = (
            (
                ToolDefinition(
                    name="diagnostics_health",
                    description="Run bounded live health checks for JARVIS components.",
                    timeout_seconds=10.0,
                    capabilities=frozenset({"diagnostics", "health", "observe"}),
                    tags=frozenset({"diagnostics", "read-only"}),
                    max_concurrency=1,
                    metadata={"verification_strategy": "live_health_checks"},
                ),
                diagnostics_health,
            ),
            (
                ToolDefinition(
                    name="diagnostics_events",
                    description="List sanitized tamper-evident runtime events.",
                    capabilities=frozenset({"diagnostics", "events", "observe"}),
                    tags=frozenset({"diagnostics", "read-only"}),
                    metadata={"verification_strategy": "ledger_hash_chain"},
                ),
                diagnostics_events,
            ),
            (
                ToolDefinition(
                    name="diagnostics_metrics",
                    description="Observe bounded low-cardinality runtime metrics.",
                    capabilities=frozenset({"diagnostics", "metrics", "observe"}),
                    tags=frozenset({"diagnostics", "read-only"}),
                    metadata={"verification_strategy": "registry_snapshot"},
                ),
                diagnostics_metrics,
            ),
        )
        for definition, handler in definitions:
            executor.register(definition, handler, source="core:diagnostics")

