"""Exam-date planning: scope, coverage, a budget that is never exceeded, honest overload.

A controllable clock in the student's own time zone, an in-memory store,
and the curriculum as shipped: the daily budget holds, untested topics stay
visible next to strong ones, a missed day replans within bounds, a moved
exam keeps history, a date is a date across midnight, overload is stated
with numbers, an inferred scope waits for confirmation, estimates learn
from real minutes, and planned, started, skipped, completed and missed are
five different things.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest

from app.medical.catalog import Curriculum
from app.medical.concepts import default_concept_graph
from app.medical.learning import LearningEngine
from app.medical.model import MedicalModelClient
from app.medical.models import ConceptMastery, DocumentStatus, ExamAttempt, MasteryLevel, Question, QuestionAttempt, QuestionOption, QuestionOrigin, StudyDocument
from app.medical.planner import COVERAGE_LABELS_TR, DEFAULT_ESTIMATES, MIN_ACTIVITY_MINUTES, StudyPlanner
from app.medical.prerequisites import PrerequisiteGraph
from app.medical.retrieval import Retriever
from app.medical.review import rule_decision
from app.medical.store import MedicalStore
from app.medical.understanding import UnderstandingEngine

ISTANBUL = timezone(timedelta(hours=3))
START = datetime(2026, 9, 8, 9, 0, tzinfo=ISTANBUL)
EXCITABLE = "physiology.excitable"
TRANSPORT = "physiology.membrane_transport"
MUSCLE = "physiology.muscle_physiology"


class Clock:
    def __init__(self, start: datetime = START) -> None:
        self.now = start

    def __call__(self) -> datetime:
        return self.now

    def advance(self, **delta) -> None:
        self.now = self.now + timedelta(**delta)


def question(question_id: str, topic_id: str, concept: str, *, origin: str = QuestionOrigin.GENERATED) -> Question:
    """A bank item. Generated with no source passage by default: study material
    under the scoring policy, which a person's key (``MANUAL``) is not."""
    options = [QuestionOption(key, text) for key, text in zip("ABCD", ["Bir", "İki", "Üç", "Dört"])]
    return Question(question_id=question_id, subject="physiology", stem=f"{question_id} sorusu?", options=options, correct_key="B", topic_id=topic_id, concept_ids=[concept], explanation="Açıklama.", origin=origin)


def build(path=None, clock: Clock | None = None, scoring=None):
    store = MedicalStore(path)
    curriculum = Curriculum()
    concepts = default_concept_graph()
    tick = clock or Clock()
    learning = LearningEngine(store, curriculum, concepts, clock=lambda: tick.now.astimezone(timezone.utc))
    understanding = UnderstandingEngine(store, learning, concepts, curriculum, MedicalModelClient(None), Retriever(store), clock=lambda: tick.now.astimezone(timezone.utc))
    graph = PrerequisiteGraph(concepts, store)
    reminders: list[tuple[str, str]] = []

    def remind(text: str, at: str) -> str:
        reminders.append((text, at))
        return f"rem-{len(reminders)}"

    planner = StudyPlanner(store, curriculum, concepts, learning, understanding, graph, scoring=scoring or rule_decision, clock=tick, remind=remind)
    planner.reminders = reminders  # type: ignore[attr-defined]
    return planner, store, learning, understanding, tick


def physiology_plan(planner: StudyPlanner, *, days: int = 10, minutes: int = 45, **fields):
    exam = (planner.today() + timedelta(days=days)).isoformat()
    return planner.create("Komite 2", exam, subjects=["physiology"], daily_minutes=minutes, **fields)


def _chain(planner: StudyPlanner, plan_id: str) -> list[dict]:
    """Every session of the first activity the plan lays out, in order."""
    activities = planner.activities(plan_id)
    first = activities[0]
    return [item for item in activities if (item["topic_id"], item["kind"]) == (first["topic_id"], first["kind"])]


# ---------------------------------------------------------------------------
# budget and coverage
# ---------------------------------------------------------------------------


def test_a_forty_five_minute_budget_is_respected_on_every_day() -> None:
    planner, store, _learning, _understanding, _clock = build()
    record = physiology_plan(planner)

    summary = planner.replan(record["plan_id"])

    assert summary["scope_confirmed"] is True and summary["coverage"]["total"] == 11
    assert all(day["planned_minutes"] <= 45 for day in summary["days"]) and summary["days"][0]["planned_minutes"] > 0
    for day in summary["days"]:
        for activity in day["activities"]:
            assert activity["estimate_label"].startswith("tahmini") and activity["reason"] and activity["status"] == "planned"
    today = planner.today_view(record["plan_id"])
    assert today["budget"] == 45 and today["planned_minutes"] <= 45 and today["next"]["kind"] == "read"
    assert today["next"]["reason"] == "Sınav kapsamında ama hiç açılmadı."
    assert summary["fit"] is True or summary["overload"] is not None


def test_untested_topics_stay_visible_beside_a_strong_one_and_reading_never_counts_as_mastery() -> None:
    planner, store, learning, _understanding, clock = build()
    # An exam the student sat: both halves of the topic's evidence — the
    # answers and the mastery rows the finish writes from them — rest on
    # the one scoring decision, so neither can carry the verdict alone.
    for identifier in ("a", "b", "c"):
        store.save_question(question(identifier, EXCITABLE, "physiology.action_potential", origin=QuestionOrigin.IMPORTED_EXAM))
    for identifier in ("a", "b", "c"):
        learning.record(store.get_question(identifier), True, chosen_key="B")
    store.save_attempt(ExamAttempt("att", "e1", started_at=clock.now, finished_at=clock.now, answers={identifier: QuestionAttempt(identifier, "B", True) for identifier in ("a", "b", "c")}))
    store.save_record("understanding_event", "ev1", {"event_id": "ev1", "concept_ids": ["physiology.action_potential"], "classification": "correct_supported", "correct": True, "at": clock.now.isoformat()}, subject_key="physiology.action_potential")
    planner.log_study(topic_id=TRANSPORT, activity="read", minutes=12)
    record = physiology_plan(planner)

    coverage = planner.coverage(record["plan_id"])

    by_topic = {row["topic_id"]: row for row in coverage["topics"]}
    assert by_topic[EXCITABLE]["state"] == "demonstrated" and by_topic[EXCITABLE]["flags"]["demonstrated"] is True
    assert by_topic[TRANSPORT]["state"] == "studied_unassessed", "opening material is studied, never mastered"
    assert by_topic[MUSCLE]["state"] == "unstudied"
    assert coverage["counts"]["unstudied"] == 9 and coverage["counts"]["demonstrated"] == 1 and coverage["counts"]["studied_unassessed"] == 1
    assert set(COVERAGE_LABELS_TR) == {"misconception", "due_review", "unstudied", "studied_unassessed", "assessed_limited", "demonstrated"}

    summary = planner.replan(record["plan_id"])
    first_day = summary["days"][0]["activities"]
    assert first_day and first_day[0]["kind"] in ("read", "assess") and first_day[0]["topic_id"] != EXCITABLE, "the strong topic is not first in line"
    kinds = {(item["topic_id"], item["kind"]) for day in summary["days"] for item in day["activities"]}
    assert (TRANSPORT, "assess") in kinds and (EXCITABLE, "recap") not in {item for item in kinds if item[1] != "recap"}


def test_coverage_counts_answers_that_measured_something_and_no_others() -> None:
    planner, store, _learning, _understanding, clock = build()
    store.save_question(question("m1", MUSCLE, "physiology.sliding_filament", origin=QuestionOrigin.MANUAL))
    store.save_question(question("g1", TRANSPORT, "physiology.na_k_atpase"))
    store.save_attempt(ExamAttempt("att", "e1", started_at=clock.now, finished_at=clock.now, answers={identifier: QuestionAttempt(identifier, "B", True) for identifier in ("m1", "g1")}))
    record = physiology_plan(planner)

    by_topic = {row["topic_id"]: row for row in planner.coverage(record["plan_id"])["topics"]}

    assert by_topic[MUSCLE]["state"] == "assessed_limited" and by_topic[MUSCLE]["attempts"] == 1
    # The study-only answer is work done, not knowledge shown: the plan may not
    # report the topic as covered on the strength of it.
    assert by_topic[TRANSPORT]["state"] == "unstudied"
    assert by_topic[TRANSPORT]["attempts"] == 0 and by_topic[TRANSPORT]["flags"]["assessed"] is False


def test_coverage_asks_the_scoring_decision_once_per_question_however_often_it_was_answered() -> None:
    """In the academy that decision is the reviewer's, which re-reads every
    document a question cites. Coverage runs on coverage(), summary(),
    replan() and today_view(), and one page load runs two of those, so a
    paper answered again and again must not be weighed again and again."""
    asked: list[str] = []

    def counting(item):
        asked.append(item.question_id)
        return rule_decision(item)

    planner, store, _learning, _understanding, clock = build(scoring=counting)
    store.save_question(question("m1", MUSCLE, "physiology.sliding_filament", origin=QuestionOrigin.IMPORTED_EXAM))
    for index in range(40):
        store.save_attempt(ExamAttempt(f"att{index}", "e1", started_at=clock.now, finished_at=clock.now, answers={"m1": QuestionAttempt("m1", "B", True)}))
    record = physiology_plan(planner)
    asked.clear()

    by_topic = {row["topic_id"]: row for row in planner.coverage(record["plan_id"])["topics"]}

    assert by_topic[MUSCLE]["attempts"] == 40
    assert asked == ["m1"], "one decision per question, not one per answered row"


# ---------------------------------------------------------------------------
# work longer than a day
# ---------------------------------------------------------------------------


def test_a_reading_longer_than_a_day_is_split_and_every_session_states_its_own_minutes() -> None:
    """A session carries the reason the work exists plus one sentence about
    itself. Told what the session before it had left over as well, a reading
    cut across three days would end up reciting all three figures."""
    planner, _store, _learning, _understanding, _clock = build()
    record = physiology_plan(planner, minutes=7)

    sessions = _chain(planner, record["plan_id"])

    base = "Sınav kapsamında ama hiç açılmadı."
    assert [item["estimate_minutes"] for item in sessions] == [7, 7, 6]
    assert sessions[0]["reason"] == base + f" Tahmini {DEFAULT_ESTIMATES['read']} dk: bir güne sığmaz, birkaç oturuma yayılır; bu oturum 7 dk."
    assert sessions[1]["reason"] == base + " Önceki oturumun devamı: bu oturum 7 dk, 6 dk sonraya kalıyor."
    assert sessions[2]["reason"] == base + " Önceki oturumun devamı: bu oturum 6 dk."


def test_no_session_of_a_split_is_shorter_than_the_shortest_activity_there_is() -> None:
    """``MIN_ACTIVITY_MINUTES`` is the shortest thing this module is willing
    to call an activity, and a cut has two sides. Where no cut clears it on
    both, the work is left whole and reported as uncovered instead."""
    planner, _store, _learning, _understanding, _clock = build()
    record = physiology_plan(planner, minutes=19)

    sessions = _chain(planner, record["plan_id"])

    assert [item["estimate_minutes"] for item in sessions] == [15, 5]
    assert all(day["planned_minutes"] <= 19 for day in planner.summary(record["plan_id"])["days"])

    # Twelve minutes of questions over five-minute days: two sittings would
    # need six minutes each and three would need fifteen minutes of work.
    tight, _store, _learning, _understanding, _clock = build()
    exam = (tight.today() + timedelta(days=12)).isoformat()
    cramped = tight.create("Komite 3", exam, topic_ids=[EXCITABLE], daily_minutes=MIN_ACTIVITY_MINUTES)
    tight.log_study(topic_id=EXCITABLE, activity="read", minutes=20)
    summary = tight.replan(cramped["plan_id"], reason="okundu")

    assert not tight.activities(cramped["plan_id"]), "nothing is quietly shortened to fit"
    assert [(item["kind"], item["estimate_minutes"]) for item in summary["uncovered"]] == [("assess", DEFAULT_ESTIMATES["assess"])]


def test_a_budget_that_leaves_an_undividable_scrap_ahead_still_plans_every_minute() -> None:
    """Cutting the work a day at a time takes the whole budget and asks again
    tomorrow, which on a six-minute day leaves eight minutes no later cut can
    divide: offered for ever, planned never, while the days ahead stay empty.
    The chain is settled in one go instead, so the floor costs nothing."""
    planner, _store, _learning, _understanding, _clock = build()
    record = physiology_plan(planner, minutes=6)
    plan_id = record["plan_id"]

    sessions = _chain(planner, plan_id)

    summary = planner.summary(plan_id)
    left = [item for item in summary["uncovered"] if (item["topic_id"], item["kind"]) == (sessions[0]["topic_id"], sessions[0]["kind"])]
    assert [item["estimate_minutes"] for item in sessions] == [5, 5, 5, 5]
    assert sum(item["estimate_minutes"] for item in sessions) == DEFAULT_ESTIMATES["read"] and left == []
    assert all(item["estimate_minutes"] >= MIN_ACTIVITY_MINUTES for item in sessions)


def test_work_no_day_can_hold_is_named_as_such_and_not_blamed_on_the_deadline() -> None:
    """Overload is one reason work stays uncovered and the floor is another.
    Left unsaid, the plan lists work as open on the same screen as a scope it
    says fits, and the student is given no idea which number to change."""
    planner, _store, _learning, _understanding, _clock = build()
    exam = (planner.today() + timedelta(days=12)).isoformat()
    record = planner.create("Komite 4", exam, topic_ids=[EXCITABLE], daily_minutes=MIN_ACTIVITY_MINUTES)
    planner.log_study(topic_id=EXCITABLE, activity="read", minutes=20)
    planner.replan(record["plan_id"], reason="okundu")

    summary = planner.summary(record["plan_id"])

    assert summary["fit"] is True and summary["overload"] is None
    assert len(summary["uncovered"]) == 1
    assert f"en kısa oturum {MIN_ACTIVITY_MINUTES} dk" in summary["uncovered_note"]
    # Work that only ran out of days keeps the overload wording to itself.
    fine, _store, _learning, _understanding, _clock = build()
    assert fine.summary(physiology_plan(fine, days=1, minutes=45)["plan_id"])["uncovered_note"] == ""


def test_the_half_of_a_split_still_owed_survives_the_replan_that_follows_it() -> None:
    """Coverage reads topics, not sessions, so the day after the first half is
    done the topic simply reads as studied. A second session shown to the
    student, done as asked and then deleted with its minutes neither planned
    nor reported is worse than one that was never offered."""
    planner, _store, _learning, _understanding, clock = build()
    record = physiology_plan(planner, minutes=10)
    plan_id = record["plan_id"]
    sessions = _chain(planner, plan_id)
    assert [item["estimate_minutes"] for item in sessions] == [10, 10] and sessions[0]["remaining_minutes"] == 10

    planner.start(sessions[0]["activity_id"])
    # A budget change on the same day must not bury the half still owed either.
    planner.replan(plan_id, reason="bütçe değişti")
    assert [item["estimate_minutes"] for item in _chain(planner, plan_id)] == [10, 10]
    planner.complete(sessions[0]["activity_id"], minutes=10)
    clock.advance(days=1)

    planner.today_view(plan_id)

    after = _chain(planner, plan_id)
    owed = [item for item in after if item["status"] == "planned"]
    assert [item["estimate_minutes"] for item in owed] == [10], "the second half is still planned"
    assert "Önceki oturumun devamı" in owed[0]["reason"]
    # The topic has been opened by now and the plan's own coverage says so, so
    # the sentence beside the session cannot still call it untouched.
    state = {row["topic_id"]: row["state"] for row in planner.coverage(plan_id)["topics"]}[owed[0]["topic_id"]]
    assert state == "studied_unassessed" and owed[0]["state"] == state
    assert owed[0]["reason"].startswith("Okundu ama hiç soru çözülmedi") and "hiç açılmadı" not in owed[0]["reason"]

    planner.start(owed[0]["activity_id"])
    planner.complete(owed[0]["activity_id"], minutes=10)
    clock.advance(days=1)
    planner.today_view(plan_id)

    assert not [item for item in _chain(planner, plan_id) if item["status"] in ("planned", "started")], "the chain ends by itself"


def test_a_misconception_and_a_due_review_come_first_with_the_prerequisite_named() -> None:
    planner, store, learning, understanding, clock = build()
    store.save_question(question("q1", EXCITABLE, "physiology.action_potential"))
    store.save_question(question("q2", EXCITABLE, "physiology.action_potential"))
    for identifier in ("q1", "q2"):
        understanding.record_event(store.get_question(identifier), correct=False, answer_key="A", confidence="sure")
    store.save_mastery(ConceptMastery("physiology.na_k_atpase", subject="physiology", attempts=4, correct=1, level=MasteryLevel.WEAK, next_review_at=clock.now.astimezone(timezone.utc) - timedelta(days=1)))
    record = physiology_plan(planner)

    summary = planner.replan(record["plan_id"])

    first = summary["days"][0]["activities"]
    assert first[0]["kind"] == "repair" and first[0]["topic_id"] == EXCITABLE and "yanlış anlama" in first[0]["reason"]
    kinds = [(item["topic_id"], item["kind"]) for day in summary["days"] for item in day["activities"]]
    assert (TRANSPORT, "review") in kinds
    prerequisite = next((item for day in summary["days"] for item in day["activities"] if item["kind"] == "prerequisite"), None)
    assert prerequisite is not None and prerequisite["concept_id"] in ("physiology.resting_membrane_potential",) and "ön koşuluna dayanabilir" in prerequisite["reason"]

    # Half a reading left over is work already begun, which is worth something
    # and worth less than a repair. Handed the day outright it would push the
    # misconception to tomorrow, and the student is told none of it.
    carried, store, _learning, understanding, clock = build()
    plan_id = physiology_plan(carried, minutes=10)["plan_id"]
    half = carried.activities(plan_id)[0]
    carried.start(half["activity_id"])
    carried.complete(half["activity_id"], minutes=10)
    clock.advance(days=1)
    for identifier in ("q3", "q4"):
        store.save_question(question(identifier, EXCITABLE, "physiology.action_potential"))
        understanding.record_event(store.get_question(identifier), correct=False, answer_key="A", confidence="sure")

    today = carried.replan(plan_id)["days"][0]["activities"]

    assert half["remaining_minutes"] == 10, "a half-session is in the pool"
    assert today[0]["kind"] == "repair" and today[0]["topic_id"] == EXCITABLE
    owed = [item for item in carried.activities(plan_id) if item["topic_id"] == half["topic_id"] and item["status"] == "planned"]
    assert [item["estimate_minutes"] for item in owed] == [10], "and it is still planned, only not first"


# ---------------------------------------------------------------------------
# days, dates and replanning
# ---------------------------------------------------------------------------


def test_a_missed_day_is_recorded_and_replanned_within_the_budget() -> None:
    planner, _store, _learning, _understanding, clock = build()
    record = physiology_plan(planner, minutes=30)
    before = planner.replan(record["plan_id"])
    day_one = [item["activity_id"] for item in before["days"][0]["activities"]]
    assert day_one

    clock.advance(days=1)
    after = planner.today_view(record["plan_id"])

    missed = [item for item in planner.activities(record["plan_id"]) if item["status"] == "missed"]
    assert {item["activity_id"] for item in missed} == set(day_one), "yesterday's untouched work is history, not a backlog"
    assert after["planned_minutes"] <= 30 and after["budget"] == 30
    summary = planner.summary(record["plan_id"])
    assert all(day["planned_minutes"] <= 30 for day in summary["days"])
    assert {item["topic_id"] for item in missed} <= {item["topic_id"] for day in summary["days"] for item in day["activities"]} | {item["topic_id"] for item in summary["uncovered"]}


def test_changing_the_exam_date_keeps_history_and_completed_work() -> None:
    planner, _store, _learning, _understanding, clock = build()
    record = physiology_plan(planner, days=6)
    summary = planner.replan(record["plan_id"])
    first = summary["days"][0]["activities"][0]
    planner.start(first["activity_id"])
    done = planner.complete(first["activity_id"], minutes=18)
    assert done["status"] == "completed" and done["actual_minutes"] == 18

    moved = planner.update(record["plan_id"], {"exam_date": (planner.today() + timedelta(days=20)).isoformat()})

    assert moved["days_left"] == 20 and any("sınav tarihi" in item["note"] for item in moved["history"])
    kept = [item for item in planner.activities(record["plan_id"]) if item["status"] == "completed"]
    assert [item["activity_id"] for item in kept] == [first["activity_id"]]
    assert all(day["planned_minutes"] <= 45 for day in moved["days"])
    with pytest.raises(ValueError):
        planner.update(record["plan_id"], {"exam_date": "yarın"})


def test_today_is_the_local_date_and_the_exam_date_has_no_clock() -> None:
    late = Clock(datetime(2026, 9, 8, 23, 30, tzinfo=timezone.utc))  # 02:30 on the 9th in Istanbul
    planner, _store, _learning, _understanding, _clock = build(clock=Clock(late.now.astimezone(ISTANBUL)))
    assert planner.today() == date(2026, 9, 9)
    record = planner.create("Komite 1", "2026-09-12", subjects=["physiology"], daily_minutes=30)
    assert planner.summary(record["plan_id"])["days_left"] == 3
    with pytest.raises(ValueError, match="geçmişte"):
        planner.create("Eski", "2026-09-08", subjects=["physiology"])
    utc_planner, _s, _l, _u, _c = build(clock=late)
    assert utc_planner.today() == date(2026, 9, 8), "a UTC clock is a UTC date"


def test_an_unavailable_day_gets_nothing_and_an_override_changes_one_day_only() -> None:
    planner, _store, _learning, _understanding, _clock = build()
    off = (planner.today() + timedelta(days=1)).isoformat()
    record = physiology_plan(planner, unavailable=[off])
    planner.update(record["plan_id"], {"overrides": {(planner.today() + timedelta(days=2)).isoformat(): 20}})

    summary = planner.summary(record["plan_id"])

    assert summary["days"][1]["budget"] == 0 and summary["days"][1]["activities"] == []
    assert summary["days"][2]["budget"] == 20 and summary["days"][2]["planned_minutes"] <= 20
    assert summary["days"][3]["budget"] == 45


# ---------------------------------------------------------------------------
# overload, inferred scope, estimates, states
# ---------------------------------------------------------------------------


def test_an_overloaded_scope_is_said_with_numbers_and_a_prioritised_smaller_plan() -> None:
    planner, _store, _learning, _understanding, _clock = build()
    record = planner.create("Yarın öbür gün", (planner.today() + timedelta(days=1)).isoformat(), subjects=["physiology", "biochemistry"], daily_minutes=30)

    summary = planner.replan(record["plan_id"])

    assert summary["fit"] is False and summary["overload"]["short_by"] > 0
    assert "sığmıyor" in summary["overload"]["message"] and summary["uncovered"]
    assert all(day["planned_minutes"] <= 30 for day in summary["days"])
    assert summary["planned"] >= 1 and summary["needed_minutes"] > summary["available_minutes"]
    view = planner.today_view(record["plan_id"])
    assert view["fit"] is False and view["uncovered_count"] == len(summary["uncovered"])


def test_a_scope_inferred_from_documents_waits_for_the_student() -> None:
    planner, store, _learning, _understanding, _clock = build()
    store.save_document(StudyDocument("d1", "Kas fizyolojisi", "kas.pdf", "sha", subject="physiology", topic_ids=[MUSCLE, EXCITABLE], page_count=30, status=DocumentStatus.READY))
    record = planner.create("Komite 2", (planner.today() + timedelta(days=9)).isoformat(), document_ids=["d1"], daily_minutes=40)

    assert record["scope_confirmed"] is False and record["proposed_scope"]["topic_ids"] == [MUSCLE, EXCITABLE]
    view = planner.today_view(record["plan_id"])
    assert view["activities"] == [] and "onayla" in view["message"] and view["proposed_scope"]["topic_ids"] == [MUSCLE, EXCITABLE]
    assert planner.replan(record["plan_id"])["planned"] == 0

    confirmed = planner.confirm_scope(record["plan_id"], topic_ids=[MUSCLE])

    assert confirmed["scope_confirmed"] is True and confirmed["scope"]["topic_ids"] == [MUSCLE] and confirmed["scope"]["document_ids"] == ["d1"]
    assert confirmed["coverage"]["total"] == 1 and confirmed["planned"] >= 1


def test_estimates_learn_from_real_minutes_and_states_stay_distinct() -> None:
    planner, _store, _learning, _understanding, clock = build()
    record = physiology_plan(planner)
    summary = planner.replan(record["plan_id"])
    first, second = summary["days"][0]["activities"][:2]
    assert first["estimate_minutes"] == DEFAULT_ESTIMATES["read"] and first["estimate_label"] == "tahmini"

    started = planner.start(first["activity_id"])
    assert started["status"] == "started" and started["started_at"]
    clock.advance(minutes=90)
    assert planner.activities(record["plan_id"], day=planner.today())[0]["status"] == "started", "time passing completes nothing"
    completed = planner.complete(first["activity_id"])
    assert completed["status"] == "completed" and completed["actual_minutes"] == 90
    skipped = planner.skip(second["activity_id"], "bugün değil")
    assert skipped["status"] == "skipped" and skipped["skip_note"] == "bugün değil"

    replanned = planner.replan(record["plan_id"], reason="test")
    read = next(item for day in replanned["days"] for item in day["activities"] if item["kind"] == "read" and item["status"] == "planned")
    # One 90-minute session pulls the 20-minute default to 55; a day holds 45, so the session is split.
    assert read["estimate_label"] == "tahmini (gözlemden: 1 kez)" and read["estimate_minutes"] == 45 and read["split"] is True
    assert "birkaç oturuma yayılır" in read["reason"]
    assert all(day["planned_minutes"] <= 45 for day in replanned["days"])
    statuses = {item["status"] for item in planner.activities(record["plan_id"])}
    assert {"completed", "skipped", "planned"} <= statuses
    view = planner.today_view(record["plan_id"])
    assert view["done_minutes"] == 90


def test_work_too_long_for_one_day_keeps_its_remainder_for_the_next() -> None:
    planner, _store, _learning, _understanding, _clock = build()
    record = physiology_plan(planner, minutes=10)  # a 20-minute reading against a 10-minute day

    activities = planner.activities(record["plan_id"])
    first = activities[0]
    rest = next(item for item in activities[1:] if item["topic_id"] == first["topic_id"] and item["kind"] == first["kind"])

    assert first["split"] is True and first["estimate_minutes"] == 10 and "birkaç oturuma yayılır" in first["reason"]
    # What did not fit today is the next session, not a silent loss: the two
    # sessions are the whole reading.
    assert rest["date"] > first["date"] and "devamı" in rest["reason"]
    assert first["estimate_minutes"] + rest["estimate_minutes"] == DEFAULT_ESTIMATES["read"]
    assert all(day["planned_minutes"] <= 10 for day in planner.summary(record["plan_id"])["days"])


def test_a_confirmed_plan_is_laid_out_the_moment_it_is_created() -> None:
    planner, _store, _learning, _understanding, _clock = build()
    record = physiology_plan(planner, minutes=40)

    summary = planner.summary(record["plan_id"])

    # The first summary already says what fits: the page reads it before "Bugün".
    assert summary["planned"] > 0 and summary["planned_minutes"] > 0
    assert summary["needed_minutes"] > 0 and isinstance(summary["fit"], bool)
    assert record.get("last_planned_at") is not None
    assert any("oluşturuldu" in item["note"] for item in record["history"])


def test_manual_work_survives_replanning_and_must_fit_the_day() -> None:
    planner, _store, _learning, _understanding, _clock = build()
    record = physiology_plan(planner, minutes=40)
    tomorrow = planner.today() + timedelta(days=1)

    manual = planner.add_manual(record["plan_id"], day=tomorrow, title="Hoca slaytları 1-20", minutes=25)
    with pytest.raises(ValueError, match="sığmıyor"):
        planner.add_manual(record["plan_id"], day=tomorrow, title="Fazla", minutes=30)
    summary = planner.replan(record["plan_id"], reason="test")

    day = next(item for item in summary["days"] if item["date"] == tomorrow.isoformat())
    assert any(item["activity_id"] == manual["activity_id"] and item["manual"] for item in day["activities"])
    assert day["planned_minutes"] <= 40


def test_a_daily_reminder_goes_out_once_when_enabled_and_everything_survives_a_restart(tmp_path) -> None:
    clock = Clock()
    planner, store, _learning, _understanding, _tick = build(tmp_path / "medical.sqlite3", clock)
    record = physiology_plan(planner, notify={"enabled": True, "time": "08:30"})

    planner.today_view(record["plan_id"])
    planner.today_view(record["plan_id"])

    assert len(planner.reminders) == 1 and planner.reminders[0][1] == "08:30" and "Komite 2" in planner.reminders[0][0]
    assert planner.plan(record["plan_id"])["reminders"][planner.today().isoformat()] == "rem-1"
    store.close()

    again, _s, _l, _u, _c = build(tmp_path / "medical.sqlite3", clock)
    assert again.plan(record["plan_id"])["name"] == "Komite 2"
    assert again.activities(record["plan_id"]) and again.today_view(record["plan_id"])["activities"]
    assert len(again.reminders) == 0, "the reminder for today was already sent by the earlier session"
