from __future__ import annotations

import asyncio
import inspect
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from app.core.models import (
    ToolDefinition,
    ToolExecutionStatus,
    ToolResult,
)
from app.security.permissions import PermissionDecision, PermissionEngine


ToolHandler = Callable[..., Any]


@dataclass(frozen=True, slots=True)
class RegisteredTool:
    """A registered JARVIS tool and its executable handler."""

    definition: ToolDefinition
    handler: ToolHandler


class _AwaitableToolResult(ToolResult):
    """A ToolResult that can also be awaited."""

    def __await__(self):
        async def _resolve() -> ToolResult:
            return self

        return _resolve().__await__()


class ToolExecutor:
    """
    Central execution gateway for JARVIS tools.

    Supports both synchronous and asynchronous callers.

    Outside an active event loop:
        result = executor.execute(...)

    Inside an active event loop:
        result = await executor.execute(...)

    Both synchronous and asynchronous tool handlers are supported.
    """

    def __init__(
        self,
        permission_engine: PermissionEngine | Any | None = None,
    ) -> None:
        self._tools: dict[str, RegisteredTool] = {}

        if (
            permission_engine is not None
            and hasattr(permission_engine, "list_tools")
            and not hasattr(permission_engine, "evaluate")
        ):
            registry = permission_engine
            self._permission_engine = PermissionEngine()

            for tool in registry.list_tools():
                self._tools[tool.name.strip()] = RegisteredTool(
                    definition=tool.definition,
                    handler=tool.handler,
                )
        else:
            self._permission_engine = (
                permission_engine
                if permission_engine is not None
                else PermissionEngine()
            )

    def register(
        self,
        definition: ToolDefinition,
        handler: ToolHandler,
    ) -> None:
        """Register a tool with its executable handler."""
        name = definition.name.strip()

        if not name:
            raise ValueError("Tool name cannot be empty.")

        if not callable(handler):
            raise TypeError("Tool handler must be callable.")

        if name in self._tools:
            raise ValueError(
                f"Tool '{name}' is already registered."
            )

        self._tools[name] = RegisteredTool(
            definition=definition,
            handler=handler,
        )

    def unregister(self, name: str) -> RegisteredTool:
        """Remove and return a registered tool."""
        normalized_name = name.strip()

        try:
            return self._tools.pop(normalized_name)
        except KeyError as exc:
            raise KeyError(
                f"Tool '{normalized_name}' is not registered."
            ) from exc

    def get(self, name: str) -> RegisteredTool:
        """Return a registered tool."""
        normalized_name = name.strip()

        try:
            return self._tools[normalized_name]
        except KeyError as exc:
            raise KeyError(
                f"Tool '{normalized_name}' is not registered."
            ) from exc

    def contains(self, name: str) -> bool:
        """Return whether a tool is registered."""
        return name.strip() in self._tools

    def list_names(self) -> tuple[str, ...]:
        """Return registered tool names."""
        return tuple(self._tools)

    def _now(self) -> datetime:
        return datetime.now(timezone.utc)

    def _evaluate_permission(
        self,
        definition: ToolDefinition,
        *,
        operation: str | None,
        parameters: dict[str, Any],
        confirmation_granted: bool,
    ) -> ToolResult | None:
        permission = self._permission_engine.evaluate(
            definition,
            operation=operation,
            parameters=parameters,
        )

        if permission.decision == PermissionDecision.DENY:
            return ToolResult(
                status=ToolExecutionStatus.BLOCKED,
                tool_name=definition.name,
                message=permission.reason,
                error="Permission denied.",
                verified=False,
            )

        if (
            permission.decision == PermissionDecision.CONFIRM
            and not confirmation_granted
        ):
            return ToolResult(
                status=ToolExecutionStatus.BLOCKED,
                tool_name=definition.name,
                message=permission.reason,
                error="User confirmation required.",
                verified=False,
            )

        return None

    def execute(
        self,
        name: str,
        *args: Any,
        operation: str | None = None,
        parameters: dict[str, Any] | None = None,
        confirmation_granted: bool = False,
    ) -> ToolResult | Any:
        """
        Execute a tool.

        If called from normal synchronous code, execution happens
        immediately and a ToolResult is returned.

        If called while an asyncio event loop is running, an async
        execution coroutine is returned and can be awaited.
        """
        try:
            asyncio.get_running_loop()
            in_async_context = True
        except RuntimeError:
            in_async_context = False

        if in_async_context:
            return self._execute_async(
                name,
                *args,
                operation=operation,
                parameters=parameters,
                confirmation_granted=confirmation_granted,
            )

        return self._execute_sync(
            name,
            *args,
            operation=operation,
            parameters=parameters,
            confirmation_granted=confirmation_granted,
        )

    def _execute_sync(
        self,
        name: str,
        *args: Any,
        operation: str | None,
        parameters: dict[str, Any] | None,
        confirmation_granted: bool,
    ) -> ToolResult:
        normalized_name = name.strip()

        registered = self.get(normalized_name)

        definition = registered.definition
        execution_parameters = parameters or {}
        started_at = self._now()

        blocked = self._evaluate_permission(
            definition,
            operation=operation,
            parameters=execution_parameters,
            confirmation_granted=confirmation_granted,
        )

        if blocked is not None:
            blocked.started_at = started_at
            blocked.finished_at = self._now()
            return blocked

        try:
            value = registered.handler(
                *args,
                **execution_parameters,
            )

            if inspect.isawaitable(value):
                value.close()
                return ToolResult(
                    status=ToolExecutionStatus.FAILED,
                    tool_name=definition.name,
                    message="Tool execution requires asynchronous execution.",
                    error=(
                        "An asynchronous tool handler cannot be executed "
                        "from synchronous code. Use 'await executor.execute(...)'."
                    ),
                    started_at=started_at,
                    finished_at=self._now(),
                    verified=False,
                )

            return ToolResult(
                status=ToolExecutionStatus.SUCCESS,
                tool_name=definition.name,
                message="Tool executed successfully.",
                data=value,
                started_at=started_at,
                finished_at=self._now(),
                verified=True,
            )

        except Exception as exc:
            return ToolResult(
                status=ToolExecutionStatus.FAILED,
                tool_name=definition.name,
                message="Tool execution failed.",
                error=str(exc),
                started_at=started_at,
                finished_at=self._now(),
                verified=False,
            )

    async def _execute_async(
        self,
        name: str,
        *args: Any,
        operation: str | None,
        parameters: dict[str, Any] | None,
        confirmation_granted: bool,
    ) -> ToolResult:
        normalized_name = name.strip()

        try:
            registered = self.get(normalized_name)
        except KeyError as exc:
            return ToolResult(
                status=ToolExecutionStatus.FAILED,
                tool_name=normalized_name,
                message="Tool is not registered.",
                error=str(exc),
                started_at=None,
                finished_at=self._now(),
                verified=False,
            )

        definition = registered.definition
        execution_parameters = parameters or {}
        started_at = self._now()

        blocked = self._evaluate_permission(
            definition,
            operation=operation,
            parameters=execution_parameters,
            confirmation_granted=confirmation_granted,
        )

        if blocked is not None:
            blocked.started_at = started_at
            blocked.finished_at = self._now()
            return blocked

        try:
            value = registered.handler(
                *args,
                **execution_parameters,
            )

            if inspect.isawaitable(value):
                value = await value

            return ToolResult(
                status=ToolExecutionStatus.SUCCESS,
                tool_name=definition.name,
                message="Tool executed successfully.",
                data=value,
                started_at=started_at,
                finished_at=self._now(),
                verified=True,
            )

        except Exception as exc:
            return ToolResult(
                status=ToolExecutionStatus.FAILED,
                tool_name=definition.name,
                message="Tool execution failed.",
                error=str(exc),
                started_at=started_at,
                finished_at=self._now(),
                verified=False,
            )

    def __len__(self) -> int:
        return len(self._tools)
