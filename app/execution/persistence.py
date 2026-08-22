from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Iterable
from uuid import UUID

from app.execution.state import (
    ExecutionSnapshot,
    ExecutionStateStore,
)


class FileExecutionStateStore(ExecutionStateStore):
    """Persist execution snapshots as an atomic JSON document."""

    def __init__(
        self,
        path: str | Path,
    ) -> None:
        super().__init__()
        self.path = Path(path)
        self.path.parent.mkdir(
            parents=True,
            exist_ok=True,
        )
        self._load()

    def save(
        self,
        snapshot: ExecutionSnapshot,
    ) -> ExecutionSnapshot:
        saved = super().save(snapshot)
        self._flush()
        return saved

    def delete(
        self,
        plan_id: UUID,
    ) -> None:
        super().delete(plan_id)
        self._flush()

    def clear(self) -> None:
        for snapshot in tuple(self.list()):
            super().delete(snapshot.plan_id)
        self._flush()

    def reload(self) -> None:
        self._snapshots.clear()
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            return

        raw = self.path.read_text(
            encoding="utf-8",
        ).strip()

        if not raw:
            return

        data = json.loads(raw)

        if not isinstance(data, list):
            raise ValueError(
                "Execution state file must contain a JSON list."
            )

        for item in data:
            if not isinstance(item, dict):
                raise ValueError(
                    "Execution state entries must be JSON objects."
                )

            snapshot = ExecutionSnapshot.from_dict(item)
            self._snapshots[snapshot.plan_id] = snapshot

    def _flush(self) -> None:
        payload = [
            snapshot.to_dict()
            for snapshot in self.list()
        ]

        self.path.parent.mkdir(
            parents=True,
            exist_ok=True,
        )

        fd, temporary_name = tempfile.mkstemp(
            prefix=f"{self.path.name}.",
            suffix=".tmp",
            dir=str(self.path.parent),
        )

        try:
            with os.fdopen(
                fd,
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
                temporary_name,
                self.path,
            )
        except Exception:
            try:
                os.unlink(temporary_name)
            except FileNotFoundError:
                pass
            raise
