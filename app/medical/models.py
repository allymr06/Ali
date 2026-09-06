"""Domain models of the Medical Academy.

Everything the study layer persists or passes across the bridge is one
of these plain dataclasses. They carry no behaviour that needs a model;
scoring, mastery and validation live in their own modules so they can
be tested without a provider.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, fields, is_dataclass
from datetime import datetime
from enum import IntEnum, StrEnum
from typing import Any
from uuid import uuid4

from app.core.time import utc_now


# ---------------------------------------------------------------------------
# vocabularies
# ---------------------------------------------------------------------------


class Subject(StrEnum):
    ANATOMY = "anatomy"
    HISTOLOGY = "histology"
    MICROBIOLOGY = "microbiology"
    BIOCHEMISTRY = "biochemistry"
    BIOPHYSICS = "biophysics"
    PHYSIOLOGY = "physiology"
    BIOLOGY = "biology"


SUBJECT_LABELS_TR: dict[str, str] = {
    Subject.ANATOMY: "Anatomi",
    Subject.HISTOLOGY: "Histoloji",
    Subject.MICROBIOLOGY: "Mikrobiyoloji",
    Subject.BIOCHEMISTRY: "Biyokimya",
    Subject.BIOPHYSICS: "Biyofizik",
    Subject.PHYSIOLOGY: "Fizyoloji",
    Subject.BIOLOGY: "Biyoloji",
}


class DepthLevel(StrEnum):
    """How deep an explanation goes; the user switches it freely."""

    SIMPLE = "simple"
    STANDARD = "standard"
    DETAILED = "detailed"
    EXAM = "exam"
    RAPID = "rapid"


DEPTH_LABELS_TR: dict[str, str] = {
    DepthLevel.SIMPLE: "Basit",
    DepthLevel.STANDARD: "Standart",
    DepthLevel.DETAILED: "Ayrıntılı",
    DepthLevel.EXAM: "Sınav modu",
    DepthLevel.RAPID: "Hızlı tekrar",
}


class StudyMode(StrEnum):
    TEACH = "teach"
    SIMPLIFY = "simplify"
    SHORT_NOTES = "short_notes"
    HIGH_YIELD = "high_yield"
    COMPARE = "compare"
    RAPID_REVIEW = "rapid_review"
    ORAL_EXAM = "oral_exam"
    QUIZ = "quiz"
    EXAM_SIMULATION = "exam_simulation"
    WEAK_AREAS = "weak_areas"


STUDY_MODE_LABELS_TR: dict[str, str] = {
    StudyMode.TEACH: "Öğret",
    StudyMode.SIMPLIFY: "Basitleştir",
    StudyMode.SHORT_NOTES: "Kısa not",
    StudyMode.HIGH_YIELD: "Yüksek verim",
    StudyMode.COMPARE: "Karşılaştır",
    StudyMode.RAPID_REVIEW: "Hızlı tekrar",
    StudyMode.ORAL_EXAM: "Sözlü sınav",
    StudyMode.QUIZ: "Quiz",
    StudyMode.EXAM_SIMULATION: "Sınav simülasyonu",
    StudyMode.WEAK_AREAS: "Zayıf alanlar",
}


class KnowledgeSource(StrEnum):
    """Which material grounds an answer."""

    SELECTED_DOCUMENTS = "selected_documents"
    COURSE_MATERIAL = "course_material"
    COURSE_AND_JARVIS = "course_and_jarvis"
    STANDARD = "standard"


KNOWLEDGE_SOURCE_LABELS_TR: dict[str, str] = {
    KnowledgeSource.SELECTED_DOCUMENTS: "Yalnız seçili belgeler",
    KnowledgeSource.COURSE_MATERIAL: "Ders materyali",
    KnowledgeSource.COURSE_AND_JARVIS: "Ders materyali + JARVIS bilgisi",
    KnowledgeSource.STANDARD: "Standart tıp bilgisi",
}


class KnowledgePriority(StrEnum):
    """Professor priority for exam preparation."""

    STRICT_LECTURE = "strict_lecture"
    LECTURE_FIRST = "lecture_first"
    BALANCED = "balanced"
    STANDARD_FIRST = "standard_first"


KNOWLEDGE_PRIORITY_LABELS_TR: dict[str, str] = {
    KnowledgePriority.STRICT_LECTURE: "Yalnız ders materyali",
    KnowledgePriority.LECTURE_FIRST: "Önce ders materyali",
    KnowledgePriority.BALANCED: "Dengeli",
    KnowledgePriority.STANDARD_FIRST: "Önce standart tıp bilgisi",
}


class QuestionType(StrEnum):
    SINGLE_BEST_ANSWER = "single_best_answer"
    TRUE_FALSE = "true_false"
    MATCHING = "matching"
    ASSERTION_REASON = "assertion_reason"
    MULTI_STATEMENT = "multi_statement"


class QuestionOrigin(StrEnum):
    GENERATED = "generated"
    IMPORTED_EXAM = "imported_exam"
    MANUAL = "manual"
    LECTURE_DERIVED = "lecture_derived"


class Difficulty(IntEnum):
    EASY = 1
    EASY_MEDIUM = 2
    MEDIUM = 3
    MEDIUM_HARD = 4
    HARD = 5


DIFFICULTY_LABELS_TR: dict[int, str] = {
    1: "Kolay",
    2: "Kolay-orta",
    3: "Orta",
    4: "Orta-zor",
    5: "Zor",
}


class DocumentStatus(StrEnum):
    PENDING = "pending"
    READING = "reading"
    EXTRACTING = "extracting"
    ANALYZING_VISUALS = "analyzing_visuals"
    INDEXING = "indexing"
    READY = "ready"
    FAILED = "failed"


DOCUMENT_STATUS_LABELS_TR: dict[str, str] = {
    DocumentStatus.PENDING: "Bekliyor",
    DocumentStatus.READING: "Belge okunuyor",
    DocumentStatus.EXTRACTING: "Sayfalar çıkarılıyor",
    DocumentStatus.ANALYZING_VISUALS: "Şekiller inceleniyor",
    DocumentStatus.INDEXING: "Kavramlar dizinleniyor",
    DocumentStatus.READY: "Hazır",
    DocumentStatus.FAILED: "Başarısız",
}


class ComparisonCategory(StrEnum):
    """How a lecture statement relates to standard knowledge."""

    CONSISTENT = "consistent"
    SIMPLIFIED = "simplified"
    INCOMPLETE = "incomplete"
    POTENTIALLY_MISLEADING = "potentially_misleading"
    POSSIBLY_INCORRECT = "possibly_incorrect"
    TERMINOLOGY_DIFFERENCE = "terminology_difference"


COMPARISON_LABELS_TR: dict[str, str] = {
    ComparisonCategory.CONSISTENT: "Tutarlı",
    ComparisonCategory.SIMPLIFIED: "Basitleştirilmiş",
    ComparisonCategory.INCOMPLETE: "Eksik",
    ComparisonCategory.POTENTIALLY_MISLEADING: "Yanıltıcı olabilir",
    ComparisonCategory.POSSIBLY_INCORRECT: "Muhtemelen hatalı",
    ComparisonCategory.TERMINOLOGY_DIFFERENCE: "Terminoloji farkı",
}


class MasteryLevel(StrEnum):
    UNKNOWN = "unknown"
    WEAK = "weak"
    MODERATE = "moderate"
    STRONG = "strong"


MASTERY_LABELS_TR: dict[str, str] = {
    MasteryLevel.UNKNOWN: "Bilinmiyor",
    MasteryLevel.WEAK: "Zayıf",
    MasteryLevel.MODERATE: "Orta",
    MasteryLevel.STRONG: "Güçlü",
}


class EvidenceSupport(StrEnum):
    """Calibrated words instead of invented percentages."""

    HIGH = "high"
    MODERATE = "moderate"
    LIMITED = "limited"
    NONE = "none"


SUPPORT_LABELS_TR: dict[str, str] = {
    EvidenceSupport.HIGH: "Yüksek destek",
    EvidenceSupport.MODERATE: "Orta destek",
    EvidenceSupport.LIMITED: "Sınırlı kanıt",
    EvidenceSupport.NONE: "Kanıt yok",
}


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def new_id(prefix: str) -> str:
    return f"{prefix}-{uuid4().hex[:12]}"


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _parse_datetime(value: Any) -> datetime | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(str(value))
    except ValueError:
        return None


def to_plain(value: Any) -> Any:
    """Recursively turn a model into JSON-compatible data."""
    if is_dataclass(value) and not isinstance(value, type):
        return {
            item.name: to_plain(getattr(value, item.name))
            for item in fields(value)
        }
    if isinstance(value, dict):
        return {str(key): to_plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [to_plain(item) for item in value]
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, (StrEnum, IntEnum)):
        return value.value
    return value


def dumps(value: Any) -> str:
    return json.dumps(to_plain(value), ensure_ascii=False, sort_keys=True)


# ---------------------------------------------------------------------------
# curriculum and knowledge
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class Topic:
    """One node of a subject's hierarchy; data, not navigation code."""

    topic_id: str
    subject: str
    title_tr: str
    title_en: str
    parent_id: str | None = None
    keywords: list[str] = field(default_factory=list)
    concept_ids: list[str] = field(default_factory=list)
    order: int = 0

    @property
    def depth_path(self) -> str:
        return self.topic_id


@dataclass(slots=True)
class Concept:
    """A learnable unit that mastery is tracked against."""

    concept_id: str
    subject: str
    name: str
    topic_id: str | None = None
    aliases: list[str] = field(default_factory=list)
    kind: str = "concept"
    relations: list[dict[str, str]] = field(default_factory=list)


@dataclass(slots=True)
class Landmark:
    landmark_id: str
    latin: str
    turkish: str
    note: str = ""


@dataclass(slots=True)
class AnatomyStructure:
    """A curated anatomical structure with correct Latin nomenclature."""

    structure_id: str
    canonical: str
    kind: str
    region: str
    turkish: str
    english: str
    parent_id: str | None = None
    abbreviations: list[str] = field(default_factory=list)
    synonyms: list[str] = field(default_factory=list)
    landmarks: list[Landmark] = field(default_factory=list)
    facts: dict[str, Any] = field(default_factory=dict)
    relations: list[dict[str, str]] = field(default_factory=list)
    topic_id: str | None = None
    concept_id: str | None = None
    source: str = ""


# ---------------------------------------------------------------------------
# documents
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class SourceReference:
    """Where a claim came from: never invented, always page-anchored."""

    document_id: str
    page_number: int
    chunk_id: str | None = None
    quote: str = ""
    title: str = ""

    def label(self) -> str:
        name = self.title or self.document_id
        return f"{name}, s. {self.page_number}"


@dataclass(slots=True)
class StudyDocument:
    document_id: str
    title: str
    file_name: str
    sha256: str
    kind: str = "pdf"
    page_count: int = 0
    subject: str | None = None
    topic_ids: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    status: str = DocumentStatus.PENDING
    status_detail: str = ""
    error: str | None = None
    stored_path: str | None = None
    professor_id: str | None = None
    visual_pages_analyzed: int = 0
    visual_pages_pending: int = 0
    chunk_count: int = 0
    summary: str = ""
    key_terms: list[str] = field(default_factory=list)
    imported_at: datetime = field(default_factory=utc_now)
    indexed_at: datetime | None = None


@dataclass(slots=True)
class DocumentPage:
    document_id: str
    page_number: int
    text: str = ""
    headings: list[str] = field(default_factory=list)
    image_area_ratio: float = 0.0
    image_count: int = 0
    path_count: int = 0
    visual_summary: str = ""
    visual_labels: list[str] = field(default_factory=list)
    visual_status: str = "not_needed"

    @property
    def char_count(self) -> int:
        return len(self.text)

    @property
    def needs_visual_analysis(self) -> bool:
        return self.visual_status == "pending"


@dataclass(slots=True)
class DocumentChunk:
    chunk_id: str
    document_id: str
    page_number: int
    index_in_page: int
    text: str
    heading: str = ""
    kind: str = "text"
    start_char: int = 0
    end_char: int = 0


@dataclass(slots=True)
class EvidenceBlock:
    """A retrieved passage with its citation and neighbouring context."""

    reference: SourceReference
    text: str
    score: float
    kind: str = "text"


@dataclass(slots=True)
class ComparisonFinding:
    statement: str
    category: str
    explanation: str
    standard_view: str = ""
    reference: SourceReference | None = None
    support: str = EvidenceSupport.MODERATE


# ---------------------------------------------------------------------------
# notes and questions
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class StudyNote:
    note_id: str
    title: str
    content: str
    subject: str | None = None
    topic_id: str | None = None
    mode: str = StudyMode.SHORT_NOTES
    references: list[SourceReference] = field(default_factory=list)
    created_at: datetime = field(default_factory=utc_now)


@dataclass(slots=True)
class QuestionOption:
    key: str
    text: str
    concept: str = ""
    explanation: str = ""


@dataclass(slots=True)
class Question:
    question_id: str
    subject: str
    stem: str
    options: list[QuestionOption]
    correct_key: str | None
    topic_id: str | None = None
    concept_ids: list[str] = field(default_factory=list)
    difficulty: int = Difficulty.MEDIUM
    question_type: str = QuestionType.SINGLE_BEST_ANSWER
    explanation: str = ""
    references: list[SourceReference] = field(default_factory=list)
    origin: str = QuestionOrigin.GENERATED
    professor_id: str | None = None
    image_ref: str | None = None
    tags: list[str] = field(default_factory=list)
    language: str = "tr"
    created_at: datetime = field(default_factory=utc_now)
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def has_answer_key(self) -> bool:
        return bool(self.correct_key)

    def option(self, key: str) -> QuestionOption | None:
        wanted = str(key or "").strip().upper()
        for option in self.options:
            if option.key.upper() == wanted:
                return option
        return None


@dataclass(slots=True)
class ExamConfig:
    subjects: list[str] = field(default_factory=list)
    topic_ids: list[str] = field(default_factory=list)
    document_ids: list[str] = field(default_factory=list)
    page_from: int = 0
    page_to: int = 0
    question_count: int = 10
    option_count: int = 5
    difficulty: int = Difficulty.MEDIUM
    professor_id: str | None = None
    knowledge_priority: str = KnowledgePriority.BALANCED
    timed_seconds: int = 0
    immediate_feedback: bool = False
    answers_at_end: bool = True
    randomize: bool = True
    weak_emphasis: bool = False
    question_type: str = QuestionType.SINGLE_BEST_ANSWER
    include_images: bool = False
    one_at_a_time: bool = True
    title: str = ""
    wrong_only: bool = False


@dataclass(slots=True)
class Exam:
    exam_id: str
    title: str
    config: ExamConfig
    question_ids: list[str]
    status: str = "ready"
    mode: str = "study"
    created_at: datetime = field(default_factory=utc_now)
    started_at: datetime | None = None
    finished_at: datetime | None = None
    generation_notes: list[str] = field(default_factory=list)


@dataclass(slots=True)
class QuestionAttempt:
    question_id: str
    answer_key: str | None
    correct: bool | None
    answered_at: datetime = field(default_factory=utc_now)
    elapsed_seconds: float | None = None
    flagged: bool = False


@dataclass(slots=True)
class ExamAttempt:
    attempt_id: str
    exam_id: str
    started_at: datetime = field(default_factory=utc_now)
    finished_at: datetime | None = None
    answers: dict[str, QuestionAttempt] = field(default_factory=dict)
    score: float | None = None
    analysis: dict[str, Any] = field(default_factory=dict)
    current_index: int = 0


@dataclass(slots=True)
class ConceptMastery:
    concept_id: str
    subject: str = ""
    attempts: int = 0
    correct: int = 0
    recent: list[bool] = field(default_factory=list)
    streak: int = 0
    level: str = MasteryLevel.UNKNOWN
    last_attempt_at: datetime | None = None
    next_review_at: datetime | None = None
    reason: str = ""
    confusions: dict[str, int] = field(default_factory=dict)

    @property
    def accuracy(self) -> float:
        return self.correct / self.attempts if self.attempts else 0.0


# ---------------------------------------------------------------------------
# professor style
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class StyleFeature:
    feature_id: str
    label_tr: str
    observed: int
    total: int
    level: str
    detail: str = ""

    @property
    def ratio(self) -> float:
        return self.observed / self.total if self.total else 0.0


@dataclass(slots=True)
class ProfessorProfile:
    profile_id: str
    name: str
    subject: str | None = None
    question_ids: list[str] = field(default_factory=list)
    sample_size: int = 0
    features: list[StyleFeature] = field(default_factory=list)
    average_options: float = 0.0
    average_stem_words: float = 0.0
    answer_distribution: dict[str, int] = field(default_factory=dict)
    confidence: str = EvidenceSupport.NONE
    basis: str = ""
    updated_at: datetime = field(default_factory=utc_now)
    notes: str = ""


# ---------------------------------------------------------------------------
# session
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class StudySession:
    """The persistent study state shared by the page, the chat and voice."""

    session_id: str = "default"
    subject: str | None = None
    topic_id: str | None = None
    mode: str = StudyMode.TEACH
    depth: str = DepthLevel.STANDARD
    knowledge_source: str = KnowledgeSource.COURSE_AND_JARVIS
    knowledge_priority: str = KnowledgePriority.BALANCED
    document_ids: list[str] = field(default_factory=list)
    page_from: int = 0
    page_to: int = 0
    difficulty: int = Difficulty.MEDIUM
    question_count: int = 10
    option_count: int = 5
    professor_id: str | None = None
    language: str = "tr"
    active_exam_id: str | None = None
    active_attempt_id: str | None = None
    chat_quiz: dict[str, Any] = field(default_factory=dict)
    recent_topics: list[str] = field(default_factory=list)
    updated_at: datetime = field(default_factory=utc_now)
    last_activity_at: datetime | None = None
    adaptive_difficulty: bool = True


# ---------------------------------------------------------------------------
# serialization
# ---------------------------------------------------------------------------


_DATETIME_FIELDS = {
    "imported_at",
    "indexed_at",
    "created_at",
    "started_at",
    "finished_at",
    "answered_at",
    "last_attempt_at",
    "next_review_at",
    "updated_at",
    "last_activity_at",
}


def _build(cls: type, data: dict[str, Any]) -> Any:
    """Instantiate ``cls`` from stored data, ignoring unknown keys.

    A missing or unparsable timestamp keeps the field's own default (a
    fresh ``utc_now`` for creation times, ``None`` for optional ones).
    """
    kwargs: dict[str, Any] = {}
    for item in fields(cls):
        if item.name not in data:
            continue
        value = data[item.name]
        if item.name in _DATETIME_FIELDS:
            value = _parse_datetime(value)
            if value is None:
                continue
        kwargs[item.name] = value
    return cls(**kwargs)


def reference_from_dict(data: dict[str, Any]) -> SourceReference:
    return SourceReference(
        document_id=str(data.get("document_id", "")),
        page_number=int(data.get("page_number") or 0),
        chunk_id=data.get("chunk_id"),
        quote=str(data.get("quote") or ""),
        title=str(data.get("title") or ""),
    )


def question_from_dict(data: dict[str, Any]) -> Question:
    payload = dict(data)
    payload["options"] = [
        QuestionOption(
            key=str(option.get("key", "")).upper(),
            text=str(option.get("text", "")),
            concept=str(option.get("concept") or ""),
            explanation=str(option.get("explanation") or ""),
        )
        for option in payload.get("options", [])
        if isinstance(option, dict)
    ]
    payload["references"] = [
        reference_from_dict(item) for item in payload.get("references", []) if isinstance(item, dict)
    ]
    payload["difficulty"] = int(payload.get("difficulty") or Difficulty.MEDIUM)
    return _build(Question, payload)


def document_from_dict(data: dict[str, Any]) -> StudyDocument:
    return _build(StudyDocument, dict(data))


def page_from_dict(data: dict[str, Any]) -> DocumentPage:
    return _build(DocumentPage, dict(data))


def chunk_from_dict(data: dict[str, Any]) -> DocumentChunk:
    return _build(DocumentChunk, dict(data))


def note_from_dict(data: dict[str, Any]) -> StudyNote:
    payload = dict(data)
    payload["references"] = [
        reference_from_dict(item) for item in payload.get("references", []) if isinstance(item, dict)
    ]
    return _build(StudyNote, payload)


def exam_from_dict(data: dict[str, Any]) -> Exam:
    payload = dict(data)
    config = payload.get("config") or {}
    payload["config"] = _build(ExamConfig, dict(config)) if isinstance(config, dict) else ExamConfig()
    return _build(Exam, payload)


def attempt_from_dict(data: dict[str, Any]) -> ExamAttempt:
    payload = dict(data)
    answers = payload.get("answers") or {}
    payload["answers"] = {
        str(key): _build(QuestionAttempt, dict(value))
        for key, value in answers.items()
        if isinstance(value, dict)
    }
    return _build(ExamAttempt, payload)


def mastery_from_dict(data: dict[str, Any]) -> ConceptMastery:
    payload = dict(data)
    payload["recent"] = [bool(item) for item in payload.get("recent", [])]
    return _build(ConceptMastery, payload)


def professor_from_dict(data: dict[str, Any]) -> ProfessorProfile:
    payload = dict(data)
    payload["features"] = [
        _build(StyleFeature, dict(item)) for item in payload.get("features", []) if isinstance(item, dict)
    ]
    return _build(ProfessorProfile, payload)


def session_from_dict(data: dict[str, Any]) -> StudySession:
    return _build(StudySession, dict(data))


def structure_from_dict(data: dict[str, Any]) -> AnatomyStructure:
    payload = dict(data)
    payload["landmarks"] = [
        _build(Landmark, dict(item)) for item in payload.get("landmarks", []) if isinstance(item, dict)
    ]
    return _build(AnatomyStructure, payload)


__all__ = [name for name in globals() if not name.startswith("_")]
