"""Source support and ambiguity review, the student's flag, and invalidation.

A well-formed question with a real page reference is not thereby supported
by that page: the reviewer reads the passage and says what it found, in
statuses named for the finding; only source-supported items go into a new
scored paper; imported keys are never touched; a changed or deleted source
makes a review stale or unavailable; a flagged and invalidated question
keeps its attempt and corrects the learning record it moved.
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from app.medical.anatomy import AnatomyAssetRegistry, AnatomyLab
from app.medical.catalog import Curriculum
from app.medical.concepts import default_concept_graph
from app.medical.generation import GenerationError, QuestionGenerator
from app.medical.learning import LearningEngine
from app.medical.model import MedicalModelClient
from app.medical.models import (
    DocumentPage,
    DocumentStatus,
    ExamAttempt,
    Question,
    QuestionAttempt,
    QuestionOption,
    QuestionOrigin,
    SourceReference,
    StudyDocument,
)
from app.medical.retrieval import Retriever
from app.medical.review import FLAG_KINDS_TR, SUPPORT_STATUS_LABELS_TR, SourceSupportReviewer, status_from_review
from app.medical.store import MedicalStore
from app.medical.terminology import load_anatomy_data
from tests.test_medical_exams import ARM, SLIDE_OPTIONS, SLIDE_STEM, batch, config, draft, seed_lecture

BASE = datetime(2026, 9, 8, 9, 0, tzinfo=timezone.utc)


class Gateway:
    def __init__(self, *replies: str, fail: bool = False) -> None:
        self.replies = list(replies)
        self.prompts: list[str] = []
        self.fail = fail

    async def generate(self, request, context, **kwargs):
        self.prompts.append(request.text)
        if self.fail:
            raise RuntimeError("provider down")
        reply = self.replies.pop(0) if len(self.replies) > 1 else self.replies[0]
        return SimpleNamespace(text=reply)


def review_reply(*, supports: str = "yes", explanation: str = "yes", alternative: str = "", outside: bool = False, figure: str = "not_applicable", conflict: str = "", reasoning: str = "Pasaj anahtarı açıkça veriyor.") -> str:
    return json.dumps({"evidence_supports_key": supports, "explanation_follows_evidence": explanation, "alternative_defensible_option": alternative, "relies_on_outside_information": outside, "figure_supports_identification": figure, "conflict_with_evidence": conflict, "reasoning": reasoning})


def grounded_question(question_id: str = "g1", *, origin: str = QuestionOrigin.LECTURE_DERIVED, refs: bool = True, image_ref: str | None = None) -> Question:
    options = [QuestionOption(key, text) for key, text in zip("ABCD", ["Capitulum humeri", "Trochlea humeri", "Olecranon", "Acromion"])]
    return Question(
        question_id=question_id,
        subject="anatomy",
        stem="Humerus distal ucunda radius başı ile eklem yapan yapı hangisidir?",
        options=options,
        correct_key="A",
        topic_id=ARM,
        concept_ids=["anatomy.humerus"],
        explanation="Capitulum humeri radius başı ile eklem yapar.",
        references=[SourceReference("d1", 12, quote="Capitulum humeri caput radii ile eklem yapar.", title="Üst ekstremite")] if refs else [],
        origin=origin,
        image_ref=image_ref,
    )


def store_with_source(path=None) -> MedicalStore:
    store = MedicalStore(path)
    store.save_document(StudyDocument("d1", "Üst ekstremite", "ust.pdf", "sha-1", subject="anatomy", page_count=20, status=DocumentStatus.READY))
    store.save_page(DocumentPage("d1", 12, text="Humerus distal ucu: capitulum humeri caput radii ile, trochlea humeri ise ulna ile eklem yapar."))
    return store


def reviewer_for(store: MedicalStore, gateway=None) -> SourceSupportReviewer:
    return SourceSupportReviewer(store, MedicalModelClient(gateway), clock=lambda: BASE)


# ---------------------------------------------------------------------------
# the mapping and the review
# ---------------------------------------------------------------------------


def test_review_findings_map_to_statuses_named_for_what_was_found() -> None:
    assert status_from_review({"evidence_supports_key": "yes", "explanation_follows_evidence": "yes"}, correct_key="A", has_figure=False)[0] == "source_supported"
    assert status_from_review({"evidence_supports_key": "yes", "explanation_follows_evidence": "yes", "alternative_defensible_option": "C"}, correct_key="A", has_figure=False)[0] == "needs_review"
    assert status_from_review({"evidence_supports_key": "yes", "explanation_follows_evidence": "yes", "alternative_defensible_option": "A"}, correct_key="A", has_figure=False)[0] == "source_supported", "naming the key itself is no alternative"
    assert status_from_review({"evidence_supports_key": "no"}, correct_key="A", has_figure=False)[0] == "conflicting_evidence"
    assert status_from_review({"evidence_supports_key": "yes", "explanation_follows_evidence": "yes", "conflict_with_evidence": "Pasaj ulna diyor."}, correct_key="A", has_figure=False) == ("conflicting_evidence", "Pasaj ulna diyor.")
    assert status_from_review({"evidence_supports_key": "yes", "explanation_follows_evidence": "yes", "relies_on_outside_information": True}, correct_key="A", has_figure=False)[0] == "needs_review"
    assert status_from_review({"evidence_supports_key": "cannot_tell"}, correct_key="A", has_figure=False)[0] == "insufficient_evidence"
    assert status_from_review({"evidence_supports_key": "partly", "explanation_follows_evidence": "yes"}, correct_key="A", has_figure=False)[0] == "insufficient_evidence"
    assert status_from_review({"evidence_supports_key": "yes", "explanation_follows_evidence": "yes", "figure_supports_identification": "no"}, correct_key="A", has_figure=True)[0] == "needs_review"
    assert status_from_review({"evidence_supports_key": "yes", "explanation_follows_evidence": "yes", "figure_supports_identification": "no"}, correct_key="A", has_figure=False)[0] == "source_supported"
    assert "medically verified" not in " ".join(SUPPORT_STATUS_LABELS_TR.values()).lower() and "doğrulandı" not in " ".join(SUPPORT_STATUS_LABELS_TR.values())


def test_a_supported_question_is_recorded_with_its_source_hash_and_becomes_scorable() -> None:
    store = store_with_source()
    question = grounded_question()
    store.save_question(question)
    gateway = Gateway(review_reply())
    reviewer = reviewer_for(store, gateway)

    assert reviewer.status_of(question) == {"status": "needs_review", "label": "İnceleme gerekli", "scored": False, "reason": "Kaynak desteği henüz incelenmedi.", "checked_at": None}
    status = asyncio.run(reviewer.review(question))

    assert status["status"] == "source_supported" and status["scored"] is True and status["assessor"].startswith("model:") and status["version"] == "support-1"
    stored = store.get_question("g1")
    assert stored.metadata["support"]["source_hash"] == {"d1": "sha-1"} and stored.metadata["support"]["findings"]["evidence_supports_key"] == "yes"
    assert store.count_records("support_review", subject_key="g1") == 1
    assert "capitulum humeri caput radii ile" in gateway.prompts[0], "the passage, not the model's memory, is what is judged"


@pytest.mark.parametrize(
    "reply, status",
    [
        (review_reply(alternative="B"), "needs_review"),
        (review_reply(conflict="Pasaj trochlea diyor."), "conflicting_evidence"),
        (review_reply(supports="cannot_tell"), "insufficient_evidence"),
        (review_reply(outside=True), "needs_review"),
        ("not json at all", "unresolved"),
    ],
)
def test_ambiguity_conflict_thin_evidence_and_a_broken_reply_are_named_and_kept_out_of_scoring(reply, status) -> None:
    store = store_with_source()
    question = grounded_question()
    store.save_question(question)
    reviewer = reviewer_for(store, Gateway(reply))

    result = asyncio.run(reviewer.review(question))

    assert result["status"] == status and result["scored"] is False
    assert reviewer.status_of(store.get_question("g1"))["status"] == status


def test_a_review_timeout_or_missing_model_is_unresolved_not_approved() -> None:
    store = store_with_source()
    question = grounded_question()
    store.save_question(question)
    down = asyncio.run(reviewer_for(store, Gateway("x", fail=True)).review(question))
    assert down["status"] == "unresolved" and "İnceleme tamamlanamadı" in down["reason"]
    absent = asyncio.run(reviewer_for(store, None).review(grounded_question("g2")))
    assert absent["status"] == "unresolved" and "kapalı" in absent["reason"]


def test_imported_keys_are_never_reviewed_or_rewritten_and_unsourced_items_are_study_content() -> None:
    store = store_with_source()
    imported = grounded_question("imp", origin=QuestionOrigin.IMPORTED_EXAM)
    store.save_question(imported)
    gateway = Gateway(review_reply(conflict="Anahtar yanlış."))
    reviewer = reviewer_for(store, gateway)

    status = asyncio.run(reviewer.review(imported))

    assert status["status"] == "imported" and status["scored"] is True and gateway.prompts == []
    assert store.get_question("imp").correct_key == "A" and "support" not in store.get_question("imp").metadata

    unsourced = grounded_question("free", origin=QuestionOrigin.GENERATED, refs=False)
    store.save_question(unsourced)
    assert asyncio.run(reviewer.review(unsourced))["status"] == "not_applicable"
    assert reviewer.status_of(unsourced)["scored"] is False and gateway.prompts == []


def test_a_changed_or_deleted_source_makes_the_review_stale_or_unavailable() -> None:
    store = store_with_source()
    question = grounded_question()
    store.save_question(question)
    reviewer = reviewer_for(store, Gateway(review_reply()))
    assert asyncio.run(reviewer.review(question))["status"] == "source_supported"

    changed = store.get_document("d1")
    changed.sha256 = "sha-2"
    store.save_document(changed)
    assert reviewer.status_of(store.get_question("g1"))["status"] == "stale"
    assert reviewer.scored_eligible(store.get_question("g1")) == (False, "Kaynak değişti; inceleme eski")

    store.delete_document("d1")
    assert reviewer.status_of(store.get_question("g1"))["status"] == "unavailable"


# ---------------------------------------------------------------------------
# the gate in a new paper
# ---------------------------------------------------------------------------


def test_the_gate_keeps_supported_and_unsourced_items_and_explains_the_rest() -> None:
    store = store_with_source()
    supported, ambiguous, unsourced = grounded_question("s"), grounded_question("a"), grounded_question("u", refs=False, origin=QuestionOrigin.GENERATED)
    for question in (supported, ambiguous, unsourced):
        store.save_question(question)
    reviewer = reviewer_for(store, Gateway(review_reply(), review_reply(alternative="B")))

    kept, quarantined, notes = asyncio.run(reviewer.gate([supported, ambiguous, unsourced]))

    assert [item.question_id for item in kept] == ["s", "u"] and [item.question_id for item in quarantined] == ["a"]
    assert notes[0].startswith("1 soru kaynak desteği doğrulanamadığı için kâğıda alınmadı (İnceleme gerekli×1)")
    assert notes[1].startswith("1 soru kaynak pasaja dayanmıyor")
    assert unsourced.metadata["support"]["status"] == "not_applicable"


def test_the_gate_is_bounded_and_leaves_the_rest_unresolved_rather_than_waved_through() -> None:
    store = store_with_source()
    questions = [grounded_question(f"q{index}") for index in range(3)]
    for question in questions:
        store.save_question(question)
    reviewer = SourceSupportReviewer(store, MedicalModelClient(Gateway(review_reply())), clock=lambda: BASE, batch_limit=2)

    kept, quarantined, notes = asyncio.run(reviewer.gate(questions))

    assert [item.question_id for item in kept] == ["q0", "q1"] and [item.question_id for item in quarantined] == ["q2"]
    assert quarantined[0].metadata["support"]["status"] == "unresolved" and "en çok 2 soru" in quarantined[0].metadata["support"]["reason"]


def make_generator(store: MedicalStore, gateway, *, reviewer) -> QuestionGenerator:
    curriculum = Curriculum()
    concepts = default_concept_graph()
    structures, _terms, _source = load_anatomy_data()
    learning = LearningEngine(store, curriculum, concepts, clock=lambda: BASE)
    return QuestionGenerator(store, MedicalModelClient(gateway), Retriever(store), curriculum, concepts, AnatomyLab(structures, AnatomyAssetRegistry(None)), learning, reviewer=reviewer)


def test_a_new_paper_quarantines_the_unsupported_and_returns_a_smaller_valid_paper() -> None:
    store = MedicalStore()
    seed_lecture(store)
    paper = batch(
        draft("Radius başı ile eklem yapan humerus çıkıntısı hangisidir?", correct="A", source_index=1, source_page=12),
        draft(SLIDE_STEM, SLIDE_OPTIONS, correct="B", source_index=1, source_page=12),
    )
    # The second item is quarantined for a reason that does not depend on a
    # letter: generation re-letters the options with a random seed, so an
    # "alternative C" would sometimes name the key the shuffle just produced.
    gateway = Gateway(paper, review_reply(), review_reply(outside=True))
    reviewer = SourceSupportReviewer(store, MedicalModelClient(gateway), clock=lambda: BASE)
    generator = make_generator(store, gateway, reviewer=reviewer)

    questions, notes = asyncio.run(generator.generate(config(question_count=2, document_ids=["d1"])))

    assert len(questions) == 1 and questions[0].metadata["support"]["status"] == "source_supported"
    assert questions[0].metadata["validation"] == {"structure": "passed", "references": "verified", "checked_at": questions[0].metadata["validation"]["checked_at"]}
    assert any("kâğıda alınmadı" in note for note in notes)
    bank = store.query_questions(limit=10)
    assert len(bank) == 2 and {item.metadata["support"]["status"] for item in bank} == {"source_supported", "needs_review"}


def test_a_paper_with_nothing_supported_is_refused_with_the_reason() -> None:
    store = MedicalStore()
    seed_lecture(store)
    gateway = Gateway(batch(draft("Radius başı ile eklem yapan humerus çıkıntısı hangisidir?", correct="A", source_index=1, source_page=12)), "broken reply")
    generator = make_generator(store, gateway, reviewer=SourceSupportReviewer(store, MedicalModelClient(gateway), clock=lambda: BASE))
    with pytest.raises(GenerationError, match="kaynak desteği süzgecinden geçmedi"):
        asyncio.run(generator.generate(config(question_count=1, document_ids=["d1"])))
    assert store.query_questions(limit=5)[0].metadata["support"]["status"] == "unresolved"


# ---------------------------------------------------------------------------
# the student's flag and invalidation
# ---------------------------------------------------------------------------


def test_a_flag_is_recorded_on_the_question_and_kinds_are_checked() -> None:
    store = store_with_source()
    store.save_question(grounded_question())
    reviewer = reviewer_for(store)

    flag = reviewer.flag("g1", "multiple_answers", "B de olabilir bence", exam_id="e1")

    assert flag["kind_label"] == "Birden fazla doğru olabilir" and flag["status"] == "open"
    assert store.get_question("g1").metadata["flags"] == [{"flag_id": flag["flag_id"], "kind": "multiple_answers", "at": BASE.isoformat()}]
    assert reviewer.flags_for("g1")[0]["note"] == "B de olabilir bence" and reviewer.open_flags()[0]["flag_id"] == flag["flag_id"]
    with pytest.raises(ValueError):
        reviewer.flag("g1", "typo")
    with pytest.raises(ValueError):
        reviewer.flag("missing", "disputed_answer")
    assert set(FLAG_KINDS_TR) == {"disputed_answer", "multiple_answers", "source_mismatch", "figure_problem"}
    resolved = reviewer.resolve_flag(flag["flag_id"], "kept", "Anahtar doğru.")
    assert resolved["status"] == "resolved" and reviewer.open_flags() == []


def test_invalidating_a_question_keeps_the_attempt_and_corrects_the_mastery_it_moved() -> None:
    store = store_with_source()
    curriculum = Curriculum()
    learning = LearningEngine(store, curriculum, default_concept_graph(), clock=lambda: BASE)
    wrong = grounded_question("g1")
    other = grounded_question("g2")
    for question in (wrong, other):
        store.save_question(question)
    attempt = ExamAttempt("a1", "e1", started_at=BASE, finished_at=BASE, answers={"g1": QuestionAttempt("g1", "B", False), "g2": QuestionAttempt("g2", "A", True)})
    store.save_attempt(attempt)
    learning.record(wrong, False, chosen_key="B")
    learning.record(other, True, chosen_key="A")
    before = store.get_mastery("anatomy.humerus")
    assert before.attempts == 2 and before.correct == 1 and before.recent == [False, True]

    reviewer = reviewer_for(store)
    reviewer.flag("g1", "disputed_answer", "Anahtar yanlış.")
    invalidated = reviewer.invalidate("g1", "Anahtar yanlış: doğru cevap B.")
    corrected = learning.exclude_question(invalidated, reason="anahtar hatası")

    assert invalidated.metadata["invalidated"]["reason"] == "Anahtar yanlış: doğru cevap B." and "geçersiz" in invalidated.tags
    assert store.get_attempt("a1").answers["g1"].answer_key == "B", "the attempt is history and stays"
    after = corrected[0]
    assert after.attempts == 1 and after.correct == 1 and after.recent == [True] and after.streak == 1
    assert "Bir soru geçersiz sayıldı" in after.reason
    assert reviewer.flags_for("g1")[0]["status"] == "resolved" and reviewer.flags_for("g1")[0]["resolution"] == "invalidated"
    assert reviewer.status_of(store.get_question("g1"))["status"] == "invalidated"

    generator = make_generator(store, None, reviewer=None)
    picked = generator.from_bank(config(question_count=5, topic_ids=[ARM]), wrong_question_ids=["g1"])
    assert "g1" not in {item.question_id for item in picked} and "g2" in {item.question_id for item in picked}
