from __future__ import annotations

import asyncio
import contextlib
import inspect
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError
from datetime import datetime
from threading import RLock
from typing import Any, get_type_hints
from uuid import UUID

from app.core.models import (
    RiskLevel,
    ToolDefinition,
    ToolExecutionStatus,
    ToolResult,
)
from app.core.time import utc_now
from app.security.approval import (
    ApprovalExecutionContext,
    ApprovalGrant,
    validate_approval_grant,
)
from app.security.permissions import (
    PermissionDecision,
    PermissionEngine,
    PermissionScope,
)
from app.tools.base import RegisteredTool, ToolCallable
from app.tools.contracts import ToolContract
from app.tools.registry import ToolRegistry


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
        if (
            permission_engine is not None
            and hasattr(permission_engine, "list_tools")
            and not hasattr(permission_engine, "evaluate")
        ):
            self._registry = permission_engine
            self._permission_engine = PermissionEngine()
        else:
            self._registry = ToolRegistry()
            self._permission_engine = (
                permission_engine
                if permission_engine is not None
                else PermissionEngine()
            )
        self._active_execution_counts: dict[str, int] = {}
        self._execution_count_lock = RLock()
        self._consumed_approval_ids: dict[UUID, datetime | None] = {}
        self._approval_lock = RLock()

    def register(
        self,
        definition: ToolDefinition,
        handler: ToolCallable,
        *,
        enabled: bool = True,
        source: str = "runtime",
    ) -> None:
        """Register a tool with its executable handler."""
        name = definition.name.strip()

        if not name:
            raise ValueError("Tool name cannot be empty.")

        if not callable(handler):
            raise TypeError("Tool handler must be callable.")

        if (
            "windows" in definition.capabilities
            and "read-only" not in definition.tags
            and not definition.requires_confirmation
        ):
            raise ValueError(
                "Mutating Windows tools must require explicit confirmation."
            )

        self._registry.register(
            RegisteredTool(
                definition=definition,
                handler=handler,
                enabled=enabled,
                source=source,
            )
        )

    def unregister(self, name: str) -> RegisteredTool:
        """Remove and return a registered tool."""
        removed = self._registry.unregister(name)
        with self._execution_count_lock:
            self._active_execution_counts.pop(removed.name, None)
        return removed

    def get(self, name: str) -> RegisteredTool:
        """Return a registered tool."""
        return self._registry.get(name)

    def contains(self, name: str) -> bool:
        """Return whether a tool is registered."""
        return self._registry.contains(name)

    def list_names(self) -> tuple[str, ...]:
        """Return registered tool names."""
        return self._registry.list_names()

    @property
    def registry_revision(self) -> int:
        return self._registry.revision

    @property
    def permission_engine(self) -> PermissionEngine:
        return self._permission_engine

    def enable(self, name: str) -> RegisteredTool:
        """Make a registered tool available for discovery and execution."""
        return self._registry.enable(name)

    def disable(self, name: str) -> RegisteredTool:
        """Hide and block a registered tool without unregistering it."""
        return self._registry.disable(name)

    def _try_acquire_execution_slot(self, definition: ToolDefinition) -> bool:
        limit = definition.max_concurrency

        if limit is None:
            return True

        with self._execution_count_lock:
            active = self._active_execution_counts.get(definition.name, 0)

            if active >= limit:
                return False

            self._active_execution_counts[definition.name] = active + 1
            return True

    def _release_execution_slot(self, definition: ToolDefinition) -> None:
        if definition.max_concurrency is None:
            return

        with self._execution_count_lock:
            active = self._active_execution_counts.get(definition.name, 0)

            if active <= 1:
                self._active_execution_counts.pop(definition.name, None)
            else:
                self._active_execution_counts[definition.name] = active - 1

    def _concurrency_blocked_result(
        self,
        definition: ToolDefinition,
        started_at: datetime,
    ) -> ToolResult:
        return ToolResult(
            status=ToolExecutionStatus.BLOCKED,
            tool_name=definition.name,
            message="Tool concurrency limit reached.",
            error=(
                "Maximum concurrent executions: "
                f"{definition.max_concurrency}."
            ),
            started_at=started_at,
            finished_at=self._now(),
            verified=False,
        )

    def _now(self) -> datetime:
        return utc_now()

    def _validate_arguments(
        self,
        handler: ToolCallable,
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
            if annotation in {str, int, float, bool, bytes}:
                return type(value) is annotation

            return isinstance(value, annotation)

        return True

    def _validate_output(
        self,
        handler: ToolCallable,
        value: Any,
    ) -> str | None:
        """Validate a handler result against its declared return type."""
        try:
            signature = inspect.signature(handler)
            annotation = signature.return_annotation

            try:
                annotation = get_type_hints(
                    handler,
                    globalns=getattr(handler, "__globals__", None),
                    localns=None,
                ).get("return", annotation)
            except (NameError, TypeError, ValueError):
                pass

            if (
                annotation is inspect.Signature.empty
                or isinstance(annotation, str)
            ):
                return None

            if not self._matches_type(value, annotation):
                return (
                    "Tool returned an invalid type. "
                    f"Expected {annotation!r}, got {type(value).__name__}."
                )
        except (TypeError, ValueError):
            return None

        return None

    def _normalize_result(
        self,
        *,
        definition: ToolDefinition,
        value: Any,
        started_at: datetime,
    ) -> ToolResult:
        if isinstance(value, ToolResult):
            value.tool_name = definition.name
            value.started_at = value.started_at or started_at
            value.finished_at = value.finished_at or self._now()

            if value.status is not ToolExecutionStatus.SUCCESS:
                value.verified = False

            return value

        read_only_observation = (
            definition.risk_level is RiskLevel.READ_ONLY
            and not definition.requires_confirmation
        )
        return ToolResult(
            status=ToolExecutionStatus.SUCCESS,
            tool_name=definition.name,
            message=(
                "Read-only tool observation completed."
                if read_only_observation
                else "Tool executed successfully; outcome is not yet verified."
            ),
            data=value,
            started_at=started_at,
            finished_at=self._now(),
            verified=read_only_observation,
        )

    def _interrupted_result(
        self,
        *,
        definition: ToolDefinition,
        started_at: datetime,
        status: ToolExecutionStatus,
        side_effects_may_continue: bool,
    ) -> ToolResult:
        if status is ToolExecutionStatus.CANCELLED:
            message = "Tool execution was cancelled."
            error = "Cancellation requested."
        else:
            message = "Tool execution timed out."
            error = (
                "Tool execution exceeded the "
                f"{definition.timeout_seconds} second timeout."
            )

        return ToolResult(
            status=status,
            tool_name=definition.name,
            message=message,
            error=error,
            started_at=started_at,
            finished_at=self._now(),
            verified=False,
            side_effects_may_continue=side_effects_may_continue,
        )

    def _evaluate_permission(
        self,
        definition: ToolDefinition,
        *,
        operation: str | None,
        parameters: dict[str, Any],
        approval_grant: ApprovalGrant | None,
        approval_context: ApprovalExecutionContext | None,
        permission_scope: PermissionScope | None,
        confirmation_granted: bool,
    ) -> ToolResult | None:
        permission = self._permission_engine.evaluate(
            definition,
            operation=operation,
            parameters=parameters,
            scope=permission_scope,
        )

        if permission.decision == PermissionDecision.DENY:
            return ToolResult(
                status=ToolExecutionStatus.BLOCKED,
                tool_name=definition.name,
                message=permission.reason,
                error="Permission denied.",
                verified=False,
            )

        if permission.decision == PermissionDecision.CONFIRM:
            validation = validate_approval_grant(
                approval_grant,
                operation=permission.operation,
                tool_name=definition.name,
                parameters=parameters,
                context=approval_context,
                tool_version=definition.version,
            )

            if not validation.valid:
                validation_error = (
                    "Unbound confirmation flags are not accepted."
                    if confirmation_granted and approval_grant is None
                    else validation.reason
                )
                error = (
                    "User confirmation required."
                    if approval_grant is None and not confirmation_granted
                    else f"User confirmation required: {validation_error}"
                )
                return ToolResult(
                    status=ToolExecutionStatus.BLOCKED,
                    tool_name=definition.name,
                    message=permission.reason,
                    error=error,
                    verified=False,
                )

            if not self._consume_approval_grant(approval_grant):
                return ToolResult(
                    status=ToolExecutionStatus.BLOCKED,
                    tool_name=definition.name,
                    message="Approval grant has already been used.",
                    error="Approval replay blocked.",
                    verified=False,
                )

        return None

    def _consume_approval_grant(self, grant: ApprovalGrant) -> bool:
        """Atomically consume one capability so concurrent replay fails closed."""
        now = utc_now()
        with self._approval_lock:
            expired = tuple(
                operation_id
                for operation_id, expiry in self._consumed_approval_ids.items()
                if expiry is not None and expiry <= now
            )
            for operation_id in expired:
                self._consumed_approval_ids.pop(operation_id, None)
            if grant.operation_id in self._consumed_approval_ids:
                return False
            self._consumed_approval_ids[grant.operation_id] = grant.expires_at
            return True

    def execute(
        self,
        name: str,
        *args: Any,
        operation: str | None = None,
        parameters: dict[str, Any] | None = None,
        confirmation_granted: bool = False,
        approval_grant: ApprovalGrant | None = None,
        approval_context: ApprovalExecutionContext | None = None,
        permission_scope: PermissionScope | None = None,
        cancel_event: Any | None = None,
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
                approval_grant=approval_grant,
                approval_context=approval_context,
                permission_scope=permission_scope,
                cancel_event=cancel_event,
            )

        return self._execute_sync(
            name,
            *args,
            operation=operation,
            parameters=parameters,
            confirmation_granted=confirmation_granted,
            approval_grant=approval_grant,
            approval_context=approval_context,
            permission_scope=permission_scope,
            cancel_event=cancel_event,
        )

    def _execute_sync(
        self,
        name: str,
        *args: Any,
        operation: str | None,
        parameters: dict[str, Any] | None,
        confirmation_granted: bool,
        approval_grant: ApprovalGrant | None,
        approval_context: ApprovalExecutionContext | None,
        permission_scope: PermissionScope | None,
        cancel_event: Any | None,
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

        if parameters is not None and not isinstance(parameters, dict):
            return ToolResult(
                status=ToolExecutionStatus.FAILED,
                tool_name=definition.name,
                message="Invalid tool arguments.",
                error="Tool parameters must be a dictionary.",
                finished_at=self._now(),
                verified=False,
            )

        execution_parameters = dict(parameters or {})
        started_at = self._now()

        blocked = self._evaluate_permission(
            definition,
            operation=operation,
            parameters=execution_parameters,
            approval_grant=approval_grant,
            approval_context=approval_context,
            permission_scope=permission_scope,
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

        if not self._try_acquire_execution_slot(definition):
            return self._concurrency_blocked_result(definition, started_at)

        try:
            if cancel_event is not None and cancel_event.is_set():
                return self._interrupted_result(
                    definition=definition,
                    started_at=started_at,
                    status=ToolExecutionStatus.CANCELLED,
                    side_effects_may_continue=False,
                )

            pool = ThreadPoolExecutor(max_workers=1)
            future = pool.submit(
                registered.handler,
                *args,
                **execution_parameters,
            )
            deadline = time.monotonic() + definition.timeout_seconds

            try:
                while True:
                    if cancel_event is not None and cancel_event.is_set():
                        future.cancel()
                        return self._interrupted_result(
                            definition=definition,
                            started_at=started_at,
                            status=ToolExecutionStatus.CANCELLED,
                            side_effects_may_continue=future.running(),
                        )

                    remaining = deadline - time.monotonic()

                    if remaining <= 0:
                        future.cancel()
                        return self._interrupted_result(
                            definition=definition,
                            started_at=started_at,
                            status=ToolExecutionStatus.TIMEOUT,
                            side_effects_may_continue=future.running(),
                        )

                    try:
                        value = future.result(timeout=min(remaining, 0.01))
                        break
                    except FuturesTimeoutError:
                        continue
            finally:
                pool.shutdown(wait=False, cancel_futures=True)

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

            output_error = self._validate_output(registered.handler, value)

            if output_error is not None:
                return ToolResult(
                    status=ToolExecutionStatus.FAILED,
                    tool_name=definition.name,
                    message="Invalid tool output.",
                    error=output_error,
                    started_at=started_at,
                    finished_at=self._now(),
                    verified=False,
                )

            return self._normalize_result(
                definition=definition,
                value=value,
                started_at=started_at,
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
        finally:
            self._release_execution_slot(definition)

    async def _execute_async(
        self,
        name: str,
        *args: Any,
        operation: str | None,
        parameters: dict[str, Any] | None,
        confirmation_granted: bool,
        approval_grant: ApprovalGrant | None,
        approval_context: ApprovalExecutionContext | None,
        permission_scope: PermissionScope | None,
        cancel_event: Any | None,
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

        if parameters is not None and not isinstance(parameters, dict):
            return ToolResult(
                status=ToolExecutionStatus.FAILED,
                tool_name=definition.name,
                message="Invalid tool arguments.",
                error="Tool parameters must be a dictionary.",
                finished_at=self._now(),
                verified=False,
            )

        execution_parameters = dict(parameters or {})
        started_at = self._now()

        blocked = self._evaluate_permission(
            definition,
            operation=operation,
            parameters=execution_parameters,
            approval_grant=approval_grant,
            approval_context=approval_context,
            permission_scope=permission_scope,
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

        if not self._try_acquire_execution_slot(definition):
            return self._concurrency_blocked_result(definition, started_at)

        try:
            if cancel_event is not None and cancel_event.is_set():
                return self._interrupted_result(
                    definition=definition,
                    started_at=started_at,
                    status=ToolExecutionStatus.CANCELLED,
                    side_effects_may_continue=False,
                )

            thread_based = not inspect.iscoroutinefunction(
                registered.handler
            )

            if thread_based:
                operation_task = asyncio.create_task(
                    asyncio.to_thread(
                        registered.handler,
                        *args,
                        **execution_parameters,
                    )
                )
            else:
                operation_task = asyncio.create_task(
                    registered.handler(
                        *args,
                        **execution_parameters,
                    )
                )

            cancel_task = (
                asyncio.create_task(cancel_event.wait())
                if cancel_event is not None
                else None
            )
            wait_set = {operation_task}

            if cancel_task is not None:
                wait_set.add(cancel_task)

            try:
                done, _ = await asyncio.wait(
                    wait_set,
                    timeout=definition.timeout_seconds,
                    return_when=asyncio.FIRST_COMPLETED,
                )
            except asyncio.CancelledError:
                operation_task.cancel()

                with contextlib.suppress(asyncio.CancelledError):
                    await operation_task

                if cancel_task is not None:
                    cancel_task.cancel()

                raise

            if operation_task not in done:
                operation_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await operation_task
                was_cancelled = (
                    cancel_task is not None
                    and cancel_task in done
                    and bool(cancel_task.result())
                )

                return self._interrupted_result(
                    definition=definition,
                    started_at=started_at,
                    status=(
                        ToolExecutionStatus.CANCELLED
                        if was_cancelled
                        else ToolExecutionStatus.TIMEOUT
                    ),
                    side_effects_may_continue=thread_based,
                )

            value = operation_task.result()

            if inspect.isawaitable(value):
                value.close()
                return ToolResult(
                    status=ToolExecutionStatus.FAILED,
                    tool_name=definition.name,
                    message="Invalid asynchronous tool handler.",
                    error=(
                        "A synchronous handler returned an awaitable; "
                        "register an async function instead."
                    ),
                    started_at=started_at,
                    finished_at=self._now(),
                    verified=False,
                )

            output_error = self._validate_output(registered.handler, value)

            if output_error is not None:
                return ToolResult(
                    status=ToolExecutionStatus.FAILED,
                    tool_name=definition.name,
                    message="Invalid tool output.",
                    error=output_error,
                    started_at=started_at,
                    finished_at=self._now(),
                    verified=False,
                )

            return self._normalize_result(
                definition=definition,
                value=value,
                started_at=started_at,
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
        finally:
            if "cancel_task" in locals() and cancel_task is not None:
                cancel_task.cancel()
            self._release_execution_slot(definition)

    @staticmethod
    def _annotation_to_schema(annotation: Any) -> dict[str, Any]:
        from types import UnionType
        from typing import Literal, Union, get_args, get_origin

        type_mapping: dict[Any, str] = {
            str: "string",
            int: "integer",
            float: "number",
            bool: "boolean",
            type(None): "null",
        }
        origin = get_origin(annotation)
        args = get_args(annotation)

        if annotation is Any or annotation is inspect.Signature.empty:
            return {}

        if origin is Literal:
            return {"enum": list(args)}

        if origin in (Union, UnionType):
            return {
                "anyOf": [
                    ToolExecutor._annotation_to_schema(option)
                    for option in args
                ]
            }

        if origin in {list, set, frozenset}:
            return {
                "type": "array",
                "items": ToolExecutor._annotation_to_schema(
                    args[0] if args else Any
                ),
            }

        if origin is tuple:
            if len(args) == 2 and args[1] is Ellipsis:
                return {
                    "type": "array",
                    "items": ToolExecutor._annotation_to_schema(args[0]),
                }

            return {
                "type": "array",
                "prefixItems": [
                    ToolExecutor._annotation_to_schema(item)
                    for item in args
                ],
                "minItems": len(args),
                "maxItems": len(args),
            }

        if origin is dict:
            value_annotation = args[1] if len(args) > 1 else Any
            return {
                "type": "object",
                "additionalProperties": (
                    ToolExecutor._annotation_to_schema(value_annotation)
                ),
            }

        return {"type": type_mapping.get(annotation, "string")}

    def _input_schema(self, registered: RegisteredTool) -> dict[str, Any]:
        signature = inspect.signature(registered.handler)

        try:
            type_hints = get_type_hints(registered.handler)
        except (NameError, TypeError, ValueError):
            type_hints = {}

        properties: dict[str, Any] = {}
        required: list[str] = []

        for parameter in signature.parameters.values():
            if parameter.kind in {
                inspect.Parameter.VAR_POSITIONAL,
                inspect.Parameter.VAR_KEYWORD,
            }:
                continue

            annotation = type_hints.get(parameter.name, parameter.annotation)
            property_schema = self._annotation_to_schema(annotation)

            if not property_schema:
                property_schema = {"type": "string"}

            if parameter.default is not inspect.Parameter.empty:
                property_schema["default"] = parameter.default
            else:
                required.append(parameter.name)

            properties[parameter.name] = property_schema

        schema: dict[str, Any] = {
            "type": "object",
            "properties": properties,
            "additionalProperties": False,
        }

        if required:
            schema["required"] = required

        return schema

    def _output_schema(self, registered: RegisteredTool) -> dict[str, Any]:
        signature = inspect.signature(registered.handler)

        try:
            annotation = get_type_hints(registered.handler).get(
                "return",
                signature.return_annotation,
            )
        except (NameError, TypeError, ValueError):
            annotation = signature.return_annotation

        return self._annotation_to_schema(annotation)

    def get_contract_objects(
        self,
        *,
        names: set[str] | frozenset[str] | None = None,
        capabilities: set[str] | frozenset[str] | None = None,
        tags: set[str] | frozenset[str] | None = None,
        include_disabled: bool = False,
    ) -> tuple[ToolContract, ...]:
        """Return typed contracts filtered by required capabilities/tags."""
        return tuple(
            ToolContract(
                definition=registered.definition,
                input_schema=self._input_schema(registered),
                output_schema=self._output_schema(registered),
                enabled=registered.enabled,
                source=registered.source,
            )
            for registered in self._registry.list_tools(
                names=names,
                capabilities=capabilities,
                tags=tags,
                include_disabled=include_disabled,
            )
        )

    def get_tool_contracts(
        self,
        *,
        names: set[str] | frozenset[str] | None = None,
        capabilities: set[str] | frozenset[str] | None = None,
        tags: set[str] | frozenset[str] | None = None,
        include_disabled: bool = False,
    ) -> list[dict[str, Any]]:
        """Return provider-neutral input, output, and security contracts."""
        return [
            contract.to_dict()
            for contract in self.get_contract_objects(
                names=names,
                capabilities=capabilities,
                tags=tags,
                include_disabled=include_disabled,
            )
        ]

    def get_openai_tools(
        self,
        *,
        names: set[str] | frozenset[str] | None = None,
        capabilities: set[str] | frozenset[str] | None = None,
        tags: set[str] | frozenset[str] | None = None,
    ) -> list[dict[str, Any]]:
        """Generate filtered OpenAI-compatible function schemas."""
        return [
            contract.to_openai()
            for contract in self.get_contract_objects(
                names=names,
                capabilities=capabilities,
                tags=tags,
            )
        ]

    def __len__(self) -> int:
        return len(self._registry)

