"""Figure questions, the interactive paper and what the results say.

A figure question shows one of the student's own lecture pages -- never
something JARVIS drew -- so every claim it makes must trace back to a page
the vision pass actually described. A typed "beni sına" opens the paper on
the exam screen (options to mark, a finish button, the wrong answers
explained), while the spoken one keeps the letter-by-letter chat quiz.
"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest

from app.core.models import Context, Request
from app.medical.academy import create_medical_academy
from app.medical.models import (
    DocumentChunk,
    DocumentPage,
    DocumentStatus,
    ExamConfig,
    Question,
    QuestionOption,
    QuestionOrigin,
    StudyDocument,
    Subject,
)
from app.medical.prompts import question_generation_prompt
from app.medical.questions import figure_payload

ARM = "anatomy.musculoskeletal.upper_limb.arm"
OPTIONS = ["Capitulum humeri", "Trochlea humeri", "Olecranon", "Acromion"]


class Gateway:
    def __init__(self, *replies: str) -> None:
        self.replies = list(replies) or ["{}"]
        self.prompts: list[str] = []

    async def generate(self, request, context, **kwargs):
        self.prompts.append(request.text)
        reply = self.replies.pop(0) if len(self.replies) > 1 else self.replies[0]
        return SimpleNamespace(text=reply)


@pytest.fixture()
def build(tmp_path):
    built = []

    def factory(gateway=None):
        directory = str(tmp_path / f"medical{len(built)}")
        built.append(create_medical_academy(settings=SimpleNamespace(medical_directory=directory), provider_gateway=gateway))
        return built[-1]

    yield factory
    for academy in built:
        academy.close()


def seed_figure_document(academy, *, document_id: str = "d1", analysed: bool = True) -> StudyDocument:
    document = StudyDocument(
        document_id=document_id,
        title="Ust Ekstremite Slaytlari",
        file_name="ust.pdf",
        sha256=f"sha-{document_id}",
        kind="text",
        page_count=20,
        status=DocumentStatus.READY,
        subject="anatomy",
    )
    academy.store.save_document(document)
    academy.store.save_pages(
        [
            DocumentPage(document_id=document_id, page_number=7, text="Humerus on yuz.", image_count=1, visual_status="done" if analysed else "pending",
                         visual_summary="Humerus on yuzu: caput humeri, tuberculum majus ve tuberculum minus isaretli." if analysed else "",
                         visual_labels=["caput humeri", "tuberculum majus", "tuberculum minus"] if analysed else []),
            DocumentPage(document_id=document_id, page_number=8, text="Metin sayfasi.", image_count=0, visual_status="not_needed"),
        ]
    )
    academy.store.replace_chunks(
        document_id,
        [DocumentChunk(chunk_id=f"{document_id}-c1", document_id=document_id, page_number=8, index_in_page=0, text="Humerus kolun uzun kemigidir; capitulum radius ile eklemlesir.")],
    )
    return document


def draft(stem: str, *, figure_index: int = 0, **extra) -> dict:
    data = {
        "stem": stem,
        "options": [{"key": key, "text": text} for key, text in zip("ABCD", OPTIONS)],
        "correct_key": "A",
        "explanation": "Capitulum humeri radius basi ile eklemlesen yuvarlak yapidir.",
        "concept": "Capitulum humeri",
        "difficulty": 3,
    }
    if figure_index:
        data["figure_index"] = figure_index
    data.update(extra)
    return data


def batch(*items: dict) -> str:
    return json.dumps({"questions": list(items)})


# ---------------------------------------------------------------------------
# the prompt
# ---------------------------------------------------------------------------


def prompt(**overrides) -> str:
    fields = dict(count=5, option_count=4, difficulty=3, subject="anatomy", topic_path="Kol", question_type="single_best_answer",
                  evidence_text="", knowledge_priority="balanced", style_directive=None, avoid_stems=[], concept_hints=[],
                  curated_facts="", weak_concepts=[])
    fields.update(overrides)
    return question_generation_prompt(**fields)


def test_the_prompt_lists_the_figures_and_forbids_inventing_their_content() -> None:
    text = prompt(figures=["[Şekil 1] Slaytlar, s. 7 — Humerus on yuzu | etiketler: caput humeri"], figure_questions=2)

    assert "[Şekil 1] Slaytlar, s. 7" in text
    assert "at least 2 of the questions ABOUT a figure" in text
    assert "Never describe a figure detail that is not in its description" in text


def test_the_prompt_has_no_figure_block_without_figures() -> None:
    assert "Figures:" not in prompt(figures=[], figure_questions=2)
    assert "Figures:" not in prompt(figures=["[Şekil 1] x"], figure_questions=0)


# ---------------------------------------------------------------------------
# which pages may become figures
# ---------------------------------------------------------------------------


def test_only_pages_the_vision_pass_described_are_offered(build) -> None:
    academy = build(None)
    seed_figure_document(academy, document_id="d1", analysed=True)
    seed_figure_document(academy, document_id="d2", analysed=False)

    figures = academy.generator._figures(ExamConfig(subjects=[Subject.ANATOMY], document_ids=["d1", "d2"], include_images=True))

    assert [(figure.document_id, figure.page_number) for figure in figures] == [("d1", 7)]
    assert "tuberculum majus" in figures[0].prompt_line()


def test_figures_are_off_unless_the_paper_asks_for_images(build) -> None:
    academy = build(None)
    seed_figure_document(academy)

    assert academy.generator._figures(ExamConfig(subjects=[Subject.ANATOMY], document_ids=["d1"], include_images=False)) == []


def test_a_subject_only_paper_draws_figures_from_the_subjects_own_documents(build) -> None:
    academy = build(None)
    seed_figure_document(academy, document_id="d1")
    other = seed_figure_document(academy, document_id="d2")
    other.subject = "histology"
    academy.store.save_document(other)

    figures = academy.generator._figures(ExamConfig(subjects=[Subject.ANATOMY], include_images=True))

    assert {figure.document_id for figure in figures} == {"d1"}


def test_a_page_range_limits_the_figures(build) -> None:
    academy = build(None)
    seed_figure_document(academy)

    assert academy.generator._figures(ExamConfig(subjects=[Subject.ANATOMY], document_ids=["d1"], include_images=True, page_from=8, page_to=12)) == []


# ---------------------------------------------------------------------------
# generation: the figure the model names becomes the question's page
# ---------------------------------------------------------------------------


def generate(academy, config: ExamConfig):
    return asyncio.run(academy.generator.generate(config))


def test_a_figure_question_is_anchored_to_the_page_it_shows(build) -> None:
    academy = build(Gateway(batch(draft("Şekildeki işaretli yapı hangisidir?", figure_index=1))))
    seed_figure_document(academy)

    questions, _notes = generate(academy, ExamConfig(subjects=[Subject.ANATOMY], topic_ids=[ARM], document_ids=["d1"], question_count=1, option_count=4, include_images=True))

    question = questions[0]
    assert question.image_ref == "d1|7"
    assert question.metadata["figure_title"] == "Ust Ekstremite Slaytlari"
    assert [(ref.document_id, ref.page_number) for ref in question.references] == [("d1", 7)]
    assert question.origin == QuestionOrigin.LECTURE_DERIVED
    assert "[Şekil 1]" in academy.model._gateway.prompts[0]


def test_a_figure_index_that_was_never_offered_yields_no_figure(build) -> None:
    academy = build(Gateway(batch(draft("Şekildeki işaretli yapı hangisidir?", figure_index=4))))
    seed_figure_document(academy)

    questions, _notes = generate(academy, ExamConfig(subjects=[Subject.ANATOMY], topic_ids=[ARM], document_ids=["d1"], question_count=1, option_count=4, include_images=True))

    assert questions[0].image_ref is None
    assert figure_payload(questions[0]) is None


def test_a_subject_only_item_gets_the_topic_of_its_concept(build) -> None:
    academy = build(Gateway(batch(draft("Radius başı ile eklemleşen humerus yapısı hangisidir?"))))

    questions, _notes = generate(academy, ExamConfig(subjects=[Subject.ANATOMY], question_count=1, option_count=4))

    assert questions[0].topic_id, "a paper with no topic must still file each item under one"
    assert academy.curriculum.exists(questions[0].topic_id)


# ---------------------------------------------------------------------------
# the payload the runner reads
# ---------------------------------------------------------------------------


def test_the_figure_payload_names_document_page_and_caption() -> None:
    question = Question(question_id="q", subject="anatomy", stem="s", options=[QuestionOption("A", "x"), QuestionOption("B", "y")],
                        correct_key="A", image_ref="d1|7", metadata={"figure_title": "Slaytlar", "figure_caption": "Slaytlar · s. 7"})

    assert figure_payload(question) == {"document_id": "d1", "page_number": 7, "title": "Slaytlar", "caption": "Slaytlar · s. 7"}


@pytest.mark.parametrize("ref", [None, "", "d1", "d1|", "|7", "d1|yedi"])
def test_an_unreadable_image_reference_yields_no_figure(ref) -> None:
    question = Question(question_id="q", subject="anatomy", stem="s", options=[], correct_key=None, image_ref=ref)

    assert figure_payload(question) is None


# ---------------------------------------------------------------------------
# the paper opens on the exam screen
# ---------------------------------------------------------------------------


def bank(academy, count: int = 3) -> None:
    stems = ["Humerus distal ucunda radius ile eklemlesen yapi hangisidir?", "Sulcus intertubercularis hangi iki cikinti arasinda uzanir?", "Fossa olecrani humerusun hangi yuzunde bulunur?"]
    for index in range(count):
        academy.store.save_question(Question(question_id=f"q{index}", subject="anatomy", topic_id=ARM, stem=stems[index % 3],
                                             options=[QuestionOption(key, text) for key, text in zip("ABCD", OPTIONS)], correct_key="B",
                                             explanation="Gerekce kayitli.", concept_ids=[f"topic:{ARM}"]))


def plan(academy, text: str, **kwargs):
    return asyncio.run(academy.tutor.plan(text, **kwargs))


def test_a_typed_quiz_opens_a_paper_instead_of_asking_in_chat(build) -> None:
    academy = build(None)
    bank(academy)
    events: list[dict] = []
    academy.subscribe(events.append)

    augmentation = plan(academy, "anatomiden beni sina")

    assert "Sınav ekranında açtım" in augmentation.direct_response
    assert "Soru 1" not in augmentation.direct_response
    opened = [event for event in events if event.get("kind") == "exam_ready"]
    assert opened and opened[0]["open"] is True
    assert academy.sessions.chat_quiz_state() == {}
    exam = academy.store.get_exam(augmentation.metadata["exam_id"])
    assert exam.config.answers_at_end is True and exam.config.immediate_feedback is False


def test_a_spoken_quiz_still_asks_letter_by_letter(build) -> None:
    academy = build(None)
    bank(academy)

    augmentation = plan(academy, "anatomiden beni sina", spoken=True)

    assert "Soru 1" in augmentation.direct_response
    assert academy.sessions.chat_quiz_state()["active"] is True


def test_a_paper_the_model_must_write_opens_when_it_is_ready(build) -> None:
    stems = [
        "Radius başı ile eklemleşen humerus yapısı hangisidir?",
        "Scapula üzerinde acromion ile eklemleşen kemik hangisidir?",
        "Ulna proksimalinde trochlea humeri ile eklemleşen çukur hangisidir?",
        "Clavicula sternum tarafında hangi eklemi yapar?",
        "Fossa olecrani hangi kemiğin arka yüzünde bulunur?",
    ]
    academy = build(Gateway(batch(*[draft(stem) for stem in stems])))
    events: list[dict] = []
    academy.subscribe(events.append)
    # The paper takes its option count from the session; the drafts are four-option items.
    academy.sessions.update({"topic_id": ARM, "option_count": 4})

    async def run():
        augmentation = await academy.tutor.plan("beni sina")
        while academy._background:
            await asyncio.gather(*list(academy._background), return_exceptions=True)
        return augmentation

    augmentation = asyncio.run(run())

    assert "hazırlıyorum" in augmentation.direct_response
    ready = [event for event in events if event.get("kind") == "exam_ready"]
    assert ready and ready[0].get("open") is True
    assert academy.sessions.chat_quiz_state() == {}


def test_the_results_suggestion_names_the_blanks_when_nothing_was_wrong(build) -> None:
    academy = build(None)
    bank(academy)
    payload = asyncio.run(academy.generate_exam({"from_bank": True, "question_count": 3, "topic_ids": [ARM], "randomize": False}))
    academy.start_exam(payload["exam_id"])
    academy.answer(payload["exam_id"], "q0", "B")

    analysis = academy.finish_exam(payload["exam_id"])["analysis"]

    assert analysis["incorrect"] == 0 and analysis["unanswered"] == 2
    assert analysis["suggestion"]["kind"] == "answer_blanks"
    assert "2 soru boş kaldı" in analysis["suggestion"]["text"]
