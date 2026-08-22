from __future__ import annotations

import json
import os
import tempfile
from dataclasses import asdict, is_dataclass
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any
from uuid import UUID

from app.planning.models import (
    Plan,
    PlanStatus,
    PlanStep,
    PlanStepStatus,
)


class PlanPersistenceError(ValueError):
    """Raised when a plan cannot be safely persisted."""


def _json_safe(value: Any) -> Any:
    if value is None:
        return None

    if isinstance(value, (str, int, float, bool)):
        return value

    if isinstance(value, UUID):
        return {
            "__type__": "uuid",
            "value": str(value),
        }

    if isinstance(value, datetime):
        return {
            "__type__": "datetime",
            "value": value.isoformat(),
        }

    if isinstance(value, Enum):
        return value.value

    if is_dataclass(value):
        return _json_safe(asdict(value))

    if isinstance(value, (list, tuple)):
        return [
            _json_safe(item)
            for item in value
        ]

    if isinstance(value, dict):
        return {
            str(key): _json_safe(item)
            for key, item in value.items()
        }

    raise PlanPersistenceError(
        "Plan metadata contains a value that cannot be persisted: "
        f"{type(value).__name__}"
    )


def _restore_json(value: Any) -> Any:
    if isinstance(value, dict):
        if (
            value.get("__type__") == "uuid"
            and "value" in value
        ):
            return UUID(str(value["value"]))

        if (
            value.get("__type__") == "datetime"
            and "value" in value
        ):
            return datetime.fromisoformat(str(value["value"]))

        return {
            key: _restore_json(item)
            for key, item in value.items()
        }

    if isinstance(value, list):
        return [
            _restore_json(item)
            for item in value
        ]

    return value


class PlanStore:
    """Atomic JSON persistence for executable plans."""

    def __init__(
        self,
        path: str | Path,
    ) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(
            parents=True,
            exist_ok=True,
        )

    def save(
        self,
        plan: Plan,
    ) -> Plan:
        payload = self._serialize(plan)

        temporary_fd, temporary_path = tempfile.mkstemp(
            prefix=f"{self.path.name}.",
            suffix=".tmp",
            dir=str(self.path.parent),
        )

        try:
            with os.fdopen(
                temporary_fd,
                "w",
                encoding="utf-8",
                newline="",
            ) as handle:
                json.dump(
                    payload,
                    handle,
                    ensure_ascii=False,
                    sort_keys=True,
                    indent=2,
                )
                handle.write("\n")

            os.replace(
                temporary_path,
                self.path,
            )

        except Exception:
            try:
                os.unlink(temporary_path)
            except FileNotFoundError:
                pass
            raise

        return plan

    def load(self) -> Plan:
        if not self.path.exists():
            raise FileNotFoundError(
                f"Plan file does not exist: {self.path}"
            )

        raw = self.path.read_text(
            encoding="utf-8",
        ).strip()

        if not raw:
            raise PlanPersistenceError(
                "Plan file is empty."
            )

        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise PlanPersistenceError(
                "Plan file contains invalid JSON."
            ) from exc

        if not isinstance(data, dict):
            raise PlanPersistenceError(
                "Persisted plan must be a JSON object."
            )

        return self._deserialize(data)

    @staticmethod
    def _serialize(
        plan: Plan,
    ) -> dict[str, Any]:
        return {
            "plan_id": str(plan.plan_id),
            "goal": plan.goal,
            "status": plan.status.value,
            "metadata": _json_safe(
                plan.metadata
            ),
            "steps": [
                {
                    "step_id": str(step.step_id),
                    "name": step.name,
                    "description": step.description,
                    "dependencies": list(
                        step.dependencies
                    ),
                    "status": step.status.value,
                    "metadata": _json_safe(
                        step.metadata
                    ),
                }
                for step in plan.steps
            ],
        }

    @staticmethod
    def _deserialize(
        data: dict[str, Any],
    ) -> Plan:
        required = (
            "plan_id",
            "goal",
            "status",
            "steps",
        )

        missing = [
            key
            for key in required
            if key not in data
        ]

        if missing:
            raise PlanPersistenceError(
                "Persisted plan is missing fields: "
                + ", ".join(missing)
            )

        steps_data = data["steps"]

        if not isinstance(steps_data, list):
            raise PlanPersistenceError(
                "Persisted plan steps must be a list."
            )

        steps: list[PlanStep] = []

        for item in steps_data:
            if not isinstance(item, dict):
                raise PlanPersistenceError(
                    "Persisted plan step must be an object."
                )

            try:
                step = PlanStep(
                    name=str(item["name"]),
                    description=str(
                        item.get(
                            "description",
                            "",
                        )
                    ),
                    dependencies=list(
                        item.get(
                            "dependencies",
                            [],
                        )
                    ),
                    status=PlanStepStatus(
                        item.get(
                            "status",
                            PlanStepStatus.PENDING.value,
                        )
                    ),
                    step_id=UUID(
                        str(item["step_id"])
                    ),
                    metadata=_restore_json(
                        dict(
                            item.get(
                                "metadata",
                                {},
                            )
                        )
                    ),
                )
            except (
                KeyError,
                TypeError,
                ValueError,
            ) as exc:
                raise PlanPersistenceError(
                    "Invalid persisted plan step."
                ) from exc

            steps.append(step)

        try:
            return Plan(
                goal=str(data["goal"]),
                steps=steps,
                status=PlanStatus(
                    data["status"]
                ),
                plan_id=UUID(
                    str(data["plan_id"])
                ),
                metadata=_restore_json(
                    dict(
                        data.get(
                            "metadata",
                            {},
                        )
                    )
                ),
            )
        except (
            TypeError,
            ValueError,
        ) as exc:
            raise PlanPersistenceError(
                "Invalid persisted plan."
            ) from exc
