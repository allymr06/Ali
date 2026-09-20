from __future__ import annotations

import asyncio
from collections.abc import Callable, Coroutine
from concurrent.futures import Future, TimeoutError as FutureTimeoutError
from dataclasses import dataclass, field
from threading import Thread
from typing import Any
from uuid import UUID

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
    approval_callback: InteractiveApprovalCallback | None = field(
        default=None,
        repr=False,
    )
    paused: bool = False
    # Surfaces that keep a conversation open while something else closes
    # it. The phone holds a thread on screen with a live composer, and
    # only the surface that archived it knows - see _announce_status.
    conversation_status_watchers: list[Callable[[str, str], None]] = field(
        default_factory=list,
        repr=False,
    )
    _runner: AsyncRunner | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        self.restore_latest_conversation()

    def set_paused(self, paused: bool) -> None:
        """Gate new user-initiated work; a UI pause, not a security control."""
        self.paused = bool(paused)
        self.state.status = "PAUSED" if self.paused else "LOCAL CORE READY"

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

        self._load_conversation(conversation)

        return True

    @staticmethod
    def _visible_turns(conversation: Any) -> list[ChatMessage]:
        return [
            ChatMessage(
                turn.role.value,
                turn.content,
                metadata=dict(turn.metadata),
            )
            for turn in conversation.turns
            if turn.role in {MessageRole.USER, MessageRole.ASSISTANT}
            and turn.content
            and turn.content.strip()
        ]

    def _load_conversation(self, conversation: Any) -> None:
        self.context = Context(
            conversation_id=conversation.conversation_id
        )
        self.state.messages = self._visible_turns(conversation)
        self.state.voice_messages = []

    @staticmethod
    def conversation_title(conversation: Any, limit: int = 80) -> str:
        """The first thing the user said, or a neutral placeholder."""
        for turn in conversation.turns:
            if (
                turn.role is MessageRole.USER
                and turn.content
                and turn.content.strip()
            ):
                text = " ".join(turn.content.split())
                return text if len(text) <= limit else text[: limit - 1] + "…"
        return "Yeni konuşma"

    def list_conversations(self, limit: int = 50) -> list[dict[str, object]]:
        """Stored conversations, newest first, with display metadata."""
        conversations = sorted(
            self.application.conversation_engine.list(),
            key=lambda item: (item.updated_at, item.created_at),
            reverse=True,
        )
        return [
            {
                "conversation_id": str(conversation.conversation_id),
                "title": self.conversation_title(conversation),
                "status": conversation.status.value,
                "turn_count": len(self._visible_turns(conversation)),
                "created_at": conversation.created_at.isoformat(),
                "updated_at": conversation.updated_at.isoformat(),
                "active": (
                    conversation.conversation_id == self.context.conversation_id
                ),
            }
            for conversation in conversations[: max(1, limit)]
        ]

    def search_conversations(self, query: str, limit: int = 12) -> list[dict[str, object]]:
        """Stored conversations whose visible turns mention the query.

        Matching is casefolded in Python so Turkish dotted and dotless I
        behave (SQL LIKE only folds ASCII). Each hit carries an excerpt
        around the first match; newest conversations come first.
        """
        def fold(text: str) -> str:
            # Turkish-aware and length-preserving: casefold() turns the
            # dotted capital i into "i" plus a combining dot, which both
            # misses matches and skews excerpt offsets. Mapping the two
            # Turkish capitals first and then lower() keeps offsets exact.
            return text.replace("İ", "i").replace("I", "ı").lower()

        needle = fold(" ".join(str(query or "").split()))
        if not needle:
            return []
        results: list[dict[str, object]] = []
        conversations = sorted(
            self.application.conversation_engine.list(),
            key=lambda item: (item.updated_at, item.created_at),
            reverse=True,
        )
        for conversation in conversations:
            turns = self._visible_turns(conversation)
            matches = 0
            excerpt = ""
            excerpt_role = ""
            for message in turns:
                text = " ".join(message.text.split())
                folded = fold(text)
                if needle not in folded:
                    continue
                matches += 1
                if not excerpt:
                    start = folded.index(needle)
                    begin = max(0, start - 40)
                    end = min(len(text), start + len(needle) + 60)
                    prefix = "…" if begin else ""
                    suffix = "…" if end < len(text) else ""
                    excerpt = f"{prefix}{text[begin:end]}{suffix}"
                    excerpt_role = message.role
            title = self.conversation_title(conversation)
            if not matches and needle not in fold(title):
                continue
            results.append(
                {
                    "conversation_id": str(conversation.conversation_id),
                    "title": title,
                    "status": conversation.status.value,
                    "matches": matches,
                    "excerpt": excerpt,
                    "excerpt_role": excerpt_role,
                    "turn_count": len(turns),
                    "updated_at": conversation.updated_at.isoformat(),
                    "active": (
                        conversation.conversation_id == self.context.conversation_id
                    ),
                }
            )
            if len(results) >= max(1, limit):
                break
        return results

    def conversation_snapshot(
        self,
        conversation_id: str,
    ) -> tuple[str, str, str, list[ChatMessage]]:
        """(title, created date, status, messages) from a single store read.

        A caller that needs more than one of these - the phone wants the
        title, the status and the transcript in one answer - must not pay
        for the conversation twice: behind the engine is a database file
        whose read rebuilds every turn.
        """
        conversation = self.application.conversation_engine.get(UUID(str(conversation_id)))
        return (
            self.conversation_title(conversation),
            conversation.created_at.astimezone().strftime("%d.%m.%Y %H:%M"),
            conversation.status.value,
            self._visible_turns(conversation),
        )

    def conversation_export(self, conversation_id: str) -> tuple[str, str, list[ChatMessage]]:
        """(title, created date, visible messages) of a stored conversation.

        Reading for export never activates or switches anything: the student
        can print an old conversation while another one stays active.
        """
        title, created, _status, messages = self.conversation_snapshot(conversation_id)
        return (title, created, messages)

    def _announce_status(self, conversation_id: UUID, status: ConversationStatus) -> None:
        """Tell the other surfaces what may now be done with a thread.

        A surface can be showing this conversation with a composer of its
        own - the phone is, over the network - and nothing else in the
        process can see that it just stopped taking messages.
        """
        for watcher in list(self.conversation_status_watchers):
            try:
                watcher(str(conversation_id), status.value)
            except Exception:
                # A listening surface must not undo the archive itself.
                pass

    def open_conversation(self, conversation_id: str) -> list[ChatMessage]:
        """Switch the shared context to a stored conversation."""
        engine = self.application.conversation_engine
        identifier = UUID(str(conversation_id))
        conversation = engine.get(identifier)
        if conversation.status is not ConversationStatus.ACTIVE:
            conversation = engine.activate(identifier)
            self._announce_status(identifier, conversation.status)
        self._load_conversation(conversation)
        return list(self.state.messages)

    def start_new_conversation(self) -> str:
        """Begin an empty conversation; it is stored on the first turn."""
        self.context = Context()
        self.state.messages = []
        self.state.voice_messages = []
        return str(self.context.conversation_id)

    def archive_conversation(self, conversation_id: str) -> bool:
        """Archive a conversation; returns whether it was the open one."""
        identifier = UUID(str(conversation_id))
        archived = self.application.conversation_engine.archive(identifier)
        self._announce_status(identifier, archived.status)
        if identifier == self.context.conversation_id:
            self.start_new_conversation()
            return True
        return False

    def unarchive_conversation(self, conversation_id: str) -> None:
        """Bring an archived conversation back to the active list."""
        identifier = UUID(str(conversation_id))
        activated = self.application.conversation_engine.activate(identifier)
        self._announce_status(identifier, activated.status)

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
        context: Context | None = None,
        source: RequestSource = RequestSource.TEXT,
    ) -> ChatMessage:
        """Run one turn through the core.

        With an explicit ``context`` (a mobile session, for instance) the
        turn uses that conversation and leaves the desktop's own state -
        busy flag, status line, message list - untouched; the shared
        conversation store still records both turns.
        """
        normalized = text.strip()
        if not normalized:
            raise ValueError("Command cannot be empty.")
        if context is not None:
            manage_state = False
        active_context = context if context is not None else self.context
        if manage_state:
            self.state.busy = True
            self.state.status = "PROCESSING"
            self.state.messages.append(ChatMessage("user", normalized))
        try:
            request = Request(
                normalized,
                source=source,
            )

            approval_options = (
                {"approval_callback": self.approval_callback}
                if self.approval_callback is not None
                else {}
            )
            if stream_callback is None:
                response = await self.application.engine.handle(
                    request,
                    active_context,
                    **approval_options,
                )
            else:
                response = await self.application.engine.handle(
                    request,
                    active_context,
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
                        "elapsed_seconds",
                        "tool_calls",
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
            and metadata.get("speech_error") == "provider"
            and metadata.get("speech_fallback") == "windows-local"
        ):
            # Only worth mentioning when the cloud voice actually
            # failed. The local voice simply winning the speed race is
            # normal operation, not something to apologize for.
            if metadata.get("speech_error_reason") == "quota":
                # The free-tier speech model runs out after a handful of
                # calls a day; naming that spares the user hunting a bug
                # that is really a plan limit.
                return (
                    "Bugünkü ücretsiz bulut ses kotası doldu; yanıtı "
                    "yerel sesle okudum. Sürekli net ve tek ses için "
                    "Gemini planını yükseltmen gerekiyor."
                )
            return (
                "Bulut sesi şu an kullanılamıyor; yerel Türkçe sesle "
                "yanıtladım."
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
                    # Voice and text share one conversation: what was
                    # said aloud must exist in the chat history too.
                    self.state.messages.append(message)
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
                    self.state.messages.append(message)
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
                # One shared conversation for voice and text, so a
                # voice exchange is remembered when typing later.
                "context": self.context,
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
            results = (await voice.run_once(self.context, **options),)
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
        self,
        query: str,
        *,
        max_sources: int = 5,
        sources: tuple[str, ...] = (),
        site: str | None = None,
    ) -> dict[str, object]:
        if self.application.research is None:
            raise RuntimeError("Web research is not enabled in configuration.")
        # Only a chosen source list or site travels: a research backend
        # that knows nothing of them is asked the way it always was.
        options: dict[str, object] = {"max_sources": max_sources}
        if sources:
            options["sources"] = tuple(sources)
        if site:
            options["site"] = site
        report = await asyncio.to_thread(
            self.application.research.research,
            query,
            **options,
        )
        return report.to_dict()

    async def run_vision(self, purpose: str) -> str:
        service = self.application.vision
        if service is None:
            raise RuntimeError("Vision is not enabled in configuration.")
        request = service.request_consent(purpose)
        grant = service.approve_consent(request.request_id)
        result = await service.analyze(purpose, grant, context=self.context)
        # VisionSessionResult carries the model's words as response_text;
        # the first live capture found this reading a field that never
        # existed, hidden by a test double that had invented it.
        if result.response_text is not None:
            return result.response_text
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
