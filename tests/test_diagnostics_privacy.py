from __future__ import annotations

from app.diagnostics.privacy import (
    sanitize_attributes,
    sanitize_text,
    sanitize_trace_id,
)


def test_diagnostic_attributes_redact_sensitive_keys_recursively() -> None:
    cleaned = sanitize_attributes(
        {
            "api_key": "sk-supersecretvalue",
            "nested": {"Authorization": "Bearer abcdefghijklmnop"},
            "safe": "visible",
        }
    )

    assert cleaned["api_key"] == "[REDACTED]"
    assert cleaned["nested"] == {"Authorization": "[REDACTED]"}
    assert cleaned["safe"] == "visible"


def test_diagnostic_text_removes_bearer_and_key_like_values() -> None:
    text = sanitize_text("Authorization Bearer abcdefghijklmnop and sk-abcdefgh12345678")
    assert "abcdefghijklmnop" not in text
    assert "sk-" not in text
    assert text.count("[REDACTED]") == 2


def test_diagnostic_attributes_are_bounded() -> None:
    cleaned = sanitize_attributes({f"item_{index}": index for index in range(40)})
    assert len(cleaned) == 33
    assert cleaned["truncated"] is True


def test_trace_identifiers_are_stable_but_non_reversible() -> None:
    raw = "request-123-secret-like-correlation"
    first = sanitize_trace_id(raw)
    assert first == sanitize_trace_id(raw)
    assert raw not in first
    assert len(first) == 32
    assert sanitize_trace_id(None) is None
