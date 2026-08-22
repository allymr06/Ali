from __future__ import annotations

import asyncio
import contextlib
import json
from uuid import uuid4

from app.conversation.engine import ConversationEngine
from app.core.models import (
    Context,
    Request,
    Response,
    ToolExecutionStatus,
    ToolResult,
)
from app.execution.context import ExecutionContext
from app.execution.models import ExecutionLimits, ExecutionUsage, RetryPolicy
from app.execution.service import ExecutionService
from app.execution.verification import VerificationEngine
from app.execution.task_service import TaskExecutionService
from app.diagnostics.models import DiagnosticLevel
from app.memory.analyzer import MemoryAnalyzer
from app.memory.manager import MemoryManager
from app.memory.safety import SensitiveMemoryError
from app.planning.planner import Planner
from app.planning.models import Plan, PlanStep
from app.planning.executor import PlanExecutor
from app.memory.policy import MemoryPolicy
from app.providers.registry import ProviderRegistry
from app.reliability.admission import (
    AdmissionController,
    AdmissionRejectedError,
)
from app.providers.gateway import ProviderGateway
from app.tasks.manager import TaskManager
from app.tasks.runtime import DurableTaskRuntime
from app.tools.executor import ToolExecutor


class _ExecutionCancelled(Exception):
    """Signal cancellation requested through the public cancel event."""


class CoreEngine:
    """
    Central orchestration entry point for JARVIS.

    CoreEngine coordinates providers, memory, and tool execution
    while keeping implementation details isolated behind explicit
    interfaces.
    """

    def __init__(
        self,
        provider_registry: ProviderRegistry,
        memory_manager: MemoryManager,
        memory_policy: MemoryPolicy | None = None,
        tool_executor: ToolExecutor | None = None,
        task_manager: TaskManager | None = None,
        execution_limits: ExecutionLimits | None = None,
        provider_gateway: ProviderGateway | None = None,
        conversation_engine: ConversationEngine | None = None,
        task_runtime_directory: str | None = None,
        diagnostics=None,
        max_concurrent_requests: int = 8,
        max_queued_requests: int = 32,
        admission_timeout_seconds: float = 2.0,
    ) -> None:
        self._provider_registry = provider_registry
        self._memory_manager = memory_manager
        self._memory_policy = memory_policy or MemoryPolicy()
        self._memory_analyzer = MemoryAnalyzer()
        self._tool_executor = (
            tool_executor
            if tool_executor is not None
            else ToolExecutor()
        )
        self._task_manager = (
            task_manager
            if task_manager is not None
            else TaskManager()
        )
        self._execution_limits = execution_limits or ExecutionLimits()
        self._provider_gateway = provider_gateway or ProviderGateway(
            provider_registry,
            max_retries=0,
        )
        self._conversation_engine = conversation_engine or ConversationEngine()
        self._diagnostics = diagnostics
        self._admission = AdmissionController(
            max_concurrent_requests,
            max_queued_requests,
            admission_timeout_seconds,
        )

        self._planner = Planner()
        self._plan_executor = PlanExecutor(
            self._planner
        )
        self._verification_engine = VerificationEngine()
        self._execution_service = ExecutionService(
            tool_executor=self._tool_executor,
            plan_executor=self._plan_executor,
            verification_engine=self._verification_engine,
            retry_policy=RetryPolicy(),
            limits=self._execution_limits,
        )
        self._task_execution_service = TaskExecutionService(
            task_manager=self._task_manager,
            execution_service=self._execution_service,
            retry_policy=RetryPolicy(),
            limits=self._execution_limits,
        )
        self._task_runtime = (
            DurableTaskRuntime(
                task_runtime_directory,
                task_manager=self._task_manager,
                tool_executor=self._tool_executor,
                limits=self._execution_limits,
                retry_policy=RetryPolicy(),
            )
            if task_runtime_directory is not None
            else None
        )

    @property
    def task_manager(self) -> TaskManager:
        """Return the task manager used by this engine."""
        return self._task_manager

    @property
    def task_runtime(self) -> DurableTaskRuntime | None:
        """Return durable task orchestration when configured."""
        return self._task_runtime

    @property
    def execution_service(self) -> ExecutionService:
        """Return the shared execution service used by plans and tasks."""
        return self._execution_service

    @property
    def execution_limits(self) -> ExecutionLimits:
        return self._execution_limits

    @property
    def provider_gateway(self) -> ProviderGateway:
        return self._provider_gateway

    @property
    def conversation_engine(self) -> ConversationEngine:
        return self._conversation_engine

    @property
    def tool_executor(self) -> ToolExecutor:
        return self._tool_executor

    def create_plan(
        self,
        goal: str,
        steps: list[PlanStep] | None = None,
    ) -> Plan:
        """Create and validate a JARVIS execution plan."""
        return self._planner.create_plan(
            goal,
            steps=steps,
        )

    async def execute_plan(
        self,
        plan: Plan,
        *,
        cancel_event=None,
        execution_context: ExecutionContext | None = None,
    ) -> Plan:
        """Execute a validated plan through the execution service."""
        return await self._execution_service.execute(
            plan,
            cancel_event=cancel_event,
            execution_context=execution_context,
            limits=self._execution_limits,
        )

    async def execute_task(
        self,
        goal: str,
        plan: Plan,
        *,
        cancel_event=None,
        request_id=None,
        conversation_id=None,
    ):
        """
        Create and execute a tracked JARVIS task from a validated plan.
        """
        if goal.strip() != plan.goal.strip():
            raise ValueError(
                "Task goal and plan goal must match."
            )

        if not plan.steps:
            raise ValueError("Cannot execute a task without plan steps.")

        task = self._task_manager.create(goal)

        task_metadata = {
            "request_id": str(request_id)
            if request_id is not None
            else None,
            "conversation_id": str(conversation_id)
            if conversation_id is not None
            else None,
        }

        if any(
            value is not None
            for value in task_metadata.values()
        ):
            plan.metadata["execution_context"] = task_metadata

        if self._task_runtime is not None:
            return await self._task_runtime.execute_new(
                task.task_id,
                plan,
                request_id=request_id,
                conversation_id=conversation_id,
                cancel_event=cancel_event,
            )

        return await self._task_execution_service.execute(
            task.task_id,
            plan,
            cancel_event=cancel_event,
            request_id=request_id,
            conversation_id=conversation_id,
        )

    @staticmethod
    async def _await_provider(
        awaitable,
        *,
        cancel_event,
        timeout: float,
    ):
        operation_task = asyncio.create_task(awaitable)
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
                timeout=timeout,
                return_when=asyncio.FIRST_COMPLETED,
            )

            if operation_task in done:
                return operation_task.result()

            operation_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await operation_task

            if cancel_task is not None and cancel_task in done:
                raise _ExecutionCancelled

            raise TimeoutError("Core execution time budget exhausted.")
        finally:
            if not operation_task.done():
                operation_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await operation_task

            if cancel_task is not None:
                cancel_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await cancel_task

    @staticmethod
    def _model_token_usage(model_response) -> int:
        usage = getattr(model_response, "usage", {}) or {}

        total = usage.get("total_tokens")
        if isinstance(total, int) and total >= 0:
            return total

        for input_key, output_key in (
            ("input_tokens", "output_tokens"),
            ("prompt_tokens", "completion_tokens"),
        ):
            input_tokens = usage.get(input_key, 0)
            output_tokens = usage.get(output_key, 0)

            if isinstance(input_tokens, int) and isinstance(output_tokens, int):
                return max(0, input_tokens) + max(0, output_tokens)

        return 0

    @staticmethod
    def _invalid_tool_result(
        tool_name: str,
        error: str,
    ) -> ToolResult:
        return ToolResult(
            status=ToolExecutionStatus.FAILED,
            tool_name=tool_name,
            message="Invalid tool call.",
            error=error,
            verified=False,
        )

    @staticmethod
    def _tool_message(tool_call_id, result: ToolResult) -> dict[str, object]:
        return {
            "role": "tool",
            "tool_call_id": tool_call_id,
            "content": (
                str(result.data)
                if result.succeeded
                else (result.error or result.message)
            ),
        }

    @staticmethod
    def _tool_filter_values(
        metadata: dict[str, object],
        key: str,
    ) -> set[str] | None:
        value = metadata.get(key)

        if value is None:
            return None

        if isinstance(value, str):
            values = [value]
        elif isinstance(value, (list, tuple, set, frozenset)):
            values = list(value)
        else:
            raise ValueError(f"Request metadata '{key}' must be strings.")

        if not all(isinstance(item, str) for item in values):
            raise ValueError(f"Request metadata '{key}' must be strings.")

        return {item.strip() for item in values if item.strip()}

    @property
    def admission(self) -> AdmissionController:
        return self._admission

    async def handle(
        self,
        request: Request,
        context: Context | None = None,
        *,
        cancel_event=None,
        limits: ExecutionLimits | None = None,
    ) -> Response:
        try:
            lease = await self._admission.acquire()
        except AdmissionRejectedError:
            self._record_diagnostic(
                "request.rejected",
                "Core request rejected by admission control.",
                level=DiagnosticLevel.WARNING,
                trace_id=str(request.request_id),
            )
            if self._diagnostics is not None:
                try:
                    self._diagnostics.metrics.increment("core.requests.rejected")
                except Exception:
                    pass
            raise
        async with lease:
            return await self._handle_admitted(
                request,
                context,
                cancel_event=cancel_event,
                limits=limits,
            )

    async def _handle_admitted(
        self,
        request: Request,
        context: Context | None = None,
        *,
        cancel_event=None,
        limits: ExecutionLimits | None = None,
    ) -> Response:
        """
        Process one request through the JARVIS orchestration pipeline.
        """
        active_context = (
            context
            if context is not None
            else Context()
        )
        self._record_diagnostic(
            "request.started",
            "Core request started.",
            trace_id=str(request.request_id),
            attributes={"source": request.source.value},
        )

        candidate = self._memory_analyzer.analyze(request)

        decision = self._memory_policy.evaluate(
            request,
            candidate,
        )

        memory_saved = False
        memory_write_reason: str | None = None
        if (
            decision.should_remember
            and candidate is not None
        ):
            try:
                self._memory_manager.remember(
                    candidate.content,
                    memory_type=decision.memory_type,
                    importance=decision.importance,
                    confidence=candidate.confidence,
                    source_reference=f"request:{request.request_id}",
                    metadata={"reason": decision.reason},
                )
                memory_saved = True
            except SensitiveMemoryError as exc:
                memory_write_reason = str(exc)

        recalled_memories = self._memory_manager.recall(
            request.text,
            limit=5,
        )

        active_context.memories.clear()
        active_context.memories.extend(
            memory.content
            for memory in recalled_memories
        )
        active_context.values["memory_provenance"] = [
            {
                "memory_id": str(memory.memory_id),
                "source": memory.source.value,
                "source_reference": memory.source_reference,
                "confidence": memory.confidence,
                "freshness": memory.freshness().value,
            }
            for memory in recalled_memories
        ]
        self._conversation_engine.prepare_request(request, active_context)

        provider = self._provider_registry.get_default()
        active_limits = limits or self._execution_limits
        usage = ExecutionUsage()
        usage.start()
        try:
            tool_schemas = self._tool_executor.get_openai_tools(
                names=self._tool_filter_values(
                    request.metadata,
                    "allowed_tools",
                ),
                capabilities=self._tool_filter_values(
                    request.metadata,
                    "tool_capabilities",
                ),
                tags=self._tool_filter_values(
                    request.metadata,
                    "tool_tags",
                ),
            )
        except ValueError as exc:
            tool_schemas = []
            request.metadata["tool_filter_error"] = str(exc)
        exposed_tool_names = {
            str(item.get("function", {}).get("name", ""))
            for item in tool_schemas
            if isinstance(item.get("function"), dict)
        }
        tool_results: list[ToolResult] = []
        processed_tool_calls: dict[str, ToolResult] = {}
        duplicate_tool_calls = 0
        invalid_tool_calls = 0
        executed_tool_calls = 0
        tool_iterations = 0
        outcome = "completed"
        budget_reason: str | None = None
        model_response = None

        while usage.model_iterations < active_limits.max_model_iterations:
            if cancel_event is not None and cancel_event.is_set():
                outcome = "cancelled"
                break

            remaining = usage.remaining_seconds(active_limits)

            if remaining <= 0:
                outcome = "budget_exhausted"
                budget_reason = "time"
                break

            usage.model_iterations += 1

            try:
                model_response = await self._await_provider(
                    self._provider_gateway.generate(
                        request,
                        active_context,
                        tools=tool_schemas or None,
                    ),
                    cancel_event=cancel_event,
                    timeout=remaining,
                )
            except _ExecutionCancelled:
                outcome = "cancelled"
                break
            except TimeoutError:
                outcome = "budget_exhausted"
                budget_reason = "time"
                break
            except Exception as exc:
                self._record_diagnostic(
                    "request.failed",
                    "Core provider request failed.",
                    level=DiagnosticLevel.ERROR,
                    trace_id=str(request.request_id),
                    attributes={"error_type": type(exc).__name__},
                )
                raise

            usage.model_tokens += self._model_token_usage(model_response)

            if usage.model_tokens > active_limits.max_model_tokens:
                outcome = "budget_exhausted"
                budget_reason = "model_tokens"
                break

            tool_calls = getattr(model_response, "tool_calls", []) or []

            if not tool_calls:
                outcome = "completed"
                break

            tool_iterations += 1
            normalized_tool_calls = []
            invalid_tool_call_ids: set[str] = set()
            for tool_call in tool_calls:
                normalized = dict(tool_call)
                original_id = normalized.get("id")
                if not isinstance(original_id, str) or not original_id.strip():
                    generated_id = f"invalid-{uuid4()}"
                    normalized["id"] = generated_id
                    invalid_tool_call_ids.add(generated_id)
                normalized_tool_calls.append(normalized)
            tool_calls = normalized_tool_calls
            self._conversation_engine.add_assistant_tool_calls(
                active_context,
                request_id=request.request_id,
                content=model_response.text or None,
                tool_calls=tool_calls,
            )
            new_tool_call_found = False

            for tool_call in tool_calls:
                tool_call_id = tool_call.get("id")

                if tool_call_id and tool_call_id in processed_tool_calls:
                    duplicate_tool_calls += 1
                    message = self._tool_message(
                        tool_call_id,
                        processed_tool_calls[tool_call_id],
                    )
                    self._conversation_engine.add_tool_result(
                        active_context,
                        request_id=request.request_id,
                        tool_call_id=str(tool_call_id),
                        content=str(message["content"]),
                        metadata={"duplicate": True},
                    )
                    continue

                if usage.tool_calls >= active_limits.max_tool_calls:
                    outcome = "budget_exhausted"
                    budget_reason = "tool_calls"
                    break

                new_tool_call_found = True
                usage.tool_calls += 1
                function = tool_call.get("function", {})

                if not isinstance(function, dict):
                    function = {}

                tool_name = function.get("name")

                if tool_call_id in invalid_tool_call_ids:
                    invalid_tool_calls += 1
                    result = self._invalid_tool_result(
                        str(tool_name or ""),
                        "Tool call id is missing or invalid.",
                    )
                elif not isinstance(tool_name, str) or not tool_name.strip():
                    invalid_tool_calls += 1
                    result = self._invalid_tool_result(
                        "",
                        "Tool function name is missing.",
                    )
                elif tool_name.strip() not in exposed_tool_names:
                    invalid_tool_calls += 1
                    result = self._invalid_tool_result(
                        tool_name,
                        "Tool is not available for this request.",
                    )
                else:
                    raw_arguments = function.get("arguments", "{}")

                    try:
                        arguments = json.loads(raw_arguments)
                    except (TypeError, json.JSONDecodeError):
                        arguments = None

                    if not isinstance(arguments, dict):
                        invalid_tool_calls += 1
                        result = self._invalid_tool_result(
                            tool_name,
                            "Tool arguments must be a JSON object.",
                        )
                    else:
                        remaining = usage.remaining_seconds(active_limits)

                        if remaining <= 0:
                            outcome = "budget_exhausted"
                            budget_reason = "time"
                            break

                        try:
                            executed_tool_calls += 1
                            result = await asyncio.wait_for(
                                self._tool_executor.execute(
                                    tool_name,
                                    parameters=arguments,
                                    cancel_event=cancel_event,
                                ),
                                timeout=remaining,
                            )
                        except TimeoutError:
                            outcome = "budget_exhausted"
                            budget_reason = "time"
                            break

                tool_results.append(result)

                if tool_call_id:
                    processed_tool_calls[tool_call_id] = result

                message = self._tool_message(tool_call_id, result)
                self._conversation_engine.add_tool_result(
                    active_context,
                    request_id=request.request_id,
                    tool_call_id=str(tool_call_id),
                    content=str(message["content"]),
                    metadata={
                        "tool_name": result.tool_name,
                        "status": result.status.value,
                        "verified": self._verification_engine.verify(
                            result
                        ).passed,
                    },
                )

            if outcome in {"budget_exhausted", "cancelled"}:
                break

            if not new_tool_call_found:
                outcome = (
                    "completed"
                    if getattr(model_response, "text", "")
                    else "cycle_detected"
                )
                break
        else:
            outcome = "budget_exhausted"
            budget_reason = "model_iterations"

        successful_tools = sum(result.succeeded for result in tool_results)
        verified_tools = sum(
            self._verification_engine.verify(result).passed
            for result in tool_results
        )
        failed_tools = len(tool_results) - successful_tools
        completion_verified = (
            outcome == "completed"
            and failed_tools == 0
            and verified_tools == len(tool_results)
        )

        if model_response is None:
            response_text = (
                "Request cancelled."
                if outcome == "cancelled"
                else "Execution budget exhausted before a response was produced."
            )
            provider_name = provider.name
            model_name = None
        else:
            response_text = model_response.text
            provider_name = model_response.provider
            model_name = model_response.model

            if not response_text and outcome != "completed":
                response_text = (
                    "Request cancelled."
                    if outcome == "cancelled"
                    else "Execution stopped before verified completion."
                )

        response = Response(
            text=response_text,
            request_id=request.request_id,
            metadata={
                "provider": provider_name,
                "model": model_name,
                "memory_decision": decision.should_remember,
                "memory_saved": memory_saved,
                "memory_write_reason": memory_write_reason,
                "recalled_memory_ids": [
                    str(memory.memory_id) for memory in recalled_memories
                ],
                "memory_count": len(active_context.memories),
                "conversation_id": str(active_context.conversation_id),
                "outcome": outcome,
                "completion_verified": completion_verified,
                "budget_reason": budget_reason,
                "tool_calls": executed_tool_calls,
                "tool_call_attempts": usage.tool_calls,
                "tool_iterations": tool_iterations,
                "model_iterations": usage.model_iterations,
                "model_tokens": usage.model_tokens,
                "successful_tool_calls": successful_tools,
                "verified_tool_calls": verified_tools,
                "failed_tool_calls": failed_tools,
                "invalid_tool_calls": invalid_tool_calls,
                "duplicate_tool_calls": duplicate_tool_calls,
                "elapsed_seconds": usage.elapsed_seconds,
                "provider_metadata": (
                    dict(model_response.metadata)
                    if model_response is not None
                    else {}
                ),
            },
        )
        conversation_turn = self._conversation_engine.complete_response(
            request,
            response,
            active_context,
        )
        conversation = self._conversation_engine.get(
            active_context.conversation_id
        )
        response.metadata["conversation_turn_id"] = (
            str(conversation_turn.turn_id)
            if conversation_turn is not None
            else None
        )
        response.metadata["conversation_turn_count"] = len(
            conversation.turns
        )
        response.metadata["conversation_summary_turn_count"] = (
            conversation.summary_turn_count
        )
        self._record_diagnostic(
            "request.completed",
            "Core request completed.",
            trace_id=str(request.request_id),
            attributes={
                "outcome": outcome,
                "provider": provider_name,
                "model": model_name,
                "tool_calls": executed_tool_calls,
                "completion_verified": completion_verified,
                "elapsed_seconds": usage.elapsed_seconds,
            },
        )
        if self._diagnostics is not None:
            try:
                self._diagnostics.metrics.increment("core.requests")
                self._diagnostics.metrics.observe(
                    "core.request.duration", usage.elapsed_seconds
                )
            except Exception:
                pass
        return response

    def _record_diagnostic(
        self,
        name: str,
        message: str,
        *,
        trace_id: str | None = None,
        attributes: dict[str, object] | None = None,
        level: DiagnosticLevel = DiagnosticLevel.INFO,
    ) -> None:
        if self._diagnostics is None:
            return
        try:
            self._diagnostics.record(
                "core",
                name,
                message,
                level=level,
                trace_id=trace_id,
                attributes=attributes,
            )
        except Exception:
            pass
