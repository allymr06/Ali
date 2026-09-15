"""Flashcards: every card from recorded material, a deterministic scheduler,
and reviews that count as study but never as measurement."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app.medical.academy import create_medical_academy
from app.medical.flashcards import DEFAULT_NEW_PER_DAY, FlashcardDeck, Scheduler
from app.medical.models import Question, QuestionOption, QuestionOrigin
from tests.test_medical_documents import make_pdf

ARM = "anatomy.musculoskeletal.upper_limb.arm"
BASE = datetime(2026, 9, 15, 9, 0, tzinfo=timezone.utc)


class Clock:
    def __init__(self) -> None:
        self.now = BASE

    def __call__(self) -> datetime:
        return self.now

    def advance(self, **delta) -> None:
        self.now += timedelta(**delta)


@pytest.fixture()
def academy(tmp_path):
    built = []

    def factory():
        settings = SimpleNamespace(medical_directory=str(tmp_path / f"medical{len(built)}"), medical_source_review=True)
        instance = create_medical_academy(settings=settings, provider_gateway=None)
        built.append(instance)
        return instance

    yield factory
    for instance in built:
        instance.close()


def deck_of(instance, clock: Clock | None = None) -> tuple[FlashcardDeck, Clock]:
    clock = clock or Clock()
    study = instance.study
    return FlashcardDeck(instance.store, instance.anatomy, instance.terminology, instance.curriculum, study.histology, instance.pipeline, planner=study.planner, clock=clock), clock


def question(question_id: str, *, origin: str = QuestionOrigin.MANUAL, key: str | None = "A") -> Question:
    options = [QuestionOption(k, t) for k, t in zip("ABCD", ["Capitulum humeri", "Trochlea humeri", "Olecranon", "Acromion"])]
    return Question(question_id=question_id, subject="anatomy", topic_id=ARM, stem=f"Soru {question_id}: radius başı ile eklemleşen yapı?", options=options, correct_key=key, explanation="Capitulum humeri radius başı ile eklemleşir.", origin=origin)


# ---------------------------------------------------------------------------
# the scheduler
# ---------------------------------------------------------------------------


def test_the_scheduler_walks_a_card_through_learning_review_and_a_lapse() -> None:
    now = BASE
    card = {"state": "new", "ease": 2.5, "interval_days": 0, "reps": 0, "lapses": 0}
    first = Scheduler.apply(card, "good", now)
    assert (first["state"], first["interval_days"], first["reps"]) == ("review", 1, 1)
    assert first["due_at"].startswith("2026-09-16")
    second = Scheduler.apply(first, "good", now + timedelta(days=1))
    assert second["interval_days"] == 3, "2.5 rounds half-up to 3"
    third = Scheduler.apply(second, "good", now + timedelta(days=4))
    assert third["interval_days"] == 8  # 3 * 2.5 = 7.5 -> 8
    lapsed = Scheduler.apply(third, "again", now + timedelta(days=12))
    assert (lapsed["state"], lapsed["interval_days"], lapsed["lapses"], lapsed["ease"]) == ("learning", 0, 1, 2.3)
    assert lapsed["due_at"] == (now + timedelta(days=12)).isoformat(), "a lapse is asked again today"
    relearned = Scheduler.apply(lapsed, "good", now + timedelta(days=12))
    assert (relearned["state"], relearned["interval_days"]) == ("review", 1)


def test_ease_is_clamped_hard_shrinks_it_and_the_interval_never_passes_a_year() -> None:
    card = {"state": "review", "ease": 1.35, "interval_days": 10, "reps": 5, "lapses": 0}
    hard = Scheduler.apply(card, "hard", BASE)
    assert hard["ease"] == 1.3 and hard["interval_days"] == 12  # 10 * 1.2
    easy = {"state": "review", "ease": 2.95, "interval_days": 300, "reps": 9, "lapses": 0}
    grown = Scheduler.apply(easy, "easy", BASE)
    assert grown["ease"] == 3.0 and grown["interval_days"] == 365, "capped at a year"
    with pytest.raises(ValueError):
        Scheduler.apply(card, "perfect", BASE)


def test_previews_say_what_each_button_would_schedule() -> None:
    labels = Scheduler.preview({"state": "new", "ease": 2.5, "interval_days": 0, "reps": 0, "lapses": 0}, BASE)
    assert labels == {"again": "bugün", "hard": "bugün", "good": "1 gün", "easy": "3 gün"}
    long_run = Scheduler.preview({"state": "review", "ease": 2.5, "interval_days": 40, "reps": 6, "lapses": 0}, BASE)
    assert long_run["good"] == "3.3 ay" and long_run["again"] == "bugün"


# ---------------------------------------------------------------------------
# builders: recorded material only
# ---------------------------------------------------------------------------


def test_a_topic_builds_fact_and_terminology_cards_once_and_never_from_atlas_mirrors(academy) -> None:
    instance = academy()
    deck, _clock = deck_of(instance)
    report = deck.build_topic(ARM)
    assert report["added"] > 10 and report["existing"] == 0 and "Kol" in report["topic_label"]
    cards = deck.all()
    biceps = next(card for card in cards if card["source"] == "anatomy_fact" and "biceps" in card["front"].lower() and "innervasyonu" in card["front"])
    assert "musculocutaneus" in biceps["back"].lower()
    assert biceps["provenance"].startswith("Ders kartı: ")
    terms = [card for card in cards if card["source"] == "terminology"]
    assert terms and all(card["back"] for card in terms)
    landmark = next(card for card in cards if card["source"] == "landmark")
    assert landmark["front"].endswith("Latince adı?") and landmark["back"], "a landmark card answers with the Latin name"
    assert landmark["source_label"] == "İşaret noktası"
    again = deck.build_topic(ARM)
    assert again["added"] == 0 and again["existing"] == report["added"], "a rebuild finds the same ids"
    with pytest.raises(ValueError):
        deck.build_topic("no.such.topic")


def test_an_atlas_mirror_of_a_lesson_makes_no_duplicate_card(academy, monkeypatch) -> None:
    instance = academy()
    deck, _clock = deck_of(instance)
    lesson = next(structure for structure in instance.anatomy.all() if structure.topic_id and instance.curriculum.is_within(structure.topic_id, ARM) and structure.kind == "muscle")
    mirror = replace(lesson, structure_id="za_ffff00000000", canonical=lesson.canonical + " · sağ")
    monkeypatch.setattr(instance.anatomy, "all", lambda: [lesson, mirror])
    report = deck.build_topic(ARM)
    fronts = [card["front"] for card in deck.all() if card["source"] == "anatomy_fact"]
    # One set of fact cards for the lesson; the mirror contributed nothing
    # (the extra adds in the report are the lesson's terminology entries).
    assert len(fronts) == 5 and all("· sağ" not in front for front in fronts)
    assert report["added"] >= len(fronts)


def test_wrong_answers_become_cards_with_the_banks_own_key_and_nothing_unscored(academy) -> None:
    instance = academy()
    deck, _clock = deck_of(instance)
    from app.medical.questions import new_attempt, record_answer

    scored = question("q-wrong")
    unscored = question("q-study", origin=QuestionOrigin.GENERATED)
    right = question("q-right")
    for item in (scored, unscored, right):
        instance.store.save_question(item)
    exam = instance.exam_builder.build(instance.exam_config({"topic_ids": [ARM], "question_count": 3}), [scored, unscored, right])
    attempt = new_attempt(exam)
    record_answer(attempt, scored, "B")
    record_answer(attempt, unscored, "B")
    record_answer(attempt, right, "A")
    instance.store.save_attempt(attempt)

    report = deck.add_wrong_questions(scoring=instance.study.reviewer.decision)

    assert report == {"added": 1, "existing": 0, "skipped_unscored": 1, "wrong_seen": 2}
    card = next(card for card in deck.all() if card["source"] == "question")
    assert card["question_id"] == "q-wrong"
    assert card["front"].startswith("Soru q-wrong") and "A) Capitulum humeri" in card["front"]
    assert card["back"].startswith("A) Capitulum humeri") and "eklemleşir" in card["back"]
    assert deck.add_wrong_questions(scoring=instance.study.reviewer.decision)["added"] == 0


def imported_specimen(instance, tmp_path, *, label: str = "Hiyalin kıkırdak") -> dict:
    source = tmp_path / "histo.pdf"
    source.write_bytes(make_pdf([("Hiyalin kikirdak, HE boyasi", True)]))
    document, _created = instance.pipeline.import_file(source)
    instance.pipeline.process(document.document_id)
    return instance.study.histology.add_specimen(document.document_id, 1, {"x": 0.05, "y": 0.3, "w": 0.9, "h": 0.6}, label=label, basis="user_confirmed", features=["İzogen gruplar"])


def test_histology_cards_show_the_crop_and_answer_with_the_recorded_name(academy, tmp_path) -> None:
    instance = academy()
    deck, _clock = deck_of(instance)
    specimen = imported_specimen(instance, tmp_path)
    report = deck.add_histology()
    assert report["added"] == 1
    card = next(card for card in deck.all() if card["source"] == "histology")
    assert card["front"] == "Bu doku ya da yapı nedir?" and card["back"].startswith("Hiyalin kıkırdak") and "İzogen gruplar" in card["back"]
    assert deck.payload(card)["has_image"] is True
    image = deck.front_image(card["card_id"])
    assert image[:8] == b"\x89PNG\r\n\x1a\n"
    instance.study.histology.update_specimen(specimen["specimen_id"], {"unreadable": True})
    assert deck.add_histology()["added"] == 0, "an unreadable specimen makes no card"


# ---------------------------------------------------------------------------
# image occlusion from the page's own text layer
# ---------------------------------------------------------------------------


def test_occlusion_masks_only_labels_the_page_prints_and_refuses_the_rest(academy, tmp_path) -> None:
    instance = academy()
    deck, _clock = deck_of(instance)
    source = tmp_path / "sekil.pdf"
    source.write_bytes(make_pdf([("Fossa olecrani", True), ("", True)]))
    document, _created = instance.pipeline.import_file(source)
    instance.pipeline.process(document.document_id)

    found = deck.occlusion_candidates(document.document_id, 1)
    assert [item["label"] for item in found["candidates"]] == ["Fossa olecrani"]
    assert all(0 <= value <= 1 for box in found["candidates"][0]["boxes"] for value in box)

    report = deck.add_occlusion(document.document_id, 1, None, ["Fossa olecrani", "Uydurma etiket"])
    assert report["added"] == 1 and report["missing"] == ["Uydurma etiket"], "a label the page does not print is refused"
    card = next(card for card in deck.all() if card["source"] == "occlusion")
    assert card["back"] == "Fossa olecrani" and "s. 1" in card["provenance"]

    masked = deck.front_image(card["card_id"])
    plain = instance.pipeline.render_region(document.document_id, 1, (0.0, 0.0, 1.0, 1.0))
    assert masked[:8] == b"\x89PNG\r\n\x1a\n" and masked != plain, "the front covers the label"
    assert deck.front_image(card["card_id"]) == masked, "rendered once, cached"

    empty = deck.occlusion_candidates(document.document_id, 2)
    assert empty["candidates"] == [] and "metin katmanında" in empty["reason"]
    with pytest.raises(ValueError):
        deck.occlusion_candidates(document.document_id, 1, {"x": 0.5, "y": 0.5, "w": 0.9, "h": 0.9})


# ---------------------------------------------------------------------------
# the queue, an answer, and what an answer may never touch
# ---------------------------------------------------------------------------


def test_the_queue_orders_due_before_new_respects_the_daily_budget_and_skips_suspended(academy) -> None:
    instance = academy()
    deck, clock = deck_of(instance)
    deck.build_topic(ARM)
    deck.update_settings({"new_per_day": 3})
    first = deck.queue()
    assert len(first["cards"]) == 3 and all(card["state"] == "new" for card in first["cards"])
    assert first["new_budget"] == 3 and first["settings"]["new_per_day"] == 3
    graded = [deck.answer(card["card_id"], "good")["card"]["card_id"] for card in first["cards"]]
    assert deck.queue()["new_budget"] == 0, "three new cards were seen today"
    assert deck.queue()["cards"] == [], "nothing due, no budget left"
    clock.advance(days=1)
    tomorrow = deck.queue()
    assert [card["card_id"] for card in tomorrow["cards"][:3]] == graded, "yesterday's cards are due first"
    assert {card["state"] for card in tomorrow["cards"][:3]} == {"review"}
    assert tomorrow["cards"][3]["state"] == "new", "then the day's new budget"
    suspended_id = tomorrow["cards"][0]["card_id"]
    deck.suspend(suspended_id)
    assert all(card["card_id"] != suspended_id for card in deck.queue()["cards"])
    restored = deck.suspend(suspended_id, False)["card"]
    assert restored["state"] == "review", "unsuspending restores the state it had"


def test_an_again_card_returns_the_same_day_and_an_answer_is_recorded_once(academy) -> None:
    instance = academy()
    deck, clock = deck_of(instance)
    deck.build_topic(ARM)
    card = deck.queue()["cards"][0]
    answered = deck.answer(card["card_id"], "again", submission_id="s1")
    assert answered["repeated"] is False and answered["card"]["state"] == "learning"
    assert any(item["card_id"] == card["card_id"] for item in deck.queue()["cards"]), "an 'again' card is asked again today"
    repeat = deck.answer(card["card_id"], "good", submission_id="s1")
    assert repeat["repeated"] is True and repeat["card"]["state"] == "learning", "a retried send grades nothing twice"
    assert deck.queue(limit=60)["reviewed_today"] == 1


def test_reviews_never_move_mastery_findings_or_attempts(academy) -> None:
    instance = academy()
    deck, _clock = deck_of(instance)
    deck.build_topic(ARM)
    for card in deck.queue()["cards"][:5]:
        deck.answer(card["card_id"], "again")
    assert instance.store.list_mastery() == [], "self-graded cards are not measurement"
    assert instance.study.understanding.events() == []
    assert instance.store.list_attempts() == []


def test_forecast_counts_the_backlog_today_and_each_days_own_load_later(academy) -> None:
    instance = academy()
    deck, clock = deck_of(instance)
    deck.build_topic(ARM)
    cards = deck.queue()["cards"][:4]
    deck.answer(cards[0]["card_id"], "good")   # due +1d
    deck.answer(cards[1]["card_id"], "easy")   # due +3d
    deck.answer(cards[2]["card_id"], "easy")   # due +3d
    clock.advance(days=2)
    forecast = deck.forecast(days=3)
    assert forecast[0]["due"] == 1, "yesterday's one-day card is backlog today"
    assert forecast[1]["due"] == 2, "the two easy cards land tomorrow"
    assert forecast[2]["due"] == 0


def test_the_overview_counts_sources_and_deletion_is_confirmed_and_complete(academy, tmp_path) -> None:
    instance = academy()
    deck, _clock = deck_of(instance)
    deck.build_topic(ARM)
    imported_specimen(instance, tmp_path)
    deck.add_histology()
    overview = deck.overview()
    assert overview["total"] == len(deck.all()) and overview["empty_state"] == ""
    sources = {row["source"]: row["count"] for row in overview["by_source"]}
    assert sources["anatomy_fact"] > 0 and sources["histology"] == 1
    card = next(card for card in deck.all() if card["source"] == "histology")
    assert deck.delete(card["card_id"]) is True and deck.get(card["card_id"]) is None
    assert "cards_delete" in instance.study.CONFIRMED_ACTIONS, "deletion asks first at the bridge"


def test_the_workflow_actions_answer_build_and_report_through_the_facade(academy) -> None:
    instance = academy()
    study = instance.study
    report = study.call("cards_build_topic", {"topic_id": ARM})
    assert report["added"] > 0
    queue = study.call("cards_queue", {"limit": 5})
    assert queue["cards"] and queue["cards"][0]["previews"]["good"] == "1 gün"
    first = queue["cards"][0]
    answered = study.call("cards_answer", {"card_id": first["card_id"], "grade": "good", "submission_id": "x1"})
    assert answered["card"]["interval_days"] == 1 and answered["review"]["grade_label"] == "İyi"
    overview = study.call("cards_overview", {})
    assert overview["reviewed_today"] == 1 and overview["settings"]["new_per_day"] == DEFAULT_NEW_PER_DAY
    assert study.call("cards_settings", {"fields": {"new_per_day": 5}})["settings"]["new_per_day"] == 5
    block = study.dashboard_block()
    assert "cards_due" in block and "cards_new" in block
