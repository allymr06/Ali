"""Text utilities shared by the study layer.

Turkish, English and Latin appear side by side in medical material, so
normalization folds Turkish letters (``ı``→``i``, ``ş``→``s``) and
strips accents before matching, while the original text is always kept
for display. Everything here is deterministic and provider-free.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

_TRANSLATION = str.maketrans({"ı": "i", "İ": "i"})
_TOKEN_PATTERN = re.compile(r"[a-z0-9]+")
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+(?=[A-ZÇĞİÖŞÜ0-9(])")
_WHITESPACE = re.compile(r"[ \t\f\v]+")
_LATIN_ENDINGS = (
    "us", "um", "is", "ae", "alis", "aris", "icus", "ica", "icum", "osus",
    "osa", "osum", "ium", "ii", "is", "es", "ae", "ior", "ius", "eus", "ea",
    "ica", "inus", "ina", "inum", "atus", "ata", "atum", "ulum", "ula",
    "culus", "cula", "culum",
)
_LATIN_HEADS = (
    "musculus", "musculi", "os ", "ossa", "articulatio", "articulationes",
    "ligamentum", "ligamenta", "nervus", "nervi", "arteria", "arteriae",
    "vena", "venae", "facies", "processus", "tuberculum", "tuberositas",
    "fossa", "foramen", "sulcus", "margo", "angulus", "caput", "collum",
    "corpus", "condylus", "epicondylus", "trochanter", "crista", "linea",
    "spina", "incisura", "cavitas", "fovea", "canalis", "meatus", "tuber",
    "labrum", "bursa", "membrana", "fascia", "tendo", "vagina", "plexus",
    "ramus", "truncus", "ganglion",
)

STOPWORDS: frozenset[str] = frozenset(
    {
        # Turkish
        "ve", "veya", "ile", "bir", "bu", "su", "o", "da", "de", "mi", "mu",
        "mu", "ki", "icin", "gibi", "kadar", "olan", "olarak", "ne", "nasil",
        "neden", "hangi", "hangisi", "en", "cok", "daha", "ama", "fakat",
        "ancak", "her", "tum", "bana", "bize", "beni", "sen", "ben", "biz",
        "onlar", "onun", "bunun", "sunun", "var", "yok", "ise", "diye",
        "hakkinda", "uzerine", "icinde", "arasinda", "sonra", "once",
        "yani", "ya", "hem", "bile", "sadece", "yalniz", "lutfen", "anlat",
        "acikla", "soyle", "goster", "ver", "yap", "hazirla", "olsun",
        # English
        "the", "a", "an", "and", "or", "of", "in", "on", "to", "for", "is",
        "are", "with", "by", "from", "as", "at", "it", "this", "that",
        "these", "those", "which", "what", "how", "why", "me", "my", "about",
        "please", "explain", "show", "make", "give",
        # Latin function words
        "et", "cum", "ad", "ex", "per",
    }
)


def fold(text: str) -> str:
    """Casefold and strip Turkish/Latin accents for matching."""
    lowered = str(text or "").casefold().translate(_TRANSLATION)
    decomposed = unicodedata.normalize("NFKD", lowered)
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch))


def normalize(text: str) -> str:
    """Folded text with punctuation collapsed to single spaces."""
    return " ".join(_TOKEN_PATTERN.findall(fold(text)))


def tokens(text: str) -> list[str]:
    return _TOKEN_PATTERN.findall(fold(text))


def content_tokens(text: str, *, min_length: int = 2) -> list[str]:
    return [
        token
        for token in tokens(text)
        if len(token) >= min_length and token not in STOPWORDS
    ]


def stem(token: str, length: int = 5) -> str:
    """A crude prefix stem: Turkish suffix chains make exact matching
    useless, and a fixed prefix is honest about being approximate."""
    return token[:length] if len(token) > length else token


def stems(text: str) -> list[str]:
    return [stem(token) for token in content_tokens(text)]


def has_stem(text_tokens: Sequence[str], candidates: Iterable[str]) -> bool:
    return any(
        any(token.startswith(candidate) for candidate in candidates)
        for token in text_tokens
    )


def sentences(text: str) -> list[str]:
    cleaned = " ".join(str(text or "").split())
    if not cleaned:
        return []
    return [part.strip() for part in _SENTENCE_END.split(cleaned) if part.strip()]


def clean_lines(text: str) -> list[str]:
    """Collapse whitespace per line and drop empty lines."""
    lines = []
    for raw in str(text or "").replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        line = _WHITESPACE.sub(" ", raw).strip()
        if line:
            lines.append(line)
    return lines


def looks_latin(word: str) -> bool:
    """Heuristic: does a word look like an anatomical Latin term?"""
    folded = fold(word)
    if len(folded) < 4 or not folded.isalpha():
        return False
    if any(folded.startswith(head.strip()) for head in _LATIN_HEADS):
        return True
    return any(folded.endswith(ending) for ending in _LATIN_ENDINGS if len(ending) >= 2) and not folded.endswith("ies") and folded not in {"this", "thus", "plus", "bonus", "focus", "virus", "status", "campus"}


def latin_density(text: str) -> float:
    words = [word for word in tokens(text) if word.isalpha()]
    if not words:
        return 0.0
    return sum(1 for word in words if looks_latin(word)) / len(words)


def ngrams(items: Sequence[str], size: int = 3) -> set[tuple[str, ...]]:
    if len(items) < size:
        return {tuple(items)} if items else set()
    return {tuple(items[index : index + size]) for index in range(len(items) - size + 1)}


def jaccard(left: set, right: set) -> float:
    if not left and not right:
        return 0.0
    union = len(left | right)
    return len(left & right) / union if union else 0.0


def similarity(left: str, right: str) -> float:
    """Token 3-gram Jaccard blended with token-set overlap (0..1)."""
    left_tokens = content_tokens(left)
    right_tokens = content_tokens(right)
    if not left_tokens or not right_tokens:
        return 0.0
    gram_score = jaccard(ngrams(left_tokens), ngrams(right_tokens))
    set_score = jaccard(set(left_tokens), set(right_tokens))
    return round(0.6 * gram_score + 0.4 * set_score, 4)


@dataclass(frozen=True, slots=True)
class TextSpan:
    text: str
    start: int
    end: int
    heading: str = ""


_HEADING_HINT = re.compile(
    r"^(?:\d+(?:\.\d+)*[.)]?\s+)?[A-ZÇĞİÖŞÜ][^.!?]{2,70}$"
)


def is_heading(line: str, *, next_line: str | None = None) -> bool:
    """A short line without terminal punctuation that opens a section."""
    stripped = line.strip()
    if not stripped or len(stripped) > 80:
        return False
    if stripped.endswith((".", "!", "?", ",", ";", ":")):
        return False
    words = stripped.split()
    if len(words) > 10:
        return False
    if stripped.isupper() and len(stripped) > 3:
        return True
    title_case = sum(1 for word in words if word[:1].isupper()) >= max(1, len(words) - 1)
    numbered = bool(re.match(r"^\d+(?:\.\d+)*[.)]?\s", stripped))
    return bool(_HEADING_HINT.match(stripped)) and (title_case or numbered)


def chunk_text(
    text: str,
    *,
    target_chars: int = 900,
    max_chars: int = 1400,
    overlap_sentences: int = 1,
) -> list[TextSpan]:
    """Split page text into heading-aware, sentence-aligned chunks.

    Chunks keep their character offsets in the page so a citation can
    point back to the exact span; a heading line becomes the chunk's
    heading rather than being swallowed into the prose.
    """
    source = str(text or "")
    if not source.strip():
        return []
    lines = source.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    # Walk lines keeping offsets so spans map back into the source.
    units: list[tuple[str, int, int, bool]] = []
    cursor = 0
    for line in lines:
        start = cursor
        cursor += len(line) + 1
        stripped = line.strip()
        if not stripped:
            continue
        leading = len(line) - len(line.lstrip())
        unit_start = start + leading
        unit_end = unit_start + len(stripped)
        units.append((stripped, unit_start, unit_end, is_heading(stripped)))
    spans: list[TextSpan] = []
    heading = ""
    buffer: list[tuple[str, int, int]] = []

    def flush() -> None:
        nonlocal buffer
        if not buffer:
            return
        chunk = " ".join(item[0] for item in buffer)
        spans.append(TextSpan(chunk, buffer[0][1], buffer[-1][2], heading))
        keep = buffer[-overlap_sentences:] if overlap_sentences > 0 else []
        buffer = list(keep) if sum(len(item[0]) for item in keep) < target_chars // 2 else []

    for text_unit, start, end, heading_flag in units:
        if heading_flag:
            flush()
            buffer = []
            heading = text_unit
            continue
        pieces = sentences(text_unit) or [text_unit]
        offset = start
        for piece in pieces:
            location = source.find(piece, offset, end)
            piece_start = location if location >= 0 else offset
            piece_end = piece_start + len(piece)
            offset = piece_end
            current = sum(len(item[0]) for item in buffer) + len(buffer)
            if buffer and current + len(piece) > target_chars:
                flush()
            if len(piece) > max_chars:
                # A pathological run-on: hard split so no chunk explodes.
                for index in range(0, len(piece), max_chars):
                    part = piece[index : index + max_chars]
                    spans.append(TextSpan(part, piece_start + index, piece_start + index + len(part), heading))
                buffer = []
                continue
            buffer.append((piece, piece_start, piece_end))
    flush()
    # Drop overlap-only tails duplicated by the final flush.
    deduped: list[TextSpan] = []
    seen: set[tuple[int, int]] = set()
    for span in spans:
        key = (span.start, span.end)
        if key in seen or not span.text.strip():
            continue
        seen.add(key)
        deduped.append(span)
    return deduped


# A pair with no page word around it: the dash may not be glued to
# another number group, which is what keeps phone numbers, dates,
# decimals, times and UUID-shaped ids out.
_BARE_PAIR = re.compile(
    r"(?<![\d.,:/\-–—])(\d{1,4})\s*[-–—]\s*(\d{1,4})(?![\d\-–—])(?![.,:/]\d)"
)
# The word touching the pair, with only punctuation allowed in between.
_WORD_BEFORE = re.compile(r"([a-z]+)[^a-z0-9]*$")
_WORD_AFTER = re.compile(r"[^a-z0-9]*([a-z]+)")
_NOT_PAGES_BEFORE = frozenset(
    {
        "saat", "saatte", "saatler", "dakika", "saniye", "zorluk", "seviye",
        "duzey", "derece", "puan", "not", "soru", "sik", "secenek", "adet",
        "tane", "yas", "tel", "telefon", "telefonum", "numara", "numaram",
        "numarasi", "no", "kod",
        "hour", "hours", "minute", "minutes", "second", "seconds",
        "difficulty", "level", "score", "phone", "question", "questions",
        "option", "options", "age", "code",
    }
)
_NOT_PAGES_AFTER = frozenset(
    {
        "soru", "sorusu", "soruluk", "sorudan", "sik", "sikli", "secenek",
        "secenekli", "dakika", "saniye", "saat", "adet", "tane", "kelime",
        "yas", "puan", "derece",
        "question", "questions", "option", "options", "minute", "minutes",
        "second", "seconds", "hour", "hours", "word", "words", "point",
        "points",
    }
)


def _bare_page_pair(folded: str) -> tuple[int, int] | None:
    """Read a bare ``20-40`` pair as pages, but only where it can only
    mean pages.

    Students state the scope with no page word at all ("20-40 arası soru
    hazırla"), so refusing the form drops their scope silently. The
    guards keep every other kind of number out: the pair must climb, both
    sides must be plain 1..9999 page numbers, and no neighbouring word may
    give the numbers a different unit.
    """
    for match in _BARE_PAIR.finditer(folded):
        first_text, second_text = match.group(1), match.group(2)
        # A zero-padded number is an id, a phone or a clock, never a page.
        if first_text.startswith("0") or second_text.startswith("0"):
            continue
        first, second = int(first_text), int(second_text)
        if second <= first:
            continue
        # "2024-2025" is an academic year; an explicit page word still
        # gets such a span through the patterns above.
        if second == first + 1 and 1900 <= first <= 2100:
            continue
        before = _WORD_BEFORE.search(folded[: match.start()])
        if before is not None and before.group(1) in _NOT_PAGES_BEFORE:
            continue
        after = _WORD_AFTER.match(folded[match.end() :])
        if after is not None and after.group(1) in _NOT_PAGES_AFTER:
            continue
        return (first, second)
    return None


def parse_page_range(text: str) -> tuple[int, int] | None:
    """``20-40`` / ``20–40. sayfalar`` / ``sayfa 20 ile 40`` → (20, 40).

    A pair carrying no page word is read as pages only under the guards in
    ``_bare_page_pair``. Pages are 1-based downstream, so a non-positive
    page is refused instead of selecting nothing.
    """
    folded = fold(text)
    patterns = (
        r"(?:sayfa(?:lar)?(?:i|si|ini|dan|den)?\s*)(\d{1,4})\s*(?:-|–|—|ile|ila|to|/)\s*(\d{1,4})",
        r"(\d{1,4})\s*(?:-|–|—|ile|ila|to)\s*(\d{1,4})\s*\.?\s*(?:sayfa|sayfalar|s\b|pages?|pp?\b)",
        r"(?:pages?|pp?\.?)\s*(\d{1,4})\s*(?:-|–|—|to)\s*(\d{1,4})",
    )
    for pattern in patterns:
        match = re.search(pattern, folded)
        if match:
            first, second = int(match.group(1)), int(match.group(2))
            if first <= 0 or second <= 0:
                continue
            return (min(first, second), max(first, second))
    bare = _bare_page_pair(folded)
    if bare is not None:
        return bare
    single = re.search(r"(?:sayfa|page|s\.)\s*(\d{1,4})\b", folded)
    if single:
        page = int(single.group(1))
        return (page, page) if page > 0 else None
    return None


def excerpt(text: str, limit: int = 220) -> str:
    cleaned = " ".join(str(text or "").split())
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[: limit - 1].rstrip() + "…"
