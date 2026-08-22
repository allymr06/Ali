from __future__ import annotations

import asyncio

import pytest

from app.core.models import TaskStatus, ToolDefinition
from app.execution.coordinator import RecoveryCandidateStatus
from app.planning.models import PlanStatus, PlanStep
from app.planning.planner import Planner
from app.tasks.manager import TaskManager
from app.tasks.runtime import DurableTaskRuntime
from app.tasks.sqlite import SQLiteTaskStore
from app.tools.executor import ToolExecutor


def create_runtime(tmp_path):
    database = tmp_path / "tasks.sqlite3"
    manager = TaskManager(SQLiteTaskStore(database))
    tools = ToolExecutor()
    runtime = DurableTaskRuntime(
        tmp_path / "runtime",
        task_manager=manager,
        tool_executor=tools,
    )
    return database, manager, tools, runtime


def test_new_task_execution_persists_terminal_plan_and_task(tmp_path) -> None:
    database, manager, tools, runtime = create_runtime(tmp_path)
    tools.register(ToolDefinition(name="work", description="verified work"), lambda: "done")
    task = manager.create("Persistent task")
    plan = Planner().create_plan(
        task.goal,
        [PlanStep("work", metadata={"tool_name": "work", "parameters": {}})],
    )

    result = asyncio.run(runtime.execute_new(task.task_id, plan))

    assert result.status is TaskStatus.COMPLETED
    assert runtime.inspect(task.task_id).status is RecoveryCandidateStatus.COMPLETED
    manager.close()

    reopened = TaskManager(SQLiteTaskStore(database))
    assert reopened.get(task.task_id).status is TaskStatus.COMPLETED
    assert reopened.get(task.task_id).steps[0].result == "done"
    reopened.close()


def test_task_pauses_at_safe_step_boundary_and_resumes_after_restart(tmp_path) -> None:
    async def scenario() -> None:
        database, manager, tools, runtime = create_runtime(tmp_path)
        first_started = asyncio.Event()
        release_first = asyncio.Event()
        calls: list[str] = []

        async def first() -> str:
            calls.append("first")
            first_started.set()
            await release_first.wait()
            return "first-done"

        def second() -> str:
            calls.append("second")
            return "second-done"

        tools.register(ToolDefinition(name="first", description="first"), first)
        tools.register(ToolDefinition(name="second", description="second"), second)
        task = manager.create("Pause and resume")
        plan = Planner().create_plan(
            task.goal,
            [
                PlanStep("first", metadata={"tool_name": "first", "parameters": {}}),
                PlanStep(
                    "second",
                    dependencies=["first"],
                    metadata={"tool_name": "second", "parameters": {}},
                ),
            ],
        )

        running = asyncio.create_task(runtime.execute_new(task.task_id, plan))
        await first_started.wait()
        runtime.request_pause(task.task_id)
        release_first.set()
        paused = await running

        assert paused.status is TaskStatus.PAUSED
        assert paused.progress == 0.5
        assert calls == ["first"]
        manager.close()

        reopened_manager = TaskManager(SQLiteTaskStore(database))
        reopened_runtime = DurableTaskRuntime(
            tmp_path / "runtime",
            task_manager=reopened_manager,
            tool_executor=tools,
        )
        assert reopened_runtime.recoverable()[0].task_id == task.task_id

        completed = await reopened_runtime.resume(task.task_id)

        assert completed.status is TaskStatus.COMPLETED
        assert completed.progress == 1.0
        assert calls == ["first", "second"]
        reopened_manager.close()

    asyncio.run(scenario())


def test_runtime_rejects_reentry_while_task_is_running(tmp_path) -> None:
    async def scenario() -> None:
        _, manager, tools, runtime = create_runtime(tmp_path)
        started = asyncio.Event()
        release = asyncio.Event()

        async def wait_tool() -> str:
            started.set()
            await release.wait()
            return "done"

        tools.register(ToolDefinition(name="wait", description="wait"), wait_tool)
        task = manager.create("Single admission")
        plan = Planner().create_plan(
            task.goal,
            [PlanStep("wait", metadata={"tool_name": "wait", "parameters": {}})],
        )
        running = asyncio.create_task(runtime.execute_new(task.task_id, plan))
        await started.wait()

        with pytest.raises(ValueError, match="state running"):
            await runtime.resume(task.task_id)

        release.set()
        await running
        manager.close()

    asyncio.run(scenario())


def test_runtime_cancellation_is_persisted_as_terminal(tmp_path) -> None:
    async def scenario() -> None:
        _, manager, tools, runtime = create_runtime(tmp_path)
        started = asyncio.Event()
        release = asyncio.Event()

        async def first() -> str:
            started.set()
            await release.wait()
            return "done"

        tools.register(ToolDefinition(name="first", description="first"), first)
        tools.register(ToolDefinition(name="second", description="second"), lambda: "never")
        task = manager.create("Cancel safely")
        plan = Planner().create_plan(
            task.goal,
            [
                PlanStep("first", metadata={"tool_name": "first", "parameters": {}}),
                PlanStep("second", dependencies=["first"], metadata={"tool_name": "second", "parameters": {}}),
            ],
        )
        running = asyncio.create_task(runtime.execute_new(task.task_id, plan))
        await started.wait()
        runtime.request_cancel(task.task_id)
        release.set()

        result = await running

        assert result.status is TaskStatus.CANCELLED
        assert runtime.inspect(task.task_id).status is RecoveryCandidateStatus.CANCELLED
        manager.close()

    asyncio.run(scenario())


def test_runtime_refuses_resume_for_terminal_task(tmp_path) -> None:
    _, manager, _, runtime = create_runtime(tmp_path)
    task = manager.create("Terminal")
    manager.start(task.task_id)
    manager.complete(task.task_id)

    with pytest.raises(ValueError, match="state completed"):
        asyncio.run(runtime.resume(task.task_id))

    manager.close()
