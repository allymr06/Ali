"""The tutor and the study session: what one chat turn becomes.

The tutor decides everything it can without a provider — quiz grading,
navigation, session changes, an honest refusal when the material is not
there — and hands the rest to the model with a prompt that names its
grounding. These tests drive it the way the chat does: real store, real
curriculum, real anatomy data, and a provider that is either absent or
a scripted fake.
"""

from __future__ import annotations

import asyncio
import json
from datetime import timedelta
from types import SimpleNamespace

import pytest

from app.core.engine import REQUEST_AUGMENTATION_TIMEOUT_SECONDS
from app.core.time import utc_now
from app.medical.academy import create_medical_academy
from app.medical.catalog import Curriculum
from app.medical.context import MAX_QUESTIONS, SessionManager, StudyContext
from app.medical.intents import MedicalIntent, StudyCommand
from app.medical.models import (
    DepthLevel,
    DocumentChunk,
    KnowledgeSource,
    Question,
    QuestionOption,
    StudyDocument,
    StudyMode,
    Subject,
)
from app.medical.store import MedicalStore
from app.medical.tutor import CONTEXT_WINDOW, MEDICAL_TOOLS

ARM = "anatomy.musculoskeletal.upper_limb.arm"
OPTIONS = ["Capitulum humeri", "Trochlea humeri", "Olecranon", "Acromion"]


class Gateway:
    def __init__(self, *replies: str) -> None:
        self.replies = list(replies) or ["{}"]
        self.prompts: list[str] = []
        self.system_prompts: list[str] = []

    async def generate(self, request, context, **kwargs):
        self.prompts.append(request.text)
        self.system_prompts.append(kwargs.get("system_prompt") or "")
        reply = self.replies.pop(0) if len(self.replies) > 1 else self.replies[0]
        if isinstance(reply, Exception):
            raise reply
        return SimpleNamespace(text=reply)


class HeldGateway(Gateway):
    """A provider that answers only when the test lets it.

    The shipped model takes seconds to write questions; holding the reply
    stands in for that without the test sleeping for it.
    """

    def __init__(self, *replies: str) -> None:
        super().__init__(*replies)
        self.release = asyncio.Event()

    async def generate(self, request, context, **kwargs):
        await self.release.wait()
        return await super().generate(request, context, **kwargs)


STEMS = [
    "Humerus distal ucunda radius basi ile eklemlesen olusum hangisidir?",
    "Sulcus intertubercularis hangi iki cikinti arasinda uzanir?",
    "Fossa olecrani humerusun hangi yuzunde yer alir?",
    "Collum chirurgicum humeri neden klinik olarak onemlidir?",
    "Tuberositas deltoidea hangi kasin yapisma yeridir?",
    "Epicondylus medialis arkasindan hangi sinir gecer?",
    "Caput humeri hangi cukurla eklem yapar?",
    "Crista tuberculi majoris nereye dogru uzanir?",
    "Sulcus nervi radialis humerusun neresinde bulunur?",
    "Trochlea humeri hangi kemikle eklemlesir?",
]


def generated(count: int) -> str:
    """A schema-valid batch of questions, distinct enough to survive the filters."""
    return json.dumps(
        {
            "questions": [
                {
                    "stem": STEMS[index % len(STEMS)],
                    "options": [
                        {"key": key, "text": f"{text} {index}"}
                        for key, text in zip("ABCDE", [*OPTIONS, "Processus coracoideus"])
                    ],
                    "correct_key": "B",
                    "explanation": "Gerekce satiri modelden geldi ve kayda gecti.",
                    "concept": "humerus",
                    "difficulty": 3,
                }
                for index in range(count)
            ]
        }
    )


@pytest.fixture()
def build(tmp_path):
    """Academies over the shipped data; every provider is a fake."""
    built = []

    def factory(gateway=None):
        directory = str(tmp_path / f"medical{len(built)}")
        built.append(create_medical_academy(settings=SimpleNamespace(medical_directory=directory), provider_gateway=gateway))
        return built[-1]

    yield factory
    for academy in built:
        academy.close()


def plan(academy, text: str, **kwargs):
    return asyncio.run(academy.tutor.plan(text, **kwargs))


async def within_the_augmentation_budget(academy, text: str, **kwargs):
    """One turn under the slice the engine gives the augmenter before it
    cancels it — the budget a chat quiz has to answer inside."""
    return await asyncio.wait_for(academy.tutor.plan(text, **kwargs), timeout=REQUEST_AUGMENTATION_TIMEOUT_SECONDS)


async def settle(academy) -> None:
    """Let the academy's background jobs finish, the way the app's loop does."""
    while academy._background:
        await asyncio.gather(*list(academy._background), return_exceptions=True)


def bank(academy, count: int = 3, *, correct: str = "B") -> list[str]:
    """Answerable arm questions in the bank, so a quiz needs no model."""
    stems = [
        "Humerus distal ucunda radius basi ile eklemlesen yapi hangisidir?",
        "Sulcus intertubercularis hangi iki cikinti arasinda uzanir?",
        "Fossa olecrani humerusun hangi yuzunde bulunur?",
        "Collum chirurgicum humeri neden klinik olarak onemlidir?",
        "Tuberositas deltoidea hangi kasin yapisma yeridir?",
    ]
    ids = []
    for index in range(count):
        question = Question(
            question_id=f"q{index}",
            subject="anatomy",
            topic_id=ARM,
            stem=stems[index % len(stems)],
            options=[QuestionOption(key, text) for key, text in zip("ABCD", OPTIONS)],
            correct_key=correct,
            explanation="Gerekce soru bankasinda kayitli durur.",
            concept_ids=[f"topic:{ARM}"],
        )
        academy.store.save_question(question)
        ids.append(question.question_id)
    return ids


def seed_document(academy, *, page: int = 12, status: str = "ready") -> StudyDocument:
    document = StudyDocument(
        document_id="d1",
        title="Ust Ekstremite Ders Notu",
        file_name="ust.pdf",
        sha256="sha-d1",
        page_count=40,
        status=status,
        subject="anatomy",
    )
    academy.store.save_document(document)
    academy.store.replace_chunks(
        "d1",
        [
            DocumentChunk(
                chunk_id="d1-c1",
                document_id="d1",
                page_number=page,
                index_in_page=0,
                text=(
                    "Humerus distal ucunda capitulum humeri radius basi ile eklemlesir; "
                    "trochlea humeri ise ulna ile eklemlesir."
                ),
            )
        ],
    )
    return document


# ---------------------------------------------------------------------------
# what the tutor declines to touch
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "saat kac",
        "masaustundeki dosyalari listele",
        "bugun hava nasil",
        "tesekkurler",
    ],
)
def test_an_ordinary_turn_is_left_alone(build, text: str) -> None:
    assert plan(build(None), text) is None


def test_a_forced_turn_is_treated_as_medical_even_without_a_marker(build) -> None:
    augmentation = plan(build(None), "bunu biraz daha acar misin", forced=True)

    assert augmentation is not None and augmentation.kind == "medical"


# ---------------------------------------------------------------------------
# teaching turns
# ---------------------------------------------------------------------------


def test_a_teaching_turn_hands_the_core_a_prompt_and_only_the_medical_tools(build) -> None:
    augmentation = plan(build(None), "humerus anatomisini anlat")

    assert augmentation is not None
    assert augmentation.allowed_tools == MEDICAL_TOOLS
    assert augmentation.direct_response is None
    assert augmentation.suppress_memory is True
    assert augmentation.metadata["subject"] == "anatomy"


def test_the_prompt_names_the_depth_the_session_is_set_to(build) -> None:
    academy = build(None)
    academy.sessions.update({"depth": DepthLevel.SIMPLE})

    augmentation = plan(academy, "trochlea humeri nedir anlat")

    assert "sadeleştir" in augmentation.system_prompt.lower() or "simple" in augmentation.system_prompt.lower()


def test_lecture_evidence_is_quoted_with_its_page_and_reported_as_a_reference(build) -> None:
    academy = build(None)
    seed_document(academy)
    academy.sessions.update({"document_ids": ["d1"]})

    augmentation = plan(academy, "capitulum humeri neyle eklemlesir anlat")

    assert "s. 12" in augmentation.system_prompt
    assert augmentation.metadata["evidence_count"] == 1
    assert augmentation.metadata["references"][0]["page_number"] == 12
    assert augmentation.metadata["references"][0]["chunk_id"] == "d1-c1"


def test_standard_knowledge_mode_sends_no_lecture_evidence(build) -> None:
    academy = build(None)
    seed_document(academy)
    academy.sessions.update({"document_ids": ["d1"], "knowledge_source": KnowledgeSource.STANDARD})

    augmentation = plan(academy, "capitulum humeri neyle eklemlesir anlat")

    assert augmentation.metadata["evidence_count"] == 0
    assert "s. 12" not in augmentation.system_prompt


def test_document_only_mode_without_a_selected_document_stays_empty_handed(build) -> None:
    academy = build(None)
    seed_document(academy)
    academy.sessions.update({"knowledge_source": KnowledgeSource.SELECTED_DOCUMENTS})

    augmentation = plan(academy, "capitulum humeri neyle eklemlesir anlat")

    assert augmentation.metadata["evidence_count"] == 0


def test_a_spoken_turn_asks_for_a_spoken_shaped_answer(build) -> None:
    written = plan(build(None), "humerus anatomisini anlat")
    spoken = plan(build(None), "humerus anatomisini anlat", spoken=True)

    assert written.system_prompt != spoken.system_prompt
    assert "spoken aloud" in spoken.system_prompt
    assert "no lists, tables" in spoken.system_prompt


def test_an_anatomy_turn_carries_the_curated_facts_rather_than_recall(build) -> None:
    augmentation = plan(build(None), "scapula uzerindeki olusumlari anlat")

    assert augmentation.metadata["curated_facts"] is True
    assert augmentation.metadata["structure_ids"]


# ---------------------------------------------------------------------------
# the chat quiz
# ---------------------------------------------------------------------------

# The letter-by-letter quiz is the voice loop's: a typed "beni sına" opens the
# paper on the exam screen instead (tests/test_medical_figures.py), so every
# turn below is spoken.


def test_a_quiz_without_a_subject_asks_which_one_instead_of_picking_one(build) -> None:
    augmentation = plan(build(None), "beni sina")

    assert "Hangi dersten" in augmentation.direct_response
    assert build(None).sessions.chat_quiz_state() == {}


def test_a_quiz_starts_from_the_bank_when_there_is_no_provider(build) -> None:
    academy = build(None)
    bank(academy)

    augmentation = plan(academy, "anatomiden beni sina", spoken=True)

    assert "Quiz başladı" in augmentation.direct_response
    assert "Soru 1" in augmentation.direct_response
    quiz = academy.sessions.chat_quiz_state()
    assert quiz["active"] is True and quiz["index"] == 0 and len(quiz["question_ids"]) == 3


def test_the_first_question_is_shown_without_its_answer(build) -> None:
    academy = build(None)
    bank(academy)

    text = plan(academy, "anatomiden beni sina", spoken=True).direct_response

    assert "Doğru cevap" not in text and "correct" not in text.lower()


def test_a_correct_answer_is_confirmed_and_the_next_question_follows(build) -> None:
    academy = build(None)
    bank(academy)
    plan(academy, "anatomiden beni sina", spoken=True)

    augmentation = plan(academy, "B")

    assert augmentation.metadata["correct"] is True
    assert "Soru 2" in augmentation.direct_response
    assert academy.sessions.chat_quiz_state()["index"] == 1


def test_a_wrong_answer_names_the_right_one_and_is_recorded(build) -> None:
    academy = build(None)
    bank(academy)
    plan(academy, "anatomiden beni sina", spoken=True)

    augmentation = plan(academy, "A")

    assert augmentation.metadata["correct"] is False
    assert "B" in augmentation.direct_response
    assert academy.learning.summary()["attempts"] == 1


def test_the_last_answer_closes_the_quiz_with_a_score(build) -> None:
    academy = build(None)
    bank(academy, 2)
    plan(academy, "anatomiden beni sina", spoken=True)
    plan(academy, "B")

    augmentation = plan(academy, "B")

    assert "Quiz bitti" in augmentation.direct_response
    assert "2/2" in augmentation.direct_response
    assert academy.sessions.chat_quiz_state() == {}


def test_skipping_reveals_that_question_and_moves_on(build) -> None:
    academy = build(None)
    bank(academy)
    plan(academy, "anatomiden beni sina", spoken=True)

    augmentation = plan(academy, "sonraki soru")

    assert "Soru atlandı (doğru cevap: B)" in augmentation.direct_response
    assert "Soru 2" in augmentation.direct_response


def test_a_skipped_question_is_not_recorded_as_an_attempt(build) -> None:
    academy = build(None)
    bank(academy)
    plan(academy, "anatomiden beni sina", spoken=True)

    plan(academy, "sonraki soru")

    assert academy.learning.summary()["attempts"] == 0


def test_stopping_closes_the_quiz_and_reports_what_was_done(build) -> None:
    academy = build(None)
    bank(academy)
    plan(academy, "anatomiden beni sina", spoken=True)
    plan(academy, "B")

    augmentation = plan(academy, "bitir")

    assert "Quiz yarıda kapatıldı" in augmentation.direct_response
    assert "1/3" in augmentation.direct_response
    assert academy.sessions.chat_quiz_state() == {}


def test_a_stop_word_outside_a_quiz_is_not_a_medical_turn(build) -> None:
    assert plan(build(None), "bitir") is None


def test_an_answer_letter_outside_a_quiz_is_not_graded_as_one(build) -> None:
    academy = build(None)

    assert plan(academy, "B") is None


# ---------------------------------------------------------------------------
# a quiz the model has to write: the turn must not wait for it
# ---------------------------------------------------------------------------


def test_a_quiz_the_bank_can_fill_is_started_without_asking_the_provider(build) -> None:
    gateway = Gateway(generated(5))
    academy = build(gateway)
    bank(academy, 5)

    augmentation = asyncio.run(within_the_augmentation_budget(academy, "anatomiden 3 soru sor", spoken=True))

    assert "Quiz başladı" in augmentation.direct_response
    assert gateway.prompts == [], "the bank could fill it, so nothing was asked of the provider"


def test_a_quiz_that_needs_the_model_answers_the_turn_before_the_model_does(build) -> None:
    """The engine cancels the augmenter after two seconds and says nothing,
    so waiting for a generation round trip here means the quiz never runs."""
    gateway = HeldGateway(generated(5))
    academy = build(gateway)

    async def turn():
        augmentation = await within_the_augmentation_budget(academy, "anatomiden beni sina", spoken=True)
        gateway.release.set()
        await settle(academy)
        return augmentation

    augmentation = asyncio.run(turn())

    assert "hazırlıyorum" in augmentation.direct_response
    assert augmentation.metadata["quiz"] == "preparing"


def test_nothing_claims_the_questions_exist_before_they_do(build) -> None:
    gateway = HeldGateway(generated(5))
    academy = build(gateway)
    events: list[dict] = []
    academy.subscribe(events.append)

    async def turn():
        await within_the_augmentation_budget(academy, "anatomiden beni sina", spoken=True)
        held = (academy.sessions.chat_quiz_state(), academy.store.summary()["questions"], list(events))
        gateway.release.set()
        await settle(academy)
        return held

    quiz, questions, emitted = asyncio.run(turn())

    assert quiz == {} and questions == 0
    assert emitted == [], "no quiz may be announced while the model is still writing it"


def test_the_prepared_quiz_starts_and_reports_its_first_question(build) -> None:
    gateway = HeldGateway(generated(5))
    academy = build(gateway)
    events: list[dict] = []
    academy.subscribe(events.append)

    async def turn():
        await within_the_augmentation_budget(academy, "anatomiden beni sina", spoken=True)
        gateway.release.set()
        await settle(academy)

    asyncio.run(turn())

    quiz = academy.sessions.chat_quiz_state()
    assert quiz["active"] is True and quiz["index"] == 0 and len(quiz["question_ids"]) == 5
    ready = [event for event in events if event["kind"] == "quiz_ready"]
    assert ready and ready[0]["count"] == 5
    assert "Soru 1" in ready[0]["question"] and "Doğru cevap" not in ready[0]["question"]


def test_a_prepared_quiz_can_be_answered_in_chat_when_it_lands(build) -> None:
    gateway = HeldGateway(generated(5))
    academy = build(gateway)

    async def turn():
        await within_the_augmentation_budget(academy, "anatomiden beni sina", spoken=True)
        gateway.release.set()
        await settle(academy)

    asyncio.run(turn())
    asked = academy.store.get_question(academy.sessions.chat_quiz_state()["question_ids"][0])
    augmentation = plan(academy, asked.correct_key)

    assert augmentation.metadata["correct"] is True
    assert "Soru 2" in augmentation.direct_response


def test_a_chat_exam_that_needs_the_model_does_not_wait_for_it_either(build) -> None:
    gateway = HeldGateway(generated(10))
    academy = build(gateway)
    events: list[dict] = []
    academy.subscribe(events.append)

    async def turn():
        augmentation = await within_the_augmentation_budget(academy, "anatomiden 10 soruluk sinav hazirla")
        gateway.release.set()
        await settle(academy)
        return augmentation

    augmentation = asyncio.run(turn())

    assert "hazırlıyorum" in augmentation.direct_response
    ready = [event for event in events if event["kind"] == "exam_ready"]
    assert ready and ready[0]["count"] == len(academy.store.get_exam(ready[0]["exam_id"]).question_ids)
    assert ready[0]["count"] > 0
    assert academy.sessions.get().active_exam_id == ready[0]["exam_id"]


def test_a_prepared_quiz_that_fails_says_so_instead_of_going_quiet(build) -> None:
    gateway = HeldGateway("bu JSON degil", "hala JSON degil")
    academy = build(gateway)
    events: list[dict] = []
    academy.subscribe(events.append)

    async def turn():
        await within_the_augmentation_budget(academy, "anatomiden beni sina", spoken=True)
        gateway.release.set()
        await settle(academy)

    asyncio.run(turn())

    failed = [event for event in events if event["kind"] == "job_failed"]
    assert failed and failed[0]["message"]
    assert academy.sessions.chat_quiz_state() == {}


def test_asking_again_while_the_questions_are_being_written_starts_no_second_job(build) -> None:
    gateway = HeldGateway(generated(5))
    academy = build(gateway)

    async def turn():
        await within_the_augmentation_budget(academy, "anatomiden beni sina", spoken=True)
        second = await within_the_augmentation_budget(academy, "anatomiden beni sina", spoken=True)
        gateway.release.set()
        await settle(academy)
        return second

    second = asyncio.run(turn())

    assert "hâlâ hazırlıyorum" in second.direct_response
    assert len(gateway.prompts) == 1, "one request, one paper"


def test_a_prepared_quiz_does_not_replace_one_the_student_already_started(build) -> None:
    """The model can land minutes later; whatever the student is answering
    by then is theirs, and the finished paper waits on the exam screen."""
    gateway = HeldGateway(generated(5))
    academy = build(gateway)
    events: list[dict] = []
    academy.subscribe(events.append)

    async def turn():
        await within_the_augmentation_budget(academy, "anatomiden beni sina", spoken=True)
        bank(academy, 3)
        await within_the_augmentation_budget(academy, "anatomiden 3 soru sor", spoken=True)
        started = academy.sessions.chat_quiz_state()["question_ids"]
        gateway.release.set()
        await settle(academy)
        return started

    started = asyncio.run(turn())

    assert academy.sessions.chat_quiz_state()["question_ids"] == started
    assert [event["kind"] for event in events].count("exam_ready") == 1


def test_an_oral_exam_leaves_the_questioning_to_the_model(build) -> None:
    academy = build(None)

    augmentation = plan(academy, "sozlu sinav yap anatomiden")

    assert augmentation.direct_response is None
    assert "ORAL EXAM" in augmentation.system_prompt
    assert academy.sessions.chat_quiz_state()["mode"] == "oral"


def test_a_letter_during_an_oral_exam_is_answered_by_the_model_not_graded(build) -> None:
    academy = build(None)
    plan(academy, "sozlu sinav yap anatomiden")

    augmentation = plan(academy, "B")

    assert augmentation.direct_response is None
    assert "ORAL EXAM" in augmentation.system_prompt


def test_closing_an_oral_exam_says_how_to_resume_it(build) -> None:
    academy = build(None)
    plan(academy, "sozlu sinav yap anatomiden")

    augmentation = plan(academy, "bitir")

    assert "Sözlü sınav kapatıldı" in augmentation.direct_response
    assert academy.sessions.chat_quiz_state() == {}


# ---------------------------------------------------------------------------
# explaining a mistake
# ---------------------------------------------------------------------------


def test_why_wrong_after_an_answer_gives_the_model_that_exact_item(build) -> None:
    academy = build(None)
    bank(academy)
    plan(academy, "anatomiden beni sina", spoken=True)
    asked = academy.sessions.chat_quiz_state()["question_ids"][0]
    plan(academy, "A")

    augmentation = plan(academy, "neden yanlış")

    assert augmentation.metadata["question_id"] == asked
    assert "Student chose: A" in augmentation.system_prompt
    assert "Correct: B" in augmentation.system_prompt


def test_why_wrong_outside_a_quiz_is_an_ordinary_lesson_not_a_guess(build) -> None:
    augmentation = plan(build(None), "anatomide neden yanlış oluyorum")

    assert augmentation is not None and augmentation.direct_response is None
    assert "question_id" not in augmentation.metadata


# ---------------------------------------------------------------------------
# review, exams, anatomy, documents, professors
# ---------------------------------------------------------------------------


def test_a_review_request_alone_is_not_taken_from_the_rest_of_jarvis(build) -> None:
    """Without a study context these words could be about anything, so the
    academy leaves the turn to the ordinary assistant."""
    assert plan(build(None), "zayıf olduğum konuları tekrar edelim") is None


def test_a_review_request_with_no_history_says_so_rather_than_inventing_gaps(build) -> None:
    academy = build(None)
    plan(academy, "humerus anatomisini anlat")

    augmentation = plan(academy, "zayıf olduğum konuları tekrar edelim")

    assert "Henüz zayıf olarak işaretlenmiş bir kavram yok" in augmentation.direct_response


def test_a_review_request_lists_the_concepts_the_history_actually_holds(build) -> None:
    academy = build(None)
    bank(academy)
    plan(academy, "anatomiden beni sina", spoken=True)
    plan(academy, "A")
    plan(academy, "A")
    plan(academy, "A")

    augmentation = plan(academy, "zayıf olduğum konuları tekrar edelim")

    assert augmentation.direct_response is None
    assert augmentation.metadata["weak_concepts"] == [f"topic:{ARM}"]
    assert "0/3 correct" in augmentation.system_prompt


def test_an_exam_request_without_a_subject_asks_for_one(build) -> None:
    augmentation = plan(build(None), "20 soruluk sinav hazirla")

    assert "Hangi ders ya da konudan" in augmentation.direct_response


def test_an_exam_request_reports_the_paper_it_actually_built(build) -> None:
    academy = build(None)
    bank(academy, 5)

    augmentation = plan(academy, "anatomiden 3 soruluk sinav hazirla")

    exam = academy.store.get_exam(augmentation.metadata["exam_id"])
    assert exam is not None and len(exam.question_ids) == 3
    assert "Sınav hazır" in augmentation.direct_response
    assert "3 soru" in augmentation.direct_response


def test_an_exam_that_cannot_be_built_explains_why_instead_of_failing_the_turn(build) -> None:
    academy = build(None)

    augmentation = plan(academy, "anatomiden 10 soruluk sinav hazirla")

    assert augmentation.direct_response
    assert augmentation.metadata.get("error") == "generation"


def test_a_professor_style_exam_without_any_profile_says_how_to_make_one(build) -> None:
    augmentation = plan(build(None), "hocanın tarzında anatomiden 10 soru hazırla")

    assert "Henüz bir hoca profili yok" in augmentation.direct_response


def test_asking_about_professors_with_none_stored_points_at_the_import_screen(build) -> None:
    augmentation = plan(build(None), "hoca profilleri")

    assert "Henüz hoca profili yok" in augmentation.direct_response


def test_an_unknown_structure_name_never_opens_the_lab_on_a_guess(build) -> None:
    academy = build(None)
    events: list[dict] = []
    academy.subscribe(events.append)

    augmentation = plan(academy, "anatomi labında zümrüt kemiğini aç")

    assert augmentation.metadata["intent"] != MedicalIntent.ANATOMY_OPEN
    assert not [event for event in events if event.get("kind") == "anatomy_open"]


def test_the_lab_refuses_a_structure_it_does_not_hold(build) -> None:
    academy = build(None)
    command = StudyCommand(text="aç", intent=MedicalIntent.ANATOMY_OPEN, structure_ids=["zumrut_kemigi"])

    augmentation = academy.tutor._anatomy_action(command, {"intent": MedicalIntent.ANATOMY_OPEN})

    assert "bulamadım" in augmentation.direct_response


def test_opening_a_known_structure_emits_the_event_and_admits_the_missing_model(build) -> None:
    academy = build(None)
    events: list[dict] = []
    academy.subscribe(events.append)

    augmentation = plan(academy, "humerus labda aç")

    assert augmentation.metadata["structure_id"]
    opened = [event for event in events if event.get("kind") == "anatomy_open"]
    assert opened and opened[0]["structure_id"] == augmentation.metadata["structure_id"]
    assert "şematik" in augmentation.direct_response


def test_a_bare_analyse_request_is_left_to_the_rest_of_jarvis(build) -> None:
    assert plan(build(None), "bu PDF'i analiz et") is None


def test_analysing_a_document_with_none_imported_points_at_the_library(build) -> None:
    academy = build(None)
    plan(academy, "humerus anatomisini anlat")

    augmentation = plan(academy, "bu PDF'i analiz et")

    assert "Henüz işlenmiş bir belge yok" in augmentation.direct_response


def test_analysing_a_document_without_a_provider_says_the_model_is_needed(build) -> None:
    academy = build(None)
    seed_document(academy)
    plan(academy, "humerus anatomisini anlat")

    augmentation = plan(academy, "bu PDF'i analiz et")

    assert "analiz modeli gerekiyor" in augmentation.direct_response


def test_a_document_turn_remembers_which_document_it_was_about(build) -> None:
    academy = build(Gateway(json.dumps({"title": "x", "subject": "anatomy", "summary": "y", "topics": [], "key_terms": []})))
    seed_document(academy)
    plan(academy, "humerus anatomisini anlat")

    plan(academy, "bu PDF'i analiz et")

    assert academy.sessions.get().document_ids == ["d1"]


# ---------------------------------------------------------------------------
# the session behind the turns
# ---------------------------------------------------------------------------


def session_manager() -> SessionManager:
    return SessionManager(MedicalStore(), Curriculum())


def test_a_chat_command_is_remembered_for_the_next_turn(build) -> None:
    academy = build(None)

    plan(academy, "anatomiden 7 soruluk zor sinav hazirla")

    session = academy.sessions.get()
    assert session.subject == "anatomy" and session.question_count == 7


def test_a_topic_chosen_in_chat_lands_in_the_recent_list(build) -> None:
    academy = build(None)

    plan(academy, "omuz eklemini anlat")

    session = academy.sessions.get()
    assert session.topic_id and session.topic_id in session.recent_topics


def test_harder_and_easier_move_the_difficulty_one_step_within_bounds() -> None:
    manager = session_manager()

    for _ in range(6):
        manager.apply_command(StudyCommand(text="daha zor", intent=MedicalIntent.GENERAL, harder=True))
    assert manager.get().difficulty == 5

    for _ in range(6):
        manager.apply_command(StudyCommand(text="daha kolay", intent=MedicalIntent.GENERAL, easier=True))
    assert manager.get().difficulty == 1


def test_an_out_of_range_number_is_pulled_into_range_and_reported() -> None:
    manager = session_manager()

    session, problems = manager.update({"question_count": 500})

    assert session.question_count == MAX_QUESTIONS
    assert problems and "soru sayısı" in problems[0]


def test_an_unknown_choice_keeps_the_current_value_and_says_which_field() -> None:
    manager = session_manager()

    session, problems = manager.update({"mode": "hipnoz"})

    assert session.mode == StudyMode.TEACH
    assert problems == ["Bilinmeyen mod: hipnoz"]


def test_an_unknown_field_is_reported_rather_than_silently_dropped() -> None:
    manager = session_manager()

    _session, problems = manager.update({"telepathy": True})

    assert problems == ["Bilinmeyen alan: telepathy"]


def test_an_unknown_subject_or_topic_is_refused() -> None:
    manager = session_manager()

    session, problems = manager.update({"subject": "astroloji", "topic_id": "anatomy.uydurma"})

    assert session.subject is None and session.topic_id is None
    assert len(problems) == 2


def test_changing_the_subject_clears_a_topic_that_belonged_to_the_old_one() -> None:
    manager = session_manager()
    manager.update({"topic_id": ARM})

    session, _problems = manager.update({"subject": Subject.HISTOLOGY})

    assert session.subject == "histology" and session.topic_id is None


def test_choosing_a_topic_also_sets_the_subject_it_belongs_to() -> None:
    manager = session_manager()

    session, problems = manager.update({"topic_id": ARM})

    assert problems == [] and session.subject == "anatomy" and session.topic_id == ARM


def test_an_inverted_page_range_is_swapped_into_a_usable_one() -> None:
    manager = session_manager()

    session, problems = manager.update({"page_from": 40, "page_to": 12})

    assert (session.page_from, session.page_to) == (12, 40)
    assert problems == []


def test_a_command_overlays_the_session_only_for_that_turn() -> None:
    manager = session_manager()
    manager.update({"subject": Subject.ANATOMY, "difficulty": 2})

    context = manager.resolve(StudyCommand(text="zor sor", intent=MedicalIntent.QUIZ, difficulty=5))

    assert isinstance(context, StudyContext) and context.difficulty == 5
    assert manager.get().difficulty == 2


def test_the_described_session_carries_readable_labels_and_the_full_choices() -> None:
    manager = session_manager()
    manager.update({"topic_id": ARM})

    payload = manager.describe()

    assert payload["labels"]["subject"] == "Anatomi"
    assert "Kol" in payload["labels"]["topic"] or "kol" in payload["labels"]["topic"].lower()
    assert {item["value"] for item in payload["options"]["modes"]} == {item.value for item in StudyMode}
    assert all(item["label"] for item in payload["options"]["subjects"])


def test_an_unset_session_says_nothing_was_chosen_rather_than_guessing() -> None:
    payload = session_manager().describe()

    assert payload["labels"]["subject"] == "Ders seçilmedi"
    assert payload["labels"]["topic"] == "Konu seçilmedi"


def test_a_stale_session_stops_lending_its_context_to_a_bare_follow_up(build) -> None:
    academy = build(None)
    plan(academy, "humerus anatomisini anlat")

    session = academy.sessions.get()
    session.last_activity_at = utc_now() - CONTEXT_WINDOW - timedelta(minutes=1)
    academy.sessions.save(session)

    assert plan(academy, "biraz daha acar misin") is None
