from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from app.core.models import ToolDefinition

ToolCallable = Callable[..., Any]


@dataclass(slots=True)
class RegisteredTool:
    """A tool definition paired with its executable callable."""

    definition: ToolDefinition
    handler: ToolCallable
    enabled: bool = True
    source: str = "runtime"

    def __post_init__(self) -> None:
        if not isinstance(self.enabled, bool):
            raise TypeError("Tool enabled state must be a boolean.")

        if not callable(self.handler):
            raise TypeError("Tool handler must be callable.")

        if not isinstance(self.source, str):
            raise TypeError("Tool source must be a string.")

        self.source = self.source.strip()

        if not self.source:
            raise ValueError("Tool source cannot be empty.")

    @property
    def name(self) -> str:
        return self.definition.name
