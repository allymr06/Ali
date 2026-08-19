from __future__ import annotations

from app.core.models import Context, Request, Response
from app.memory.base import MemoryStore
from app.providers.registry import ProviderRegistry


class CoreEngine:
    """
    Central orchestration entry point for JARVIS.

    The engine coordinates AI providers and memory through abstractions.
    """

    def __init__(
        self,
        provider_registry: ProviderRegistry,
        memory_store: MemoryStore | None = None,
    ) -> None:
        self._provider_registry = provider_registry
        self._memory_store = memory_store

    async def handle(
        self,
        request: Request,
        context: Context | None = None,
    ) -> Response:
        """
        Process a request through memory and the configured AI provider.
        """
        active_context = context or Context()

        if self._memory_store is not None:
            memories = self._memory_store.search(
                request.text,
                limit=5,
            )
            active_context.values["relevant_memories"] = memories

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
                "finish_reason": model_response.finish_reason,
            },
        )