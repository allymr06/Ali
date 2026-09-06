"""Question quality, grading, similarity and interpretable mastery."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.medical.concepts import ConceptGraph
from app.medical.learning import RECENT_WINDOW, LearningEngine, level_for, next_review_for, reason_for
from app.medical.models import (
    Concept, ConceptMastery, Exam, ExamAttempt, ExamConfig, MasteryLevel,
    Question, QuestionAttempt, QuestionOption, QuestionOrigin, QuestionType, SourceReference,
)
from app.medical.questions import (
    SIMILARITY_THRESHOLD, analyse_attempt, build_question, grade, is_too_similar,
    most_similar, shuffle_options, validate_question,
)
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
    """Naming a topic is all the analysis and the engine ask of a curriculum."""

    def breadcrumb(self, topic_id: str) -> str:
        return {"anatomy.joints": "Anatomi › Eklemler", "anatomy": "Anatomi"}.get(topic_id, "")


def make_question(question_id: str = "q1", **overrides) -> Question:
    """A four-option item that validates cleanly before any override."""
    texts = overrides.pop("texts", OPTIONS)
    fields = {"subject": "anatomy", "stem": STEM, "correct_key": "A", "explanation": EXPLANATION,
              "options": [QuestionOption(key, text) for key, text in zip("ABCD", texts)]}
    return Question(question_id=question_id, **{**fields, **overrides})


def problems(**overrides) -> list[str]:
    return validate_question(make_question(**overrides))


def build(raw: dict, **kwargs) -> Question:
    kwargs.setdefault("difficulty", 3)
    return build_question(raw, subject="anatomy", topic_id=kwargs.pop("topic_id", None), **kwargs)


def mastery(**overrides) -> ConceptMastery:
    return ConceptMastery(concept_id="c.eklem", **overrides)


def build_engine(clock: Clock) -> tuple[LearningEngine, MedicalStore]:
    graph = ConceptGraph([Concept("c.eklem", "anatomy", "Eklem tipleri"), Concept("c.kemik", "anatomy", "Kemik")])
    store = MedicalStore()
    return LearningEngine(store, Breadcrumbs(), graph, clock=clock), store


# ---------------------------------------------------------------------------
# validation
# ---------------------------------------------------------------------------


def test_validation_names_every_documented_defect_and_passes_a_clean_item() -> None:
    assert validate_question(make_question(), expected_options=4) == []

    broken = make_question(
        stem="Kisa bir govde",
        options=[QuestionOption("A", "Sinovyal eklem"), QuestionOption("C", "sinovyal   eklem"), QuestionOption("D", "Hiçbiri")],
        correct_key="Z", explanation="Cok kisa.", difficulty=9,
    )
    assert set(validate_question(broken, expected_options=4)) == {
        "stem_too_short", "option_count_3_expected_4", "option_keys_not_sequential", "duplicate_options",
        "all_or_none_option", "answer_key_not_an_option", "explanation_missing", "difficulty_out_of_range",
    }
    assert problems(correct_key=None) == ["missing_answer_key"]
    assert "too_few_options" in problems(options=[QuestionOption("A", "Tek secenek")])
    assert "empty_option" in problems(texts=["Capitulum", "  ", "Olecranon", "Acromion"])

    # Boundaries: one character either side of each minimum, and both ends of the scale.
    assert "stem_too_short" in problems(stem="Kisa bir govde")
    assert "stem_too_short" not in problems(stem="Kisa bir govdem")
    assert "explanation_missing" in problems(explanation="x" * 19)
    assert "explanation_missing" not in problems(explanation="x" * 20)
    assert [problems(difficulty=value) for value in (0, 6)] == [["difficulty_out_of_range"]] * 2
    assert problems(difficulty=1) == problems(difficulty=5) == []

    # The two relaxations are opt-in, never assumed.
    bare, catch_all = make_question(explanation=""), make_question(texts=OPTIONS[:3] + ["Yukarıdakilerin hepsi"])
    assert validate_question(bare) == ["explanation_missing"] and validate_question(bare, require_explanation=False) == []
    assert validate_question(catch_all) == ["all_or_none_option"]
    assert validate_question(catch_all, allow_all_of_the_above=True) == []

    # An answer the student could guess without knowing anything.
    verbose = "Capitulum humeri, radius basi ile eklemlesen ve fleksiyon sirasinda yuk tasiyan yuvarlak yapidir"
    assert problems(texts=[verbose, "Trochlea", "Olecranon", "Acromion"]) == ["correct_option_longest"]
    assert problems(stem="Capitulum humeri hangi kemikte yer alir ve ne ile eklem yapar?") == ["stem_contains_answer"]
    # A long-but-not-outlying answer is not punished for being informative.
    assert problems(texts=[
        "Capitulum humeri radius basi ile eklemlesen yuvarlak yapidir",
        "Trochlea humeri ulna incisurasi ile eklemlesen makara yapidir",
        "Olecranon ulna proksimalindeki genis ve belirgin cikintidir",
        "Acromion scapula spinasinin lateral uzantisini olusturur",
    ]) == []


def test_the_catch_all_and_letter_referencing_forms_a_turkish_model_actually_writes() -> None:
    def last_option(text: str, **overrides) -> list[str]:
        return validate_question(make_question(texts=OPTIONS[:3] + [text], **overrides), expected_options=4)

    # "all/none of the above" in the spellings the prompt's own wording invites.
    for text in ("Yukarıdakilerin tümü", "Yukarıdakilerin tamamı", "Hiçbir şık doğru değildir",
                 "Hiçbir seçenek doğru değildir", "Her ikisi de doğrudur", "Aşağıdakilerin hiçbiri"):
        assert last_option(text) == ["all_or_none_option"], text

    # An option that names other options by letter: shuffle_options re-letters the paper
    # around it, so its stated answer becomes a sentence about the wrong options.
    for text in ("B ve C", "A ve C", "A ve B doğrudur", "Yalnız A ve B", "A, B ve C"):
        assert last_option(text) == ["option_references_another_option"], text
    # It is broken by construction, so the all-of-the-above relaxation does not reach it.
    letter_pair = make_question(texts=OPTIONS[:3] + ["B ve C"])
    assert validate_question(letter_pair, allow_all_of_the_above=True) == ["option_references_another_option"]

    # A professor's own imported question keeps its letters and is never re-lettered.
    imported = make_question(texts=OPTIONS[:3] + ["B ve C"], origin=QuestionOrigin.IMPORTED_EXAM)
    assert validate_question(imported, expected_options=4) == []

    # The other direction: real answers that merely contain a letter stay valid.
    for texts in (["Vitamin A ve B12 eksikliği", "Demir eksikliği", "Folat eksikliği", "Çinko eksikliği"],
                  ["A ve D vitaminleri", "Demir", "Folat", "Çinko"],
                  ["Hepsidin", "Ferritin", "Transferrin", "Seruloplazmin"],
                  ["Hiçbir kas buraya tutunmaz", "M. deltoideus", "M. biceps brachii", "M. triceps brachii"]):
        assert validate_question(make_question(texts=texts), expected_options=4) == [], texts


# ---------------------------------------------------------------------------
# building
# ---------------------------------------------------------------------------


def test_build_question_reletters_options_but_keeps_the_answer_on_its_text() -> None:
    built = build(
        {
            "stem": "  Humerus distal   ucunda radius ile eklem yapan yapi hangisidir? ",
            "options": [
                {"key": "C", "text": "Trochlea  humeri"},
                {"key": "A", "text": "Capitulum humeri", "concept": "capitulum", "explanation": " Radius ile  eklem yapar. "},
                {"key": "B", "text": "Olecranon"},
                "bozuk kayit",
            ],
            "correct_key": "a", "explanation": EXPLANATION, "question_type": "uydurma_tip",
            "trap": "  Trochlea ile   karistirilir ", "topic": " Ust ekstremite ", "concept": " Capitulum  humeri ",
        },
        topic_id="anatomy.joints", concept_ids=["c.eklem"],
    )

    assert [option.key for option in built.options] == ["A", "B", "C"]  # junk dropped, letters resequenced
    assert [option.text for option in built.options] == ["Trochlea humeri", "Capitulum humeri", "Olecranon"]
    assert built.correct_key == "B"  # the answer followed its text, not its old letter
    assert built.option("B").explanation == "Radius ile eklem yapar."
    assert built.stem == STEM and built.concept_ids == ["c.eklem"] and built.topic_id == "anatomy.joints"
    assert built.question_type == QuestionType.SINGLE_BEST_ANSWER  # an invented type falls back
    assert built.metadata == {
        "trap": "Trochlea ile karistirilir", "topic_hint": "Ust ekstremite", "concept_name": "Capitulum humeri",
    }
    assert validate_question(built, expected_options=3) == []


def test_build_question_trims_to_the_option_count_and_clamps_difficulty() -> None:
    raw = {
        "stem": STEM, "correct_key": "E", "explanation": EXPLANATION,
        "options": [{"key": key, "text": text} for key, text in zip("ABCDE", ["bir", "iki", "uc", "dort", "Capitulum humeri"])],
    }
    trimmed = build(raw, option_count=4)

    assert [option.key for option in trimmed.options] == ["A", "B", "C", "D"]
    assert trimmed.option(trimmed.correct_key).text == "Capitulum humeri"  # the answer is never the one dropped
    assert "dort" not in [option.text for option in trimmed.options]
    assert len(build(raw, option_count=9).options) == 5  # a generous count never pads with invented options

    # A stated difficulty is clamped to 1..5; an unreadable or absent one leaves the request (3) standing.
    assert [build({**raw, "difficulty": value}).difficulty for value in (9, 4, -2, "zor", 0)] == [5, 4, 1, 3, 3]
    assert build(raw, difficulty=99).difficulty == 5


def test_trimming_keeps_the_given_order_and_holds_even_without_an_answer_key() -> None:
    texts = ["bir", "iki", "uc", "dort", "Capitulum humeri", "alti"]
    raw = {
        "stem": STEM, "correct_key": "E", "explanation": EXPLANATION,
        "options": [{"key": key, "text": text} for key, text in zip("ABCDEF", texts)],
    }

    trimmed = build(raw, option_count=4)
    # The tail goes; the answer keeps its place instead of being pulled to the front and re-lettered A.
    assert [option.text for option in trimmed.options] == ["bir", "iki", "uc", "Capitulum humeri"]
    assert trimmed.correct_key == "D"

    # The count is a promise to the student, not a favour granted only to drafts that named their answer.
    keyless = build({**raw, "correct_key": "Z"}, option_count=4)
    assert [option.text for option in keyless.options] == texts[:4]
    assert keyless.correct_key is None
    assert validate_question(keyless, expected_options=4) == ["missing_answer_key"]


def test_build_question_drops_options_past_the_last_letter_instead_of_keying_them_empty() -> None:
    texts = ["bir", "iki", "uc", "dort", "bes", "alti", "yedi", "sekiz"]
    overflowing = build({"stem": STEM, "correct_key": "C", "explanation": EXPLANATION,
                         "options": [{"text": text} for text in texts]})

    assert [option.key for option in overflowing.options] == list("ABCDEF")  # never an option with key ""
    assert [option.text for option in overflowing.options] == texts[:6]
    assert overflowing.option("C").text == "uc" and overflowing.correct_key == "C"
    assert validate_question(overflowing, expected_options=6) == []

    # A missing letter is filled from the option's place in the built list, so junk does not shift it.
    with_junk = build({"stem": STEM, "correct_key": "A", "explanation": EXPLANATION,
                       "options": ["bozuk kayit", {"text": "Capitulum humeri"}, {"text": "Trochlea humeri"}]})
    assert [option.key for option in with_junk.options] == ["A", "B"]
    assert with_junk.correct_key == "A"


def test_build_question_keeps_a_page_reference_only_when_it_was_supplied() -> None:
    raw = {"stem": STEM, "options": [{"key": "A", "text": "Capitulum"}, {"key": "B", "text": "Trochlea"}], "correct_key": "A"}
    references = [SourceReference("d1", 7, chunk_id="c7", quote="yedinci sayfa"),
                  SourceReference("d1", 12, chunk_id="c12", quote="onikinci sayfa")]

    def refs_of(**extra) -> list[SourceReference]:
        supplied = extra.pop("references", references)
        return build({**raw, **extra}, references=supplied, document_title="Atlas").references

    kept = refs_of(source_page=12, source_quote="  model   alintisi ")
    assert [(ref.page_number, ref.chunk_id) for ref in kept] == [(12, "c12")]
    assert kept[0].quote == "model alintisi" and kept[0].title == "Atlas"

    assert refs_of(source_page=99) == []  # a page nobody supplied is dropped, never invented
    assert refs_of(source_page=0) == [] and refs_of(source_page="sayfa yok") == []
    assert refs_of() == []  # silence about the page means no citation at all
    assert refs_of(source_page=12, references=[]) == []


def test_a_page_two_documents_share_is_cited_by_its_excerpt_index_or_not_at_all() -> None:
    """Page 12 of the slides and page 12 of the lecture note are different material."""
    raw = {"stem": STEM, "options": [{"key": "A", "text": "M. deltoideus"}, {"key": "B", "text": "M. biceps brachii"}],
           "correct_key": "A", "explanation": EXPLANATION}
    lecture = SourceReference("d1", 12, chunk_id="d1-c1", quote="capitulum humeri", title="Ders Notu")
    slides = SourceReference("d2", 12, chunk_id="d2-c1", quote="tuberositas deltoidea", title="Hoca Slaytlari")

    def cited(**extra) -> list[tuple[str, int, str | None]]:
        built = build(
            {**raw, **extra}, references=[lecture, slides], origin=QuestionOrigin.LECTURE_DERIVED, document_title="Ders Notu",
        )
        return [(ref.document_id, ref.page_number, ref.chunk_id) for ref in built.references]

    # The question came from the slides; only the [Kaynak N] index can say so.
    assert cited(source_index=2, source_page=12) == [("d2", 12, "d2-c1")]
    assert cited(source_index=1, source_page=12) == [("d1", 12, "d1-c1")]
    # Index and page are two independent claims about the same excerpt, and the
    # prompt asks for both. One of them alone is one unverified claim, so it cites
    # nothing: the student loses a chip rather than following a wrong one.
    assert cited(source_index=2) == []

    # A page alone cannot choose between two documents, so nothing is cited at all:
    # a missing citation is honest where a chip opening the wrong page is not.
    assert cited(source_page=12) == []
    assert cited(source_index=2, source_page=7) == []  # the model contradicted itself
    assert cited(source_index=9, source_page=12) == []  # an excerpt that was never sent


def test_a_lecture_badge_is_dropped_together_with_the_page_it_could_not_be_verified_against() -> None:
    raw = {"stem": STEM, "options": [{"key": "A", "text": "Capitulum"}, {"key": "B", "text": "Trochlea"}],
           "correct_key": "A", "explanation": EXPLANATION}
    references = [SourceReference("d1", 12, chunk_id="c12", quote="onikinci sayfa")]

    def origin_of(**extra) -> tuple[str, int]:
        built = build({**raw, **extra}, references=references, origin=QuestionOrigin.LECTURE_DERIVED)
        return built.origin, len(built.references)

    assert origin_of(source_page=12) == (QuestionOrigin.LECTURE_DERIVED, 1)
    # An invented page leaves nothing to check, so the item may not keep the badge that
    # tells the student it came from their own lecture notes.
    assert origin_of(source_page=777) == (QuestionOrigin.GENERATED, 0)
    assert origin_of() == (QuestionOrigin.GENERATED, 0)
    # An imported professor question has no page of ours and must keep its own origin.
    imported = build(raw, references=references, origin=QuestionOrigin.IMPORTED_EXAM)
    assert imported.origin == QuestionOrigin.IMPORTED_EXAM and imported.references == []


# ---------------------------------------------------------------------------
# shuffling, grading and similarity
# ---------------------------------------------------------------------------


def test_shuffling_is_seeded_and_the_answer_key_follows_its_text() -> None:
    first, second = shuffle_options(make_question(), seed="deneme"), shuffle_options(make_question(), seed="deneme")
    texts = lambda item: [option.text for option in item.options]  # noqa: E731

    assert texts(first) == texts(second) and first.correct_key == second.correct_key
    assert [option.key for option in first.options] == ["A", "B", "C", "D"]
    assert first.option(first.correct_key).text == "Capitulum humeri"
    assert sorted(texts(first)) == sorted(OPTIONS)  # a shuffle loses nothing and adds nothing
    assert texts(shuffle_options(make_question(), seed="baska")) != texts(first)
    # The point of shuffling: the answer must not sit on A every time.
    assert len({shuffle_options(make_question(), seed=f"s{index}").correct_key for index in range(12)}) > 1

    keyless = make_question(correct_key=None)
    assert shuffle_options(keyless, seed="deneme") is keyless
    assert [option.key for option in keyless.options] == ["A", "B", "C", "D"]

    assert grade(first, first.correct_key.lower()) is True and grade(first, f"  {first.correct_key}  ") is True
    assert grade(first, "Z") is False
    assert grade(first, None) is False  # not answering is wrong, but it is still a verdict
    assert grade(keyless, "A") is None  # no key, no verdict


def test_a_reworded_copy_is_flagged_while_a_new_item_is_cleared() -> None:
    original = make_question("o1")
    reworded = make_question("r1", stem="Radius ile eklem yapan humerus distal ucundaki yapi hangisidir?",
                             texts=["Capitulum humeri", "Olecranon", "Acromion", "Trochlea humeri"])
    fresh = make_question("n1", stem="Karaciger safra uretimini hangi hucre tipinde gerceklestirir?",
                          texts=["Hepatosit", "Kupffer hucresi", "Ito hucresi", "Kolanjiyosit"])

    flagged, score, other_id = is_too_similar(reworded, [original, fresh])
    assert flagged is True and other_id == "o1" and score >= SIMILARITY_THRESHOLD

    # The same reworded stem with a different answer text is a genuinely new item.
    moved = make_question("r2", stem=reworded.stem, texts=["Olecranon", "Capitulum humeri", "Acromion", "Trochlea"])
    cleared, plain_score, _ = is_too_similar(moved, [original, fresh])
    assert cleared is False and plain_score < SIMILARITY_THRESHOLD < score
    assert is_too_similar(moved, [original], threshold=0.3)[0] is True  # the caller may be stricter

    assert most_similar(fresh, [original, reworded]) == (0.0, None)
    assert most_similar(original, [original]) == (0.0, None)  # an item is never its own duplicate
    assert most_similar(original, []) == (0.0, None)


def test_a_copy_that_differs_only_by_turkish_inflection_is_still_a_copy() -> None:
    """Agglutination changes every word form, so raw tokens barely overlap."""
    proximal = ["Tuberculum majus", "Capitulum humeri", "Olecranon", "Condylus medialis"]
    stored = make_question("s1", stem="Humerus proksimal ucunda hangi yapi bulunur?", texts=proximal)
    reinflected = make_question("s2", stem="Humerusun proksimal ucunda hangi yapilar bulunmaktadir?", texts=proximal)

    flagged, score, other_id = is_too_similar(reinflected, [stored])

    assert flagged is True and other_id == "s1" and score >= SIMILARITY_THRESHOLD

    # Stemming is only allowed to speak for items that already share an answer text; with a
    # different answer the item is judged on the unchanged general similarity instead.
    different_answer = make_question("s3", stem=reinflected.stem, texts=proximal, correct_key="C")
    assert most_similar(different_answer, [stored])[0] < SIMILARITY_THRESHOLD <= score
    # And two questions on unrelated facts stay far apart however they are inflected.
    unrelated = make_question("s5", stem="Karacigerin safra uretimini hangi hucreler yapmaktadir?",
                              texts=["Hepatosit", "Kupffer hucresi", "Ito hucresi", "Kolanjiyosit"])
    assert is_too_similar(unrelated, [stored])[0] is False


# ---------------------------------------------------------------------------
# exam analysis
# ---------------------------------------------------------------------------


def exam_question(question_id: str, topic_id: str | None, correct_key: str | None, **overrides) -> Question:
    return make_question(question_id, stem=f"Soru {question_id}", topic_id=topic_id, correct_key=correct_key, **overrides)


def test_analysis_scores_only_gradable_questions_and_lists_what_to_review() -> None:
    questions = [
        exam_question("q1", "anatomy.joints", "A", difficulty=1, concept_ids=["c.eklem"]),
        exam_question("q2", "anatomy.joints", "A", difficulty=1, concept_ids=["c.eklem"]),
        exam_question("q3", "anatomy", "B", difficulty=5, subject="histology"),  # graded against its own key
        exam_question("q4", "anatomy", "A", difficulty=5),
        exam_question("q5", "anatomy", None, difficulty=5),  # no answer key at all
        exam_question("q6", None, "A", difficulty=3),  # no topic
    ]
    exam = Exam("e1", "Deneme", ExamConfig(difficulty=4), [item.question_id for item in questions] + ["silinmis"])
    attempt = ExamAttempt("a1", "e1", started_at=BASE, finished_at=BASE + timedelta(minutes=5))
    attempt.answers = {
        "q1": QuestionAttempt("q1", "A", True), "q2": QuestionAttempt("q2", "B", False),
        "q3": QuestionAttempt("q3", "B", True, flagged=True), "q4": QuestionAttempt("q4", None, None),
        "q5": QuestionAttempt("q5", "A", None),
    }

    report = analyse_attempt(exam, questions, attempt)

    assert (report["total"], report["correct"], report["incorrect"], report["unanswered"]) == (5, 2, 1, 2)
    assert report["score"] == 0.4 and report["percent"] == 40
    assert report["ungradable"] == 1  # q5 has no key; the deleted id is not counted as a question at all
    # Review = wrong, then flagged, then unanswered; each listed once, the ungradable one never.
    assert report["review_question_ids"] == ["q2", "q3", "q4", "q6"] and report["wrong_question_ids"] == ["q2"]
    assert [(row["key"], row["correct"], row["total"]) for row in report["by_subject"]] == [("anatomy", 1, 4), ("histology", 1, 1)]
    assert [(row["key"], row["label"]) for row in report["by_difficulty"]] == [("3", "Orta"), ("1", "Kolay"), ("5", "Zor")]
    assert report["by_topic"][0]["label"] == "Konu belirsiz"  # no curriculum, no invented breadcrumb
    assert report["elapsed_seconds"] == 300.0 and report["finished_at"] == "2026-04-01T08:05:00+00:00"

    empty = analyse_attempt(Exam("e2", "Bos", ExamConfig(), []), [], ExamAttempt("a2", "e2"))
    assert empty["score"] is None and empty["percent"] is None and empty["suggestion"] is None
    assert empty["elapsed_seconds"] is None


def test_analysis_names_the_weak_topic_and_only_promotes_a_strong_result() -> None:
    questions = [
        exam_question(f"q{index}", topic, "A", concept_ids=[concept], metadata={"concept_name": name})
        for index, (topic, concept, name) in enumerate(
            [("anatomy.joints", "c.eklem", "Eklem tipleri")] * 2 + [("anatomy", "c.kemik", "Kemik")] * 2, start=1
        )
    ]
    exam = Exam("e1", "Deneme", ExamConfig(difficulty=2), [item.question_id for item in questions])
    attempt = ExamAttempt("a1", "e1", started_at=BASE)
    attempt.answers = {item.question_id: QuestionAttempt(item.question_id, key, None) for item, key in zip(questions, "BBAA")}

    report = analyse_attempt(exam, questions, attempt, curriculum=Breadcrumbs(), mastery_levels={"c.eklem": "weak"})
    assert report["suggestion"] == {
        "kind": "weak_topic", "topic_id": "anatomy.joints", "label": "Anatomi › Eklemler",
        "text": "Sıradaki çalışma: Anatomi › Eklemler konusunu tekrar et, ardından bu konudan zorluk 2 ile yeni bir test çöz.",
    }
    assert [(item["concept_id"], item["accuracy"], item["mastery"]) for item in report["weak_concepts"]] == [("c.eklem", 0.0, "weak")]
    assert [(item["label"], item["question_ids"]) for item in report["strong_concepts"]] == [("Kemik", ["q3", "q4"])]

    perfect = ExamAttempt("a2", "e1", started_at=BASE)
    perfect.answers = {item.question_id: QuestionAttempt(item.question_id, "A", True) for item in questions}
    promoted = analyse_attempt(exam, questions, perfect, curriculum=Breadcrumbs())
    assert promoted["score"] == 1.0
    assert promoted["suggestion"] == {"kind": "harder", "text": "Sonuç güçlü: bir sonraki testte zorluğu bir kademe artırabilirsin."}


# ---------------------------------------------------------------------------
# mastery and the learning engine
# ---------------------------------------------------------------------------


def test_level_review_interval_and_reason_follow_recent_accuracy() -> None:
    def level(recent: list[bool], attempts: int = 0) -> str:
        return level_for(mastery(attempts=attempts or len(recent), correct=sum(recent), recent=recent))

    assert level([True]) == MasteryLevel.UNKNOWN  # one attempt is not evidence
    assert level([True, True]) == MasteryLevel.MODERATE  # two right is not yet strong
    assert level([True] * 3) == MasteryLevel.STRONG
    assert level([True, False, True, False]) == MasteryLevel.MODERATE  # exactly half is not weak
    assert level([True, False, False, False]) == MasteryLevel.WEAK
    assert level([True] * 4 + [False]) == MasteryLevel.STRONG  # exactly 0.8 clears the bar
    # Old failures stop counting once they fall out of the recent window.
    assert level([False] * 8 + [True] * RECENT_WINDOW, attempts=16) == MasteryLevel.STRONG

    def interval(**overrides) -> timedelta:
        return next_review_for(mastery(**overrides), BASE) - BASE

    assert interval() == timedelta(days=1) and interval(level=MasteryLevel.WEAK) == timedelta(days=1)
    assert interval(level=MasteryLevel.MODERATE) == timedelta(days=3)
    assert interval(level=MasteryLevel.STRONG) == timedelta(days=7)
    assert interval(level=MasteryLevel.STRONG, streak=4) == timedelta(days=21)
    assert interval(level=MasteryLevel.STRONG, streak=40) == timedelta(days=30)  # capped, never forgotten

    assert reason_for(mastery()) == "Bilinmiyor: yalnızca 0 deneme var."
    assert reason_for(mastery(attempts=4, correct=1, recent=[True, False, False, False], level=MasteryLevel.WEAK)) == (
        "Zayıf: 4 denemede 1 doğru (son 4 denemede 1 doğru)."
    )
    assert "aralıklı tekrar" in reason_for(mastery(attempts=5, correct=5, recent=[True] * 5, streak=5, level=MasteryLevel.STRONG))


def test_record_updates_attempts_recent_window_streak_and_confusions() -> None:
    clock = Clock()
    engine, store = build_engine(clock)
    question = make_question(topic_id="anatomy.joints", concept_ids=["c.eklem", "c.kemik"],
                             texts=["Sinovyal eklem", "Kikirdak eklem", "Fibroz eklem", "Sinostoz"])
    question.options[1].concept = "Kikirdak eklem"

    updated = engine.record(question, False, chosen_key="B")
    assert [item.concept_id for item in updated] == ["c.eklem", "c.kemik"]  # every concept of the item moves
    first = store.get_mastery("c.eklem")
    assert (first.attempts, first.correct, first.streak) == (1, 0, 0)
    assert first.confusions == {"Kikirdak eklem": 1}  # the option's concept, not its letter
    assert first.subject == "anatomy" and first.level == MasteryLevel.UNKNOWN
    assert first.last_attempt_at == BASE and first.next_review_at == BASE + timedelta(days=1)

    engine.record(question, False, chosen_key="B")
    weak = store.get_mastery("c.eklem")
    assert weak.level == MasteryLevel.WEAK and weak.confusions == {"Kikirdak eklem": 2}
    assert weak.recent == [False, False] and weak.reason.startswith("Zayıf:")

    for _ in range(RECENT_WINDOW):
        engine.record(question, True, chosen_key="A")
    grown = store.get_mastery("c.eklem")
    assert (grown.attempts, grown.correct, grown.streak) == (10, 8, 8)
    assert grown.recent == [True] * RECENT_WINDOW  # the window keeps only the last eight
    assert grown.level == MasteryLevel.STRONG and grown.confusions == {"Kikirdak eklem": 2}
    assert grown.next_review_at == BASE + timedelta(days=30)
    assert engine.levels() == {"c.eklem": MasteryLevel.STRONG, "c.kemik": MasteryLevel.STRONG}

    # Without concept ids mastery is still tracked, against the topic.
    assert LearningEngine.concept_ids_for(make_question(topic_id="anatomy.joints")) == ["topic:anatomy.joints"]
    assert LearningEngine.concept_ids_for(make_question(topic_id=None)) == ["topic:anatomy"]
    assert engine.concept_name("topic:anatomy.joints") == "Anatomi › Eklemler"
    assert engine.concept_name("topic:bilinmeyen") == "bilinmeyen"  # no breadcrumb is invented
    assert engine.concept_name("c.yok", fallback="Adsız") == "Adsız"


def test_review_queue_returns_only_due_concepts_and_says_why() -> None:
    clock = Clock()
    engine, _store = build_engine(clock)
    weak_item, moderate_item = make_question(concept_ids=["c.eklem"]), make_question(concept_ids=["c.kemik"])
    for correct, item in ((False, weak_item), (False, weak_item), (True, moderate_item), (True, moderate_item)):
        engine.record(item, correct, chosen_key="B")

    assert engine.review_queue() == []  # nothing is due on the day it was answered
    assert engine.summary() == {
        "concepts": 2, "attempts": 4, "correct": 2, "accuracy": 0.5,
        "levels": {"unknown": 0, "weak": 1, "moderate": 1, "strong": 0}, "due_reviews": 0,
    }

    clock.advance(2)
    queue = engine.review_queue()
    assert [row["concept_id"] for row in queue] == ["c.eklem"]  # only the one whose day has come
    assert queue[0]["name"] == "Eklem tipleri" and queue[0]["level_label"] == "Zayıf"
    assert queue[0]["reason"].endswith("1 gün gecikmiş.") and "2 denemede 0 doğru" in queue[0]["reason"]
    assert (queue[0]["attempts"], queue[0]["correct"]) == (2, 0)
    assert queue[0]["next_review_at"] == "2026-04-02T08:00:00+00:00"

    clock.advance(3)
    assert [row["concept_id"] for row in engine.review_queue()] == ["c.eklem", "c.kemik"]  # weak before moderate
    assert [row["concept_id"] for row in engine.review_queue(limit=1)] == ["c.eklem"]
    assert engine.review_queue(now=BASE) == []  # an explicit moment overrides the clock
    assert engine.summary()["due_reviews"] == 2


def test_suggest_difficulty_needs_five_results_and_moves_one_step_at_most() -> None:
    suggest = LearningEngine.suggest_difficulty
    assert suggest(3, []) == (3, "Yeterli deneme yok; zorluk aynı kalıyor.")
    assert suggest(3, [True] * 4)[0] == 3  # four results are not yet evidence
    assert suggest(3, [True] * 4 + [False]) == (4, "Son 5 sorunun 4'i doğru: zorluk bir kademe arttı.")
    assert suggest(3, [True] * 5)[0] == 4  # never two steps at once
    assert suggest(3, [False] * 4 + [True])[0] == 2 and suggest(3, [True, True, True, False, False])[0] == 3
    assert suggest(5, [True] * 5)[0] == 5  # already at the ceiling
    assert suggest(1, [False] * 5) == (1, "Son 5 soruda 0 doğru: zorluk aynı kalıyor.")
    assert suggest(2, [False] * 5 + [True] * 5)[0] == 3  # only the last five count


def test_insights_and_hints_report_the_history_that_actually_happened() -> None:
    clock = Clock()
    engine, _store = build_engine(clock)
    confused = make_question(concept_ids=["c.eklem"], texts=["Sinovyal eklem", "Kikirdak eklem", "Fibroz", "Sinostoz"])
    confused.options[1].concept = "Kikirdak eklem"
    mastered = make_question(concept_ids=["c.kemik"])
    engine.record(confused, False, chosen_key="B")
    engine.record(confused, False, chosen_key="B")
    for _ in range(5):
        engine.record(mastered, True)

    insights = engine.insights()
    assert insights[0] == (
        "Eklem tipleri sorularında 2 kez 'Kikirdak eklem' seçeneğine kaydın: bu ikisini ayıran kriteri tekrar et."
    )
    assert insights[1] == "Kemik: 5 soruluk doğru serisi; tekrar aralığı uzatıldı."
    assert len(engine.insights(limit=1)) == 1

    hints = engine.hints_for(["c.eklem", "c.kemik", "c.bilinmeyen"])
    assert hints == ["Eklem tipleri: weak (0/2), often confused with 'Kikirdak eklem'", "Kemik: strong, can be brief"]
    assert engine.hints_for(["c.eklem", "c.kemik"], limit=1) == [hints[0]] and engine.hints_for([]) == []
    assert [item.concept_id for item in engine.weak()] == ["c.eklem"] and engine.weak_concept_names() == ["Eklem tipleri"]
    assert [item.concept_id for item in engine.strong()] == ["c.kemik"]
