from __future__ import annotations

import re
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from hashlib import sha256
from html.parser import HTMLParser

from app.research.models import InjectionFinding

_SPACE = re.compile(r"\s+")
_INJECTION_PATTERNS = {
    "instruction_override": re.compile(
        r"\b(ignore|disregard|override)\b.{0,60}\b(previous|prior|system|developer)\b",
        re.IGNORECASE,
    ),
    "role_impersonation": re.compile(
        r"\b(system message|you are (?:chatgpt|an? (?:ai|assistant)))\b",
        re.IGNORECASE,
    ),
    "tool_or_secret_request": re.compile(
        r"\b(call|run|execute|reveal|print)\b.{0,60}\b(tool|command|password|secret|api key|token)\b",
        re.IGNORECASE,
    ),
}


def parse_datetime(value: str | None) -> datetime | None:
    if not value or not value.strip():
        return None
    normalized = value.strip()
    try:
        parsed = datetime.fromisoformat(normalized.replace("Z", "+00:00"))
    except ValueError:
        try:
            parsed = parsedate_to_datetime(normalized)
        except (TypeError, ValueError, OverflowError):
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def detect_prompt_injection(text: str) -> tuple[InjectionFinding, ...]:
    findings: list[InjectionFinding] = []
    for category, pattern in _INJECTION_PATTERNS.items():
        match = pattern.search(text)
        if match is not None:
            evidence = match.group(0).casefold().encode("utf-8")
            findings.append(
                InjectionFinding(category, sha256(evidence).hexdigest())
            )
    return tuple(findings)


class _HTMLTextParser(HTMLParser):
    _ignored = {"script", "style", "noscript", "template", "svg", "canvas"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._ignored_depth = 0
        self._in_title = False
        self.title_parts: list[str] = []
        self.text_parts: list[str] = []
        self.published_at: datetime | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        normalized = tag.casefold()
        if normalized in self._ignored:
            self._ignored_depth += 1
            return
        if self._ignored_depth:
            return
        if normalized == "title":
            self._in_title = True
        if normalized == "meta" and self.published_at is None:
            values = {key.casefold(): value or "" for key, value in attrs}
            key = (values.get("property") or values.get("name")).casefold()
            if key in {
                "article:published_time",
                "date",
                "datepublished",
                "publication_date",
                "pubdate",
            }:
                self.published_at = parse_datetime(values.get("content"))
        if normalized in {"p", "div", "article", "section", "li", "br", "h1", "h2", "h3"}:
            self.text_parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        normalized = tag.casefold()
        if normalized in self._ignored:
            self._ignored_depth = max(0, self._ignored_depth - 1)
            return
        if normalized == "title":
            self._in_title = False

    def handle_data(self, data: str) -> None:
        if self._ignored_depth:
            return
        if self._in_title:
            self.title_parts.append(data)
        self.text_parts.append(data)


def extract_content(
    body: bytes,
    content_type: str,
    *,
    max_characters: int,
) -> tuple[str, str, datetime | None, tuple[InjectionFinding, ...]]:
    charset_match = re.search(r"charset=([\w.-]+)", content_type, re.IGNORECASE)
    charset = charset_match.group(1) if charset_match else "utf-8"
    try:
        decoded = body.decode(charset, errors="replace")
    except LookupError:
        decoded = body.decode("utf-8", errors="replace")
    title = ""
    published_at = None
    if "html" in content_type.casefold():
        parser = _HTMLTextParser()
        parser.feed(decoded)
        parser.close()
        title = _SPACE.sub(" ", " ".join(parser.title_parts)).strip()
        text = _SPACE.sub(" ", " ".join(parser.text_parts)).strip()
        published_at = parser.published_at
    else:
        text = _SPACE.sub(" ", decoded).strip()
    text = text[:max_characters]
    return title[:500], text, published_at, detect_prompt_injection(text)
