"""Does the cited passage support the answer? A second reading, kept apart.

Structural validation (``validate_question``) says a question is well
formed; reference integrity (``build_question``) says the page it cites was
one of the pages it was given. Neither says the passage supports the key.
This module adds that third, separate outcome for generated, source-grounded
questions: a bounded structured review of the question against the cited
passage only, recorded with the source's content hash so it goes stale when
the material changes and unavailable when it is deleted.

Statuses are named for what was found, never for what was not: a review that
agreed is *source_supported*, an alternative defensible option or reliance on
outside information is *needs_review*, a contradiction is
*conflicting_evidence*, too little to tell is *insufficient_evidence*, a
timeout is *unresolved*. Nothing here is "medically verified": a model that
agrees with a model is not a fact check, and the label says so.

Only *source_supported* items go into a newly generated scored paper; the
rest stay in the bank with their status and the paper's notes say what was
left out. Imported professor questions keep their keys untouched; they are
not reviewed, and any disagreement is a flag beside them, never an edit.

The student's own flag ("Soruda hata olabilir") and the invalidation of a
question live here too. Invalidation preserves the attempt and records the
correction; the learning and understanding engines then drop the evidence
that rested on the question.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from datetime import datetime
from typing import Any

from app.core.time import utc_now
from app.medical.model import MedicalModelError
from app.medical.models import Question, QuestionOrigin, new_id
from app.medical.prompts import PIPELINE_SYSTEM, support_review_prompt
from app.medical.schemas import SUPPORT_REVIEW_SCHEMA

SUPPORT_KIND = "support_review"
FLAG_KIND = "question_flag"
REVIEW_VERSION = "support-1"
MAX_REVIEWS_PER_BATCH = 12
PASSAGE_CHARS = 1800

SUPPORT_STATUS_LABELS_TR: dict[str, str] = {
    "source_supported": "Kaynak destekli",
    "needs_review": "İnceleme gerekli",
    "conflicting_evidence": "Çelişkili kanıt",
    "insufficient_evidence": "Yetersiz kanıt",
    "unresolved": "İnceleme tamamlanamadı",
    "not_applicable": "Kaynaksız (yalnız çalışma)",
    "imported": "Hoca anahtarı (kaynak incelemesi yok)",
    "stale": "Kaynak değişti; inceleme eski",
    "unavailable": "Kaynak silinmiş",
    "invalidated": "Geçersiz sayıldı",
}
SCORED_STATUSES = frozenset({"source_supported", "imported"})

FLAG_KINDS_TR: dict[str, str] = {
    "disputed_answer": "Cevap anahtarı tartışmalı",
    "multiple_answers": "Birden fazla doğru olabilir",
    "source_mismatch": "Kaynak uyuşmuyor",
    "figure_problem": "Şekil okunmuyor ya da yanıltıcı",
}


def references_hash(store: Any, question: Question) -> dict[str, str | None]:
    """The content hash of every document the question rests on (None when gone)."""
    document_ids: list[str] = [ref.document_id for ref in question.references if ref.document_id]
    if question.image_ref and "|" in question.image_ref:
        document_ids.append(question.image_ref.split("|", 1)[0])
    hashes: dict[str, str | None] = {}
    for document_id in dict.fromkeys(document_ids):
        document = store.get_document(document_id)
        hashes[document_id] = document.sha256 if document is not None else None
    return hashes


def status_from_review(data: dict[str, Any], *, correct_key: str | None, has_figure: bool) -> tuple[str, str]:
    """Map the reviewer's findings to a status and a one-line Turkish reason."""
    supports = str(data.get("evidence_supports_key") or "cannot_tell")
    explanation = str(data.get("explanation_follows_evidence") or "cannot_tell")
    alternative = str(data.get("alternative_defensible_option") or "").strip().upper()
    outside = bool(data.get("relies_on_outside_information"))
    figure = str(data.get("figure_supports_identification") or "not_applicable")
    conflict = " ".join(str(data.get("conflict_with_evidence") or "").split())
    if conflict or supports == "no":
        return "conflicting_evidence", conflict or "Kaynak pasaj işaretli anahtarı desteklemiyor."
    if alternative and alternative != str(correct_key or "").upper():
        return "needs_review", f"{alternative} şıkkı da bu ifadeyle savunulabilir: soru muğlak."
    if outside:
        return "needs_review", "Cevap kaynak pasajın dışındaki bilgiye dayanıyor."
    if has_figure and figure == "no":
        return "needs_review", "Şekil açıklaması istenen tanımlamayı desteklemiyor."
    if supports == "cannot_tell" or explanation == "cannot_tell" or (has_figure and figure == "cannot_tell"):
        return "insufficient_evidence", "Pasaj anahtarı doğrulamaya yetmiyor."
    if supports == "partly" or explanation in ("partly", "no"):
        return "insufficient_evidence", "Pasaj anahtarı kısmen destekliyor ya da açıklama pasaja dayanmıyor."
    return "source_supported", "Pasaj anahtarı ve açıklamayı destekliyor."


class SourceSupportReviewer:
    def __init__(self, store: Any, model: Any, *, clock: Callable[[], datetime] | None = None, batch_limit: int = MAX_REVIEWS_PER_BATCH) -> None:
        self._store = store
        self._model = model
        self._clock = clock or utc_now
        self._batch_limit = max(1, int(batch_limit))

    # ------------------------------------------------------------------
    # what the question rests on
    # ------------------------------------------------------------------

    def evidence_text(self, question: Question) -> tuple[str, str]:
        """The cited pages' text (bounded) and the figure description, from the store as it is now."""
        parts: list[str] = []
        for ref in question.references[:4]:
            page = self._store.get_page(ref.document_id, ref.page_number)
            body = " ".join(page.text.split()) if page is not None else ""
            if not body:
                # A document indexed before its pages were kept whole still has its chunks.
                chunks = self._store.chunks(document_ids=[ref.document_id], page_from=ref.page_number, page_to=ref.page_number)
                body = " ".join(" ".join(chunk.text.split()) for chunk in chunks if chunk.kind == "text")
            if not body:
                continue
            parts.append(f"[{ref.title or ref.document_id}, s. {ref.page_number}] {body[:PASSAGE_CHARS]}")
        figure_text = ""
        if question.image_ref and "|" in question.image_ref:
            document_id, _, page_number = question.image_ref.partition("|")
            try:
                page = self._store.get_page(document_id, int(page_number))
            except ValueError:
                page = None
            if page is not None:
                figure_text = " ".join(page.visual_summary.split())[:PASSAGE_CHARS]
        return "\n\n".join(parts), figure_text

    def status_of(self, question: Question) -> dict[str, Any]:
        """The current support status, with the source's version checked now."""
        invalidated = question.metadata.get("invalidated")
        if invalidated:
            return {"status": "invalidated", "label": SUPPORT_STATUS_LABELS_TR["invalidated"], "scored": False, "reason": str(invalidated.get("reason", "")), "checked_at": invalidated.get("at")}
        if question.origin in (QuestionOrigin.IMPORTED_EXAM, QuestionOrigin.MANUAL):
            return {"status": "imported", "label": SUPPORT_STATUS_LABELS_TR["imported"], "scored": question.has_answer_key, "reason": "Hocanın ya da öğrencinin verdiği anahtar aynen korunur.", "checked_at": None}
        if not question.references and not question.image_ref:
            return {"status": "not_applicable", "label": SUPPORT_STATUS_LABELS_TR["not_applicable"], "scored": False, "reason": "Kaynak pasaj yok: çalışma içeriği, kaynak destekli ölçme değil.", "checked_at": None}
        support = question.metadata.get("support")
        if not isinstance(support, dict) or not support.get("status"):
            return {"status": "needs_review", "label": SUPPORT_STATUS_LABELS_TR["needs_review"], "scored": False, "reason": "Kaynak desteği henüz incelenmedi.", "checked_at": None}
        current = references_hash(self._store, question)
        recorded = support.get("source_hash") or {}
        for document_id, digest in recorded.items():
            if current.get(document_id) is None:
                return {"status": "unavailable", "label": SUPPORT_STATUS_LABELS_TR["unavailable"], "scored": False, "reason": "Kaynak belge silinmiş; inceleme artık geçerli değil.", "checked_at": support.get("checked_at")}
            if digest != current.get(document_id):
                return {"status": "stale", "label": SUPPORT_STATUS_LABELS_TR["stale"], "scored": False, "reason": "Kaynak belge değişmiş; inceleme yenilenmeli.", "checked_at": support.get("checked_at")}
        status = str(support.get("status"))
        return {"status": status, "label": SUPPORT_STATUS_LABELS_TR.get(status, status), "scored": status in SCORED_STATUSES, "reason": str(support.get("reason", "")), "checked_at": support.get("checked_at"), "assessor": support.get("assessor"), "version": support.get("version")}

    def scored_eligible(self, question: Question) -> tuple[bool, str]:
        status = self.status_of(question)
        return bool(status["scored"]), status["label"]

    # ------------------------------------------------------------------
    # the review
    # ------------------------------------------------------------------

    async def review(self, question: Question) -> dict[str, Any]:
        """Review one question against its cited passage and record the outcome."""
        now = self._clock().isoformat()
        if question.origin in (QuestionOrigin.IMPORTED_EXAM, QuestionOrigin.MANUAL):
            return self.status_of(question)
        if not question.references and not question.image_ref:
            record = {"status": "not_applicable", "reason": SUPPORT_STATUS_LABELS_TR["not_applicable"], "checked_at": now, "assessor": "rule", "version": REVIEW_VERSION, "source_hash": {}}
            return self._store_support(question, record)
        evidence_text, figure_text = self.evidence_text(question)
        source_hash = references_hash(self._store, question)
        if not evidence_text and not figure_text:
            record = {"status": "unavailable", "reason": "Kaynak sayfalar depoda yok.", "checked_at": now, "assessor": "rule", "version": REVIEW_VERSION, "source_hash": source_hash}
            return self._store_support(question, record)
        if not self._model.available:
            record = {"status": "unresolved", "reason": "Model sağlayıcısı kapalı; inceleme yapılamadı.", "checked_at": now, "assessor": "none", "version": REVIEW_VERSION, "source_hash": source_hash}
            return self._store_support(question, record)
        prompt = support_review_prompt(stem=question.stem, options=[(option.key, option.text) for option in question.options], correct_key=question.correct_key or "", explanation=question.explanation, evidence_text=evidence_text, figure_text=figure_text)
        try:
            data = await self._model.structured("support_review", prompt, SUPPORT_REVIEW_SCHEMA, system_prompt=PIPELINE_SYSTEM, max_attempts=1)
        except MedicalModelError as exc:
            record = {"status": "unresolved", "reason": f"İnceleme tamamlanamadı: {exc}", "checked_at": now, "assessor": "none", "version": REVIEW_VERSION, "source_hash": source_hash}
            return self._store_support(question, record)
        status, reason = status_from_review(data, correct_key=question.correct_key, has_figure=bool(question.image_ref))
        record = {
            "status": status,
            "reason": reason,
            "checked_at": now,
            "assessor": f"model:{getattr(self._model, 'model', '') or 'unknown'}",
            "version": REVIEW_VERSION,
            "source_hash": source_hash,
            "findings": {
                "evidence_supports_key": data.get("evidence_supports_key"),
                "explanation_follows_evidence": data.get("explanation_follows_evidence"),
                "alternative_defensible_option": data.get("alternative_defensible_option") or "",
                "relies_on_outside_information": bool(data.get("relies_on_outside_information")),
                "figure_supports_identification": data.get("figure_supports_identification"),
                "conflict_with_evidence": data.get("conflict_with_evidence") or "",
            },
            "reasoning": " ".join(str(data.get("reasoning") or "").split())[:600],
        }
        return self._store_support(question, record)

    def _store_support(self, question: Question, record: dict[str, Any]) -> dict[str, Any]:
        question.metadata["support"] = record
        self._store.save_question(question)
        self._store.save_record(SUPPORT_KIND, new_id("sr"), {**record, "question_id": question.question_id}, subject_key=question.question_id)
        return self.status_of(question)

    async def gate(self, questions: Iterable[Question]) -> tuple[list[Question], list[Question], list[str]]:
        """Keep the source-supported items of a new paper; say what was left out and why.

        Questions with no source are not reviewed: they stay, labelled as
        study content. At most ``batch_limit`` reviews run; the rest are
        left unresolved and out of the paper rather than waved through.
        """
        kept: list[Question] = []
        quarantined: list[Question] = []
        reviewed = 0
        reasons: dict[str, int] = {}
        for question in questions:
            grounded = bool(question.references or question.image_ref) and question.origin not in (QuestionOrigin.IMPORTED_EXAM, QuestionOrigin.MANUAL)
            if not grounded:
                if not isinstance(question.metadata.get("support"), dict):
                    question.metadata["support"] = {"status": "not_applicable", "reason": SUPPORT_STATUS_LABELS_TR["not_applicable"], "checked_at": self._clock().isoformat(), "assessor": "rule", "version": REVIEW_VERSION, "source_hash": {}}
                kept.append(question)
                continue
            if reviewed >= self._batch_limit:
                question.metadata["support"] = {"status": "unresolved", "reason": f"Tek seferde en çok {self._batch_limit} soru incelenir; bu soru incelenmedi.", "checked_at": self._clock().isoformat(), "assessor": "none", "version": REVIEW_VERSION, "source_hash": references_hash(self._store, question)}
                quarantined.append(question)
                reasons["unresolved"] = reasons.get("unresolved", 0) + 1
                continue
            reviewed += 1
            status = await self.review(question)
            if status["scored"]:
                kept.append(question)
            else:
                quarantined.append(question)
                reasons[status["status"]] = reasons.get(status["status"], 0) + 1
        notes: list[str] = []
        if quarantined:
            detail = ", ".join(f"{SUPPORT_STATUS_LABELS_TR.get(status, status)}×{count}" for status, count in sorted(reasons.items()))
            notes.append(f"{len(quarantined)} soru kaynak desteği doğrulanamadığı için kâğıda alınmadı ({detail}); soru bankasında durumuyla duruyor.")
        unsourced = sum(1 for question in kept if (question.metadata.get("support") or {}).get("status") == "not_applicable")
        if unsourced:
            notes.append(f"{unsourced} soru kaynak pasaja dayanmıyor: çalışma içeriği olarak işaretlendi, kaynak destekli ölçme değil.")
        return kept, quarantined, notes

    # ------------------------------------------------------------------
    # the student's flag, and invalidation
    # ------------------------------------------------------------------

    def flag(self, question_id: str, kind: str, note: str = "", *, exam_id: str | None = None, attempt_id: str | None = None) -> dict[str, Any]:
        if kind not in FLAG_KINDS_TR:
            raise ValueError("Bilinmeyen işaret türü.")
        question = self._store.get_question(question_id)
        if question is None:
            raise ValueError("Soru bulunamadı.")
        record = {
            "flag_id": new_id("flag"),
            "question_id": question_id,
            "kind": kind,
            "kind_label": FLAG_KINDS_TR[kind],
            "note": " ".join(str(note or "").split())[:400],
            "exam_id": exam_id,
            "attempt_id": attempt_id,
            "at": self._clock().isoformat(),
            "status": "open",
        }
        self._store.save_record(FLAG_KIND, record["flag_id"], record, subject_key=question_id)
        flags = [item for item in question.metadata.get("flags", []) if isinstance(item, dict)]
        flags.append({"flag_id": record["flag_id"], "kind": kind, "at": record["at"]})
        question.metadata["flags"] = flags[-20:]
        self._store.save_question(question)
        return record

    def flags_for(self, question_id: str) -> list[dict[str, Any]]:
        return self._store.list_records(FLAG_KIND, subject_key=question_id, limit=50)

    def open_flags(self, *, limit: int = 50) -> list[dict[str, Any]]:
        return [item for item in self._store.list_records(FLAG_KIND, limit=500) if item.get("status") == "open"][:limit]

    def invalidate(self, question_id: str, reason: str, *, by: str = "student") -> Question:
        """Mark a question wrong for scoring from now on; the past is kept as it was."""
        question = self._store.get_question(question_id)
        if question is None:
            raise ValueError("Soru bulunamadı.")
        question.metadata["invalidated"] = {"reason": " ".join(str(reason or "").split())[:300], "at": self._clock().isoformat(), "by": by}
        question.tags = list(dict.fromkeys(question.tags + ["geçersiz"]))[:20]
        self._store.save_question(question)
        for flag in self.flags_for(question_id):
            if flag.get("status") == "open":
                flag["status"] = "resolved"
                flag["resolution"] = "invalidated"
                self._store.save_record(FLAG_KIND, flag["flag_id"], flag, subject_key=question_id)
        return question

    def resolve_flag(self, flag_id: str, resolution: str, note: str = "") -> dict[str, Any]:
        for flag in self._store.list_records(FLAG_KIND, limit=1000):
            if flag.get("flag_id") == flag_id:
                flag["status"] = "resolved"
                flag["resolution"] = resolution
                flag["resolution_note"] = " ".join(str(note or "").split())[:300]
                self._store.save_record(FLAG_KIND, flag_id, flag, subject_key=flag["question_id"])
                return flag
        raise ValueError("İşaret bulunamadı.")

    def payload(self, question: Question) -> dict[str, Any]:
        status = self.status_of(question)
        return {**status, "flags": self.flags_for(question.question_id), "validation": question.metadata.get("validation") or {}}
