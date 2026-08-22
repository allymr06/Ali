from __future__ import annotations

import asyncio
from dataclasses import dataclass
from uuid import UUID

from app.core.models import (
    RiskLevel,
    Task,
    TaskStatus,
    ToolDefinition,
    ToolExecutionStatus,
    ToolResult,
)
from app.tasks.manager import TaskManager
from app.tasks.runtime import DurableTaskRuntime
from app.tools.executor import ToolExecutor


@dataclass(slots=True)
class TaskControlService:
    """Provider-visible observation and control for durable tasks."""

    manager: TaskManager
    runtime: DurableTaskRuntime | None = None

    @staticmethod
    def _serialize(task: Task) -> dict[str, object]:
        return {
            "task_id": str(task.task_id),
            "goal": task.goal,
            "status": task.status.value,
            "progress": task.progress,
            "current_step": task.current_step,
            "error": task.error,
            "created_at": task.created_at.isoformat(),
            "updated_at": task.updated_at.isoformat(),
            "plan_id": task.metadata.get("plan_id"),
            "parent_task_id": task.metadata.get("parent_task_id"),
            "recovery_required": bool(task.metadata.get("recovery_required", False)),
            "steps": [
                {
                    "step_id": str(step.step_id),
                    "name": step.name,
                    "status": step.status.value,
                    "error": step.error,
                }
                for step in task.steps
            ],
        }

    def list(self, status: str | None = None, limit: int = 100) -> list[dict[str, object]]:
        if limit < 1 or limit > 500:
            raise ValueError("Task list limit must be between 1 and 500.")
        selected_status = TaskStatus(status) if status is not None else None
        tasks = self.manager.list()
        if selected_status is not None:
            tasks = [task for task in tasks if task.status is selected_status]
        tasks.sort(key=lambda task: task.updated_at, reverse=True)
        return [self._serialize(task) for task in tasks[:limit]]

    def get(self, task_id: str) -> dict[str, object]:
        return self._serialize(self.manager.get(UUID(task_id)))

    async def resume(self, task_id: str) -> ToolResult:
        if self.runtime is None:
            raise RuntimeError("Durable task runtime is not configured.")
        task = await self.runtime.resume(UUID(task_id))
        verified = task.status in {
            TaskStatus.COMPLETED,
            TaskStatus.PAUSED,
            TaskStatus.CANCELLED,
            TaskStatus.FAILED,
        }
        return ToolResult(
            status=(ToolExecutionStatus.SUCCESS if verified else ToolExecutionStatus.FAILED),
            tool_name="resume_task",
            message=f"Task resume reached {task.status.value}.",
            data=self._serialize(task),
            verified=verified,
        )

    async def pause(self, task_id: str) -> ToolResult:
        if self.runtime is None:
            raise RuntimeError("Durable task runtime is not configured.")
        identifier = UUID(task_id)
        self.runtime.request_pause(identifier)
        task = await self._wait_for_terminal_boundary(identifier, TaskStatus.PAUSED)
        verified = task.status is TaskStatus.PAUSED
        return ToolResult(
            status=(ToolExecutionStatus.SUCCESS if verified else ToolExecutionStatus.PARTIAL),
            tool_name="pause_task",
            message="Task paused at a safe boundary." if verified else "Pause requested.",
            data=self._serialize(task),
            verified=verified,
            side_effects_may_continue=not verified,
        )

    async def cancel(self, task_id: str) -> ToolResult:
        if self.runtime is None:
            raise RuntimeError("Durable task runtime is not configured.")
        identifier = UUID(task_id)
        self.runtime.request_cancel(identifier)
        task = await self._wait_for_terminal_boundary(identifier, TaskStatus.CANCELLED)
        verified = task.status is TaskStatus.CANCELLED
        return ToolResult(
            status=(ToolExecutionStatus.SUCCESS if verified else ToolExecutionStatus.PARTIAL),
            tool_name="cancel_task",
            message="Task cancelled." if verified else "Cancellation requested.",
            data=self._serialize(task),
            verified=verified,
            side_effects_may_continue=not verified,
        )

    async def _wait_for_terminal_boundary(
        self,
        task_id: UUID,
        expected: TaskStatus,
        timeout_seconds: float = 5.0,
    ) -> Task:
        deadline = asyncio.get_running_loop().time() + timeout_seconds
        while asyncio.get_running_loop().time() < deadline:
            task = self.manager.get(task_id)
            if task.status is expected or not self.runtime.is_active(task_id):
                return task
            await asyncio.sleep(0.01)
        return self.manager.get(task_id)

    def register_tools(self, executor: ToolExecutor) -> None:
        def list_tasks(status: str | None = None, limit: int = 100) -> ToolResult:
            return ToolResult(
                status=ToolExecutionStatus.SUCCESS,
                tool_name="list_tasks",
                message="Durable tasks observed.",
                data=self.list(status=status, limit=limit),
                verified=True,
            )

        def get_task(task_id: str) -> ToolResult:
            return ToolResult(
                status=ToolExecutionStatus.SUCCESS,
                tool_name="get_task",
                message="Durable task observed.",
                data=self.get(task_id),
                verified=True,
            )

        executor.register(
            ToolDefinition(
                name="list_tasks",
                description="List durable JARVIS tasks and their progress.",
                capabilities=frozenset({"tasks", "observe"}),
                tags=frozenset({"tasks", "read-only"}),
            ),
            list_tasks,
            source="core:tasks",
        )
        executor.register(
            ToolDefinition(
                name="get_task",
                description="Inspect one durable JARVIS task and its steps.",
                capabilities=frozenset({"tasks", "observe"}),
                tags=frozenset({"tasks", "read-only"}),
            ),
            get_task,
            source="core:tasks",
        )
        if self.runtime is None:
            return
        for name, description, handler, capability in (
            ("pause_task", "Pause an active task at a safe step boundary.", self.pause, "pause"),
            ("resume_task", "Resume a recoverable durable task.", self.resume, "resume"),
            ("cancel_task", "Cancel an active durable task.", self.cancel, "cancel"),
        ):
            executor.register(
                ToolDefinition(
                    name=name,
                    description=description,
                    risk_level=RiskLevel.MEDIUM,
                    requires_confirmation=True,
                    capabilities=frozenset({"tasks", capability}),
                    tags=frozenset({"tasks", "action"}),
                    metadata={"verification_strategy": "durable_task_state"},
                ),
                handler,
                source="core:tasks",
            )
