"""Medical Academy: structured model calls, the tutor and the study facade.

Three promises are held here. Every model-backed pipeline speaks one JSON
contract that is repaired exactly once and then fails loudly; everything the
tutor can decide deterministically (navigation, quiz grading, session state)
is decided without a provider; and nothing is invented — a missing term, a
missing 3D model, a missing subject and a rejected draft are all reported as
such. The only model here is a fake gateway returning canned text.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from app.core.models import Context, Request, RequestSource, ToolExecutionStatus
from app.medical.academy import create_medical_academy
from app.medical.generation import ExamBuilder, GenerationError
from app.medical.model import MedicalModelClient, MedicalModelError, extract_json
from app.medical.models import ExamConfig, Question, QuestionOption, QuestionOrigin
from app.medical.schemas import NOTES_SCHEMA, QUESTIONS_SCHEMA, coerce_strings, validate, wire_schema
from app.medical.tutor import MEDICAL_TOOLS, MedicalTutor
from app.tools.executor import ToolExecutor

ARM = "anatomy.musculoskeletal.upper_limb.arm"
PROFESSOR_STEM = "Humerus distal ucunda radius basi ile eklem yapan yapi hangisidir?"
CAPITULUM = ["Capitulum humeri", "Trochlea humeri", "Olecranon", "Acromion"]
NERVES = ["N. medianus", "N. radialis", "N. ulnaris", "N. axillaris"]
NERVE_STEM = "Humerus govdesinde seyreden sinir hangisidir?"
SMALL_SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": "string", "maxLength": 5},
        "support": {"type": "string", "enum": ["high", "limited"]},
        "page": {"type": "integer", "minimum": 1, "maximum": 3},
        "tags": {"type": "array", "items": {"type": "string", "maxLength": 3}, "minItems": 1, "maxItems": 2},
    },
    "required": ["title", "support", "page"],
}


class FakeGateway:
    """A provider stand-in: canned replies, and it records how it was called."""

    def __init__(self, *replies: str) -> None:
        self.replies = list(replies)
        self.calls: list[dict] = []

    async def generate(self, request, context, **kwargs):
        self.calls.append({"prompt": request.text, "metadata": dict(request.metadata), **kwargs})
        return SimpleNamespace(text=self.replies.pop(0) if len(self.replies) > 1 else self.replies[0])


def draft(stem: str, texts: list[str], correct: str, explanation: str) -> dict:
    """One model-drafted item in the shape QUESTIONS_SCHEMA asks for."""
    return {
        "stem": stem, "correct_key": correct, "explanation": explanation, "difficulty": 3,
        "concept": texts["ABCD".index(correct)],
        "options": [{"key": key, "text": text} for key, text in zip("ABCD", texts)],
    }


def bank_question(question_id: str, stem: str, texts: list[str], **overrides) -> Question:
    """A gradable four-option bank item whose answer is B unless overridden."""
    fields = {"subject": "anatomy", "topic_id": ARM, "correct_key": "B", "concept_ids": [f"topic:{ARM}"],
              "explanation": "Gerekce soru bankasinda kayitli olarak durur."}
    fields.update(overrides)
    options = [QuestionOption(key, text) for key, text in zip("ABCD", texts)]
    return Question(question_id=question_id, stem=stem, options=options, **fields)


@pytest.fixture()
def build(tmp_path):
    """Build academies over the shipped data; every provider is a fake."""
    academies = []

    def factory(gateway=None):
        directory = str(tmp_path / f"medical{len(academies)}")
        academies.append(create_medical_academy(settings=SimpleNamespace(medical_directory=directory), provider_gateway=gateway))
        return academies[-1]

    yield factory
    for academy in academies:
        academy.close()


# ---------------------------------------------------------------------------
# the JSON contract every pipeline speaks
# ---------------------------------------------------------------------------


def test_extract_json_tolerates_fences_and_prose_but_never_guesses() -> None:
    assert extract_json('```json\n{"a": 1}\n```') == {"a": 1}
    assert extract_json('Iste sonuc:\n{"a": [1, 2]}\nUmarim yardimci olur.') == {"a": [1, 2]}
    assert extract_json("```\n[1, 2]\n```") == [1, 2]
    with pytest.raises(ValueError, match="empty reply"):
        extract_json("   ")
    with pytest.raises(ValueError, match="no JSON object"):
        extract_json("Bu konuda bir sey yazamam.")


def test_validate_names_each_problem_and_coercion_trims_instead_of_failing() -> None:
    assert validate({"title": "cok uzun", "support": "orta", "page": 9, "tags": []}, SMALL_SCHEMA) == [
        "$.title: longer than 5 characters",
        "$.support: value not in ['high', 'limited']",
        "$.page: above maximum 3",
        "$.tags: fewer than 1 items",
    ]
    assert validate({"support": "high", "page": 0}, SMALL_SCHEMA) == ["$.title: missing", "$.page: below minimum 1"]
    assert validate({"title": 5, "support": "high", "page": 2}, SMALL_SCHEMA) == ["$.title: expected string"]
    # A boolean is not an integer page; an unknown key is simply ignored.
    assert validate({"title": "ok", "support": "high", "page": True}, SMALL_SCHEMA) == ["$.page: expected integer"]
    assert validate({"title": "ok", "support": "high", "page": 1, "extra": {"x": 1}}, SMALL_SCHEMA) == []
    assert validate({"title": "ok", "support": "high", "page": 1, "tags": list("abc")}, SMALL_SCHEMA) == ["$.tags: more than 2 items"]

    coerced = coerce_strings({"title": "cok uzun", "tags": ["uzuncatext", "b", "c"], "extra": "kalir"}, SMALL_SCHEMA)
    assert coerced == {"title": "cok u", "tags": ["uzu", "b"], "extra": "kalir"}
    assert validate({**coerced, "support": "high", "page": 1}, SMALL_SCHEMA) == []


@pytest.mark.asyncio
async def test_the_model_client_repairs_one_bad_reply_then_refuses_to_pretend() -> None:
    broken = json.dumps({"questions": [{"stem": "kisa"}]})
    usable = json.dumps({"questions": [draft(NERVE_STEM, NERVES, "B", "Sulcus icinde seyreder.")]})
    gateway = FakeGateway(broken, usable)

    data = await MedicalModelClient(gateway).structured("question_generation", "uret", QUESTIONS_SCHEMA)

    assert len(data["questions"]) == 1
    assert len(gateway.calls) == 2, "one repair attempt, not an open-ended retry loop"
    assert "rejected" in gateway.calls[1]["prompt"] and "options: missing" in gateway.calls[1]["prompt"]
    assert gateway.calls[0]["response_format"]["json_schema"] == {"name": "question_generation", "schema": wire_schema(QUESTIONS_SCHEMA)}
    assert gateway.calls[0]["metadata"] == {"medical_pipeline": "question_generation", "tool_schema_selection": False, "structured_output": True}

    stubborn = FakeGateway(broken)
    with pytest.raises(MedicalModelError) as raised:
        await MedicalModelClient(stubborn).structured("question_generation", "uret", QUESTIONS_SCHEMA)
    assert len(stubborn.calls) == 2
    assert raised.value.problems[0] == "$.questions[0].options: missing"
    assert raised.value.raw.startswith('{"questions"')

    # One over-long field is trimmed to the schema rather than thrown away.
    overlong = FakeGateway(json.dumps({"title": "Not", "markdown": "x" * 12_050}))
    notes = await MedicalModelClient(overlong).structured("study_notes", "yaz", NOTES_SCHEMA)
    assert len(notes["markdown"]) == 12_000 and len(overlong.calls) == 1

    # A missing or dead provider is an error, never a silently empty answer.
    class Broken:
        async def generate(self, request, context, **kwargs):
            raise RuntimeError("provider down")

    assert MedicalModelClient(None).available is False
    with pytest.raises(MedicalModelError, match="Model sağlayıcısı yapılandırılmamış."):
        await MedicalModelClient(None).structured("document_analysis", "oku", QUESTIONS_SCHEMA)
    with pytest.raises(MedicalModelError, match=r"Model çağrısı başarısız \(RuntimeError\)."):
        await MedicalModelClient(Broken()).text("page_ocr", "oku")


# ---------------------------------------------------------------------------
# the facade: session, curriculum, terminology, anatomy
# ---------------------------------------------------------------------------


def test_the_facade_reports_an_offline_academy_and_a_validated_session(build) -> None:
    academy = build()
    available = academy.available()
    assert available["model"] is False and available["persistent"] is True
    assert available["structures"] > 0 and available["terms"] > 0 and available["concepts"] > 0
    dashboard = academy.dashboard()
    assert dashboard["available"] == available and dashboard["counts"]["questions"] == 0
    assert dashboard["session"]["labels"]["subject"] == "Ders seçilmedi" and dashboard["jobs"] == []
    assert dashboard["recent_exams"] == [] and dashboard["professors"] == []

    chosen = academy.update_session({"topic_id": ARM, "depth": "detailed"})
    assert chosen["problems"] == [] and chosen["session"]["subject"] == "anatomy"
    assert chosen["session"]["labels"]["topic"].endswith("Kol: humerus ve kol kasları")

    rejected = academy.update_session({"topic_id": "yok.boyle.konu", "subject": "astroloji", "sihir": 1})
    assert rejected["problems"] == ["Bilinmeyen konu: yok.boyle.konu", "Bilinmeyen ders: astroloji", "Bilinmeyen alan: sihir"]
    assert rejected["session"]["topic_id"] == ARM and rejected["session"]["subject"] == "anatomy"

    # Out-of-band numbers are clamped, and a reversed page range is put right.
    clamped = academy.update_session({"difficulty": 99, "question_count": 0, "page_from": 40, "page_to": 10})
    assert clamped["session"]["difficulty"] == 5 and clamped["session"]["question_count"] == 1
    assert (clamped["session"]["page_from"], clamped["session"]["page_to"]) == (10, 40)
    assert academy.session_state()["difficulty"] == 5


def test_curriculum_terminology_and_anatomy_answer_from_the_shipped_data(build) -> None:
    academy = build()
    anatomy = academy.subjects()[0]
    assert anatomy["topic_id"] == "anatomy" and anatomy["children"] and anatomy["concepts"] > 0
    assert anatomy["mastery"] == {"weak": 0, "moderate": 0, "strong": 0} and anatomy["documents"] == 0
    topic = academy.topic(ARM)
    assert topic["subject_label"] == "Anatomi" and topic["title"] == "Kol: humerus ve kol kasları"
    assert topic["path"][0]["topic_id"] == "anatomy" and topic["structures"] and topic["question_count"] == 0
    assert academy.topic("yok.boyle.konu") is None

    assert academy.search("   ") == {"query": "", "terms": [], "topics": [], "structures": [], "hits": []}
    found = academy.search("humerus")
    assert [entry["canonical"] for entry in found["terms"]][:1] == ["Humerus"]
    assert ARM in [item["topic_id"] for item in found["topics"]]
    assert "humerus" in [item["structure_id"] for item in found["structures"]]
    assert found["hits"] == [], "nothing was imported, so there is no lecture material to cite"
    assert "Kol kemiği" in academy.term("humerus")["entries"][0]["explanation"]
    assert academy.term("zzzqqq") == {"query": "zzzqqq", "entries": []}

    structures = academy.anatomy_structures()
    assert structures["hierarchy"] and structures["assets"]["available"] == []
    assert "Terminologia Anatomica" in structures["source"]
    humerus = academy.anatomy_structure("humerus")
    assert humerus["canonical"] == "Humerus" and humerus["landmark_count"] > 0
    assert humerus["model"]["available"] is False and academy.anatomy_structure("yok") is None
    quiz = academy.anatomy_quiz("humerus", count=99)
    assert 1 <= len(quiz) <= 20, "the requested count is clamped, never taken at face value"
    assert all(item["correct_key"] in {option["key"] for option in item["options"]} for item in quiz)
    assert len(academy.anatomy_quiz("humerus", count=3)) == 3
    mesh = academy.anatomy_mesh("humerus")
    assert mesh["available"] is False and "3B model yok" in mesh["reason"]


def test_question_bank_filters_flags_and_refuses_an_impossible_answer_key(build) -> None:
    academy = build()
    academy.store.save_question(bank_question("q1", NERVE_STEM, NERVES, difficulty=2))
    academy.store.save_question(bank_question(
        "q2", "Yalanci cok katli epitel nerede bulunur?", ["Trakea", "Ozofagus", "Deri", "Mesane"],
        subject="histology", topic_id=None, correct_key=None, difficulty=4, concept_ids=[],
        origin=QuestionOrigin.IMPORTED_EXAM, professor_id="p1",
    ))

    def ids(filters=None):
        return sorted(item["question_id"] for item in academy.question_bank(filters)["questions"])

    assert ids() == ["q1", "q2"] and academy.question_bank()["counts"] == {"generated": 1, "imported_exam": 1, "total": 2}
    assert ids({"subject": "anatomy"}) == ["q1"] and ids({"with_answer_key": True}) == ["q1"]
    assert ids({"topic_id": "anatomy.musculoskeletal"}) == ["q1"], "a parent topic matches its descendants"
    assert ids({"origin": "imported_exam"}) == ["q2"] and ids({"professor_id": "p1"}) == ["q2"]
    assert ids({"difficulty": 4}) == ["q2"] and ids({"text": "epitel"}) == ["q2"]
    assert ids({"answered": "unanswered"}) == ["q1", "q2"] and ids({"answered": "correct"}) == []

    keyless = academy.question_bank({"with_answer_key": False})["questions"]
    assert [item["question_id"] for item in keyless] == ["q2"]
    assert keyless[0]["problems"] == ["missing_answer_key"] and keyless[0]["has_answer_key"] is False

    assert academy.set_answer_key("q2", "b")["correct_key"] == "B"
    with pytest.raises(ValueError, match="Bu harfte bir seçenek yok."):
        academy.set_answer_key("q2", "E")
    assert academy.store.get_question("q2").correct_key == "B", "the refused key changed nothing"
    assert academy.set_answer_key("q2", "")["correct_key"] is None
    assert academy.set_answer_key("bilinmeyen", "A") is None


def test_an_unknown_subject_filter_narrows_to_nothing_and_says_so(build) -> None:
    academy = build()
    academy.store.save_question(bank_question("q1", NERVE_STEM, NERVES))
    academy.store.save_question(bank_question(
        "q2", "Yalanci cok katli epitel nerede bulunur?", ["Trakea", "Ozofagus", "Deri", "Mesane"],
        subject="histology", topic_id=None, concept_ids=[],
    ))

    typo = academy.question_bank({"subject": "anatomii"})
    assert typo["questions"] == [] and typo["total"] == 0
    assert typo["problems"] == ["Bilinmeyen ders: anatomii"], "a typo is reported, not dropped"
    assert typo["counts"] == {"generated": 2, "total": 2}, "the bank behind the rejected filter is untouched"

    # The Turkish label is a legitimate spelling of the subject and still filters.
    kept = academy.question_bank({"subject": "Anatomi"})
    assert [item["question_id"] for item in kept["questions"]] == ["q1"] and kept["problems"] == []


# ---------------------------------------------------------------------------
# tools
# ---------------------------------------------------------------------------


def test_the_four_medical_tools_register_read_only_and_answer_honestly(build) -> None:
    academy = build()
    executor = ToolExecutor()
    events: list[dict] = []
    academy.subscribe(events.append)
    academy.register_tools(executor)

    registered = {name for name in executor.list_names() if name.startswith("medical_")}
    assert registered == set(MEDICAL_TOOLS)
    assert {name: executor.get(name).definition.risk_level.value for name in registered} == {
        "medical_search_library": "read_only", "medical_lookup_term": "read_only",
        "medical_study_state": "read_only", "medical_open_anatomy": "low",
    }
    assert all("read-only" in executor.get(name).definition.tags for name in registered)
    assert all(executor.get(name).source == "core:medical" for name in registered)

    found = {name: executor.execute(name, parameters=params) for name, params in (
        ("medical_search_library", {"query": "humerus"}),
        ("medical_lookup_term", {"term": "humerus"}),
        ("medical_open_anatomy", {"structure": "humerus", "highlight": "caput; collum_anatomicum"}),
        ("medical_study_state", {}),
    )}
    assert all(result.succeeded and result.verified for result in found.values())
    assert found["medical_search_library"].data == {"evidence": []}
    assert "bulunamadı" in found["medical_search_library"].message, "an empty library says so, it does not invent evidence"
    assert found["medical_lookup_term"].data["terms"][0]["canonical"] == "Humerus"
    assert found["medical_open_anatomy"].data == {"structure_id": "humerus", "highlight": ["caput", "collum_anatomicum"]}
    assert found["medical_study_state"].data["labels"]["subject"] == "Ders seçilmedi"

    for name, params in (("medical_lookup_term", {"term": "zzzqqq"}), ("medical_open_anatomy", {"structure": "zzzqqq"})):
        missing = executor.execute(name, parameters=params)
        assert missing.status is ToolExecutionStatus.FAILED and missing.error == "not_found" and missing.verified is False
    assert [event["kind"] for event in events] == ["anatomy_open"], "only the opened structure is an event"


# ---------------------------------------------------------------------------
# the tutor over one core turn
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_study_turn_is_augmented_and_a_household_turn_is_left_alone(build) -> None:
    academy = build()
    assert await academy.augment(Request("hava bugün nasıl"), Context()) is None
    assert await academy.augment(Request("spotify'da müzik aç"), Context()) is None

    augmentation = await academy.augment(Request("Scapulayı bana basit anlat"), Context())
    assert augmentation.kind == "medical" and augmentation.suppress_memory is True and augmentation.direct_response is None
    assert augmentation.allowed_tools == MEDICAL_TOOLS, "the turn is narrowed to the medical tools"
    assert "Never fabricate citations" in augmentation.system_prompt and "cannot replace" in augmentation.system_prompt
    assert augmentation.metadata["intent"] == "medical.simplify"
    assert augmentation.metadata["evidence_count"] == 0 and augmentation.metadata["references"] == []


@pytest.mark.asyncio
async def test_navigation_and_a_subjectless_exam_are_handled_without_a_model(build) -> None:
    academy = build()
    events: list[dict] = []
    academy.subscribe(events.append)

    opened = await academy.augment(Request("Anatomy Lab'de humerus aç"), Context())
    assert opened.system_prompt is None and opened.allowed_tools is None
    assert "Humerus" in opened.direct_response and opened.metadata["structure_id"] == "humerus"
    assert "şematik işaret haritası" in opened.direct_response, "the missing 3D model is admitted"
    assert [(event["kind"], event["structure_id"], event["quiz"]) for event in events] == [("anatomy_open", "humerus", False)]

    # A second academy, because the request above already put anatomy in the session.
    fresh = build()
    asked = await fresh.augment(Request("20 soru hazırla"), Context())
    assert asked.direct_response.startswith("Hangi ders ya da konudan sınav hazırlayayım?")
    assert asked.metadata["intent"] == "medical.exam_generate"
    assert fresh.store.list_exams() == [] and fresh.store.count_questions() == {"total": 0}


@pytest.mark.asyncio
async def test_a_chat_quiz_grades_records_mastery_and_closes_with_a_summary(build) -> None:
    academy = build()
    academy.store.save_question(bank_question("q1", NERVE_STEM, NERVES))
    academy.store.save_question(bank_question("q2", "Musculus biceps brachii hangi eklemi bukcer?", ["Art. humeri", "Art. cubiti", "Art. radioulnaris", "Art. sternoclavicularis"]))
    academy.update_session({"topic_id": ARM})

    started = await academy.augment(Request("beni sına", source=RequestSource.VOICE), Context())
    assert started.metadata["quiz"] == "started" and started.system_prompt is None
    assert "Quiz başladı" in started.direct_response and "**Soru 1**" in started.direct_response
    quiz = academy.sessions.chat_quiz_state()
    assert quiz["active"] is True and len(quiz["question_ids"]) == 2 and quiz["index"] == 0

    answered = await academy.augment(Request("B"), Context())
    assert answered.metadata["correct"] is True and "✅ Doğru" in answered.direct_response
    assert "**Soru 2**" in answered.direct_response, "grading moves straight on to the next question"
    assert academy.sessions.chat_quiz_state()["index"] == 1
    assert [(item.concept_id, item.attempts, item.correct) for item in academy.learning.all()] == [(f"topic:{ARM}", 1, 1)]

    closed = await academy.augment(Request("quizi bitir"), Context())
    assert closed.metadata["quiz"] == "stopped" and academy.sessions.chat_quiz_state() == {}
    assert closed.direct_response.startswith("Quiz yarıda kapatıldı. Sonuç: 1/2 doğru (%50).")
    exam = academy.store.list_exams()[0]
    assert exam.status == "completed" and academy.store.latest_attempt(exam.exam_id).analysis["percent"] == 50


@pytest.mark.asyncio
async def test_a_bare_stop_word_closes_the_quiz_the_intro_promised_it_would(build) -> None:
    academy = build()
    academy.store.save_question(bank_question("q1", NERVE_STEM, NERVES))
    academy.store.save_question(bank_question("q2", "Musculus biceps brachii hangi eklemi bukcer?", ["Art. humeri", "Art. cubiti", "Art. radioulnaris", "Art. sternoclavicularis"]))
    academy.update_session({"topic_id": ARM})

    assert await academy.augment(Request("bitir"), Context()) is None, "with no quiz open there is nothing to close"

    started = await academy.augment(Request("beni sına", source=RequestSource.VOICE), Context())
    assert "“bitir” ile kapatabilirsin" in started.direct_response
    assert academy.sessions.chat_quiz_state()["active"] is True

    # "bitir" alone carries no medical word, so the parser cannot make a quiz
    # command of it; the promise in the intro is kept all the same.
    closed = await academy.augment(Request("bitir"), Context())
    assert closed.metadata["quiz"] == "stopped" and academy.sessions.chat_quiz_state() == {}
    assert closed.direct_response.startswith("Quiz yarıda kapatıldı. Sonuç: 0/2 doğru")


def test_a_stop_word_is_a_whole_word_so_dura_mater_never_closes_a_quiz(build) -> None:
    academy = build()
    academy.sessions.start_chat_quiz(["q1", "q2"], mode="quiz")
    session = academy.sessions.get()

    assert MedicalTutor._wants_stop("bitir", session) is True
    assert MedicalTutor._wants_stop("quizi bitir", session) is True
    assert MedicalTutor._wants_stop("BİTİR", session) is True, "Turkish capitals fold to the same word"

    # "dur" hides inside "dura", "end" inside "tendon" and "endokrin": these are
    # questions a student asks mid-quiz, not requests to close it.
    for question in ("dura mater nedir", "tendon nedir", "endokrin bezleri anlat", "kapak kemigini anlat"):
        assert MedicalTutor._wants_stop(question, session) is False, question
    assert MedicalTutor._wants_stop("bu konuyu bitirdikten sonra tekrar sorularina gecelim lutfen", session) is False

    academy.sessions.stop_chat_quiz()
    assert MedicalTutor._wants_stop("bitir", academy.sessions.get()) is False, "a closed quiz is not closed twice"


# ---------------------------------------------------------------------------
# generation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_generation_keeps_valid_unique_items_and_says_what_it_dropped(build) -> None:
    valid_one = draft("Sulcus nervi radialis icinde seyreden sinir hangisidir?", NERVES, "B", "Nervus radialis bu sulcusta seyreder.")
    valid_two = draft(
        "Musculus biceps brachii'nin kisa basi nereden baslar?",
        ["Processus coracoideus", "Tuberculum supraglenoidale", "Acromion", "Spina scapulae"],
        "A", "Caput breve processus coracoideus uzerinden baslar.",
    )
    near_copy = draft(PROFESSOR_STEM, CAPITULUM, "A", "Capitulum humeri radius basi ile eklemlesir.")
    defective = draft("Kisa", ["Ayni", "Ayni", "Baska", "Diger"], "A", "Bu madde bilerek bozuk uretildi.")
    reply = json.dumps({"questions": [valid_one, near_copy, defective, valid_two]}, ensure_ascii=False)
    academy = build(FakeGateway(f"Iste sorular:\n```json\n{reply}\n```\nUmarim yardimci olur."))
    academy.store.save_question(bank_question("prof-1", PROFESSOR_STEM, CAPITULUM, correct_key="A", origin=QuestionOrigin.IMPORTED_EXAM, professor_id="p1"))

    config = ExamConfig(subjects=["anatomy"], topic_ids=[ARM], question_count=2, option_count=4, difficulty=3)
    questions, notes = await academy.generator.generate(config)
    assert [question.stem for question in questions] == [valid_one["stem"], valid_two["stem"]]
    assert notes == ["Elenen taslaklar: duplicate_options×1, stem_too_short×1, too_similar×1"]
    assert PROFESSOR_STEM not in " ".join(notes), "a rejected draft is counted, never quoted back"
    for question, source in zip(questions, (valid_one, valid_two)):
        assert [option.key for option in question.options] == ["A", "B", "C", "D"]
        expected = source["options"]["ABCD".index(source["correct_key"])]["text"]
        assert question.option(question.correct_key).text == expected, "shuffling moves the letter, not the answer"
        assert question.concept_ids and question.origin == QuestionOrigin.GENERATED

    stored = academy.store.query_questions(subject="anatomy", limit=50)
    assert sorted(question.stem for question in stored) == sorted([valid_one["stem"], valid_two["stem"], PROFESSOR_STEM])
    remaining = academy.store.query_questions(text="Capitulum humeri", limit=50)
    assert [item.origin for item in remaining] == [QuestionOrigin.IMPORTED_EXAM], "the near-copy was never saved"


@pytest.mark.asyncio
async def test_generation_refuses_without_a_model_a_subject_or_a_usable_draft(build) -> None:
    config = ExamConfig(subjects=["anatomy"], topic_ids=[ARM], question_count=2, option_count=4)
    with pytest.raises(GenerationError, match="model sağlayıcısı gerekli"):
        await build().generator.generate(config)

    academy = build(FakeGateway(json.dumps({"questions": [draft("Kisa", ["Ayni", "Ayni", "X", "Y"], "A", "Bozuk taslak.")]})))
    with pytest.raises(GenerationError, match="Ders seçilmeden soru üretilemez."):
        await academy.generator.generate(ExamConfig(question_count=1, option_count=4))
    with pytest.raises(GenerationError, match="Model geçerli soru üretemedi"):
        await academy.generator.generate(config)
    assert academy.store.count_questions() == {"total": 0}, "nothing unusable reaches the bank"


def test_exam_titles_name_the_topic_the_filter_and_the_size(build) -> None:
    academy = build()
    builder = ExamBuilder(academy.store, academy.curriculum)
    assert builder.title_for(ExamConfig(title="  Deneme sınavı  ", question_count=3)) == "Deneme sınavı"
    assert builder.title_for(ExamConfig(subjects=["anatomy", "histology"], question_count=5)) == "Anatomi + Histoloji · 5 soru"
    assert builder.title_for(ExamConfig(topic_ids=[ARM], question_count=3, wrong_only=True)) == "Kol: humerus ve kol kasları · yanlışlar · 3 soru"

    profile_id = academy.create_professor("Yılmaz Hoca", "anatomy")["profile_id"]
    config = ExamConfig(subjects=["anatomy"], question_count=2, professor_id=profile_id, randomize=False, immediate_feedback=True)
    assert builder.title_for(config) == "Anatomi · Yılmaz Hoca tarzı · 2 soru"

    exam = builder.build(config, [bank_question("q1", NERVE_STEM, NERVES)], notes=["Soru bankasından seçildi."])
    assert exam.question_ids == ["q1"] and exam.status == "ready" and exam.mode == "study"
    assert exam.generation_notes == ["Soru bankasından seçildi."]
    assert academy.store.get_exam(exam.exam_id).title == exam.title
