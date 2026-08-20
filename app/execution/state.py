from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any
from uuid import UUID, uuid4

from app.core.time import utc_now


class ExecutionSnapshotStatus(str, Enum):
    RUNNING = "running"
    PAUSED = "paused"
    WAITING_FOR_APPROVAL = "waiting_for_approval"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass(slots=True)
class ExecutionSnapshot:
    plan_id: UUID
    status: ExecutionSnapshotStatus
    goal: str
    current_step_id: UUID | None = None
    current_step_name: str | None = None
    completed_step_ids: list[UUID] = field(default_factory=list)
    failed_step_ids: list[UUID] = field(default_factory=list)
    attempts: dict[str, int] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)
    snapshot_id: UUID = field(default_factory=uuid4)
    created_at: datetime = field(default_factory=utc_now)
    updated_at: datetime = field(default_factory=utc_now)

    def touch(self) -> None:
        self.updated_at = utc_now()

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)

        for key, value in list(data.items()):
            if isinstance(value, UUID):
                data[key] = str(value)
            elif isinstance(value, datetime):
                data[key] = value.isoformat()

        data["status"] = self.status.value
        data["completed_step_ids"] = [
            str(value)
            for value in self.completed_step_ids
        ]
        data["failed_step_ids"] = [
            str(value)
            for value in self.failed_step_ids
        ]

        return data

    @classmethod
    def from_dict(
        cls,
        data: dict[str, Any],
    ) -> "ExecutionSnapshot":
        return cls(
            plan_id=UUID(str(data["plan_id"])),
            status=ExecutionSnapshotStatus(
                data["status"]
            ),
            goal=str(data["goal"]),
            current_step_id=(
                UUID(str(data["current_step_id"]))
                if data.get("current_step_id")
                else None
            ),
            current_step_name=data.get(
                "current_step_name"
            ),
            completed_step_ids=[
                UUID(str(value))
                for value in data.get(
                    "completed_step_ids",
                    [],
                )
            ],
            failed_step_ids=[
                UUID(str(value))
                for value in data.get(
                    "failed_step_ids",
                    [],
                )
            ],
            attempts={
                str(key): int(value)
                for key, value in data.get(
                    "attempts",
                    {},
                ).items()
            },
            metadata=dict(
                data.get(
                    "metadata",
                    {},
                )
            ),
            snapshot_id=UUID(
                str(
                    data.get(
                        "snapshot_id",
                        uuid4(),
                    )
                )
            ),
            created_at=datetime.fromisoformat(
                str(data["created_at"])
            ),
            updated_at=datetime.fromisoformat(
                str(data["updated_at"])
            ),
        )


class ExecutionStateStore:
    """In-memory execution snapshot repository."""

    def __init__(self) -> None:
        self._snapshots: dict[UUID, ExecutionSnapshot] = {}

    def save(
        self,
        snapshot: ExecutionSnapshot,
    ) -> ExecutionSnapshot:
        snapshot.touch()
        self._snapshots[snapshot.plan_id] = snapshot
        return snapshot

    def get(
        self,
        plan_id: UUID,
    ) -> ExecutionSnapshot:
        try:
            return self._snapshots[plan_id]
        except KeyError as exc:
            raise KeyError(
                f"Unknown execution snapshot: {plan_id}"
            ) from exc

    def delete(
        self,
        plan_id: UUID,
    ) -> None:
        self._snapshots.pop(
            plan_id,
            None,
        )

    def list(self) -> list[ExecutionSnapshot]:
        return list(
            self._snapshots.values()
        )
