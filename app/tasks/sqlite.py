from __future__ import annotations

import json
import shutil
import sqlite3
from dataclasses import asdict, is_dataclass
from datetime import datetime
from enum import Enum
from pathlib import Path
from threading import RLock
from typing import Any
from uuid import UUID

from app.core.models import Task, TaskStatus, TaskStep, TaskStepStatus
from app.tasks.base import TaskStore


class TaskPersistenceError(ValueError):
    """Raised when task state cannot be safely persisted."""


def _json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, UUID):
        return {"__type__": "uuid", "value": str(value)}
    if isinstance(value, datetime):
        return {"__type__": "datetime", "value": value.isoformat()}
    if isinstance(value, Enum):
        return value.value
    if is_dataclass(value):
        return _json_safe(asdict(value))
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if isinstance(value, dict):
        if not all(isinstance(key, str) for key in value):
            raise TaskPersistenceError("Task metadata keys must be strings.")
        return {key: _json_safe(item) for key, item in value.items()}
    raise TaskPersistenceError(
        "Task state contains a value that cannot be persisted: "
        f"{type(value).__name__}."
    )


def _restore_json(value: Any) -> Any:
    if isinstance(value, list):
        return [_restore_json(item) for item in value]
    if isinstance(value, dict):
        if value.get("__type__") == "uuid":
            return UUID(str(value["value"]))
        if value.get("__type__") == "datetime":
            return datetime.fromisoformat(str(value["value"]))
        return {key: _restore_json(item) for key, item in value.items()}
    return value


class SQLiteTaskStore(TaskStore):
    """Transactional, migration-aware task persistence."""

    SCHEMA_VERSION = 1

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path).expanduser().resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = RLock()
        try:
            self._connection = sqlite3.connect(
                self.path, timeout=5.0, check_same_thread=False
            )
            self._connection.row_factory = sqlite3.Row
            self._connection.execute("PRAGMA journal_mode = WAL")
            self._connection.execute("PRAGMA synchronous = FULL")
            self._connection.execute("PRAGMA busy_timeout = 5000")
            self._migrate()
            result = self._connection.execute("PRAGMA quick_check").fetchone()[0]
            if result != "ok":
                raise TaskPersistenceError(
                    f"Task database integrity check failed: {result}"
                )
        except sqlite3.DatabaseError as exc:
            connection = getattr(self, "_connection", None)
            if connection is not None:
                connection.close()
            raise TaskPersistenceError(
                f"Task database is unreadable: {self.path}"
            ) from exc

    def _migrate(self) -> None:
        version = int(self._connection.execute("PRAGMA user_version").fetchone()[0])
        if version > self.SCHEMA_VERSION:
            raise TaskPersistenceError(
                f"Task schema {version} is newer than supported version "
                f"{self.SCHEMA_VERSION}."
            )
        if version == 0:
            with self._connection:
                self._connection.executescript(
                    """
                    CREATE TABLE IF NOT EXISTS tasks (
                        task_id TEXT PRIMARY KEY,
                        status TEXT NOT NULL,
                        updated_at TEXT NOT NULL,
                        payload_json TEXT NOT NULL
                    );
                    CREATE INDEX IF NOT EXISTS idx_tasks_status_updated
                        ON tasks(status, updated_at DESC);
                    PRAGMA user_version = 1;
                    """
                )

    def save(self, task: Task) -> Task:
        payload = json.dumps(
            self._serialize(task),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        with self._lock, self._connection:
            self._connection.execute(
                """
                INSERT INTO tasks(task_id, status, updated_at, payload_json)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(task_id) DO UPDATE SET
                    status=excluded.status,
                    updated_at=excluded.updated_at,
                    payload_json=excluded.payload_json
                """,
                (str(task.task_id), task.status.value, task.updated_at.isoformat(), payload),
            )
        return task

    def get(self, task_id: UUID) -> Task:
        with self._lock:
            row = self._connection.execute(
                "SELECT payload_json FROM tasks WHERE task_id = ?", (str(task_id),)
            ).fetchone()
        if row is None:
            raise KeyError(f"Unknown task: {task_id}")
        return self._deserialize_payload(row["payload_json"])

    def list(self) -> tuple[Task, ...]:
        with self._lock:
            rows = self._connection.execute(
                "SELECT payload_json FROM tasks ORDER BY updated_at, task_id"
            ).fetchall()
        return tuple(self._deserialize_payload(row["payload_json"]) for row in rows)

    def delete(self, task_id: UUID) -> Task:
        task = self.get(task_id)
        with self._lock, self._connection:
            self._connection.execute("DELETE FROM tasks WHERE task_id = ?", (str(task_id),))
        return task

    def close(self) -> None:
        with self._lock:
            self._connection.close()

    def backup_to(self, destination: str | Path) -> Path:
        backup_path = Path(destination).expanduser().resolve()
        if backup_path == self.path:
            raise ValueError("Backup destination must differ from the live database.")
        backup_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = backup_path.with_suffix(backup_path.suffix + ".tmp")
        if temporary.exists():
            temporary.unlink()
        target = sqlite3.connect(temporary)
        try:
            with self._lock:
                self._connection.backup(target)
            result = target.execute("PRAGMA quick_check").fetchone()[0]
            if result != "ok":
                raise TaskPersistenceError(
                    f"Task backup integrity check failed: {result}"
                )
        finally:
            target.close()
        temporary.replace(backup_path)
        return backup_path

    @classmethod
    def restore_from_backup(
        cls,
        backup: str | Path,
        destination: str | Path,
    ) -> SQLiteTaskStore:
        backup_path = Path(backup).expanduser().resolve()
        destination_path = Path(destination).expanduser().resolve()
        if not backup_path.is_file():
            raise FileNotFoundError(backup_path)
        destination_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination_path.with_suffix(destination_path.suffix + ".restore")
        shutil.copy2(backup_path, temporary)
        probe = sqlite3.connect(temporary)
        try:
            result = probe.execute("PRAGMA quick_check").fetchone()[0]
            if result != "ok":
                raise TaskPersistenceError(
                    f"Task backup integrity check failed: {result}"
                )
        finally:
            probe.close()
        temporary.replace(destination_path)
        return cls(destination_path)

    @staticmethod
    def _serialize(task: Task) -> dict[str, Any]:
        return {
            "task_id": str(task.task_id),
            "goal": task.goal,
            "status": task.status.value,
            "created_at": task.created_at.isoformat(),
            "updated_at": task.updated_at.isoformat(),
            "current_step": task.current_step,
            "progress": task.progress,
            "result": _json_safe(task.result),
            "error": task.error,
            "metadata": _json_safe(task.metadata),
            "steps": [
                {
                    "step_id": str(step.step_id),
                    "name": step.name,
                    "status": step.status.value,
                    "created_at": step.created_at.isoformat(),
                    "updated_at": step.updated_at.isoformat(),
                    "result": _json_safe(step.result),
                    "error": step.error,
                    "metadata": _json_safe(step.metadata),
                }
                for step in task.steps
            ],
        }

    @classmethod
    def _deserialize_payload(cls, raw: str) -> Task:
        try:
            data = json.loads(raw)
            if not isinstance(data, dict):
                raise TypeError("Task payload must be an object.")
            steps = [
                TaskStep(
                    name=str(item["name"]),
                    step_id=UUID(str(item["step_id"])),
                    status=TaskStepStatus(item["status"]),
                    created_at=datetime.fromisoformat(str(item["created_at"])),
                    updated_at=datetime.fromisoformat(str(item["updated_at"])),
                    result=_restore_json(item.get("result")),
                    error=item.get("error"),
                    metadata=_restore_json(dict(item.get("metadata", {}))),
                )
                for item in data.get("steps", [])
            ]
            return Task(
                goal=str(data["goal"]),
                task_id=UUID(str(data["task_id"])),
                status=TaskStatus(data["status"]),
                created_at=datetime.fromisoformat(str(data["created_at"])),
                updated_at=datetime.fromisoformat(str(data["updated_at"])),
                current_step=data.get("current_step"),
                progress=float(data.get("progress", 0.0)),
                result=_restore_json(data.get("result")),
                error=data.get("error"),
                steps=steps,
                metadata=_restore_json(dict(data.get("metadata", {}))),
            )
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise TaskPersistenceError("Invalid persisted task record.") from exc
