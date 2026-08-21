from __future__ import annotations

import asyncio
import contextlib
import json

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
from app.memory.analyzer import MemoryAnalyzer
from app.memory.manager import MemoryManager
from app.planning.planner import Planner
from app.planning.models import Plan, PlanStep
from app.planning.executor import PlanExecutor
from app.memory.policy import MemoryPolicy
from app.providers.registry import ProviderRegistry
from app.tasks.manager import TaskManager
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

    @property
    def task_manager(self) -> TaskManager:
        """Return the task manager used by this engine."""
        return self._task_manager

    @property
    def execution_service(self) -> ExecutionService:
        """Return the shared execution service used by plans and tasks."""
        return self._execution_service

    @property
    def execution_limits(self) -> ExecutionLimits:
        return self._execution_limits

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

    async def handle(
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

        candidate = self._memory_analyzer.analyze(request)

        decision = self._memory_policy.evaluate(
            request,
            candidate,
        )

        if (
            decision.should_remember
            and candidate is not None
        ):
            self._memory_manager.remember(
                candidate.content,
                memory_type=decision.memory_type,
                importance=decision.importance,
                confidence=candidate.confidence,
            )

        recalled_memories = self._memory_manager.recall(
            request.text,
            limit=5,
        )

        active_context.memories.clear()
        active_context.memories.extend(
            memory.content
            for memory in recalled_memories
        )

        provider = self._provider_registry.get_default()
        active_limits = limits or self._execution_limits
        usage = ExecutionUsage()
        usage.start()
        tool_schemas = self._tool_executor.get_openai_tools()
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
                    provider.generate(
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
            messages = active_context.values.setdefault("messages", [])
            messages.append(
                {
                    "role": "assistant",
                    "content": model_response.text or None,
                    "tool_calls": tool_calls,
                }
            )
            new_tool_call_found = False

            for tool_call in tool_calls:
                tool_call_id = tool_call.get("id")

                if tool_call_id and tool_call_id in processed_tool_calls:
                    duplicate_tool_calls += 1
                    messages.append(
                        self._tool_message(
                            tool_call_id,
                            processed_tool_calls[tool_call_id],
                        )
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

                if not isinstance(tool_name, str) or not tool_name.strip():
                    invalid_tool_calls += 1
                    result = self._invalid_tool_result(
                        "",
                        "Tool function name is missing.",
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

                messages.append(self._tool_message(tool_call_id, result))

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

        return Response(
            text=response_text,
            request_id=request.request_id,
            metadata={
                "provider": provider_name,
                "model": model_name,
                "memory_decision": decision.should_remember,
                "memory_count": len(active_context.memories),
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
            },
        )
