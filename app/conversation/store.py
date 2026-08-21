from __future__ import annotations

from abc import ABC, abstractmethod
from copy import deepcopy
from threading import RLock
from uuid import UUID

from app.conversation.models import Conversation


class ConversationStore(ABC):
    @abstractmethod
    def save(self, conversation: Conversation) -> Conversation: ...

    @abstractmethod
    def get(self, conversation_id: UUID) -> Conversation: ...

    @abstractmethod
    def delete(self, conversation_id: UUID) -> Conversation: ...

    @abstractmethod
    def list(self) -> tuple[Conversation, ...]: ...


class InMemoryConversationStore(ConversationStore):
    """Thread-safe store with copy isolation for runtime conversation state."""

    def __init__(self) -> None:
        self._items: dict[UUID, Conversation] = {}
        self._lock = RLock()

    def save(self, conversation: Conversation) -> Conversation:
        with self._lock:
            self._items[conversation.conversation_id] = deepcopy(conversation)
            return deepcopy(conversation)

    def get(self, conversation_id: UUID) -> Conversation:
        with self._lock:
            try:
                return deepcopy(self._items[conversation_id])
            except KeyError as exc:
                raise KeyError(f"Unknown conversation: {conversation_id}") from exc

    def delete(self, conversation_id: UUID) -> Conversation:
        with self._lock:
            try:
                return self._items.pop(conversation_id)
            except KeyError as exc:
                raise KeyError(f"Unknown conversation: {conversation_id}") from exc

    def list(self) -> tuple[Conversation, ...]:
        with self._lock:
            return tuple(deepcopy(item) for item in self._items.values())

    def contains(self, conversation_id: UUID) -> bool:
        with self._lock:
            return conversation_id in self._items
