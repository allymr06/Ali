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

import base64
from collections.abc import Callable, Mapping
from datetime import timedelta
from datetime import timedelta
from typing import Any

from app.medical.flashcards import FlashcardDeck
from app.medical.histology import HistologyBank
from app.medical.models import SUBJECT_LABELS_TR
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
        self.reviewer = SourceSupportReviewer(store, model, gate=source_review)
        self.source_review = bool(source_review)
        if self.source_review:
            academy.generator._reviewer = self.reviewer
        # One scoring decision for the whole academy: the bank picker, the
        # paper, the answer and the analysis all ask the reviewer.
        academy.generator.scoring = self.reviewer.decision
        self.planner = StudyPlanner(store, curriculum, concepts, learning, self.understanding, self.prerequisites, remind=remind, emit=academy._emit)
        self.histology = HistologyBank(store, academy.pipeline, learning, self.understanding, concepts, model)
        # Flashcards: repetition from the student's own material, never measurement.
        self.flashcards = FlashcardDeck(store, academy.anatomy, academy.terminology, curriculum, self.histology, academy.pipeline, planner=self.planner)
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
            "understanding_check": lambda payload: {"check": self.understanding.check_payload(self.understanding.check(_text(payload, "check_id")))},
            "understanding_explain": lambda payload: {"event": self.explain(_text(payload, "event_id"), _text(payload, "reasoning"))},
            "repair_get": lambda payload: {"session": self.understanding.repair(_text(payload, "session_id"))},
            "repair_step": lambda payload: {"session": self.understanding.complete_step(_text(payload, "session_id"), _text(payload, "step"))},
            "repair_answer": lambda payload: {"session": self.understanding.answer_transfer(_text(payload, "session_id"), _optional(payload, "answer_key"), confidence=_optional(payload, "confidence"), reasoning=_text(payload, "reasoning"), submission_id=_optional(payload, "submission_id")), "finding": self._finding_of_session(_text(payload, "session_id"))},
            # prerequisites and diagnosis
            "prerequisites": lambda payload: self.prerequisites.payload(_text(payload, "concept_id")),
            "concept_search": lambda payload: {"concepts": self._concept_search(_text(payload, "text"))},
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
            "plan_activity_start": lambda payload: {"activity": self.planner.start(_text(payload, "activity_id")), "today": self.planner.today_view(_optional(payload, "plan_id")), "sources": self.planner.activity_sources(_text(payload, "activity_id"))},
            "plan_activity_sources": lambda payload: {"sources": self.planner.activity_sources(_text(payload, "activity_id"))},
            "note_context": lambda payload: {"context": self._academy.note_context(subject=_optional(payload, "subject"), topic_id=_optional(payload, "topic_id"), document_ids=_items(payload, "document_ids"))},
            "jobs": lambda payload: {"jobs": self._academy.jobs.recent(limit=_number(payload, "limit", 30))},
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
            "histology_crop": lambda payload: self._crop(_text(payload, "specimen_id"), bool(payload.get("masked"))),
            "histology_hide_answer": lambda payload: {"specimen": self._specimen(self.histology.hide_printed_answer(_text(payload, "specimen_id"))["specimen_id"])},
            "histology_mask_add": lambda payload: {"mask": self.histology.add_mask(_text(payload, "specimen_id"), kind=_text(payload, "kind", "rect"), points=list(payload.get("points") or []), label=_text(payload, "label")), "specimen": self._specimen(_text(payload, "specimen_id"))},
            "histology_mask_remove": lambda payload: {"specimen": self.histology.payload(self.histology.remove_mask(_text(payload, "specimen_id"), _text(payload, "mask_id")), reveal=True)},
            "histology_source": lambda payload: {"source": self.histology.source(_text(payload, "specimen_id"))},
            "histology_compare": lambda payload: self.histology.compare(_text(payload, "specimen_id")),
            "histology_session_start": lambda payload: {"session": self._session(self.histology.start_session(mode=_text(payload, "mode", "study"), seconds=_number(payload, "seconds", 60), count=_number(payload, "count", 10), document_id=_optional(payload, "document_id"), specimen_ids=(_items(payload, "specimen_ids") or None)))},
            "histology_session": lambda payload: {"session": self._session(self.histology.session(_text(payload, "session_id")))},
            "histology_show": lambda payload: {"session": self.histology.show(_text(payload, "session_id"), _number(payload, "index"))},
            "histology_finish": lambda payload: {"session": self.histology.finish_session(_text(payload, "session_id"))},
            # flashcards (deterministic; a grade is study, not evidence)
            "weekly_report": lambda payload: self.weekly_report(days=_number(payload, "days", 7)),
            "weekly_report": lambda payload: self.weekly_report(days=_number(payload, "days", 7)),
            "committee_options": lambda payload: self._academy.committee_options(),
            "committee_exam": lambda payload: {"exam": self._academy.committee_exam(_mapping(payload, "distribution"), seconds_per_question=(_number(payload, "seconds_per_question") or None), unseen_only=bool(payload.get("unseen_only")), seed=_optional(payload, "seed"))},
            "cards_overview": lambda payload: self.flashcards.overview(),
            "cards_queue": lambda payload: self.flashcards.queue(limit=_number(payload, "limit", 60)),
            "cards_answer": lambda payload: self.flashcards.answer(_text(payload, "card_id"), _text(payload, "grade"), submission_id=_optional(payload, "submission_id")),
            "cards_build_topic": lambda payload: self.flashcards.build_topic(_text(payload, "topic_id")),
            "cards_add_wrongs": lambda payload: self.flashcards.add_wrong_questions(scoring=self.reviewer.decision),
            "cards_add_histology": lambda payload: self.flashcards.add_histology(),
            "cards_occlusion_scan": lambda payload: self.flashcards.occlusion_candidates(_text(payload, "document_id"), _number(payload, "page_number", 1), _mapping(payload, "region") or None),
            "cards_occlusion_add": lambda payload: self.flashcards.add_occlusion(_text(payload, "document_id"), _number(payload, "page_number", 1), _mapping(payload, "region") or None, [str(item) for item in (payload.get("labels") or [])]),
            "cards_suspend": lambda payload: self.flashcards.suspend(_text(payload, "card_id"), payload.get("suspended") is not False),
            "cards_delete": lambda payload: {"deleted": self.flashcards.delete(_text(payload, "card_id")), **self.flashcards.overview()},
            "cards_settings": lambda payload: {"settings": self.flashcards.update_settings(_mapping(payload, "fields"))},
            "cards_image": lambda payload: {"card_id": _text(payload, "card_id"), "image": "data:image/png;base64," + base64.b64encode(self.flashcards.front_image(_text(payload, "card_id"))).decode("ascii")},
        }
        self.ASYNC_ACTIONS: dict[str, Callable[[Mapping[str, Any]], Any]] = {
            "understanding_assess": lambda payload: self._async("understanding_assess", lambda: self.understanding.assess(_text(payload, "event_id")), key="event"),
            "diagnostic_ask": lambda payload: self._async("diagnostic_ask", lambda: self.understanding.diagnostic(_text(payload, "finding_id")), key="finding"),
            "diagnostic_answer": lambda payload: self._async("diagnostic_answer", lambda: self.understanding.answer_diagnostic(_text(payload, "finding_id"), _text(payload, "answer")), key="finding"),
            "repair_start": lambda payload: self._async("repair_start", lambda: self.understanding.start_repair(_text(payload, "finding_id")), key="session"),
            "question_review": lambda payload: self._async("question_review", lambda: self._review(_text(payload, "question_id")), key="support", question_id=_text(payload, "question_id")),
            "histology_answer": lambda payload: self._async("histology_answer", lambda: self.histology.answer(_text(payload, "session_id"), _text(payload, "specimen_id"), _text(payload, "text"), confidence=_optional(payload, "confidence"), explanation=_text(payload, "explanation"), submission_id=_optional(payload, "submission_id"), timed_out=bool(payload.get("timed_out"))), key="session"),
        }
        self.CONFIRMED_ACTIONS: frozenset[str] = frozenset({"invalidate_question", "plan_delete", "histology_delete", "cards_delete"})
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

    def _concept_search(self, text: str, *, limit: int = 8) -> list[dict[str, Any]]:
        """Concepts a student can name a prerequisite by: alias hits first, then a name substring."""
        query = " ".join(str(text or "").split())
        if len(query) < 2:
            return []
        graph = self._academy.concepts
        found = list(graph.find(query, limit=limit))
        folded = query.replace("İ", "i").replace("I", "ı").casefold()
        for concept in graph.all():
            if len(found) >= limit:
                break
            if concept in found:
                continue
            names = [concept.name, *concept.aliases]
            if any(folded in str(name).replace("İ", "i").replace("I", "ı").casefold() for name in names):
                found.append(concept)
        return [{"concept_id": item.concept_id, "name": item.name, "subject": item.subject, "subject_label": SUBJECT_LABELS_TR.get(item.subject, item.subject)} for item in found[:limit]]

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
        # While a timed session still asks about it, no screen shows its name.
        return self.histology.payload(specimen, reveal=specimen_id not in self.histology.under_test())

    def _crop(self, specimen_id: str, masked: bool) -> dict[str, Any]:
        under_test = specimen_id in self.histology.under_test()
        return {"specimen_id": specimen_id, "masked": masked or under_test, "image": self.histology.crop_data_url(specimen_id, masked=masked or under_test)}

    def _session(self, session: dict[str, Any] | None) -> dict[str, Any] | None:
        return self.histology.session_payload(session) if session is not None else None

    # ------------------------------------------------------------------
    # what the dashboard shows
    # ------------------------------------------------------------------

    def weekly_report(self, *, days: int = 7) -> dict[str, Any]:
        """The last week as the records tell it; nothing is estimated.

        Study minutes come from the planner's log and completed activities,
        answers from finished attempts (scored ones only enter accuracy),
        cards from the review log, findings from their own records. A day
        with nothing recorded is a zero, not a gap in the chart, and the
        streak is consecutive recorded days ending today.
        """
        window = max(1, min(31, int(days)))
        now = self.planner.now()
        day_keys = [(now - timedelta(days=offset)).date().isoformat() for offset in range(window - 1, -1, -1)]
        per_day: dict[str, dict[str, Any]] = {key: {"date": key, "minutes": 0, "answers": 0, "cards": 0, "activities_done": 0} for key in day_keys}

        def bucket(stamp: Any) -> dict[str, Any] | None:
            key = str(stamp or "")[:10]
            return per_day.get(key)

        for log in self._academy.store.list_records("study_log", limit=1000):
            row = bucket(log.get("at"))
            if row is not None and log.get("minutes"):
                row["minutes"] += int(log["minutes"])
        planned = completed = skipped = missed = 0
        for activity in self._academy.store.list_records("plan_activity", limit=2000):
            if str(activity.get("date") or "") not in per_day:
                continue
            status = str(activity.get("status") or "planned")
            planned += 1
            if status == "completed":
                completed += 1
                row = per_day[str(activity["date"])]
                row["activities_done"] += 1
                # A completed read/recap already logged its minutes to the
                # study log; adding them here would count them twice.
                if activity.get("actual_minutes") and activity.get("kind") not in ("read", "recap"):
                    row["minutes"] += int(activity["actual_minutes"])
            elif status == "skipped":
                skipped += 1
            elif status == "missed":
                missed += 1

        answered_total = correct_total = scored_total = unscored_answered = 0
        papers = 0
        for attempt in self._academy.store.list_attempts(limit=500):
            for entry in attempt.answers.values():
                row = bucket(entry.answered_at.isoformat() if entry.answered_at else None)
                if row is not None and entry.answer_key:
                    row["answers"] += 1
            if attempt.finished_at is None or attempt.finished_at.date().isoformat() not in per_day:
                continue
            analysis = attempt.analysis or {}
            if analysis.get("total") is None:
                continue
            papers += 1
            scored_total += int(analysis.get("total") or 0)
            correct_total += int(analysis.get("correct") or 0)
            answered_total += int(analysis.get("correct") or 0) + int(analysis.get("incorrect") or 0)
            unscored_answered += int(analysis.get("unscored_answered") or 0)

        reviews = [item for item in self._academy.store.list_records("flashcard_review", limit=2000) if str(item.get("at", ""))[:10] in per_day]
        for review in reviews:
            per_day[str(review["at"])[:10]]["cards"] += 1
        card_grades: dict[str, int] = {}
        for review in reviews:
            card_grades[str(review.get("grade"))] = card_grades.get(str(review.get("grade")), 0) + 1

        findings_opened = sum(1 for item in self.understanding.findings(limit=500) if str(item.get("created_at", ""))[:10] in per_day)
        findings_resolved = sum(
            1
            for item in self.understanding.findings(limit=500)
            if item.get("status") in ("resolved", "dismissed", "withdrawn") and str(item.get("updated_at", ""))[:10] in per_day
        )
        histology_sessions = [item for item in self._academy.store.list_records("histology_session", limit=200) if item.get("status") == "closed" and str(item.get("finished_at", ""))[:10] in per_day]
        histology_identified = sum(int((item.get("results") or {}).get("identified") or 0) for item in histology_sessions)
        histology_scored = sum(int((item.get("results") or {}).get("scored") or 0) for item in histology_sessions)

        streak = 0
        for key in reversed(day_keys):
            row = per_day[key]
            if row["minutes"] or row["answers"] or row["cards"] or row["activities_done"]:
                streak += 1
            else:
                break

        countdowns = []
        for plan in self.planner.plans():
            summary = self.planner.summary(plan["plan_id"])
            countdowns.append({"plan_id": summary["plan_id"], "name": summary["name"], "exam_date": summary["exam_date"], "days_left": summary["days_left"]})
        countdowns.sort(key=lambda item: item["days_left"])

        return {
            "days": [per_day[key] for key in day_keys],
            "totals": {
                "minutes": sum(row["minutes"] for row in per_day.values()),
                "answers": sum(row["answers"] for row in per_day.values()),
                "cards": len(reviews),
                "papers": papers,
                "scored": scored_total,
                "correct": correct_total,
                "accuracy": round(correct_total / scored_total, 3) if scored_total else None,
                "unscored_answered": unscored_answered,
                "card_grades": card_grades,
                "findings_opened": findings_opened,
                "findings_resolved": findings_resolved,
                "histology_identified": histology_identified,
                "histology_scored": histology_scored,
                "activities": {"planned": planned, "completed": completed, "skipped": skipped, "missed": missed},
            },
            "streak_days": streak,
            "countdowns": countdowns,
            "empty": not any(row["minutes"] or row["answers"] or row["cards"] or row["activities_done"] for row in per_day.values()),
            "note": "Bu özet yalnız kayıtlardan hesaplanır: dakikalar plan günlüğünden, doğruluk puanlı sorulardan, kartlar tekrar defterinden. Tahmin yoktur.",
        }

    def dashboard_block(self) -> dict[str, Any]:
        findings = self.understanding.findings(limit=200)
        open_findings = [item for item in findings if item.get("status") in ("hypothesis", "supported", "reopened", "repair_demonstrated")]
        try:
            today = self.planner.today_view()
        except ValueError:
            today = {"plan": None, "activities": [], "next": None}
        cards = self.flashcards.queue(limit=1)
        return {
            "today": today,
            "cards_due": cards["due"],
            "cards_new": min(cards["new_available"], cards["new_budget"]),
            "findings_open": len(open_findings),
            "findings_active": sum(1 for item in open_findings if item.get("status") in ("supported", "reopened")),
            "plans": len(self.planner.plans()),
            "histology": self.histology.overview()["counts"],
            "open_flags": len(self.reviewer.open_flags()),
        }
