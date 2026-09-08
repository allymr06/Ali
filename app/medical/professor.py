"""Professor question import and evidence-based style profiling.

Imported questions are parsed deterministically (numbered stems, lettered
options, answer keys only when the text states them). A style profile is
a set of observed ratios with sample sizes and an honest confidence; the
generation directive it yields only repeats what was actually seen.
"""

from __future__ import annotations

import re
import statistics
from difflib import SequenceMatcher
from collections import Counter
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from typing import Any

from app.core.time import utc_now
from app.medical.models import (
    EvidenceSupport,
    ProfessorProfile,
    Question,
    QuestionOption,
    QuestionOrigin,
    StyleFeature,
    SUBJECT_LABELS_TR,
    new_id,
)
from app.medical.text import fold, latin_density, similarity, tokens

_QUESTION_START = re.compile(r"^\s*(\d{1,3})\s*[.)\-:]\s*(.*)$")
_OPTION_LINE = re.compile(r"^\s*\(?([A-Fa-f])\s*[.)\-:]\s*(.+)$")
_INLINE_OPTIONS = re.compile(r"(?:(?<=\s)|^)\(?([A-Fa-f])[.)]\s*")
# A key stated at the very end of the stem ("… hangisidir? Cevap: C"). The
# letter has to be the last thing on the line: an annotation like "Cevap D
# değildir" names the option that is NOT the answer, and reading it as a key
# would hand the student the inverse of what the paper says.
_ANSWER_TAIL = re.compile(r"(?:do[gğ]ru\s+)?(?:cevap|yan[ıi]t|answer|key)\s*[:\-]?\s*\(?([A-Fa-f])\)?\s*[.)]?\s*$", re.IGNORECASE)
# A whole line that states nothing but the answer. Exam prose mentions
# "cevap" freely — inside an option, in a note beside the stem — and such a
# line names no key, so anything around the statement disqualifies it.
_ANSWER_ONLY = re.compile(r"^\(?(?:do[gğ]ru\s+)?(?:cevap|yan[ıi]t|answer|key)\)?\s*[:\-]?\s*\(?([A-Fa-f])\)?\.?$", re.IGNORECASE)
_ANSWER_TABLE = re.compile(r"\b(\d{1,3})\s*[-.:)]\s*([A-Fa-f])\b")
_ANSWER_HEADER = re.compile(r"(cevap anahtar|yan[ıi]t anahtar|answer key|cevaplar|answers)", re.IGNORECASE)
# What may stand between the pairs of an answer-table line: separators only.
# A line holding anything else ("3. B vitamini eksikliği …") is exam text.
_TABLE_SEPARATORS = re.compile(r"[\s,;.:|/\\–—-]*")
_DANGLING_OPENERS = re.compile(r"[\s(\[{]+$")
_MULTI_STATEMENT = re.compile(r"(?:^|\s|\()(i{1,3}|iv|v)\s*[.)\-]", re.IGNORECASE)
# The exam system's export: every question carries its owner and department,
# pages open with the committee, and the key is a suffix on the option itself
# ("-Doğru Seçenek"); the student's own mark is another suffix and never a key.
_OWNER_LINE = re.compile(r"^\s*soru\s+sahibi\s*[:\-–]\s*(.+?)\s*$", re.IGNORECASE)
_DEPARTMENT_LINE = re.compile(r"^\s*anabilim\s*dal[ıi]\s*[:\-–]\s*(.+?)\s*$", re.IGNORECASE)
_COMMITTEE_LINE = re.compile(r"^\s*kom[iİı]te\s*[-:]?\s*(\d{1,2})\s*$", re.IGNORECASE)
_STEM_PREFIX = re.compile(r"^\s*soru\s*[:\-–]\s*", re.IGNORECASE)
# In an export every question starts as "12. soru:"; a numbered list inside a
# stem ("1. Bazı öğünlerden…") is then part of the question, not a new one.
_EXPORT_START = re.compile(r"^\s*(\d{1,3})\s*[.)\-:]?\s*soru\s*[:\-–]\s*(.*)$", re.IGNORECASE)
EXPORT_MIN_STARTS = 3
_KEY_SUFFIX = re.compile(r"\s*[-–]\s*do[gğ]ru\s+se[çc]enek\s*[-–]?\s*$", re.IGNORECASE)
_STUDENT_SUFFIX = re.compile(r"\s*[-–]\s*[öo][ğg]rencinin\s+i[şs]aretledi[ğg]i\s*[-–]?\s*$", re.IGNORECASE)
_IMAGE_WORDS = ("sekil", "resim", "okla", "isaretli", "goruntu", "fotograf", "figure", "image", "arrow", "labeled", "labelled", "mikrograf", "preparat", "kesitte")

SAMPLE_LIMITED = 10
SAMPLE_MODERATE = 30


@dataclass(slots=True)
class ParsedQuestion:
    number: str
    stem: str
    options: list[tuple[str, str]]
    answer_key: str | None = None
    has_image: bool = False
    warnings: list[str] = field(default_factory=list)
    # From an exam system's export: who wrote the question, which department
    # and committee it belongs to, and which option the student had marked.
    owner: str | None = None
    department: str | None = None
    committee: str | None = None
    student_marked: str | None = None


@dataclass(slots=True)
class ImportResult:
    questions: list[ParsedQuestion]
    answer_key_found: bool
    notes: list[str] = field(default_factory=list)


@dataclass(slots=True)
class _AnswerSection:
    """Question lines and the answer table that closes them, if any."""

    body: list[str]
    answer_map: dict[str, str]
    conflicts: list[str]


class QuestionImportParser:
    """Deterministic extraction from exam-like text."""

    def parse(self, text: str) -> ImportResult:
        normalized = str(text or "").replace("\r\n", "\n").replace("\r", "\n")
        lines = normalized.split("\n")
        questions: list[ParsedQuestion] = []
        notes: list[str] = []
        unresolved: list[str] = []
        export = sum(1 for line in lines if _EXPORT_START.match(line)) >= EXPORT_MIN_STARTS
        for section in self._split_answer_tables(lines):
            parsed_section: list[ParsedQuestion] = []
            for number, block_lines, committee in self._blocks(section.body, export=export):
                parsed = self._parse_block(number, block_lines, export=export)
                if parsed is not None:
                    parsed.committee = committee
                    parsed_section.append(parsed)
            # Every paper starts numbering at 1, so a section that holds the
            # same number twice holds more than one paper: a table row cannot
            # be attributed to a single question and is refused, not guessed.
            counted = Counter(parsed.number for parsed in parsed_section)
            for parsed in parsed_section:
                if parsed.answer_key is not None:
                    continue
                if parsed.number in section.conflicts or (counted[parsed.number] > 1 and parsed.number in section.answer_map):
                    unresolved.append(parsed.number)
                    continue
                table_key = section.answer_map.get(parsed.number)
                # A mistyped table entry ("3-F" against five options) names
                # nothing to index, so it is dropped exactly as a stray inline
                # key is.
                if table_key and any(key == table_key for key, _ in parsed.options):
                    parsed.answer_key = table_key
            questions.extend(parsed_section)
        found = any(question.answer_key for question in questions)
        if questions and not found and not unresolved:
            notes.append("Metinde cevap anahtarı bulunamadı; sorular anahtarsız kaydedildi.")
        if unresolved:
            listed = ", ".join(dict.fromkeys(unresolved))
            notes.append(f"Cevap anahtarı şu soru numaralarına güvenle bağlanamadı: {listed}. Bu sorular anahtarsız kaydedildi.")
        missing = [question.number for question in questions if len(question.options) < 2]
        if missing:
            notes.append(f"Seçenekleri ayrıştırılamayan sorular: {', '.join(missing[:12])}.")
        return ImportResult(questions=[question for question in questions if len(question.options) >= 2], answer_key_found=found, notes=notes)

    @classmethod
    def _split_answer_tables(cls, lines: list[str]) -> list[_AnswerSection]:
        """Cut the text wherever an answer table stands.

        A table states the keys of the questions above it, so several papers
        pasted into one file each keep their own table instead of the last one
        overwriting the rest, and nothing below a table is thrown away.
        """
        sections: list[_AnswerSection] = []
        body: list[str] = []
        index = 0
        while index < len(lines):
            end = cls._table_end(lines, index)
            if end is None:
                # A run of pairs that is not a table cannot become one a line
                # later — a suffix of it holds fewer pairs and the same text
                # after it — so the run joins the body without being rescanned.
                stop = index + 1
                if cls._is_pair_line(lines[index]):
                    while stop < len(lines) and cls._is_pair_line(lines[stop]):
                        stop += 1
                body.extend(lines[index:stop])
                index = stop
                continue
            answer_map, conflicts = cls._table_map(lines[index:end])
            sections.append(_AnswerSection(body=body, answer_map=answer_map, conflicts=conflicts))
            body = []
            index = end
        sections.append(_AnswerSection(body=body, answer_map={}, conflicts=[]))
        return sections

    @classmethod
    def _table_end(cls, lines: list[str], index: int) -> int | None:
        """Where the answer table starting at `index` ends, or None."""
        line = lines[index]
        # A header only opens a table when pair lines actually follow it, which
        # is a far better test than the line's length: a titled key ("CEVAP
        # ANAHTARI (2024 - A Grubu)") is a header, a numbered stem never is.
        header = bool(_ANSWER_HEADER.search(line)) and not _QUESTION_START.match(line)
        if not header and not cls._is_pair_line(line):
            return None
        end = cursor = index + 1 if header else index
        while cursor < len(lines):
            if cls._is_pair_line(lines[cursor]):
                cursor += 1
                end = cursor
            elif not lines[cursor].strip():
                cursor += 1  # a blank line between table rows does not end it
            else:
                break
        pairs = _ANSWER_TABLE.findall("\n".join(lines[index:end]))
        if header:
            return end if len(pairs) >= 3 else None
        # With no header only a dense table at the very end of the text is
        # certain enough to read as one; anywhere else the run is exam text.
        if len(pairs) >= 5 and all(not rest.strip() for rest in lines[end:]):
            return end
        return None

    @staticmethod
    def _is_pair_line(line: str) -> bool:
        """True for a line made of nothing but 'n-X' pairs and separators."""
        stripped = line.strip()
        if not stripped or not _ANSWER_TABLE.search(stripped):
            return False
        return _TABLE_SEPARATORS.fullmatch(_ANSWER_TABLE.sub(" ", stripped)) is not None

    @staticmethod
    def _table_map(table_lines: list[str]) -> tuple[dict[str, str], list[str]]:
        """The table's rows, minus every number it states two ways."""
        answer_map: dict[str, str] = {}
        conflicts: list[str] = []
        for number, key in _ANSWER_TABLE.findall("\n".join(table_lines)):
            letter = key.upper()
            previous = answer_map.get(number)
            if previous is not None and previous != letter and number not in conflicts:
                # The table contradicts itself here and which row is the typo
                # is unknowable, so neither letter may be handed to a question.
                conflicts.append(number)
            answer_map.setdefault(number, letter)
        for number in conflicts:
            answer_map.pop(number, None)
        return answer_map, conflicts

    @staticmethod
    def _blocks(lines: list[str], *, export: bool = False) -> list[tuple[str, list[str], str | None]]:
        """(number, lines, committee) per question; a committee heading holds until the next.

        ``export`` narrows a question start to "N. soru:" so that a numbered list
        inside a stem stays inside it.
        """
        blocks: list[tuple[str, list[str], str | None]] = []
        current: tuple[str, list[str], str | None] | None = None
        committee: str | None = None
        start = _EXPORT_START if export else _QUESTION_START
        for line in lines:
            heading = _COMMITTEE_LINE.match(line)
            if heading:
                committee = heading.group(1)
                continue
            match = start.match(line)
            if match and not _OPTION_LINE.match(line):
                if current is not None:
                    blocks.append(current)
                current = (match.group(1), [match.group(2)], committee)
                continue
            if current is not None:
                current[1].append(line)
        if current is not None:
            blocks.append(current)
        return blocks

    @staticmethod
    def _strip_marks(text: str) -> tuple[str, list[str], bool]:
        """Take the export's suffixes off an option: (text, key marks, student's mark).

        The suffixes come in either order, may both sit on one option, and may
        have been wrapped onto the line below by the scan.
        """
        marks = 0
        student = False
        while True:
            if _KEY_SUFFIX.search(text):
                text = _KEY_SUFFIX.sub("", text).strip()
                marks += 1
                continue
            if _STUDENT_SUFFIX.search(text):
                text = _STUDENT_SUFFIX.sub("", text).strip()
                student = True
                continue
            break
        return text, ["key"] * marks, student

    def _parse_block(self, number: str, lines: list[str], *, export: bool = False) -> ParsedQuestion | None:
        # (text, held): a held line looked like an option but came before the
        # owner lines of an export; it is stem unless no options follow them.
        stem_entries: list[tuple[str, bool]] = []
        options: list[tuple[str, str]] = []
        answer: str | None = None
        owner: str | None = None
        department: str | None = None
        marked_keys: list[str] = []
        student_marked: str | None = None
        warnings: list[str] = []
        # A scanned export can miss the owner lines of one question; only a
        # block that has them holds its stem back until they are read.
        expects_owner = export and any(
            _OWNER_LINE.match(line.strip()) or _DEPARTMENT_LINE.match(line.strip()) for line in lines
        )
        for line in lines:
            stripped = line.strip()
            if not stripped:
                continue
            owner_line = _OWNER_LINE.match(stripped)
            if owner_line:
                owner = owner_line.group(1).strip()
                continue
            department_line = _DEPARTMENT_LINE.match(stripped)
            if department_line:
                department = department_line.group(1).strip()
                continue
            option = _OPTION_LINE.match(stripped)
            # In an export the options follow the owner and department lines; a
            # stem that opens with an abbreviation ("A. subclavia ve …") is not
            # option A. The line is held: a paper that prints the options beside
            # a figure, above the owner lines, has no options after them.
            held = False
            if option and expects_owner and owner is None and department is None and not options:
                option = None
                held = True
            if option:
                letter, text = option.group(1).upper(), option.group(2).strip()
                text, marks, student = self._strip_marks(text)
                marked_keys.extend(letter for _ in marks)
                if student:
                    student_marked = letter
                options.append((letter, text))
                continue
            stated_answer = _ANSWER_ONLY.match(stripped)
            if stated_answer:
                answer = stated_answer.group(1).upper()
                continue
            if options:
                # Continuation of the last option; a scan wraps the suffix too.
                key, text = options[-1]
                merged, marks, student = self._strip_marks(f"{text} {stripped}")
                marked_keys.extend(key for _ in marks)
                if student:
                    student_marked = key
                options[-1] = (key, merged)
            elif stem_entries and stem_entries[-1][1] and not held:
                text, _held = stem_entries[-1]
                stem_entries[-1] = (f"{text} {stripped}", True)
            else:
                stem_entries.append((stripped, held))
        if not options and any(held for _, held in stem_entries):
            # Nothing followed the owner lines: the held lines were the options.
            for text, held in stem_entries:
                if not held:
                    continue
                option = _OPTION_LINE.match(text)
                letter, body = option.group(1).upper(), option.group(2).strip()
                body, marks, student = self._strip_marks(body)
                marked_keys.extend(letter for _ in marks)
                if student:
                    student_marked = letter
                options.append((letter, body))
            stem_parts = [text for text, held in stem_entries if not held]
        else:
            stem_parts = [text for text, _ in stem_entries]
        if stem_parts:
            # "1. soru:" leaves the word on the first line; it is not the stem.
            stem_parts[0] = _STEM_PREFIX.sub("", stem_parts[0])
        stem = " ".join(part for part in stem_parts if part).strip()
        if not options and stem:
            stem, options = self._split_inline_options(stem)
        else:
            # "A) x B) y C) z" on one line: the first marker was taken as
            # option A with the rest as its text; split the rest too.
            expanded: list[tuple[str, str]] = []
            for key, text in options:
                head, inline = self._split_inline_options(f"{key}) {text}")
                if len(inline) >= 2 and not head:
                    expanded.extend(inline)
                else:
                    expanded.append((key, text))
            options = expanded
        if not stem:
            return None
        # Keep only the first run of sequential keys.
        cleaned: list[tuple[str, str]] = []
        for key, text in options:
            expected = "ABCDEF"[len(cleaned)] if len(cleaned) < 6 else None
            if key == expected:
                cleaned.append((key, text))
        if answer is None and marked_keys:
            distinct = list(dict.fromkeys(marked_keys))
            if len(distinct) == 1:
                answer = distinct[0]
            else:
                # Two options marked correct name no key; the paper contradicts itself.
                warnings.append("birden çok seçenek 'Doğru Seçenek' olarak işaretli")
        stem_answer = _ANSWER_TAIL.search(stem)
        if stem_answer and answer is None:
            # A stem that is nothing but the statement asks nothing, so the
            # statement is only read as a key when a question survives it.
            remainder = _DANGLING_OPENERS.sub("", stem[: stem_answer.start()].strip())
            if remainder:
                answer = stem_answer.group(1).upper()
                stem = remainder
        folded = fold(stem)
        has_image = any(word in folded for word in _IMAGE_WORDS)
        return ParsedQuestion(
            number=number,
            stem=stem,
            options=cleaned,
            answer_key=answer if answer and any(key == answer for key, _ in cleaned) else None,
            has_image=has_image,
            warnings=warnings,
            owner=owner or None,
            department=department or None,
            student_marked=student_marked if student_marked and any(key == student_marked for key, _ in cleaned) else None,
        )

    @staticmethod
    def _split_inline_options(text: str) -> tuple[str, list[tuple[str, str]]]:
        matches = list(_INLINE_OPTIONS.finditer(text))
        if len(matches) < 2:
            return text, []
        stem = text[: matches[0].start()].strip()
        options: list[tuple[str, str]] = []
        for index, match in enumerate(matches):
            end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
            options.append((match.group(1).upper(), text[match.end() : end].strip(" ;,")))
        return stem, options


# ---------------------------------------------------------------------------
# who wrote it: professor mentions on title pages, in file names, in headings
# ---------------------------------------------------------------------------

# Academic titles as they appear on Turkish lecture title pages, in any of
# their spellings ("Prof. Dr.", "PROF.DR.", "Yrd.Doç.Dr.", "Dr. Öğr. Üyesi",
# "Doktor Öğretim Üyesi", "Öğr. Gör.", "Uzm. Dr."). A line is a mention only
# when it STARTS with one of these; "Öğrenim Hedefleri" starts with none.
_TITLE_WORDS = (
    "prof", "profesör", "profesor", "doç", "doc", "doçent", "docent", "dr", "doktor", "yrd", "yard", "yardımcı", "yardimci",
    "uzm", "uzman", "arş", "ars", "araştırma", "arastirma", "öğr", "ogr", "öğretim", "ogretim", "gör", "gor", "görevlisi",
    "gorevlisi", "üyesi", "uyesi", "md", "phd", "op",
)
# Longest spellings first and a boundary after each token, so "Öğr" never
# eats the start of "Öğretim" and "Gör" never the start of "Görkem".
_TITLE_PATTERN = re.compile(
    r"^(?:(?:" + "|".join(re.escape(word) for word in sorted(_TITLE_WORDS, key=len, reverse=True)) + r")(?:\.|(?=\s)|$)[.\s]*)+",
    re.IGNORECASE,
)
_ROLE_WORDS = frozenset({
    "uzmanı", "uzmani", "epidemiyolog", "başkanı", "baskani", "anabilim", "dalı", "dali", "bölümü", "bolumu", "fakültesi",
    "fakultesi", "üniversitesi", "universitesi", "hastanesi", "kliniği", "klinigi", "hedefleri", "hedefler", "ve", "tıp",
    "tip", "fakülte", "fakulte", "öğrenim", "ogrenim", "ders", "dersi", "kurulu", "komite", "yılı", "yili", "eğitim", "egitim",
})
_NAME_TOKEN = re.compile(r"^[A-Za-zÇĞİÖŞÜçğıöşüÂâÎîÛû]+(?:-[A-Za-zÇĞİÖŞÜçğıöşüÂâÎîÛû]+)?\.?$")
_BULLET = re.compile(r"^[\s•·\-–—*\u2022\uf0b7\uf09e]+")
# What follows a name in a heading without being part of it.
_TRAILING_WORDS = frozenset({"soruları", "sorulari", "sınavı", "sinavi", "sınav", "sinav", "vize", "final", "hoca", "hocanın", "hocanin", "notları", "notlari", "dersi", "ders"})
_PARENTHETICAL = re.compile(r"\s*[\(\[][^\)\]]*[\)\]]\s*$")
# "<Subject words> <Name Surname> <n> - <topic>": the lecturer written into the file name.
_TITLE_NAME = re.compile(r"^(?P<lead>[^\d]+?)\s+\d{1,2}\s*[-–]")
_SUBJECT_WORDS = frozenset({
    "anatomi", "anatomy", "histoloji", "embriyoloji", "fizyoloji", "biyokimya", "biyofizik", "biyoloji", "mikrobiyoloji",
    "parazitoloji", "halk", "sağlığı", "sagligi", "tıbbi", "tibbi", "tıp", "tip", "tarihi", "etik", "biyoistatistik",
    "kanıta", "kanita", "dayalı", "dayali", "eleştirel", "elestirel", "düşünme", "dusunme", "sanat", "iletişim", "iletisim",
    "becerileri", "insan", "bilimleri", "laboratuvar", "lab", "uygulama", "kdt", "hup", "ve",
})
_TITLE_DISPLAY = {
    "prof": "Prof.", "profesör": "Prof.", "profesor": "Prof.", "doç": "Doç.", "doc": "Doç.", "doçent": "Doç.", "docent": "Doç.",
    "dr": "Dr.", "doktor": "Dr.", "yrd": "Yrd.", "yard": "Yrd.", "yardımcı": "Yrd.", "yardimci": "Yrd.", "uzm": "Uzm.",
    "uzman": "Uzm.", "arş": "Arş.", "ars": "Arş.", "araştırma": "Arş.", "arastirma": "Arş.", "öğr": "Öğr.", "ogr": "Öğr.",
    "öğretim": "Öğr.", "ogretim": "Öğr.", "gör": "Gör.", "gor": "Gör.", "görevlisi": "Gör.", "gorevlisi": "Gör.",
    "üyesi": "Üyesi", "uyesi": "Üyesi", "md": "MD", "phd": "PhD", "op": "Op.",
}
_TURKISH_LOWER = str.maketrans({"I": "ı", "İ": "i"})
_TURKISH_UPPER = str.maketrans({"i": "İ", "ı": "I"})
MENTION_PAGES = 3


@dataclass(slots=True)
class ProfessorMention:
    """A lecturer named in the material, with the key that identifies them."""

    name: str
    key: str
    line: str
    page_number: int = 0
    source: str = "page"
    complete: bool = True


def turkish_title(word: str) -> str:
    """Title-case one name token with Turkish letters ("AYŞE" → "Ayşe", "ıSMAİL" stays sane)."""
    if not word:
        return word
    if "-" in word:
        return "-".join(turkish_title(part) for part in word.split("-"))
    lowered = word.translate(_TURKISH_LOWER).lower()
    return lowered[0].translate(_TURKISH_UPPER).upper() + lowered[1:]


# A rank on its own ("Prof.", "Dr.") or a pair that only makes sense as one
# ("Öğr. Gör.", "Arş. Gör.", "Öğr. Üyesi", "Uzm. Dr."). A bare abbreviation
# such as "Arş." (araştırma) or "Yrd." (yardımcı) opens ordinary sentences.
_RANK_WORDS = frozenset({"prof", "profesör", "profesor", "doç", "doc", "doçent", "docent", "dr", "doktor", "md", "phd", "op"})
_RANK_PAIRS = (("öğr", "gör"), ("ogr", "gor"), ("öğretim", "görevlisi"), ("ogretim", "gorevlisi"), ("arş", "gör"), ("ars", "gor"), ("araştırma", "görevlisi"), ("arastirma", "gorevlisi"), ("öğr", "üyesi"), ("ogr", "uyesi"), ("öğretim", "üyesi"), ("ogretim", "uyesi"), ("uzm", "dr"), ("uzman", "dr"))


def _is_rank(titles: list[str]) -> bool:
    if any(token in _RANK_WORDS for token in titles):
        return True
    return any(first in titles and second in titles for first, second in _RANK_PAIRS)


def _split_title(line: str) -> tuple[list[str], str] | None:
    """(title tokens, remainder) when the line starts with an academic rank."""
    stripped = _PARENTHETICAL.sub("", _BULLET.sub("", line).strip()).strip()
    match = _TITLE_PATTERN.match(stripped)
    if not match:
        return None
    titles = [token.strip(".").casefold() for token in re.split(r"[.\s]+", match.group(0)) if token.strip(".")]
    if not _is_rank(titles):
        return None
    return titles, stripped[match.end() :].strip(" .:,;-–")


def academic_title_and_name(line: str) -> tuple[str, str] | None:
    """("Prof. Dr.", "Ayla Kürkçüoğlu") when the line is a person with a title.

    One to four name tokens, letters only (an initial like "A." counts), no
    role words: "Halk Sağlığı Uzmanı" after "Dr." names a job, not a person.
    """
    parts = _split_title(line)
    if parts is None:
        return None
    titles, remainder = parts
    tokens = [re.sub(r"['’].*$", "", token) for token in remainder.replace(",", " ").split() if token]
    tokens = [token for token in tokens if token]
    while tokens and tokens[-1].casefold().translate(_TURKISH_LOWER).strip(":'’") in _TRAILING_WORDS:
        tokens.pop()
    if not 1 <= len(tokens) <= 4:
        return None
    for token in tokens:
        if not _NAME_TOKEN.match(token) or token.rstrip(".").casefold().translate(_TURKISH_LOWER) in _ROLE_WORDS:
            return None
    if any(char.isdigit() for char in remainder):
        return None
    title = " ".join(dict.fromkeys(_TITLE_DISPLAY.get(token, token.capitalize() + ".") for token in titles))
    name = " ".join(token if (len(token) == 2 and token.endswith(".")) else turkish_title(token.rstrip(".")) for token in tokens)
    return title, name


def professor_key(name: str) -> str:
    """The identity behind the spellings: titles dropped, letters folded."""
    parts = _split_title(name)
    remainder = parts[1] if parts is not None else name
    tokens = [token.strip(".").strip() for token in fold(remainder).replace("-", " ").split()]
    return " ".join(token for token in tokens if token)


def same_person(key_a: str, key_b: str) -> bool:
    """Same surname and a first name that agrees, an initial counting as agreement.

    A surname the PDF text layer broke apart ("Kürkçüo lu ğ") still names the
    same person: with the same first name, the letters of the rest have to
    agree almost entirely. A lone first name matches nobody.
    """
    first_a, first_b = key_a.split(), key_b.split()
    if not first_a or not first_b:
        return False
    if key_a == key_b:
        return True
    if len(first_a) == 1 or len(first_b) == 1:
        return False
    a, b = first_a[0], first_b[0]
    first_agrees = a == b or (len(a) == 1 and b.startswith(a)) or (len(b) == 1 and a.startswith(b))
    if first_a[-1] == first_b[-1]:
        return first_agrees
    if a != b:
        return False
    rest_a, rest_b = "".join(first_a[1:]), "".join(first_b[1:])
    return len(rest_a) >= 6 and len(rest_b) >= 6 and SequenceMatcher(None, rest_a, rest_b).ratio() >= 0.9


def garbled_name(name: str) -> bool:
    """A name the text layer broke: a fragment of one or two letters that is not an initial."""
    tokens = name.split()
    return len(tokens) >= 3 and any(len(token) <= 2 and not token.endswith(".") for token in tokens[1:])


def looks_like_question_paper(text: str, question_count: int) -> bool:
    """A compiled paper, not a lecture with a few review questions at the end.

    Only a paper is cut at the lecturer headings inside it: in a lecture a line
    such as "Dr. Refik Saydam" is history, not the author of what follows.
    """
    if question_count < 5:
        return False
    lines = [line for line in str(text or "").splitlines() if line.strip()]
    if not lines:
        return False
    question_like = sum(1 for line in lines if _QUESTION_START.match(line) or _OPTION_LINE.match(line))
    return question_like / len(lines) >= 0.35


def professor_mentions(pages: Iterable[Any], *, limit_pages: int | None = MENTION_PAGES) -> list[ProfessorMention]:
    """Lecturers named on the opening pages, first mention first.

    ``limit_pages=None`` reads every page handed in (the closing pages, where
    a lecturer sometimes signs off).

    A name cut by a line break ("Dr. Hasan" / "OZAN") is joined when the next
    line is a bare surname; a name that stays a single token is kept but
    marked incomplete so the report can say so.
    """
    mentions: list[ProfessorMention] = []
    seen: set[str] = set()
    for page in list(pages)[: limit_pages or None]:
        number = int(getattr(page, "page_number", 0) or 0)
        if limit_pages and number > limit_pages:
            continue  # a later page handed in by position is still a later page
        lines = [line.strip() for line in str(getattr(page, "text", "")).splitlines()]
        for index, line in enumerate(lines):
            if not line or len(line) > 90:
                continue
            found = academic_title_and_name(line)
            if found is None:
                continue
            title, name = found
            complete = len(name.split()) >= 2
            if not complete:
                nxt = lines[index + 1].strip() if index + 1 < len(lines) else ""
                tokens = nxt.split()
                if 1 <= len(tokens) <= 2 and all(_NAME_TOKEN.match(token) and token.casefold().translate(_TURKISH_LOWER) not in _ROLE_WORDS for token in tokens) and not academic_title_and_name(nxt):
                    name = name + " " + " ".join(turkish_title(token) for token in tokens)
                    complete = True
            key = professor_key(name)
            if not key or key in seen:
                continue
            seen.add(key)
            if garbled_name(name):
                complete = False
            mentions.append(ProfessorMention(name=f"{title} {name}".strip(), key=key, line=line, page_number=int(getattr(page, "page_number", 0) or 0), source="page", complete=complete))
    return mentions


def fuller_name(candidate: str, current: str) -> bool:
    """Whether ``candidate`` says more about the person than ``current``:
    fewer initials first, then more tokens (a title counts)."""

    def initials(name: str) -> int:
        return sum(1 for token in name.split() if len(token.rstrip(".")) == 1)

    new_tokens, old_tokens = candidate.split(), current.split()
    if initials(candidate) != initials(current):
        return initials(candidate) < initials(current)
    return len(new_tokens) > len(old_tokens)


# A rank as it opens a phrase inside prose. The strict reader below decides
# whether what follows is actually a person.
_RANK_START = re.compile(
    r"\b(?:prof|profesör|profesor|doç|doc|doçent|docent|dr|doktor|yrd|yard|uzm|uzman|arş|ars|öğr|ogr|öğretim|ogretim)\b\.?",
    re.IGNORECASE,
)
_PHRASE_END = re.compile(r"[,;:\n\r\"'()\[\]\u2018\u2019\u201c\u201d]")
MENTION_TOKENS = 6
# What a described cover slide says about itself. A lecturer is read from a
# picture only there: a portrait inside a history lecture names a person too.
COVER_WORDS = ("kapak", "baslik", "başlık", "sunum", "slayt", "slide", "title")


def looks_like_cover(summary: str) -> bool:
    folded = fold(str(summary or ""))
    return any(fold(word) in folded for word in COVER_WORDS)


def mentions_in_text(text: str) -> list[ProfessorMention]:
    """Lecturers named inside prose rather than on their own line.

    A slide deck whose title page is a picture has no text to read; what the
    vision pass wrote about that page does name the lecturer, but as a
    sentence. The scan walks to each rank, takes the phrase that follows and
    hands it to the same strict reader, so nothing becomes a person here that
    would not be one on a line of its own.
    """
    body = str(text or "")
    found: list[ProfessorMention] = []
    seen: set[str] = set()
    for match in _RANK_START.finditer(body):
        window = _PHRASE_END.split(body[match.start() : match.start() + 90])[0]
        tokens = window.split()[:MENTION_TOKENS]
        # The name ends where the sentence resumes: Turkish prose continues in
        # lower case ("Prof. Dr. Özgül Kısa adı yazıyor"), so a lower-case token
        # is the boundary rather than part of the name.
        kept: list[str] = []
        for token in tokens:
            letters = token.strip(".")
            if kept and letters[:1].islower():
                break
            kept.append(token)
        for size in range(len(kept), 1, -1):
            parsed = academic_title_and_name(" ".join(kept[:size]))
            if parsed is None:
                continue
            title, name = parsed
            # In prose only a full name is safe: a lone first name after a rank
            # is far more often the start of a sentence than a lecturer.
            if len(name.split()) < 2 or garbled_name(name):
                break
            key = professor_key(name)
            if key and key not in seen:
                seen.add(key)
                found.append(ProfessorMention(name=f"{title} {name}", key=key, line=window.strip()[:90], source="visual", complete=True))
            break
    return found


def mention_from_owner(owner: str) -> ProfessorMention | None:
    """The lecturer an exam export names as the question's owner ("RABET GÖZİL")."""
    parsed = academic_title_and_name("Dr. " + str(owner or "").strip())
    if parsed is None:
        return None
    _title, name = parsed
    if len(name.split()) < 2:
        return None
    return ProfessorMention(name=name, key=professor_key(name), line=str(owner).strip(), source="question")


def professor_from_title(title: str) -> ProfessorMention | None:
    """The lecturer written into a file name such as "Mikrobiyoloji Ülker Çuhacı 2 - Mantarlar"."""
    match = _TITLE_NAME.match(str(title or "").strip())
    if not match:
        return None
    tokens = [token for token in match.group("lead").replace("_", " ").split() if token]
    while tokens and fold(tokens[0]) in _SUBJECT_WORDS:
        tokens.pop(0)
    if not 2 <= len(tokens) <= 4:
        return None
    if any(not _NAME_TOKEN.match(token) or not token[0].isupper() or fold(token) in _SUBJECT_WORDS for token in tokens):
        return None
    name = " ".join(turkish_title(token) for token in tokens)
    return ProfessorMention(name=name, key=professor_key(name), line=title, page_number=0, source="title")


def split_by_professor(text: str) -> list[tuple[ProfessorMention | None, str]]:
    """Cut a compiled question file at the headings that name a lecturer.

    Each section is attributed to the heading above it; text before the first
    heading has no lecturer. A heading is a short line that is nothing but a
    title and a name, so a name mentioned inside a question does not cut it.
    """
    sections: list[tuple[ProfessorMention | None, list[str]]] = [(None, [])]
    for line in str(text or "").replace("\r\n", "\n").split("\n"):
        stripped = line.strip()
        found = academic_title_and_name(stripped) if 0 < len(stripped) <= 80 and not _QUESTION_START.match(stripped) else None
        if found is not None and len(found[1].split()) >= 2:
            title, name = found
            sections.append((ProfessorMention(name=f"{title} {name}", key=professor_key(name), line=stripped, source="heading"), []))
            continue
        sections[-1][1].append(line)
    return [(mention, "\n".join(lines)) for mention, lines in sections if "\n".join(lines).strip() or mention is not None]


def imported_question(parsed: ParsedQuestion, *, subject: str, professor_id: str | None, topic_id: str | None = None, document_id: str | None = None, origin: str = QuestionOrigin.IMPORTED_EXAM, page_number: int | None = None, image_ref: str | None = None, tags: Iterable[str] = ()) -> Question:
    question = Question(
        question_id=new_id("q"),
        subject=subject,
        stem=parsed.stem,
        options=[QuestionOption(key=key, text=text) for key, text in parsed.options],
        correct_key=parsed.answer_key,
        topic_id=topic_id,
        origin=origin,
        professor_id=professor_id,
        image_ref=image_ref,
        tags=list(dict.fromkeys(["imported", *tags]))[:20],
        metadata={"number": parsed.number, "has_image": parsed.has_image, "source_document_id": document_id, **({"page_number": page_number} if page_number else {})},
    )
    return question


# ---------------------------------------------------------------------------
# style features
# ---------------------------------------------------------------------------


def _folded_all(question: Question) -> str:
    return fold(question.stem + " " + " ".join(option.text for option in question.options))


def _has(text: str, phrases: Iterable[str]) -> bool:
    padded = f" {text} "
    return any(f" {phrase}" in padded or phrase in padded for phrase in phrases)


def _negative(question: Question) -> bool:
    return _has(fold(question.stem), ("degildir", "yanlistir", "hangisi yanlis", "dogru degildir", "haric", "except", "not true", "is not", "false", "yanlis olan", "bulunmaz", "yer almaz", "gorulmez", "olmayan", "degil", "yapmaz", "gecmez"))


def _which_true(question: Question) -> bool:
    return _has(fold(question.stem), ("dogrudur", "dogru olan", "hangisi dogru", "which is true", "which is correct", "dogru olarak", "dogrudur?"))


def _clinical(question: Question) -> bool:
    return _has(fold(question.stem), ("yasinda", "hasta", "basvur", "sikayet", "muayene", "year old", "year-old", "patient", "presents", "complain", "kaza", "travma", "kirik"))


def _multi_statement(question: Question) -> bool:
    return len(_MULTI_STATEMENT.findall(fold(question.stem))) >= 2 or any(_has(fold(option.text), ("i ve ii", "i, ii", "ii ve iii", "i and ii", "yalniz i", "only i")) for option in question.options)


def _matching(question: Question) -> bool:
    return _has(fold(question.stem), ("eslestir", "match", "eslestirme"))


def _latin_heavy(question: Question) -> bool:
    return latin_density(question.stem + " " + " ".join(option.text for option in question.options)) >= 0.25


def _numeric(question: Question) -> bool:
    body = re.sub(r"^\s*\d+\s*[.)]", "", question.stem)
    return bool(re.search(r"\d", body))


def _definition(question: Question) -> bool:
    return _has(fold(question.stem), ("nedir", "tanimi", "ne denir", "hangi terim", "adi nedir", "is called", "term for", "defined as", "hangisidir", "olarak adlandirilir"))


def _recognition(question: Question) -> bool:
    return bool(question.metadata.get("has_image")) or _has(fold(question.stem), _IMAGE_WORDS + ("hangi yapi", "gorulen yapi"))


def _mechanism(question: Question) -> bool:
    return _has(fold(question.stem), ("mekanizma", "nasil", "neden", "sebebi", "sonucu", "mechanism", "why", "how", "leads to", "yol acar", "sonucunda"))


def _exception(question: Question) -> bool:
    return _has(fold(question.stem), ("haric", "except", "disinda"))


def _long_stem(question: Question) -> bool:
    return len(tokens(question.stem)) > 25


def _short_stem(question: Question) -> bool:
    return len(tokens(question.stem)) <= 8


def _similar_options(question: Question) -> bool:
    texts = [option.text for option in question.options]
    if len(texts) < 3:
        return False
    scores = [similarity(texts[i], texts[j]) for i in range(len(texts)) for j in range(i + 1, len(texts))]
    return bool(scores) and statistics.mean(scores) >= 0.2


def _five_options(question: Question) -> bool:
    return len(question.options) == 5


FEATURES: list[tuple[str, str, Callable[[Question], bool]]] = [
    ("negative_stem", "Olumsuz kök (“değildir / yanlıştır / hariç”)", _negative),
    ("which_true", "“Hangisi doğrudur” kökü", _which_true),
    ("clinical_vignette", "Klinik senaryo", _clinical),
    ("multi_statement", "Çok önermeli (I, II, III) soru", _multi_statement),
    ("matching", "Eşleştirme", _matching),
    ("latin_terminology", "Yoğun Latince terminoloji", _latin_heavy),
    ("numeric_fact", "Sayısal bilgi", _numeric),
    ("definition", "Tanım / terim sorusu", _definition),
    ("structure_recognition", "Yapı tanıma / şekil", _recognition),
    ("mechanism", "Mekanizma / neden-sonuç", _mechanism),
    ("exception", "“Hariç” tipi istisna", _exception),
    ("long_stem", "Uzun kök (>25 kelime)", _long_stem),
    ("short_stem", "Kısa doğrudan hatırlama (≤8 kelime)", _short_stem),
    ("similar_distractors", "Birbirine çok benzeyen çeldiriciler", _similar_options),
    ("five_options", "Beş şıklı", _five_options),
]

FEATURE_DIRECTIVES: dict[str, str] = {
    "negative_stem": "use negative stems ('… değildir', 'hangisi yanlıştır', '… hariç')",
    "which_true": "ask 'hangisi doğrudur' style stems",
    "clinical_vignette": "open with a short clinical vignette (age, presentation) before the question",
    "multi_statement": "use multi-statement items (I, II, III with combinations as options)",
    "matching": "include matching-type items",
    "latin_terminology": "keep stems and options terminology-heavy with official Latin names",
    "numeric_fact": "test numeric facts (levels, counts, angles, percentages)",
    "definition": "ask definition/term questions ('… ne denir', 'hangi terim')",
    "structure_recognition": "ask structure-recognition questions (describe the landmark in words when no image is available)",
    "mechanism": "ask mechanism / cause-effect questions",
    "exception": "use '… hariç' exception stems",
    "long_stem": "write long, detailed stems (over 25 words)",
    "short_stem": "write short direct recall stems (8 words or fewer)",
    "similar_distractors": "make distractors very similar to each other (neighbouring structures, near-identical terms)",
    "five_options": "use five options",
}


def _level(ratio: float) -> str:
    if ratio <= 0.10:
        return "low"
    if ratio <= 0.30:
        return "moderate"
    if ratio <= 0.60:
        return "high"
    return "very_high"


LEVEL_LABELS_TR = {"low": "Düşük", "moderate": "Orta", "high": "Yüksek", "very_high": "Çok yüksek"}


def confidence_for(sample_size: int) -> str:
    if sample_size <= 0:
        return EvidenceSupport.NONE
    if sample_size < SAMPLE_LIMITED:
        return EvidenceSupport.LIMITED
    if sample_size < SAMPLE_MODERATE:
        return EvidenceSupport.MODERATE
    return EvidenceSupport.HIGH


CONFIDENCE_LABELS_TR = {
    EvidenceSupport.NONE: "Veri yok",
    EvidenceSupport.LIMITED: "Sınırlı",
    EvidenceSupport.MODERATE: "Orta",
    EvidenceSupport.HIGH: "Yüksek",
}


class StyleProfiler:
    def profile(self, name: str, questions: list[Question], *, subject: str | None = None, profile_id: str | None = None, notes: str = "") -> ProfessorProfile:
        sample = [question for question in questions if question.stem.strip()]
        size = len(sample)
        features: list[StyleFeature] = []
        for feature_id, label, predicate in FEATURES:
            observed = sum(1 for question in sample if predicate(question))
            ratio = observed / size if size else 0.0
            features.append(StyleFeature(feature_id=feature_id, label_tr=label, observed=observed, total=size, level=_level(ratio) if size else "low"))
        average_options = round(statistics.mean(len(question.options) for question in sample), 2) if sample else 0.0
        average_words = round(statistics.mean(len(tokens(question.stem)) for question in sample), 1) if sample else 0.0
        distribution: dict[str, int] = {}
        for question in sample:
            if question.correct_key:
                distribution[question.correct_key] = distribution.get(question.correct_key, 0) + 1
        confidence = confidence_for(size)
        keyed = sum(distribution.values())
        basis = self.basis_text(size, keyed, confidence)
        return ProfessorProfile(
            profile_id=profile_id or new_id("prof"),
            name=name.strip()[:80] or "Hoca",
            subject=subject,
            question_ids=[question.question_id for question in sample],
            sample_size=size,
            features=features,
            average_options=average_options,
            average_stem_words=average_words,
            answer_distribution=distribution,
            confidence=confidence,
            basis=basis,
            updated_at=utc_now(),
            notes=notes,
        )

    @staticmethod
    def basis_text(size: int, keyed: int, confidence: str) -> str:
        if size == 0:
            return "Henüz soru yüklenmedi; profil boş."
        label = CONFIDENCE_LABELS_TR.get(confidence, confidence)
        text = f"Profil {size} soruya dayanıyor; güven {label.lower()}."
        if size < SAMPLE_LIMITED:
            text += " Örneklem küçük: bu oranlar eğilim değil, sadece gözlemdir."
        if keyed < size:
            text += f" {size - keyed} sorunun cevap anahtarı yok."
        return text

    @staticmethod
    def directive(profile: ProfessorProfile) -> str | None:
        """Generation guidance made only of observed, high-enough ratios."""
        if profile.sample_size == 0:
            return None
        lines = [
            f"Professor style profile '{profile.name}' (based on {profile.sample_size} real exam questions; confidence "
            f"{profile.confidence}). Imitate the STYLE, never the content:"
        ]
        strong = [feature for feature in profile.features if feature.level in {"high", "very_high"} and feature.observed >= 2]
        moderate = [feature for feature in profile.features if feature.level == "moderate" and feature.observed >= 3]
        for feature in strong:
            directive = FEATURE_DIRECTIVES.get(feature.feature_id)
            if directive:
                lines.append(f"- In about {round(100 * feature.ratio)}% of questions ({feature.observed}/{feature.total}): {directive}.")
        for feature in moderate:
            directive = FEATURE_DIRECTIVES.get(feature.feature_id)
            if directive:
                lines.append(f"- Occasionally ({feature.observed}/{feature.total}): {directive}.")
        if profile.average_options:
            lines.append(f"- Average option count observed: {profile.average_options}; average stem length {profile.average_stem_words} words.")
        # The caveat qualifies guidance rather than being guidance, so whether
        # anything usable was said has to be decided before it is appended.
        has_guidance = len(lines) > 1
        if profile.confidence == EvidenceSupport.LIMITED:
            lines.append("- The sample is small; treat these as loose tendencies, keep questions varied.")
        if not has_guidance:
            lines.append("- No strong pattern stands out; write varied first-year exam questions of the same subject.")
        return "\n".join(lines)

    @staticmethod
    def to_dict(profile: ProfessorProfile) -> dict[str, Any]:
        return {
            "profile_id": profile.profile_id,
            "name": profile.name,
            "subject": profile.subject,
            "subject_label": SUBJECT_LABELS_TR.get(profile.subject or "", profile.subject or "Belirsiz"),
            "sample_size": profile.sample_size,
            "question_ids": list(profile.question_ids),
            "features": [
                {
                    "feature_id": feature.feature_id,
                    "label": feature.label_tr,
                    "observed": feature.observed,
                    "total": feature.total,
                    "ratio": round(feature.ratio, 3),
                    "level": feature.level,
                    "level_label": LEVEL_LABELS_TR.get(feature.level, feature.level),
                }
                for feature in profile.features
            ],
            "average_options": profile.average_options,
            "average_stem_words": profile.average_stem_words,
            "answer_distribution": dict(profile.answer_distribution),
            "confidence": profile.confidence,
            "confidence_label": CONFIDENCE_LABELS_TR.get(profile.confidence, profile.confidence),
            "basis": profile.basis,
            "updated_at": profile.updated_at.isoformat(),
            "notes": profile.notes,
        }
