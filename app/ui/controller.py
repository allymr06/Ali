from __future__ import annotations

import asyncio
from collections.abc import Callable, Coroutine
from concurrent.futures import Future
from dataclasses import dataclass, field
from threading import Thread
from typing import Any

from app.core.models import Context, Request, RequestSource
from app.ui.models import ChatMessage, RuntimeSnapshot, UIState


class AsyncRunner:
    """Own one background event loop for a responsive desktop UI."""

    def __init__(self) -> None:
        self._loop = asyncio.new_event_loop()
        self._thread = Thread(target=self._run, name="jarvis-ui-async", daemon=True)
        self._closed = False
        self._thread.start()

    def _run(self) -> None:
        asyncio.set_event_loop(self._loop)
        self._loop.run_forever()

    def submit(self, operation: Coroutine[Any, Any, Any]) -> Future[Any]:
        if self._closed:
            operation.close()
            raise RuntimeError("UI async runner is closed.")
        return asyncio.run_coroutine_threadsafe(operation, self._loop)

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._loop.call_soon_threadsafe(self._loop.stop)
        self._thread.join(timeout=2)
        self._loop.close()


@dataclass(slots=True)
class DesktopController:
    application: Any
    state: UIState = field(default_factory=UIState)
    context: Context = field(default_factory=Context)
    _runner: AsyncRunner | None = field(default=None, repr=False)

    def snapshot(self) -> RuntimeSnapshot:
        settings = self.application.settings
        memories = tuple(
            self.application.memory_service.list(active_only=True, limit=20)
        )
        tasks = tuple(self.application.task_service.list(limit=20))
        contracts = self.application.tool_executor.get_contract_objects(
            include_disabled=True
        )
        tools = tuple(
            {
                "name": contract.definition.name,
                "description": contract.definition.description,
                "risk": contract.definition.risk_level.value,
                "enabled": contract.enabled,
                "source": contract.source,
            }
            for contract in contracts
        )
        return RuntimeSnapshot(
            provider=settings.default_provider,
            model=(
                settings.gemini_model
                if settings.default_provider == "gemini"
                else settings.openai_model
                if settings.default_provider == "openai"
                else settings.default_model
            ) or settings.default_model,
            memory_count=len(memories),
            task_count=len(tasks),
            tool_count=len(tools),
            enabled_tools=sum(bool(item["enabled"]) for item in tools),
            voice_available=self.application.voice is not None,
            vision_available=self.application.vision is not None,
            research_available=self.application.research is not None,
            windows_available=self.application.windows is not None,
            diagnostic_event_count=len(self.application.diagnostics.ledger),
            diagnostic_integrity_valid=(
                self.application.diagnostics.ledger.verify_integrity()
            ),
            tasks=tasks,
            memories=memories,
            tools=tools,
        )

    def replace_application(self, application: Any) -> None:
        """Swap the live runtime after a validated configuration change."""
        previous = self.application
        self.application = application
        self.context = Context()
        close = getattr(previous, "close", None)
        if close is not None:
            close()

    async def submit_command(self, text: str) -> ChatMessage:
        normalized = text.strip()
        if not normalized:
            raise ValueError("Command cannot be empty.")
        self.state.busy = True
        self.state.status = "PROCESSING"
        self.state.messages.append(ChatMessage("user", normalized))
        try:
            response = await self.application.engine.handle(
                Request(normalized, source=RequestSource.TEXT), self.context
            )
            message = ChatMessage("assistant", response.text or "No response text.")
            self.state.messages.append(message)
            self.state.status = "LOCAL CORE READY"
            return message
        except Exception:
            self.state.status = "REQUEST FAILED"
            raise
        finally:
            self.state.busy = False

    async def run_voice(self) -> str:
        if self.application.voice is None:
            raise RuntimeError("Voice is not enabled in configuration.")
        result = await self.application.voice.run_once(self.context)
        return result.response_text or result.state.value

    async def run_research(
        self, query: str, *, max_sources: int = 5
    ) -> dict[str, object]:
        if self.application.research is None:
            raise RuntimeError("Web research is not enabled in configuration.")
        report = await asyncio.to_thread(
            self.application.research.research,
            query,
            max_sources=max_sources,
        )
        return report.to_dict()

    async def run_vision(self, purpose: str) -> str:
        service = self.application.vision
        if service is None:
            raise RuntimeError("Vision is not enabled in configuration.")
        request = service.request_consent(purpose)
        grant = service.approve_consent(request.request_id)
        result = await service.analyze(purpose, grant, context=self.context)
        if result.response is not None:
            return result.response.text
        return result.error_code or result.state.value

    def submit_background(
        self,
        operation: Coroutine[Any, Any, Any],
        callback: Callable[[Future[Any]], None],
    ) -> Future[Any]:
        if self._runner is None:
            self._runner = AsyncRunner()
        future = self._runner.submit(operation)
        future.add_done_callback(callback)
        return future

    def close(self) -> None:
        if self._runner is not None:
            self._runner.close()
            self._runner = None
        close = getattr(self.application, "close", None)
        if close is not None:
            close()
