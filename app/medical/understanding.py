"""Confidence-aware understanding: what the student misunderstands, and its repair.

Mastery (``learning.py``) says how often an answer was right. This module keeps
the other half of the story — how sure the student was, what they said their
reasoning was, and what that reasoning showed — as inspectable records:

* an *understanding event* per answer that carried more than a letter: the
  confidence the student reported ("Eminim", "Kararsızım", "Tahmin ettim";
  missing means unknown, never a guess), the explanation they gave when one
  was asked, and the model's assessment of that explanation;
* a *finding* per suspected misconception: the concept, the answer or the
  excerpt that raised it, the suspected misunderstanding, its sources, its
  evidence, its status (hypothesis → supported → repair demonstrated →
  resolved; disputed, dismissed, reopened, withdrawn), and every transition;
* a *repair session*: the problem in plain Turkish, the passage to read, a
  short explanation, a transfer question in another context, and a delayed
  follow-up scheduled through the review queue.

The rules that move state are deterministic and written here; the model only
observes (assesses an explanation, drafts a diagnostic question, writes an
explanation). One wrong option never makes a finding by itself, a correct
answer with a contradictory explanation earns its mark but not the status of
understanding, and an immediate success after repair is "ilk onarım
gösterildi", not resolution — that waits for the delayed follow-up.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Iterable
from datetime import datetime, timedelta
from typing import Any

from app.core.time import utc_now
from app.medical.model import MedicalModelError
from app.medical.models import (
    SUBJECT_LABELS_TR,
    ExamConfig,
    Question,
    new_id,
)
from app.medical.prompts import (
    PIPELINE_SYSTEM,
    diagnostic_assessment_prompt,
    diagnostic_question_prompt,
    reasoning_assessment_prompt,
    repair_explanation_prompt,
    transfer_directive,
)
from app.medical.questions import grade, most_similar, question_payload
from app.medical.retrieval import RetrievalScope
from app.medical.schemas import (
    DIAGNOSTIC_ASSESSMENT_SCHEMA,
    DIAGNOSTIC_QUESTION_SCHEMA,
    REASONING_ASSESSMENT_SCHEMA,
)

EVENT_KIND = "understanding_event"
FINDING_KIND = "misconception"
REPAIR_KIND = "repair"
CHECK_KIND = "understanding_check"

CONFIDENCE_LABELS_TR: dict[str, str] = {"sure": "Eminim", "unsure": "Kararsızım", "guess": "Tahmin ettim"}
CONFIDENCE_LEVELS = frozenset(CONFIDENCE_LABELS_TR)

CLASSIFICATION_LABELS_TR: dict[str, str] = {
    "correct_supported": "Doğru cevap, gerekçe destekliyor",
    "correct_unsupported": "Doğru cevap, gerekçe yok ya da tahmin",
    "correct_contradictory": "Doğru cevap, gerekçe çelişkili",
    "wrong_low_confidence": "Yanlış cevap, düşük güven",
    "wrong_high_confidence": "Yanlış cevap, yüksek güven",
    "unknown": "Sınıflanmadı",
}

FINDING_STATUS_LABELS_TR: dict[str, str] = {
    "hypothesis": "Hipotez",
    "supported": "Desteklenen bulgu",
    "disputed": "İtiraz edildi",
    "repair_demonstrated": "İlk onarım gösterildi",
    "resolved": "Çözüldü",
    "reopened": "Yeniden açıldı",
    "dismissed": "Kapatıldı",
    "withdrawn": "Geri çekildi",
}
OPEN_STATUSES = frozenset({"hypothesis", "supported", "reopened", "repair_demonstrated"})
ACTIVE_STATUSES = frozenset({"supported", "reopened"})

ASSESSMENT_VERSION = "understanding-1"
EXPLANATION_SAMPLE_PER_EXAM = 2
EXPLANATION_SAMPLE_PERCENT = 25
EVIDENCE_TO_SUPPORT = 2
MAX_FINDINGS_PER_EVENT = 2
DEFAULT_CONFIRMATION: dict[str, int] = {"min_days": 2, "successes": 1, "follow_up_days": 3}
TRANSFER_LIMITATION = "Farklı bağlamda bir soru daha doğru cevaplandı; bu ilk onarımı gösterir, kalıcı anlamayı kanıtlamaz. Gecikmeli tekrar bekleniyor."


def classify(correct: bool | None, confidence: str | None, verdict: str | None) -> str:
    """The five categories the workflow tells apart, from what is known.

    ``verdict`` is the assessment of the student's explanation (``supported``,
    ``contradictory``, ``insufficient``, ``off_topic``) or None when no
    explanation was given or assessed. Unknown confidence is not treated as
    either sure or unsure: a wrong answer with no confidence reported counts
    as low-confidence evidence, never as a confident error.
    """
    if correct is None:
        return "unknown"
    if correct:
        if verdict == "contradictory":
            return "correct_contradictory"
        if verdict == "supported":
            return "correct_supported"
        return "correct_unsupported"
    if confidence == "sure":
        return "wrong_high_confidence"
    return "wrong_low_confidence"


def _excerpt(text: str, limit: int = 240) -> str:
    cleaned = " ".join(str(text or "").split())
    return cleaned if len(cleaned) <= limit else cleaned[: limit - 1].rstrip() + "…"


class UnderstandingEngine:
    def __init__(
        self,
        store: Any,
        learning: Any,
        concepts: Any,
        curriculum: Any,
        model: Any,
        retriever: Any,
        *,
        generator: Any | None = None,
        clock: Callable[[], datetime] | None = None,
        confirmation: dict[str, int] | None = None,
        emit: Callable[[dict[str, Any]], None] | None = None,
    ) -> None:
        self._store = store
        self._learning = learning
        self._concepts = concepts
        self._curriculum = curriculum
        self._model = model
        self._retriever = retriever
        self._generator = generator
        self._clock = clock or utc_now
        self.confirmation = {**DEFAULT_CONFIRMATION, **(confirmation or {})}
        self._emit = emit or (lambda event: None)

    # ------------------------------------------------------------------
    # naming and lookups
    # ------------------------------------------------------------------

    def concept_name(self, concept_id: str) -> str:
        if concept_id.startswith("question:"):
            question = self._store.get_question(concept_id[len("question:"):])
            return _excerpt(question.stem, 80) if question is not None else "Soru"
        return self._learning.concept_name(concept_id)

    def _concept_ids(self, question: Question) -> list[str]:
        """The concepts an answer is evidence about.

        A question that names its concepts is evidence about them. One that
        does not — an imported committee question, usually — is matched
        against the concept graph by its own wording, within its subject. When
        nothing matches, the finding is anchored to the question itself rather
        than to a topic or a whole subject, so unrelated questions never feed
        one finding and the repair looks up the question, not the subject.
        """
        if question.concept_ids:
            return list(question.concept_ids)
        if self._concepts is not None:
            # The stem, the key and the question's own explanation say what the
            # question is about. The wrong options must not: a distractor would
            # name the finding after a structure the question only ruled out.
            correct = question.option(question.correct_key or "")
            text = " ".join([question.stem, correct.text if correct else "", question.explanation or ""])
            for concept in self._concepts.find(text, limit=3):
                if concept.subject == question.subject:
                    return [concept.concept_id]
        return [f"question:{question.question_id}"]

    def _statement(self, concept_id: str, question: Question, event: dict[str, Any]) -> str:
        chosen = question.option(event.get("answer_key") or "")
        correct = question.option(event.get("correct_key") or "")
        chosen_text = _excerpt(chosen.text, 80) if chosen else (event.get("answer_key") or "—")
        correct_text = _excerpt(correct.text, 80) if correct else (event.get("correct_key") or "?")
        if concept_id.startswith("question:"):
            return f"Bu soruda “{chosen_text}” seçildi; doğrusu “{correct_text}”."
        return f"{self.concept_name(concept_id)} konusunda yanlış cevap: “{chosen_text}” seçildi, doğrusu “{correct_text}”."

    def _retrieval_query(self, finding: dict[str, Any]) -> str:
        """What to look up for a finding: the concept's name, or the question itself when the finding is anchored to one."""
        concept_id = str(finding.get("concept_id", ""))
        if concept_id.startswith("question:"):
            question = self._store.get_question(concept_id[len("question:"):])
            if question is not None:
                correct = question.option(question.correct_key or "")
                return _excerpt(" ".join([question.stem, correct.text if correct else "", question.explanation or ""]), 400)
        return finding.get("concept_name") or concept_id

    @staticmethod
    def _principle(originals: list[Question]) -> str:
        parts: list[str] = []
        for question in originals[:1]:
            correct = question.option(question.correct_key or "")
            if correct:
                parts.append(f"Doğru cevap: {correct.text}")
            if question.explanation:
                parts.append(_excerpt(question.explanation, 300))
        return " ".join(parts)

    def _subject_of(self, concept_id: str, fallback: str) -> str:
        concept = self._concepts.get(concept_id) if self._concepts is not None else None
        return concept.subject if concept is not None else fallback

    def events(self, *, concept_id: str | None = None, limit: int = 200) -> list[dict[str, Any]]:
        if concept_id is None:
            return self._store.list_records(EVENT_KIND, limit=limit)
        return [event for event in self._store.list_records(EVENT_KIND, limit=2000) if concept_id in event.get("concept_ids", [])][:limit]

    def event(self, event_id: str) -> dict[str, Any] | None:
        return self._store.get_record(EVENT_KIND, event_id)

    def findings(self, *, status: str | None = None, concept_id: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        items = self._store.list_records(FINDING_KIND, subject_key=concept_id, limit=2000)
        if status is not None:
            items = [item for item in items if item.get("status") == status]
        items.sort(key=lambda item: (-int(item.get("priority", 0)), item.get("status") not in ACTIVE_STATUSES, item.get("updated_at", "")))
        return items[:limit]

    def finding(self, finding_id: str) -> dict[str, Any] | None:
        return self._store.get_record(FINDING_KIND, finding_id)

    def open_findings_for(self, concept_ids: Iterable[str], *, topic_id: str | None = None) -> list[dict[str, Any]]:
        """Open findings on these concepts — and, given a topic, those anchored to a question of that topic."""
        wanted = set(concept_ids)
        found: list[dict[str, Any]] = []
        for item in self._store.list_records(FINDING_KIND, limit=2000):
            if item.get("status") not in OPEN_STATUSES:
                continue
            concept_id = str(item.get("concept_id", ""))
            if concept_id in wanted or (topic_id is not None and item.get("topic_id") == topic_id and concept_id.startswith("question:")):
                found.append(item)
        return found

    # ------------------------------------------------------------------
    # sampling: when an explanation is asked for
    # ------------------------------------------------------------------

    def wants_explanation(self, question: Question, *, exam_id: str | None, immediate: bool) -> tuple[bool, str]:
        """Ask for a short explanation on a bounded sample, or where a finding needs it.

        A timed or answers-at-end paper is never interrupted: the question is
        put to the student on the results screen instead. In a practice
        sitting a small deterministic sample is asked, plus any question on a
        concept with an open finding.
        """
        concept_ids = self._concept_ids(question)
        if self.open_findings_for(concept_ids):
            return True, "open_finding"
        if not immediate:
            return False, "deferred"
        if exam_id:
            asked = sum(1 for event in self._store.list_records(EVENT_KIND, limit=2000) if event.get("exam_id") == exam_id and event.get("reasoning"))
            if asked >= EXPLANATION_SAMPLE_PER_EXAM:
                return False, "sample_full"
        digest = hashlib.sha1(f"{exam_id or ''}:{question.question_id}".encode("utf-8")).hexdigest()
        if int(digest[:8], 16) % 100 < EXPLANATION_SAMPLE_PERCENT:
            return True, "sample"
        return False, "not_sampled"

    # ------------------------------------------------------------------
    # events
    # ------------------------------------------------------------------

    def record_event(
        self,
        question: Question,
        *,
        correct: bool | None,
        answer_key: str | None,
        confidence: str | None = None,
        reasoning: str = "",
        source: str = "exam",
        exam_id: str | None = None,
        attempt_id: str | None = None,
        submission_id: str | None = None,
        finding_id: str | None = None,
    ) -> dict[str, Any]:
        """Store one answer's context; a repeated submission id returns the stored event."""
        level = str(confidence or "").strip().lower() or None
        if level is not None and level not in CONFIDENCE_LEVELS:
            raise ValueError("Güven bildirimi 'sure', 'unsure' ya da 'guess' olmalı.")
        key = str(submission_id or "").strip()
        concept_ids = self._concept_ids(question)
        if key:
            for existing in self._store.list_records(EVENT_KIND, subject_key=concept_ids[0], limit=500):
                if existing.get("submission_id") == key:
                    return existing
        now = self._clock()
        text = _excerpt(reasoning, 1200)
        event = {
            "event_id": new_id("ev"),
            "at": now.isoformat(),
            "question_id": question.question_id,
            "stem": _excerpt(question.stem, 200),
            "concept_ids": concept_ids,
            "subject": question.subject,
            "answer_key": answer_key,
            "correct_key": question.correct_key,
            "correct": correct,
            "confidence": level,
            "confidence_label": CONFIDENCE_LABELS_TR.get(level or "", ""),
            "reasoning": text,
            "source": source,
            "exam_id": exam_id,
            "attempt_id": attempt_id,
            "submission_id": key or None,
            "finding_id": finding_id,
            "assessment": {"status": "pending" if text else "not_needed", "verdict": None, "assessor": None, "version": ASSESSMENT_VERSION},
            "classification": classify(correct, level, None),
            "invalidated": False,
        }
        self._store.save_record(EVENT_KIND, event["event_id"], event, subject_key=concept_ids[0])
        self._apply_event(event, question)
        return self._store.get_record(EVENT_KIND, event["event_id"]) or event

    async def assess(self, event_id: str) -> dict[str, Any]:
        """Have the model read the student's explanation; the rules do the rest."""
        event = self.event(event_id)
        if event is None:
            raise ValueError("Kayıt bulunamadı.")
        if not event.get("reasoning"):
            return event
        question = self._store.get_question(event["question_id"])
        assessment = dict(event.get("assessment") or {})
        if not self._model.available:
            assessment.update({"status": "unavailable", "note": "Model sağlayıcısı kapalı; gerekçe kaydedildi, değerlendirme sonra yapılabilir."})
            event["assessment"] = assessment
            self._store.save_record(EVENT_KIND, event_id, event, subject_key=event["concept_ids"][0])
            return event
        evidence_text = ""
        if question is not None and question.references:
            evidence_text = "; ".join(f"{ref.title or ref.document_id}, s. {ref.page_number}: {ref.quote}" for ref in question.references[:3] if ref.quote)
        prompt = reasoning_assessment_prompt(
            stem=question.stem if question is not None else event.get("stem", ""),
            options=[(option.key, option.text) for option in (question.options if question is not None else [])],
            correct_key=event.get("correct_key"),
            answer_key=event.get("answer_key"),
            correct=event.get("correct"),
            confidence=event.get("confidence_label") or "bildirilmedi",
            reasoning=event.get("reasoning", ""),
            explanation=question.explanation if question is not None else "",
            evidence_text=evidence_text,
        )
        try:
            data = await self._model.structured("reasoning_assessment", prompt, REASONING_ASSESSMENT_SCHEMA, system_prompt=PIPELINE_SYSTEM, max_attempts=1)
        except MedicalModelError as exc:
            assessment.update({"status": "unavailable", "note": f"Değerlendirme yapılamadı: {exc}"})
            event["assessment"] = assessment
            self._store.save_record(EVENT_KIND, event_id, event, subject_key=event["concept_ids"][0])
            return event
        assessment.update(
            {
                "status": "done",
                "verdict": str(data.get("verdict") or "insufficient"),
                "suspected_misconception": _excerpt(data.get("suspected_misconception", ""), 400),
                "quote": _excerpt(data.get("quote", ""), 300),
                "assessment_confidence": str(data.get("assessment_confidence") or "low"),
                "diagnostic_question": _excerpt(data.get("diagnostic_question", ""), 400),
                "note": _excerpt(data.get("note", ""), 400),
                "assessor": f"model:{getattr(self._model, 'model', '') or 'unknown'}",
                "version": ASSESSMENT_VERSION,
                "at": self._clock().isoformat(),
            }
        )
        event["assessment"] = assessment
        event["classification"] = classify(event.get("correct"), event.get("confidence"), assessment["verdict"])
        self._store.save_record(EVENT_KIND, event_id, event, subject_key=event["concept_ids"][0])
        if question is not None:
            self._apply_assessment(event, question)
        return self._store.get_record(EVENT_KIND, event_id) or event

    # ------------------------------------------------------------------
    # deterministic transitions
    # ------------------------------------------------------------------

    def _evidence_item(self, event: dict[str, Any], kind: str, *, outcome: str, excerpt: str = "", weight: int = 1) -> dict[str, Any]:
        return {
            "event_id": event.get("event_id"),
            "question_id": event.get("question_id"),
            "kind": kind,
            "excerpt": _excerpt(excerpt or event.get("reasoning") or f"Cevap {event.get('answer_key') or '—'} (doğru: {event.get('correct_key') or '?'})"),
            "outcome": outcome,
            "at": event.get("at"),
            "weight": weight,
            "valid": True,
        }

    def _mistake(self, question: Question, event: dict[str, Any]) -> dict[str, str]:
        """What was picked and what the key was — the repair's explanation needs both, in the question's own words."""
        chosen = question.option(event.get("answer_key") or "")
        correct = question.option(event.get("correct_key") or "")
        return {
            "stem": _excerpt(question.stem, 300),
            "chosen": _excerpt(chosen.text, 120) if chosen else str(event.get("answer_key") or ""),
            "correct": _excerpt(correct.text, 120) if correct else str(event.get("correct_key") or ""),
            "explanation": _excerpt(question.explanation, 300),
        }

    def _new_finding(self, concept_id: str, question: Question, event: dict[str, Any], statement: str, *, priority: int, status: str = "hypothesis") -> dict[str, Any]:
        now = self._clock().isoformat()
        finding = {
            "finding_id": new_id("mc"),
            "concept_id": concept_id,
            "concept_name": self.concept_name(concept_id),
            "subject": self._subject_of(concept_id, question.subject),
            "topic_id": question.topic_id,
            "statement": statement,
            "status": status,
            "priority": priority,
            "evidence": [],
            "sources": [{"document_id": ref.document_id, "page_number": ref.page_number, "title": ref.title, "quote": ref.quote} for ref in question.references[:3]],
            "question_ids": [question.question_id],
            "follow_ups": [],
            "history": [{"at": now, "status": status, "note": "İlk kanıt kaydedildi.", "by": "rule"}],
            "provenance": {"assessor": "rule", "version": ASSESSMENT_VERSION},
            "mistake": self._mistake(question, event) if event.get("correct") is False else None,
            "student_note": "",
            "pending_diagnostic": None,
            "repair": None,
            "limitations": [],
            "created_at": now,
        }
        return finding

    def _save_finding(self, finding: dict[str, Any]) -> dict[str, Any]:
        finding["priority"] = self.priority_for(finding)
        self._store.save_record(FINDING_KIND, finding["finding_id"], finding, subject_key=finding["concept_id"])
        return finding

    def _transition(self, finding: dict[str, Any], status: str, note: str, *, by: str = "rule") -> None:
        if finding.get("status") != status:
            finding["status"] = status
        finding.setdefault("history", []).append({"at": self._clock().isoformat(), "status": status, "note": note, "by": by})

    @staticmethod
    def priority_for(finding: dict[str, Any]) -> int:
        """Confident errors and contradictory reasoning first; a guess last."""
        weights = {"answer_confident": 3, "explanation": 3, "diagnostic": 3, "transfer": 2, "answer": 1, "follow_up": 2}
        score = 0
        for item in finding.get("evidence", []):
            if not item.get("valid", True):
                continue
            if item.get("outcome") in ("against", "confirms"):
                score += weights.get(item.get("kind", "answer"), 1)
        if finding.get("status") == "reopened":
            score += 2
        if finding.get("status") in ("resolved", "dismissed", "withdrawn"):
            return 0
        return min(9, score)

    def _distinct_support(self, finding: dict[str, Any]) -> float:
        """How much independent evidence stands against the student's understanding.

        A confident wrong answer, a contradictory explanation and a confirming
        diagnostic count one each; a plain wrong answer counts 0.7, so two
        wrong clicks alone never make a supported finding (1.4), three do
        (2.1), and one wrong click plus an explanation does (1.7 + …). Each
        question is counted once per kind of evidence.
        """
        seen: dict[str, float] = {}
        for item in finding.get("evidence", []):
            if not item.get("valid", True) or item.get("outcome") not in ("against", "confirms"):
                continue
            kind = str(item.get("kind") or "answer")
            key = f"{kind}:{item.get('question_id') or item.get('event_id')}"
            seen[key] = 0.7 if kind == "answer" else 1.0
        return sum(seen.values())

    def _apply_event(self, event: dict[str, Any], question: Question) -> None:
        """A wrong answer adds evidence; a confident one opens a hypothesis.

        A low-confidence wrong answer opens nothing on its own: it waits for a
        second wrong answer on the concept, an explanation, or a diagnostic.
        A correct answer after repair feeds the follow-up rule.
        """
        classification = event.get("classification")
        for concept_id in event.get("concept_ids", [])[:MAX_FINDINGS_PER_EVENT]:
            open_findings = [item for item in self.findings(concept_id=concept_id) if item.get("status") in OPEN_STATUSES]
            finding = open_findings[0] if open_findings else None
            if event.get("correct") is False:
                kind = "answer_confident" if classification == "wrong_high_confidence" else "answer"
                if finding is None:
                    wrong_events = [item for item in self.events(concept_id=concept_id) if item.get("correct") is False and not item.get("invalidated")]
                    if classification != "wrong_high_confidence" and len(wrong_events) < 2:
                        continue
                    statement = self._statement(concept_id, question, event)
                    finding = self._new_finding(concept_id, question, event, statement, priority=3 if kind == "answer_confident" else 2)
                    if len(wrong_events) >= 2 and classification != "wrong_high_confidence":
                        for earlier in wrong_events:
                            if earlier.get("event_id") != event.get("event_id"):
                                finding["evidence"].append(self._evidence_item(earlier, "answer", outcome="against"))
                                finding["question_ids"] = list(dict.fromkeys(finding["question_ids"] + [earlier.get("question_id")]))
                finding["evidence"].append(self._evidence_item(event, kind, outcome="against"))
                finding["question_ids"] = list(dict.fromkeys(finding.get("question_ids", []) + [question.question_id]))
                if finding.get("status") == "repair_demonstrated" and classification == "wrong_high_confidence":
                    self._transition(finding, "reopened", "Onarımdan sonra yüksek güvenle yanlış cevap: bulgu yeniden açıldı.")
                elif finding.get("status") == "hypothesis" and self._distinct_support(finding) >= EVIDENCE_TO_SUPPORT:
                    self._transition(finding, "supported", "Birbirinden bağımsız iki kanıt: bulgu desteklendi.")
                self._save_finding(finding)
            elif event.get("correct") is True and finding is not None:
                finding["evidence"].append(self._evidence_item(event, "follow_up" if event.get("source") in ("review", "exam", "check") else event.get("source", "answer"), outcome="for"))
                self._save_finding(finding)
                self.confirm_follow_ups(finding_id=finding["finding_id"])

    def _apply_assessment(self, event: dict[str, Any], question: Question) -> None:
        assessment = event.get("assessment") or {}
        verdict = assessment.get("verdict")
        if verdict not in ("contradictory", "supported"):
            return
        for concept_id in event.get("concept_ids", [])[:MAX_FINDINGS_PER_EVENT]:
            open_findings = [item for item in self.findings(concept_id=concept_id) if item.get("status") in OPEN_STATUSES]
            finding = open_findings[0] if open_findings else None
            if verdict == "contradictory":
                statement = assessment.get("suspected_misconception") or f"{self.concept_name(concept_id)} hakkındaki açıklama kaynakla çelişiyor."
                if finding is None:
                    finding = self._new_finding(concept_id, question, event, statement, priority=3)
                elif not finding.get("statement") or finding["statement"].startswith(self.concept_name(concept_id) + " konusunda yanlış cevap"):
                    finding["statement"] = statement
                finding["provenance"] = {"assessor": assessment.get("assessor"), "version": ASSESSMENT_VERSION}
                finding["evidence"].append(self._evidence_item(event, "explanation", outcome="against", excerpt=assessment.get("quote") or event.get("reasoning", ""), weight=2))
                finding["question_ids"] = list(dict.fromkeys(finding.get("question_ids", []) + [question.question_id]))
                if assessment.get("assessment_confidence") == "low" and assessment.get("diagnostic_question"):
                    finding["pending_diagnostic"] = {"question": assessment["diagnostic_question"], "expected_answer": "", "rubric": "", "asked_at": self._clock().isoformat(), "origin": "assessment"}
                    finding.setdefault("history", []).append({"at": self._clock().isoformat(), "status": finding["status"], "note": "Açıklama belirsiz: kısa teşhis sorusu soruldu.", "by": "rule"})
                elif finding.get("status") == "hypothesis" and self._distinct_support(finding) >= EVIDENCE_TO_SUPPORT:
                    self._transition(finding, "supported", "Cevap ve açıklama birlikte: bulgu desteklendi.")
                elif finding.get("status") == "repair_demonstrated":
                    self._transition(finding, "reopened", "Onarımdan sonra çelişkili açıklama: bulgu yeniden açıldı.")
                self._save_finding(finding)
            elif verdict == "supported" and finding is not None and event.get("correct") is True:
                finding["evidence"].append(self._evidence_item(event, "explanation", outcome="for", excerpt=assessment.get("quote") or event.get("reasoning", "")))
                self._save_finding(finding)
                self.confirm_follow_ups(finding_id=finding["finding_id"])

    # ------------------------------------------------------------------
    # the student's word on a finding
    # ------------------------------------------------------------------

    def challenge(self, finding_id: str, note: str = "") -> dict[str, Any]:
        finding = self.finding(finding_id)
        if finding is None:
            raise ValueError("Bulgu bulunamadı.")
        finding["student_note"] = _excerpt(note, 400)
        self._transition(finding, "disputed", "Öğrenci bulguya itiraz etti." + (f" Not: {_excerpt(note, 200)}" if note else ""), by="student")
        return self._save_finding(finding)

    def dismiss(self, finding_id: str, note: str = "") -> dict[str, Any]:
        finding = self.finding(finding_id)
        if finding is None:
            raise ValueError("Bulgu bulunamadı.")
        finding["student_note"] = _excerpt(note, 400)
        self._transition(finding, "dismissed", "Öğrenci bulguyu kapattı; geçmiş korunuyor." + (f" Not: {_excerpt(note, 200)}" if note else ""), by="student")
        return self._save_finding(finding)

    def reopen(self, finding_id: str, note: str = "") -> dict[str, Any]:
        finding = self.finding(finding_id)
        if finding is None:
            raise ValueError("Bulgu bulunamadı.")
        self._transition(finding, "reopened", "Bulgu yeniden açıldı." + (f" Not: {_excerpt(note, 200)}" if note else ""), by="student")
        return self._save_finding(finding)

    # ------------------------------------------------------------------
    # a short diagnostic question when the explanation was ambiguous
    # ------------------------------------------------------------------

    async def diagnostic(self, finding_id: str) -> dict[str, Any]:
        finding = self.finding(finding_id)
        if finding is None:
            raise ValueError("Bulgu bulunamadı.")
        if finding.get("pending_diagnostic") and finding["pending_diagnostic"].get("expected_answer"):
            return finding
        if not self._model.available:
            raise MedicalModelError("Teşhis sorusu için model sağlayıcısı gerekli.")
        evidence_lines = [item.get("excerpt", "") for item in finding.get("evidence", []) if item.get("valid", True)][:4]
        data = await self._model.structured(
            "diagnostic_question",
            diagnostic_question_prompt(finding.get("concept_name", ""), finding.get("statement", ""), evidence_lines),
            DIAGNOSTIC_QUESTION_SCHEMA,
            system_prompt=PIPELINE_SYSTEM,
            max_attempts=1,
        )
        finding["pending_diagnostic"] = {
            "question": _excerpt(data.get("question", ""), 400),
            "expected_answer": _excerpt(data.get("expected_answer", ""), 400),
            "rubric": _excerpt(data.get("rubric", ""), 400),
            "asked_at": self._clock().isoformat(),
            "origin": "model",
        }
        finding.setdefault("history", []).append({"at": self._clock().isoformat(), "status": finding["status"], "note": "Kısa teşhis sorusu hazırlandı.", "by": "model"})
        return self._save_finding(finding)

    async def answer_diagnostic(self, finding_id: str, answer: str) -> dict[str, Any]:
        finding = self.finding(finding_id)
        if finding is None or not finding.get("pending_diagnostic"):
            raise ValueError("Bekleyen teşhis sorusu yok.")
        pending = finding["pending_diagnostic"]
        text = _excerpt(answer, 800)
        verdict, note, assessor = "unclear", "", "rule"
        if self._model.available and text:
            try:
                data = await self._model.structured(
                    "diagnostic_assessment",
                    diagnostic_assessment_prompt(pending.get("question", ""), pending.get("expected_answer", ""), pending.get("rubric", ""), finding.get("statement", ""), text),
                    DIAGNOSTIC_ASSESSMENT_SCHEMA,
                    system_prompt=PIPELINE_SYSTEM,
                    max_attempts=1,
                )
                verdict = str(data.get("verdict") or "unclear")
                note = _excerpt(data.get("note", ""), 300)
                assessor = f"model:{getattr(self._model, 'model', '') or 'unknown'}"
            except MedicalModelError as exc:
                note = f"Değerlendirme yapılamadı: {exc}"
        elif not text:
            note = "Boş cevap."
        outcome = {"confirms": "confirms", "refutes": "for", "unclear": "unclear"}[verdict if verdict in ("confirms", "refutes", "unclear") else "unclear"]
        finding["evidence"].append({"event_id": None, "question_id": None, "kind": "diagnostic", "excerpt": text, "outcome": outcome, "at": self._clock().isoformat(), "weight": 2, "valid": True, "note": note, "assessor": assessor})
        finding["pending_diagnostic"] = None
        if verdict == "confirms" and finding.get("status") in ("hypothesis", "disputed"):
            self._transition(finding, "supported", "Teşhis sorusu yanlış anlamayı doğruladı.")
        elif verdict == "refutes":
            refutations = sum(1 for item in finding.get("evidence", []) if item.get("kind") == "diagnostic" and item.get("outcome") == "for")
            if finding.get("status") == "hypothesis" and refutations >= 2:
                self._transition(finding, "withdrawn", "İki teşhis cevabı hipotezi çürüttü; bulgu geri çekildi.")
            else:
                finding.setdefault("history", []).append({"at": self._clock().isoformat(), "status": finding["status"], "note": "Teşhis cevabı bulguyu desteklemedi.", "by": assessor})
        else:
            finding.setdefault("history", []).append({"at": self._clock().isoformat(), "status": finding["status"], "note": "Teşhis cevabı belirsiz kaldı." + (f" {note}" if note else ""), "by": assessor})
        return self._save_finding(finding)

    # ------------------------------------------------------------------
    # repair
    # ------------------------------------------------------------------

    def repair(self, session_id: str) -> dict[str, Any] | None:
        return self._store.get_record(REPAIR_KIND, session_id)

    def repairs_for(self, finding_id: str) -> list[dict[str, Any]]:
        return self._store.list_records(REPAIR_KIND, subject_key=finding_id, limit=50)

    async def start_repair(self, finding_id: str) -> dict[str, Any]:
        """Open a repair session for a supported finding (a hypothesis may be repaired too, and says so)."""
        finding = self.finding(finding_id)
        if finding is None:
            raise ValueError("Bulgu bulunamadı.")
        if finding.get("status") in ("resolved", "dismissed", "withdrawn"):
            raise ValueError("Bu bulgu kapalı; onarım için önce yeniden aç.")
        for existing in self.repairs_for(finding_id):
            if existing.get("status") == "open":
                return existing
        concept_id = finding["concept_id"]
        name = finding.get("concept_name") or self.concept_name(concept_id)
        now = self._clock()
        evidence_lines = [item.get("excerpt", "") for item in finding.get("evidence", []) if item.get("valid", True) and item.get("outcome") in ("against", "confirms")][:4]
        statement = str(finding.get("statement", "")).strip()
        problem = statement if statement.startswith(name) else f"{name}: {statement}".strip()
        if finding.get("status") == "hypothesis":
            problem += " (Henüz bir hipotez: tek bir kanıt var; onarım yine de yapılabilir.)"

        passage = self._passage_for(finding)
        explanation = await self._explanation_for(name, finding, passage)
        transfer = await self._transfer_for(finding)
        follow_up_at = now + timedelta(days=int(self.confirmation.get("follow_up_days", 3)))
        session = {
            "session_id": new_id("rep"),
            "finding_id": finding_id,
            "concept_id": concept_id,
            "concept_name": name,
            "status": "open",
            "started_at": now.isoformat(),
            "steps": [
                {"step": "problem", "title": "Sorun", "text": problem, "done": False},
                {"step": "passage", "title": "Ders pasajı", "sources": passage["sources"], "text": passage["text"], "done": False},
                {"step": "explanation", "title": "Kısa anlatım", "text": explanation["text"], "assessor": explanation["assessor"], "done": False},
                {"step": "transfer", "title": "Farklı bağlamda soru", "question": transfer["question"], "similarity": transfer["similarity"], "limitations": transfer["limitations"], "note": transfer["note"], "answered": None, "done": False},
                {"step": "follow_up", "title": "Gecikmeli tekrar", "due_at": follow_up_at.isoformat(), "text": f"{int(self.confirmation.get('follow_up_days', 3))} gün sonra bu kavram tekrar kuyruğuna düşer; o soruyu doğru cevaplamak bulguyu çözer.", "done": False},
            ],
            "outcome": None,
        }
        self._store.save_record(REPAIR_KIND, session["session_id"], session, subject_key=finding_id)
        finding["repair"] = {"session_id": session["session_id"], "started_at": now.isoformat(), "initial_repair_at": None, "confirmed_at": None}
        finding["follow_ups"].append({"due_at": follow_up_at.isoformat(), "done_at": None, "outcome": None, "event_id": None, "session_id": session["session_id"]})
        finding["limitations"] = list(dict.fromkeys(finding.get("limitations", []) + transfer["limitations"]))
        finding.setdefault("history", []).append({"at": now.isoformat(), "status": finding["status"], "note": "Onarım oturumu açıldı.", "by": "student"})
        self._save_finding(finding)
        self._learning.schedule_review(concept_id, follow_up_at, reason="Onarım sonrası gecikmeli tekrar.")
        self._emit({"kind": "repair_started", "finding_id": finding_id, "session_id": session["session_id"], "concept": name, "quiet": True})
        return session

    def _passage_for(self, finding: dict[str, Any]) -> dict[str, Any]:
        sources = list(finding.get("sources") or [])
        text = ""
        if not sources and self._retriever is not None:
            try:
                blocks = self._retriever.retrieve(self._retrieval_query(finding), RetrievalScope(subject=finding.get("subject")), limit=2)
            except Exception:
                blocks = []
            for block in blocks:
                sources.append({"document_id": block.reference.document_id, "page_number": block.reference.page_number, "title": block.reference.title, "quote": _excerpt(block.text, 300)})
                text = text or _excerpt(block.text, 600)
        elif sources:
            text = sources[0].get("quote", "")
        if not sources:
            text = "Kütüphanede bu kavram için sayfa bulunamadı; ders notunu ekleyince pasaj burada görünür."
        return {"sources": sources[:3], "text": text}

    async def _explanation_for(self, name: str, finding: dict[str, Any], passage: dict[str, Any]) -> dict[str, Any]:
        evidence_lines = [item.get("excerpt", "") for item in finding.get("evidence", []) if item.get("valid", True) and item.get("outcome") in ("against", "confirms")][:4]
        if not self._model.available:
            return {"text": "Model sağlayıcısı kapalı: pasajı oku ve JARVIS'e bu kavramı sor; anlatım sonra üretilebilir.", "assessor": "none"}
        try:
            text = await self._model.text("repair_explanation", repair_explanation_prompt(name, finding.get("statement", ""), evidence_lines, passage.get("text", ""), mistake=finding.get("mistake")), system_prompt=PIPELINE_SYSTEM)
        except MedicalModelError as exc:
            return {"text": f"Anlatım üretilemedi ({exc}); pasajı oku ve JARVIS'e sor.", "assessor": "none"}
        return {"text": _excerpt(text, 1500), "assessor": f"model:{getattr(self._model, 'model', '') or 'unknown'}"}

    async def _transfer_for(self, finding: dict[str, Any]) -> dict[str, Any]:
        """A fresh question on the same principle in another context, with its limits stated."""
        original_ids = [item for item in finding.get("question_ids", []) if item]
        originals = [question for question in (self._store.get_question(identifier) for identifier in original_ids) if question is not None]
        limitations = [TRANSFER_LIMITATION]
        note = ""
        question: Question | None = None
        if self._generator is not None and self._model.available:
            subject = finding.get("subject") or (originals[0].subject if originals else None)
            config = ExamConfig(subjects=[subject] if subject else [], topic_ids=[finding["topic_id"]] if finding.get("topic_id") else [], question_count=1, option_count=5, difficulty=3, include_images=False, randomize=False)
            directive = transfer_directive(finding.get("concept_name", ""), finding.get("statement", ""), [item.stem for item in originals[:2]], principle=self._principle(originals))
            try:
                generated, notes = await self._generator.generate(config, professor_directive=directive)
                if generated:
                    question = generated[0]
                    question.tags = list(dict.fromkeys(question.tags + ["transfer", f"finding:{finding['finding_id']}"]))[:20]
                    question.metadata["transfer_for"] = finding["finding_id"]
                    self._store.save_question(question)
                note = " ".join(notes)[:300]
            except Exception as exc:  # GenerationError or a model failure
                note = f"Yeni soru üretilemedi: {exc}"
        if question is None:
            candidates = [item for item in self._store.query_questions(concept_id=finding["concept_id"], with_answer_key=True, limit=20) if item.question_id not in original_ids]
            if candidates:
                question = candidates[0]
                limitations.append("Yeni soru üretilemedi; bankadaki bir soru kullanıldı, bağlam farkı garanti değil.")
            else:
                note = note or "Bu kavram için farklı bağlamda soru yok; model açıkken yeniden dene ya da soru bankasına ekle."
        similarity = None
        if question is not None and originals:
            score, _ = most_similar(question, originals)
            similarity = round(float(score), 3)
            if similarity is not None and similarity >= 0.45:
                limitations.append("Yeni soru orijinale çok benziyor; ezberle de doğru cevaplanabilir.")
        payload = question_payload(question, reveal=False, include_explanation=False, curriculum=self._curriculum) if question is not None else None
        return {"question": payload, "question_id": question.question_id if question is not None else None, "similarity": similarity, "limitations": limitations, "note": note}

    def complete_step(self, session_id: str, step: str) -> dict[str, Any]:
        session = self.repair(session_id)
        if session is None:
            raise ValueError("Onarım oturumu bulunamadı.")
        for item in session.get("steps", []):
            if item.get("step") == step:
                item["done"] = True
        self._store.save_record(REPAIR_KIND, session_id, session, subject_key=session["finding_id"])
        return session

    def answer_transfer(self, session_id: str, answer_key: str | None, *, confidence: str | None = None, reasoning: str = "", submission_id: str | None = None) -> dict[str, Any]:
        """Grade the transfer question; a correct answer is the initial repair, not the resolution."""
        session = self.repair(session_id)
        if session is None:
            raise ValueError("Onarım oturumu bulunamadı.")
        transfer = next((item for item in session.get("steps", []) if item.get("step") == "transfer"), None)
        if transfer is None or not transfer.get("question"):
            raise ValueError("Bu oturumda cevaplanacak soru yok.")
        if transfer.get("answered") is not None:
            return session
        question = self._store.get_question(transfer["question"]["question_id"])
        if question is None:
            raise ValueError("Soru bulunamadı.")
        correct = grade(question, answer_key)
        event = self.record_event(question, correct=correct, answer_key=answer_key, confidence=confidence, reasoning=reasoning, source="repair", submission_id=submission_id, finding_id=session["finding_id"])
        self._learning.record(question, bool(correct), chosen_key=answer_key)
        transfer["answered"] = {"answer_key": answer_key, "correct": correct, "event_id": event["event_id"], "at": self._clock().isoformat()}
        transfer["done"] = True
        transfer["question"] = question_payload(question, reveal=True, include_explanation=True, curriculum=self._curriculum)
        finding = self.finding(session["finding_id"])
        if finding is not None:
            finding["evidence"].append(self._evidence_item(event, "transfer", outcome="for" if correct else "against", excerpt=f"Farklı bağlamda soru: {'doğru' if correct else 'yanlış'} ({answer_key or '—'})", weight=2))
            if correct:
                self._transition(finding, "repair_demonstrated", "Farklı bağlamdaki soru doğru: ilk onarım gösterildi; gecikmeli tekrar bekleniyor.")
                finding.setdefault("repair", {})["initial_repair_at"] = self._clock().isoformat()
                session["outcome"] = "initial_repair"
            else:
                finding.setdefault("history", []).append({"at": self._clock().isoformat(), "status": finding["status"], "note": "Farklı bağlamdaki soru yanlış: onarım henüz gösterilmedi.", "by": "rule"})
                session["outcome"] = "not_yet"
            self._save_finding(finding)
        session["status"] = "closed"
        self._store.save_record(REPAIR_KIND, session_id, session, subject_key=session["finding_id"])
        return session

    def confirm_follow_ups(self, *, finding_id: str | None = None, now: datetime | None = None) -> list[dict[str, Any]]:
        """Resolve a repaired finding once the delayed follow-up succeeded.

        The criteria are the engine's ``confirmation`` settings: at least
        ``min_days`` after the repair, ``successes`` correct answers on the
        concept from a review, an exam or an understanding check — not from
        the transfer question itself.
        """
        moment = now or self._clock()
        changed: list[dict[str, Any]] = []
        candidates = [self.finding(finding_id)] if finding_id else self.findings(status="repair_demonstrated")
        for finding in candidates:
            if finding is None or finding.get("status") != "repair_demonstrated":
                continue
            repaired_at = ((finding.get("repair") or {}).get("initial_repair_at")) or finding.get("updated_at")
            if not repaired_at:
                continue
            since = datetime.fromisoformat(repaired_at)
            earliest = since + timedelta(days=int(self.confirmation.get("min_days", 2)))
            successes = [
                event
                for event in self.events(concept_id=finding["concept_id"])
                if event.get("correct") is True and not event.get("invalidated") and event.get("source") in ("review", "exam", "check", "histology")
                and datetime.fromisoformat(event["at"]) >= earliest
            ]
            if len(successes) >= int(self.confirmation.get("successes", 1)) and moment >= earliest:
                self._transition(finding, "resolved", f"Gecikmeli tekrar doğru ({len(successes)} başarı, en az {self.confirmation.get('min_days', 2)} gün sonra): bulgu çözüldü.")
                finding.setdefault("repair", {})["confirmed_at"] = moment.isoformat()
                for follow_up in finding.get("follow_ups", []):
                    if follow_up.get("done_at") is None:
                        follow_up["done_at"] = moment.isoformat()
                        follow_up["outcome"] = "confirmed"
                        follow_up["event_id"] = successes[-1].get("event_id")
                changed.append(self._save_finding(finding))
        return changed

    # ------------------------------------------------------------------
    # understanding checks: a question, a confidence, a short explanation
    # ------------------------------------------------------------------

    def start_check(self, *, concept_id: str | None = None, topic_id: str | None = None, subject: str | None = None) -> dict[str, Any] | None:
        """Pick a keyed bank question for the concept or topic and open a check."""
        candidates = self._store.query_questions(concept_id=concept_id, topic_id=topic_id, subject=subject, with_answer_key=True, limit=60)
        if not candidates:
            return None
        recent = {event.get("question_id") for event in self._store.list_records(EVENT_KIND, limit=200)}
        ordered = sorted(candidates, key=lambda item: (item.question_id in recent, item.created_at))
        question = ordered[0]
        check = {
            "check_id": new_id("chk"),
            "question_id": question.question_id,
            "concept_id": concept_id,
            "topic_id": topic_id,
            "status": "open",
            "opened_at": self._clock().isoformat(),
            "question": question_payload(question, reveal=False, include_explanation=False, curriculum=self._curriculum),
            "explanation_required": True,
            "result": None,
        }
        self._store.save_record(CHECK_KIND, check["check_id"], check, subject_key=concept_id or topic_id or question.subject)
        return check

    def check(self, check_id: str) -> dict[str, Any] | None:
        return self._store.get_record(CHECK_KIND, check_id)

    def answer_check(self, check_id: str, answer_key: str | None, *, confidence: str | None, reasoning: str = "", submission_id: str | None = None) -> dict[str, Any]:
        check = self.check(check_id)
        if check is None:
            raise ValueError("Anlama kontrolü bulunamadı.")
        if check.get("result") is not None:
            return check
        question = self._store.get_question(check["question_id"])
        if question is None:
            raise ValueError("Soru bulunamadı.")
        correct = grade(question, answer_key)
        event = self.record_event(question, correct=correct, answer_key=answer_key, confidence=confidence, reasoning=reasoning, source="check", submission_id=submission_id)
        self._learning.record(question, bool(correct), chosen_key=answer_key)
        check["result"] = {"answer_key": answer_key, "correct": correct, "event_id": event["event_id"], "classification": event.get("classification"), "at": self._clock().isoformat()}
        check["question"] = question_payload(question, reveal=True, include_explanation=True, curriculum=self._curriculum)
        check["status"] = "answered"
        self._store.save_record(CHECK_KIND, check_id, check, subject_key=check.get("concept_id") or check.get("topic_id") or question.subject)
        return check

    # ------------------------------------------------------------------
    # corrections from the question-review workflow
    # ------------------------------------------------------------------

    def invalidate_question(self, question_id: str, reason: str) -> dict[str, int]:
        """A question found wrong: its events stay but no longer count as evidence."""
        events = 0
        for event in self._store.list_records(EVENT_KIND, limit=5000):
            if event.get("question_id") == question_id and not event.get("invalidated"):
                event["invalidated"] = True
                event["invalidation_reason"] = _excerpt(reason, 200)
                self._store.save_record(EVENT_KIND, event["event_id"], event, subject_key=event["concept_ids"][0])
                events += 1
        findings = 0
        for finding in self._store.list_records(FINDING_KIND, limit=5000):
            touched = False
            for item in finding.get("evidence", []):
                if item.get("question_id") == question_id and item.get("valid", True):
                    item["valid"] = False
                    item["invalidation_reason"] = _excerpt(reason, 200)
                    touched = True
            if not touched:
                continue
            remaining = self._distinct_support(finding)
            if remaining == 0 and finding.get("status") in OPEN_STATUSES | {"disputed"}:
                self._transition(finding, "withdrawn", f"Dayandığı soru geçersiz sayıldı ({_excerpt(reason, 120)}); bulgu geri çekildi.")
            elif finding.get("status") == "supported" and remaining < EVIDENCE_TO_SUPPORT:
                self._transition(finding, "hypothesis", "Bir kanıt geçersiz sayıldı; bulgu hipoteze indi.")
            else:
                finding.setdefault("history", []).append({"at": self._clock().isoformat(), "status": finding["status"], "note": "Bir kanıt geçersiz sayıldı; kalan kanıt yeterli.", "by": "rule"})
            self._save_finding(finding)
            findings += 1
        return {"events": events, "findings": findings}

    # ------------------------------------------------------------------
    # payloads
    # ------------------------------------------------------------------

    def finding_payload(self, finding: dict[str, Any]) -> dict[str, Any]:
        status = finding.get("status", "hypothesis")
        return {
            **finding,
            "status_label": FINDING_STATUS_LABELS_TR.get(status, status),
            "subject_label": SUBJECT_LABELS_TR.get(finding.get("subject", ""), finding.get("subject", "")),
            "open": status in OPEN_STATUSES,
            "evidence_count": sum(1 for item in finding.get("evidence", []) if item.get("valid", True)),
            "repairs": self.repairs_for(finding["finding_id"]),
        }

    def overview(self, *, limit: int = 30) -> dict[str, Any]:
        items = self.findings(limit=500)
        counts: dict[str, int] = {}
        for item in items:
            counts[item.get("status", "hypothesis")] = counts.get(item.get("status", "hypothesis"), 0) + 1
        open_items = [self.finding_payload(item) for item in items if item.get("status") in OPEN_STATUSES | {"disputed"}][:limit]
        closed = [self.finding_payload(item) for item in items if item.get("status") in ("resolved", "dismissed", "withdrawn")][:limit]
        events = self._store.list_records(EVENT_KIND, limit=20)
        return {
            "findings": open_items,
            "closed": closed,
            "counts": counts,
            "recent_events": [
                {**event, "classification_label": CLASSIFICATION_LABELS_TR.get(event.get("classification", "unknown"), event.get("classification", ""))}
                for event in events
            ],
            "confidence_levels": [{"key": key, "label": label} for key, label in CONFIDENCE_LABELS_TR.items()],
            "confirmation": dict(self.confirmation),
            "checks": [item for item in self._store.list_records(CHECK_KIND, limit=10) if item.get("status") == "open"],
        }
