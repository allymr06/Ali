"""The mastery model may only claim what the recorded history shows."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.medical.concepts import ConceptGraph
from app.medical.learning import LearningEngine, top_confusions
from app.medical.models import Concept, Question, QuestionOption
from app.medical.store import MedicalStore

BASE = datetime(2026, 4, 1, 8, 0, tzinfo=timezone.utc)
STEM = "Humerus distal ucunda radius ile eklem yapan yapi hangisidir?"
EXPLANATION = "Capitulum, radius basi ile eklemlesen yuvarlak yapidir."
OPTIONS = ["Capitulum humeri", "Trochlea humeri", "Olecranon", "Acromion"]


class Clock:
    def __init__(self) -> None:
        self.now = BASE

    def __call__(self) -> datetime:
        return self.now

    def advance(self, days: float) -> None:
        self.now += timedelta(days=days)


class Breadcrumbs:
    def breadcrumb(self, topic_id: str) -> str:
        return {"anatomy": "Anatomi"}.get(topic_id, "")


def make_question(concept_ids: list[str]) -> Question:
    return Question(
        question_id="q1", subject="anatomy", stem=STEM, correct_key="A", explanation=EXPLANATION,
        options=[QuestionOption(key, text) for key, text in zip("ABCD", OPTIONS)],
        concept_ids=list(concept_ids),
    )


def build_engine(clock: Clock) -> tuple[LearningEngine, MedicalStore]:
    graph = ConceptGraph([Concept("c.x", "anatomy", "Capitulum humeri")])
    store = MedicalStore()
    return LearningEngine(store, Breadcrumbs(), graph, clock=clock), store


def hint_after(*chosen_keys: str) -> str:
    """The tutor-prompt hint after a run of wrong answers on those options."""
    engine, _store = build_engine(Clock())
    question = make_question(["c.x"])
    for key in chosen_keys:
        engine.record(question, False, chosen_key=key)
    return engine.hints_for(["c.x"])[0]


def test_the_confusion_hint_claims_a_habit_only_once_the_count_shows_one() -> None:
    # One wrong click on each of two options: a real event each, no pattern.
    tied = hint_after("C", "B")
    assert tied == "Capitulum humeri: weak (0/2), picked 'Olecranon' and 'Trochlea humeri' once each"
    assert "often" not in tied  # "often" after a single observation is an invented frequency

    # The order the mistakes were made in cannot change the line.
    assert hint_after("B", "C") == tied

    # A single distractor picked once is reported as the one event it was. The
    # second wrong answer carries no chosen key, the shape anatomy answers record.
    assert hint_after("C", "") == "Capitulum humeri: weak (0/2), picked 'Olecranon' once"

    # Three picks of the same option is a habit the count supports.
    assert hint_after("B", "D", "B", "B").endswith("often confused with 'Trochlea humeri'")

    # Two options each picked twice are both named; neither is singled out.
    assert hint_after("B", "B", "C", "C").endswith("often confused with 'Olecranon' and 'Trochlea humeri'")

    # No confusion recorded (a wrong answer with no chosen option) claims nothing.
    assert hint_after("", "") == "Capitulum humeri: weak (0/2)"


def test_top_confusions_breaks_ties_alphabetically_and_keeps_every_leader() -> None:
    assert top_confusions({}) == ([], 0)
    assert top_confusions({"Olecranon": 3, "Acromion": 1}) == (["Olecranon"], 3)
    assert top_confusions({"Olecranon": 2, "Acromion": 2}) == (["Acromion", "Olecranon"], 2)
    assert top_confusions({"Acromion": 2, "Olecranon": 2}) == (["Acromion", "Olecranon"], 2)  # insertion order is not evidence
    assert top_confusions({"C": 1, "B": 1, "A": 1}, limit=2) == (["A", "B"], 1)
    assert top_confusions({"C": 1, "B": 1}, limit=1) == (["B"], 1)


def test_due_reviews_counts_every_due_concept_not_one_capped_page() -> None:
    clock = Clock()
    engine, _store = build_engine(clock)
    for index in range(120):
        question = make_question([f"c.{index}"])
        engine.record(question, False, chosen_key="B")
        engine.record(question, False, chosen_key="B")  # two attempts make it weak, so it falls due after a day

    assert engine.summary()["due_reviews"] == 0  # nothing is due on the day it was answered
    clock.advance(3)
    assert len([item for item in engine.all() if item.next_review_at <= clock()]) == 120
    assert engine.summary()["due_reviews"] == 120  # the count is the whole backlog, not the queue's page size
    assert len(engine.review_queue(limit=100)) == 100  # the queue itself is still capped for display
