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

    @property
    def name(self) -> str:
        return self.definition.name
