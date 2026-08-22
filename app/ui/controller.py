from __future__ import annotations

import asyncio
from collections.abc import Callable, Coroutine
from concurrent.futures import Future, TimeoutError as FutureTimeoutError
from dataclasses import dataclass, field
from threading import Thread
from typing import Any

from app.conversation.models import ConversationStatus, MessageRole
from app.core.models import Context, Request, RequestSource
from app.security.interactive import InteractiveApprovalCallback
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

        async def cancel_pending() -> None:
            current = asyncio.current_task()
            pending = [
                task
                for task in asyncio.all_tasks(self._loop)
                if task is not current and not task.done()
            ]
            for task in pending:
                task.cancel()
            if pending:
                await asyncio.gather(*pending, return_exceptions=True)
            await self._loop.shutdown_asyncgens()

        try:
            shutdown = asyncio.run_coroutine_threadsafe(
                cancel_pending(),
                self._loop,
            )
            shutdown.result(timeout=2)
        except (FutureTimeoutError, RuntimeError):
            pass
        finally:
            if self._loop.is_running():
                self._loop.call_soon_threadsafe(self._loop.stop)
            self._thread.join(timeout=2)
            if not self._thread.is_alive() and not self._loop.is_closed():
                self._loop.close()


@dataclass(slots=True)
class DesktopController:
    application: Any
    state: UIState = field(default_factory=UIState)
    context: Context = field(default_factory=Context)
    voice_context: Context = field(default_factory=Context)
    approval_callback: InteractiveApprovalCallback | None = field(
        default=None,
        repr=False,
    )
    _runner: AsyncRunner | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        self.restore_latest_conversation()

    def restore_latest_conversation(
        self,
    ) -> bool:
        conversations = tuple(
            conversation
            for conversation
            in self.application.conversation_engine.list()
            if conversation.status
            is ConversationStatus.ACTIVE
        )

        if not conversations:
            return False

        conversation = max(
            conversations,
            key=lambda item: (
                item.updated_at,
                item.created_at,
            ),
        )

        self.context = Context(
            conversation_id=(
                conversation.conversation_id
            )
        )

        self.state.messages = [
            ChatMessage(
                turn.role.value,
                turn.content,
                metadata=dict(turn.metadata),
            )
            for turn
            in conversation.turns
            if turn.role
            in {
                MessageRole.USER,
                MessageRole.ASSISTANT,
            }
            and turn.content
            and turn.content.strip()
        ]

        return True

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

    def replace_application(
        self,
        application: Any,
    ) -> None:
        """
        Swap the live runtime without discarding
        the active conversation.
        """
        previous = self.application

        previous_conversation_id = (
            self.context.conversation_id
        )
        previous_voice_conversation_id = (
            self.voice_context.conversation_id
        )

        self.application = application

        try:
            self.application.conversation_engine.get(
                previous_conversation_id
            )
        except KeyError:
            self.context = Context()
            self.restore_latest_conversation()
        else:
            self.context = Context(
                conversation_id=(
                    previous_conversation_id
                )
            )

        try:
            self.application.conversation_engine.get(
                previous_voice_conversation_id
            )
        except KeyError:
            self.voice_context = Context()
        else:
            self.voice_context = Context(
                conversation_id=previous_voice_conversation_id
            )

        close = getattr(
            previous,
            "close",
            None,
        )

        if close is not None:
            close()

    async def submit_command(
        self,
        text: str,
        *,
        stream_callback: Callable[[str], None] | None = None,
        manage_state: bool = True,
    ) -> ChatMessage:
        normalized = text.strip()
        if not normalized:
            raise ValueError("Command cannot be empty.")
        if manage_state:
            self.state.busy = True
            self.state.status = "PROCESSING"
            self.state.messages.append(ChatMessage("user", normalized))
        try:
            request = Request(
                normalized,
                source=RequestSource.TEXT,
            )

            approval_options = (
                {"approval_callback": self.approval_callback}
                if self.approval_callback is not None
                else {}
            )
            if stream_callback is None:
                response = await self.application.engine.handle(
                    request,
                    self.context,
                    **approval_options,
                )
            else:
                response = await self.application.engine.handle(
                    request,
                    self.context,
                    stream_callback=stream_callback,
                    **approval_options,
                )
            message = ChatMessage(
                "assistant",
                response.text or "No response text.",
                metadata={
                    key: response.metadata[key]
                    for key in (
                        "reasoning_level",
                        "assurance_level",
                        "uncertainty_summary",
                    )
                    if response.metadata.get(key) is not None
                },
            )
            if manage_state:
                self.state.messages.append(message)
                self.state.status = "LOCAL CORE READY"
            return message
        except Exception as exc:
            if manage_state:
                self.state.status = "RECOVERING"

            message = ChatMessage(
                "system",
                (
                    "İstek tamamlanamadı. "
                    f"({type(exc).__name__}). "
                    "JARVIS oturumu korundu; "
                    "tekrar deneyebilirsin."
                ),
            )

            if manage_state:
                self.state.messages.append(
                    message
                )
                self.state.status = (
                    "LOCAL CORE READY"
                )

            return message

        finally:
            if manage_state:
                self.state.busy = False

    _VOICE_ERROR_NOTICES = {
        "synthesis": (
            "Yanıt sese çevrilemedi (sağlayıcı kotası dolmuş "
            "olabilir); yanıtı metin olarak ekledim."
        ),
        "provider": (
            "Konuşman çözümlenemedi. Sağlayıcı hatası olabilir; "
            "tekrar dener misin?"
        ),
        "timeout": "Ses işlemi zaman aşımına uğradı.",
        "device": (
            "Mikrofon veya hoparlöre erişilemedi. Cihaz "
            "bağlantısını kontrol eder misin?"
        ),
        "configuration": (
            "Ses yapılandırması eksik. Ayarlar ekranından API "
            "anahtarını kontrol eder misin?"
        ),
    }

    @classmethod
    def _voice_turn_notice(cls, result: object) -> str | None:
        metadata = getattr(result, "metadata", None) or {}
        if (
            isinstance(metadata, dict)
            and metadata.get("speech_fallback") == "windows-sapi"
        ):
            return (
                "Bulut sesi şu an kullanılamıyor; yedek Windows "
                "sesiyle yanıtladım."
            )
        error_code = getattr(result, "error_code", None)
        if isinstance(error_code, str):
            return cls._VOICE_ERROR_NOTICES.get(error_code)
        if (
            isinstance(metadata, dict)
            and metadata.get("ignored_reason") == "no_speech"
        ):
            return (
                "Ses algılanmadığı için sesli modu kapattım. "
                "Mikrofon düğmesiyle yeniden başlatabilirsin."
            )
        return None

    async def run_voice(
        self,
        *,
        message_callback: Callable[[ChatMessage], None] | None = None,
        manage_state: bool = True,
    ) -> str:
        if self.application.voice is None:
            raise RuntimeError(
                "Voice is not enabled in configuration."
            )

        voice = self.application.voice
        last_response: str | None = None

        def record_result(result: object) -> None:
            nonlocal last_response
            transcript = getattr(
                result,
                "transcript",
                None,
            )

            if (
                isinstance(transcript, str)
                and transcript.strip()
            ):
                message = ChatMessage(
                    "user",
                    transcript.strip(),
                )
                if manage_state:
                    self.state.voice_messages.append(message)
                if message_callback is not None:
                    message_callback(message)

            response_text = getattr(
                result,
                "response_text",
                None,
            )

            if (
                isinstance(response_text, str)
                and response_text.strip()
            ):
                last_response = response_text.strip()
                message = ChatMessage(
                    "assistant",
                    last_response,
                )
                if manage_state:
                    self.state.voice_messages.append(message)
                if message_callback is not None:
                    message_callback(message)

            notice = self._voice_turn_notice(result)
            if notice is not None:
                message = ChatMessage("system", notice)
                if manage_state:
                    self.state.voice_messages.append(message)
                if message_callback is not None:
                    message_callback(message)

        run_continuous = getattr(
            voice,
            "run_continuous",
            None,
        )

        if callable(run_continuous):
            options = {
                "max_turns": 100,
                "context": self.voice_context,
                "max_consecutive_failures": 2,
                "result_callback": record_result,
            }
            if self.approval_callback is not None:
                options["approval_callback"] = self.approval_callback
            results = await run_continuous(**options)
        else:
            # Compatibility path for older voice adapters
            # and lightweight test doubles.
            options = (
                {"approval_callback": self.approval_callback}
                if self.approval_callback is not None
                else {}
            )
            results = (await voice.run_once(self.voice_context, **options),)
            record_result(results[0])

        if last_response is not None:
            return last_response

        if results:
            state = getattr(
                results[-1],
                "state",
                None,
            )

            state_value = getattr(
                state,
                "value",
                None,
            )

            if (
                isinstance(state_value, str)
                and state_value
            ):
                return state_value

            if state is not None:
                return str(state)

        return "idle"

    async def interrupt_voice(self) -> bool:
        voice = self.application.voice
        if voice is None:
            return False
        interrupt = getattr(voice, "interrupt_active", None)
        if not callable(interrupt):
            return False
        return bool(await interrupt())

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
