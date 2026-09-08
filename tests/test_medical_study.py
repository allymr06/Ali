"""The connected study workflow through the academy and the Nova bridge.

One journey, end to end, with a scripted model and temporary state: an
exam is planned, today's activity says what to do, an answer carries the
student's confidence, a contradictory explanation opens a finding, the
diagnosis looks at a prerequisite, a repair session asks a transfer
question, the delayed review resolves the finding, and the plan's coverage
moves. Then the bridge: sync actions answer at once, async ones run as
jobs and report back, destructive ones need confirmation, and the answer
action carries confidence through.
"""

from __future__ import annotations

import asyncio
import json
from datetime import timedelta
from types import SimpleNamespace

import pytest

from app.medical.academy import create_medical_academy
from app.medical.models import Question, QuestionOption, SourceReference

AP = "physiology.action_potential"
RMP = "physiology.resting_membrane_potential"
EXCITABLE = "physiology.excitable"


class Gateway:
    def __init__(self, *replies: str) -> None:
        self.replies = list(replies)
        self.prompts: list[str] = []

    async def generate(self, request, context, **kwargs):
        self.prompts.append(request.text)
        reply = self.replies.pop(0) if len(self.replies) > 1 else self.replies[0]
        return SimpleNamespace(text=reply)


def question(question_id: str, stem: str, *, concept: str = AP, correct: str = "B") -> Question:
    options = [QuestionOption(key, text) for key, text in zip("ABCDE", ["Ca2+ girişi", "Na+ girişi", "K+ girişi", "Cl- çıkışı", "Na+ çıkışı"])]
    return Question(question_id=question_id, subject="physiology", stem=stem, options=options, correct_key=correct, topic_id=EXCITABLE, concept_ids=[concept], explanation="Yükselen fazda Na+ girer.", references=[SourceReference("d1", 3, quote="Na+ girişi", title="Fizyoloji")])


@pytest.fixture()
def academy(tmp_path):
    built = []

    def factory(gateway=None, **overrides):
        fields = {"medical_directory": str(tmp_path / f"medical{len(built)}"), "medical_source_review": False, **overrides}
        settings = SimpleNamespace(**fields)
        instance = create_medical_academy(settings=settings, provider_gateway=gateway)
        built.append(instance)
        return instance

    yield factory
    for instance in built:
        instance.close()


def transfer_reply() -> str:
    return json.dumps({"questions": [{
        "stem": "Bir kalp kası hücresinde hızlı depolarizasyon fazında hangi olay gerçekleşir?",
        "options": [{"key": "A", "text": "K+ girişi"}, {"key": "B", "text": "Na+ girişi"}, {"key": "C", "text": "Cl- çıkışı"}, {"key": "D", "text": "Ca2+ çıkışı"}, {"key": "E", "text": "Na+ çıkışı"}],
        "correct_key": "B", "explanation": "Hızlı depolarizasyon Na+ girişiyle olur; ilke sinir hücresindekiyle aynıdır.", "concept": "Aksiyon potansiyeli", "difficulty": 3}]})


def test_the_journey_from_scope_to_repair_and_a_moving_plan(academy) -> None:
    assessment = json.dumps({"verdict": "contradictory", "suspected_misconception": "Yükselen fazı K+ girişi sanıyor.", "quote": "potasyum girer", "assessment_confidence": "high", "diagnostic_question": "", "note": "Not."})
    gateway = Gateway(assessment, "Yükselen fazda Na+ girer; K+ çıkışı repolarizasyondur.", transfer_reply())
    instance = academy(gateway)
    store, study = instance.store, instance.study
    for index in range(5):
        store.save_question(question(f"q{index}", f"Aksiyon potansiyeli sorusu {index}?"))
    store.save_question(question("rmp", "Dinlenim potansiyeli sorusu?", concept=RMP))

    # 1. Exam scope → today's activity.
    plan = study.call("plan_create", {"name": "Komite 2", "exam_date": (study.planner.today() + timedelta(days=7)).isoformat(), "subjects": ["physiology"], "daily_minutes": 45})["plan"]
    today = study.call("plan_today", {})["today"]
    assert plan["scope_confirmed"] is True and today["next"]["kind"] == "read" and today["planned_minutes"] <= 45
    assert instance.dashboard()["study"]["today"]["plan"]["name"] == "Komite 2"

    # 2. A practice sitting: the answer carries confidence; a reasoning is asked on a sample.
    paper = asyncio.run(instance.generate_exam({"from_bank": True, "question_count": 5, "topic_ids": [EXCITABLE], "randomize": False, "immediate_feedback": True}))
    exam_id = paper["exam_id"]
    ids = [item["question_id"] for item in paper["questions"]]
    instance.start_exam(exam_id)
    first = instance.answer(exam_id, ids[0], "B", confidence="sure", reasoning="Çünkü hücreye potasyum girer.")
    assert first["feedback"]["correct"] is True and first["confidence"] == "sure" and first["event_id"]
    with pytest.raises(ValueError):
        instance.answer(exam_id, ids[1], "B", confidence="çok emin")
    again = instance.answer(exam_id, ids[0], "B", flagged=True)
    assert "event_id" not in again, "flagging re-sends the answer and records no second event"

    # 3. Targeted diagnosis: the contradictory explanation opens a finding though the mark was right.
    assessed = asyncio.run(study.start("understanding_assess", {"event_id": first["event_id"]}))["study"]["event"]
    assert assessed["classification"] == "correct_contradictory"
    overview = study.call("understanding_overview", {})
    (finding,) = overview["findings"]
    assert finding["statement"] == "Yükselen fazı K+ girişi sanıyor." and finding["status"] == "hypothesis"
    assert store.latest_attempt(exam_id).answers[ids[0]].correct is True, "the exam mark is the exam's"

    # 4. Prerequisite repair: the diagnosis asks about the foundation and comes back.
    diagnosis = study.call("diagnosis_start", {"concept_id": AP, "objective": {"question_stem": paper["questions"][0]["stem"]}})["diagnosis"]
    assert diagnosis["intro"].startswith("Aksiyon potansiyeli konusuna dönmeden önce")
    candidate = next(item for item in diagnosis["candidates"] if item["concept_id"] == RMP)
    assert candidate["question"]["question_id"] == "rmp" and "correct_key" not in candidate["question"]
    located = study.call("diagnosis_answer", {"diagnosis_id": diagnosis["diagnosis_id"], "concept_id": RMP, "answer_key": "A", "confidence": "unsure"})["diagnosis"]
    for item in located["candidates"]:
        if item.get("question") and item.get("answer") is None:
            located = study.call("diagnosis_skip", {"diagnosis_id": diagnosis["diagnosis_id"], "concept_id": item["concept_id"]})["diagnosis"]
    assert located["status"] == "located" and located["located"] == RMP and located["path"] == [RMP, AP]
    assert study.call("diagnosis_finish", {"diagnosis_id": diagnosis["diagnosis_id"]})["diagnosis"]["status"] == "closed"

    # 5. New application question, then the delayed review, then the plan.
    session = asyncio.run(study.start("repair_start", {"finding_id": finding["finding_id"]}))["study"]["session"]
    steps = {item["step"]: item for item in session["steps"]}
    assert steps["explanation"]["text"].startswith("Yükselen fazda Na+") and steps["transfer"]["question"]["stem"].startswith("Bir kalp kası")
    transfer = store.get_question(steps["transfer"]["question"]["question_id"])
    closed = study.call("repair_answer", {"session_id": session["session_id"], "answer_key": transfer.correct_key, "confidence": "sure"})
    assert closed["session"]["outcome"] == "initial_repair" and closed["finding"]["status"] == "repair_demonstrated"
    coverage = study.call("plan_coverage", {"plan_id": plan["plan_id"]})["coverage"]
    excitable = next(row for row in coverage["topics"] if row["topic_id"] == EXCITABLE)
    assert excitable["state"] in ("assessed_limited", "due_review") and excitable["attempts"] >= 1
    # Everything survives a restart of the academy.
    reopened = academy(gateway, medical_directory=str(store.path.parent))
    assert reopened.study.understanding.finding(finding["finding_id"])["status"] == "repair_demonstrated"
    assert reopened.study.planner.plan(plan["plan_id"])["name"] == "Komite 2"


def test_a_paper_marked_at_the_end_keeps_confidence_until_it_is_finished(academy) -> None:
    instance = academy(None)
    store, study = instance.store, instance.study
    for index in range(3):
        store.save_question(question(f"q{index}", f"Soru {index}?"))
    paper = asyncio.run(instance.generate_exam({"from_bank": True, "question_count": 3, "topic_ids": [EXCITABLE], "randomize": False}))
    exam_id = paper["exam_id"]
    instance.start_exam(exam_id)
    instance.answer(exam_id, "q0", "A", confidence="sure")
    instance.answer(exam_id, "q1", "B", confidence="guess")
    assert study.understanding.events() == [], "nothing is judged before the paper is marked"

    result = instance.finish_exam(exam_id)

    events = result["analysis"]["events"]
    assert set(events) == {"q0", "q1"}
    recorded = {event["question_id"]: event for event in study.understanding.events()}
    assert recorded["q0"]["classification"] == "wrong_high_confidence" and recorded["q1"]["classification"] == "correct_unsupported"
    assert instance.finish_exam(exam_id)["analysis"]["events"] == events, "finishing again records nothing twice"
    assert len(study.understanding.events()) == 2
    (finding,) = study.understanding.findings()
    assert finding["concept_id"] == AP and finding["evidence"][0]["kind"] == "answer_confident"


def test_a_practice_paper_records_each_answer_once_even_when_it_is_finished(academy) -> None:
    instance = academy(None)
    store, study = instance.store, instance.study
    for index in range(2):
        store.save_question(question(f"q{index}", f"Soru {index}?"))
    paper = asyncio.run(instance.generate_exam({"from_bank": True, "question_count": 2, "topic_ids": [EXCITABLE], "randomize": False, "immediate_feedback": True}))
    exam_id = paper["exam_id"]
    instance.start_exam(exam_id)
    # The page sends its own token with the answer; the event's identity is still the attempt and the question.
    first = instance.answer(exam_id, "q0", "A", confidence="sure", submission_id=f"{exam_id}:q0:A")
    second = instance.answer(exam_id, "q1", "B", confidence="guess", submission_id=f"{exam_id}:q1:B")
    assert len(study.understanding.events()) == 2

    result = instance.finish_exam(exam_id)

    assert result["analysis"]["events"] == {"q0": first["event_id"], "q1": second["event_id"]}
    assert len(study.understanding.events()) == 2, "finishing a practice paper records nothing twice"
    (finding,) = study.understanding.findings()
    evidence = finding["evidence"]
    assert len({item["event_id"] for item in evidence}) == len(evidence), "no event is counted twice as evidence"
    assert [item["kind"] for item in evidence] == ["answer", "follow_up"]


def test_invalidating_a_question_through_the_workflow_corrects_everything_it_moved(academy) -> None:
    instance = academy(None)
    store, study = instance.store, instance.study
    store.save_question(question("q0", "Soru 0?"))
    store.save_question(question("q1", "Soru 1?"))
    paper = asyncio.run(instance.generate_exam({"from_bank": True, "question_count": 2, "topic_ids": [EXCITABLE], "randomize": False, "immediate_feedback": True}))
    instance.start_exam(paper["exam_id"])
    instance.answer(paper["exam_id"], "q0", "A", confidence="sure")
    instance.answer(paper["exam_id"], "q1", "B", confidence="sure")
    assert store.get_mastery(AP).attempts == 2 and len(study.understanding.findings()) == 1

    study.call("flag_question", {"question_id": "q0", "kind": "disputed_answer", "note": "Anahtar A olmalı."})
    outcome = study.call("invalidate_question", {"question_id": "q0", "reason": "Anahtar hatalı", "confirmed": True})

    assert outcome["support"]["status"] == "invalidated" and outcome["understanding"] == {"events": 1, "findings": 1}
    assert store.get_mastery(AP).attempts == 1 and store.get_mastery(AP).correct == 1
    assert study.understanding.findings()[0]["status"] == "withdrawn"
    assert store.latest_attempt(paper["exam_id"]).answers["q0"].answer_key == "A", "the attempt is history"
    bank = instance.question_bank({"topic_id": EXCITABLE})
    by_id = {item["question_id"]: item for item in bank["questions"]}
    assert by_id["q0"]["invalidated"] is True and by_id["q0"]["support"]["status"] == "invalidated" and by_id["q0"]["flags"] == 1
    assert by_id["q1"]["support"]["status"] == "needs_review"


def test_unknown_and_malformed_study_calls_fail_closed(academy) -> None:
    instance = academy(None)
    with pytest.raises(KeyError):
        instance.study.call("no_such_action", {})
    with pytest.raises(ValueError):
        instance.study.call("plan_create", {"name": "", "exam_date": "2030-01-01"})
    with pytest.raises(ValueError):
        instance.study.call("understanding_finding", {"finding_id": "missing"})
    with pytest.raises(ValueError):
        instance.study.call("histology_add", {"document_id": "missing", "page_number": 1, "region": {"x": 0, "y": 0, "w": 1, "h": 1}})
    assert instance.study.call("plan_today", {})["today"]["plan"] is None
    assert instance.study.call("histology_overview", {})["empty_state"]


def test_a_student_names_a_prerequisite_and_the_graph_keeps_its_provenance(academy) -> None:
    instance = academy(None)
    study = instance.study

    # The name box: a Turkish alias or a fragment of the name finds the concept.
    found = study.call("concept_search", {"text": "dinlenim"})["concepts"]
    assert any(item["concept_id"] == RMP for item in found) and all("subject_label" in item for item in found)
    assert study.call("concept_search", {"text": "d"})["concepts"] == []

    # The student's own suggestion is pending until confirmed, and carries its provenance.
    nernst = next(item for item in study.call("prerequisites", {"concept_id": RMP})["prerequisites"] if item["depth"] == 1)
    edge = study.call("prerequisite_suggest", {"concept_id": AP, "requires": nernst["concept_id"], "provenance": "student", "note": "Öğrenci önerdi."})["edge"]
    assert edge["status"] == "pending" and edge["provenance_label"] == "Öğrenci önerdi"
    pending = study.call("prerequisites", {"concept_id": AP})["pending"]
    assert [item["edge_id"] for item in pending] == [edge["edge_id"]]
    confirmed = study.call("prerequisite_confirm", {"edge_id": edge["edge_id"]})["edge"]
    assert confirmed["status"] == "reviewed"
    direct = [item for item in study.call("prerequisites", {"concept_id": AP})["prerequisites"] if item["depth"] == 1]
    assert any(item["concept_id"] == nernst["concept_id"] and item["provenance"] == "student" for item in direct)

    # A link that would close a cycle is refused at confirmation, never stored as reviewed.
    backwards = study.call("prerequisite_suggest", {"concept_id": RMP, "requires": AP, "provenance": "student"})["edge"]
    with pytest.raises(ValueError, match="döngü"):
        study.call("prerequisite_confirm", {"edge_id": backwards["edge_id"]})
    with pytest.raises(ValueError):
        study.call("prerequisite_suggest", {"concept_id": AP, "requires": RMP, "provenance": "guess"})
