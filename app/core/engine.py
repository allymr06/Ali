from __future__ import annotations

from app.core.models import Context, Request, Response
from app.memory.analyzer import MemoryAnalyzer
from app.memory.manager import MemoryManager
from app.memory.policy import MemoryPolicy
from app.providers.registry import ProviderRegistry


class CoreEngine:
    """
    Central orchestration entry point for JARVIS.

    CoreEngine coordinates providers and memory while keeping
    implementation details isolated behind explicit interfaces.
    """

    def __init__(
        self,
        provider_registry: ProviderRegistry,
        memory_manager: MemoryManager,
        memory_policy: MemoryPolicy | None = None,
    ) -> None:
        self._provider_registry = provider_registry
        self._memory_manager = memory_manager
        self._memory_policy = memory_policy or MemoryPolicy()
        self._memory_analyzer = MemoryAnalyzer()

    async def handle(
        self,
        request: Request,
        context: Context | None = None,
    ) -> Response:
        """
        Process one request through the JARVIS orchestration pipeline.
        """
        active_context = context if context is not None else Context()

        candidate = self._memory_analyzer.analyze(request)

        decision = self._memory_policy.evaluate(
            request,
            candidate,
        )

        if decision.should_remember and candidate is not None:
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

        model_response = await provider.generate(
            request,
            active_context,
        )

        return Response(
            text=model_response.text,
            request_id=request.request_id,
            metadata={
                "provider": model_response.provider,
                "model": model_response.model,
                "memory_decision": decision.should_remember,
                "memory_count": len(active_context.memories),
            },
        )
