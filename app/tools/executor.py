from __future__ import annotations

import inspect
from datetime import datetime, timezone
from typing import Any

from app.core.models import ToolExecutionStatus, ToolResult
from app.tools.registry import ToolRegistry


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class ToolExecutor:
    """Execute registered tools and return structured results."""

    def __init__(self, registry: ToolRegistry) -> None:
        self._registry = registry

    async def execute(
        self,
        tool_name: str,
        *args: Any,
        **kwargs: Any,
    ) -> ToolResult:
        started_at = utc_now()

        try:
            tool = self._registry.get(tool_name)
        except KeyError as exc:
            return ToolResult(
                status=ToolExecutionStatus.FAILED,
                tool_name=tool_name,
                error=str(exc),
                started_at=started_at,
                finished_at=utc_now(),
            )

        try:
            result = tool.handler(*args, **kwargs)

            if inspect.isawaitable(result):
                result = await result

            return ToolResult(
                status=ToolExecutionStatus.SUCCESS,
                tool_name=tool.name,
                message="Tool executed successfully.",
                data=result,
                started_at=started_at,
                finished_at=utc_now(),
            )

        except Exception as exc:
            return ToolResult(
                status=ToolExecutionStatus.FAILED,
                tool_name=tool.name,
                error=str(exc),
                started_at=started_at,
                finished_at=utc_now(),
            )
