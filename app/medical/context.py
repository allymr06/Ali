"""The study session: one persistent, validated state shared by the
page, the chat, the voice loop and the tutor.

``StudyContext`` is the resolved, per-request view (session defaults
overlaid with what the current command stated); ``SessionManager`` owns
the persisted ``StudySession`` and every change to it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.medical.catalog import Curriculum, valid_subject
from app.medical.intents import StudyCommand
from app.medical.models import (
    DEPTH_LABELS_TR,
    KNOWLEDGE_PRIORITY_LABELS_TR,
    KNOWLEDGE_SOURCE_LABELS_TR,
    STUDY_MODE_LABELS_TR,
    SUBJECT_LABELS_TR,
    DepthLevel,
    KnowledgePriority,
    KnowledgeSource,
    StudyMode,
    StudySession,
    to_plain,
)
from app.medical.store import MedicalStore

MAX_QUESTIONS = 60
MAX_OPTIONS = 6
MIN_OPTIONS = 2
MAX_RECENT_TOPICS = 8


@dataclass(slots=True)
class StudyContext:
    """What the tutor needs to know for one request."""

    subject: str | None = None
    topic_id: str | None = None
    mode: str = StudyMode.TEACH
    depth: str = DepthLevel.STANDARD
    knowledge_source: str = KnowledgeSource.COURSE_AND_JARVIS
    knowledge_priority: str = KnowledgePriority.BALANCED
    document_ids: list[str] = field(default_factory=list)
    page_from: int = 0
    page_to: int = 0
    difficulty: int = 3
    question_count: int = 10
    option_count: int = 5
    professor_id: str | None = None
    language: str = "tr"
    concept_ids: list[str] = field(default_factory=list)
    structure_ids: list[str] = field(default_factory=list)
    spoken: bool = False

    def to_dict(self) -> dict[str, Any]:
        return to_plain(self)


def _clamp(value: Any, low: int, high: int, default: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return default
    return max(low, min(high, number))


def _enum_value(enum_type: type, value: Any) -> str | None:
    text = str(value or "").strip().lower()
    for member in enum_type:  # type: ignore[attr-defined]
        if text == member.value:
            return member.value
    return None


# Field names as they read in a Turkish sentence, for the problem list.
_FIELD_LABELS_TR: dict[str, str] = {
    "mode": "mod",
    "depth": "derinlik",
    "knowledge_source": "bilgi kaynağı",
    "knowledge_priority": "bilgi önceliği",
    "difficulty": "zorluk",
    "question_count": "soru sayısı",
    "option_count": "şık sayısı",
    "page_from": "başlangıç sayfası",
    "page_to": "bitiş sayfası",
}


def _blank(value: Any) -> bool:
    """Nothing was picked — a cleared select or an emptied number box."""
    return value is None or (isinstance(value, str) and not value.strip())


def _checked_enum(enum_type: type, key: str, value: Any, current: str, problems: list[str]) -> str:
    """Keep ``current`` when the choice is unknown, and say why."""
    if _blank(value):
        return current
    resolved = _enum_value(enum_type, value)
    if resolved is None:
        problems.append(f"Bilinmeyen {_FIELD_LABELS_TR[key]}: {value}")
        return current
    return resolved


def _checked_number(key: str, value: Any, low: int, high: int, current: int, problems: list[str]) -> int:
    """Clamp like ``_clamp``, but report it: a number the session cannot
    hold must not be corrected behind the student's back."""
    if _blank(value):
        return current
    try:
        number = int(value)
    except (TypeError, ValueError):
        problems.append(f"Geçersiz {_FIELD_LABELS_TR[key]}: {value} (sayı olmalı)")
        return current
    if not low <= number <= high:
        problems.append(f"Geçersiz {_FIELD_LABELS_TR[key]}: {number} ({low}-{high} aralığında olmalı)")
        return max(low, min(high, number))
    return number


class SessionManager:
    def __init__(self, store: MedicalStore, curriculum: Curriculum) -> None:
        self._store = store
        self._curriculum = curriculum

    def get(self) -> StudySession:
        return self._store.get_session()

    def save(self, session: StudySession) -> StudySession:
        return self._store.save_session(session)

    # ------------------------------------------------------------------
    # updates
    # ------------------------------------------------------------------

    def update(self, fields: dict[str, Any]) -> tuple[StudySession, list[str]]:
        """Apply page/bridge updates; every value the session cannot hold
        is reported. An unknown choice keeps the value the session had; a
        number outside the supported range is pulled to the nearest limit
        so the student is left with a usable session either way."""
        session = self.get()
        problems: list[str] = []
        if not isinstance(fields, dict):
            return session, ["Geçersiz oturum güncellemesi."]
        for key, value in fields.items():
            if key == "subject":
                if value in (None, ""):
                    session.subject = None
                    session.topic_id = None
                else:
                    subject = valid_subject(value)
                    if subject is None:
                        problems.append(f"Bilinmeyen ders: {value}")
                    else:
                        if session.subject != subject:
                            session.topic_id = None
                        session.subject = subject
            elif key == "topic_id":
                if value in (None, ""):
                    session.topic_id = None
                elif not self._curriculum.exists(str(value)):
                    problems.append(f"Bilinmeyen konu: {value}")
                else:
                    topic = self._curriculum.get(str(value))
                    session.topic_id = topic.topic_id
                    session.subject = topic.subject
                    self._remember_topic(session, topic.topic_id)
            elif key == "mode":
                session.mode = _checked_enum(StudyMode, key, value, session.mode, problems)
            elif key == "depth":
                session.depth = _checked_enum(DepthLevel, key, value, session.depth, problems)
            elif key == "knowledge_source":
                session.knowledge_source = _checked_enum(KnowledgeSource, key, value, session.knowledge_source, problems)
            elif key == "knowledge_priority":
                session.knowledge_priority = _checked_enum(KnowledgePriority, key, value, session.knowledge_priority, problems)
            elif key == "document_ids":
                if isinstance(value, (list, tuple)):
                    session.document_ids = [str(item) for item in value if str(item).strip()][:20]
                else:
                    problems.append("document_ids bir liste olmalı.")
            elif key == "page_from":
                session.page_from = _checked_number(key, value, 0, 100_000, session.page_from, problems)
            elif key == "page_to":
                session.page_to = _checked_number(key, value, 0, 100_000, session.page_to, problems)
            elif key == "difficulty":
                session.difficulty = _checked_number(key, value, 1, 5, session.difficulty, problems)
            elif key == "question_count":
                session.question_count = _checked_number(key, value, 1, MAX_QUESTIONS, session.question_count, problems)
            elif key == "option_count":
                session.option_count = _checked_number(key, value, MIN_OPTIONS, MAX_OPTIONS, session.option_count, problems)
            elif key == "professor_id":
                session.professor_id = str(value).strip() or None if value not in (None, "") else None
            elif key == "language":
                session.language = str(value or "tr").strip().lower()[:5] or "tr"
            elif key == "adaptive_difficulty":
                session.adaptive_difficulty = bool(value)
            else:
                problems.append(f"Bilinmeyen alan: {key}")
        if session.page_from and session.page_to and session.page_from > session.page_to:
            session.page_from, session.page_to = session.page_to, session.page_from
        self.save(session)
        return session, problems

    @staticmethod
    def _remember_topic(session: StudySession, topic_id: str) -> None:
        recent = [item for item in session.recent_topics if item != topic_id]
        recent.insert(0, topic_id)
        session.recent_topics = recent[:MAX_RECENT_TOPICS]

    def apply_command(self, command: StudyCommand) -> StudySession:
        """Persist the constraints a chat command stated, so the next
        request ("bir tane daha") inherits them."""
        session = self.get()
        changed = False
        if command.subject and command.subject != session.subject:
            session.subject = command.subject
            session.topic_id = None
            changed = True
        if command.topic_id and self._curriculum.exists(command.topic_id) and command.topic_id != session.topic_id:
            session.topic_id = command.topic_id
            self._remember_topic(session, command.topic_id)
            changed = True
        if command.question_count:
            session.question_count = _clamp(command.question_count, 1, MAX_QUESTIONS, session.question_count)
            changed = True
        if command.option_count:
            session.option_count = _clamp(command.option_count, MIN_OPTIONS, MAX_OPTIONS, session.option_count)
            changed = True
        if command.difficulty:
            session.difficulty = _clamp(command.difficulty, 1, 5, session.difficulty)
            changed = True
        elif command.harder:
            session.difficulty = min(5, session.difficulty + 1)
            changed = True
        elif command.easier:
            session.difficulty = max(1, session.difficulty - 1)
            changed = True
        if command.page_range:
            session.page_from, session.page_to = command.page_range
            changed = True
        if command.depth:
            session.depth = command.depth
            changed = True
        if changed:
            self.save(session)
        return session

    # ------------------------------------------------------------------
    # resolved context
    # ------------------------------------------------------------------

    def resolve(self, command: StudyCommand | None = None, *, spoken: bool = False) -> StudyContext:
        session = self.get()
        context = StudyContext(
            subject=session.subject,
            topic_id=session.topic_id,
            mode=session.mode,
            depth=session.depth,
            knowledge_source=session.knowledge_source,
            knowledge_priority=session.knowledge_priority,
            document_ids=list(session.document_ids),
            page_from=session.page_from,
            page_to=session.page_to,
            difficulty=session.difficulty,
            question_count=session.question_count,
            option_count=session.option_count,
            professor_id=session.professor_id,
            language=session.language,
            spoken=spoken,
        )
        if command is not None:
            if command.subject:
                context.subject = command.subject
            if command.topic_id:
                context.topic_id = command.topic_id
            if command.depth:
                context.depth = command.depth
            if command.question_count:
                context.question_count = _clamp(command.question_count, 1, MAX_QUESTIONS, context.question_count)
            if command.option_count:
                context.option_count = _clamp(command.option_count, MIN_OPTIONS, MAX_OPTIONS, context.option_count)
            if command.difficulty:
                context.difficulty = _clamp(command.difficulty, 1, 5, context.difficulty)
            if command.page_range:
                context.page_from, context.page_to = command.page_range
            context.concept_ids = list(command.concept_ids)
            context.structure_ids = list(command.structure_ids)
            if command.professor_style and context.professor_id is None:
                context.professor_id = None
        return context

    # ------------------------------------------------------------------
    # chat quiz state
    # ------------------------------------------------------------------

    def start_chat_quiz(self, question_ids: list[str], *, mode: str, exam_id: str | None = None) -> StudySession:
        session = self.get()
        session.chat_quiz = {
            "active": bool(question_ids),
            "mode": mode,
            "exam_id": exam_id,
            "question_ids": list(question_ids),
            "index": 0,
            "answered": {},
            "last_question_id": question_ids[0] if question_ids else None,
            "last_answer": None,
        }
        return self.save(session)

    def chat_quiz_state(self) -> dict[str, Any]:
        return dict(self.get().chat_quiz or {})

    def update_chat_quiz(self, **changes: Any) -> StudySession:
        session = self.get()
        quiz = dict(session.chat_quiz or {})
        quiz.update(changes)
        session.chat_quiz = quiz
        return self.save(session)

    def stop_chat_quiz(self) -> StudySession:
        session = self.get()
        session.chat_quiz = {}
        return self.save(session)

    # ------------------------------------------------------------------
    # presentation
    # ------------------------------------------------------------------

    def describe(self, session: StudySession | None = None) -> dict[str, Any]:
        active = session or self.get()
        payload = to_plain(active)
        payload["labels"] = {
            "subject": SUBJECT_LABELS_TR.get(active.subject or "", "Ders seçilmedi"),
            "topic": self._curriculum.breadcrumb(active.topic_id) if active.topic_id else "Konu seçilmedi",
            "mode": STUDY_MODE_LABELS_TR.get(active.mode, active.mode),
            "depth": DEPTH_LABELS_TR.get(active.depth, active.depth),
            "knowledge_source": KNOWLEDGE_SOURCE_LABELS_TR.get(active.knowledge_source, active.knowledge_source),
            "knowledge_priority": KNOWLEDGE_PRIORITY_LABELS_TR.get(active.knowledge_priority, active.knowledge_priority),
        }
        payload["options"] = {
            "modes": [{"value": item.value, "label": STUDY_MODE_LABELS_TR[item]} for item in StudyMode],
            "depths": [{"value": item.value, "label": DEPTH_LABELS_TR[item]} for item in DepthLevel],
            "knowledge_sources": [{"value": item.value, "label": KNOWLEDGE_SOURCE_LABELS_TR[item]} for item in KnowledgeSource],
            "knowledge_priorities": [{"value": item.value, "label": KNOWLEDGE_PRIORITY_LABELS_TR[item]} for item in KnowledgePriority],
            "subjects": [{"value": key, "label": label} for key, label in SUBJECT_LABELS_TR.items()],
        }
        return payload
