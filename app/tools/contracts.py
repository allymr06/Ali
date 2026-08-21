from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from typing import Any

from app.core.models import ToolDefinition


@dataclass(frozen=True, slots=True)
class ToolContract:
    """Provider-neutral, serializable contract for one executable tool."""

    definition: ToolDefinition
    input_schema: dict[str, Any]
    output_schema: dict[str, Any]
    enabled: bool = True
    source: str = "runtime"

    def to_dict(self) -> dict[str, Any]:
        definition = self.definition
        return {
            "name": definition.name,
            "description": definition.description,
            "version": definition.version,
            "input_schema": deepcopy(self.input_schema),
            "output_schema": deepcopy(self.output_schema),
            "risk_level": definition.risk_level.value,
            "requires_confirmation": definition.requires_confirmation,
            "timeout_seconds": definition.timeout_seconds,
            "capabilities": sorted(definition.capabilities),
            "tags": sorted(definition.tags),
            "max_concurrency": definition.max_concurrency,
            "retry_policy": {
                "max_attempts": definition.retry_max_attempts,
                "backoff_seconds": definition.retry_backoff_seconds,
                "idempotent": definition.idempotent,
            },
            "enabled": self.enabled,
            "source": self.source,
            "metadata": deepcopy(definition.metadata),
        }

    def to_openai(self) -> dict[str, Any]:
        """Return the OpenAI function-tool representation."""
        return {
            "type": "function",
            "function": {
                "name": self.definition.name,
                "description": self.definition.description,
                "parameters": deepcopy(self.input_schema),
            },
        }
