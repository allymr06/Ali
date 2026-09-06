"""Professor question import and evidence-based style profiling.

Imported questions are parsed deterministically (numbered stems, lettered
options, answer keys only when the text states them). A style profile is
a set of observed ratios with sample sizes and an honest confidence; the
generation directive it yields only repeats what was actually seen.
"""

from __future__ import annotations

import re
import statistics
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
_ANSWER_INLINE = re.compile(r"(?:cevap|yan[ıi]t|answer|do[gğ]ru\s*cevap|do[gğ]ru\s*yan[ıi]t|key)\s*[:\-]?\s*\(?([A-Fa-f])\)?", re.IGNORECASE)
# A whole line that states nothing but the answer. Exam prose mentions
# "cevap" freely — inside an option, in a note beside the stem — and such a
# line names no key, so anything around the statement disqualifies it.
_ANSWER_ONLY = re.compile(r"^\(?(?:do[gğ]ru\s+)?(?:cevap|yan[ıi]t|answer|key)\)?\s*[:\-]?\s*\(?([A-Fa-f])\)?\.?$", re.IGNORECASE)
_ANSWER_TABLE = re.compile(r"\b(\d{1,3})\s*[-.:)]\s*([A-Fa-f])\b")
_ANSWER_HEADER = re.compile(r"(cevap anahtar|yan[ıi]t anahtar|answer key|cevaplar|answers)", re.IGNORECASE)
_MULTI_STATEMENT = re.compile(r"(?:^|\s|\()(i{1,3}|iv|v)\s*[.)\-]", re.IGNORECASE)
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


@dataclass(slots=True)
class ImportResult:
    questions: list[ParsedQuestion]
    answer_key_found: bool
    notes: list[str] = field(default_factory=list)


class QuestionImportParser:
    """Deterministic extraction from exam-like text."""

    def parse(self, text: str) -> ImportResult:
        normalized = str(text or "").replace("\r\n", "\n").replace("\r", "\n")
        lines = normalized.split("\n")
        answer_map, body_lines = self._split_answer_table(lines)
        blocks = self._blocks(body_lines)
        questions: list[ParsedQuestion] = []
        notes: list[str] = []
        for number, block_lines in blocks:
            parsed = self._parse_block(number, block_lines)
            if parsed is None:
                continue
            table_key = answer_map.get(number)
            # A mistyped table entry ("3-F" against five options) names nothing
            # to index, so it is dropped exactly as a stray inline key is.
            if parsed.answer_key is None and table_key and any(key == table_key for key, _ in parsed.options):
                parsed.answer_key = table_key
            questions.append(parsed)
        found = any(question.answer_key for question in questions)
        if questions and not found:
            notes.append("Metinde cevap anahtarı bulunamadı; sorular anahtarsız kaydedildi.")
        missing = [question.number for question in questions if len(question.options) < 2]
        if missing:
            notes.append(f"Seçenekleri ayrıştırılamayan sorular: {', '.join(missing[:12])}.")
        return ImportResult(questions=[question for question in questions if len(question.options) >= 2], answer_key_found=found, notes=notes)

    @staticmethod
    def _split_answer_table(lines: list[str]) -> tuple[dict[str, str], list[str]]:
        """A trailing 'Cevap anahtarı' block or a dense run of 'n-X' pairs."""
        answer_map: dict[str, str] = {}
        body = list(lines)
        for index, line in enumerate(lines):
            if _ANSWER_HEADER.search(line) and len(line.strip()) < 40:
                tail = "\n".join(lines[index:])
                pairs = _ANSWER_TABLE.findall(tail)
                if len(pairs) >= 3:
                    answer_map = {number: key.upper() for number, key in pairs}
                    body = lines[:index]
                    break
        if not answer_map:
            tail = "\n".join(lines[-12:])
            pairs = _ANSWER_TABLE.findall(tail)
            if len(pairs) >= 5 and len(tail) < 400:
                answer_map = {number: key.upper() for number, key in pairs}
                body = lines[:-12]
        return answer_map, body

    @staticmethod
    def _blocks(lines: list[str]) -> list[tuple[str, list[str]]]:
        blocks: list[tuple[str, list[str]]] = []
        current: tuple[str, list[str]] | None = None
        for line in lines:
            match = _QUESTION_START.match(line)
            if match and not _OPTION_LINE.match(line):
                if current is not None:
                    blocks.append(current)
                current = (match.group(1), [match.group(2)])
                continue
            if current is not None:
                current[1].append(line)
        if current is not None:
            blocks.append(current)
        return blocks

    def _parse_block(self, number: str, lines: list[str]) -> ParsedQuestion | None:
        stem_parts: list[str] = []
        options: list[tuple[str, str]] = []
        answer: str | None = None
        for line in lines:
            stripped = line.strip()
            if not stripped:
                continue
            option = _OPTION_LINE.match(stripped)
            if option:
                options.append((option.group(1).upper(), option.group(2).strip()))
                continue
            stated_answer = _ANSWER_ONLY.match(stripped)
            if stated_answer:
                answer = stated_answer.group(1).upper()
                continue
            if options:
                # Continuation of the last option.
                key, text = options[-1]
                options[-1] = (key, f"{text} {stripped}")
            else:
                stem_parts.append(stripped)
        stem = " ".join(stem_parts).strip()
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
        stem_answer = _ANSWER_INLINE.search(stem)
        if stem_answer and answer is None and len(stem) - stem_answer.start() < 20:
            answer = stem_answer.group(1).upper()
            stem = stem[: stem_answer.start()].strip()
        folded = fold(stem)
        has_image = any(word in folded for word in _IMAGE_WORDS)
        return ParsedQuestion(number=number, stem=stem, options=cleaned, answer_key=answer if answer and any(key == answer for key, _ in cleaned) else None, has_image=has_image)

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


def imported_question(parsed: ParsedQuestion, *, subject: str, professor_id: str | None, topic_id: str | None = None, document_id: str | None = None) -> Question:
    question = Question(
        question_id=new_id("q"),
        subject=subject,
        stem=parsed.stem,
        options=[QuestionOption(key=key, text=text) for key, text in parsed.options],
        correct_key=parsed.answer_key,
        topic_id=topic_id,
        origin=QuestionOrigin.IMPORTED_EXAM,
        professor_id=professor_id,
        tags=["imported"],
        metadata={"number": parsed.number, "has_image": parsed.has_image, "source_document_id": document_id},
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
