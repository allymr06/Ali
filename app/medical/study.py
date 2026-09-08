"""The connected study workflow, and the explicit contract the page calls it by.

``StudyWorkflow`` composes the services added around the academy —
understanding (confidence, findings, repair), prerequisites and their
diagnosis, source-support review and flags, the exam-date planner and the
histology bank — and names every operation the Nova page may call:

* ``SYNC_ACTIONS`` answer at once from the store;
* ``ASYNC_ACTIONS`` need the model (an assessment, a diagnostic question, a
  repair session, a review, a histology explanation) and run on the desktop's
  async runner; the page hears the outcome as a ``job_report`` push whose
  ``job`` is ``"study"`` and whose ``action`` names the call.
* ``CONFIRMED_ACTIONS`` are destructive and need ``confirmed: true``.

The academy facade stays what it was; this module is where the pieces meet.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from app.medical.histology import HistologyBank
from app.medical.planner import StudyPlanner
from app.medical.prerequisites import PrerequisiteDiagnosis, PrerequisiteGraph
from app.medical.review import SourceSupportReviewer
from app.medical.understanding import UnderstandingEngine


def _text(payload: Mapping[str, Any], key: str, default: str = "") -> str:
    return str(payload.get(key, default) or "").strip()


def _optional(payload: Mapping[str, Any], key: str) -> str | None:
    value = _text(payload, key)
    return value or None


def _number(payload: Mapping[str, Any], key: str, default: int = 0) -> int:
    try:
        return int(payload.get(key) or default)
    except (TypeError, ValueError):
        return default


def _items(payload: Mapping[str, Any], key: str) -> list[str]:
    value = payload.get(key)
    if isinstance(value, (list, tuple)):
        return [str(item) for item in value if str(item).strip()]
    if isinstance(value, str) and value.strip():
        return [item.strip() for item in value.split(",") if item.strip()]
    return []


def _mapping(payload: Mapping[str, Any], key: str) -> dict[str, Any]:
    value = payload.get(key)
    return dict(value) if isinstance(value, Mapping) else {}


class StudyWorkflow:
    def __init__(self, academy: Any, *, source_review: bool = True, remind: Callable[[str, str], str | None] | None = None) -> None:
        self._academy = academy
        store, learning, concepts, curriculum, model = academy.store, academy.learning, academy.concepts, academy.curriculum, academy.model
        self.understanding = UnderstandingEngine(store, learning, concepts, curriculum, model, academy.retriever, generator=academy.generator, emit=academy._emit)
        self.prerequisites = PrerequisiteGraph(concepts, store)
        self.diagnosis = PrerequisiteDiagnosis(self.prerequisites, store, learning, self.understanding, curriculum)
        self.reviewer = SourceSupportReviewer(store, model)
        self.source_review = bool(source_review)
        if self.source_review:
            academy.generator._reviewer = self.reviewer
        self.planner = StudyPlanner(store, curriculum, concepts, learning, self.understanding, self.prerequisites, remind=remind, emit=academy._emit)
        self.histology = HistologyBank(store, academy.pipeline, learning, self.understanding, concepts, model)
        self.SYNC_ACTIONS: dict[str, Callable[[Mapping[str, Any]], Any]] = {
            # understanding
            "understanding_overview": lambda payload: self.understanding.overview(),
            "understanding_finding": lambda payload: self._finding(_text(payload, "finding_id")),
            "understanding_events": lambda payload: {"events": self.understanding.events(concept_id=_optional(payload, "concept_id"), limit=_number(payload, "limit", 50))},
            "understanding_challenge": lambda payload: {"finding": self.understanding.finding_payload(self.understanding.challenge(_text(payload, "finding_id"), _text(payload, "note")))},
            "understanding_dismiss": lambda payload: {"finding": self.understanding.finding_payload(self.understanding.dismiss(_text(payload, "finding_id"), _text(payload, "note")))},
            "understanding_reopen": lambda payload: {"finding": self.understanding.finding_payload(self.understanding.reopen(_text(payload, "finding_id"), _text(payload, "note")))},
            "understanding_check_start": lambda payload: {"check": self.understanding.start_check(concept_id=_optional(payload, "concept_id"), topic_id=_optional(payload, "topic_id"), subject=_optional(payload, "subject"))},
            "understanding_check_answer": lambda payload: {"check": self.understanding.answer_check(_text(payload, "check_id"), _optional(payload, "answer_key"), confidence=_optional(payload, "confidence"), reasoning=_text(payload, "reasoning"), submission_id=_optional(payload, "submission_id"))},
            "understanding_explain": lambda payload: {"event": self.explain(_text(payload, "event_id"), _text(payload, "reasoning"))},
            "repair_get": lambda payload: {"session": self.understanding.repair(_text(payload, "session_id"))},
            "repair_step": lambda payload: {"session": self.understanding.complete_step(_text(payload, "session_id"), _text(payload, "step"))},
            "repair_answer": lambda payload: {"session": self.understanding.answer_transfer(_text(payload, "session_id"), _optional(payload, "answer_key"), confidence=_optional(payload, "confidence"), reasoning=_text(payload, "reasoning"), submission_id=_optional(payload, "submission_id")), "finding": self._finding_of_session(_text(payload, "session_id"))},
            # prerequisites and diagnosis
            "prerequisites": lambda payload: self.prerequisites.payload(_text(payload, "concept_id")),
            "prerequisite_suggest": lambda payload: {"edge": self.prerequisites.suggest(_text(payload, "concept_id"), _text(payload, "requires"), provenance=_text(payload, "provenance", "model_suggested"), note=_text(payload, "note"), source=_mapping(payload, "source")).to_dict()},
            "prerequisite_confirm": lambda payload: {"edge": self.prerequisites.confirm(_text(payload, "edge_id")).to_dict()},
            "prerequisite_reject": lambda payload: {"edge": self.prerequisites.reject(_text(payload, "edge_id")).to_dict()},
            "diagnosis_struggling": lambda payload: {"struggling": self.diagnosis.struggling(), "recent": self.diagnosis.recent()},
            "diagnosis_start": lambda payload: {"diagnosis": self.diagnosis.start(_text(payload, "concept_id"), objective=_mapping(payload, "objective") or None, reason=_text(payload, "reason"))},
            "diagnosis_get": lambda payload: {"diagnosis": self.diagnosis.get(_text(payload, "diagnosis_id"))},
            "diagnosis_answer": lambda payload: {"diagnosis": self.diagnosis.answer(_text(payload, "diagnosis_id"), _text(payload, "concept_id"), _optional(payload, "answer_key"), confidence=_optional(payload, "confidence"), submission_id=_optional(payload, "submission_id"))},
            "diagnosis_skip": lambda payload: {"diagnosis": self.diagnosis.skip(_text(payload, "diagnosis_id"), _optional(payload, "concept_id"))},
            "diagnosis_shorten": lambda payload: {"diagnosis": self.diagnosis.shorten(_text(payload, "diagnosis_id"))},
            "diagnosis_redirect": lambda payload: {"diagnosis": self.diagnosis.redirect(_text(payload, "diagnosis_id"), _text(payload, "concept_id"))},
            "diagnosis_step": lambda payload: {"diagnosis": self.diagnosis.complete_step(_text(payload, "diagnosis_id"), _text(payload, "concept_id"))},
            "diagnosis_finish": lambda payload: {"diagnosis": self.diagnosis.finish(_text(payload, "diagnosis_id"))},
            # review, flags, invalidation
            "question_support": lambda payload: {"support": self._support(_text(payload, "question_id"))},
            "flag_question": lambda payload: {"flag": self.reviewer.flag(_text(payload, "question_id"), _text(payload, "kind"), _text(payload, "note"), exam_id=_optional(payload, "exam_id"), attempt_id=_optional(payload, "attempt_id"))},
            "question_flags": lambda payload: {"flags": self.reviewer.flags_for(_text(payload, "question_id"))},
            "open_flags": lambda payload: {"flags": self.reviewer.open_flags()},
            "resolve_flag": lambda payload: {"flag": self.reviewer.resolve_flag(_text(payload, "flag_id"), _text(payload, "resolution", "kept"), _text(payload, "note"))},
            "invalidate_question": lambda payload: self.invalidate_question(_text(payload, "question_id"), _text(payload, "reason", "Öğrenci geçersiz saydı.")),
            # planner
            "plans": lambda payload: {"plans": [self.planner.summary(item["plan_id"]) for item in self.planner.plans(include_done=bool(payload.get("include_done")))]},
            "plan": lambda payload: {"plan": self.planner.summary(_text(payload, "plan_id"))},
            "plan_create": lambda payload: {"plan": self.planner.summary(self.planner.create(
                _text(payload, "name"), _text(payload, "exam_date"),
                subjects=_items(payload, "subjects"), topic_ids=_items(payload, "topic_ids"), document_ids=_items(payload, "document_ids"),
                page_ranges=_mapping(payload, "page_ranges"), daily_minutes=(payload.get("daily_minutes") if isinstance(payload.get("daily_minutes"), Mapping) else _number(payload, "daily_minutes", 45)),
                unavailable=_items(payload, "unavailable"), weights=_mapping(payload, "weights"), weighting=_text(payload, "weighting", "manual"),
                notify=_mapping(payload, "notify"), priorities=_mapping(payload, "priorities"),
            )["plan_id"])},
            "plan_update": lambda payload: {"plan": self.planner.update(_text(payload, "plan_id"), _mapping(payload, "fields"))},
            "plan_confirm_scope": lambda payload: {"plan": self.planner.confirm_scope(_text(payload, "plan_id"), subjects=(_items(payload, "subjects") if "subjects" in payload else None), topic_ids=(_items(payload, "topic_ids") if "topic_ids" in payload else None), document_ids=(_items(payload, "document_ids") if "document_ids" in payload else None), page_ranges=(_mapping(payload, "page_ranges") if "page_ranges" in payload else None))},
            "plan_delete": lambda payload: {"deleted": self.planner.delete(_text(payload, "plan_id")), "plans": [self.planner.summary(item["plan_id"]) for item in self.planner.plans()]},
            "plan_today": lambda payload: {"today": self.planner.today_view(_optional(payload, "plan_id"))},
            "plan_coverage": lambda payload: {"coverage": self.planner.coverage(_text(payload, "plan_id"))},
            "plan_replan": lambda payload: {"plan": self.planner.replan(_text(payload, "plan_id"), reason=_text(payload, "reason", "öğrenci istedi"))},
            "plan_activity_start": lambda payload: {"activity": self.planner.start(_text(payload, "activity_id")), "today": self.planner.today_view(_optional(payload, "plan_id"))},
            "plan_activity_complete": lambda payload: {"activity": self.planner.complete(_text(payload, "activity_id"), minutes=(_number(payload, "minutes") or None)), "today": self.planner.today_view(_optional(payload, "plan_id"))},
            "plan_activity_skip": lambda payload: {"activity": self.planner.skip(_text(payload, "activity_id"), _text(payload, "note")), "today": self.planner.today_view(_optional(payload, "plan_id"))},
            "plan_manual": lambda payload: {"activity": self.planner.add_manual(_text(payload, "plan_id"), day=_text(payload, "day"), title=_text(payload, "title"), minutes=_number(payload, "minutes", 15), topic_id=_optional(payload, "topic_id"), kind=_text(payload, "kind", "read"))},
            "plan_log_study": lambda payload: {"log": self.planner.log_study(topic_id=_optional(payload, "topic_id"), document_id=_optional(payload, "document_id"), activity=_text(payload, "activity", "read"), minutes=(_number(payload, "minutes") or None))},
            # histology
            "histology_overview": lambda payload: self.histology.overview(),
            "histology_add": lambda payload: {"specimen": self.histology.add_specimen(_text(payload, "document_id"), _number(payload, "page_number", 1), _mapping(payload, "region"), label=_text(payload, "label"), latin=_text(payload, "latin"), stain=_optional(payload, "stain"), magnification=_optional(payload, "magnification"), features=_items(payload, "features"), basis=_text(payload, "basis", "none"), notes=_text(payload, "notes"), confusable_with=_items(payload, "confusable_with"))},
            "histology_specimen": lambda payload: {"specimen": self._specimen(_text(payload, "specimen_id"))},
            "histology_update": lambda payload: {"specimen": self.histology.payload(self.histology.update_specimen(_text(payload, "specimen_id"), _mapping(payload, "fields")), reveal=True)},
            "histology_confirm": lambda payload: {"specimen": self.histology.payload(self.histology.confirm_label(_text(payload, "specimen_id"), _text(payload, "label"), latin=_text(payload, "latin"), features=(_items(payload, "features") if "features" in payload else None), stain=(_text(payload, "stain") if "stain" in payload else None), magnification=(_text(payload, "magnification") if "magnification" in payload else None)), reveal=True)},
            "histology_delete": lambda payload: {"deleted": self.histology.delete_specimen(_text(payload, "specimen_id")), **self.histology.overview()},
            "histology_crop": lambda payload: {"specimen_id": _text(payload, "specimen_id"), "image": self.histology.crop_data_url(_text(payload, "specimen_id"))},
            "histology_mask_add": lambda payload: {"mask": self.histology.add_mask(_text(payload, "specimen_id"), kind=_text(payload, "kind", "rect"), points=list(payload.get("points") or []), label=_text(payload, "label")), "specimen": self._specimen(_text(payload, "specimen_id"))},
            "histology_mask_remove": lambda payload: {"specimen": self.histology.payload(self.histology.remove_mask(_text(payload, "specimen_id"), _text(payload, "mask_id")), reveal=True)},
            "histology_source": lambda payload: {"source": self.histology.source(_text(payload, "specimen_id"))},
            "histology_compare": lambda payload: self.histology.compare(_text(payload, "specimen_id")),
            "histology_session_start": lambda payload: {"session": self._session(self.histology.start_session(mode=_text(payload, "mode", "study"), seconds=_number(payload, "seconds", 60), count=_number(payload, "count", 10), document_id=_optional(payload, "document_id"), specimen_ids=(_items(payload, "specimen_ids") or None)))},
            "histology_session": lambda payload: {"session": self._session(self.histology.session(_text(payload, "session_id")))},
            "histology_show": lambda payload: {"session": self.histology.show(_text(payload, "session_id"), _number(payload, "index"))},
            "histology_finish": lambda payload: {"session": self.histology.finish_session(_text(payload, "session_id"))},
        }
        self.ASYNC_ACTIONS: dict[str, Callable[[Mapping[str, Any]], Any]] = {
            "understanding_assess": lambda payload: self._async("understanding_assess", lambda: self.understanding.assess(_text(payload, "event_id")), key="event"),
            "diagnostic_ask": lambda payload: self._async("diagnostic_ask", lambda: self.understanding.diagnostic(_text(payload, "finding_id")), key="finding"),
            "diagnostic_answer": lambda payload: self._async("diagnostic_answer", lambda: self.understanding.answer_diagnostic(_text(payload, "finding_id"), _text(payload, "answer")), key="finding"),
            "repair_start": lambda payload: self._async("repair_start", lambda: self.understanding.start_repair(_text(payload, "finding_id")), key="session"),
            "question_review": lambda payload: self._async("question_review", lambda: self._review(_text(payload, "question_id")), key="support", question_id=_text(payload, "question_id")),
            "histology_answer": lambda payload: self._async("histology_answer", lambda: self.histology.answer(_text(payload, "session_id"), _text(payload, "specimen_id"), _text(payload, "text"), confidence=_optional(payload, "confidence"), explanation=_text(payload, "explanation"), submission_id=_optional(payload, "submission_id"), timed_out=bool(payload.get("timed_out"))), key="session"),
        }
        self.CONFIRMED_ACTIONS: frozenset[str] = frozenset({"invalidate_question", "plan_delete", "histology_delete"})
        self.ASYNC_MESSAGES_TR: dict[str, str] = {
            "understanding_assess": "Gerekçe değerlendiriliyor.",
            "diagnostic_ask": "Teşhis sorusu hazırlanıyor.",
            "diagnostic_answer": "Teşhis cevabı değerlendiriliyor.",
            "repair_start": "Onarım oturumu hazırlanıyor.",
            "question_review": "Kaynak desteği inceleniyor.",
            "histology_answer": "Cevap değerlendiriliyor.",
        }

    # ------------------------------------------------------------------
    # dispatch
    # ------------------------------------------------------------------

    def call(self, name: str, payload: Mapping[str, Any]) -> dict[str, Any]:
        handler = self.SYNC_ACTIONS.get(name)
        if handler is None:
            raise KeyError(name)
        result = handler(payload)
        return result if isinstance(result, dict) else {"result": result}

    def start(self, name: str, payload: Mapping[str, Any]) -> Any:
        """The coroutine for an async action; it resolves to ``{"study": {...}}``."""
        handler = self.ASYNC_ACTIONS.get(name)
        if handler is None:
            raise KeyError(name)
        return handler(payload)

    async def _async(self, action: str, factory: Callable[[], Any], *, key: str, **extra: Any) -> dict[str, Any]:
        result = await factory()
        if key == "finding" and isinstance(result, dict):
            result = self.understanding.finding_payload(result)
        if key == "session" and isinstance(result, dict) and "items" in result:
            result = self.histology.session_payload(result)
        return {"study": {"action": action, key: result, **extra}}

    # ------------------------------------------------------------------
    # small compositions
    # ------------------------------------------------------------------

    def _finding(self, finding_id: str) -> dict[str, Any]:
        finding = self.understanding.finding(finding_id)
        if finding is None:
            raise ValueError("Bulgu bulunamadı.")
        return {"finding": self.understanding.finding_payload(finding), "events": self.understanding.events(concept_id=finding["concept_id"], limit=30)}

    def _finding_of_session(self, session_id: str) -> dict[str, Any] | None:
        session = self.understanding.repair(session_id)
        if session is None:
            return None
        finding = self.understanding.finding(session["finding_id"])
        return self.understanding.finding_payload(finding) if finding is not None else None

    def explain(self, event_id: str, reasoning: str) -> dict[str, Any]:
        """Add the reasoning the student wrote after the fact; the assessment is a separate, async step."""
        event = self.understanding.event(event_id)
        if event is None:
            raise ValueError("Kayıt bulunamadı.")
        text = " ".join(str(reasoning or "").split())[:1200]
        if not text:
            raise ValueError("Gerekçe boş.")
        if event.get("reasoning"):
            return event
        event["reasoning"] = text
        event["assessment"] = {**(event.get("assessment") or {}), "status": "pending"}
        self._academy.store.save_record("understanding_event", event_id, event, subject_key=event["concept_ids"][0])
        return event

    def _support(self, question_id: str) -> dict[str, Any]:
        question = self._academy.store.get_question(question_id)
        if question is None:
            raise ValueError("Soru bulunamadı.")
        return self.reviewer.payload(question)

    async def _review(self, question_id: str) -> dict[str, Any]:
        question = self._academy.store.get_question(question_id)
        if question is None:
            raise ValueError("Soru bulunamadı.")
        await self.reviewer.review(question)
        return self.reviewer.payload(self._academy.store.get_question(question_id) or question)

    def invalidate_question(self, question_id: str, reason: str) -> dict[str, Any]:
        """Invalidate, then correct what rested on the question: mastery and understanding evidence; the attempts stay."""
        question = self.reviewer.invalidate(question_id, reason)
        corrected = self._academy.learning.exclude_question(question, reason=reason)
        withdrawn = self.understanding.invalidate_question(question_id, reason)
        return {"question_id": question_id, "support": self.reviewer.payload(question), "mastery_corrected": [self._academy.learning.mastery_payload(item) for item in corrected], "understanding": withdrawn}

    def _specimen(self, specimen_id: str) -> dict[str, Any]:
        specimen = self.histology.specimen(specimen_id)
        if specimen is None:
            raise ValueError("Örnek bulunamadı.")
        return self.histology.payload(specimen, reveal=True)

    def _session(self, session: dict[str, Any] | None) -> dict[str, Any] | None:
        return self.histology.session_payload(session) if session is not None else None

    # ------------------------------------------------------------------
    # what the dashboard shows
    # ------------------------------------------------------------------

    def dashboard_block(self) -> dict[str, Any]:
        findings = self.understanding.findings(limit=200)
        open_findings = [item for item in findings if item.get("status") in ("hypothesis", "supported", "reopened", "repair_demonstrated")]
        try:
            today = self.planner.today_view()
        except ValueError:
            today = {"plan": None, "activities": [], "next": None}
        return {
            "today": today,
            "findings_open": len(open_findings),
            "findings_active": sum(1 for item in open_findings if item.get("status") in ("supported", "reopened")),
            "plans": len(self.planner.plans()),
            "histology": self.histology.overview()["counts"],
            "open_flags": len(self.reviewer.open_flags()),
        }
