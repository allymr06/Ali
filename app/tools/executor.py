from __future__ import annotations

import asyncio
import inspect
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, get_type_hints

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

    def _validate_arguments(
        self,
        handler: ToolHandler,
        args: tuple[Any, ...],
        parameters: dict[str, Any],
    ) -> str | None:
        """Validate tool input arguments without resolving the return type."""
        try:
            signature = inspect.signature(handler)

            bound = signature.bind(*args, **parameters)
            bound.apply_defaults()

            try:
                resolved_hints = get_type_hints(
                    handler,
                    globalns=getattr(handler, "__globals__", None),
                    localns=None,
                )
            except (NameError, TypeError, ValueError):
                resolved_hints = {}

            for name, value in bound.arguments.items():
                parameter = signature.parameters[name]

                annotation = resolved_hints.get(
                    name,
                    parameter.annotation,
                )

                if annotation is inspect.Parameter.empty:
                    continue

                if isinstance(annotation, str):
                    continue

                if not self._matches_type(value, annotation):
                    return (
                        f"Argument '{name}' has invalid type. "
                        f"Expected {annotation!r}, "
                        f"got {type(value).__name__}."
                    )

        except TypeError as exc:
            return str(exc)

        return None
    def _matches_type(
        self,
        value: Any,
        annotation: Any,
    ) -> bool:
        """Return whether a value matches a runtime type annotation."""
        from types import UnionType
        from typing import Annotated, Literal, Union, get_args, get_origin

        if annotation is Any:
            return True

        if annotation is None:
            return value is None

        if value is None:
            origin = get_origin(annotation)

            if origin in (Union, UnionType):
                return type(None) in get_args(annotation)

            return annotation is type(None)

        origin = get_origin(annotation)
        args = get_args(annotation)

        if origin in (Union, UnionType):
            return any(
                self._matches_type(
                    value,
                    option,
                )
                for option in args
            )

        if origin is Annotated:
            if not args:
                return True

            return self._matches_type(
                value,
                args[0],
            )

        if origin is Literal:
            return any(
                type(value) is type(literal)
                and value == literal
                for literal in args
            )

        if origin is list:
            if not isinstance(value, list):
                return False

            if not args:
                return True

            return all(
                self._matches_type(
                    item,
                    args[0],
                )
                for item in value
            )

        if origin is dict:
            if not isinstance(value, dict):
                return False

            if len(args) < 2:
                return True

            key_type, value_type = args

            return all(
                self._matches_type(key, key_type)
                and self._matches_type(item, value_type)
                for key, item in value.items()
            )

        if origin is tuple:
            if not isinstance(value, tuple):
                return False

            if not args:
                return True

            if len(args) == 2 and args[1] is Ellipsis:
                return all(
                    self._matches_type(
                        item,
                        args[0],
                    )
                    for item in value
                )

            if len(value) != len(args):
                return False

            return all(
                self._matches_type(
                    item,
                    expected,
                )
                for item, expected in zip(
                    value,
                    args,
                    strict=True,
                )
            )

        if origin is set:
            if not isinstance(value, set):
                return False

            if not args:
                return True

            return all(
                self._matches_type(
                    item,
                    args[0],
                )
                for item in value
            )

        if origin is frozenset:
            if not isinstance(value, frozenset):
                return False

            if not args:
                return True

            return all(
                self._matches_type(
                    item,
                    args[0],
                )
                for item in value
            )

        if isinstance(annotation, type):
            return isinstance(value, annotation)

        return True
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
        
        argument_error = self._validate_arguments(
            registered.handler,
            args,
            execution_parameters,
        )

        if argument_error is not None:
            return ToolResult(
                status=ToolExecutionStatus.FAILED,
                tool_name=definition.name,
                message="Invalid tool arguments.",
                error=argument_error,
                started_at=started_at,
                finished_at=self._now(),
                verified=False,
            )

        try:
            with ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(
                    registered.handler,
                    *args,
                    **execution_parameters,
                )

                try:
                    value = future.result(
                        timeout=definition.timeout_seconds,
                    )
                except FuturesTimeoutError:
                    future.cancel()

                    return ToolResult(
                        status=ToolExecutionStatus.TIMEOUT,
                        tool_name=definition.name,
                        message="Tool execution timed out.",
                        error=(
                            f"Tool execution exceeded the "
                            f"{definition.timeout_seconds} second timeout."
                        ),
                        started_at=started_at,
                        finished_at=self._now(),
                        verified=False,
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

            if isinstance(value, ToolResult):
                value.tool_name = definition.name
                value.verified = (
                    value.status is ToolExecutionStatus.SUCCESS
                )
                return value

            if isinstance(value, ToolResult):
                value.tool_name = definition.name
                value.verified = (
                    value.status is ToolExecutionStatus.SUCCESS
                )
                return value

            if isinstance(value, ToolResult):
                value.tool_name = definition.name
                value.verified = (
                    value.status is ToolExecutionStatus.SUCCESS
                )
                return value

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

        argument_error = self._validate_arguments(
            registered.handler,
            args,
            execution_parameters,
        )

        if argument_error is not None:
            return ToolResult(
                status=ToolExecutionStatus.FAILED,
                tool_name=definition.name,
                message="Invalid tool arguments.",
                error=argument_error,
                started_at=started_at,
                finished_at=self._now(),
                verified=False,
            )

        try:
            value = registered.handler(
                *args,
                **execution_parameters,
            )

            if inspect.isawaitable(value):
                try:
                    value = await asyncio.wait_for(
                        value,
                        timeout=definition.timeout_seconds,
                    )
                except asyncio.TimeoutError:
                    return ToolResult(
                        status=ToolExecutionStatus.TIMEOUT,
                        tool_name=definition.name,
                        message="Tool execution timed out.",
                        error=(
                            f"Tool execution exceeded the "
                            f"{definition.timeout_seconds} second timeout."
                        ),
                        started_at=started_at,
                        finished_at=self._now(),
                        verified=False,
                    )

            if isinstance(value, ToolResult):
                value.tool_name = definition.name
                value.verified = (
                    value.status is ToolExecutionStatus.SUCCESS
                )
                return value

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
    def get_openai_tools(self) -> list[dict[str, Any]]:
        """Generate OpenAI-compatible tool schemas for registered tools."""
        tools: list[dict[str, Any]] = []

        type_mapping: dict[Any, str] = {
            str: "string",
            int: "integer",
            float: "number",
            bool: "boolean",
        }

        def annotation_to_schema(annotation: Any) -> dict[str, Any]:
            from types import UnionType
            from typing import Literal, Union, get_args, get_origin

            origin = get_origin(annotation)
            args = get_args(annotation)

            if origin is Literal:
                return {
                    "enum": list(args),
                }

            if origin in (Union, UnionType):
                non_none_args = tuple(
                    arg
                    for arg in args
                    if arg is not type(None)
                )

                if len(non_none_args) == 1:
                    return annotation_to_schema(
                        non_none_args[0]
                    )

            if origin is list:
                item_annotation = (
                    args[0]
                    if args
                    else str
                )

                return {
                    "type": "array",
                    "items": annotation_to_schema(
                        item_annotation
                    ),
                }

            if origin is dict:
                value_annotation = (
                    args[1]
                    if len(args) > 1
                    else Any
                )

                return {
                    "type": "object",
                    "additionalProperties": (
                        annotation_to_schema(
                            value_annotation
                        )
                        if value_annotation is not Any
                        else {}
                    ),
                }

            return {
                "type": type_mapping.get(
                    annotation,
                    "string",
                )
            }

        for registered in self._tools.values():
            definition = registered.definition
            signature = inspect.signature(registered.handler)
            type_hints = get_type_hints(registered.handler)

            properties: dict[str, Any] = {}
            required: list[str] = []

            for parameter in signature.parameters.values():
                if parameter.kind in (
                    inspect.Parameter.VAR_POSITIONAL,
                    inspect.Parameter.VAR_KEYWORD,
                ):
                    continue

                annotation = type_hints.get(parameter.name, parameter.annotation)

                if annotation is inspect.Parameter.empty:
                    property_schema = {
                        "type": "string",
                    }
                else:
                    property_schema = annotation_to_schema(
                        annotation
                    )

                if parameter.default is not inspect.Parameter.empty:
                    property_schema["default"] = parameter.default
                else:
                    required.append(parameter.name)

                properties[parameter.name] = property_schema

            parameters: dict[str, Any] = {
                "type": "object",
                "properties": properties,
            }

            if required:
                parameters["required"] = required

            tools.append(
                {
                    "type": "function",
                    "function": {
                        "name": definition.name,
                        "description": definition.description,
                        "parameters": parameters,
                    },
                }
            )

        return tools

    def __len__(self) -> int:
        return len(self._tools)

