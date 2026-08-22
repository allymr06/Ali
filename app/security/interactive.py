from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from types import MappingProxyType
from uuid import UUID

from app.core.models import RiskLevel


_SENSITIVE_PARAMETER_PARTS = (
    "api_key",
    "apikey",
    "authorization",
    "credential",
    "password",
    "secret",
    "token",
)

_CONTENT_PARAMETER_PARTS = (
    "content",
    "data",
    "text",
    "value",
)


def _safe_value(name: str, value: object) -> object:
    normalized = name.strip().casefold()
    if any(part in normalized for part in _SENSITIVE_PARAMETER_PARTS):
        return "<gizli>"
    if (
        isinstance(value, str)
        and any(part in normalized for part in _CONTENT_PARAMETER_PARTS)
    ):
        return f"<{len(value)} karakter>"
    if isinstance(value, str) and len(value) > 240:
        return f"{value[:120]}… <{len(value)} karakter>"
    if isinstance(value, list):
        return f"<{len(value)} öğe>"
    if isinstance(value, dict):
        return f"<{len(value)} alan>"
    return value


def safe_approval_parameters(
    parameters: Mapping[str, object],
) -> Mapping[str, object]:
    """Return a read-only, display-safe summary of exact tool arguments."""
    return MappingProxyType(
        {
            str(name): _safe_value(str(name), value)
            for name, value in parameters.items()
        }
    )


@dataclass(frozen=True, slots=True)
class InteractiveApprovalRequest:
    """A short-lived request for one exact user-visible side effect."""

    operation_id: UUID
    request_id: UUID
    conversation_id: UUID
    request_source: str
    tool_name: str
    operation: str
    risk_level: RiskLevel
    reason: str
    parameters: Mapping[str, object]
    expires_at: datetime


InteractiveApprovalCallback = Callable[
    [InteractiveApprovalRequest],
    Awaitable[bool],
]
