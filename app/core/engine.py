from __future__ import annotations

from app.core.models import Context, Request, Response
from app.providers.registry import ProviderRegistry


class CoreEngine:
    """
    Central orchestration entry point for JARVIS.

    The engine delegates AI generation to the currently selected
    provider from ProviderRegistry.
    """

    def __init__(
        self,
        provider_registry: ProviderRegistry,
    ) -> None:
        self._provider_registry = provider_registry

    async def handle(
        self,
        request: Request,
        context: Context | None = None,
    ) -> Response:
        """
        Process a request through the configured AI provider.
        """
        active_context = context or Context()

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