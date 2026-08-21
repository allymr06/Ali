from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any
from uuid import UUID, uuid4

from app.core.time import utc_now


class ConversationStatus(str, Enum):
    ACTIVE = "active"
    ARCHIVED = "archived"


class MessageRole(str, Enum):
    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"


@dataclass(slots=True)
class ConversationTurn:
    conversation_id: UUID
    role: MessageRole
    content: str | None
    turn_id: UUID = field(default_factory=uuid4)
    request_id: UUID | None = None
    response_id: UUID | None = None
    tool_call_id: str | None = None
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: datetime = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        if isinstance(self.role, str):
            self.role = MessageRole(self.role)
        if self.content is not None and not isinstance(self.content, str):
            raise TypeError("Turn content must be a string or None.")
        if self.role in {MessageRole.USER, MessageRole.SYSTEM} and not (
            self.content and self.content.strip()
        ):
            raise ValueError(f"{self.role.value} turn content cannot be empty.")
        if self.role is MessageRole.ASSISTANT and not self.content and not self.tool_calls:
            raise ValueError("Assistant turn requires content or tool calls.")
        if self.role is MessageRole.TOOL and not self.tool_call_id:
            raise ValueError("Tool turn requires tool_call_id.")
        if not isinstance(self.tool_calls, list) or not all(
            isinstance(item, dict) for item in self.tool_calls
        ):
            raise TypeError("tool_calls must be a list of objects.")

    def to_message(self) -> dict[str, Any]:
        message: dict[str, Any] = {"role": self.role.value}
        if self.content is not None:
            message["content"] = self.content
        elif self.role is MessageRole.ASSISTANT:
            message["content"] = None
        if self.tool_calls:
            message["tool_calls"] = list(self.tool_calls)
        if self.tool_call_id:
            message["tool_call_id"] = self.tool_call_id
        return message


@dataclass(slots=True)
class Conversation:
    conversation_id: UUID = field(default_factory=uuid4)
    status: ConversationStatus = ConversationStatus.ACTIVE
    turns: list[ConversationTurn] = field(default_factory=list)
    summary: str | None = None
    summary_turn_count: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: datetime = field(default_factory=utc_now)
    updated_at: datetime = field(default_factory=utc_now)

    def add_turn(self, turn: ConversationTurn) -> None:
        if self.status is not ConversationStatus.ACTIVE:
            raise ValueError("Cannot add turns to an archived conversation.")
        if turn.conversation_id != self.conversation_id:
            raise ValueError("Turn belongs to another conversation.")
        if any(existing.turn_id == turn.turn_id for existing in self.turns):
            raise ValueError(f"Duplicate conversation turn: {turn.turn_id}")
        self.turns.append(turn)
        self.updated_at = utc_now()
