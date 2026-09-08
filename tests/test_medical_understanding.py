"""Confidence-aware understanding: events, findings, diagnostics and repair.

Every rule that moves a finding is exercised here with an in-memory store, a
scripted model and a controllable clock: one wrong option never makes a
finding, a confident error does, a correct answer with a contradictory
explanation keeps its mark but opens a finding, unknown confidence never
penalises, a repeated submission is stored once, a transfer question shows
an initial repair only, resolution waits for the delayed follow-up, a
later confident error reopens, an invalidated question withdraws what
rested on it, and everything survives a reopened store.
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app.medical.catalog import Curriculum
from app.medical.concepts import default_concept_graph
from app.medical.learning import LearningEngine
from app.medical.model import MedicalModelClient
from app.medical.models import Question, QuestionOption, SourceReference
from app.medical.retrieval import Retriever
from app.medical.store import MedicalStore
from app.medical.understanding import (
    CONFIDENCE_LABELS_TR,
    EXPLANATION_SAMPLE_PER_EXAM,
    UnderstandingEngine,
    classify,
)

AP = "physiology.action_potential"
RMP = "physiology.resting_membrane_potential"
TOPIC = "physiology.excitable"
BASE = datetime(2026, 9, 8, 9, 0, tzinfo=timezone.utc)


class Clock:
    def __init__(self, start: datetime = BASE) -> None:
        self.now = start

    def __call__(self) -> datetime:
        return self.now

    def advance(self, **delta) -> None:
        self.now = self.now + timedelta(**delta)


class Gateway:
    def __init__(self, *replies: str) -> None:
        self.replies = list(replies)
        self.prompts: list[str] = []

    async def generate(self, request, context, **kwargs):
        self.prompts.append(request.text)
        reply = self.replies.pop(0) if len(self.replies) > 1 else self.replies[0]
        return SimpleNamespace(text=reply)


def question(question_id: str, stem: str, *, concept: str = AP, correct: str = "B", refs: bool = True) -> Question:
    options = [QuestionOption(key, text) for key, text in zip("ABCD", ["Kalsiyum girişi", "Sodyum girişi", "Potasyum girişi", "Klor çıkışı"])]
    return Question(
        question_id=question_id,
        subject="physiology",
        stem=stem,
        options=options,
        correct_key=correct,
        topic_id=TOPIC,
        concept_ids=[concept],
        explanation="Depolarizasyon fazında voltaj kapılı Na+ kanalları açılır ve Na+ hücreye girer.",
        references=[SourceReference("d1", 12, quote="Aksiyon potansiyelinin yükselen fazı Na+ girişiyle olur.", title="Fizyoloji notları")] if refs else [],
    )


def build(gateway=None, *, clock: Clock | None = None, path=None, generator=None):
    store = MedicalStore(path)
    curriculum = Curriculum()
    concepts = default_concept_graph()
    tick = clock or Clock()
    learning = LearningEngine(store, curriculum, concepts, clock=tick)
    model = MedicalModelClient(gateway)
    engine = UnderstandingEngine(store, learning, concepts, curriculum, model, Retriever(store), generator=generator, clock=tick)
    return engine, store, learning


def assessment(verdict: str, *, misconception: str = "", confidence: str = "high", diagnostic: str = "", quote: str = "") -> str:
    return json.dumps({"verdict": verdict, "suspected_misconception": misconception, "quote": quote, "assessment_confidence": confidence, "diagnostic_question": diagnostic, "note": "Not."})


# ---------------------------------------------------------------------------
# classification
# ---------------------------------------------------------------------------


def test_the_five_categories_and_unknown_confidence_never_count_as_confident() -> None:
    assert classify(True, "sure", "supported") == "correct_supported"
    assert classify(True, "guess", None) == "correct_unsupported"
    assert classify(True, None, "insufficient") == "correct_unsupported"
    assert classify(True, "sure", "contradictory") == "correct_contradictory"
    assert classify(False, "unsure", None) == "wrong_low_confidence"
    assert classify(False, None, None) == "wrong_low_confidence", "missing confidence is unknown, not a confident error"
    assert classify(False, "sure", None) == "wrong_high_confidence"
    assert classify(None, "sure", None) == "unknown"
    assert set(CONFIDENCE_LABELS_TR) == {"sure", "unsure", "guess"}


def test_a_confidence_outside_the_three_levels_is_refused() -> None:
    engine, store, _learning = build()
    store.save_question(question("q1", "Yükselen faz?"))
    with pytest.raises(ValueError):
        engine.record_event(store.get_question("q1"), correct=False, answer_key="A", confidence="very")


# ---------------------------------------------------------------------------
# events and findings
# ---------------------------------------------------------------------------


def test_one_wrong_option_with_low_confidence_opens_no_finding_but_a_confident_error_does() -> None:
    engine, store, _learning = build()
    store.save_question(question("q1", "Yükselen faz?"))
    store.save_question(question("q2", "Depolarizasyon iyonu?"))

    first = engine.record_event(store.get_question("q1"), correct=False, answer_key="A", confidence="guess", source="exam", exam_id="e1")
    assert first["classification"] == "wrong_low_confidence" and engine.findings() == []

    second = engine.record_event(store.get_question("q2"), correct=False, answer_key="C", confidence="sure", source="exam", exam_id="e1")
    (finding,) = engine.findings()
    assert second["classification"] == "wrong_high_confidence"
    assert finding["status"] == "hypothesis" and finding["concept_id"] == AP and finding["priority"] >= 3
    assert [item["kind"] for item in finding["evidence"]] == ["answer_confident"]
    assert finding["sources"][0]["page_number"] == 12 and finding["history"][0]["status"] == "hypothesis"
    assert finding["provenance"] == {"assessor": "rule", "version": "understanding-1"}


def test_two_low_confidence_errors_make_a_hypothesis_and_three_support_it() -> None:
    engine, store, _learning = build()
    for index in range(3):
        store.save_question(question(f"q{index}", f"Soru {index}?"))
    engine.record_event(store.get_question("q0"), correct=False, answer_key="A", confidence="unsure")
    assert engine.findings() == []
    engine.record_event(store.get_question("q1"), correct=False, answer_key="A")
    (finding,) = engine.findings()
    assert finding["status"] == "hypothesis" and len(finding["evidence"]) == 2, "the earlier wrong answer is carried in as evidence"
    engine.record_event(store.get_question("q2"), correct=False, answer_key="A", confidence="guess")
    (finding,) = engine.findings()
    assert finding["status"] == "supported" and finding["history"][-1]["note"].startswith("Birbirinden bağımsız")


def test_a_repeated_submission_id_is_stored_once() -> None:
    engine, store, _learning = build()
    store.save_question(question("q1", "Yükselen faz?"))
    first = engine.record_event(store.get_question("q1"), correct=False, answer_key="A", confidence="sure", submission_id="s-1")
    again = engine.record_event(store.get_question("q1"), correct=False, answer_key="A", confidence="sure", submission_id="s-1")
    assert again["event_id"] == first["event_id"] and len(engine.events()) == 1
    (finding,) = engine.findings()
    assert len(finding["evidence"]) == 1


def test_a_correct_answer_with_a_contradictory_explanation_opens_a_finding_without_touching_the_mark() -> None:
    gateway = Gateway(assessment("contradictory", misconception="Aksiyon potansiyelinin yükselen fazını potasyum girişi sanıyor.", quote="potasyum girer"))
    engine, store, _learning = build(gateway)
    store.save_question(question("q1", "Yükselen faz?"))

    event = engine.record_event(store.get_question("q1"), correct=True, answer_key="B", confidence="sure", reasoning="Çünkü hücreye potasyum girer ve zar pozitifleşir.", source="check")
    assert event["assessment"]["status"] == "pending" and event["classification"] == "correct_unsupported"

    assessed = asyncio.run(engine.assess(event["event_id"]))

    assert assessed["correct"] is True, "the mark is the exam's business and stays"
    assert assessed["classification"] == "correct_contradictory"
    assert assessed["assessment"]["verdict"] == "contradictory" and assessed["assessment"]["assessor"].startswith("model:")
    (finding,) = engine.findings()
    assert finding["statement"] == "Aksiyon potansiyelinin yükselen fazını potasyum girişi sanıyor."
    assert finding["evidence"][0]["kind"] == "explanation" and finding["evidence"][0]["excerpt"] == "potasyum girer"
    assert finding["status"] == "hypothesis", "one contradictory explanation is a hypothesis, not a verdict"
    assert "Student's explanation: Çünkü hücreye potasyum girer" in gateway.prompts[0]


def test_an_ambiguous_explanation_asks_a_diagnostic_question_instead_of_deciding() -> None:
    gateway = Gateway(assessment("contradictory", misconception="Belki iyon yönünü karıştırıyor.", confidence="low", diagnostic="Depolarizasyonda hangi iyon hangi yöne hareket eder?"))
    engine, store, _learning = build(gateway)
    store.save_question(question("q1", "Yükselen faz?"))
    event = engine.record_event(store.get_question("q1"), correct=False, answer_key="C", confidence="sure", reasoning="İyonlar dışarı çıkar galiba.")

    asyncio.run(engine.assess(event["event_id"]))

    (finding,) = engine.findings()
    assert finding["pending_diagnostic"]["question"] == "Depolarizasyonda hangi iyon hangi yöne hareket eder?"
    assert finding["status"] == "hypothesis"


def test_a_diagnostic_answer_confirms_or_after_two_refutations_withdraws() -> None:
    gateway = Gateway(json.dumps({"question": "Na+ hangi yöne akar?", "expected_answer": "İçeri", "rubric": "Yön doğru mu"}), json.dumps({"verdict": "confirms", "note": "Yönü ters söyledi.", "assessment_confidence": "high"}))
    engine, store, _learning = build(gateway)
    store.save_question(question("q1", "Yükselen faz?"))
    engine.record_event(store.get_question("q1"), correct=False, answer_key="C", confidence="sure")
    (finding,) = engine.findings()

    asked = asyncio.run(engine.diagnostic(finding["finding_id"]))
    assert asked["pending_diagnostic"]["expected_answer"] == "İçeri"
    confirmed = asyncio.run(engine.answer_diagnostic(finding["finding_id"], "Na+ dışarı akar."))
    assert confirmed["status"] == "supported" and confirmed["evidence"][-1]["kind"] == "diagnostic" and confirmed["pending_diagnostic"] is None

    asked_json = json.dumps({"question": "Soru?", "expected_answer": "Cevap", "rubric": ""})
    refute_json = json.dumps({"verdict": "refutes", "note": "Doğru anlatıyor."})
    refuting = Gateway(asked_json, refute_json, asked_json, refute_json)
    engine2, store2, _ = build(refuting)
    store2.save_question(question("q1", "Yükselen faz?"))
    engine2.record_event(store2.get_question("q1"), correct=False, answer_key="C", confidence="sure")
    (finding2,) = engine2.findings()
    for _ in range(2):
        asyncio.run(engine2.diagnostic(finding2["finding_id"]))
        finding2 = asyncio.run(engine2.answer_diagnostic(finding2["finding_id"], "Na+ hücreye girer, zar pozitifleşir."))
    assert finding2["status"] == "withdrawn" and "çürüttü" in finding2["history"][-1]["note"]


def test_without_a_model_the_explanation_is_kept_and_the_assessment_says_unavailable() -> None:
    engine, store, _learning = build(None)
    store.save_question(question("q1", "Yükselen faz?"))
    event = engine.record_event(store.get_question("q1"), correct=True, answer_key="B", reasoning="Na+ girer.")
    assessed = asyncio.run(engine.assess(event["event_id"]))
    assert assessed["assessment"]["status"] == "unavailable" and assessed["reasoning"] == "Na+ girer."
    assert assessed["classification"] == "correct_unsupported"


def test_the_student_can_challenge_or_dismiss_and_the_history_keeps_it() -> None:
    engine, store, _learning = build()
    store.save_question(question("q1", "Yükselen faz?"))
    engine.record_event(store.get_question("q1"), correct=False, answer_key="C", confidence="sure")
    (finding,) = engine.findings()

    disputed = engine.challenge(finding["finding_id"], "Şıkkı yanlış okudum.")
    assert disputed["status"] == "disputed" and disputed["student_note"] == "Şıkkı yanlış okudum."
    dismissed = engine.dismiss(finding["finding_id"])
    assert dismissed["status"] == "dismissed" and [item["status"] for item in dismissed["history"]] == ["hypothesis", "disputed", "dismissed"]
    assert engine.overview()["findings"] == [] and engine.overview()["closed"][0]["finding_id"] == finding["finding_id"]
    assert engine.reopen(finding["finding_id"])["status"] == "reopened"


# ---------------------------------------------------------------------------
# sampling
# ---------------------------------------------------------------------------


def test_explanations_are_asked_on_a_bounded_sample_and_never_during_a_timed_paper() -> None:
    engine, store, _learning = build()
    for index in range(40):
        store.save_question(question(f"q{index}", f"Soru {index}?", concept=f"physiology.c{index}"))
    sampled = [identifier for identifier in (f"q{index}" for index in range(40)) if engine.wants_explanation(store.get_question(identifier), exam_id="e1", immediate=True)[0]]
    assert 3 <= len(sampled) <= 20, "a deterministic minority of a practice paper"
    assert engine.wants_explanation(store.get_question("q0"), exam_id="e1", immediate=False) == (False, "deferred")
    for identifier in sampled[:EXPLANATION_SAMPLE_PER_EXAM]:
        engine.record_event(store.get_question(identifier), correct=True, answer_key="B", reasoning="Çünkü.", exam_id="e1")
    assert engine.wants_explanation(store.get_question(sampled[-1]), exam_id="e1", immediate=True) == (False, "sample_full")
    # A concept with an open finding is always asked, even in a paper marked at the end.
    store.save_question(question("open", "Açık bulgu?", concept="physiology.open"))
    engine.record_event(store.get_question("open"), correct=False, answer_key="A", confidence="sure")
    assert engine.wants_explanation(store.get_question("open"), exam_id="e2", immediate=False) == (True, "open_finding")


# ---------------------------------------------------------------------------
# repair
# ---------------------------------------------------------------------------


def transfer_reply(stem: str) -> str:
    return json.dumps({"questions": [{
        "stem": stem,
        "options": [{"key": "A", "text": "K+ girişi"}, {"key": "B", "text": "Na+ girişi"}, {"key": "C", "text": "Cl- çıkışı"}, {"key": "D", "text": "Ca2+ çıkışı"}, {"key": "E", "text": "Na+ çıkışı"}],
        "correct_key": "B", "explanation": "Hızlı depolarizasyon voltaj kapılı Na+ kanallarının açılıp Na+ girmesiyle olur; ilke sinir hücresindekiyle aynıdır.",
        "concept": "Aksiyon potansiyeli", "difficulty": 3}]})


EXPLANATION = "Yükselen fazda Na+ hücreye girer; K+ çıkışı ise repolarizasyonu yapar. Yönü karıştırmak iki fazı karıştırmaktır."


def repair_gateway(*, sessions: int = 1) -> Gateway:
    """Replies for each repair session in order: the explanation, then the transfer paper."""
    stems = ["Bir kalp kası hücresinde hızlı depolarizasyon fazında hangi olay gerçekleşir?", "Bir iskelet kası lifinde uyarı sonrası zar potansiyelini pozitife çeken akım hangisidir?"]
    replies: list[str] = []
    for index in range(sessions):
        replies += [EXPLANATION, transfer_reply(stems[index % len(stems)])]
    return Gateway(*replies)


def build_with_generator(gateway, clock: Clock, path=None):
    from tests.test_medical_exams import make_generator

    store = MedicalStore(path)
    generator = make_generator(store, gateway, clock=clock)
    curriculum = Curriculum()
    concepts = default_concept_graph()
    learning = LearningEngine(store, curriculum, concepts, clock=clock)
    model = MedicalModelClient(gateway)
    engine = UnderstandingEngine(store, learning, concepts, curriculum, model, Retriever(store), generator=generator, clock=clock)
    return engine, store, learning


def transfer_keys(store, session) -> tuple[str, str]:
    """The generated paper shuffles its options: read the key back from the store."""
    transfer = next(item for item in session["steps"] if item["step"] == "transfer")
    question = store.get_question(transfer["question"]["question_id"])
    wrong = next(option.key for option in question.options if option.key != question.correct_key)
    return question.correct_key, wrong


def test_a_repair_session_explains_cites_teaches_asks_anew_and_schedules_the_follow_up(tmp_path) -> None:
    clock = Clock()
    engine, store, learning = build_with_generator(repair_gateway(), clock, tmp_path / "medical.sqlite3")
    store.save_question(question("q1", "Aksiyon potansiyelinin yükselen fazında hangi iyon hücreye girer?"))
    engine.record_event(store.get_question("q1"), correct=False, answer_key="C", confidence="sure")
    (finding,) = engine.findings()

    session = asyncio.run(engine.start_repair(finding["finding_id"]))

    steps = {item["step"]: item for item in session["steps"]}
    assert steps["problem"]["text"].startswith("Aksiyon potansiyeli konusunda yanlış cevap") and "hipotez" in steps["problem"]["text"]
    assert steps["passage"]["sources"][0] == {"document_id": "d1", "page_number": 12, "title": "Fizyoloji notları", "quote": "Aksiyon potansiyelinin yükselen fazı Na+ girişiyle olur."}
    assert steps["explanation"]["text"].startswith("Yükselen fazda Na+") and steps["explanation"]["assessor"].startswith("model:")
    transfer = steps["transfer"]
    assert transfer["question"]["stem"].startswith("Bir kalp kası hücresinde") and "correct_key" not in transfer["question"]
    assert transfer["similarity"] is not None and transfer["limitations"][0].startswith("Farklı bağlamda bir soru daha")
    assert steps["follow_up"]["due_at"] == (clock.now + timedelta(days=3)).isoformat()
    assert store.get_mastery(AP).next_review_at == clock.now + timedelta(days=3), "the follow-up rides the review queue"
    assert engine.finding(finding["finding_id"])["follow_ups"][0]["due_at"] == steps["follow_up"]["due_at"]
    assert asyncio.run(engine.start_repair(finding["finding_id"]))["session_id"] == session["session_id"], "one open session per finding"

    # The transfer question answered right is the initial repair, not the resolution.
    right, wrong = transfer_keys(store, session)
    closed = engine.answer_transfer(session["session_id"], right, confidence="sure", reasoning="Na+ girer.")
    assert closed["outcome"] == "initial_repair" and closed["status"] == "closed"
    repaired = engine.finding(finding["finding_id"])
    assert repaired["status"] == "repair_demonstrated" and repaired["repair"]["initial_repair_at"] == clock.now.isoformat()
    assert engine.answer_transfer(session["session_id"], wrong)["outcome"] == "initial_repair", "a second answer changes nothing"

    # Too early for confirmation, then a delayed review success resolves it.
    assert engine.confirm_follow_ups() == []
    clock.advance(days=1)
    engine.record_event(store.get_question("q1"), correct=True, answer_key="B", source="review")
    assert engine.finding(finding["finding_id"])["status"] == "repair_demonstrated", "a day later is inside min_days"
    clock.advance(days=2)
    engine.record_event(store.get_question("q1"), correct=True, answer_key="B", source="review")
    resolved = engine.finding(finding["finding_id"])
    assert resolved["status"] == "resolved" and resolved["repair"]["confirmed_at"] == clock.now.isoformat()
    assert resolved["follow_ups"][0]["outcome"] == "confirmed"

    # Everything is still there after a restart.
    store.close()
    again, reopened_store, _ = build_with_generator(repair_gateway(), clock, tmp_path / "medical.sqlite3")
    assert again.finding(finding["finding_id"])["status"] == "resolved"
    assert again.repair(session["session_id"])["outcome"] == "initial_repair"
    assert len(again.events(concept_id=AP)) == 4


def test_a_confident_error_after_repair_reopens_the_finding_and_a_wrong_transfer_is_not_a_repair() -> None:
    clock = Clock()
    engine, store, _learning = build_with_generator(repair_gateway(sessions=2), clock)
    store.save_question(question("q1", "Yükselen faz?"))
    engine.record_event(store.get_question("q1"), correct=False, answer_key="C", confidence="sure")
    (finding,) = engine.findings()
    session = asyncio.run(engine.start_repair(finding["finding_id"]))

    _right, wrong_key = transfer_keys(store, session)
    wrong = engine.answer_transfer(session["session_id"], wrong_key, confidence="unsure")
    assert wrong["outcome"] == "not_yet" and engine.finding(finding["finding_id"])["status"] == "hypothesis"

    second = asyncio.run(engine.start_repair(finding["finding_id"]))
    assert second["session_id"] != session["session_id"]
    engine.answer_transfer(second["session_id"], transfer_keys(store, second)[0])
    assert engine.finding(finding["finding_id"])["status"] == "repair_demonstrated"
    clock.advance(days=5)
    engine.record_event(store.get_question("q1"), correct=False, answer_key="C", confidence="sure", source="exam")
    reopened = engine.finding(finding["finding_id"])
    assert reopened["status"] == "reopened" and reopened["priority"] >= 5


def test_repair_without_a_model_falls_back_to_the_bank_and_says_what_it_could_not_do() -> None:
    engine, store, _learning = build(None)
    store.save_question(question("q1", "Yükselen faz?", refs=False))
    store.save_question(question("q2", "Depolarizasyonda giren iyon?", refs=False))
    engine.record_event(store.get_question("q1"), correct=False, answer_key="C", confidence="sure")
    (finding,) = engine.findings()

    session = asyncio.run(engine.start_repair(finding["finding_id"]))

    steps = {item["step"]: item for item in session["steps"]}
    assert "Model sağlayıcısı kapalı" in steps["explanation"]["text"] and steps["explanation"]["assessor"] == "none"
    assert steps["passage"]["sources"] == [] and "Kütüphanede" in steps["passage"]["text"]
    assert steps["transfer"]["question"]["question_id"] == "q2"
    assert any("bankadaki bir soru" in item for item in steps["transfer"]["limitations"])


# ---------------------------------------------------------------------------
# understanding checks and corrections
# ---------------------------------------------------------------------------


def test_an_understanding_check_asks_a_bank_question_and_records_confidence_and_reasoning() -> None:
    engine, store, learning = build(Gateway(assessment("supported")))
    store.save_question(question("q1", "Yükselen faz?"))

    check = engine.start_check(concept_id=AP)
    assert check["question"]["question_id"] == "q1" and "correct_key" not in check["question"]

    answered = engine.answer_check(check["check_id"], "B", confidence="sure", reasoning="Na+ girer.")
    assert answered["result"]["correct"] is True and answered["question"]["correct_key"] == "B"
    assert learning.summary()["attempts"] == 1
    assert engine.answer_check(check["check_id"], "A", confidence="guess")["result"]["answer_key"] == "B", "answered once"
    assessed = asyncio.run(engine.assess(answered["result"]["event_id"]))
    assert assessed["classification"] == "correct_supported"
    assert engine.start_check(concept_id="physiology.nothing") is None


def test_an_invalidated_question_withdraws_the_finding_that_rested_on_it_alone() -> None:
    engine, store, _learning = build()
    store.save_question(question("q1", "Yükselen faz?"))
    store.save_question(question("q2", "Depolarizasyon?"))
    engine.record_event(store.get_question("q1"), correct=False, answer_key="C", confidence="sure")
    (finding,) = engine.findings()

    result = engine.invalidate_question("q1", "Anahtar hatalıydı")

    assert result == {"events": 1, "findings": 1}
    withdrawn = engine.finding(finding["finding_id"])
    assert withdrawn["status"] == "withdrawn" and withdrawn["evidence"][0]["valid"] is False
    assert engine.events()[0]["invalidated"] is True and engine.events()[0]["invalidation_reason"] == "Anahtar hatalıydı"
    # A finding with evidence from another question survives, one step down.
    engine.record_event(store.get_question("q2"), correct=False, answer_key="C", confidence="sure")
    (second,) = [item for item in engine.findings() if item["status"] == "hypothesis"]
    assert second["question_ids"] == ["q2"]
