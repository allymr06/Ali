from __future__ import annotations

from app.core.models import Context, Request, Response
from app.memory.manager import MemoryManager
from app.memory.policy import MemoryPolicy
from app.providers.registry import ProviderRegistry


class CoreEngine:
    """
    Central orchestration entry point for JARVIS.

    CoreEngine coordinates providers and memory but does not own
    their implementation details.
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

    async def handle(
        self,
        request: Request,
        context: Context | None = None,
    ) -> Response:
        """
        Process one request through the JARVIS orchestration pipeline.
        """
        active_context = context or Context()

        decision = self._memory_policy.evaluate(request)

        if decision.should_remember:
            self._memory_manager.remember(
                request.text,
                memory_type=decision.memory_type,
                importance=decision.importance,
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
            },
        )