from __future__ import annotations

from collections.abc import Callable
from typing import Any

from app.core.models import Context, Request, Response


class CoreEngine:
    """
    Central orchestration entry point for JARVIS.

    The engine intentionally knows nothing about a specific AI provider,
    UI framework, voice implementation, or Windows automation mechanism.
    Those systems will be connected through explicit interfaces later.
    """

    def __init__(
        self,
        responder: Callable[[Request, Context], str] | None = None,
    ) -> None:
        self._responder = responder or self._default_responder

    def handle(
        self,
        request: Request,
        context: Context | None = None,
    ) -> Response:
        """
        Process one normalized request and return a response.

        The current implementation establishes the orchestration boundary.
        AI reasoning, tools, permissions, memory, and task execution will be
        attached through subsequent milestones.
        """
        active_context = context or Context()

        response_text = self._responder(
            request,
            active_context,
        )

        return Response(
            text=response_text,
            request_id=request.request_id,
        )

    @staticmethod
    def _default_responder(
        request: Request,
        context: Context,
    ) -> str:
        """
        Safe fallback used before an AI provider is connected.
        """
        return (
            "JARVIS Core hazır. "
            f"İsteğiniz alındı: {request.text}"
        )