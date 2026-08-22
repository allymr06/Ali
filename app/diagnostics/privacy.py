from __future__ import annotations

import re
from hashlib import sha256
from collections.abc import Mapping

_SENSITIVE_KEY = re.compile(
    r"(?:password|passwd|secret|token|api[_-]?key|authorization|cookie|credential|private[_-]?key)",
    re.IGNORECASE,
)
_BEARER = re.compile(r"\bBearer\s+[A-Za-z0-9._~+/=-]+", re.IGNORECASE)
_KEY_VALUE = re.compile(
    r"\b(?:sk-[A-Za-z0-9_-]{8,}|[A-Za-z0-9_-]{24,})\b"
)


def sanitize_text(value: object, *, limit: int = 1_000) -> str:
    text = str(value)
    text = _BEARER.sub("[REDACTED]", text)
    text = _KEY_VALUE.sub("[REDACTED]", text)
    return text[:limit]


def sanitize_trace_id(value: object | None) -> str | None:
    """Create a stable, non-reversible correlation identifier."""
    if value is None:
        return None
    return sha256(str(value).encode("utf-8")).hexdigest()[:32]


def sanitize_attributes(
    values: Mapping[str, object] | None,
    *,
    max_items: int = 32,
    depth: int = 0,
) -> dict[str, object]:
    if values is None:
        return {}
    if depth > 3:
        return {"truncated": True}
    result: dict[str, object] = {}
    for index, (raw_key, raw_value) in enumerate(values.items()):
        if index >= max_items:
            result["truncated"] = True
            break
        key = sanitize_text(raw_key, limit=100)
        if _SENSITIVE_KEY.search(key):
            result[key] = "[REDACTED]"
        elif isinstance(raw_value, Mapping):
            result[key] = sanitize_attributes(raw_value, depth=depth + 1)
        elif isinstance(raw_value, (list, tuple, set, frozenset)):
            result[key] = [sanitize_text(item, limit=300) for item in list(raw_value)[:20]]
        elif isinstance(raw_value, (str, int, float, bool)) or raw_value is None:
            result[key] = sanitize_text(raw_value) if isinstance(raw_value, str) else raw_value
        else:
            result[key] = sanitize_text(type(raw_value).__name__)
    return result
