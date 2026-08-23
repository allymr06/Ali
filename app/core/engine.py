from __future__ import annotations

import asyncio
import contextlib
import json
from collections.abc import Callable
from datetime import timedelta
from uuid import uuid4

from app.conversation.engine import ConversationEngine
from app.core.models import (
    Context,
    Request,
    RequestSource,
    Response,
    ToolExecutionStatus,
    ToolResult,
)
from app.core.assurance import summarize_assurance
from app.core.interaction_policy import InteractionPolicy
from app.core.time import utc_now
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
from app.providers.base import ModelResponse
from app.providers.registry import ProviderRegistry
from app.reliability.admission import (
    AdmissionController,
    AdmissionRejectedError,
)
from app.providers.gateway import ProviderGateway
from app.tasks.manager import TaskManager
from app.tasks.runtime import DurableTaskRuntime
from app.tools.executor import ToolExecutor
from app.tools.fast_actions import ApprovedApplicationFastRouter
from app.tools.selection import ToolSchemaSelector
from app.tools.routing import DeterministicToolRouter
from app.security.approval import (
    ApprovalExecutionContext,
    ApprovalGrant,
    approval_binding_digest,
)
from app.security.interactive import (
    InteractiveApprovalCallback,
    InteractiveApprovalRequest,
    safe_approval_parameters,
)
from app.security.permissions import PermissionDecision


class _ExecutionCancelled(Exception):
    """Signal cancellation requested through the public cancel event."""


class CoreEngine:
    """
    Central orchestration entry point for JARVIS.

    CoreEngine coordinates providers, memory, and tool execution
    while keeping implementation details isolated behind explicit
    interfaces.
    """

    ACTION_INTEGRITY_DIRECTIVE = (
        "Eylem bütünlüğü: Bir eylemi yalnızca ilgili aracı ÇAĞIRARAK "
        "yapabilirsin; araç çağırmadan 'yapıyorum', 'başlattım', "
        "'ayarladım' deme. İstek bir eylem gerektiriyorsa uygun aracı "
        "çağır; uygun araç yoksa bunu açıkça söyle. Araç sonucu "
        "başarısızsa başarılı gibi anlatma."
    )

    VOICE_RESPONSE_DIRECTIVE = (
        "Bu istek sesli olarak yanıtlanacak. En fazla iki kısa ve "
        "doğal cümleyle cevap ver; liste, başlık ve kod kullanma. "
        "Detay gerekiyorsa en önemli noktayı söyle ve daha fazlasını "
        "isteyip istemediğini sor."
    )

    def __init__(
        self,
        provider_registry: ProviderRegistry,
        memory_manager: MemoryManager,
        memory_policy: MemoryPolicy | None = None,
        tool_executor: ToolExecutor | None = None,
        task_manager: TaskManager | None = None,
        execution_limits: ExecutionLimits | None = None,
        provider_gateway: ProviderGateway | None = None,
        action_model: str | None = None,
        fast_action_router: ApprovedApplicationFastRouter | None = None,
        tool_schema_selector: ToolSchemaSelector | None = None,
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
        self._deterministic_tool_router = (
            DeterministicToolRouter()
        )
        self._interaction_policy = (
            InteractionPolicy()
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
        self._action_model = (
            action_model.strip() if action_model else None
        ) or None
        self._fast_action_router = fast_action_router
        self._tool_schema_selector = tool_schema_selector
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

    async def _execute_tool_with_interactive_approval(
        self,
        *,
        tool_name: str,
        parameters: dict[str, object],
        request: Request,
        context: Context,
        approval_callback: InteractiveApprovalCallback | None,
        approval_timeout_seconds: float,
        cancel_event=None,
    ) -> ToolResult:
        """Execute one call only after a bound, user-originated approval."""
        try:
            registered = self._tool_executor.get(tool_name)
        except KeyError:
            return await self._tool_executor.execute(
                tool_name,
                parameters=parameters,
                cancel_event=cancel_event,
            )

        definition = registered.definition
        permission = self._tool_executor.permission_engine.evaluate(
            definition,
            operation=tool_name,
            parameters=parameters,
        )

        approval_grant = None
        approval_context = None

        if permission.decision is PermissionDecision.CONFIRM:
            if approval_callback is None:
                return ToolResult(
                    status=ToolExecutionStatus.BLOCKED,
                    tool_name=tool_name,
                    message=permission.reason,
                    error="User confirmation required.",
                    data={"approval_status": "required"},
                    verified=False,
                )

            operation_id = uuid4()
            expires_at = utc_now() + timedelta(minutes=5)
            approval_request = InteractiveApprovalRequest(
                operation_id=operation_id,
                request_id=request.request_id,
                conversation_id=context.conversation_id,
                request_source=request.source.value,
                tool_name=tool_name,
                operation=permission.operation,
                risk_level=permission.risk_level,
                reason=permission.reason,
                parameters=safe_approval_parameters(parameters),
                expires_at=expires_at,
            )

            try:
                approved = await asyncio.wait_for(
                    approval_callback(approval_request),
                    timeout=max(0.1, min(300.0, approval_timeout_seconds)),
                )
            except (TimeoutError, asyncio.CancelledError):
                raise
            except Exception:
                approved = False

            if approved is not True:
                return ToolResult(
                    status=ToolExecutionStatus.BLOCKED,
                    tool_name=tool_name,
                    message="Operation was not approved by the user.",
                    error="User denied the operation.",
                    data={"approval_status": "denied"},
                    verified=False,
                )

            approval_context = ApprovalExecutionContext(
                task_id=None,
                plan_id=None,
                step_id=operation_id,
                conversation_id=context.conversation_id,
                request_id=request.request_id,
                approval_operation_id=operation_id,
            )
            try:
                binding_digest = approval_binding_digest(
                    operation=permission.operation,
                    tool_name=tool_name,
                    parameters=parameters,
                    task_id=None,
                    plan_id=None,
                    step_id=operation_id,
                    tool_version=definition.version,
                    conversation_id=context.conversation_id,
                    request_id=request.request_id,
                )
            except ValueError as exc:
                return ToolResult(
                    status=ToolExecutionStatus.BLOCKED,
                    tool_name=tool_name,
                    message="Operation approval could not be bound safely.",
                    error=str(exc),
                    verified=False,
                )

            approval_grant = ApprovalGrant(
                operation_id=operation_id,
                binding_digest=binding_digest,
                expires_at=expires_at,
                task_id=None,
            )

        return await self._tool_executor.execute(
            tool_name,
            operation=permission.operation,
            parameters=parameters,
            approval_grant=approval_grant,
            approval_context=approval_context,
            cancel_event=cancel_event,
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

    def _tool_output_is_sensitive(self, tool_name: str) -> bool:
        try:
            definition = self._tool_executor.get(tool_name).definition
        except KeyError:
            return False
        return definition.metadata.get("sensitive_output") is True

    @staticmethod
    def _persistent_tool_content(
        message: dict[str, object],
        *,
        sensitive: bool,
    ) -> str:
        if sensitive:
            return (
                "Sensitive tool output was used for this request "
                "but was not retained."
            )
        return str(message["content"])

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

    async def _collect_streamed_response(
        self,
        request: Request,
        context: Context,
        *,
        model: str | None,
        system_prompt: str | None,
        callback: Callable[[str], None] | None,
        cancel_event=None,
    ) -> ModelResponse:
        """
        Collect one chat-only provider stream into the normal
        ModelResponse contract while exposing cumulative text
        to the caller as soon as chunks arrive.

        This path never accepts tool calls.
        """
        parts: list[str] = []
        provider_name: str | None = None
        model_name: str | None = model
        finish_reason: str | None = None
        usage: dict[str, int] = {}
        metadata: dict[str, object] = {}

        async for chunk in self._provider_gateway.stream(
            request,
            context,
            model=model,
            system_prompt=system_prompt,
            tools=None,
            cancel_event=cancel_event,
        ):
            if chunk.tool_calls:
                raise RuntimeError(
                    "Tool calls are not allowed on the "
                    "chat-only streaming route."
                )

            provider_name = chunk.provider
            model_name = chunk.model or model_name

            if chunk.finish_reason is not None:
                finish_reason = chunk.finish_reason

            if chunk.usage:
                usage = dict(chunk.usage)

            if chunk.metadata:
                metadata.update(
                    chunk.metadata
                )

            if not chunk.text:
                continue

            parts.append(
                chunk.text
            )

            if callback is not None:
                cumulative = "".join(
                    parts
                )

                try:
                    callback(
                        cumulative
                    )
                except Exception:
                    # UI presentation failures must never
                    # break the Core request.
                    pass

        if provider_name is None:
            raise RuntimeError(
                "Streaming provider returned no chunks."
            )

        if model_name is None:
            raise RuntimeError(
                "Streaming provider did not identify a model."
            )

        metadata["streamed"] = True

        return ModelResponse(
            text="".join(parts),
            model=model_name,
            provider=provider_name,
            finish_reason=finish_reason,
            tool_calls=[],
            usage=usage,
            metadata=metadata,
        )

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
        stream_callback: Callable[[str], None] | None = None,
        approval_callback: InteractiveApprovalCallback | None = None,
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
                stream_callback=stream_callback,
                approval_callback=approval_callback,
            )

    async def _handle_admitted(
        self,
        request: Request,
        context: Context | None = None,
        *,
        cancel_event=None,
        limits: ExecutionLimits | None = None,
        stream_callback: Callable[[str], None] | None = None,
        approval_callback: InteractiveApprovalCallback | None = None,
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
        interaction_decision = (
            self._interaction_policy.evaluate(
                request
            )
        )

        if not interaction_decision.expose_tools:
            tool_schemas = []

        exposed_tool_names = {
            str(item.get("function", {}).get("name", ""))
            for item in tool_schemas
            if isinstance(item.get("function"), dict)
        }
        fast_action_route = None

        if (
            self._fast_action_router
            is not None
            and interaction_decision.expose_tools
        ):
            fast_action_route = (
                self._fast_action_router
                .route(
                    request,
                    available_tool_names=(
                        exposed_tool_names
                    ),
                )
            )

        tool_results: list[ToolResult] = []
        processed_tool_calls: dict[str, ToolResult] = {}
        duplicate_tool_calls = 0
        invalid_tool_calls = 0
        executed_tool_calls = 0
        tool_iterations = 0
        outcome = "completed"
        budget_reason: str | None = None
        model_response = None
        blocked_plaintext_tool_call: str | None = None

        deterministic_route = (
            self._deterministic_tool_router.route(
                request,
                tool_executor=self._tool_executor,
                tool_schemas=tool_schemas,
            )
        )

        pending_tool_calls = None
        deterministic_tool_name = None
        deterministic_tool_reason = None

        if deterministic_route is not None:
            deterministic_tool_name = (
                deterministic_route.tool_name
            )
            deterministic_tool_reason = (
                deterministic_route.reason
            )

            pending_tool_calls = [
                {
                    "id": (
                        "deterministic-"
                        f"{uuid4()}"
                    ),
                    "type": "function",
                    "function": {
                        "name": (
                            deterministic_route.tool_name
                        ),
                        "arguments": json.dumps(
                            deterministic_route.parameters,
                            separators=(",", ":"),
                            sort_keys=True,
                        ),
                    },
                }
            ]

            tool_schemas = [
                schema
                for schema in tool_schemas
                if (
                    isinstance(
                        schema.get("function"),
                        dict,
                    )
                    and schema["function"].get(
                        "name"
                    )
                    != deterministic_route.tool_name
                )
            ]

        fast_action_tool_name = None

        if (
            deterministic_route is None
            and fast_action_route is not None
        ):
            fast_action_tool_name = (
                fast_action_route.tool_name
            )

            pending_tool_calls = [
                {
                    "id": (
                        "fast-action-"
                        f"{uuid4()}"
                    ),
                    "type": "function",
                    "function": {
                        "name": (
                            fast_action_tool_name
                        ),
                        "arguments": json.dumps(
                            (
                                fast_action_route
                                .parameters
                            ),
                            separators=(
                                ",",
                                ":",
                            ),
                            sort_keys=True,
                        ),
                    },
                }
            ]

            tool_schemas = [
                schema
                for schema
                in tool_schemas
                if (
                    isinstance(
                        schema.get(
                            "function"
                        ),
                        dict,
                    )
                    and schema[
                        "function"
                    ].get(
                        "name"
                    )
                    == fast_action_tool_name
                )
            ]

            exposed_tool_names = {
                fast_action_tool_name
            }

        provider_model_override = None
        provider_system_prompt = (
            interaction_decision.system_prompt
        )

        if request.source is RequestSource.VOICE:
            # Spoken replies are synthesized sentence by sentence, so
            # brevity directly cuts both generation and speech latency.
            voice_directive = self.VOICE_RESPONSE_DIRECTIVE
            provider_system_prompt = (
                f"{provider_system_prompt}\n\n{voice_directive}"
                if provider_system_prompt
                else voice_directive
            )

        tool_schema_count_before = len(
            tool_schemas
        )
        tool_schema_selection_reason = None
        tool_schema_selection_names: list[str] = []

        if (
            self._tool_schema_selector is not None
            and deterministic_tool_name is None
            and fast_action_tool_name is None
        ):
            selection = (
                self._tool_schema_selector.select(
                    request,
                    available_names=(
                        exposed_tool_names
                    ),
                )
            )

            selected_names = set(
                selection.names
            )

            tool_schemas = [
                schema
                for schema in tool_schemas
                if (
                    isinstance(
                        schema.get("function"),
                        dict,
                    )
                    and schema[
                        "function"
                    ].get(
                        "name"
                    )
                    in selected_names
                )
            ]

            exposed_tool_names.intersection_update(
                selected_names
            )

            tool_schema_selection_reason = (
                selection.reason
            )

            tool_schema_selection_names = sorted(
                selected_names
            )

        tool_schema_count_after = len(
            tool_schemas
        )

        if tool_schemas:
            # Assistant-level no-false-completion guard: with tools in
            # hand, claiming an action without calling one is a lie the
            # user cannot detect.
            action_directive = self.ACTION_INTEGRITY_DIRECTIVE
            provider_system_prompt = (
                f"{provider_system_prompt}\n\n{action_directive}"
                if provider_system_prompt
                else action_directive
            )
            # Tool-bearing turns escalate to the action model: correct
            # tool selection beats the latency of the lite chat model.
            if (
                self._action_model
                and provider.name == "gemini"
                and request.metadata.get("model") is None
            ):
                provider_model_override = self._action_model
            # Tool selection needs deliberate reasoning even when the
            # rate-limit fallback drops the turn to the lite model.
            request.metadata.setdefault(
                "reasoning_task_type", "complex"
            )

        while usage.model_iterations < active_limits.max_model_iterations:
            if interaction_decision.direct_response is not None:
                model_response = ModelResponse(
                    text=interaction_decision.direct_response,
                    model="jarvis-identity-composer",
                    provider="core",
                    finish_reason="stop",
                    tool_calls=[],
                    usage={},
                    metadata={
                        "direct_response": interaction_decision.kind,
                        "generation_skipped": True,
                        "identity_source": "jarvis-core",
                    },
                )

                outcome = "completed"
                break

            if cancel_event is not None and cancel_event.is_set():
                outcome = "cancelled"
                break

            remaining = usage.remaining_seconds(active_limits)

            if remaining <= 0:
                outcome = "budget_exhausted"
                budget_reason = "time"
                break

            if pending_tool_calls is not None:
                tool_calls = pending_tool_calls
                pending_tool_calls = None
                assistant_tool_content = None
            else:
                if (
                    fast_action_route
                    is not None
                    and fast_action_tool_name
                    is not None
                    and tool_results
                    and (
                        tool_results[-1]
                        .tool_name
                        == fast_action_tool_name
                    )
                ):
                    latest_fast_result = (
                        tool_results[-1]
                    )

                    fast_verified = (
                        self._verification_engine
                        .verify(
                            latest_fast_result
                        )
                        .passed
                    )

                    fast_success = (
                        latest_fast_result.succeeded
                        and fast_verified
                    )

                    model_response = (
                        ModelResponse(
                            text=(
                                fast_action_route
                                .display_name
                                + (
                                    " a\u00e7\u0131ld\u0131."
                                    if fast_success
                                    else (
                                        " a\u00e7\u0131lamad\u0131."
                                    )
                                )
                            ),
                            model=(
                                "jarvis-fast-action"
                            ),
                            provider="core",
                            finish_reason="stop",
                            tool_calls=[],
                            usage={},
                            metadata={
                                "fast_action": (
                                    fast_action_tool_name
                                ),
                                "generation_skipped": True,
                                "verified": (
                                    fast_verified
                                ),
                            },
                        )
                    )

                    outcome = "completed"
                    break

                deterministic_final = None

                if (
                    deterministic_tool_name is not None
                    and tool_results
                    and (
                        tool_results[-1].tool_name
                        == deterministic_tool_name
                    )
                    and tool_results[-1].succeeded
                    and self._verification_engine.verify(
                        tool_results[-1]
                    ).passed
                ):
                    deterministic_final = (
                        provider.try_deterministic_finalization(
                            request,
                            active_context,
                        )
                    )

                if deterministic_final is not None:
                    model_response = deterministic_final
                    outcome = "completed"
                    break

                usage.model_iterations += 1

                provider_context = active_context

                try:
                    stream_chat = (
                        stream_callback is not None
                        and not tool_schemas
                    )

                    if stream_chat:
                        model_response = await self._await_provider(
                            self._collect_streamed_response(
                                request,
                                provider_context,
                                model=provider_model_override,
                                system_prompt=provider_system_prompt,
                                callback=stream_callback,
                                cancel_event=cancel_event,
                            ),
                            cancel_event=cancel_event,
                            timeout=remaining,
                        )
                    else:
                        model_response = await self._await_provider(
                            self._provider_gateway.generate(
                                request,
                                provider_context,
                                model=provider_model_override,
                                system_prompt=provider_system_prompt,
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
                    if (
                        provider_model_override is not None
                        and type(exc).__name__
                        == "ProviderRateLimitError"
                    ):
                        # The escalated action model is rate limited;
                        # the default model answers instead of the
                        # whole request failing.
                        self._record_diagnostic(
                            "request.model_fallback",
                            "Action model rate limited; retrying "
                            "on the default model.",
                            trace_id=str(request.request_id),
                        )
                        provider_model_override = None
                        continue
                    self._record_diagnostic(
                        "request.failed",
                        "Core provider request failed.",
                        level=DiagnosticLevel.ERROR,
                        trace_id=str(request.request_id),
                        attributes={
                            "error_type": type(exc).__name__
                        },
                    )
                    raise

                usage.model_tokens += self._model_token_usage(
                    model_response
                )

                if (
                    usage.model_tokens
                    > active_limits.max_model_tokens
                ):
                    outcome = "budget_exhausted"
                    budget_reason = "model_tokens"
                    break

                tool_calls = (
                    getattr(
                        model_response,
                        "tool_calls",
                        [],
                    )
                    or []
                )

                if not tool_calls:
                    blocked_plaintext_tool_call = (
                        self._interaction_policy
                        .plaintext_tool_name(
                            model_response.text,
                            self._tool_executor.list_names(),
                        )
                    )

                    if blocked_plaintext_tool_call is not None:
                        model_response.metadata[
                            "blocked_plaintext_tool_call"
                        ] = blocked_plaintext_tool_call

                        model_response.metadata[
                            "plaintext_tool_call_blocked"
                        ] = True

                        model_response.text = (
                            self._interaction_policy
                            .safe_fallback(
                                interaction_decision
                            )
                        )

                    outcome = "completed"
                    break

                assistant_tool_content = (
                    model_response.text
                    or None
                )

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
                content=assistant_tool_content,
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
                    sensitive_output = self._tool_output_is_sensitive(
                        processed_tool_calls[tool_call_id].tool_name
                    )
                    self._conversation_engine.add_tool_result(
                        active_context,
                        request_id=request.request_id,
                        tool_call_id=str(tool_call_id),
                        content=self._persistent_tool_content(
                            message,
                            sensitive=sensitive_output,
                        ),
                        metadata={
                            "duplicate": True,
                            "sensitive_output_not_retained": sensitive_output,
                        },
                        provider_content=(
                            str(message["content"])
                            if sensitive_output
                            else None
                        ),
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
                                self._execute_tool_with_interactive_approval(
                                    tool_name=tool_name,
                                    parameters=arguments,
                                    request=request,
                                    context=active_context,
                                    approval_callback=approval_callback,
                                    approval_timeout_seconds=remaining,
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
                sensitive_output = self._tool_output_is_sensitive(
                    result.tool_name
                )
                self._conversation_engine.add_tool_result(
                    active_context,
                    request_id=request.request_id,
                    tool_call_id=str(tool_call_id),
                    content=self._persistent_tool_content(
                        message,
                        sensitive=sensitive_output,
                    ),
                    metadata={
                        "tool_name": result.tool_name,
                        "status": result.status.value,
                        "verified": self._verification_engine.verify(
                            result
                        ).passed,
                        "sensitive_output_not_retained": sensitive_output,
                    },
                    provider_content=(
                        str(message["content"])
                        if sensitive_output
                        else None
                    ),
                )

                approval_status = (
                    result.data.get("approval_status")
                    if isinstance(result.data, dict)
                    else None
                )
                if approval_status in {"required", "denied"}:
                    outcome = f"approval_{approval_status}"
                    break

            if deterministic_tool_name is not None:
                exposed_tool_names.discard(
                    deterministic_tool_name
                )

            if outcome in {
                "budget_exhausted",
                "cancelled",
                "approval_required",
                "approval_denied",
            }:
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
        assurance = summarize_assurance(tool_results, outcome=outcome)

        if outcome == "approval_required":
            response_text = (
                "Bu işlem açık onay gerektiriyor; hiçbir değişiklik yapılmadı."
            )
            provider_name = provider.name
            model_name = getattr(model_response, "model", None)
        elif outcome == "approval_denied":
            response_text = (
                "Reddedilen işlem yapılmadı; daha önce ayrı ayrı onayladığın "
                "işlemler tamamlanmış olabilir."
                if successful_tools
                else "İşlem iptal edildi; bilgisayarında değişiklik yapılmadı."
            )
            provider_name = provider.name
            model_name = getattr(model_response, "model", None)
        elif model_response is None:
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

        raw_reasoning_level = (
            model_response.metadata.get("reasoning_level")
            if model_response is not None
            else None
        )
        reasoning_level = (
            raw_reasoning_level
            if raw_reasoning_level in {"minimal", "low", "medium", "high"}
            else None
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
                "reasoning_level": reasoning_level,
                "assurance_level": assurance.level.value,
                "uncertainty_summary": assurance.uncertainty,
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
                "deterministic_tool_route": (
                    deterministic_tool_name
                ),
                "deterministic_tool_route_reason": (
                    deterministic_tool_reason
                ),
                "fast_action_route": (
                    fast_action_tool_name
                ),
                "fast_action_route_reason": (
                    fast_action_route.reason
                    if fast_action_route
                    is not None
                    else None
                ),
                "tool_schema_count_before": (
                    tool_schema_count_before
                ),
                "tool_schema_count_after": (
                    tool_schema_count_after
                ),
                "tool_schema_selection_reason": (
                    tool_schema_selection_reason
                ),
                "tool_schema_selection_names": (
                    tool_schema_selection_names
                ),
                "interaction_kind": (
                    interaction_decision.kind
                ),
                "tools_suppressed": (
                    not interaction_decision.expose_tools
                ),
                "blocked_plaintext_tool_call": (
                    blocked_plaintext_tool_call
                ),
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
