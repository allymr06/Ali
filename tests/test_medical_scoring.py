"""One scoring decision, applied everywhere the academy measures.

The user test of 13 September 2026 found a question the bank itself labelled
"puansız" counted as a correct answer, written into mastery and read as a
finding; blanks turned into weak concepts; a bank paper that ignored the
document and page filters; a histology answer filed under the nearest
concept name; and results that named the number asked for rather than the
number built. Each of those is pinned here against the source records.
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app.medical.academy import create_medical_academy
from app.medical.generation import GenerationError
from app.medical.jobs import DuplicateJob, JobLedger, JobTimeout, fingerprint
from app.medical.models import Exam, ExamAttempt, ExamConfig, Question, QuestionAttempt, QuestionOption, QuestionOrigin, SourceReference
from app.medical.questions import analyse_attempt
from app.medical.repair import REPAIR_KIND, repair_learning
from app.medical.review import SCORED_STATUSES, rule_decision
from app.medical.store import MedicalStore
from tests.test_medical_documents import make_pdf

ARM = "anatomy.musculoskeletal.upper_limb.arm"
BASE = datetime(2026, 9, 13, 9, 0, tzinfo=timezone.utc)
OPTIONS = ["Capitulum humeri", "Trochlea humeri", "Olecranon", "Acromion"]


def question(question_id: str, *, origin: str = QuestionOrigin.MANUAL, refs: bool = False, key: str | None = "A", support: dict | None = None, **overrides) -> Question:
    fields: dict = {
        "subject": "anatomy",
        "topic_id": ARM,
        "stem": f"Humerus sorusu {question_id}: distal uçta radius ile eklem yapan yapı?",
        "options": [QuestionOption(k, t) for k, t in zip("ABCD", OPTIONS)],
        "correct_key": key,
        "explanation": "Capitulum humeri radius başı ile eklemleşir.",
        "origin": origin,
        "concept_ids": [f"concept.{question_id}"],
        "metadata": {"concept_name": f"Kavram {question_id}"},
    }
    if refs:
        fields["references"] = [SourceReference("d1", 12, quote="capitulum", title="Üst ekstremite")]
    if support is not None:
        fields["metadata"]["support"] = support
    fields.update(overrides)
    return Question(question_id=question_id, **fields)


@pytest.fixture()
def academy(tmp_path):
    built = []

    def factory(gateway=None, *, review: bool = True):
        settings = SimpleNamespace(medical_directory=str(tmp_path / f"medical{len(built)}"), medical_source_review=review)
        instance = create_medical_academy(settings=settings, provider_gateway=gateway)
        built.append(instance)
        return instance

    yield factory
    for instance in built:
        instance.close()


# ---------------------------------------------------------------------------
# the decision
# ---------------------------------------------------------------------------


def test_the_rule_counts_keys_from_people_and_cited_sources_and_nothing_else() -> None:
    assert rule_decision(question("k", key=None))["status"] == "no_answer_key"
    assert rule_decision(question("i", origin=QuestionOrigin.IMPORTED_EXAM))["scored"] is True
    assert rule_decision(question("m", origin=QuestionOrigin.MANUAL))["scored"] is True
    unsourced = rule_decision(question("g", origin=QuestionOrigin.GENERATED))
    assert unsourced == {"scored": False, "status": "not_applicable", "label": "Kaynaksız (yalnız çalışma)", "reason": "Kaynak pasaj yok: çalışma içeriği, kaynak destekli ölçme değil."}
    assert rule_decision(question("c", origin=QuestionOrigin.LECTURE_DERIVED, refs=True))["status"] == "source_cited"
    for status in ("conflicting_evidence", "needs_review", "insufficient_evidence", "unresolved"):
        decided = rule_decision(question("r", origin=QuestionOrigin.LECTURE_DERIVED, refs=True, support={"status": status, "reason": "x"}))
        assert decided["scored"] is False and decided["status"] == status
    supported = rule_decision(question("s", origin=QuestionOrigin.LECTURE_DERIVED, refs=True, support={"status": "source_supported", "reason": "ok"}))
    assert supported["scored"] is True and "source_supported" in SCORED_STATUSES
    gone = rule_decision(question("v", origin=QuestionOrigin.IMPORTED_EXAM, metadata={"invalidated": {"reason": "anahtar yanlış"}}))
    assert gone["scored"] is False and gone["status"] == "invalidated" and gone["reason"] == "anahtar yanlış"


def test_the_reviewer_gate_follows_the_setting_but_never_scores_unsourced_or_invalid_items(academy) -> None:
    gated = academy(None, review=True)
    open_ = academy(None, review=False)
    unreviewed = question("u", origin=QuestionOrigin.LECTURE_DERIVED, refs=True)
    assert gated.scoring_of(unreviewed)["status"] == "needs_review" and gated.scoring_of(unreviewed)["scored"] is False
    assert open_.scoring_of(unreviewed)["status"] == "source_cited" and open_.scoring_of(unreviewed)["scored"] is True
    for instance in (gated, open_):
        assert instance.scoring_of(question("g", origin=QuestionOrigin.GENERATED))["scored"] is False
        assert instance.scoring_of(question("m"))["scored"] is True
        assert instance.scoring_of(question("k", key=None))["scored"] is False


# ---------------------------------------------------------------------------
# the paper
# ---------------------------------------------------------------------------


def paper(instance, *questions: Question, immediate: bool = False, **fields):
    for item in questions:
        instance.store.save_question(item)
    payload = asyncio.run(instance.generate_exam({"from_bank": True, "question_count": len(questions), "topic_ids": [ARM], "randomize": False, "immediate_feedback": immediate, "include_unscored": True, **fields}))
    return payload["exam_id"]


def test_an_unscored_question_is_shown_explained_and_kept_out_of_score_and_learning(academy) -> None:
    instance = academy(None)
    keyed = question("q1")
    unsourced = question("q2", origin=QuestionOrigin.GENERATED)
    exam_id = paper(instance, keyed, unsourced, immediate=True)
    opened = instance.start_exam(exam_id)
    decisions = {item["question_id"]: item["scoring"] for item in opened["questions"]}
    assert decisions["q1"]["scored"] is True and decisions["q2"]["scored"] is False and decisions["q2"]["label"] == "Kaynaksız (yalnız çalışma)"
    assert opened["scored_count"] == 1 and opened["question_count"] == 2

    first = instance.answer(exam_id, "q1", "A", confidence="sure")
    second = instance.answer(exam_id, "q2", "A", confidence="sure")
    assert first["scoring"]["scored"] and first["feedback"]["correct"] is True and first.get("event_id")
    assert second["scoring"]["scored"] is False and second["feedback"]["correct"] is True, "explained, like any study item"
    assert "event_id" not in second, "no understanding event for a study-only item"
    assert [item.concept_id for item in instance.learning.all()] == ["concept.q1"], "mastery moved for the scored question only"

    result = instance.finish_exam(exam_id)
    analysis = result["analysis"]
    assert (analysis["total"], analysis["correct"], analysis["percent"]) == (1, 1, 100)
    assert analysis["scored_question_ids"] == ["q1"]
    assert analysis["unscored"] == [{"question_id": "q2", "status": "not_applicable", "label": "Kaynaksız (yalnız çalışma)", "reason": "Kaynak pasaj yok: çalışma içeriği, kaynak destekli ölçme değil.", "answered": True, "correct": True}]
    assert analysis["unscored_answered"] == 1 and analysis["policy"] == "scoring-1"
    assert set(analysis["events"]) == {"q1"}
    assert [item.concept_id for item in instance.learning.all()] == ["concept.q1"]
    assert instance.finish_exam(exam_id)["analysis"] == analysis, "finishing again changes nothing"


def test_a_paper_of_nothing_but_study_items_has_no_score_rather_than_a_misleading_one(academy) -> None:
    instance = academy(None)
    exam_id = paper(instance, question("g1", origin=QuestionOrigin.GENERATED), question("g2", origin=QuestionOrigin.GENERATED))
    instance.start_exam(exam_id)
    instance.answer(exam_id, "g1", "A")
    instance.answer(exam_id, "g2", "B")
    analysis = instance.finish_exam(exam_id)["analysis"]
    assert analysis["percent"] is None and analysis["score"] is None and analysis["total"] == 0
    assert analysis["unscored_answered"] == 2 and analysis["weak_concepts"] == [] and analysis["strong_concepts"] == []
    assert instance.learning.all() == [] and instance.study.understanding.events() == []


def test_a_simulation_marked_at_the_end_applies_the_same_decision_once(academy) -> None:
    instance = academy(None)
    exam_id = paper(instance, question("q1"), question("g1", origin=QuestionOrigin.GENERATED))
    instance.start_exam(exam_id)
    instance.answer(exam_id, "q1", "B", confidence="sure")
    instance.answer(exam_id, "g1", "B", confidence="sure")
    assert instance.learning.all() == [], "a simulation records nothing until it is finished"
    analysis = instance.finish_exam(exam_id)["analysis"]
    assert (analysis["total"], analysis["incorrect"]) == (1, 1)
    assert [item.concept_id for item in instance.learning.all()] == ["concept.q1"]
    assert {event["question_id"] for event in instance.study.understanding.events()} == {"q1"}


def test_study_only_answers_do_not_move_the_adaptive_difficulty(academy) -> None:
    instance = academy(None)
    scored = [question(f"s{index}") for index in range(5)]
    study = [question(f"g{index}", origin=QuestionOrigin.GENERATED) for index in range(5)]
    exam_id = paper(instance, *scored, *study)
    session = instance.sessions.get()
    session.difficulty, session.adaptive_difficulty = 3, True
    instance.sessions.save(session)
    instance.start_exam(exam_id)
    for item in scored:
        instance.answer(exam_id, item.question_id, "B")  # wrong: the key is A
    for item in study:
        instance.answer(exam_id, item.question_id, "A")  # right, but nothing was measured

    analysis = instance.finish_exam(exam_id)["analysis"]

    assert (analysis["total"], analysis["correct"], analysis["unscored_answered"]) == (5, 0, 5)
    # Every measured answer was wrong. The five study-only answers sit last in
    # the paper, where the verdict looks: they must not read as a run of
    # successes and raise the level the next paper is built at.
    assert analysis["adaptive"] == {"previous": 3, "suggested": 2, "reason": "Son 5 sorunun yalnız 0'i doğru: zorluk bir kademe düştü, eksik kavram önce anlatılacak."}
    assert instance.sessions.get().difficulty == 2


def test_the_plan_reports_a_topic_covered_only_when_the_reviewer_scored_the_answer(academy) -> None:
    instance = academy(None, review=True)
    unreviewed = question("q1", origin=QuestionOrigin.LECTURE_DERIVED, refs=True)
    instance.store.save_question(unreviewed)
    instance.store.save_attempt(ExamAttempt("att", "e1", started_at=BASE, finished_at=BASE, answers={"q1": QuestionAttempt("q1", "A", True)}))
    planner = instance.study.planner
    record = planner.create("Komite", (planner.today() + timedelta(days=10)).isoformat(), topic_ids=[ARM], daily_minutes=45)

    row = next(item for item in planner.coverage(record["plan_id"])["topics"] if item["topic_id"] == ARM)

    # The source is cited but nobody checked it: with the gate on the paper
    # would not score this answer, so the plan may not call the topic measured.
    assert instance.scoring_of(unreviewed)["status"] == "needs_review"
    assert row["attempts"] == 0 and row["flags"]["assessed"] is False and row["state"] != "assessed_limited"


def test_a_finished_paper_keeps_the_decision_of_its_day(academy) -> None:
    instance = academy(None)
    exam_id = paper(instance, question("q1"), question("q2"))
    instance.start_exam(exam_id)
    instance.answer(exam_id, "q1", "A")
    instance.answer(exam_id, "q2", "A")
    finished = instance.finish_exam(exam_id)
    assert finished["analysis"]["percent"] == 100
    # The question is later found wrong: the invalidation corrects the learning
    # record, and the paper's stored decision still says what it counted then.
    instance.study.invalidate_question("q2", "Anahtar yanlış.")
    later = instance.exam(exam_id)
    assert later["analysis"]["percent"] == 100 and later["analysis"]["scoring"]["q2"]["scored"] is True
    assert next(item for item in later["questions"] if item["question_id"] == "q2")["scoring"]["status"] == "imported"
    assert instance.scoring_of(instance.store.get_question("q2"))["status"] == "invalidated", "a new paper would not count it"


# ---------------------------------------------------------------------------
# the analysis: blanks are blanks
# ---------------------------------------------------------------------------


def test_a_blank_is_not_a_wrong_answer_and_a_concept_needs_an_answer_to_be_weak() -> None:
    questions = [question("q1", concept_ids=["c.a"]), question("q2", concept_ids=["c.b"]), question("q3", concept_ids=["c.c"])]
    exam = Exam("e1", "Deneme", ExamConfig(), [item.question_id for item in questions])
    attempt = ExamAttempt("a1", "e1", started_at=BASE, finished_at=BASE + timedelta(minutes=3))
    attempt.answers = {"q1": QuestionAttempt("q1", "A", True)}
    report = analyse_attempt(exam, questions, attempt)
    assert (report["correct"], report["incorrect"], report["unanswered"], report["percent"]) == (1, 0, 2, 33)
    assert report["weak_concepts"] == [], "two blanks are not two weak concepts"
    assert [item["concept_id"] for item in report["unassessed_concepts"]] == ["c.b", "c.c"]
    assert report["suggestion"]["kind"] == "answer_blanks"

    attempt.answers["q2"] = QuestionAttempt("q2", "B", False)
    report = analyse_attempt(exam, questions, attempt)
    assert [(item["concept_id"], item["correct"], item["total"], item["wrong"]) for item in report["weak_concepts"]] == [("c.b", 0, 1, 1)]


def test_the_analysis_applies_a_scoring_map_and_reports_what_it_left_out() -> None:
    questions = [question("q1"), question("q2"), question("q3", key=None)]
    exam = Exam("e1", "Deneme", ExamConfig(), ["q1", "q2", "q3"])
    attempt = ExamAttempt("a1", "e1", started_at=BASE)
    attempt.answers = {"q1": QuestionAttempt("q1", "A", True), "q2": QuestionAttempt("q2", "A", True)}
    scoring = {"q1": {"scored": True, "status": "imported", "label": "", "reason": ""}, "q2": {"scored": False, "status": "conflicting_evidence", "label": "Çelişkili kanıt", "reason": "Pasaj anahtarı desteklemiyor."}}
    report = analyse_attempt(exam, questions, attempt, scoring=scoring)
    assert (report["total"], report["correct"], report["percent"], report["ungradable"]) == (1, 1, 100, 1)
    assert report["unscored"][0]["question_id"] == "q2" and report["unscored"][0]["label"] == "Çelişkili kanıt"
    assert report["scored_question_ids"] == ["q1"]


# ---------------------------------------------------------------------------
# the bank: filters mean what they say
# ---------------------------------------------------------------------------


def test_bank_selection_honours_document_page_professor_and_figure_filters(academy) -> None:
    instance = academy(None)
    store = instance.store
    from app.medical.models import StudyDocument

    store.save_document(StudyDocument(document_id="epitel", title="Histoloji 3 - Epitel Doku", file_name="e.pdf", sha256="x", page_count=60, subject="histology"))
    cited = question("h1", origin=QuestionOrigin.IMPORTED_EXAM, subject="histology", topic_id=None, references=[SourceReference("epitel", 34, title="Histoloji 3 - Epitel Doku")])
    elsewhere = question("h2", origin=QuestionOrigin.IMPORTED_EXAM, subject="histology", topic_id=None, references=[SourceReference("epitel", 3, title="Histoloji 3 - Epitel Doku")])
    unrelated = question("b1", origin=QuestionOrigin.IMPORTED_EXAM, subject="biochemistry", topic_id="biochemistry.carbohydrate_metabolism")
    professors = question("h3", origin=QuestionOrigin.IMPORTED_EXAM, subject="histology", topic_id=None, professor_id="prof-1")
    figure = question("h4", origin=QuestionOrigin.IMPORTED_EXAM, subject="histology", topic_id=None, image_ref="epitel|34")
    for item in (cited, elsewhere, unrelated, professors, figure):
        store.save_question(item)
    generator = instance.generator

    chosen = generator.from_bank(ExamConfig(subjects=["histology"], document_ids=["epitel"], page_from=34, page_to=34, question_count=5, include_images=True))
    assert {item.question_id for item in chosen} == {"h1", "h4"}, "only what cites page 34 of that document"
    assert generator.last_bank_report["filters"] == ["Histoloji 3 - Epitel Doku s. 34-34"]
    assert generator.last_bank_report["filtered_out"] == 2

    chosen = generator.from_bank(ExamConfig(subjects=["histology"], professor_id="prof-1", question_count=5))
    assert [item.question_id for item in chosen] == ["h3"]

    chosen = generator.from_bank(ExamConfig(subjects=["histology"], include_images=False, question_count=5))
    assert "h4" not in {item.question_id for item in chosen} and len(chosen) == 3

    with pytest.raises(GenerationError, match="süzgece uymuyor"):
        asyncio.run(instance.generate_exam({"from_bank": True, "subjects": ["biochemistry"], "document_ids": ["epitel"], "page_from": 34, "page_to": 34, "question_count": 1}))


def test_bank_selection_excludes_study_items_unless_asked_and_says_why_it_is_empty(academy) -> None:
    instance = academy(None)
    instance.store.save_question(question("g1", origin=QuestionOrigin.GENERATED))
    instance.store.save_question(question("g2", origin=QuestionOrigin.GENERATED))
    assert instance.generator.from_bank(ExamConfig(topic_ids=[ARM], question_count=2)) == []
    assert instance.generator.last_bank_report["unscored"] == 2
    with pytest.raises(GenerationError, match="puansız soru var"):
        asyncio.run(instance.generate_exam({"from_bank": True, "topic_ids": [ARM], "question_count": 2}))
    payload = asyncio.run(instance.generate_exam({"from_bank": True, "topic_ids": [ARM], "question_count": 2, "include_unscored": True}))
    assert payload["question_count"] == 2 and payload["scored_count"] == 0
    assert any("yalnız çalışma içindir" in note for note in payload["notes"])
    assert any("şık sayısı ve bilgi önceliği uygulanmaz" in note for note in payload["notes"])
    with pytest.raises(GenerationError, match="anahtarlı soru yok"):
        asyncio.run(instance.generate_exam({"from_bank": True, "subjects": ["physiology"], "question_count": 2}))


def test_a_paper_is_named_and_listed_for_the_questions_it_holds(academy) -> None:
    instance = academy(None)
    instance.store.save_question(question("q1"))
    payload = asyncio.run(instance.generate_exam({"from_bank": True, "topic_ids": [ARM], "question_count": 10}))
    assert payload["title"].endswith("· 1 soru") and payload["question_count"] == 1 and payload["requested_count"] == 10
    assert "10 soru istendi, 1 soru hazırlandı." in payload["notes"]
    assert payload["status_label"] == "Hazır"
    listed = instance.exams()[0]
    assert (listed["question_count"], listed["requested_count"], listed["scored_count"]) == (1, 10, 1)


def test_the_bank_view_pages_through_a_stable_order_with_the_matched_total(academy) -> None:
    instance = academy(None)
    for index in range(7):
        instance.store.save_question(question(f"q{index}"))
    first = instance.question_bank({"limit": 3})
    second = instance.question_bank({"limit": 3, "offset": 3})
    third = instance.question_bank({"limit": 3, "offset": 6})
    ids = [item["question_id"] for page in (first, second, third) for item in page["questions"]]
    assert len(ids) == 7 and len(set(ids)) == 7, "no gap, no repeat"
    assert first["matched"] == 7 and first["has_more"] is True and third["has_more"] is False
    assert first["counts"]["total"] == 7 and first["questions"][0]["scoring"]["scored"] is True
    narrowed = instance.question_bank({"text": "q6", "limit": 3})
    assert narrowed["matched"] == 1 and narrowed["counts"]["total"] == 7


# ---------------------------------------------------------------------------
# histology: exact concepts, hidden answers, printed answers
# ---------------------------------------------------------------------------


def test_a_histology_answer_is_filed_under_the_exact_concept_or_its_own(academy) -> None:
    instance = academy(None)
    histology = instance.study.histology
    graph = instance.concepts
    squamous = next(concept for concept in graph.by_subject("histology") if "yassı" in concept.name.lower() and "tek katlı" in concept.name.lower())
    assert histology._concept_for("Tek katlı yassı epitel") == squamous.concept_id
    assert histology._concept_for("TEK KATLI YASSI EPİTEL") == squamous.concept_id
    cuboid = histology._concept_for("Tek katlı kübik epitel")
    assert cuboid != squamous.concept_id, "three shared words are not the same tissue"
    assert cuboid == histology._concept_for("tek katlı kübik epitel"), "and the unmatched answer keeps one stable id"


def specimen_setup(instance, tmp_path, *, caption_inside: bool):
    pipeline = instance.pipeline
    source = tmp_path / "histoloji.pdf"
    source.write_bytes(make_pdf([("Tek katli kubik epitel, HE, 400x", True)]))
    document, _created = pipeline.import_file(source)
    pipeline.process(document.document_id)
    region = {"x": 0.05, "y": 0.02, "w": 0.9, "h": 0.6} if caption_inside else {"x": 0.05, "y": 0.3, "w": 0.9, "h": 0.6}
    return document.document_id, region


def test_the_side_list_and_the_detail_hide_a_specimen_a_timed_session_still_asks_about(academy, tmp_path) -> None:
    instance = academy(None)
    histology, study = instance.study.histology, instance.study
    document_id, region = specimen_setup(instance, tmp_path, caption_inside=False)
    specimen = histology.add_specimen(document_id, 1, region, label="Tek katlı kübik epitel", basis="page_caption")
    assert specimen["status"] == "eligible" and specimen["answer_visible"] is False
    session = histology.start_session(mode="timed", seconds=30, count=1)
    histology.show(session["session_id"], 0)

    overview = study.call("histology_overview", {})
    row = overview["specimens"][0]
    assert row["masked"] is True and "label" not in row and row["document_title"] == "" and row["page_number"] is None
    assert overview["under_test"] == [specimen["specimen_id"]]
    detail = study.call("histology_specimen", {"specimen_id": specimen["specimen_id"]})["specimen"]
    assert detail["masked"] is True and "label" not in detail and "features" not in detail and "caption_excerpt" not in detail

    asyncio.run(histology.answer(session["session_id"], specimen["specimen_id"], "tek katlı kübik epitel"))
    shown = study.call("histology_specimen", {"specimen_id": specimen["specimen_id"]})["specimen"]
    assert shown["masked"] is False and shown["label"] == "Tek katlı kübik epitel" and shown["document_title"]
    event = instance.study.understanding.events()[0]
    assert event["concept_ids"] == [shown["concept_id"]] and shown["concept_id"] != "histology.simple_squamous"


def test_a_name_printed_inside_the_crop_keeps_the_specimen_out_of_the_blind_test_until_it_is_masked(academy, tmp_path) -> None:
    instance = academy(None)
    histology = instance.study.histology
    document_id, region = specimen_setup(instance, tmp_path, caption_inside=True)
    specimen = histology.add_specimen(document_id, 1, region, label="Tek katlı kübik epitel", basis="page_caption")
    assert specimen["answer_visible"] is True and specimen["status"] == "study_only"
    assert specimen["status_reason"].startswith("Cevap görselin üzerinde yazıyor")
    assert histology.start_session(mode="timed", count=1) is None, "not offered as a blind test"

    plain = histology.crop(specimen["specimen_id"])
    hidden = histology.hide_printed_answer(specimen["specimen_id"])
    assert hidden["masks"] and hidden["status"] == "eligible" and hidden["hidden_answer_masks"] >= 1
    assert histology.crop(specimen["specimen_id"]) == plain, "the plain crop is the page as printed"
    assert histology.crop(specimen["specimen_id"], masked=True) != plain, "the practical shows the masked one"
    session = histology.start_session(mode="timed", count=1)
    assert session is not None
    crop = instance.study.call("histology_crop", {"specimen_id": specimen["specimen_id"]})
    assert crop["masked"] is True, "under test the masked rendering is served whatever the page asked for"


def test_a_late_answer_to_a_timed_item_is_recorded_as_late(academy, tmp_path) -> None:
    instance = academy(None)
    histology = instance.study.histology
    document_id, region = specimen_setup(instance, tmp_path, caption_inside=False)
    specimen = histology.add_specimen(document_id, 1, region, label="Tek katlı kübik epitel", basis="user_confirmed")
    session = histology.start_session(mode="timed", seconds=15, count=1)
    histology.show(session["session_id"], 0)
    histology._clock = lambda: datetime.now(timezone.utc) + timedelta(seconds=40)
    late = asyncio.run(histology.answer(session["session_id"], specimen["specimen_id"], "tek katlı kübik epitel"))
    answer = late["items"][0]["answer"]
    assert answer["timed_out"] is True and answer["correct"] is False and answer["given"] == "tek katlı kübik epitel"
    again = asyncio.run(histology.answer(session["session_id"], specimen["specimen_id"], "tek katlı kübik epitel"))
    assert again["items"][0]["answer"] == answer, "one result per item"


# ---------------------------------------------------------------------------
# the check waits for its assessment; notes follow their document; readings know their sources
# ---------------------------------------------------------------------------


def test_an_understanding_check_reports_a_pending_assessment_not_a_verdict(academy) -> None:
    instance = academy(None)
    instance.store.save_question(question("q1"))
    check = instance.study.call("understanding_check_start", {"topic_id": ARM})["check"]
    answered = instance.study.call("understanding_check_answer", {"check_id": check["check_id"], "answer_key": "A", "confidence": "sure", "reasoning": "Capitulum yuvarlaktır ve radius başı ile eklemleşir."})["check"]
    result = answered["result"]
    assert result["correct"] is True and result["assessment_status"] == "pending"
    assert result["classification"] == "correct_unsupported", "the classification of the moment, labelled as pending"
    fetched = instance.study.call("understanding_check", {"check_id": check["check_id"]})["check"]
    assert fetched["result"]["assessment_status"] == "pending"
    event = instance.study.understanding.event(result["event_id"])
    event["assessment"] = {**event["assessment"], "status": "done", "verdict": "supported"}
    event["classification"] = "correct_supported"
    instance.store.save_record("understanding_event", event["event_id"], event, subject_key=event["concept_ids"][0])
    fetched = instance.study.call("understanding_check", {"check_id": check["check_id"]})["check"]
    assert fetched["result"]["assessment_status"] == "done" and fetched["result"]["classification_label"] == "Doğru cevap, gerekçe destekliyor"


def test_a_note_asked_from_another_subjects_document_follows_the_document(academy) -> None:
    instance = academy(None)
    from app.medical.models import StudyDocument

    instance.store.save_document(StudyDocument(document_id="epitel", title="Histoloji 3 - Epitel Doku", file_name="e.pdf", sha256="x", page_count=60, subject="histology", topic_ids=["histology.epithelium"]))
    context = instance.note_context(subject="biochemistry", topic_id="biochemistry.carbohydrate_metabolism", document_ids=["epitel"])
    assert context["switched"] is True and context["subject"] == "histology"
    assert context["requested_subject_label"] == "Biyokimya" and "bağlam belgeye göre ayarlandı" in context["note"]
    same = instance.note_context(subject="histology", topic_id=None, document_ids=["epitel"])
    assert same["switched"] is False and same["subject"] == "histology"


def test_notes_are_refused_when_the_model_says_the_pages_do_not_cover_the_topic(academy) -> None:
    class Gateway:
        async def generate(self, request, context, **kwargs):
            return SimpleNamespace(text=json.dumps({"title": "Karbonhidrat", "markdown": "Bu kaynak epitel dokuyu anlatıyor; karbonhidrat metabolizması yok.", "source_covers_topic": False, "cited_pages": [1]}))

    instance = academy(Gateway())
    source = instance.pipeline.import_file  # the pipeline needs a real page of text
    from tests.test_medical_documents import make_pdf as pdf

    path = instance.pipeline.directory / "epitel.pdf"
    path.write_bytes(pdf([("Tek katli kubik epitel bez kanallarinda bulunur", False)]))
    document, _ = source(path)
    instance.pipeline.process(document.document_id)
    with pytest.raises(GenerationError, match="konusunu içermiyor"):
        asyncio.run(instance.generate_notes(mode="medical.short_notes", subject="biochemistry", topic_id="biochemistry.carbohydrate_metabolism", document_ids=[document.document_id]))
    assert instance.store.list_notes() == []


def test_a_reading_activity_names_its_sources_in_order_and_admits_when_it_has_none(academy) -> None:
    instance = academy(None)
    from app.medical.models import StudyDocument

    planner = instance.study.planner
    instance.store.save_document(StudyDocument(document_id="planes", title="Anatomi 1 - Düzlemler", file_name="p.pdf", sha256="a", page_count=30, subject="anatomy", topic_ids=["anatomy.general.planes_axes"], status="ready"))
    instance.store.save_document(StudyDocument(document_id="other", title="Anatomi 2 - Kemik terimleri", file_name="k.pdf", sha256="b", page_count=30, subject="anatomy", topic_ids=["anatomy.general.bone_terms"], status="ready"))
    record = planner.create("Komite 1", (planner.today() + timedelta(days=14)).isoformat(), subjects=["anatomy"], topic_ids=["anatomy.general.planes_axes", "anatomy.general.bone_terms"], document_ids=["planes"], page_ranges={"planes": [[3, 9]]}, daily_minutes=60)
    planner.confirm_scope(record["plan_id"], subjects=["anatomy"], topic_ids=["anatomy.general.planes_axes", "anatomy.general.bone_terms"], document_ids=["planes"], page_ranges={"planes": [[3, 9]]})
    planner.replan(record["plan_id"])
    activities = planner.activities(record["plan_id"])
    reading = next(item for item in activities if item["kind"] == "read" and item["topic_id"] == "anatomy.general.planes_axes")
    sources = planner.activity_sources(reading["activity_id"])
    assert sources["documents"][0] == {"document_id": "planes", "title": "Anatomi 1 - Düzlemler", "page_count": 30, "page_from": 3, "page_to": 9, "reason": "plan kapsamında"}
    assert sources["search"] and sources["topic_label"]
    bones = next(item for item in activities if item["kind"] == "read" and item["topic_id"] == "anatomy.general.bone_terms")
    assert [item["reason"] for item in planner.activity_sources(bones["activity_id"])["documents"]] == ["konusu eşleşiyor"]
    manual = planner.add_manual(record["plan_id"], day=planner.today().isoformat(), title="Serbest okuma", minutes=10)
    free = planner.activity_sources(manual["activity_id"])
    assert [item["reason"] for item in free["documents"]] == ["plan kapsamında"] and free["topic_id"] is None and free["search"] == "Serbest okuma"
    # Adding the manual entry replanned the day, so the reading is fetched afresh.
    reading = next(item for item in planner.activities(record["plan_id"]) if item["kind"] == "read" and item["topic_id"] == "anatomy.general.planes_axes")
    started = instance.study.call("plan_activity_start", {"activity_id": reading["activity_id"]})
    assert started["sources"]["documents"][0]["document_id"] == "planes"


# ---------------------------------------------------------------------------
# the lab quiz says "pinned" only about pins that exist
# ---------------------------------------------------------------------------


def test_the_lab_quiz_describes_a_landmark_the_model_cannot_point_at(academy) -> None:
    instance = academy(None)
    lab = instance.anatomy
    scapula = lab.get("scapula")
    assert scapula is not None and len(scapula.landmarks) >= 3
    assert lab.pinned_landmarks("scapula") == set(), "no asset directory, no pins"
    items = lab.quiz("scapula", count=3, seed="x", pinned=set())
    assert items and all(item["kind"] == "landmark_describe" and item["highlight"] is None and item["pinned"] is False for item in items if item.get("landmark_id"))
    assert all("işaretlenen" not in item["stem"] for item in items)
    one = scapula.landmarks[0].landmark_id
    pinned = lab.quiz("scapula", count=10, seed="x", pinned={one})
    kinds = {item["landmark_id"]: item["kind"] for item in pinned if item.get("landmark_id")}
    assert kinds.get(one) in (None, "landmark_identify") and all(kind == "landmark_describe" for landmark_id, kind in kinds.items() if landmark_id != one)
    assert lab.concept_label(f"anatomy.scapula.{one}") == f"Scapula · {scapula.landmarks[0].latin}"
    assert lab.concept_label("anatomy.scapula") == "Scapula" and lab.concept_label("anatomy.nothing.here") == ""
    # A concept the graph knows keeps the graph's name; one it does not (an atlas
    # structure's landmark) is named by the lab instead of shown as a raw id.
    assert instance.learning.concept_name(f"anatomy.scapula.{one}") == scapula.landmarks[0].latin
    from dataclasses import replace as dc_replace

    lab._structures["za_test"] = dc_replace(scapula, structure_id="za_test", canonical="Scapula · sağ")
    assert instance.learning.concept_name(f"anatomy.za_test.{one}") == f"Scapula · sağ · {scapula.landmarks[0].latin}"
    assert instance.learning.concept_name("anatomy.za_missing.x") == "anatomy.za_missing.x", "no name is invented"


# ---------------------------------------------------------------------------
# jobs: identity, one at a time, a truthful end
# ---------------------------------------------------------------------------


def test_the_job_ledger_names_duplicates_timeouts_and_interruptions(tmp_path) -> None:
    store = MedicalStore(tmp_path / "m.sqlite3")
    events: list[dict] = []
    ledger = JobLedger(store, emit=events.append)
    request = {"topic_ids": [ARM], "question_count": 1}

    async def slow() -> dict:
        await asyncio.sleep(0.05)
        return {"exam_id": "e1", "questions": [1]}

    async def both() -> tuple:
        first = asyncio.create_task(ledger.run("create_exam", request, slow, title="Kol"))
        await asyncio.sleep(0.01)
        with pytest.raises(DuplicateJob) as raised:
            await ledger.run("create_exam", request, slow)
        return await first, raised.value.job

    outcome, duplicate = asyncio.run(both())
    assert outcome["job_id"] and duplicate["job_id"] == outcome["job_id"]
    done = ledger.get(outcome["job_id"])
    assert done["status"] == "done" and done["kind_label"] == "Sınav hazırlama" and done["request"] == request
    assert [event["job"]["status"] for event in events] == ["running", "done"]

    async def broken() -> dict:
        raise GenerationError("Soru bankasında bu ölçütlere uyan soru yok.")

    with pytest.raises(GenerationError):
        asyncio.run(ledger.run("create_exam", {"topic_ids": ["x"]}, broken))
    failed = ledger.recent()[0]
    assert failed["status"] == "failed" and failed["error"] == "Soru bankasında bu ölçütlere uyan soru yok." and failed["status_label"] == "Başarısız"

    from app.medical import jobs as jobs_module

    jobs_module.TIMEOUTS_SECONDS["create_note"] = 0.02

    async def stuck() -> dict:
        await asyncio.sleep(1)
        return {}

    with pytest.raises(JobTimeout):
        asyncio.run(ledger.run("create_note", {"mode": "x"}, stuck))
    assert ledger.recent()[0]["status"] == "timeout"
    jobs_module.TIMEOUTS_SECONDS["create_note"] = 240.0

    # A job still "running" when the process died is reported as interrupted by the next ledger.
    orphan, _created = ledger.start("create_exam", {"topic_ids": ["orphan"]})
    assert JobLedger(store).get(orphan["job_id"])["status"] == "interrupted"
    assert fingerprint("create_exam", request) == fingerprint("create_exam", dict(request))
    store.close()


def test_the_academy_runs_a_paper_as_a_job_and_answers_a_second_click_with_the_first(academy) -> None:
    instance = academy(None)
    instance.store.save_question(question("q1"))
    fields = {"from_bank": True, "topic_ids": [ARM], "question_count": 1}
    payload = asyncio.run(instance.generate_exam_job(fields))
    job = instance.jobs.get(payload["job_id"])
    assert job["status"] == "done" and job["result"]["exam_id"] == payload["exam_id"] and job["result"]["count"] == 1
    assert instance.running_job("create_exam", fields) is None
    with pytest.raises(GenerationError):
        asyncio.run(instance.generate_exam_job({"from_bank": True, "subjects": ["physiology"], "question_count": 1}))
    assert instance.jobs.recent()[0]["status"] == "failed" and "anahtarlı soru yok" in instance.jobs.recent()[0]["error"]
    listed = instance.study.call("jobs", {})["jobs"]
    assert [item["status"] for item in listed[:2]] == ["failed", "done"]


# ---------------------------------------------------------------------------
# the repair: summaries back in line with the records, once
# ---------------------------------------------------------------------------


def test_the_repair_removes_unscored_evidence_relinks_histology_and_recomputes_results_once(academy, tmp_path) -> None:
    instance = academy(None)
    store, learning, understanding, histology = instance.store, instance.learning, instance.study.understanding, instance.study.histology
    keyed = question("q1")
    unsourced = question("q2", origin=QuestionOrigin.GENERATED)
    # The old code's mistake, replayed: a finished paper whose unsourced item was scored and learned.
    for item in (keyed, unsourced):
        store.save_question(item)
    exam = instance.exam_builder.build(ExamConfig(topic_ids=[ARM], question_count=2, randomize=False), [keyed, unsourced])
    from app.medical.questions import new_attempt, record_answer

    attempt = new_attempt(exam)
    record_answer(attempt, keyed, "A")
    record_answer(attempt, unsourced, "A")
    attempt.finished_at = datetime.now(timezone.utc)
    attempt.analysis = analyse_attempt(exam, [keyed, unsourced], attempt)
    attempt.score = attempt.analysis["score"]
    store.save_attempt(attempt)
    for item in (keyed, unsourced):
        learning.record(item, True, chosen_key="A")
        understanding.record_event(item, correct=True, answer_key="A", source="exam", exam_id=exam.exam_id, attempt_id=attempt.attempt_id, submission_id=f"{attempt.attempt_id}:{item.question_id}")
    assert attempt.analysis["percent"] == 100 and attempt.analysis["total"] == 2
    assert {item.concept_id for item in learning.all()} == {"concept.q1", "concept.q2"}

    # And a histology answer filed under the nearest name.
    document_id, region = specimen_setup(instance, tmp_path, caption_inside=False)
    specimen = histology.add_specimen(document_id, 1, region, label="Tek katlı kübik epitel", basis="user_confirmed")
    wrong_concept = next(concept.concept_id for concept in instance.concepts.by_subject("histology") if "yassı" in concept.name.lower() and "tek katlı" in concept.name.lower())
    misfiled = Question(question_id=f"histology-{specimen['specimen_id']}", subject="histology", stem="Histoloji örneği", options=[], correct_key=None, concept_ids=[wrong_concept])
    learning.record(misfiled, True)
    understanding.record_event(misfiled, correct=True, answer_key="Tek katlı kübik epitel", source="histology", submission_id="s:1")
    assert store.get_mastery(wrong_concept).attempts == 1

    report = repair_learning(instance, backup=True, backup_directory=tmp_path / "backups")

    assert report["backup"] and (tmp_path / "backups").exists()
    assert [item["question_id"] for item in report["unscored_questions"]] == ["q2"]
    assert store.get_mastery("concept.q2") is None or store.get_mastery("concept.q2").attempts == 0
    assert store.get_mastery("concept.q1").attempts == 1
    excluded = next(event for event in understanding.events() if event["question_id"] == "q2")
    assert excluded["excluded"] is True and "Puansız" in excluded["exclusion_reason"]
    assert len(report["histology_relinked"]) == 1 and report["histology_relinked"][0]["from"] == wrong_concept
    assert store.get_mastery(wrong_concept) is None, "the squamous row it never earned is gone"
    moved = store.get_mastery(specimen["concept_id"] or histology._concept_for("Tek katlı kübik epitel"))
    assert moved is not None and moved.attempts == 1 and moved.correct == 1
    assert store.list_records(REPAIR_KIND, limit=10)
    fixed = store.latest_attempt(exam.exam_id)
    assert fixed.analysis["total"] == 1 and fixed.analysis["percent"] == 100 and fixed.analysis["unscored"][0]["question_id"] == "q2"
    assert fixed.analysis["policy"] == "scoring-1" and fixed.answers["q2"].answer_key == "A", "the answer itself is kept"

    again = repair_learning(instance, backup=False)
    assert again["unscored_questions"] == [] and again["histology_relinked"] == [] and again["already_applied"] == 2
    assert store.get_mastery("concept.q1").attempts == 1 and moved.attempts == 1


# ---------------------------------------------------------------------------
# the committee rehearsal: the real papers, in the real shape
# ---------------------------------------------------------------------------


def test_a_committee_rehearsal_keeps_the_distribution_the_order_and_its_honesty(academy) -> None:
    instance = academy(None)
    store = instance.store
    for index in range(4):
        store.save_question(question(f"a{index}", origin=QuestionOrigin.IMPORTED_EXAM, subject="anatomy"))
    for index in range(2):
        store.save_question(question(f"p{index}", origin=QuestionOrigin.IMPORTED_EXAM, subject="physiology", topic_id=None))
    store.save_question(question("gen", origin=QuestionOrigin.GENERATED, subject="anatomy"))
    store.save_question(question("nokey", origin=QuestionOrigin.IMPORTED_EXAM, subject="anatomy", key=None))

    options = instance.committee_options()
    rows = {row["subject"]: row for row in options["subjects"]}
    assert rows["anatomy"]["available"] == 4, "only keyed imported questions the policy scores"
    assert rows["physiology"]["available"] == 2 and options["seconds_per_question"] == 72

    exam = instance.committee_exam({"anatomy": 3, "physiology": 5}, seed="prova")
    assert exam["title"] == "Komite provası · 5 soru" and exam["question_count"] == 5
    assert exam["config"]["timed_seconds"] == 5 * 72 and exam["mode"] == "simulation"
    subjects = [item["subject"] for item in exam["questions"]]
    assert subjects == ["anatomy"] * 3 + ["physiology"] * 2, "grouped by subject, never padded across"
    ids = {item["question_id"] for item in exam["questions"]}
    assert "gen" not in ids and "nokey" not in ids
    assert any("Fizyoloji: 5 istendi, 2 bulundu" in note for note in exam["notes"])
    # A fresh exam id each call; the selection itself is seed-stable.
    again = instance.committee_exam({"anatomy": 3, "physiology": 5}, seed="prova")
    assert [item["question_id"] for item in again["questions"]] == [item["question_id"] for item in exam["questions"]]

    # Finishing scores per subject like the real committee report.
    instance.start_exam(exam["exam_id"])
    for item in exam["questions"][:3]:
        instance.answer(exam["exam_id"], item["question_id"], "A")
    analysis = instance.finish_exam(exam["exam_id"])["analysis"]
    by_subject = {row["key"]: row for row in analysis["by_subject"]}
    assert by_subject["anatomy"]["correct"] == 3 and by_subject["physiology"]["total"] == 2

    unseen = instance.committee_exam({"anatomy": 4}, unseen_only=True, seed="prova2")
    answered_ids = {item["question_id"] for item in exam["questions"] if item["subject"] == "anatomy"}
    unseen_ids = {item["question_id"] for item in unseen["questions"]}
    assert unseen_ids == {f"a{index}" for index in range(4)} - answered_ids, "only questions never answered"
    assert any("çözülmemiş" in note for note in unseen["notes"])

    with pytest.raises(GenerationError, match="Bilinmeyen ders"):
        instance.committee_exam({"astroloji": 3})
    with pytest.raises(GenerationError, match="komite sorusu yok"):
        instance.committee_exam({"histology": 5})
    with pytest.raises(GenerationError, match="En az bir ders"):
        instance.committee_exam({"anatomy": 0})


def test_the_weekly_report_counts_only_what_the_records_hold(academy) -> None:
    instance = academy(None)
    study = instance.study

    empty = study.call("weekly_report", {})
    assert empty["empty"] is True and empty["streak_days"] == 0 and len(empty["days"]) == 7
    assert empty["totals"]["accuracy"] is None, "no scored answers, no percentage"

    # A day of work: a plan with a countdown, logged minutes, a finished
    # paper with one unscored answer, and two card reviews.
    from datetime import timedelta

    exam_date = (study.planner.today() + timedelta(days=10)).isoformat()
    plan = study.planner.create("Komite 2", exam_date, subjects=["anatomy"], daily_minutes=45)
    study.planner.confirm_scope(plan["plan_id"], subjects=["anatomy"], topic_ids=[ARM], document_ids=[], page_ranges={})
    study.planner.replan(plan["plan_id"])
    activity = next(item for item in study.planner.activities(plan["plan_id"]) if item["date"] == study.planner.today().isoformat())
    study.planner.start(activity["activity_id"])
    study.planner.complete(activity["activity_id"], minutes=25)
    study.planner.log_study(topic_id=ARM, activity="read", minutes=15)

    keyed = question("wk1")
    unsourced = question("wk2", origin=QuestionOrigin.GENERATED)
    for item in (keyed, unsourced):
        instance.store.save_question(item)
    paper = asyncio.run(instance.generate_exam({"from_bank": True, "question_count": 2, "topic_ids": [ARM], "randomize": False, "include_unscored": True}))
    instance.start_exam(paper["exam_id"])
    instance.answer(paper["exam_id"], "wk1", "A")
    instance.answer(paper["exam_id"], "wk2", "A")
    instance.finish_exam(paper["exam_id"])

    study.call("cards_build_topic", {"topic_id": ARM})
    for card in study.call("cards_queue", {"limit": 2})["cards"]:
        study.call("cards_answer", {"card_id": card["card_id"], "grade": "good"})

    report = study.call("weekly_report", {})
    totals = report["totals"]
    assert report["empty"] is False and report["streak_days"] == 1
    today = report["days"][-1]
    assert today["minutes"] == 40, "25 from the completed activity + 15 from the log"
    assert today["answers"] == 2 and today["cards"] == 2 and today["activities_done"] == 1
    assert (totals["papers"], totals["scored"], totals["correct"], totals["accuracy"]) == (1, 1, 1, 1.0)
    assert totals["unscored_answered"] == 1, "the study-only answer is counted apart, never in accuracy"
    assert totals["card_grades"] == {"good": 2}
    assert totals["activities"]["completed"] == 1 and totals["activities"]["planned"] >= 1
    assert report["countdowns"][0]["name"] == "Komite 2" and report["countdowns"][0]["days_left"] == 10
    assert "kayıtlardan" in report["note"]


# ---------------------------------------------------------------------------
# safety copies of the store
# ---------------------------------------------------------------------------


def test_backups_rotate_spare_repair_snapshots_and_refuse_a_full_disk(academy, tmp_path, monkeypatch) -> None:
    import shutil as _shutil
    import sqlite3 as _sqlite3

    from app.medical import repair as repair_module
    from app.medical.repair import auto_backup_due, backup_now, list_backups

    instance = academy(None)
    instance.store.save_question(question("b1"))
    assert auto_backup_due(instance.store) is True, "no copy yet, one is due"

    reports = [backup_now(instance.store, keep=2) for _index in range(3)]
    rows = list_backups(instance.store)
    assert [row["kind"] for row in rows].count("backup") == 2, "the rotation keeps two"
    assert reports[-1]["removed"], "the oldest was removed"
    copy = _sqlite3.connect(reports[-1]["path"])
    try:
        assert copy.execute("SELECT COUNT(*) FROM questions").fetchone()[0] >= 1, "the copy opens and holds the data"
    finally:
        copy.close()  # Windows cannot rotate a file a connection still holds
    assert auto_backup_due(instance.store) is False, "a fresh copy postpones the weekly one"

    # A repair snapshot in the same folder is never rotated away.
    backups_dir = instance.store.path.parent / "backups"
    keepsake = backups_dir / "jarvis_medical-before-repair-20260913-000000.sqlite3"
    keepsake.write_bytes((instance.store.path.parent / "backups" / rows[0]["file"]).read_bytes())
    backup_now(instance.store, keep=1)
    assert keepsake.exists(), "before-repair snapshots belong to their corrections"
    assert sum(1 for row in list_backups(instance.store) if row["kind"] == "backup") == 1

    real_usage = _shutil.disk_usage
    monkeypatch.setattr(repair_module.shutil, "disk_usage", lambda path: real_usage(path)._replace(free=0))
    with pytest.raises(ValueError, match="Diskte yer yok"):
        backup_now(instance.store)

    listing = instance.backups()
    assert listing["backups"] and "canlı veriyi kendiliğinden asla ezmez" in listing["note"]
    events: list[dict] = []
    instance.subscribe(events.append)
    monkeypatch.setattr(repair_module.shutil, "disk_usage", real_usage)
    report = asyncio.run(instance.backup_job())
    assert report["path"].endswith(".sqlite3")
    assert any(event.get("kind") == "backup_done" for event in events), "the page hears the result"


def test_exports_put_no_key_on_the_question_sheet_and_label_unscored_on_the_answer_key(academy) -> None:
    instance = academy(None)
    keyed = question("x1", refs=True)
    study_only = question("x2", origin=QuestionOrigin.GENERATED)
    for item in (keyed, study_only):
        instance.store.save_question(item)
    exam = instance.exam_builder.build(instance.exam_config({"subjects": ["anatomy"], "question_count": 2}), [keyed, study_only])

    name, sheet = instance.export_exam_markdown(exam.exam_id, include_answers=False)
    assert name.endswith("soru-kagidi.md")
    assert "**1.**" in sheet and "A) Capitulum humeri" in sheet
    assert "Cevap" not in sheet and keyed.explanation not in sheet, "the question sheet is for sitting on paper"

    key_name, key = instance.export_exam_markdown(exam.exam_id, include_answers=True)
    assert key_name.endswith("cevap-anahtari.md")
    assert "**Cevap: A**" in key and keyed.explanation in key
    assert "puansız (Kaynaksız (yalnız çalışma))" in key, "a study item is labelled on paper too"
    assert "Üst ekstremite · s. 12" in key

    from app.medical.models import SourceReference, StudyNote

    instance.store.save_note(StudyNote(note_id="n1", title="Karbonhidrat Özeti", content="## Glikoliz\nNet 2 ATP.", subject="biochemistry", references=[SourceReference("d1", 3, title="Biyokimya 7")]))
    note_name, note = instance.export_note_markdown("n1")
    assert note_name == "Karbonhidrat-Özeti.md"
    assert note.startswith("# Karbonhidrat Özeti") and "Net 2 ATP." in note and "Biyokimya 7 · s. 3" in note
    with pytest.raises(ValueError):
        instance.export_note_markdown("yok")


def test_weekly_export_prints_only_what_the_records_hold(academy) -> None:
    academy = academy()
    filename, content = academy.export_week_markdown()

    assert filename.startswith("haftalik-ozet-") and filename.endswith(".md")
    assert "Bu hafta kayıtlı çalışma yok." in content, "an empty week says so on paper too"
    assert "yalnız kayıtlardan hesaplanır" in content
    assert "| Gün | Dakika | Soru | Kart | Etkinlik |" in content
    assert content.count("|") >= 7 * 6, "seven day rows on the table"

    academy.study.planner.log_study(activity="read", minutes=20)
    _, busy = academy.export_week_markdown()
    assert "Bu hafta kayıtlı çalışma yok." not in busy
    assert "- Çalışma süresi: 20 dk" in busy
    assert "İşaretlenen cevap:" in busy and "Bitirilen kâğıt:" in busy, (
        "marked answers and finished-paper scoring stay separate facts"
    )
