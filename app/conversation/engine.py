from __future__ import annotations

from typing import Any
from threading import RLock
from uuid import UUID, uuid4

from app.conversation.models import (
    Conversation,
    ConversationStatus,
    ConversationTurn,
    MessageRole,
)
from app.conversation.store import ConversationStore, InMemoryConversationStore
from app.core.models import Context, Request, Response
from app.core.time import utc_now


class ConversationEngine:
    """Own conversation lifecycle and construct compact provider context."""

    def __init__(
        self,
        store: ConversationStore | None = None,
        *,
        max_context_messages: int = 50,
        max_context_characters: int = 50_000,
        summary_max_characters: int = 4_000,
        system_prompt: str | None = None,
    ) -> None:
        if max_context_messages < 2:
            raise ValueError("max_context_messages must be at least 2.")
        if max_context_characters < 100:
            raise ValueError("max_context_characters must be at least 100.")
        if summary_max_characters < 100:
            raise ValueError("summary_max_characters must be at least 100.")
        self._store = store or InMemoryConversationStore()
        self._max_messages = max_context_messages
        self._max_characters = max_context_characters
        self._summary_max_characters = summary_max_characters
        self._system_prompt = system_prompt.strip() if system_prompt else None
        self._lock = RLock()
        # Sensitive tool output is never written to the store; the stored turn
        # carries a placeholder instead. The provider still needs the real text
        # for the turn that produced it, or the model is told a tool ran without
        # being told what it returned and answers from a hole in the transcript.
        # These overrides therefore live in memory only, keyed by the turn that
        # produced them, and are dropped as soon as that turn ends.
        self._provider_overrides: dict[UUID, tuple[UUID, dict[str, str]]] = {}

    @property
    def store(self) -> ConversationStore:
        return self._store

    def create(self, conversation_id: UUID | None = None) -> Conversation:
        selected_id = conversation_id or uuid4()
        try:
            self._store.get(selected_id)
        except KeyError:
            return self._store.save(Conversation(conversation_id=selected_id))
        raise ValueError(f"Conversation already exists: {selected_id}")

    def get(self, conversation_id: UUID) -> Conversation:
        return self._store.get(conversation_id)

    def ensure(self, conversation_id: UUID) -> Conversation:
        try:
            return self._store.get(conversation_id)
        except KeyError:
            return self.create(conversation_id)

    def archive(self, conversation_id: UUID) -> Conversation:
        with self._lock:
            # An archived conversation takes no further requests, so the turn
            # that owns any held tool output can never finish and drop it.
            self._forget_provider_overrides(conversation_id)
            conversation = self.get(conversation_id)
            conversation.status = ConversationStatus.ARCHIVED
            conversation.updated_at = utc_now()
            return self._store.save(conversation)

    def activate(self, conversation_id: UUID) -> Conversation:
        conversation = self.get(conversation_id)
        conversation.status = ConversationStatus.ACTIVE
        conversation.updated_at = utc_now()
        return self._store.save(conversation)

    def list(self) -> tuple[Conversation, ...]:
        return self._store.list()

    def delete(self, conversation_id: UUID) -> Conversation:
        with self._lock:
            # Deleting a conversation is the gesture that means forget it, and
            # this process outlives the deletion, so the sensitive tool output
            # kept in memory for its last turn goes with it.
            self._forget_provider_overrides(conversation_id)
            return self._store.delete(conversation_id)

    @staticmethod
    def _message_characters(message: dict[str, Any]) -> int:
        return len(str(message.get("content") or "")) + len(
            str(message.get("tool_calls") or "")
        )

    def _summarize(
        self,
        turns: list[ConversationTurn],
        limit: int,
    ) -> str:
        lines = []
        for turn in turns:
            content = (turn.content or "").strip()
            if not content and turn.tool_calls:
                names = [
                    str(item.get("function", {}).get("name", "tool"))
                    for item in turn.tool_calls
                ]
                content = f"requested tools: {', '.join(names)}"
            if content:
                lines.append(f"{turn.role.value}: {content}")
        summary = "\n".join(lines)
        if len(summary) > limit:
            summary = summary[-limit:]
        return summary

    def _context_messages(
        self,
        conversation: Conversation,
        overrides: dict[str, str] | None = None,
        owning_request_id: UUID | None = None,
    ) -> list[dict[str, Any]]:
        groups: list[list[ConversationTurn]] = []
        for turn in conversation.turns:
            if groups and groups[-1][0].request_id == turn.request_id:
                groups[-1].append(turn)
            else:
                groups.append([turn])
        selected_groups: list[list[ConversationTurn]] = []
        characters = 0
        message_count = 0
        for group in reversed(groups):
            size = sum(
                self._message_characters(turn.to_message())
                for turn in group
            )
            if selected_groups and (
                message_count + len(group) > self._max_messages
                or characters + size > self._max_characters
            ):
                break
            selected_groups.append(group)
            characters += size
            message_count += len(group)
        selected_groups.reverse()
        selected = [turn for group in selected_groups for turn in group]
        omitted_count = len(conversation.turns) - len(selected)
        messages: list[dict[str, Any]] = []
        if self._system_prompt:
            messages.append({"role": "system", "content": self._system_prompt})
        if omitted_count:
            omitted = conversation.turns[:omitted_count]
            summary_prefix = "Earlier conversation summary:\n"
            system_characters = len(self._system_prompt or "")
            summary_limit = min(
                self._summary_max_characters,
                max(
                    0,
                    self._max_characters
                    - characters
                    - system_characters
                    - len(summary_prefix),
                ),
            )
            conversation.summary = (
                self._summarize(omitted, summary_limit)
                if summary_limit > 0
                else None
            )
            conversation.summary_turn_count = omitted_count
            conversation.metadata["summary_updated_at"] = utc_now().isoformat()
            if conversation.summary:
                messages.append(
                    {
                        "role": "system",
                        "content": summary_prefix + conversation.summary,
                    }
                )
        for turn in selected:
            message = turn.to_message()
            # A provider may reuse a tool call id across turns, so an override
            # is only ever applied to the turn that produced it; matching on
            # the id alone would rewrite an older turn's result with the
            # current one and hand the model a falsified transcript.
            if overrides and turn.request_id == owning_request_id:
                tool_call_id = message.get("tool_call_id")
                replacement = (
                    overrides.get(tool_call_id)
                    if isinstance(tool_call_id, str)
                    else None
                )
                if replacement is not None:
                    message["content"] = replacement
            messages.append(message)
        return messages

    def _provider_overrides_for(
        self,
        conversation_id: UUID,
        request_id: UUID | None,
    ) -> dict[str, str]:
        entry = self._provider_overrides.get(conversation_id)
        if entry is None:
            return {}
        owning_request_id, overrides = entry
        if request_id is None or owning_request_id != request_id:
            del self._provider_overrides[conversation_id]
            return {}
        return overrides

    def _forget_provider_overrides(self, conversation_id: UUID) -> None:
        self._provider_overrides.pop(conversation_id, None)

    def _sync_context(
        self,
        conversation: Conversation,
        context: Context,
        request_id: UUID | None = None,
    ) -> None:
        overrides = self._provider_overrides_for(
            conversation.conversation_id,
            request_id,
        )
        messages = self._context_messages(conversation, overrides, request_id)
        context.values["messages"] = messages
        context.values["conversation_id"] = str(conversation.conversation_id)
        if request_id is not None:
            context.values["conversation_request_id"] = str(request_id)
        self._store.save(conversation)

    def prepare_request(self, request: Request, context: Context) -> Conversation:
        with self._lock:
            conversation = self.ensure(context.conversation_id)
            if conversation.status is not ConversationStatus.ACTIVE:
                raise ValueError("Conversation is archived.")
            existing = next(
                (
                    turn
                    for turn in conversation.turns
                    if turn.request_id == request.request_id
                    and turn.role is MessageRole.USER
                ),
                None,
            )
            if existing is None:
                conversation.add_turn(
                    ConversationTurn(
                        conversation_id=conversation.conversation_id,
                        role=MessageRole.USER,
                        content=request.text,
                        request_id=request.request_id,
                        metadata={"source": request.source.value},
                    )
                )
            self._sync_context(conversation, context, request.request_id)
            return conversation

    def add_assistant_tool_calls(
        self,
        context: Context,
        *,
        request_id: UUID,
        content: str | None,
        tool_calls: list[dict[str, Any]],
    ) -> ConversationTurn:
        with self._lock:
            conversation = self.ensure(context.conversation_id)
            turn = ConversationTurn(
                conversation_id=conversation.conversation_id,
                role=MessageRole.ASSISTANT,
                content=content,
                request_id=request_id,
                tool_calls=list(tool_calls),
            )
            conversation.add_turn(turn)
            self._sync_context(conversation, context, request_id)
            return turn

    def add_tool_result(
        self,
        context: Context,
        *,
        request_id: UUID,
        tool_call_id: str,
        content: str,
        metadata: dict[str, Any] | None = None,
        provider_content: str | None = None,
    ) -> ConversationTurn:
        with self._lock:
            conversation = self.ensure(context.conversation_id)
            turn = ConversationTurn(
                conversation_id=conversation.conversation_id,
                role=MessageRole.TOOL,
                content=content,
                request_id=request_id,
                tool_call_id=tool_call_id,
                metadata=dict(metadata or {}),
            )
            conversation.add_turn(turn)
            if provider_content is not None:
                entry = self._provider_overrides.get(
                    conversation.conversation_id
                )
                if entry is None or entry[0] != request_id:
                    entry = (request_id, {})
                    self._provider_overrides[
                        conversation.conversation_id
                    ] = entry
                entry[1][str(tool_call_id)] = provider_content
            self._sync_context(conversation, context, request_id)
            return turn

    def complete_response(
        self,
        request: Request,
        response: Response,
        context: Context,
    ) -> ConversationTurn | None:
        with self._lock:
            # The turn is over, so sensitive tool output has served the provider
            # call it was needed for and stops being replayed into the context.
            self._forget_provider_overrides(context.conversation_id)
            if not response.text:
                return None
            conversation = self.ensure(context.conversation_id)
            existing = next(
                (
                    turn
                    for turn in conversation.turns
                    if turn.response_id == response.response_id
                ),
                None,
            )
            if existing is not None:
                return existing
            turn = ConversationTurn(
                conversation_id=conversation.conversation_id,
                role=MessageRole.ASSISTANT,
                content=response.text,
                request_id=request.request_id,
                response_id=response.response_id,
                metadata={
                    "outcome": response.metadata.get("outcome"),
                    "provider": response.metadata.get("provider"),
                    "model": response.metadata.get("model"),
                    "reasoning_level": response.metadata.get("reasoning_level"),
                    "assurance_level": response.metadata.get("assurance_level"),
                    "uncertainty_summary": response.metadata.get(
                        "uncertainty_summary"
                    ),
                    # Kept so a reopened conversation shows how long each
                    # answer took and whether tools ran, like a live one.
                    **{
                        key: response.metadata[key]
                        for key in ("elapsed_seconds", "tool_calls")
                        if response.metadata.get(key) is not None
                    },
                },
            )
            conversation.add_turn(turn)
            self._sync_context(conversation, context, request.request_id)
            return turn
