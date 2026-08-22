from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any
from uuid import UUID, uuid4

from app.core.time import utc_now


class ExecutionEventType(str, Enum):
    PLAN_STARTED = "plan_started"
    PLAN_COMPLETED = "plan_completed"
    PLAN_FAILED = "plan_failed"
    PLAN_CANCELLED = "plan_cancelled"
    STEP_STARTED = "step_started"
    STEP_COMPLETED = "step_completed"
    STEP_FAILED = "step_failed"
    STEP_RETRYING = "step_retrying"


@dataclass(slots=True, frozen=True)
class ExecutionEvent:
    event_type: ExecutionEventType
    plan_id: UUID
    step_id: UUID | None = None
    step_name: str | None = None
    execution_id: UUID | None = None
    attempt: int | None = None
    data: dict[str, Any] = field(default_factory=dict)
    event_id: UUID = field(default_factory=uuid4)
    created_at: datetime = field(default_factory=utc_now)


class ExecutionEventBus:
    """In-process async event bus for execution lifecycle events."""

    def __init__(self) -> None:
        self._events: list[ExecutionEvent] = []
        self._subscribers: list = []

    def subscribe(self, subscriber) -> None:
        if subscriber not in self._subscribers:
            self._subscribers.append(subscriber)

    def unsubscribe(self, subscriber) -> None:
        if subscriber in self._subscribers:
            self._subscribers.remove(subscriber)

    @property
    def events(self) -> list[ExecutionEvent]:
        return list(self._events)

    async def publish(
        self,
        event: ExecutionEvent,
    ) -> None:
        self._events.append(event)

        for subscriber in tuple(self._subscribers):
            result = subscriber(event)

            if hasattr(result, "__await__"):
                await result
