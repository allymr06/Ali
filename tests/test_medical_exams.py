"""Exam generation, the sitting lifecycle and what the history reports.

An exam is where every promise about honesty becomes visible: an item the
quality filter rejected must never reach the paper, a question whose origin
is a lecture page must say so, the answer key must stay hidden until the
sitting is over, and the mastery history afterwards must describe attempts
that actually happened. The only model here is a scripted fake gateway.
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app.medical.anatomy import AnatomyLab, AnatomyAssetRegistry
from app.medical.catalog import Curriculum
from app.medical.concepts import ConceptGraph, default_concept_graph
from app.medical.generation import ExamBuilder, GenerationError, QuestionGenerator
from app.medical.learning import LearningEngine
from app.medical.model import MedicalModelClient
from app.medical.models import (
    Concept,
    DocumentChunk,
    ExamConfig,
    KnowledgePriority,
    MasteryLevel,
    Question,
    QuestionOption,
    QuestionOrigin,
    StudyDocument,
    Subject,
)
from app.medical.retrieval import Retriever
from app.medical.store import MedicalStore
from app.medical.terminology import load_anatomy_data

ARM = "anatomy.musculoskeletal.upper_limb.arm"
BASE = datetime(2026, 4, 1, 8, 0, tzinfo=timezone.utc)
STEM = "Humerus distal ucunda radius basi ile eklem yapan yapi hangisidir?"
OTHER_STEM = "Scapula uzerinde m. deltoideus'un baslangic yeri neresidir?"
OPTIONS = ["Capitulum humeri", "Trochlea humeri", "Olecranon", "Acromion"]


class Clock:
    def __init__(self) -> None:
        self.now = BASE

    def __call__(self) -> datetime:
        return self.now

    def advance(self, days: float) -> None:
        self.now += timedelta(days=days)


class Gateway:
    """Replies from a script and remembers every prompt it was given."""

    def __init__(self, *replies: str) -> None:
        self.replies = list(replies)
        self.prompts: list[str] = []

    async def generate(self, request, context, **kwargs):
        self.prompts.append(request.text)
        reply = self.replies.pop(0) if len(self.replies) > 1 else self.replies[0]
        return SimpleNamespace(text=reply)


def draft(stem: str, texts: list[str] = OPTIONS, correct: str = "A", **extra) -> dict:
    """One model-drafted item in the shape QUESTIONS_SCHEMA asks for."""
    data = {
        "stem": stem,
        "options": [{"key": key, "text": text} for key, text in zip("ABCD", texts)],
        "correct_key": correct,
        "explanation": "Capitulum humeri, radius basi ile eklemlesen yuvarlak yapidir.",
        "concept": texts["ABCD".index(correct)],
        "difficulty": 3,
    }
    data.update(extra)
    return data


def batch(*items: dict) -> str:
    return json.dumps({"questions": list(items)})


def bank_question(question_id: str, stem: str, *, correct: str = "B", **overrides) -> Question:
    fields = {
        "subject": "anatomy",
        "topic_id": ARM,
        "correct_key": correct,
        "explanation": "Gerekce soru bankasinda kayitli durur.",
        "created_at": BASE,
    }
    fields.update(overrides)
    options = [QuestionOption(key, text) for key, text in zip("ABCD", OPTIONS)]
    return Question(question_id=question_id, stem=stem, options=options, **fields)


def make_generator(store: MedicalStore, gateway=None, *, clock: Clock | None = None) -> QuestionGenerator:
    curriculum = Curriculum()
    concepts = default_concept_graph()
    structures, _terms, _source = load_anatomy_data()
    lab = AnatomyLab(structures, AnatomyAssetRegistry(None))
    learning = LearningEngine(store, curriculum, concepts, clock=clock or Clock())
    return QuestionGenerator(
        store,
        MedicalModelClient(gateway),
        Retriever(store),
        curriculum,
        concepts,
        lab,
        learning,
    )


def config(**overrides) -> ExamConfig:
    fields = {"subjects": [Subject.ANATOMY], "topic_ids": [ARM], "question_count": 1, "option_count": 4}
    fields.update(overrides)
    return ExamConfig(**fields)


def generate(generator: QuestionGenerator, cfg: ExamConfig, **kwargs):
    return asyncio.run(generator.generate(cfg, **kwargs))


def seed_lecture(store: MedicalStore, *, document_id: str = "d1", page: int = 12) -> StudyDocument:
    document = StudyDocument(
        document_id=document_id,
        title="Ust Ekstremite Ders Notu",
        file_name="ust.pdf",
        sha256=f"sha-{document_id}",
        page_count=40,
    )
    store.save_document(document)
    store.replace_chunks(
        document_id,
        [
            DocumentChunk(
                chunk_id=f"{document_id}-c1",
                document_id=document_id,
                page_number=page,
                index_in_page=0,
                text=(
                    "Humerus distal ucunda capitulum humeri radius basi ile, trochlea humeri "
                    "ise ulna ile eklemlesir. Olecranon fossa arka yuzde bulunur."
                ),
            )
        ],
    )
    return document


# ---------------------------------------------------------------------------
# generation: refusals
# ---------------------------------------------------------------------------


def test_generation_without_a_provider_says_so_instead_of_returning_nothing() -> None:
    generator = make_generator(MedicalStore(), None)

    with pytest.raises(GenerationError, match="API anahtarını"):
        generate(generator, config())


def test_generation_without_a_subject_or_topic_refuses_to_guess_one() -> None:
    generator = make_generator(MedicalStore(), Gateway(batch(draft(STEM))))

    with pytest.raises(GenerationError, match="Ders seçilmeden"):
        generate(generator, config(subjects=[], topic_ids=[]))


def test_strict_lecture_mode_refuses_when_the_documents_yield_no_text() -> None:
    store = MedicalStore()
    empty = StudyDocument(document_id="d9", title="Bos", file_name="bos.pdf", sha256="sha9")
    store.save_document(empty)
    generator = make_generator(store, Gateway(batch(draft(STEM))))

    with pytest.raises(GenerationError, match="Katı ders materyali"):
        generate(
            generator,
            config(knowledge_priority=KnowledgePriority.STRICT_LECTURE, document_ids=["d9"]),
        )


def test_a_model_that_never_produces_a_valid_item_raises_rather_than_shipping_one() -> None:
    duplicate_options = draft(STEM, ["Capitulum humeri", "Capitulum humeri", "Olecranon", "Acromion"])
    generator = make_generator(MedicalStore(), Gateway(batch(duplicate_options)))

    with pytest.raises(GenerationError, match="geçerli soru üretemedi"):
        generate(generator, config())


def test_a_provider_failure_on_the_first_round_is_reported_as_a_generation_error() -> None:
    generator = make_generator(MedicalStore(), Gateway("bu bir JSON degil"))

    with pytest.raises(GenerationError):
        generate(generator, config())


# ---------------------------------------------------------------------------
# generation: quality filtering
# ---------------------------------------------------------------------------


def test_an_accepted_item_is_persisted_and_carries_its_concept_and_topic() -> None:
    store = MedicalStore()
    generator = make_generator(store, Gateway(batch(draft(STEM))))

    questions, notes = generate(generator, config())

    assert len(questions) == 1
    stored = store.get_question(questions[0].question_id)
    assert stored is not None and stored.stem == STEM
    assert stored.subject == "anatomy" and stored.topic_id == ARM
    assert stored.concept_ids, "an accepted question must be tagged for the mastery model"


def test_a_rejected_draft_is_counted_in_the_notes_and_never_returned() -> None:
    bad = draft("Kisa?", ["Evet", "Evet", "Hayir", "Belki"])
    generator = make_generator(MedicalStore(), Gateway(batch(bad, draft(STEM))))

    questions, notes = generate(generator, config())

    assert [question.stem for question in questions] == [STEM]
    assert any("Elenen taslaklar" in note for note in notes)


def test_a_near_copy_of_a_bank_question_is_rejected_as_too_similar() -> None:
    store = MedicalStore()
    store.save_question(bank_question("q-old", STEM))
    generator = make_generator(store, Gateway(batch(draft(STEM))))

    with pytest.raises(GenerationError):
        generate(generator, config())


def test_a_new_item_survives_alongside_an_existing_question_on_the_same_topic() -> None:
    store = MedicalStore()
    store.save_question(bank_question("q-old", OTHER_STEM))
    generator = make_generator(store, Gateway(batch(draft(STEM))))

    questions, _notes = generate(generator, config())

    assert [question.stem for question in questions] == [STEM]


def test_the_avoid_list_of_earlier_stems_is_shown_to_the_model() -> None:
    store = MedicalStore()
    store.save_question(bank_question("q-old", OTHER_STEM))
    gateway = Gateway(batch(draft(STEM)))
    generator = make_generator(store, gateway)

    generate(generator, config())

    assert OTHER_STEM in gateway.prompts[0]


def test_a_short_batch_is_reported_rather_than_padded_to_the_requested_count() -> None:
    gateway = Gateway(batch(draft(STEM)), batch(draft(STEM)), batch(draft(STEM)))
    generator = make_generator(MedicalStore(), gateway)

    questions, notes = generate(generator, config(question_count=3))

    assert len(questions) == 1  # rounds two and three repeat the same stem
    assert any("3 sorudan 1 tanesi" in note for note in notes)
    assert len(gateway.prompts) == 3


def test_options_are_shuffled_so_the_drafted_letter_is_not_always_the_key() -> None:
    stems = [
        "Humerus distal ucunda radius basi ile eklem yapan yapi hangisidir?",
        "Scapula uzerinde acromion ile eklemlesen kemik hangisidir?",
        "Ulna proksimalinde trochlea humeri ile eklemlesen cukur hangisidir?",
        "Radius distal ucunda el bilegi ile eklemlesen yuzey hangisidir?",
        "Clavicula sternum tarafinda hangi eklemi yapar?",
        "Fossa olecrani hangi kemigin arka yuzunde bulunur?",
    ]
    gateway = Gateway(batch(*[draft(stem) for stem in stems]))
    generator = make_generator(MedicalStore(), gateway)

    questions, _notes = generate(generator, config(question_count=6))

    assert len(questions) == 6
    for question in questions:
        assert question.option(question.correct_key).text == "Capitulum humeri"
    assert len({question.correct_key for question in questions}) > 1, "every answer stayed on one letter"


# ---------------------------------------------------------------------------
# generation: grounding
# ---------------------------------------------------------------------------


def test_lecture_evidence_reaches_the_prompt_with_its_page_number() -> None:
    store = MedicalStore()
    seed_lecture(store)
    gateway = Gateway(batch(draft(STEM, source_page=12)))
    generator = make_generator(store, gateway)

    generate(generator, config(document_ids=["d1"]))

    assert "s. 12" in gateway.prompts[0]
    assert "capitulum humeri radius basi" in gateway.prompts[0]


def test_an_item_drawn_from_a_lecture_page_is_marked_lecture_derived() -> None:
    store = MedicalStore()
    seed_lecture(store)
    generator = make_generator(store, Gateway(batch(draft(STEM, source_page=12))))

    questions, _notes = generate(generator, config(document_ids=["d1"]))

    assert questions[0].origin == QuestionOrigin.LECTURE_DERIVED
    assert [reference.page_number for reference in questions[0].references] == [12]


def test_an_item_without_a_source_page_stays_marked_as_generated() -> None:
    store = MedicalStore()
    seed_lecture(store)
    generator = make_generator(store, Gateway(batch(draft(STEM))))

    questions, _notes = generate(generator, config(document_ids=["d1"]))

    assert questions[0].origin == QuestionOrigin.GENERATED


def test_standard_first_without_documents_sends_no_lecture_evidence() -> None:
    store = MedicalStore()
    seed_lecture(store)
    gateway = Gateway(batch(draft(STEM)))
    generator = make_generator(store, gateway)

    generate(generator, config(knowledge_priority=KnowledgePriority.STANDARD_FIRST))

    assert "capitulum humeri radius basi" not in gateway.prompts[0]


def test_weak_concepts_are_emphasised_only_when_the_config_asks_for_it() -> None:
    store = MedicalStore()
    learning = make_generator(store, Gateway(batch(draft(STEM))))
    question = bank_question("q-weak", OTHER_STEM, concept_ids=["c.anatomy.scapula"])
    store.save_question(question)
    engine = LearningEngine(store, Curriculum(), ConceptGraph([Concept("c.anatomy.scapula", "anatomy", "Scapula")]))
    for _ in range(4):
        engine.record(question, correct=False)

    gateway = Gateway(batch(draft(STEM)))
    generator = QuestionGenerator(
        store,
        MedicalModelClient(gateway),
        Retriever(store),
        Curriculum(),
        ConceptGraph([Concept("c.anatomy.scapula", "anatomy", "Scapula")]),
        AnatomyLab([], AnatomyAssetRegistry(None)),
        engine,
    )
    generate(generator, config(weak_emphasis=True, question_count=1))

    assert "Scapula" in gateway.prompts[0]
    assert isinstance(learning, QuestionGenerator)


def test_a_professor_directive_is_forwarded_verbatim_to_the_model() -> None:
    generator = make_generator(MedicalStore(), Gateway(batch(draft(STEM))))
    directive = "Sinav sorularinda klinik senaryo kullanmaz, dogrudan yapi sorar."

    generate(generator, config(), professor_directive=directive)

    assert directive in generator._model._gateway.prompts[0]  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# bank selection (no model)
# ---------------------------------------------------------------------------


def test_bank_selection_prefers_the_questions_the_student_got_wrong() -> None:
    store = MedicalStore()
    for index in range(5):
        store.save_question(bank_question(f"q{index}", f"{STEM} ({index})"))
    generator = make_generator(store, None)

    chosen = generator.from_bank(config(question_count=2), wrong_question_ids=["q3", "q4"])

    assert [question.question_id for question in chosen] == ["q3", "q4"]


def test_bank_selection_never_returns_an_excluded_question() -> None:
    store = MedicalStore()
    for index in range(4):
        store.save_question(bank_question(f"q{index}", f"{STEM} ({index})"))
    generator = make_generator(store, None)

    chosen = generator.from_bank(
        config(question_count=4),
        wrong_question_ids=["q0"],
        exclude=["q0", "q1"],
    )

    ids = {question.question_id for question in chosen}
    assert ids.isdisjoint({"q0", "q1"}) and ids == {"q2", "q3"}


def test_bank_selection_skips_items_with_no_answer_key() -> None:
    store = MedicalStore()
    store.save_question(bank_question("q-keyed", STEM))
    store.save_question(bank_question("q-unkeyed", OTHER_STEM, correct_key=""))
    generator = make_generator(store, None)

    chosen = generator.from_bank(config(question_count=5))

    assert [question.question_id for question in chosen] == ["q-keyed"]


def test_bank_selection_returns_no_more_than_the_requested_count() -> None:
    store = MedicalStore()
    for index in range(10):
        store.save_question(bank_question(f"q{index}", f"{STEM} ({index})"))
    generator = make_generator(store, None)

    assert len(generator.from_bank(config(question_count=3))) == 3


def test_bank_selection_of_an_empty_bank_returns_nothing_rather_than_raising() -> None:
    assert make_generator(MedicalStore(), None).from_bank(config()) == []


# ---------------------------------------------------------------------------
# exam assembly
# ---------------------------------------------------------------------------


def builder(store: MedicalStore | None = None) -> tuple[ExamBuilder, MedicalStore]:
    active = store or MedicalStore()
    return ExamBuilder(active, Curriculum()), active


def test_a_given_title_is_kept_and_capped() -> None:
    exams, _store = builder()

    assert exams.title_for(config(title="  Ara Sinav  ")) == "Ara Sinav"
    assert len(exams.title_for(config(title="x" * 300))) == 120


def test_a_missing_title_is_built_from_the_topic_and_the_count() -> None:
    exams, _store = builder()

    title = exams.title_for(config(question_count=20))

    assert "20 soru" in title and title != "20 soru"


def test_a_subject_only_exam_is_named_after_its_subjects() -> None:
    exams, _store = builder()

    title = exams.title_for(config(topic_ids=[], subjects=[Subject.ANATOMY, Subject.HISTOLOGY]))

    assert "Anatomi" in title and "Histoloji" in title


def test_a_wrong_only_exam_says_so_in_its_title() -> None:
    exams, _store = builder()

    assert "yanlışlar" in exams.title_for(config(wrong_only=True))


def test_an_exam_records_its_questions_notes_and_mode() -> None:
    exams, store = builder()
    questions = [bank_question("q1", STEM), bank_question("q2", OTHER_STEM)]

    exam = exams.build(config(randomize=False), questions, notes=["Soru bankasından seçildi."])

    assert exam.question_ids == ["q1", "q2"]
    assert exam.generation_notes == ["Soru bankasından seçildi."]
    assert exam.mode == "simulation" and exam.status == "ready"
    assert store.get_exam(exam.exam_id) is not None


def test_immediate_feedback_makes_it_a_study_sitting_rather_than_a_simulation() -> None:
    exams, _store = builder()

    exam = exams.build(config(immediate_feedback=True), [bank_question("q1", STEM)])

    assert exam.mode == "study"


def test_randomising_keeps_every_question_and_only_changes_the_order() -> None:
    exams, _store = builder()
    questions = [bank_question(f"q{index}", f"{STEM} ({index})") for index in range(12)]

    orders = {tuple(exams.build(config(), questions).question_ids) for _ in range(6)}

    assert all(sorted(order) == sorted(question.question_id for question in questions) for order in orders)
    assert len(orders) > 1, "randomize produced the same order every time"


# ---------------------------------------------------------------------------
# the sitting, end to end through the facade
# ---------------------------------------------------------------------------


@pytest.fixture()
def academy(tmp_path):
    from app.medical.academy import create_medical_academy

    built = []

    def factory(gateway=None):
        directory = str(tmp_path / f"medical{len(built)}")
        built.append(create_medical_academy(settings=SimpleNamespace(medical_directory=directory), provider_gateway=gateway))
        return built[-1]

    yield factory
    for item in built:
        item.close()


def sitting(factory, *, count: int = 3, **fields):
    """An exam of ``count`` bank questions, ready to be started."""
    instance = factory(None)
    for index in range(count):
        instance.store.save_question(bank_question(f"q{index}", f"{STEM} ({index})"))
    payload = asyncio.run(instance.generate_exam({"from_bank": True, "question_count": count, "topic_ids": [ARM], "randomize": False, **fields}))
    return instance, payload["exam_id"]


def test_the_paper_shows_no_answer_key_while_the_sitting_is_open(academy) -> None:
    instance, exam_id = sitting(academy)

    payload = instance.start_exam(exam_id)

    assert payload["attempt"]["attempt_id"]
    assert all(question.get("correct_key") is None for question in payload["questions"])
    assert all(question["correct"] is None for question in payload["questions"])


def test_finishing_reveals_the_keys_and_scores_what_was_answered(academy) -> None:
    instance, exam_id = sitting(academy)
    instance.start_exam(exam_id)
    instance.answer(exam_id, "q0", "B")
    instance.answer(exam_id, "q1", "A")

    payload = instance.finish_exam(exam_id)

    assert payload["status"] == "completed"
    assert payload["analysis"]["correct"] == 1 and payload["analysis"]["total"] == 3
    assert payload["analysis"]["percent"] == 33
    keys = {question["question_id"]: question.get("correct_key") for question in payload["questions"]}
    assert keys["q0"] == "B" and keys["q2"] == "B"


def test_an_answer_to_a_question_outside_the_exam_is_refused(academy) -> None:
    instance, exam_id = sitting(academy)
    instance.store.save_question(bank_question("q-stray", OTHER_STEM))
    instance.start_exam(exam_id)

    assert instance.answer(exam_id, "q-stray", "A") is None
    assert instance.answer("exam-missing", "q0", "A") is None


def test_an_answer_can_be_changed_before_the_sitting_ends(academy) -> None:
    instance, exam_id = sitting(academy)
    instance.start_exam(exam_id)

    instance.answer(exam_id, "q0", "A")
    result = instance.answer(exam_id, "q0", "B")

    assert result["answer"] == "B" and result["answered"] == 1
    assert instance.finish_exam(exam_id)["analysis"]["correct"] == 1


def test_immediate_feedback_explains_the_answer_as_soon_as_it_is_given(academy) -> None:
    instance, exam_id = sitting(academy, immediate_feedback=True)
    instance.start_exam(exam_id)

    result = instance.answer(exam_id, "q0", "A")

    assert result["feedback"]["correct"] is False
    assert result["feedback"]["correct_key"] == "B"
    assert result["feedback"]["why_correct"]
    assert result["feedback"]["correct_text"] == OPTIONS[1]


def test_a_study_sitting_records_mastery_at_the_time_of_the_answer(academy) -> None:
    instance, exam_id = sitting(academy, immediate_feedback=True)
    instance.start_exam(exam_id)

    instance.answer(exam_id, "q0", "B")

    assert instance.learning.summary()["attempts"] == 1


def test_a_simulation_records_nothing_until_it_is_finished(academy) -> None:
    instance, exam_id = sitting(academy)
    instance.start_exam(exam_id)
    instance.answer(exam_id, "q0", "B")

    assert instance.learning.summary()["attempts"] == 0

    instance.finish_exam(exam_id)

    assert instance.learning.summary()["attempts"] == 1


def test_an_unanswered_question_is_not_recorded_as_a_wrong_attempt(academy) -> None:
    instance, exam_id = sitting(academy)
    instance.start_exam(exam_id)
    instance.answer(exam_id, "q0", None)

    instance.finish_exam(exam_id)

    assert instance.learning.summary()["attempts"] == 0


def test_starting_a_finished_exam_again_begins_a_fresh_attempt(academy) -> None:
    instance, exam_id = sitting(academy)
    instance.start_exam(exam_id)
    instance.answer(exam_id, "q0", "B")
    first = instance.finish_exam(exam_id)["attempt"]["attempt_id"]

    second = instance.start_exam(exam_id)

    assert second["attempt"]["attempt_id"] != first
    assert second["attempt"]["answered"] == 0


def test_a_wrong_only_exam_is_built_from_the_questions_that_were_missed(academy) -> None:
    instance, exam_id = sitting(academy)
    instance.start_exam(exam_id)
    instance.answer(exam_id, "q0", "A")
    instance.answer(exam_id, "q1", "B")
    instance.finish_exam(exam_id)

    payload = asyncio.run(instance.generate_exam({"wrong_only": True, "question_count": 5, "topic_ids": [ARM]}))

    assert [question["question_id"] for question in payload["questions"]] == ["q0"]
    assert any("Yanlış yaptığın 1 sorudan" in note for note in payload["notes"])


def test_a_wrong_only_exam_without_any_history_says_so(academy) -> None:
    instance, _exam_id = sitting(academy)

    with pytest.raises(GenerationError, match="önce bir sınav çöz"):
        asyncio.run(instance.generate_exam({"wrong_only": True, "topic_ids": [ARM]}))


def test_an_exam_summary_reports_the_score_after_the_sitting(academy) -> None:
    instance, exam_id = sitting(academy)
    instance.start_exam(exam_id)
    instance.answer(exam_id, "q0", "B")
    instance.finish_exam(exam_id)

    summary = next(item for item in instance.exams() if item["exam_id"] == exam_id)

    assert summary["score"] == pytest.approx(1 / 3, abs=0.001) and summary["percent"] == 33
    assert summary["finished_at"]


def test_deleting_an_exam_removes_it_from_the_history(academy) -> None:
    instance, exam_id = sitting(academy)

    assert instance.delete_exam(exam_id) is True
    assert instance.exam(exam_id) is None
    assert instance.delete_exam(exam_id) is False


# ---------------------------------------------------------------------------
# what the history reports afterwards
# ---------------------------------------------------------------------------


def engine_with_history() -> tuple[LearningEngine, MedicalStore, Clock]:
    clock = Clock()
    store = MedicalStore()
    graph = ConceptGraph(
        [
            Concept("c.capitulum", "anatomy", "Capitulum humeri"),
            Concept("c.trochlea", "anatomy", "Trochlea humeri"),
            Concept("c.krebs", "biochemistry", "Krebs döngüsü"),
        ]
    )
    return LearningEngine(store, Curriculum(), graph, clock=clock), store, clock


def question_for(concept_id: str, subject: str = "anatomy") -> Question:
    return Question(
        question_id=f"q-{concept_id}",
        subject=subject,
        stem=STEM,
        options=[QuestionOption("A", OPTIONS[0]), QuestionOption("B", OPTIONS[1])],
        correct_key="A",
        concept_ids=[concept_id],
    )


def test_weak_concepts_are_listed_before_merely_moderate_ones() -> None:
    engine, _store, _clock = engine_with_history()
    weak = question_for("c.capitulum")
    moderate = question_for("c.trochlea")
    for _ in range(4):
        engine.record(weak, correct=False)
    engine.record(moderate, correct=True)
    engine.record(moderate, correct=False)
    engine.record(moderate, correct=True)

    listed = [item.concept_id for item in engine.weak()]

    assert listed[0] == "c.capitulum" and "c.trochlea" in listed


def test_the_weak_list_can_be_confined_to_one_subject() -> None:
    engine, _store, _clock = engine_with_history()
    for _ in range(3):
        engine.record(question_for("c.capitulum"), correct=False)
        engine.record(question_for("c.krebs", subject="biochemistry"), correct=False)

    assert [item.concept_id for item in engine.weak(subject="biochemistry")] == ["c.krebs"]


def test_strong_concepts_are_ordered_by_their_streak() -> None:
    engine, _store, _clock = engine_with_history()
    for _ in range(4):
        engine.record(question_for("c.capitulum"), correct=True)
    for _ in range(8):
        engine.record(question_for("c.trochlea"), correct=True)

    assert [item.concept_id for item in engine.strong()] == ["c.trochlea", "c.capitulum"]


def test_weak_concept_names_are_readable_rather_than_identifiers() -> None:
    engine, _store, _clock = engine_with_history()
    for _ in range(3):
        engine.record(question_for("c.capitulum"), correct=False)

    assert engine.weak_concept_names() == ["Capitulum humeri"]


def test_a_topic_concept_falls_back_to_its_curriculum_breadcrumb() -> None:
    engine, _store, _clock = engine_with_history()

    name = engine.concept_name(f"topic:{ARM}")

    assert "Anatomi" in name and name != f"topic:{ARM}"


def test_an_unknown_concept_id_is_echoed_rather_than_named_with_a_guess() -> None:
    engine, _store, _clock = engine_with_history()

    assert engine.concept_name("c.nonexistent") == "c.nonexistent"
    assert engine.concept_name("c.nonexistent", fallback="Bilinmeyen") == "Bilinmeyen"


def test_the_summary_counts_attempts_levels_and_due_reviews() -> None:
    engine, _store, clock = engine_with_history()
    for _ in range(3):
        engine.record(question_for("c.capitulum"), correct=False)
    for _ in range(3):
        engine.record(question_for("c.trochlea"), correct=True)
    clock.advance(2)

    summary = engine.summary()

    assert summary["concepts"] == 2 and summary["attempts"] == 6 and summary["correct"] == 3
    assert summary["accuracy"] == 0.5
    assert summary["levels"][MasteryLevel.WEAK] == 1
    assert summary["due_reviews"] == 1  # the weak concept is due after one day


def test_an_empty_history_reports_no_accuracy_rather_than_zero() -> None:
    engine, _store, _clock = engine_with_history()

    summary = engine.summary()

    assert summary["accuracy"] is None and summary["attempts"] == 0 and summary["concepts"] == 0


def test_the_mastery_payload_names_the_concept_and_ranks_the_confusions() -> None:
    engine, store, _clock = engine_with_history()
    question = Question(
        question_id="q1",
        subject="anatomy",
        stem=STEM,
        options=[QuestionOption("A", "Capitulum humeri"), QuestionOption("B", "Trochlea humeri"), QuestionOption("C", "Olecranon")],
        correct_key="A",
        concept_ids=["c.capitulum"],
    )
    for _ in range(3):
        engine.record(question, correct=False, chosen_key="B")
    engine.record(question, correct=False, chosen_key="C")

    payload = engine.mastery_payload(store.get_mastery("c.capitulum"))

    assert payload["name"] == "Capitulum humeri"
    assert payload["attempts"] == 4 and payload["accuracy"] == 0.0
    assert list(payload["confusions"])[0] == "Trochlea humeri"
    assert payload["level_label"] and payload["reason"]


def test_a_strong_streak_pushes_the_next_review_further_out_but_not_past_the_cap() -> None:
    from app.medical.learning import MAX_STRONG_INTERVAL, next_review_for
    from app.medical.models import ConceptMastery

    modest = ConceptMastery(concept_id="c1", attempts=6, correct=6, streak=2, level=MasteryLevel.STRONG)
    long_run = ConceptMastery(concept_id="c2", attempts=90, correct=90, streak=80, level=MasteryLevel.STRONG)

    assert next_review_for(modest, BASE) > BASE + timedelta(days=7)
    assert next_review_for(long_run, BASE) == BASE + MAX_STRONG_INTERVAL


def test_levels_map_every_recorded_concept_to_its_current_level() -> None:
    engine, _store, _clock = engine_with_history()
    for _ in range(3):
        engine.record(question_for("c.capitulum"), correct=False)

    assert engine.levels() == {"c.capitulum": MasteryLevel.WEAK}
