from __future__ import annotations

import importlib
import json
import pkgutil

import pytest

import app
from app.bootstrap import create_application
from app.config.settings import Settings
from app.core.models import Context, Request, RiskLevel
from app.diagnostics.models import HealthStatus
from app.ui.controller import DesktopController


def isolated_settings(tmp_path) -> Settings:
    return Settings(
        default_provider="mock",
        default_model="mock-model",
        windows_integrations_enabled=False,
        memory_database_path=str(tmp_path / "memory.sqlite3"),
        task_database_path=str(tmp_path / "tasks.sqlite3"),
        task_runtime_directory=str(tmp_path / "task-runtime"),
    )


@pytest.mark.asyncio
async def test_complete_offline_application_acceptance_path(tmp_path) -> None:
    application = create_application(isolated_settings(tmp_path))
    application.memory_manager.remember(
        "The user prefers concise reports.", source_reference="acceptance:user"
    )
    task = application.task_manager.create("Verify complete application state")
    context = Context(active_task_id=task.task_id)

    response = await application.engine.handle(
        Request("Please remember that I prefer concise reports."), context
    )
    snapshot = DesktopController(application).snapshot()
    health = await application.diagnostics.health_report()

    assert response.text.startswith("Mock yanıtı:")
    assert response.metadata["outcome"] == "completed"
    assert response.metadata["completion_verified"] is True
    assert snapshot.memory_count >= 1
    assert snapshot.task_count == 1
    assert snapshot.diagnostic_integrity_valid is True
    assert health.status is HealthStatus.HEALTHY
    assert application.diagnostics.ledger.verify_integrity() is True


def test_all_application_modules_import_without_starting_external_actions() -> None:
    failures: list[str] = []
    for module in pkgutil.walk_packages(app.__path__, prefix="app."):
        try:
            importlib.import_module(module.name)
        except Exception as exc:
            failures.append(f"{module.name}: {type(exc).__name__}: {exc}")
    assert failures == []


def test_every_tool_contract_is_unique_serializable_and_risk_consistent(tmp_path) -> None:
    application = create_application(isolated_settings(tmp_path))
    contracts = application.tool_executor.get_contract_objects(
        include_disabled=True
    )
    names = [contract.definition.name for contract in contracts]

    assert len(names) == len(set(names))
    assert all(contract.input_schema.get("additionalProperties") is False for contract in contracts)
    assert all(contract.definition.version for contract in contracts)
    for contract in contracts:
        json.dumps(contract.to_dict())
        if contract.definition.risk_level in {
            RiskLevel.MEDIUM,
            RiskLevel.HIGH,
            RiskLevel.CRITICAL,
        }:
            assert contract.definition.requires_confirmation is True


def test_no_runtime_source_uses_shell_true_or_dynamic_code_execution() -> None:
    project = __import__("pathlib").Path(__file__).resolve().parents[1]
    forbidden = ("shell=True", "os.system(", "eval(", "exec(")
    violations: list[str] = []
    for path in (project / "app").rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        compact = text.replace(" ", "")
        for pattern in forbidden:
            if pattern.replace(" ", "") in compact:
                violations.append(f"{path.relative_to(project)}: {pattern}")
    assert violations == []
