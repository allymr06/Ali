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
from app.medical.documents import DocumentError, DocumentPipeline
from app.medical.generation import ExamBuilder, GenerationError, QuestionGenerator
from app.medical.intents import MedicalIntentParser
from app.medical.learning import LearningEngine
from app.medical.model import MedicalModelClient, MedicalModelError
from app.medical.models import (
    COMPARISON_LABELS_TR,
    DocumentStatus,
    ExamConfig,
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
from app.medical.professor import QuestionImportParser, StyleProfiler, imported_question
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
from app.medical.terminology import TerminologyIndex, load_anatomy_data
from app.medical.text import excerpt
from app.medical.tutor import MEDICAL_TOOLS, MedicalTutor

EventCallback = Callable[[dict[str, Any]], None]
ANALYSIS_PAGE_CHARS = 1800
ANALYSIS_MAX_CHARS = 60_000
COMPARE_MAX_CHARS = 14_000
NOTES_MAX_CHARS = 12_000
PAGE_IMAGE_SCALE = 1.5
BANK_LIST_LIMIT = 200


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

    async def process_document(self, document_id: str, *, vision: bool = True, analysis: bool = True) -> dict[str, Any]:
        """Extract, index, look at the figures, summarise; report every stage."""
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
        self._emit({"kind": "document_ready", "document_id": document_id, "title": document.title, "page_count": document.page_count})
        return self.pipeline.payload(document)

    async def _vision_pass(self, document: StudyDocument) -> int:
        pending = self.pipeline.pages_needing_vision(document.document_id)
        analysed = 0
        for index, page in enumerate(pending, start=1):
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
            except (MedicalModelError, DocumentError) as exc:
                self.pipeline.attach_visual_summary(document.document_id, page.page_number, summary="", labels=[], status="failed")
                self._record("document.vision_failed", str(exc), level="warning", document_id=document.document_id, page=page.page_number)
                continue
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
        if not config.subjects and not config.topic_ids:
            raise GenerationError("Ders ya da konu seçilmedi.")
        notes: list[str] = []
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
            questions, notes = await self.generator.generate(config)
        exam = self.exam_builder.build(config, questions, notes=notes)
        session = self.sessions.get()
        session.active_exam_id = exam.exam_id
        self.sessions.save(session)
        self._record("exam.generated", "Exam generated.", exam_id=exam.exam_id, questions=len(questions))
        self._emit({"kind": "exam_ready", "exam_id": exam.exam_id, "title": exam.title, "count": len(exam.question_ids)})
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

    def answer(self, exam_id: str, question_id: str, answer_key: str | None, *, elapsed_seconds: float | None = None, flagged: bool | None = None, current_index: int | None = None) -> dict[str, Any] | None:
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
        if current_index is not None:
            attempt.current_index = max(0, int(current_index))
        self.store.save_attempt(attempt)
        result: dict[str, Any] = {"exam_id": exam_id, "question_id": question_id, "answer": entry.answer_key, "flagged": entry.flagged, "answered": len([item for item in attempt.answers.values() if item.answer_key])}
        if exam.config.immediate_feedback and entry.answer_key:
            # Only the first answer to a question is an attempt at recall: in
            # immediate feedback the key is revealed with it, so a later send
            # for the same question — flagging it re-sends the answer, and the
            # options stay clickable — is not a second try. Recording it would
            # state attempts, a streak and a confusion the student never made.
            if entry.correct is not None and not answered_before:
                self.learning.record(question, bool(entry.correct), chosen_key=entry.answer_key)
            result["feedback"] = explanation_payload(question, entry.answer_key)
        return result

    def finish_exam(self, exam_id: str) -> dict[str, Any] | None:
        exam = self.store.get_exam(exam_id)
        if exam is None:
            return None
        attempt = self.store.latest_attempt(exam.exam_id)
        if attempt is None:
            attempt = new_attempt(exam)
        questions = self.store.get_questions(exam.question_ids)
        if attempt.finished_at is None:
            attempt.finished_at = utc_now()
            if not exam.config.immediate_feedback:
                for question in questions:
                    entry = attempt.answers.get(question.question_id)
                    if entry is not None and entry.answer_key and entry.correct is not None:
                        self.learning.record(question, bool(entry.correct), chosen_key=entry.answer_key)
        analysis = analyse_attempt(exam, questions, attempt, curriculum=self.curriculum, mastery_levels=self.learning.levels())
        attempt.score = analysis["score"]
        attempt.analysis = analysis
        self.store.save_attempt(attempt)
        exam.status = "completed"
        exam.finished_at = attempt.finished_at
        self.store.save_exam(exam)
        session = self.sessions.get()
        if session.adaptive_difficulty and analysis["total"] >= 5:
            recent = [bool(attempt.answers[question_id].correct) for question_id in exam.question_ids if question_id in attempt.answers and attempt.answers[question_id].correct is not None]
            suggested, reason = self.learning.suggest_difficulty(session.difficulty, recent)
            analysis["adaptive"] = {"previous": session.difficulty, "suggested": suggested, "reason": reason}
            if suggested != session.difficulty:
                session.difficulty = suggested
                self.sessions.save(session)
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
        return {"hierarchy": self.anatomy.hierarchy(), "assets": {"directory": str(self.anatomy.assets.directory) if self.anatomy.assets.directory else None, "available": self.anatomy.assets.available_ids(), "problems": self.anatomy.assets.problems}, "source": self._source_note}

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

    def record_anatomy_answer(self, structure_id: str, landmark_id: str | None, correct: bool) -> dict[str, Any]:
        concept_id = f"anatomy.{structure_id}" + (f".{landmark_id}" if landmark_id else "")
        structure = self.anatomy.get(structure_id)
        question = Question(question_id="anatomy-quiz", subject="anatomy", stem="anatomy quiz", options=[], correct_key=None, topic_id=structure.topic_id if structure else None, concept_ids=[concept_id])
        updated = self.learning.record(question, correct)
        return {"mastery": [self.learning.mastery_payload(item) for item in updated]}


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
    pipeline = DocumentPipeline(
        store,
        directory=directory,
        max_pages=int(getattr(settings, "medical_max_document_pages", 400) or 400),
        max_bytes=int(getattr(settings, "medical_max_document_bytes", 60 * 1024 * 1024) or 60 * 1024 * 1024),
        vision_pages_per_document=int(getattr(settings, "medical_vision_pages_per_document", 12) or 0),
    )
    model = MedicalModelClient(provider_gateway, model=getattr(settings, "medical_model", "") or None, diagnostics=diagnostics)
    academy = MedicalAcademy(store=store, curriculum=curriculum, terminology=terminology, concepts=concepts, anatomy=anatomy, pipeline=pipeline, model=model, diagnostics=diagnostics, source_note=source_note)
    if tool_executor is not None:
        academy.register_tools(tool_executor)
    return academy


__all__ = ["MedicalAcademy", "create_medical_academy", "MEDICAL_TOOLS", "GenerationError", "MedicalModelError", "DocumentError", "timedelta"]
