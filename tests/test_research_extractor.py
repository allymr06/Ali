from __future__ import annotations

from app.research.extractor import extract_content, parse_datetime


def test_html_extractor_ignores_active_and_hidden_content() -> None:
    html = b"""
    <html><head><title> Safe title </title><script>steal()</script>
    <meta property="article:published_time" content="2026-08-20T12:00:00Z"></head>
    <body><h1>Visible</h1><style>.x{}</style><noscript>hidden</noscript><p>Text</p></body></html>
    """
    title, text, published, findings = extract_content(
        html, "text/html; charset=utf-8", max_characters=1_000
    )

    assert title == "Safe title"
    assert "Visible" in text and "Text" in text
    assert "steal" not in text and "hidden" not in text
    assert published is not None and published.year == 2026
    assert findings == ()


def test_extractor_applies_character_limit_and_handles_unknown_charset() -> None:
    _, text, _, _ = extract_content(
        b"abcdefgh", "text/plain; charset=not-real", max_characters=4
    )
    assert text == "abcd"


def test_parse_datetime_rejects_invalid_values() -> None:
    assert parse_datetime("not-a-date") is None
    assert parse_datetime(None) is None
