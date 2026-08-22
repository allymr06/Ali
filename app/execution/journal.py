from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.execution.events import ExecutionEvent


class ExecutionJournal:
    """Append-only local execution journal."""

    def __init__(
        self,
        path: str | Path,
    ) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(
            parents=True,
            exist_ok=True,
        )

    async def append(
        self,
        event: ExecutionEvent,
    ) -> None:
        record: dict[str, Any] = {
            "event_id": str(event.event_id),
            "event_type": event.event_type.value,
            "plan_id": str(event.plan_id),
            "step_id": (
                str(event.step_id)
                if event.step_id is not None
                else None
            ),
            "step_name": event.step_name,
            "execution_id": (
                str(event.execution_id)
                if event.execution_id is not None
                else None
            ),
            "attempt": event.attempt,
            "data": event.data,
            "created_at": event.created_at.isoformat(),
        }

        with self.path.open(
            "a",
            encoding="utf-8",
        ) as handle:
            handle.write(
                json.dumps(
                    record,
                    ensure_ascii=False,
                    sort_keys=True,
                    default=str,
                )
                + "\n"
            )

    def read_all(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []

        records: list[dict[str, Any]] = []

        with self.path.open(
            "r",
            encoding="utf-8",
        ) as handle:
            for line in handle:
                line = line.strip()

                if not line:
                    continue

                records.append(
                    json.loads(line)
                )

        return records

    def clear(self) -> None:
        if self.path.exists():
            self.path.unlink()
