from __future__ import annotations

import json

from app.core.models import Context, Request, Response
from app.memory.analyzer import MemoryAnalyzer
from app.memory.manager import MemoryManager
from app.planning.planner import Planner
from app.planning.models import Plan, PlanStep
from app.planning.executor import PlanExecutor
from app.memory.policy import MemoryPolicy
from app.providers.registry import ProviderRegistry
from app.tasks.manager import TaskManager
from app.tools.executor import ToolExecutor


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

        self._planner = Planner()
        self._plan_executor = PlanExecutor(
            self._planner
        )

    @property
    def task_manager(self) -> TaskManager:
        """Return the task manager used by this engine."""
        return self._task_manager

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
    ) -> Plan:
        """Execute a validated plan through the tool pipeline."""
        self._plan_executor.start(plan)

        while plan.status.value == "running":
            step = self._plan_executor.next_step(plan)

            if step is None:
                break

            tool_name = step.metadata.get("tool_name")

            if not isinstance(tool_name, str) or not tool_name:
                self._plan_executor.fail_step(
                    plan,
                    step,
                )
                break

            parameters = step.metadata.get(
                "parameters",
                {},
            )

            if not isinstance(parameters, dict):
                self._plan_executor.fail_step(
                    plan,
                    step,
                )
                break

            result = await self._tool_executor.execute(
                tool_name,
                parameters=parameters,
            )

            if result.succeeded:
                step.metadata["tool_result"] = result.data

                self._plan_executor.complete_step(
                    plan,
                    step,
                )
            else:
                step.metadata["tool_error"] = (
                    result.error
                    or result.message
                )

                self._plan_executor.fail_step(
                    plan,
                    step,
                )

        return plan
    async def handle(
        self,
        request: Request,
        context: Context | None = None,
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

        tool_schemas = (
            self._tool_executor.get_openai_tools()
        )

        tool_results = []
        processed_tool_call_ids: set[str] = set()
        max_tool_iterations = 5

        for _ in range(max_tool_iterations):
            model_response = await provider.generate(
                request,
                active_context,
                tools=tool_schemas or None,
            )

            tool_calls = getattr(
                model_response,
                "tool_calls",
                [],
            ) or []

            if not tool_calls:
                break

            messages = active_context.values.setdefault(
                "messages",
                [],
            )

            # Preserve the assistant tool-call message.
            # This is required to maintain a valid tool-call
            # conversation chain.
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

                if (
                    tool_call_id
                    and tool_call_id in processed_tool_call_ids
                ):
                    continue

                new_tool_call_found = True

                if tool_call_id:
                    processed_tool_call_ids.add(
                        tool_call_id
                    )

                function = tool_call.get(
                    "function",
                    {},
                )

                tool_name = function.get("name")

                if not tool_name:
                    continue

                raw_arguments = function.get(
                    "arguments",
                    "{}",
                )

                try:
                    arguments = json.loads(
                        raw_arguments
                    )
                except (
                    TypeError,
                    json.JSONDecodeError,
                ):
                    continue

                if not isinstance(arguments, dict):
                    continue

                result = await self._tool_executor.execute(
                    tool_name,
                    parameters=arguments,
                )

                tool_results.append(result)

                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tool_call_id,
                        "content": (
                            str(result.data)
                            if result.succeeded
                            else (
                                result.error
                                or result.message
                            )
                        ),
                    }
                )

            if not new_tool_call_found:
                break

        return Response(
            text=model_response.text,
            request_id=request.request_id,
            metadata={
                "provider": model_response.provider,
                "model": model_response.model,
                "memory_decision": decision.should_remember,
                "memory_count": len(
                    active_context.memories
                ),
                "tool_calls": len(tool_results),
                "tool_iterations": min(
                    len(tool_results),
                    max_tool_iterations,
                ),
            },
        )




