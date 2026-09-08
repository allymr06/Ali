"""The Medical Academy facade.

Wires the study layer together, exposes the operations the Nova bridge
and the tools call, registers the medical tools with the executor and
provides the request augmenter the core engine consults on every general
turn. Long pipelines (document processing, vision, comparison, exams)
run as background coroutines and report through events.
"""

from __future__ import annotations

import asyncio
import base64
import threading
from collections import Counter
from collections.abc import Callable, Coroutine, Iterable
from datetime import timedelta
from pathlib import Path
from typing import Any

from app.core.augmentation import RequestAugmentation
from app.core.models import Context, Request, RequestSource, RiskLevel, ToolDefinition, ToolExecutionStatus, ToolResult
from app.core.time import utc_now
from app.medical.anatomy import AnatomyLab
from app.medical.catalog import Curriculum, valid_subject
from app.medical.concepts import ConceptGraph, default_concept_graph
from app.medical.context import SessionManager
from app.medical.documents import DocumentError, DocumentPipeline, page_headings
from app.medical.generation import ExamBuilder, GenerationError, QuestionGenerator
from app.medical.intents import MedicalIntentParser
from app.medical.learning import LearningEngine
from app.medical.model import MedicalModelClient, MedicalModelError
from app.medical.models import (
    COMPARISON_LABELS_TR,
    DocumentPage,
    DocumentStatus,
    ExamConfig,
    ProfessorProfile,
    KnowledgePriority,
    Question,
    QuestionOrigin,
    QuestionType,
    StudyDocument,
    StudyNote,
    SUBJECT_LABELS_TR,
    SUPPORT_LABELS_TR,
    new_id,
)
from app.medical.professor import (
    MENTION_PAGES,
    ProfessorMention,
    QuestionImportParser,
    StyleProfiler,
    imported_question,
    fuller_name,
    looks_like_cover,
    looks_like_question_paper,
    mention_from_owner,
    mentions_in_text,
    professor_from_title,
    professor_key,
    professor_mentions,
    same_person,
    split_by_professor,
)
from app.medical.convert import OFFICE_SUFFIXES, OfficeConverter
from app.medical.narration import NarrationError, NarrationService
from app.medical.prompts import (
    PIPELINE_SYSTEM,
    comparison_prompt,
    document_analysis_prompt,
    notes_prompt,
    page_visual_prompt,
    question_extraction_prompt,
)
from app.medical.questions import (
    analyse_attempt,
    explanation_payload,
    new_attempt,
    question_payload,
    record_answer,
    validate_question,
)
from app.medical.retrieval import RetrievalScope, Retriever
from app.medical.schemas import (
    COMPARISON_SCHEMA,
    DOCUMENT_ANALYSIS_SCHEMA,
    NOTES_SCHEMA,
    PAGE_VISUAL_SCHEMA,
    QUESTION_EXTRACTION_SCHEMA,
)
from app.medical.store import MedicalStore
from app.medical.study import StudyWorkflow
from app.medical.understanding import CONFIDENCE_LEVELS
from app.medical.terminology import TerminologyIndex, load_anatomy_data
from app.medical.text import excerpt, fold, question_fingerprint
from app.medical.tutor import MEDICAL_TOOLS, MedicalTutor

EventCallback = Callable[[dict[str, Any]], None]
ANALYSIS_PAGE_CHARS = 1800
ANALYSIS_MAX_CHARS = 60_000
COMPARE_MAX_CHARS = 14_000
NOTES_MAX_CHARS = 12_000
PAGE_IMAGE_SCALE = 1.5
BANK_LIST_LIMIT = 200
LECTURE_SET_PREFIX = "lecture_set:"
# A published book's front matter. Its authors are not the student's
# lecturers, so a book's questions are kept without an owner.
_BOOK_STRONG = ("isbn", "yayinlari", "yayinevi", "yayin evi", "matbaa", "tum haklari", "copyright", "basimevi")
_BOOK_EDITORIAL = ("editor", "yazarlar", "baski", "basim", "ceviri", "bolum yazarlari")
# The vision pass against a free-tier provider: a breath between pages, one
# long wait after a refusal, and a stop after two refusals in a row so the
# remaining pages stay pending instead of being marked failed by an outage.
ANATOMY_SUBMISSION_MEMORY = 256  # bell-ringer submissions remembered for a retried save
VISION_PACE_SECONDS = 1.0
VISION_RETRY_WAIT_SECONDS = 20.0
VISION_OUTAGE_LIMIT = 2
# Scanned pages: below this many characters a page is read with the vision
# model, at a scale that keeps small option text legible.
OCR_MIN_CHARS = 20
OCR_IMAGE_SCALE = 2.0
OCR_PROMPT = (
    "Transcribe this scanned exam page exactly as written, in reading order, as plain text. Keep every line: "
    "the committee heading (for example 'KOMITE 5'), the question numbers ('12. soru:'), the stem, the "
    "'Soru Sahibi :' and 'Anabilimdalı :' lines, each option on its own line with its letter ('A) …'), and any "
    "suffix attached to an option such as '-Doğru Seçenek' or '-Öğrencinin işaretlediği'. Do not add, omit, "
    "correct or translate anything, and do not comment. Ignore handwritten notes in the margins."
)
_OUTAGE_MARKERS = ("ProviderUnavailableError", "ProviderRateLimitError", "ProviderTimeoutError", "zaman aşımı", "429", "quota", "RESOURCE_EXHAUSTED")


def looks_like_outage(error: Exception) -> bool:
    """A refusal that says nothing about the page: the provider is busy, gone or out of quota."""
    text = str(error)
    return any(marker in text for marker in _OUTAGE_MARKERS)
# Folder or file names that name an academy subject. Order matters where one
# alias contains another ("mikrobiyoloji" before "biyoloji").
SUBJECT_FOLDER_ALIASES: tuple[tuple[str, str], ...] = (
    ("anatomi", "anatomy"),
    ("histoloji", "histology"),
    ("embriyoloji", "histology"),
    ("mikrobiyoloji", "microbiology"),
    ("parazitoloji", "microbiology"),
    ("biyokimya", "biochemistry"),
    ("biyofizik", "biophysics"),
    ("fizyoloji", "physiology"),
    ("biyoloji", "biology"),
    ("genetik", "biology"),
)


def looks_like_book(pages: Iterable[Any]) -> bool:
    """Whether the opening pages are a book's front matter.

    An ISBN settles it; otherwise a publisher's mark has to meet an
    editorial one, so a lecture that merely cites a textbook is not
    mistaken for one.
    """
    text = fold(" ".join(str(getattr(page, "text", "")) for page in list(pages)[:3]))
    if "isbn" in text:
        return True
    return any(marker in text for marker in _BOOK_STRONG) and any(marker in text for marker in _BOOK_EDITORIAL)


def folder_subject(parts: Iterable[str]) -> str | None:
    """The subject a path names, judged from its innermost part outwards."""
    for part in reversed([str(item) for item in parts]):
        folded = fold(part)
        for alias, subject in SUBJECT_FOLDER_ALIASES:
            if fold(alias) in folded:
                return subject
    return None


class DocumentJobs:
    """Model-backed document pipelines the tutor can start from chat."""

    def __init__(self, academy: "MedicalAcademy") -> None:
        self._academy = academy

    async def analyze(self, document_id: str, *, page_from: int = 0, page_to: int = 0) -> dict[str, Any]:
        return await self._academy.analyze_document(document_id, page_from=page_from, page_to=page_to)

    async def compare(self, document_id: str, *, page_from: int = 0, page_to: int = 0) -> dict[str, Any]:
        return await self._academy.compare_document(document_id, page_from=page_from, page_to=page_to)


class MedicalAcademy:
    def __init__(
        self,
        *,
        store: MedicalStore,
        curriculum: Curriculum,
        terminology: TerminologyIndex,
        concepts: ConceptGraph,
        anatomy: AnatomyLab,
        pipeline: DocumentPipeline,
        model: MedicalModelClient,
        diagnostics: Any | None = None,
        source_note: str = "",
        narration_voice: str = "local",
        narration_checkpoint_every: int = 3,
        source_review: bool = False,
    ) -> None:
        self.store = store
        self.curriculum = curriculum
        self.terminology = terminology
        self.concepts = concepts
        self.anatomy = anatomy
        self.pipeline = pipeline
        self.model = model
        self._diagnostics = diagnostics
        self._source_note = source_note
        self.sessions = SessionManager(store, curriculum)
        self.parser = MedicalIntentParser(curriculum, terminology, concepts)
        self.retriever = Retriever(store, terminology)
        self.learning = LearningEngine(store, curriculum, concepts)
        self.generator = QuestionGenerator(store, model, self.retriever, curriculum, concepts, anatomy, self.learning)
        self.exam_builder = ExamBuilder(store, curriculum)
        self.profiler = StyleProfiler()
        self._listeners: list[EventCallback] = []
        self._lock = threading.RLock()
        self._background: set[asyncio.Task[Any]] = set()
        self._jobs: dict[str, dict[str, Any]] = {}
        self._comparisons: dict[str, dict[str, Any]] = {}
        # Bell-ringer submissions already recorded, by the page's submission
        # id, so a retried save of a station moves mastery once.
        self._anatomy_submissions: dict[str, dict[str, Any]] = {}
        # What the last vision pass met: an outage leaves pages pending and is
        # reported, so the student can resume later instead of reprocessing.
        self.vision_state: dict[str, Any] = {"outage": False, "detail": "", "at": None}
        self.narration = NarrationService(store=store, model=model, emit=self._emit, checkpoint_every=narration_checkpoint_every, prefer_cloud=narration_voice == "cloud")
        self.tutor = MedicalTutor(
            store=store,
            curriculum=curriculum,
            terminology=terminology,
            concepts=concepts,
            anatomy=anatomy,
            parser=self.parser,
            sessions=self.sessions,
            retriever=self.retriever,
            learning=self.learning,
            generator=self.generator,
            exams=self.exam_builder,
            emit=self._emit,
            run_background=self._run_background,
            model_available=lambda: self.model.available,
            document_jobs=DocumentJobs(self),
        )
        # The connected study workflow: understanding and repair, prerequisites,
        # source-support review, the exam-date planner, histology practicals.
        self.study = StudyWorkflow(self, source_review=source_review)

    # ------------------------------------------------------------------
    # events, diagnostics, background
    # ------------------------------------------------------------------

    def subscribe(self, listener: EventCallback) -> Callable[[], None]:
        with self._lock:
            self._listeners.append(listener)

        def detach() -> None:
            with self._lock:
                if listener in self._listeners:
                    self._listeners.remove(listener)

        return detach

    def _emit(self, event: dict[str, Any]) -> None:
        payload = {"at": utc_now().isoformat(), **event}
        with self._lock:
            listeners = list(self._listeners)
        for listener in listeners:
            try:
                listener(payload)
            except Exception:
                pass

    def _record(self, name: str, message: str, *, level: str = "info", **attributes: Any) -> None:
        if self._diagnostics is None:
            return
        try:
            from app.diagnostics.models import DiagnosticLevel

            self._diagnostics.record("medical", name, message, level=DiagnosticLevel(level), attributes=attributes)
        except Exception:
            pass

    def _run_background(self, coroutine: Coroutine[Any, Any, Any], *, label: str = "") -> bool:
        """Schedule a pipeline on the running loop; results arrive as events.

        Returns False when there is no loop to run it on, so a caller that
        promised to report back can say it could not start instead.
        """
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            coroutine.close()
            self._record("background.rejected", "No running loop for a medical background job.", level="warning", job=label)
            return False
        task = loop.create_task(coroutine)
        self._background.add(task)
        task.add_done_callback(lambda finished: self._background_done(finished, label))
        return True

    def _background_done(self, task: asyncio.Task[Any], label: str) -> None:
        """Say what a background pipeline raised.

        Nothing else retrieves the exception, so without this it surfaces
        only as asyncio's "never retrieved" warning at garbage collection —
        invisible in the frozen application. A job started from chat has
        promised to report back, so a failure has to report too.
        """
        self._background.discard(task)
        if task.cancelled():
            return
        error = task.exception()
        if error is None:
            return
        self._record("job.failed", "A medical background job failed.", level="warning", job=label, error=type(error).__name__)
        self._emit({"kind": "job_failed", "job": label, "error": type(error).__name__, "message": str(error) or type(error).__name__})

    def close(self) -> None:
        self.store.close()

    # ------------------------------------------------------------------
    # engine hook
    # ------------------------------------------------------------------

    async def augment(self, request: Request, context: Context) -> RequestAugmentation | None:
        marker = request.metadata.get("medical")
        forced = marker is True or isinstance(marker, dict)
        spoken = request.source is RequestSource.VOICE
        augmentation = await self.tutor.plan(request.text, forced=forced, spoken=spoken)
        if augmentation is not None:
            self._record(
                "turn.augmented",
                "Medical Academy augmented a turn.",
                intent=str(augmentation.metadata.get("intent")),
                direct=augmentation.direct_response is not None,
                evidence=int(augmentation.metadata.get("evidence_count") or 0),
            )
        return augmentation

    # ------------------------------------------------------------------
    # tools
    # ------------------------------------------------------------------

    def register_tools(self, executor: Any) -> None:
        def define(name: str, description: str, *, risk: RiskLevel = RiskLevel.READ_ONLY, tags: Iterable[str] = ()) -> ToolDefinition:
            return ToolDefinition(
                name=name,
                description=description,
                risk_level=risk,
                version="1.0.0",
                capabilities=frozenset({"medical", "study"}),
                tags=frozenset({"medical", "read-only", *tags}),
                timeout_seconds=15.0,
                metadata={"verification_strategy": "deterministic"},
            )

        academy = self

        def medical_search_library(query: str, document_id: str = "", page_from: int = 0, page_to: int = 0) -> ToolResult:
            scope = RetrievalScope(document_ids=[document_id] if document_id else [], page_from=int(page_from or 0), page_to=int(page_to or 0))
            blocks = academy.retriever.retrieve(str(query or ""), scope, limit=5)
            if not blocks:
                return ToolResult(ToolExecutionStatus.SUCCESS, "medical_search_library", message="Seçili ders materyalinde eşleşen bir parça bulunamadı.", data={"evidence": []}, verified=True)
            return ToolResult(
                ToolExecutionStatus.SUCCESS,
                "medical_search_library",
                message=f"{len(blocks)} parça bulundu: " + "; ".join(f"{block.reference.title} s. {block.reference.page_number}" for block in blocks),
                data={"evidence": [{"reference": Retriever.references([block])[0], "text": block.text} for block in blocks]},
                verified=True,
            )

        def medical_lookup_term(term: str) -> ToolResult:
            entries = academy.terminology.lookup(str(term or ""), limit=3)
            if not entries:
                return ToolResult(ToolExecutionStatus.FAILED, "medical_lookup_term", message="Terim sözlükte yok.", error="not_found")
            payloads = []
            for entry in entries:
                item = entry.to_dict()
                if entry.structure_id:
                    described = academy.anatomy.describe(entry.structure_id)
                    if described:
                        item["sections"] = described["sections"][:4]
                payloads.append(item)
            return ToolResult(ToolExecutionStatus.SUCCESS, "medical_lookup_term", message=academy.terminology.explain(entries[0]), data={"terms": payloads}, verified=True)

        def medical_open_anatomy(structure: str, highlight: str = "") -> ToolResult:
            found = academy.anatomy.search(str(structure or ""), limit=1)
            if not found:
                return ToolResult(ToolExecutionStatus.FAILED, "medical_open_anatomy", message="Böyle bir yapı Anatomi Lab'de yok.", error="not_found")
            structure_id = found[0]["structure_id"]
            highlights = [item.strip() for item in str(highlight or "").replace(";", ",").split(",") if item.strip()]
            academy._emit({"kind": "anatomy_open", "structure_id": structure_id, "highlight": highlights, "quiz": False})
            return ToolResult(ToolExecutionStatus.SUCCESS, "medical_open_anatomy", message=f"Anatomi Lab'de {found[0]['canonical']} açıldı.", data={"structure_id": structure_id, "highlight": highlights}, verified=True)

        def medical_study_state() -> ToolResult:
            state = academy.sessions.describe()
            return ToolResult(ToolExecutionStatus.SUCCESS, "medical_study_state", message=f"Çalışma oturumu: {state['labels']['subject']} · {state['labels']['topic']} · {state['labels']['mode']}", data=state, verified=True)

        executor.register(define("medical_search_library", "Yüklenen ders materyalinde (PDF/ders notu) sayfa atıflı arama yap."), medical_search_library, source="core:medical")
        executor.register(define("medical_lookup_term", "Anatomik/tıbbi bir terimi (Latince, Türkçe ya da İngilizce) sözlükte ara ve açıkla."), medical_lookup_term, source="core:medical")
        executor.register(define("medical_open_anatomy", "Anatomi Lab'de bir yapıyı aç; 'highlight' ile işaret noktalarını vurgula.", risk=RiskLevel.LOW), medical_open_anatomy, source="core:medical")
        executor.register(define("medical_study_state", "Öğrencinin güncel çalışma oturumunu (ders, konu, mod, derinlik) oku."), medical_study_state, source="core:medical")

    # ------------------------------------------------------------------
    # dashboard and session
    # ------------------------------------------------------------------

    def available(self) -> dict[str, Any]:
        return {
            "model": self.model.available,
            "persistent": self.store.persistent,
            "directory": str(self.pipeline.directory) if self.pipeline.directory else None,
            "structures": len(self.anatomy),
            "terms": len(self.terminology),
            "concepts": len(self.concepts),
        }

    def dashboard(self) -> dict[str, Any]:
        summary = self.store.summary()
        documents = self.store.list_documents()
        recent_exams = self.store.list_exams(limit=5)
        attempts = self.store.list_attempts(limit=5)
        learning = self.learning.summary()
        session = self.sessions.get()
        return {
            "available": self.available(),
            "session": self.sessions.describe(session),
            "counts": summary,
            "learning": learning,
            "review_queue": self.learning.review_queue(limit=6),
            "weak_concepts": [self.learning.mastery_payload(item) for item in self.learning.weak(limit=6)],
            "insights": self.learning.insights(limit=4),
            "recent_documents": [self.pipeline.payload(document) for document in documents[:5]],
            "recent_exams": [self.exam_summary(exam) for exam in recent_exams],
            "recent_attempts": [
                {"attempt_id": attempt.attempt_id, "exam_id": attempt.exam_id, "score": attempt.score, "finished_at": attempt.finished_at.isoformat() if attempt.finished_at else None}
                for attempt in attempts
            ],
            "recent_topics": [
                {"topic_id": topic_id, "label": self.curriculum.breadcrumb(topic_id)} for topic_id in session.recent_topics if self.curriculum.exists(topic_id)
            ],
            "professors": [self.profiler.to_dict(profile) for profile in self.store.list_professors()],
            "jobs": list(self._jobs.values()),
            "lecture_sets": self.lecture_sets(),
            "presentations": self.pipeline.converts_presentations,
            "study": self.study.dashboard_block(),
        }

    def session_state(self) -> dict[str, Any]:
        return self.sessions.describe()

    def update_session(self, fields: dict[str, Any]) -> dict[str, Any]:
        session, problems = self.sessions.update(fields)
        self._emit({"kind": "session_updated"})
        return {"session": self.sessions.describe(session), "problems": problems}

    # ------------------------------------------------------------------
    # subjects, search, terminology
    # ------------------------------------------------------------------

    def subjects(self) -> list[dict[str, Any]]:
        tree = self.curriculum.tree()
        levels = self.learning.levels()
        mastery_by_topic: dict[str, list[str]] = {}
        for item in self.learning.all():
            concept = self.concepts.get(item.concept_id)
            topic_id = concept.topic_id if concept is not None else (item.concept_id[6:] if item.concept_id.startswith("topic:") else None)
            if topic_id:
                mastery_by_topic.setdefault(topic_id, []).append(item.level)
        documents = self.store.list_documents()

        def decorate(node: dict[str, Any]) -> None:
            topic_id = node["topic_id"]
            related_levels = [level for key, levels_ in mastery_by_topic.items() if key == topic_id or key.startswith(topic_id + ".") for level in levels_]
            node["mastery"] = {
                "weak": sum(1 for level in related_levels if level == "weak"),
                "moderate": sum(1 for level in related_levels if level == "moderate"),
                "strong": sum(1 for level in related_levels if level == "strong"),
            }
            node["documents"] = sum(1 for document in documents if any(item == topic_id or item.startswith(topic_id + ".") for item in document.topic_ids))
            node["concepts"] = len(self.concepts.by_topic(topic_id))
            for child in node["children"]:
                decorate(child)

        for node in tree:
            decorate(node)
        _ = levels
        return tree

    def topic(self, topic_id: str) -> dict[str, Any] | None:
        topic = self.curriculum.get(topic_id)
        if topic is None:
            return None
        concepts = self.concepts.by_topic(topic.topic_id)
        structures = [self.anatomy.summary(structure) for structure in self.anatomy.all() if structure.topic_id and self.curriculum.is_within(structure.topic_id, topic.topic_id)]
        documents = [self.pipeline.payload(document) for document in self.store.list_documents() if any(self.curriculum.is_within(item, topic.topic_id) for item in document.topic_ids)]
        questions = self.store.query_questions(topic_id=topic.topic_id, limit=BANK_LIST_LIMIT)
        mastery = [self.learning.mastery_payload(item) for item in self.learning.all() if (self.concepts.get(item.concept_id) and self.concepts.get(item.concept_id).topic_id and self.curriculum.is_within(self.concepts.get(item.concept_id).topic_id, topic.topic_id)) or item.concept_id == f"topic:{topic.topic_id}"]
        return {
            "topic_id": topic.topic_id,
            "subject": topic.subject,
            "subject_label": SUBJECT_LABELS_TR.get(topic.subject, topic.subject),
            "title": topic.title_tr,
            "title_en": topic.title_en,
            "path": [{"topic_id": item.topic_id, "title": item.title_tr} for item in self.curriculum.path(topic.topic_id)],
            "children": [{"topic_id": item.topic_id, "title": item.title_tr} for item in self.curriculum.children(topic.topic_id)],
            "keywords": list(topic.keywords),
            "concepts": [self.concepts.to_dict(concept) for concept in concepts[:40]],
            "structures": structures,
            "documents": documents,
            "question_count": len(questions),
            "mastery": mastery,
            "notes": [self.note_payload(note) for note in self.store.list_notes(limit=100) if note.topic_id and self.curriculum.is_within(note.topic_id, topic.topic_id)],
        }

    def search(self, query: str, *, limit: int = 20) -> dict[str, Any]:
        text = str(query or "").strip()
        if not text:
            return {"query": "", "terms": [], "topics": [], "structures": [], "hits": []}
        terms = [entry.to_dict() for entry in self.terminology.lookup(text, limit=6)]
        topics = [{"topic_id": topic.topic_id, "label": self.curriculum.breadcrumb(topic.topic_id), "subject": topic.subject} for topic in self.curriculum.search(text, limit=6)]
        structures = self.anatomy.search(text, limit=6)
        hits = []
        for hit in self.retriever.search(text, limit=limit):
            document = hit.document
            entry: dict[str, Any] = {"kind": document.kind, "score": hit.score, "id": document.doc_id, "matched": hit.matched}
            if document.kind == "chunk":
                entry.update({"document_id": document.document_id, "title": self.retriever.title_of(document.document_id or ""), "page_number": document.page_number, "heading": document.title, "excerpt": excerpt(document.text, 240)})
            elif document.kind == "note":
                entry.update({"title": document.title, "excerpt": excerpt(document.text, 240)})
            else:
                entry.update({"excerpt": excerpt(document.text, 240), **{key: value for key, value in document.payload.items() if key in {"origin", "topic_id", "professor_id"}}})
            hits.append(entry)
        return {"query": text, "terms": terms, "topics": topics, "structures": structures, "hits": hits}

    def term(self, query: str) -> dict[str, Any]:
        entries = self.terminology.lookup(str(query or ""), limit=8)
        payload = []
        for entry in entries:
            item = entry.to_dict()
            item["explanation"] = self.terminology.explain(entry)
            if entry.concept_id and entry.concept_id in self.concepts:
                item["concept"] = self.concepts.to_dict(self.concepts.get(entry.concept_id))
            payload.append(item)
        return {"query": query, "entries": payload}

    # ------------------------------------------------------------------
    # documents
    # ------------------------------------------------------------------

    def documents(self) -> list[dict[str, Any]]:
        return [self.pipeline.payload(document) for document in self.store.list_documents()]

    def document(self, document_id: str) -> dict[str, Any] | None:
        document = self.store.get_document(document_id)
        if document is None:
            return None
        payload = self.pipeline.payload(document)
        pages = self.store.get_pages(document_id)
        payload["pages"] = [
            {
                "page_number": page.page_number,
                "headings": list(page.headings),
                "char_count": page.char_count,
                "image_count": page.image_count,
                "visual_status": page.visual_status,
                "has_visual_summary": bool(page.visual_summary),
            }
            for page in pages
        ]
        payload["topics"] = [{"topic_id": topic_id, "label": self.curriculum.breadcrumb(topic_id)} for topic_id in document.topic_ids if self.curriculum.exists(topic_id)]
        payload["comparison"] = self._comparisons.get(document_id)
        payload["questions"] = len(self.store.query_questions(document_id=document_id, limit=500))
        payload["job"] = self._jobs.get(document_id)
        return payload

    def page(self, document_id: str, page_number: int, *, image: bool = True) -> dict[str, Any] | None:
        page = self.store.get_page(document_id, int(page_number))
        if page is None:
            return None
        payload: dict[str, Any] = {
            "document_id": document_id,
            "page_number": page.page_number,
            "text": page.text,
            "headings": list(page.headings),
            "visual_summary": page.visual_summary,
            "visual_labels": list(page.visual_labels),
            "visual_status": page.visual_status,
            "image_count": page.image_count,
            "image_area_ratio": page.image_area_ratio,
            "image": None,
        }
        document = self.store.get_document(document_id)
        # Opening a page is study, and only that; the planner marks the topic
        # studied, never mastered.
        self.study.planner.log_study(document_id=document_id, page_number=page.page_number, activity="read")
        if image and document is not None and document.kind == "pdf":
            try:
                png = self.pipeline.render_page(document_id, page.page_number, scale=PAGE_IMAGE_SCALE)
                payload["image"] = "data:image/png;base64," + base64.b64encode(png).decode("ascii")
            except DocumentError as exc:
                payload["image_error"] = str(exc)
        return payload

    def import_document(self, path: str, *, title: str | None = None, subject: str | None = None, professor_id: str | None = None, tags: list[str] | None = None) -> tuple[StudyDocument, bool]:
        document, created = self.pipeline.import_file(path, title=title, subject=valid_subject(subject), professor_id=professor_id, tags=tags)
        if created:
            self._record("document.imported", "Document imported.", document_id=document.document_id, kind=document.kind)
        return document, created

    def import_text_document(self, text: str, *, title: str, subject: str | None = None) -> tuple[StudyDocument, bool]:
        return self.pipeline.import_text(text, title=title, subject=valid_subject(subject))

    def delete_document(self, document_id: str) -> bool:
        removed = self.pipeline.delete(document_id)
        self._comparisons.pop(document_id, None)
        self.narration.builder.forget(document_id)
        self._jobs.pop(document_id, None)
        session = self.sessions.get()
        if document_id in session.document_ids:
            session.document_ids = [item for item in session.document_ids if item != document_id]
            self.sessions.save(session)
        if removed:
            self._emit({"kind": "document_deleted", "document_id": document_id})
        return removed

    def _job(self, document_id: str, stage: str, detail: str, *, done: bool = False, error: str | None = None) -> None:
        entry = {"document_id": document_id, "stage": stage, "detail": detail, "done": done, "error": error, "at": utc_now().isoformat()}
        with self._lock:
            if done:
                self._jobs.pop(document_id, None)
            else:
                self._jobs[document_id] = entry
        self._emit({"kind": "document_status", **entry})

    async def process_document(self, document_id: str, *, vision: bool = True, analysis: bool = True, quiet: bool = False) -> dict[str, Any]:
        """Extract, index, look at the figures, summarise; report every stage.

        ``quiet`` marks the completion event as part of a batch, so the shell
        shows one notification for the batch instead of one per document.
        """
        document = await asyncio.to_thread(self.pipeline.process, document_id, progress=lambda stage, detail: self._job(document_id, str(stage), detail))
        if document.status != DocumentStatus.READY:
            self._job(document_id, "failed", document.status_detail, done=True, error=document.error)
            return self.pipeline.payload(document)
        if vision and self.model.available:
            await self._vision_pass(document)
        if analysis and self.model.available:
            try:
                await self.analyze_document(document_id, emit_done=False)
            except MedicalModelError as exc:
                self._record("document.analysis_failed", str(exc), level="warning", document_id=document_id)
        document = self.store.get_document(document_id) or document
        self._job(document_id, "ready", document.status_detail, done=True)
        self._emit({"kind": "document_ready", "document_id": document_id, "title": document.title, "page_count": document.page_count, "quiet": quiet})
        return self.pipeline.payload(document)

    # ------------------------------------------------------------------
    # lecture sets: a folder of course material imported as one unit
    # ------------------------------------------------------------------

    def lecture_sets(self) -> list[dict[str, Any]]:
        """Every imported folder with live counts of what its documents became."""
        documents = {document.document_id: document for document in self.store.list_documents()}
        payloads = []
        for key in self.store.meta_keys(LECTURE_SET_PREFIX):
            record = self.store.get_meta(key)
            if isinstance(record, dict):
                payloads.append(self._lecture_set_payload(record, documents))
        payloads.sort(key=lambda item: item.get("imported_at") or "", reverse=True)
        return payloads

    def lecture_set(self, set_id: str) -> dict[str, Any] | None:
        record = self.store.get_meta(LECTURE_SET_PREFIX + str(set_id or ""))
        if not isinstance(record, dict):
            return None
        documents = {document.document_id: document for document in self.store.list_documents()}
        payload = self._lecture_set_payload(record, documents)
        payload["documents"] = [self.pipeline.payload(documents[document_id]) for document_id in record.get("document_ids", []) if document_id in documents]
        return payload

    def _lecture_set_payload(self, record: dict[str, Any], documents: dict[str, StudyDocument]) -> dict[str, Any]:
        members = [documents[document_id] for document_id in record.get("document_ids", []) if document_id in documents]
        statuses = Counter(document.status for document in members)
        subjects = Counter(document.subject for document in members if document.subject)
        return {
            "set_id": record.get("set_id"),
            "name": record.get("name"),
            "root": record.get("root"),
            "source": record.get("source") or "",
            "imported_at": record.get("imported_at"),
            "processed_at": record.get("processed_at"),
            "counts": dict(record.get("counts") or {}),
            "documents_total": len(members),
            "ready": statuses.get(DocumentStatus.READY, 0),
            "pending": sum(count for status, count in statuses.items() if status not in {DocumentStatus.READY, DocumentStatus.FAILED}),
            "failed": statuses.get(DocumentStatus.FAILED, 0),
            "visual_pending": sum(document.visual_pages_pending for document in members),
            "visual_analyzed": sum(document.visual_pages_analyzed for document in members),
            "subjects": [{"subject": subject, "label": SUBJECT_LABELS_TR.get(subject, subject), "count": count} for subject, count in subjects.most_common()],
            "committees": self._committee_catalog(members),
            "skipped": list(record.get("skipped") or [])[:50],
            "failures": list(record.get("failed") or [])[:50],
            "mined": record.get("mined"),
        }

    @staticmethod
    def _committee_catalog(documents: list[StudyDocument]) -> list[dict[str, Any]]:
        """Derive the student's committee/course taxonomy from import tags.

        A Drive semester is filed as ``set, Komite N, Ders``. Only numbered
        committees belong in this catalogue; HUP, KDT and ungrouped material
        deliberately remain outside it. The folder label wins over the broad
        subject key so every real course remains visible.
        """
        grouped: dict[int, dict[str, list[str]]] = {}
        ignored_tags = {"taranmış metin", "analiz edildi"}
        for document in documents:
            tags = [str(tag).strip() for tag in document.tags if str(tag).strip()]
            committee_index = next(
                (
                    index
                    for index, tag in enumerate(tags)
                    if tag.casefold().startswith("komite ")
                    and tag[7:].strip().isdigit()
                    and 1 <= int(tag[7:].strip()) <= 5
                ),
                None,
            )
            if committee_index is None:
                continue
            number = int(tags[committee_index][7:].strip())
            lesson = next(
                (
                    tag
                    for tag in tags[committee_index + 1 :]
                    if tag.casefold() not in ignored_tags
                ),
                SUBJECT_LABELS_TR.get(
                    document.subject or "", document.subject or "Diğer"
                ),
            )
            grouped.setdefault(number, {}).setdefault(lesson, []).append(
                document.document_id
            )

        return [
            {
                "committee": f"Komite {number}",
                "number": number,
                "count": sum(len(ids) for ids in lessons.values()),
                "lessons": [
                    {
                        "lesson": lesson,
                        "count": len(ids),
                        "document_ids": ids,
                    }
                    for lesson, ids in sorted(
                        lessons.items(), key=lambda item: item[0].casefold()
                    )
                ],
            }
            for number, lessons in sorted(grouped.items())
        ]

    def _save_lecture_set(self, record: dict[str, Any]) -> None:
        self.store.set_meta(LECTURE_SET_PREFIX + str(record["set_id"]), record)

    def delete_lecture_set(self, set_id: str) -> bool:
        """Forget the folder as a unit; its documents stay in the library."""
        removed = self.store.delete_meta(LECTURE_SET_PREFIX + str(set_id or ""))
        if removed:
            self._emit({"kind": "lecture_set_deleted", "set_id": set_id})
        return removed

    def import_folder(self, path: str, *, name: str | None = None, source: str | None = None, progress: Callable[[int, int, str], None] | None = None) -> dict[str, Any]:
        """File every supported document below ``path`` as one lecture set.

        Folder names become tags, and the nearest folder (or the file name)
        that names a subject sets the document's subject. Nothing is
        processed here: the caller runs :meth:`process_lecture_set`, which
        needs the loop and possibly the model.
        """
        root = Path(str(path or "")).expanduser()
        if not root.is_dir():
            raise DocumentError("Klasör bulunamadı.")
        set_name = (name or root.name).strip()[:80] or root.name
        files = sorted(item for item in root.rglob("*") if item.is_file() and not item.name.startswith(".") and not item.name.endswith(".part"))
        document_ids: list[str] = []
        imported: list[str] = []
        duplicates: list[str] = []
        skipped: list[dict[str, str]] = []
        failed: list[dict[str, str]] = []
        for index, file in enumerate(files, start=1):
            relative = file.relative_to(root)
            folders = [part for part in relative.parts[:-1] if part.strip()]
            if progress is not None and (index % 5 == 0 or index == len(files)):
                progress(index, len(files), str(relative))
            if not self.pipeline.accepts(file):
                reason = "sunum için PowerPoint gerekli" if file.suffix.lower() in OFFICE_SUFFIXES else "desteklenmeyen tür"
                skipped.append({"path": str(relative), "reason": reason})
                continue
            subject = folder_subject([*folders, file.stem])
            tags = list(dict.fromkeys([set_name, *folders]))
            try:
                document, created = self.pipeline.import_file(file, subject=subject, tags=tags)
            except DocumentError as exc:
                failed.append({"path": str(relative), "error": str(exc)})
                continue
            except Exception as exc:  # a converter or disk failure must not stop the folder
                failed.append({"path": str(relative), "error": f"{type(exc).__name__}"})
                continue
            if created:
                imported.append(document.document_id)
            else:
                duplicates.append(document.document_id)
                changed = False
                for tag in tags:
                    if tag not in document.tags and len(document.tags) < 20:
                        document.tags.append(tag)
                        changed = True
                if subject and not document.subject:
                    document.subject = subject
                    changed = True
                if changed:
                    self.store.save_document(document)
            if document.document_id not in document_ids:
                document_ids.append(document.document_id)
        record = {
            "set_id": new_id("set"),
            "name": set_name,
            "root": str(root),
            "source": str(source or "").strip()[:300],
            "imported_at": utc_now().isoformat(),
            "processed_at": None,
            "document_ids": document_ids,
            "counts": {"files": len(files), "imported": len(imported), "duplicates": len(duplicates), "skipped": len(skipped), "failed": len(failed)},
            "skipped": skipped[:200],
            "failed": failed[:200],
        }
        self._save_lecture_set(record)
        self._record("lecture_set.imported", "Lecture set imported.", set_id=record["set_id"], files=len(files), imported=len(imported), duplicates=len(duplicates), failed=len(failed))
        self._emit({"kind": "lecture_set_imported", "set_id": record["set_id"], "name": set_name, "imported": len(imported), "duplicates": len(duplicates), "skipped": len(skipped), "failed": len(failed)})
        return {**record, "imported_ids": imported}

    async def process_lecture_set(self, set_id: str, *, vision: bool = True, analysis: bool = True, retry_failed: bool = False) -> dict[str, Any]:
        """Process the set's unprocessed documents one after another.

        Each document reports its own stages as usual; the set reports where
        it is in the queue and one notification at the end instead of one
        per document.
        """
        record = self.store.get_meta(LECTURE_SET_PREFIX + str(set_id or ""))
        if not isinstance(record, dict):
            raise DocumentError("Ders seti bulunamadı.")
        queue: list[StudyDocument] = []
        for document_id in record.get("document_ids", []):
            document = self.store.get_document(document_id)
            if document is None:
                continue
            if document.status == DocumentStatus.READY:
                continue
            if document.status == DocumentStatus.FAILED and not retry_failed:
                continue
            queue.append(document)
        processed: list[str] = []
        failures: list[dict[str, str]] = []
        for index, document in enumerate(queue, start=1):
            self._emit({"kind": "lecture_set_progress", "set_id": set_id, "name": record.get("name"), "done": index - 1, "total": len(queue), "current": document.title, "document_id": document.document_id})
            try:
                payload = await self.process_document(document.document_id, vision=vision, analysis=analysis, quiet=True)
            except Exception as exc:
                failures.append({"document_id": document.document_id, "title": document.title, "error": f"{type(exc).__name__}"})
                continue
            if payload.get("status") == DocumentStatus.READY:
                processed.append(document.document_id)
            else:
                failures.append({"document_id": document.document_id, "title": document.title, "error": str(payload.get("error") or payload.get("status_detail") or "işlenemedi")})
        record["processed_at"] = utc_now().isoformat()
        self._save_lecture_set(record)
        report = {"set_id": set_id, "name": record.get("name"), "queued": len(queue), "processed": len(processed), "failed": len(failures), "failures": failures[:50]}
        self._record("lecture_set.processed", "Lecture set processed.", set_id=set_id, processed=len(processed), failed=len(failures))
        self._emit({"kind": "lecture_set_processed", **report})
        return report

    async def transcribe_document(self, document_id: str, *, pace_seconds: float | None = None, retry_wait_seconds: float | None = None) -> dict[str, Any]:
        """Read the pages of a scanned document with the vision model.

        A page with no text of its own is transcribed as written — the
        committee heading, the question numbers, the owner and department
        lines, each option with its suffixes — and stored as the page's text,
        so the deterministic parser, the search and the narration see the
        paper exactly as a text PDF. The same outage rule as the figure pass
        applies: two refusals stop the document, the rest stays untranscribed
        and the report says so.
        """
        document = self.store.get_document(document_id)
        if document is None:
            raise DocumentError("Belge bulunamadı.")
        if not self.model.available:
            raise MedicalModelError("Taranmış sayfaları okumak için model sağlayıcısı gerekli.")
        pages = [page for page in self.store.get_pages(document_id) if page.char_count < OCR_MIN_CHARS]
        pace = VISION_PACE_SECONDS if pace_seconds is None else float(pace_seconds)
        retry_wait = VISION_RETRY_WAIT_SECONDS if retry_wait_seconds is None else float(retry_wait_seconds)
        transcribed = 0
        outages = 0
        stopped: str | None = None
        for index, page in enumerate(pages, start=1):
            if index > 1 and pace > 0:
                await asyncio.sleep(pace)
            self._job(document_id, "reading", f"Taranmış sayfalar okunuyor · {index} / {len(pages)} (s. {page.page_number})")
            try:
                png = await asyncio.to_thread(self.pipeline.render_page, document_id, page.page_number, scale=OCR_IMAGE_SCALE)
                text = await self.model.text("page_ocr", OCR_PROMPT, system_prompt=PIPELINE_SYSTEM, images=[{"data": png, "mime_type": "image/png", "detail": "high"}], task_type="vision")
            except MedicalModelError as exc:
                if looks_like_outage(exc):
                    outages += 1
                    self._record("document.ocr_paused", str(exc), level="warning", document_id=document_id, page=page.page_number)
                    if outages >= VISION_OUTAGE_LIMIT:
                        stopped = f"Sayfa okuma durdu: model şu an yanıt vermiyor (kota dolmuş olabilir); {len(pages) - index + 1} sayfa bekliyor, sonra sürdürülebilir."
                        break
                    if retry_wait > 0:
                        await asyncio.sleep(retry_wait)
                    continue
                self._record("document.ocr_failed", str(exc), level="warning", document_id=document_id, page=page.page_number)
                continue
            except DocumentError as exc:
                self._record("document.ocr_failed", str(exc), level="warning", document_id=document_id, page=page.page_number)
                continue
            outages = 0
            body = str(text or "").strip()
            if len(body) < OCR_MIN_CHARS:
                continue
            page.text = body
            page.headings = page_headings(body)
            self.store.save_page(page)
            transcribed += 1
        if transcribed:
            self.pipeline.reindex(document_id)
            refreshed = self.store.get_document(document_id)
            if refreshed is not None:
                refreshed.tags = list(dict.fromkeys(refreshed.tags + ["taranmış metin"]))[:20]
                self.store.save_document(refreshed)
        self.vision_state = {"outage": stopped is not None, "detail": stopped or "", "at": utc_now().isoformat() if stopped else None}
        current = self.store.get_document(document_id) or document
        self._job(document_id, "ready", current.status_detail, done=True)
        report = {"document_id": document_id, "pages": len(pages), "transcribed": transcribed, "remaining": len(pages) - transcribed, "stopped": stopped}
        self._record("document.transcribed", "Scanned pages transcribed.", document_id=document_id, transcribed=transcribed, remaining=report["remaining"], stopped=bool(stopped))
        self._emit({"kind": "document_transcribed", "document_id": document_id, "title": current.title, **{key: value for key, value in report.items() if key != "document_id"}, "quiet": True})
        return report

    def _needs_ocr(self, document: StudyDocument) -> bool:
        if document.status != DocumentStatus.READY or not document.page_count:
            return False
        return any(page.char_count < OCR_MIN_CHARS for page in self.store.get_pages(document.document_id))

    async def continue_processing(self, *, set_id: str | None = None, document_id: str | None = None, vision: bool = True, analysis: bool = True, ocr: bool = True) -> dict[str, Any]:
        """Pick up what the model left: pending figure pages, missing analyses.

        Ready documents keep their pages and chunks; only the model work that
        was refused or never run is done, one document after another, and the
        walk stops at the first outage so the rest can wait for the quota.
        """
        if not self.model.available:
            raise MedicalModelError("Şekil incelemesi ve analiz için model sağlayıcısı gerekli.")
        if document_id:
            document = self.store.get_document(document_id)
            if document is None:
                raise DocumentError("Belge bulunamadı.")
            candidates = [document]
        elif set_id:
            record = self.store.get_meta(LECTURE_SET_PREFIX + str(set_id))
            if not isinstance(record, dict):
                raise DocumentError("Ders seti bulunamadı.")
            candidates = [item for item in (self.store.get_document(identifier) for identifier in record.get("document_ids", [])) if item is not None]
        else:
            candidates = self.store.list_documents()
        queue = [item for item in candidates if item.status == DocumentStatus.READY and ((vision and item.visual_pages_pending) or (analysis and "analiz edildi" not in item.tags) or (ocr and self._needs_ocr(item)))]
        described = 0
        analysed = 0
        transcribed = 0
        touched = 0
        stopped: str | None = None
        for index, document in enumerate(queue, start=1):
            self._emit({"kind": "lecture_set_progress", "set_id": set_id, "stage": "vision", "done": index - 1, "total": len(queue), "current": document.title, "document_id": document.document_id})
            touched += 1
            if ocr and self._needs_ocr(document):
                # Words first: a scanned paper's questions, headings and figures
                # all depend on the text being there.
                read = await self.transcribe_document(document.document_id)
                transcribed += read["transcribed"]
                if read["stopped"]:
                    stopped = read["stopped"]
                    break
                document = self.store.get_document(document.document_id) or document
            if vision and document.visual_pages_pending:
                described += await self._vision_pass(document)
                if self.vision_state.get("outage"):
                    stopped = str(self.vision_state.get("detail") or "model yanıt vermiyor")
                    self._job(document.document_id, "ready", (self.store.get_document(document.document_id) or document).status_detail, done=True)
                    break
            if analysis and "analiz edildi" not in document.tags:
                try:
                    await self.analyze_document(document.document_id, emit_done=False)
                    analysed += 1
                except MedicalModelError as exc:
                    self._record("document.analysis_failed", str(exc), level="warning", document_id=document.document_id)
                    if looks_like_outage(exc):
                        stopped = "Analiz durdu: model şu an yanıt vermiyor (kota dolmuş olabilir)."
                        self._job(document.document_id, "ready", document.status_detail, done=True)
                        break
                except DocumentError:
                    pass
            refreshed = self.store.get_document(document.document_id) or document
            self._job(document.document_id, "ready", refreshed.status_detail, done=True)
        report = {"set_id": set_id, "document_id": document_id, "queued": len(queue), "documents": touched, "described": described, "analysed": analysed, "transcribed": transcribed, "stopped": stopped}
        self._record("lecture_set.continued", "Pending model work continued.", **{key: value for key, value in report.items() if key != "stopped"}, stopped=bool(stopped))
        self._emit({"kind": "vision_resumed", **report})
        return {"continue": report}

    async def import_folder_job(self, path: str, *, name: str | None = None, source: str | None = None, vision: bool = True, analysis: bool = True) -> dict[str, Any]:
        """Import a folder off the loop, then process what it brought."""
        loop = asyncio.get_running_loop()

        def progress(done: int, total: int, current: str) -> None:
            loop.call_soon_threadsafe(self._emit, {"kind": "lecture_set_progress", "set_id": None, "name": name or Path(str(path)).name, "stage": "importing", "done": done, "total": total, "current": current})

        record = await asyncio.to_thread(self.import_folder, path, name=name, source=source, progress=progress)
        processed = await self.process_lecture_set(record["set_id"], vision=vision, analysis=analysis)
        counts = dict(record.get("counts") or {})
        notes: list[str] = []
        if counts.get("skipped"):
            reasons = Counter(item.get("reason", "") for item in record.get("skipped", []))
            notes.append("Atlanan dosyalar: " + ", ".join(f"{count} × {reason}" for reason, count in reasons.most_common()) + ".")
        if counts.get("failed"):
            notes.append("İçe aktarılamayan dosyalar: " + "; ".join(f"{item['path']} ({item['error']})" for item in record.get("failed", [])[:5]) + ("…" if counts["failed"] > 5 else ""))
        if processed.get("failed"):
            notes.append("İşlenemeyen belgeler: " + "; ".join(f"{item['title']} ({item['error']})" for item in processed.get("failures", [])[:5]) + ("…" if processed["failed"] > 5 else ""))
        return {
            "set_id": record["set_id"],
            "folder_import": {"name": record["name"], **counts, "processed": processed.get("processed", 0), "process_failed": processed.get("failed", 0), "notes": notes},
        }

    async def _vision_pass(self, document: StudyDocument, *, pace_seconds: float | None = None, retry_wait_seconds: float | None = None) -> int:
        """Describe the document's pending figure pages, one model call each.

        A page the model cannot read is marked so; a page the provider refused
        to look at (busy, gone, out of quota) stays pending, and after two such
        refusals in a row the pass stops and says why, so a free-tier quota
        pauses the work instead of writing 'failed' on every remaining page.
        """
        pending = self.pipeline.pages_needing_vision(document.document_id)
        analysed = 0
        outages = 0
        pace = VISION_PACE_SECONDS if pace_seconds is None else float(pace_seconds)
        retry_wait = VISION_RETRY_WAIT_SECONDS if retry_wait_seconds is None else float(retry_wait_seconds)
        self.vision_state = {"outage": False, "detail": "", "at": None}
        for index, page in enumerate(pending, start=1):
            if index > 1 and pace > 0:
                await asyncio.sleep(pace)
            self._job(document.document_id, "analyzing_visuals", f"Şekiller inceleniyor · {index} / {len(pending)} (s. {page.page_number})")
            try:
                png = await asyncio.to_thread(self.pipeline.render_page, document.document_id, page.page_number, scale=PAGE_IMAGE_SCALE)
                data = await self.model.structured(
                    "page_visual",
                    page_visual_prompt(document.title, page.page_number, page.text),
                    PAGE_VISUAL_SCHEMA,
                    system_prompt=PIPELINE_SYSTEM,
                    images=[{"data": png, "mime_type": "image/png", "detail": "high"}],
                    task_type="vision",
                )
            except MedicalModelError as exc:
                if looks_like_outage(exc):
                    outages += 1
                    self._record("document.vision_paused", str(exc), level="warning", document_id=document.document_id, page=page.page_number)
                    if outages >= VISION_OUTAGE_LIMIT:
                        detail = f"Şekil incelemesi durdu: model şu an yanıt vermiyor (kota dolmuş olabilir); {len(pending) - index + 1} sayfa bekliyor, sonra sürdürülebilir."
                        self.vision_state = {"outage": True, "detail": detail, "at": utc_now().isoformat()}
                        self._job(document.document_id, "analyzing_visuals", detail)
                        break
                    if retry_wait > 0:
                        await asyncio.sleep(retry_wait)
                    continue
                self.pipeline.attach_visual_summary(document.document_id, page.page_number, summary="", labels=[], status="failed")
                self._record("document.vision_failed", str(exc), level="warning", document_id=document.document_id, page=page.page_number)
                continue
            except DocumentError as exc:
                self.pipeline.attach_visual_summary(document.document_id, page.page_number, summary="", labels=[], status="failed")
                self._record("document.vision_failed", str(exc), level="warning", document_id=document.document_id, page=page.page_number)
                continue
            outages = 0
            if not data.get("has_educational_figure") or data.get("legibility") == "unreadable":
                self.pipeline.attach_visual_summary(document.document_id, page.page_number, summary="", labels=[], status="none" if not data.get("has_educational_figure") else "unreadable")
                continue
            summary_parts = [f"Şekil ({data.get('figure_type', 'other')}): {data.get('description', '').strip()}"]
            points = [str(item) for item in data.get("educational_points", []) if str(item).strip()]
            if points:
                summary_parts.append("Öğrenme noktaları: " + " · ".join(points[:8]))
            labels = [str(item) for item in data.get("labels", [])] + [str(item) for item in data.get("structures", [])]
            self.pipeline.attach_visual_summary(document.document_id, page.page_number, summary="\n".join(summary_parts), labels=list(dict.fromkeys(labels)))
            analysed += 1
        return analysed

    def _page_texts(self, document_id: str, *, page_from: int = 0, page_to: int = 0, per_page: int = ANALYSIS_PAGE_CHARS, max_total: int = ANALYSIS_MAX_CHARS) -> list[tuple[int, str]]:
        texts: list[tuple[int, str]] = []
        total = 0
        for page in self.store.get_pages(document_id, page_from=page_from, page_to=page_to):
            body = page.text.strip()
            if page.visual_summary:
                body = (body + "\n[Şekil] " + page.visual_summary).strip()
            if not body:
                continue
            body = body[:per_page]
            if total + len(body) > max_total:
                break
            texts.append((page.page_number, body))
            total += len(body)
        return texts

    async def analyze_document(self, document_id: str, *, page_from: int = 0, page_to: int = 0, emit_done: bool = True) -> dict[str, Any]:
        document = self.store.get_document(document_id)
        if document is None:
            raise DocumentError("Belge bulunamadı.")
        if document.status != DocumentStatus.READY:
            raise DocumentError("Belge henüz işlenmedi.")
        if not self.model.available:
            raise MedicalModelError("Belge analizi için model sağlayıcısı gerekli.")
        self._job(document_id, "analyzing", "Konular ve terimler çıkarılıyor")
        texts = self._page_texts(document_id, page_from=page_from, page_to=page_to)
        if not texts:
            self._job(document_id, "ready", document.status_detail, done=True)
            raise DocumentError("Analiz için metin yok.")
        try:
            data = await self.model.structured("document_analysis", document_analysis_prompt(document.title, texts), DOCUMENT_ANALYSIS_SCHEMA, system_prompt=PIPELINE_SYSTEM)
        except MedicalModelError:
            self._job(document_id, "ready", document.status_detail, done=True, error="analysis_failed")
            raise
        document = self.store.get_document(document_id) or document
        document.summary = str(data.get("summary", "")).strip()
        document.key_terms = [str(item) for item in data.get("key_terms", [])][:80]
        subject = valid_subject(data.get("subject"))
        if subject and not document.subject:
            document.subject = subject
        topic_ids: list[str] = []
        for topic in data.get("topics", []):
            found = self.curriculum.search(str(topic.get("title", "")), subject=document.subject, limit=1)
            if found and found[0].parent_id is not None and found[0].topic_id not in topic_ids:
                topic_ids.append(found[0].topic_id)
        if topic_ids:
            document.topic_ids = topic_ids[:12]
        analysis = {
            "title": data.get("title"),
            "summary": document.summary,
            "topics": [dict(item) for item in data.get("topics", [])],
            "key_terms": list(document.key_terms),
            "high_yield": [str(item) for item in data.get("high_yield", [])],
            "uncertainties": [str(item) for item in data.get("uncertainties", [])],
            "topic_ids": list(document.topic_ids),
            "analysed_at": utc_now().isoformat(),
            "page_from": page_from,
            "page_to": page_to,
        }
        document.tags = list(dict.fromkeys(document.tags + ["analiz edildi"]))[:20]
        self.store.save_document(document)
        self._analysis_cache = getattr(self, "_analysis_cache", {})
        self._analysis_cache[document_id] = analysis
        self._record("document.analyzed", "Document analysed.", document_id=document_id, topics=len(topic_ids))
        if emit_done:
            self._job(document_id, "ready", document.status_detail, done=True)
            self._emit({"kind": "document_analyzed", "document_id": document_id, "title": document.title, "topics": len(topic_ids)})
        return analysis

    def document_analysis(self, document_id: str) -> dict[str, Any] | None:
        cache = getattr(self, "_analysis_cache", {})
        return cache.get(document_id)

    async def compare_document(self, document_id: str, *, page_from: int = 0, page_to: int = 0) -> dict[str, Any]:
        document = self.store.get_document(document_id)
        if document is None:
            raise DocumentError("Belge bulunamadı.")
        if not self.model.available:
            raise MedicalModelError("Karşılaştırma için model sağlayıcısı gerekli.")
        self._job(document_id, "comparing", "Ders notu standart tıp bilgisiyle karşılaştırılıyor")
        blocks = self.retriever.page_evidence(document, page_from or 1, page_to or document.page_count or 10**6, max_chars=COMPARE_MAX_CHARS)
        if not blocks:
            self._job(document_id, "ready", document.status_detail, done=True)
            raise DocumentError("Karşılaştırılacak metin yok.")
        evidence_text = self.retriever.format_evidence(blocks)
        try:
            data = await self.model.structured("document_comparison", comparison_prompt(document.subject, evidence_text), COMPARISON_SCHEMA, system_prompt=PIPELINE_SYSTEM)
        except MedicalModelError:
            self._job(document_id, "ready", document.status_detail, done=True, error="comparison_failed")
            raise
        valid_pages = {block.reference.page_number for block in blocks}
        findings = []
        for item in data.get("findings", []):
            page = int(item.get("page") or 0)
            findings.append(
                {
                    "statement": str(item.get("statement", "")).strip(),
                    "page": page if page in valid_pages else None,
                    "page_unverified": page not in valid_pages,
                    "category": item.get("category"),
                    "category_label": COMPARISON_LABELS_TR.get(str(item.get("category")), str(item.get("category"))),
                    "explanation": str(item.get("explanation", "")).strip(),
                    "standard_view": str(item.get("standard_view", "")).strip(),
                    "support": item.get("support", "moderate"),
                    "support_label": SUPPORT_LABELS_TR.get(str(item.get("support", "moderate")), str(item.get("support"))),
                }
            )
        counts: dict[str, int] = {}
        for finding in findings:
            counts[str(finding["category"])] = counts.get(str(finding["category"]), 0) + 1
        result = {
            "document_id": document_id,
            "title": document.title,
            "page_from": page_from,
            "page_to": page_to,
            "findings": findings,
            "counts": counts,
            "overall": str(data.get("overall", "")).strip(),
            "compared_at": utc_now().isoformat(),
            "note": "Kategoriler modelin standart kaynaklarla karşılaştırmasına dayanır; 'sınırlı kanıt' etiketli bulguları ders kitabından doğrula.",
        }
        with self._lock:
            self._comparisons[document_id] = result
        self._job(document_id, "ready", document.status_detail, done=True)
        self._record("document.compared", "Document compared with standard knowledge.", document_id=document_id, findings=len(findings))
        self._emit({"kind": "comparison_ready", "document_id": document_id, "title": document.title, "findings": len(findings), "counts": counts})
        return result

    def comparison(self, document_id: str) -> dict[str, Any] | None:
        return self._comparisons.get(document_id)

    # ------------------------------------------------------------------
    # notes
    # ------------------------------------------------------------------

    def note_payload(self, note: StudyNote) -> dict[str, Any]:
        return {
            "note_id": note.note_id,
            "title": note.title,
            "content": note.content,
            "subject": note.subject,
            "subject_label": SUBJECT_LABELS_TR.get(note.subject or "", note.subject or ""),
            "topic_id": note.topic_id,
            "topic_label": self.curriculum.breadcrumb(note.topic_id) if note.topic_id else "",
            "mode": note.mode,
            "references": [{"document_id": ref.document_id, "page_number": ref.page_number, "title": ref.title} for ref in note.references],
            "created_at": note.created_at.isoformat(),
        }

    def notes(self) -> list[dict[str, Any]]:
        return [self.note_payload(note) for note in self.store.list_notes(limit=200)]

    def delete_note(self, note_id: str) -> bool:
        return self.store.delete_note(note_id)

    async def generate_notes(self, *, mode: str, subject: str | None, topic_id: str | None, document_ids: list[str], page_from: int = 0, page_to: int = 0, depth: str = "standard") -> StudyNote:
        if not self.model.available:
            raise MedicalModelError("Not üretimi için model sağlayıcısı gerekli.")
        blocks: list[Any] = []
        for document_id in document_ids[:4]:
            document = self.store.get_document(document_id)
            if document is None:
                continue
            blocks.extend(self.retriever.page_evidence(document, page_from or 1, page_to or document.page_count or 10**6, max_chars=NOTES_MAX_CHARS // max(1, min(4, len(document_ids)))))
        if not blocks and topic_id and self.store.summary()["chunks"]:
            topic = self.curriculum.get(topic_id)
            if topic is not None:
                blocks.extend(self.retriever.retrieve(" ".join([topic.title_tr, *topic.keywords[:6]]), RetrievalScope(), limit=8))
        evidence_text = self.retriever.format_evidence(blocks)
        curated = ""
        if (subject == "anatomy" or (topic_id or "").startswith("anatomy")) and topic_id:
            ids = [structure.structure_id for structure in self.anatomy.all() if structure.topic_id and self.curriculum.is_within(structure.topic_id, topic_id)]
            curated = self.anatomy.facts_for_prompt(ids, limit=3)
        prompt = notes_prompt(mode=mode, subject=subject, topic_path=self.curriculum.breadcrumb(topic_id) if topic_id else "", evidence_text=evidence_text, depth=depth, curated_facts=curated)
        data = await self.model.structured("study_notes", prompt, NOTES_SCHEMA, system_prompt=PIPELINE_SYSTEM)
        cited = {int(page) for page in data.get("cited_pages", []) if isinstance(page, int)}
        # A page number identifies an excerpt only within one document: notes are
        # built from up to four, so filtering on the page alone would attach a chip
        # to every document that happens to have that page. When the cited pages
        # cannot be resolved to exactly one document, keep every block instead of
        # picking one -- an over-broad reference list is honest, a wrong chip is not.
        cited_documents = {block.reference.document_id for block in blocks if block.reference.page_number in cited}
        references = [
            block.reference
            for block in blocks
            if not cited
            or (block.reference.page_number in cited and (len(cited_documents) == 1 or block.reference.document_id in cited_documents))
        ]
        seen: set[tuple[str, int]] = set()
        unique_refs = []
        for ref in references:
            key = (ref.document_id, ref.page_number)
            if key not in seen:
                seen.add(key)
                unique_refs.append(ref)
        note = StudyNote(
            note_id=new_id("note"),
            title=str(data.get("title") or (self.curriculum.get(topic_id).title_tr if topic_id and self.curriculum.get(topic_id) else "Not")).strip()[:200],
            content=str(data.get("markdown", "")).strip(),
            subject=subject or (self.curriculum.subject_of(topic_id) if topic_id else None),
            topic_id=topic_id,
            mode=mode,
            references=unique_refs[:40],
        )
        self.store.save_note(note)
        self._emit({"kind": "note_ready", "note_id": note.note_id, "title": note.title})
        return note

    # ------------------------------------------------------------------
    # exams
    # ------------------------------------------------------------------

    def exam_config(self, fields: dict[str, Any]) -> ExamConfig:
        session = self.sessions.get()
        subjects = [valid_subject(item) for item in (fields.get("subjects") or [])]
        subjects = [item for item in subjects if item]
        topic_ids = [str(item) for item in (fields.get("topic_ids") or []) if self.curriculum.exists(str(item))]
        if not subjects and not topic_ids:
            if session.topic_id:
                topic_ids = [session.topic_id]
            if session.subject:
                subjects = [session.subject]
        if not subjects and topic_ids:
            subjects = [self.curriculum.subject_of(topic_ids[0]) or ""]
            subjects = [item for item in subjects if item]
        difficulty = int(fields.get("difficulty") or session.difficulty)
        priority = str(fields.get("knowledge_priority") or session.knowledge_priority)
        if priority not in {item.value for item in KnowledgePriority}:
            priority = session.knowledge_priority
        question_type = str(fields.get("question_type") or QuestionType.SINGLE_BEST_ANSWER)
        if question_type not in {item.value for item in QuestionType}:
            question_type = QuestionType.SINGLE_BEST_ANSWER
        return ExamConfig(
            subjects=subjects,
            topic_ids=topic_ids,
            document_ids=[str(item) for item in (fields.get("document_ids") or session.document_ids) if self.store.get_document(str(item))][:6],
            page_from=int(fields.get("page_from") or 0),
            page_to=int(fields.get("page_to") or 0),
            question_count=max(1, min(60, int(fields.get("question_count") or session.question_count))),
            option_count=max(2, min(6, int(fields.get("option_count") or session.option_count))),
            difficulty=max(1, min(5, difficulty)),
            professor_id=str(fields["professor_id"]) if fields.get("professor_id") and self.store.get_professor(str(fields["professor_id"])) else None,
            knowledge_priority=priority,
            timed_seconds=max(0, int(fields.get("timed_seconds") or 0)),
            immediate_feedback=bool(fields.get("immediate_feedback", False)),
            answers_at_end=bool(fields.get("answers_at_end", True)),
            randomize=bool(fields.get("randomize", True)),
            weak_emphasis=bool(fields.get("weak_emphasis", False)),
            question_type=question_type,
            include_images=bool(fields.get("include_images", False)),
            one_at_a_time=bool(fields.get("one_at_a_time", True)),
            title=str(fields.get("title") or "").strip(),
            wrong_only=bool(fields.get("wrong_only", False)),
        )

    async def generate_exam(self, fields: dict[str, Any]) -> dict[str, Any]:
        config = self.exam_config(fields)
        notes: list[str] = []
        if config.professor_id:
            # A professor's own lectures are the best evidence for a paper in
            # their style; they are used unless the student picked documents.
            profile = self.store.get_professor(config.professor_id)
            if profile is not None and profile.subject and not config.subjects and not config.topic_ids:
                config.subjects = [profile.subject]
            if not fields.get("document_ids") and not config.document_ids:
                owned = [document.document_id for document in self.store.list_documents() if document.professor_id == config.professor_id and document.status == DocumentStatus.READY]
                if owned:
                    config.document_ids = owned[:12]
                    notes.append(f"Kaynak: hocanın kendi ders notları ({len(config.document_ids)} belge).")
        if not config.subjects and not config.topic_ids:
            raise GenerationError("Ders ya da konu seçilmedi.")
        if config.wrong_only:
            questions = self.generator.from_bank(config, wrong_question_ids=self.tutor._wrong_question_ids(), only_wrong=True)
            if not questions:
                raise GenerationError("Kayıtlı yanlış sorun yok; önce bir sınav çöz.")
            notes.append(f"Yanlış yaptığın {len(questions)} sorudan oluşturuldu.")
        elif fields.get("from_bank"):
            questions = self.generator.from_bank(config)
            if not questions:
                raise GenerationError("Soru bankasında bu ölçütlere uyan soru yok.")
            notes.append("Soru bankasından seçildi.")
        else:
            questions, generated_notes = await self.generator.generate(config)
            notes.extend(generated_notes)
        exam = self.exam_builder.build(config, questions, notes=notes)
        session = self.sessions.get()
        session.active_exam_id = exam.exam_id
        self.sessions.save(session)
        self._record("exam.generated", "Exam generated.", exam_id=exam.exam_id, questions=len(questions))
        # "open" asks the page to put the paper in front of the student: they
        # asked for it from the exam screen and the form is not the paper.
        self._emit({"kind": "exam_ready", "exam_id": exam.exam_id, "title": exam.title, "count": len(exam.question_ids), "open": True})
        return self.exam(exam.exam_id) or {}

    def exam_summary(self, exam: Any) -> dict[str, Any]:
        attempt = self.store.latest_attempt(exam.exam_id)
        return {
            "exam_id": exam.exam_id,
            "title": exam.title,
            "status": exam.status,
            "mode": exam.mode,
            "question_count": len(exam.question_ids),
            "created_at": exam.created_at.isoformat(),
            "finished_at": exam.finished_at.isoformat() if exam.finished_at else None,
            "score": attempt.score if attempt else None,
            "percent": attempt.analysis.get("percent") if attempt and attempt.analysis else None,
            "config": {
                "subjects": list(exam.config.subjects),
                "topic_ids": list(exam.config.topic_ids),
                "difficulty": exam.config.difficulty,
                "option_count": exam.config.option_count,
                "professor_id": exam.config.professor_id,
                "answers_at_end": exam.config.answers_at_end,
                "immediate_feedback": exam.config.immediate_feedback,
                "timed_seconds": exam.config.timed_seconds,
                "wrong_only": exam.config.wrong_only,
                "document_ids": list(exam.config.document_ids),
            },
            "notes": list(exam.generation_notes),
        }

    def exams(self) -> list[dict[str, Any]]:
        return [self.exam_summary(exam) for exam in self.store.list_exams(limit=50)]

    def exam(self, exam_id: str) -> dict[str, Any] | None:
        exam = self.store.get_exam(exam_id)
        if exam is None:
            return None
        questions = self.store.get_questions(exam.question_ids)
        attempt = self.store.latest_attempt(exam.exam_id)
        finished = attempt is not None and attempt.finished_at is not None
        reveal = finished or exam.config.immediate_feedback
        payload = self.exam_summary(exam)
        payload["questions"] = [
            {
                **question_payload(question, reveal=reveal and (finished or (attempt is not None and question.question_id in attempt.answers)), include_explanation=reveal and (finished or (attempt is not None and question.question_id in attempt.answers)), curriculum=self.curriculum),
                "answer": (attempt.answers[question.question_id].answer_key if attempt and question.question_id in attempt.answers else None),
                "flagged": (attempt.answers[question.question_id].flagged if attempt and question.question_id in attempt.answers else False),
                "correct": (attempt.answers[question.question_id].correct if attempt and question.question_id in attempt.answers and (finished or exam.config.immediate_feedback) else None),
            }
            for question in questions
        ]
        payload["attempt"] = {
            "attempt_id": attempt.attempt_id if attempt else None,
            "started_at": attempt.started_at.isoformat() if attempt else None,
            "finished_at": attempt.finished_at.isoformat() if attempt and attempt.finished_at else None,
            "answered": len([item for item in attempt.answers.values() if item.answer_key]) if attempt else 0,
            "current_index": attempt.current_index if attempt else 0,
        }
        payload["analysis"] = attempt.analysis if attempt and attempt.analysis else None
        return payload

    def start_exam(self, exam_id: str) -> dict[str, Any] | None:
        exam = self.store.get_exam(exam_id)
        if exam is None:
            return None
        attempt = self.store.latest_attempt(exam.exam_id)
        if attempt is None or attempt.finished_at is not None:
            attempt = new_attempt(exam)
            self.store.save_attempt(attempt)
            exam.status = "in_progress"
            exam.started_at = attempt.started_at
            self.store.save_exam(exam)
        session = self.sessions.get()
        session.active_exam_id = exam.exam_id
        session.active_attempt_id = attempt.attempt_id
        self.sessions.save(session)
        return self.exam(exam_id)

    def answer(
        self,
        exam_id: str,
        question_id: str,
        answer_key: str | None,
        *,
        elapsed_seconds: float | None = None,
        flagged: bool | None = None,
        current_index: int | None = None,
        confidence: str | None = None,
        reasoning: str = "",
        submission_id: str | None = None,
    ) -> dict[str, Any] | None:
        """Record an answer; optionally how sure the student was and why.

        Confidence and reasoning never touch the mark. In a practice sitting
        they become an understanding event at once (the answer is graded on
        the spot); in a paper marked at the end they wait on the attempt
        until it is finished. A missing confidence is unknown, not a guess.
        """
        level = str(confidence or "").strip().lower() or None
        if level is not None and level not in CONFIDENCE_LEVELS:
            raise ValueError("Güven bildirimi 'sure', 'unsure' ya da 'guess' olmalı.")
        exam = self.store.get_exam(exam_id)
        question = self.store.get_question(question_id)
        if exam is None or question is None or question_id not in exam.question_ids:
            return None
        attempt = self.store.latest_attempt(exam.exam_id)
        if attempt is None or attempt.finished_at is not None:
            attempt = new_attempt(exam)
            exam.status = "in_progress"
            exam.started_at = attempt.started_at
            self.store.save_exam(exam)
        previous = attempt.answers.get(question_id)
        answered_before = previous is not None and bool(previous.answer_key)
        entry = record_answer(attempt, question, answer_key, elapsed_seconds=elapsed_seconds, flagged=flagged)
        if level is not None or reasoning.strip():
            entry.confidence = level if level is not None else (previous.confidence if previous is not None else None)
            entry.reasoning = " ".join(reasoning.split())[:1200] or (previous.reasoning if previous is not None else "")
        elif previous is not None:
            entry.confidence = previous.confidence
            entry.reasoning = previous.reasoning
        if current_index is not None:
            attempt.current_index = max(0, int(current_index))
        self.store.save_attempt(attempt)
        result: dict[str, Any] = {"exam_id": exam_id, "question_id": question_id, "answer": entry.answer_key, "flagged": entry.flagged, "confidence": entry.confidence, "answered": len([item for item in attempt.answers.values() if item.answer_key])}
        if exam.config.immediate_feedback and entry.answer_key:
            # Only the first answer to a question is an attempt at recall: in
            # immediate feedback the key is revealed with it, so a later send
            # for the same question — flagging it re-sends the answer, and the
            # options stay clickable — is not a second try. Recording it would
            # state attempts, a streak and a confusion the student never made.
            if entry.correct is not None and not answered_before:
                self.learning.record(question, bool(entry.correct), chosen_key=entry.answer_key)
                event = self.study.understanding.record_event(
                    question,
                    correct=bool(entry.correct),
                    answer_key=entry.answer_key,
                    confidence=entry.confidence,
                    reasoning=entry.reasoning,
                    source="exam",
                    exam_id=exam_id,
                    attempt_id=attempt.attempt_id,
                    submission_id=submission_id or f"{attempt.attempt_id}:{question_id}",
                )
                result["event_id"] = event.get("event_id")
                wanted, why = self.study.understanding.wants_explanation(question, exam_id=exam_id, immediate=True)
                result["explain"] = wanted
                result["explain_reason"] = why
            result["feedback"] = explanation_payload(question, entry.answer_key)
        return result

    def finish_exam(self, exam_id: str) -> dict[str, Any] | None:
        """Close the sitting — once.

        Finalization has effects: mastery is recorded for a simulation's
        answers and the session's difficulty may move. They happen exactly
        once per attempt, under the academy lock and inside one store
        transaction: a second request for a finished attempt (a repeated
        click, a retried call) returns the stored result untouched — with
        the adaptive verdict of its own day, not one recomputed from the
        session's later difficulty — and a failure between the writes
        leaves nothing half-applied, so the next call finalizes cleanly.
        """
        with self._lock:
            exam = self.store.get_exam(exam_id)
            if exam is None:
                return None
            attempt = self.store.latest_attempt(exam.exam_id)
            if attempt is not None and attempt.finished_at is not None:
                return self.exam(exam_id)
            if attempt is None:
                attempt = new_attempt(exam)
            questions = self.store.get_questions(exam.question_ids)
            attempt.finished_at = utc_now()
            session = self.sessions.get()
            with self.store.transaction():
                if not exam.config.immediate_feedback:
                    # A study sitting recorded every answer as it was given;
                    # only a simulation's answers wait for the end.
                    for question in questions:
                        entry = attempt.answers.get(question.question_id)
                        if entry is not None and entry.answer_key and entry.correct is not None:
                            self.learning.record(question, bool(entry.correct), chosen_key=entry.answer_key)
                analysis = analyse_attempt(exam, questions, attempt, curriculum=self.curriculum, mastery_levels=self.learning.levels())
                # What the student said about each answer travels with the
                # result: a paper marked at the end records its understanding
                # events here, once, by attempt and question.
                events: dict[str, str] = {}
                for question in questions:
                    entry = attempt.answers.get(question.question_id)
                    if entry is None or not entry.answer_key or entry.correct is None:
                        continue
                    event = self.study.understanding.record_event(
                        question,
                        correct=bool(entry.correct),
                        answer_key=entry.answer_key,
                        confidence=entry.confidence,
                        reasoning=entry.reasoning,
                        source="exam",
                        exam_id=exam_id,
                        attempt_id=attempt.attempt_id,
                        submission_id=f"{attempt.attempt_id}:{question.question_id}",
                    )
                    events[question.question_id] = str(event.get("event_id"))
                analysis["events"] = events
                self.study.understanding.confirm_follow_ups()
                if session.adaptive_difficulty and analysis["total"] >= 5:
                    recent = [bool(attempt.answers[question_id].correct) for question_id in exam.question_ids if question_id in attempt.answers and attempt.answers[question_id].correct is not None]
                    suggested, reason = self.learning.suggest_difficulty(session.difficulty, recent)
                    analysis["adaptive"] = {"previous": session.difficulty, "suggested": suggested, "reason": reason}
                    if suggested != session.difficulty:
                        session.difficulty = suggested
                        self.sessions.save(session)
                # The verdict is part of the result: saved with it, not after it.
                attempt.score = analysis["score"]
                attempt.analysis = analysis
                self.store.save_attempt(attempt)
                exam.status = "completed"
                exam.finished_at = attempt.finished_at
                self.store.save_exam(exam)
        self._record("exam.finished", "Exam finished.", exam_id=exam_id, percent=analysis.get("percent"))
        self._emit({"kind": "exam_finished", "exam_id": exam_id, "title": exam.title, "percent": analysis.get("percent")})
        return self.exam(exam_id)

    def delete_exam(self, exam_id: str) -> bool:
        return self.store.delete_exam(exam_id)

    def question_bank(self, filters: dict[str, Any] | None = None) -> dict[str, Any]:
        filters = filters or {}
        subject = valid_subject(filters.get("subject"))
        if filters.get("subject") and subject is None:
            # An unknown subject must narrow to nothing: passing None down would
            # drop the filter and answer a typo with the whole bank.
            return {"questions": [], "counts": self.store.count_questions(), "total": 0, "problems": [f"Bilinmeyen ders: {filters['subject']}"]}
        questions = self.store.query_questions(
            subject=subject,
            topic_id=str(filters.get("topic_id") or "") or None,
            origin=str(filters.get("origin") or "") or None,
            professor_id=str(filters.get("professor_id") or "") or None,
            difficulty=int(filters["difficulty"]) if filters.get("difficulty") else None,
            document_id=str(filters.get("document_id") or "") or None,
            text=str(filters.get("text") or "") or None,
            with_answer_key=(bool(filters["with_answer_key"]) if "with_answer_key" in filters and filters["with_answer_key"] is not None else None),
            limit=int(filters.get("limit") or BANK_LIST_LIMIT),
        )
        answered: dict[str, bool | None] = {}
        for attempt in self.store.list_attempts(limit=200):
            for question_id, entry in attempt.answers.items():
                if question_id not in answered and entry.answer_key:
                    answered[question_id] = entry.correct
        wanted = filters.get("answered")
        if wanted in ("answered", "unanswered", "correct", "incorrect"):
            questions = [
                question
                for question in questions
                if (wanted == "answered" and question.question_id in answered)
                or (wanted == "unanswered" and question.question_id not in answered)
                or (wanted == "correct" and answered.get(question.question_id) is True)
                or (wanted == "incorrect" and answered.get(question.question_id) is False)
            ]
        items = []
        for question in questions:
            item = question_payload(question, reveal=True, include_explanation=True, curriculum=self.curriculum)
            item["last_result"] = answered.get(question.question_id)
            item["problems"] = validate_question(question, require_explanation=False)
            item["support"] = self.study.reviewer.status_of(question)
            item["flags"] = len([flag for flag in question.metadata.get("flags", []) if isinstance(flag, dict)])
            item["invalidated"] = bool(question.metadata.get("invalidated"))
            items.append(item)
        return {"questions": items, "counts": self.store.count_questions(), "total": len(items), "problems": []}

    def delete_question(self, question_id: str) -> bool:
        return self.store.delete_question(question_id)

    def set_answer_key(self, question_id: str, key: str | None) -> dict[str, Any] | None:
        question = self.store.get_question(question_id)
        if question is None:
            return None
        normalized = str(key or "").strip().upper() or None
        if normalized and question.option(normalized) is None:
            raise ValueError("Bu harfte bir seçenek yok.")
        question.correct_key = normalized
        self.store.save_question(question)
        return question_payload(question, reveal=True, include_explanation=True, curriculum=self.curriculum)

    # ------------------------------------------------------------------
    # professors
    # ------------------------------------------------------------------

    def professors(self) -> list[dict[str, Any]]:
        return [self.profiler.to_dict(profile) for profile in self.store.list_professors()]

    def professor(self, profile_id: str) -> dict[str, Any] | None:
        profile = self.store.get_professor(profile_id)
        if profile is None:
            return None
        payload = self.profiler.to_dict(profile)
        payload["directive"] = StyleProfiler.directive(profile)
        payload["questions"] = [question_payload(question, reveal=True, include_explanation=False, curriculum=self.curriculum) for question in self.store.get_questions(profile.question_ids)]
        payload["documents"] = self.professor_documents(profile.profile_id)
        return payload

    def _rebuild_profile(self, profile_id: str, name: str, subject: str | None, question_ids: list[str], *, notes: str = "") -> dict[str, Any]:
        questions = self.store.get_questions(question_ids)
        existing = self.store.get_professor(profile_id)
        profile = self.profiler.profile(name, questions, subject=subject or (existing.subject if existing else None), profile_id=profile_id, notes=notes or (existing.notes if existing else ""))
        self.store.save_professor(profile)
        self._emit({"kind": "professor_updated", "profile_id": profile.profile_id, "sample_size": profile.sample_size})
        return self.professor(profile.profile_id) or {}

    def create_professor(self, name: str, subject: str | None = None) -> dict[str, Any]:
        profile = self.profiler.profile(name, [], subject=valid_subject(subject))
        self.store.save_professor(profile)
        return self.professor(profile.profile_id) or {}

    def delete_professor(self, profile_id: str, *, delete_questions: bool = False) -> bool:
        profile = self.store.get_professor(profile_id)
        if profile is None:
            return False
        if delete_questions:
            for question_id in profile.question_ids:
                self.store.delete_question(question_id)
        return self.store.delete_professor(profile_id)

    async def import_questions(self, *, professor_id: str | None, name: str | None, subject: str | None, text: str | None = None, path: str | None = None, image_path: str | None = None, use_model: bool = True) -> dict[str, Any]:
        """Import professor questions from text, a file (PDF/txt) or an image."""
        subject_value = valid_subject(subject)
        source_text = str(text or "")
        document_id: str | None = None
        notes: list[str] = []
        if path:
            document, _created = self.pipeline.import_file(path, subject=subject_value, professor_id=professor_id, tags=["sınav"])
            document = await asyncio.to_thread(self.pipeline.process, document.document_id)
            if document.status != DocumentStatus.READY:
                raise DocumentError(document.status_detail)
            document_id = document.document_id
            source_text = "\n\n".join(page.text for page in self.store.get_pages(document.document_id))
            if len(source_text.strip()) < 40 and use_model and self.model.available:
                notes.append("PDF'de metin bulunamadı; sayfalar görüntüden okunuyor.")
                source_text = await self._ocr_pages(document.document_id)
        if image_path:
            if not (use_model and self.model.available):
                raise MedicalModelError("Görselden soru çıkarmak için model sağlayıcısı gerekli.")
            source_text = (source_text + "\n\n" + await self._read_image(Path(image_path))).strip()
        if not source_text.strip():
            raise DocumentError("İçe aktarılacak metin yok.")
        parsed = QuestionImportParser().parse(source_text)
        notes.extend(parsed.notes)
        extracted = parsed.questions
        if (len(extracted) == 0 or len(extracted) < source_text.count("?") // 3) and use_model and self.model.available:
            data = await self.model.structured("question_extraction", question_extraction_prompt(source_text), QUESTION_EXTRACTION_SCHEMA, system_prompt=PIPELINE_SYSTEM)
            from app.medical.professor import ParsedQuestion

            model_items = []
            for index, item in enumerate(data.get("questions", []), start=1):
                options = [(str(option.get("key", "")).strip().upper()[:1], str(option.get("text", "")).strip()) for option in item.get("options", []) if isinstance(option, dict)]
                options = [(key, text) for key, text in options if key and text]
                if len(options) < 2:
                    continue
                answer = item.get("answer_key")
                answer = str(answer).strip().upper()[:1] if answer else None
                model_items.append(ParsedQuestion(number=str(item.get("number") or index), stem=str(item.get("stem", "")).strip(), options=options, answer_key=answer if answer and any(key == answer for key, _ in options) else None, has_image=bool(item.get("has_image"))))
            if len(model_items) > len(extracted):
                # Only now is the claim true: the note is written where the model's
                # reading is the one that gets stored, not where it was merely asked.
                notes.append("Deterministik ayrıştırma yetersiz kaldı; model yapısal çıkarım yaptı.")
                extracted = model_items
            elif model_items:
                notes.append("Model de denendi ama deterministik ayrıştırmadan fazlasını okuyamadı; okunan sorular ayrıştırıcıdan geliyor.")
        if not extracted:
            raise DocumentError("Metinden soru çıkarılamadı; numaralı sorular ve A) B) C) biçimli şıklar bekleniyor.")
        profile = self.store.get_professor(professor_id) if professor_id else None
        if profile is None:
            profile = self.profiler.profile(name or "Hoca", [], subject=subject_value)
            self.store.save_professor(profile)
        existing = self.store.get_questions(profile.question_ids)
        added: list[Question] = []
        skipped = 0
        for item in extracted:
            question = imported_question(item, subject=subject_value or profile.subject or "anatomy", professor_id=profile.profile_id, document_id=document_id)
            if any(q.stem.strip().casefold() == question.stem.strip().casefold() for q in existing + added):
                skipped += 1
                continue
            self.store.save_question(question)
            added.append(question)
        if skipped:
            notes.append(f"{skipped} soru zaten kayıtlıydı, atlandı.")
        without_key = sum(1 for question in added if not question.has_answer_key)
        if without_key:
            notes.append(f"{without_key} sorunun cevap anahtarı metinde yoktu; anahtarı sonradan işaretleyebilirsin. Anahtar asla tahmin edilmez.")
        payload = self._rebuild_profile(profile.profile_id, profile.name, profile.subject, profile.question_ids + [question.question_id for question in added])
        payload["import"] = {"added": len(added), "skipped": skipped, "without_key": without_key, "notes": notes, "document_id": document_id}
        self._record("professor.imported", "Professor questions imported.", profile_id=profile.profile_id, added=len(added))
        return payload

    async def _ocr_pages(self, document_id: str, *, max_pages: int = 20) -> str:
        texts: list[str] = []
        for page in self.store.get_pages(document_id)[:max_pages]:
            try:
                png = await asyncio.to_thread(self.pipeline.render_page, document_id, page.page_number, scale=PAGE_IMAGE_SCALE)
                text = await self.model.text("page_ocr", "Transcribe every question, option letter and answer line on this exam page exactly as written, in reading order. Output plain text only.", system_prompt=PIPELINE_SYSTEM, images=[{"data": png, "mime_type": "image/png", "detail": "high"}], task_type="vision")
            except (MedicalModelError, DocumentError):
                continue
            texts.append(text)
        return "\n\n".join(texts)

    async def _read_image(self, path: Path) -> str:
        if not path.is_file():
            raise DocumentError("Görsel bulunamadı.")
        suffix = path.suffix.lower()
        mime = {"png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg", "webp": "image/webp"}.get(suffix.lstrip("."))
        if mime is None:
            raise DocumentError("Desteklenen görsel türleri: PNG, JPG, WEBP.")
        data = path.read_bytes()
        if len(data) > 15 * 1024 * 1024:
            raise DocumentError("Görsel çok büyük.")
        return await self.model.text("image_questions", "Transcribe every exam question, option letter and any answer line in this image exactly as written, in reading order. Output plain text only; do not add answers that are not shown.", system_prompt=PIPELINE_SYSTEM, images=[{"data": data, "mime_type": mime, "detail": "high"}], task_type="vision")

    # ------------------------------------------------------------------
    # professors from the material: who wrote a lecture, and their questions
    # ------------------------------------------------------------------

    def professor_for_document(self, document: StudyDocument) -> ProfessorMention | None:
        """The lecturer a document names: its opening pages first, then its file name.

        A deck whose title slide is a picture carries the name only in what the
        vision pass wrote about that page, so those descriptions are read too —
        after the text, never instead of it.
        """
        opening = self.store.get_pages(document.document_id, page_from=1, page_to=MENTION_PAGES)
        mentions = professor_mentions(opening)
        complete = [mention for mention in mentions if mention.complete]
        if complete:
            return complete[0]
        from_title = professor_from_title(document.title) or professor_from_title(Path(document.file_name).stem)
        if from_title is not None:
            return from_title
        for page in opening[:2]:
            # Only a cover slide with no text of its own: a portrait inside a
            # lecture also names a person, and that person is not the lecturer.
            if page.char_count >= 40 or not looks_like_cover(page.visual_summary):
                continue
            described = mentions_in_text(page.visual_summary)
            if described:
                return described[0]
        if document.page_count > MENTION_PAGES:
            # Some lecturers sign the closing slide instead of the first.
            closing = professor_mentions(self.store.get_pages(document.document_id, page_from=max(MENTION_PAGES + 1, document.page_count - 1), page_to=document.page_count), limit_pages=None)
            signed = [mention for mention in closing if mention.complete]
            if signed:
                return signed[0]
        return mentions[0] if mentions else None

    def _profile_for_mention(self, mention: ProfessorMention, subject: str | None) -> tuple[ProfessorProfile, bool]:
        """The profile this person already has, else a new one; never two for one lecturer."""
        for profile in self.store.list_professors():
            if same_person(professor_key(profile.name), mention.key):
                # Keep the fuller spelling: a title and a whole first name beat an initial.
                if mention.complete and fuller_name(mention.name, profile.name):
                    profile.name = mention.name
                    self.store.save_professor(profile)
                if subject and not profile.subject:
                    profile.subject = subject
                    self.store.save_professor(profile)
                return profile, False
        profile = self.profiler.profile(mention.name, [], subject=subject)
        self.store.save_professor(profile)
        return profile, True

    def _mining_scope(self, *, set_id: str | None, document_ids: list[str] | None) -> list[StudyDocument]:
        if set_id:
            record = self.store.get_meta(LECTURE_SET_PREFIX + str(set_id))
            if not isinstance(record, dict):
                raise DocumentError("Ders seti bulunamadı.")
            wanted = list(record.get("document_ids", []))
        elif document_ids:
            wanted = [str(item) for item in document_ids]
        else:
            wanted = [document.document_id for document in self.store.list_documents()]
        documents = []
        for document_id in wanted:
            document = self.store.get_document(document_id)
            if document is not None and document.status == DocumentStatus.READY:
                documents.append(document)
        return documents

    def mine_questions(self, *, set_id: str | None = None, document_ids: list[str] | None = None, progress: Callable[[int, int, str], None] | None = None) -> dict[str, Any]:
        """Attribute each lecture to the professor it names and file the questions it carries.

        Deterministic only: the parser reads numbered stems with lettered
        options, a key is stored only when the material states it, and a
        question found on a page with a picture keeps that page as its
        figure. A lecturer named with a single token ("Dr. Hasan") is kept
        but reported as incomplete rather than guessed at.
        """
        documents = self._mining_scope(set_id=set_id, document_ids=document_ids)
        parser = QuestionImportParser()
        # "Aşağıdaki ifadelerden hangisi yanlıştır?" opens many questions; the
        # options tell them apart, so a question is known by stem and options.
        existing_stems = {question_fingerprint(question.stem, [option.text for option in question.options]) for question in self.store.query_questions(limit=100_000)}
        per_professor: dict[str, dict[str, Any]] = {}
        books: list[str] = []
        papers: list[str] = []
        unread: list[str] = []
        from_pictures = 0
        owners_seen: dict[str, set[str]] = {}
        # Question ids gathered per profile; the profile is re-read at the end so
        # a fuller name learned from a later lecture is what gets rebuilt.
        touched: dict[str, list[str]] = {}
        unattributed: list[str] = []
        incomplete: list[str] = []
        added_total = 0
        skipped = 0
        without_key = 0
        with_image = 0
        documents_with_questions = 0
        for index, document in enumerate(documents, start=1):
            if progress is not None and (index % 10 == 0 or index == len(documents)):
                progress(index, len(documents), document.title)
            pages = self.store.get_pages(document.document_id)
            if pages and all(page.char_count < OCR_MIN_CHARS for page in pages) and not any(page.visual_summary for page in pages):
                # A scan nobody has read yet names nobody and holds no question.
                unread.append(document.title)
                continue
            book = looks_like_book(pages)
            # A book's front matter names its authors, not the student's
            # lecturer; its questions are kept, but under nobody.
            mention = None if book else self.professor_for_document(document)
            if book:
                books.append(document.title)
            profile: ProfessorProfile | None = None
            if mention is not None:
                profile, created = self._profile_for_mention(mention, document.subject)
                if created and not mention.complete and mention.name not in incomplete:
                    # A broken spelling that merged into a known lecturer needs no fixing.
                    incomplete.append(mention.name)
                entry = per_professor.setdefault(profile.profile_id, {"profile_id": profile.profile_id, "name": profile.name, "documents": 0, "questions_added": 0})
                entry["name"] = profile.name  # a fuller spelling may have arrived with this lecture
                entry["documents"] += 1
                if document.professor_id != profile.profile_id:
                    document.professor_id = profile.profile_id
                    self.store.save_document(document)
                touched.setdefault(profile.profile_id, [])
                if mention.source == "visual":
                    from_pictures += 1
            elif not book:
                unattributed.append(document.title)
            text = "\n\n".join(page.text for page in pages)
            # A lecture keeps every question under its lecturer; only a compiled
            # paper is cut at the headings that name one.
            probe = parser.parse(text)
            paper = looks_like_question_paper(text, len(probe.questions))
            owned = any(item.owner for item in probe.questions)
            if owned:
                papers.append(document.title)
                if document.title in unattributed:
                    unattributed.remove(document.title)
            sections = split_by_professor(text) if paper and not owned else [(None, text)]
            added_here = 0
            for section_mention, section_text in sections:
                owner = profile
                if section_mention is not None and section_mention.source == "heading":
                    owner, _created = self._profile_for_mention(section_mention, document.subject)
                    touched.setdefault(owner.profile_id, [])
                    per_professor.setdefault(owner.profile_id, {"profile_id": owner.profile_id, "name": owner.name, "documents": 0, "questions_added": 0})
                parsed = parser.parse(section_text)
                for item in parsed.questions:
                    folded = question_fingerprint(item.stem, [text for _, text in item.options]) if item.stem.strip() else ""
                    if not folded or folded in existing_stems:
                        skipped += 1
                        continue
                    existing_stems.add(folded)
                    question_owner = owner
                    if item.owner:
                        # An exam export names the author of every question.
                        owner_mention = mention_from_owner(item.owner)
                        if owner_mention is not None:
                            question_owner, _created = self._profile_for_mention(owner_mention, folder_subject([item.department or ""]) or document.subject)
                            entry = per_professor.setdefault(question_owner.profile_id, {"profile_id": question_owner.profile_id, "name": question_owner.name, "documents": 0, "questions_added": 0})
                            entry["name"] = question_owner.name
                            seen_docs = owners_seen.setdefault(question_owner.profile_id, set())
                            if document.document_id not in seen_docs:
                                seen_docs.add(document.document_id)
                                entry["documents"] += 1
                            touched.setdefault(question_owner.profile_id, [])
                    page_number = self._page_of(pages, item.stem)
                    page = next((page for page in pages if page.page_number == page_number), None) if page_number else None
                    image_ref = f"{document.document_id}|{page_number}" if item.has_image and page is not None and page.image_count else None
                    subject = folder_subject([item.department or ""]) or document.subject or (question_owner.subject if question_owner else None) or ""
                    exam_item = bool(item.owner) or (paper and not book)
                    tags = ["kitaptan" if book else "çıkmış" if exam_item else "ders notundan", document.title[:60]]
                    if item.committee:
                        tags.append(f"Komite {item.committee}")
                    if item.department:
                        tags.append(item.department[:40])
                    question = imported_question(
                        item,
                        subject=subject,
                        professor_id=question_owner.profile_id if question_owner else None,
                        document_id=document.document_id,
                        origin=QuestionOrigin.IMPORTED_EXAM if exam_item else QuestionOrigin.LECTURE_DERIVED,
                        page_number=page_number,
                        image_ref=image_ref,
                        tags=tags,
                    )
                    question.metadata.update({key: value for key, value in (("owner", item.owner), ("department", item.department), ("committee", item.committee), ("student_marked", item.student_marked)) if value})
                    if "taranmış metin" in document.tags:
                        question.metadata["ocr"] = True
                    if item.warnings:
                        question.metadata["warnings"] = list(item.warnings)
                    self.store.save_question(question)
                    added_here += 1
                    if not question.has_answer_key:
                        without_key += 1
                    if image_ref:
                        with_image += 1
                    if question_owner is not None:
                        per_professor[question_owner.profile_id]["questions_added"] += 1
                        touched.setdefault(question_owner.profile_id, []).append(question.question_id)
            if added_here:
                documents_with_questions += 1
                added_total += added_here
        for profile_id, new_ids in touched.items():
            profile = self.store.get_professor(profile_id)
            if profile is None:
                continue
            new_questions = self.store.get_questions(new_ids)
            if any(question.origin == QuestionOrigin.IMPORTED_EXAM for question in new_questions):
                notes = "Sorular çıkmış sınav kâğıtlarından alındı (her sorunun 'Soru Sahibi' satırı); cevap anahtarı kâğıdın kendi işaretinden okundu."
            else:
                notes = profile.notes or ("Sorular ders notlarındaki örnek/tekrar sorularından çıkarıldı; gerçek sınav kâğıdı yüklenince tarz kesinleşir." if new_ids else "")
            self._rebuild_profile(profile.profile_id, profile.name, profile.subject, list(dict.fromkeys(profile.question_ids + new_ids)), notes=notes)
        report = {
            "documents": len(documents),
            "attributed": sum(entry["documents"] for entry in per_professor.values()),
            "documents_with_questions": documents_with_questions,
            "questions_added": added_total,
            "skipped": skipped,
            "without_key": without_key,
            "with_image": with_image,
            "professors": sorted(per_professor.values(), key=lambda entry: (-entry["documents"], entry["name"])),
            "unattributed": unattributed[:60],
            "incomplete": incomplete[:20],
            "books": books[:40],
            "papers": papers[:40],
            "unread": unread[:40],
            "from_pictures": from_pictures,
            "notes": [],
            "mined_at": utc_now().isoformat(),
        }
        if unattributed:
            report["notes"].append(f"{len(unattributed)} belgede hoca adı bulunamadı; bu belgeler hocasız kaldı.")
        if unread:
            report["notes"].append(f"{len(unread)} belge henüz okunmamış tarama: önce “Metne çevir (OCR)” gerekir ({', '.join(unread[:3])}{'…' if len(unread) > 3 else ''}).")
        if papers:
            report["notes"].append(f"{len(papers)} belge çıkmış sınav kâğıdı: her soru kendi 'Soru Sahibi' satırındaki hocaya bağlandı ({', '.join(papers[:3])}{'…' if len(papers) > 3 else ''}).")
        if books:
            report["notes"].append(f"{len(books)} belge yayımlanmış kitap: soruları alındı ama kapaktaki yazarlar hoca sayılmadı ({', '.join(books[:3])}{'…' if len(books) > 3 else ''}).")
        if from_pictures:
            report["notes"].append(f"{from_pictures} belgede hoca adı, kapak sayfası görüntüden okunduğu için şekil incelemesinden alındı.")
        if incomplete:
            report["notes"].append("Adı eksik okunan hocalar: " + ", ".join(incomplete) + ". Profil adını Hoca tarzı ekranından düzeltebilirsin.")
        if added_total == 0:
            report["notes"].append("Belgelerde numaralı, şıklı soru bloğu bulunamadı: bu dosyalar ders notu, sınav kâğıdı değil. Çıkmış soruları “Sınav dosyası yükle” ile ekleyince hoca tarzı oluşur.")
        if without_key:
            report["notes"].append(f"{without_key} sorunun cevap anahtarı metinde yoktu; anahtar asla tahmin edilmez.")
        if set_id:
            record = self.store.get_meta(LECTURE_SET_PREFIX + str(set_id))
            if isinstance(record, dict):
                record["mined"] = {key: value for key, value in report.items() if key not in {"unattributed", "professors"}}
                record["mined"]["professors"] = len(per_professor)
                self._save_lecture_set(record)
        self._record("professor.mined", "Lectures attributed and their questions mined.", documents=len(documents), professors=len(per_professor), questions=added_total)
        self._emit({"kind": "professors_mined", "set_id": set_id, "documents": len(documents), "professors": len(per_professor), "questions": added_total})
        return report

    @staticmethod
    def _page_of(pages: list[DocumentPage], stem: str) -> int | None:
        needle = " ".join(fold(stem).split())[:40]
        if len(needle) < 12:
            return None
        for page in pages:
            if needle in " ".join(fold(page.text).split()):
                return page.page_number
        return None

    async def mine_questions_job(self, *, set_id: str | None = None, document_ids: list[str] | None = None) -> dict[str, Any]:
        loop = asyncio.get_running_loop()

        def progress(done: int, total: int, current: str) -> None:
            loop.call_soon_threadsafe(self._emit, {"kind": "lecture_set_progress", "set_id": set_id, "stage": "mining", "done": done, "total": total, "current": current})

        report = await asyncio.to_thread(self.mine_questions, set_id=set_id, document_ids=document_ids, progress=progress)
        return {"mine": report}

    def professor_documents(self, profile_id: str) -> list[dict[str, Any]]:
        return [self.pipeline.payload(document) for document in self.store.list_documents() if document.professor_id == profile_id]

    def reset_professor(self, profile_id: str) -> dict[str, Any] | None:
        profile = self.store.get_professor(profile_id)
        if profile is None:
            return None
        return self._rebuild_profile(profile.profile_id, profile.name, profile.subject, [])

    # ------------------------------------------------------------------
    # progress and anatomy
    # ------------------------------------------------------------------

    def progress(self) -> dict[str, Any]:
        mastery = sorted(self.learning.all(), key=lambda item: (item.level != "weak", item.level != "moderate", -item.attempts))
        return {
            "summary": self.learning.summary(),
            "review_queue": self.learning.review_queue(limit=20),
            "weak": [self.learning.mastery_payload(item) for item in self.learning.weak(limit=12)],
            "strong": [self.learning.mastery_payload(item) for item in self.learning.strong(limit=12)],
            "all": [self.learning.mastery_payload(item) for item in mastery[:200]],
            "insights": self.learning.insights(limit=6),
            "exams": self.exams()[:10],
        }

    def anatomy_structures(self) -> dict[str, Any]:
        return {
            "hierarchy": self.anatomy.hierarchy(),
            "assets": {
                "directory": str(self.anatomy.assets.directory) if self.anatomy.assets.directory else None,
                "available": self.anatomy.assets.available_ids(),
                "problems": self.anatomy.assets.problems,
            },
            # A scene is a region drawn as one view from the licensed meshes the
            # manifest lists; the page offers only what the manifest names.
            "scenes": self.anatomy.assets.scenes(),
            "source": self._source_note,
        }

    def anatomy_structure(self, structure_id: str) -> dict[str, Any] | None:
        return self.anatomy.describe(structure_id)

    def anatomy_quiz(self, structure_id: str, *, count: int = 5) -> list[dict[str, Any]]:
        return self.anatomy.quiz(structure_id, count=max(1, min(20, int(count))), seed=new_id("aq"))

    def anatomy_mesh(self, structure_id: str) -> dict[str, Any]:
        try:
            return self.anatomy.assets.load_mesh(structure_id)
        except (FileNotFoundError, ValueError, OSError) as exc:
            described = self.anatomy.assets.describe(structure_id)
            # ``available`` and ``reason`` come last: spread first, the registry's
            # own "available: True" would land back on top of a model that could
            # not be loaded, and the student would be told nothing is registered.
            # describe() only explains what it can diagnose (nothing registered,
            # file missing); for a registered file that will not load, the
            # loader's message is the only true one.
            return {**described, "available": False, "reason": described.get("reason") or str(exc)}

    def record_anatomy_answer(self, structure_id: str, landmark_id: str | None, correct: bool, *, submission_id: str | None = None, confidence: str | None = None) -> dict[str, Any]:
        """Move the structure's mastery by one bell-ringer or quiz answer.

        ``submission_id`` is the page's name for one station's one answer:
        a save sent again because its reply was lost is answered from
        memory and moves nothing a second time. The memory is bounded and
        lives with the process — a retry belongs to the same sitting.
        """
        key = str(submission_id or "").strip()[:80]
        with self._lock:
            if key and key in self._anatomy_submissions:
                return {**self._anatomy_submissions[key], "repeated": True}
            concept_id = f"anatomy.{structure_id}" + (f".{landmark_id}" if landmark_id else "")
            structure = self.anatomy.get(structure_id)
            question = Question(question_id="anatomy-quiz", subject="anatomy", stem="anatomy quiz", options=[], correct_key=None, topic_id=structure.topic_id if structure else None, concept_ids=[concept_id])
            updated = self.learning.record(question, correct)
            result: dict[str, Any] = {"mastery": [self.learning.mastery_payload(item) for item in updated]}
            if confidence:
                event = self.study.understanding.record_event(question, correct=correct, answer_key=None, confidence=confidence, source="anatomy", submission_id=key or None)
                result["event_id"] = event.get("event_id")
            if key:
                result["submission_id"] = key
                self._anatomy_submissions[key] = result
                while len(self._anatomy_submissions) > ANATOMY_SUBMISSION_MEMORY:
                    del self._anatomy_submissions[next(iter(self._anatomy_submissions))]
            return dict(result)


# ---------------------------------------------------------------------------
# factory
# ---------------------------------------------------------------------------


def create_medical_academy(
    *,
    settings: Any,
    provider_gateway: Any | None,
    tool_executor: Any | None = None,
    diagnostics: Any | None = None,
) -> MedicalAcademy:
    directory_value = getattr(settings, "medical_directory", None)
    directory = Path(directory_value).expanduser() if directory_value else None
    store = MedicalStore(directory / "jarvis_medical.sqlite3" if directory else None)
    curriculum = Curriculum()
    structures, terms, source_note = load_anatomy_data()
    concepts = default_concept_graph(structures)
    terminology = TerminologyIndex(structures, terms, concepts.all())
    anatomy = AnatomyLab(structures, curriculum, assets_directory=(directory / "anatomy_assets") if directory else None, source_note=source_note)
    converter = OfficeConverter(directory / "converted" if directory else None, detect=bool(getattr(settings, "medical_office_conversion", True)))
    pipeline = DocumentPipeline(
        store,
        directory=directory,
        max_pages=int(getattr(settings, "medical_max_document_pages", 800) or 800),
        max_bytes=int(getattr(settings, "medical_max_document_bytes", 60 * 1024 * 1024) or 60 * 1024 * 1024),
        vision_pages_per_document=int(getattr(settings, "medical_vision_pages_per_document", 12) or 0),
        converter=converter.to_pdf if converter.available else None,
    )
    model = MedicalModelClient(provider_gateway, model=getattr(settings, "medical_model", "") or None, diagnostics=diagnostics)
    academy = MedicalAcademy(
        store=store,
        curriculum=curriculum,
        terminology=terminology,
        concepts=concepts,
        anatomy=anatomy,
        pipeline=pipeline,
        model=model,
        diagnostics=diagnostics,
        source_note=source_note,
        narration_voice=str(getattr(settings, "medical_narration_voice", "local") or "local"),
        narration_checkpoint_every=int(getattr(settings, "medical_narration_checkpoint_every", 3) or 0),
        source_review=bool(getattr(settings, "medical_source_review", False)),
    )
    academy.converter = converter
    if tool_executor is not None:
        academy.register_tools(tool_executor)
    return academy


__all__ = ["MedicalAcademy", "create_medical_academy", "MEDICAL_TOOLS", "GenerationError", "MedicalModelError", "DocumentError", "NarrationError", "timedelta"]
