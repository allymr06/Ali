import pytest

from app.core.models import ToolDefinition, ToolExecutionStatus
from app.tools.base import RegisteredTool
from app.tools.executor import ToolExecutor
from app.tools.registry import ToolRegistry


async def async_add(a: int, b: int) -> int:
    return a + b


def explode() -> None:
    raise RuntimeError("boom")


def create_executor() -> ToolExecutor:
    registry = ToolRegistry()

    registry.register(
        RegisteredTool(
            definition=ToolDefinition(
                name="add",
                description="Add two numbers.",
            ),
            handler=lambda a, b: a + b,
        )
    )

    registry.register(
        RegisteredTool(
            definition=ToolDefinition(
                name="async_add",
                description="Async addition.",
            ),
            handler=async_add,
        )
    )

    registry.register(
        RegisteredTool(
            definition=ToolDefinition(
                name="explode",
                description="Raises an exception.",
            ),
            handler=explode,
        )
    )

    return ToolExecutor(registry)


@pytest.mark.asyncio
async def test_executor_runs_sync_tool() -> None:
    executor = create_executor()

    result = await executor.execute("add", 2, 3)

    assert result.status is ToolExecutionStatus.SUCCESS
    assert result.tool_name == "add"
    assert result.data == 5
    assert result.started_at is not None
    assert result.finished_at is not None


@pytest.mark.asyncio
async def test_executor_runs_async_tool() -> None:
    executor = create_executor()

    result = await executor.execute("async_add", 4, 5)

    assert result.status is ToolExecutionStatus.SUCCESS
    assert result.data == 9


@pytest.mark.asyncio
async def test_executor_converts_tool_exception_to_failure() -> None:
    executor = create_executor()

    result = await executor.execute("explode")

    assert result.status is ToolExecutionStatus.FAILED
    assert result.tool_name == "explode"
    assert result.error == "boom"
    assert result.finished_at is not None


@pytest.mark.asyncio
async def test_executor_handles_unknown_tool() -> None:
    executor = create_executor()

    result = await executor.execute("does_not_exist")

    assert result.status is ToolExecutionStatus.FAILED
    assert result.tool_name == "does_not_exist"
    assert "not registered" in result.error


# ---------------------------------------------------------------------------
# execution observers (read-only; used by the Nova shell)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_executor_notifies_observers_on_the_async_path() -> None:
    executor = create_executor()
    events = []
    unsubscribe = executor.subscribe(events.append)

    result = await executor.execute("async_add", 2, 3)

    assert [event.phase for event in events] == ["started", "finished"]
    assert events[0].tool_name == "async_add" and events[0].result is None
    assert events[0].execution_id == events[1].execution_id
    assert events[1].result is result
    assert events[1].result.status is ToolExecutionStatus.SUCCESS
    unsubscribe()
    await executor.execute("async_add", 1, 1)
    assert len(events) == 2


def test_executor_notifies_observers_on_the_sync_path_and_survives_them() -> None:
    executor = create_executor()
    events = []

    def broken(event) -> None:
        raise RuntimeError("observer failure")

    executor.subscribe(broken)
    executor.subscribe(events.append)

    result = executor.execute("add", 2, 3)
    failed = executor.execute("does_not_exist")

    assert result.status is ToolExecutionStatus.SUCCESS
    assert failed.status is ToolExecutionStatus.FAILED
    assert [(event.phase, event.tool_name) for event in events] == [
        ("started", "add"),
        ("finished", "add"),
        ("started", "does_not_exist"),
        ("finished", "does_not_exist"),
    ]
    assert events[-1].result is failed
    with pytest.raises(TypeError):
        executor.subscribe(None)
