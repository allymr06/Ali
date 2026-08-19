from __future__ import annotations

from app.core.models import Context, Request
from app.providers.base import AIProvider, ModelCapabilities, ModelResponse


class MockProvider(AIProvider):
    """Deterministic provider used for development and testing."""

    @property
    def name(self) -> str:
        return "mock"

    @property
    def capabilities(self) -> ModelCapabilities:
        return ModelCapabilities(
            text=True,
            streaming=False,
            structured_output=False,
            tool_calling=False,
            vision=False,
        )

    async def generate(
        self,
        request: Request,
        context: Context,
        *,
        model: str | None = None,
        system_prompt: str | None = None,
        tools: list[dict[str, object]] | None = None,
    ) -> ModelResponse:
        selected_model = model or "mock-model"

        return ModelResponse(
            text=f"Mock yanıtı: {request.text}",
            model=selected_model,
            provider=self.name,
            finish_reason="stop",
        )