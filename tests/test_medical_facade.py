"""The facade the Nova screen and the bridge actually call.

Every read here becomes something the student sees, so the tests care about
what the payloads claim: a page number that was never in the material must
be marked unverified, a comparison must carry its caveat, an unknown filter
must narrow to nothing rather than answering with the whole bank, and a
missing 3D asset must be reported as missing. The provider is a fake.
"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest

from app.medical.academy import create_medical_academy
from app.medical.documents import DocumentError
from app.medical.model import MedicalModelError
from app.medical.models import (
    DocumentChunk,
    DocumentPage,
    DocumentStatus,
    Question,
    QuestionOption,
    QuestionOrigin,
    StudyDocument,
)

ARM = "anatomy.musculoskeletal.upper_limb.arm"
LECTURE = (
    "Humerus distal ucunda capitulum humeri radius basi ile eklemlesir. "
    "Trochlea humeri ulna ile eklemlesir ve fossa olecrani arka yuzde bulunur."
)


class Gateway:
    def __init__(self, *replies: str) -> None:
        self.replies = list(replies) or ["{}"]
        self.prompts: list[str] = []

    async def generate(self, request, context, **kwargs):
        self.prompts.append(request.text)
        reply = self.replies.pop(0) if len(self.replies) > 1 else self.replies[0]
        if isinstance(reply, Exception):
            raise reply
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


def seed(academy, *, status: str = DocumentStatus.READY, page: int = 12) -> StudyDocument:
    document = StudyDocument(
        document_id="d1",
        title="Ust Ekstremite Ders Notu",
        file_name="ust.pdf",
        sha256="sha-d1",
        kind="text",
        page_count=13,
        status=status,
        subject="anatomy",
    )
    academy.store.save_document(document)
    academy.store.save_pages(
        [
            DocumentPage(document_id="d1", page_number=page, text=LECTURE, headings=["Humerus"]),
            DocumentPage(document_id="d1", page_number=page + 1, text="Scapula ve clavicula kisa notlar.", image_count=2, visual_status="pending"),
        ]
    )
    academy.store.replace_chunks(
        "d1",
        [
            DocumentChunk(chunk_id="d1-c1", document_id="d1", page_number=page, index_in_page=0, text=LECTURE, heading="Humerus"),
            DocumentChunk(chunk_id="d1-c2", document_id="d1", page_number=page + 1, index_in_page=0, text="Scapula ve clavicula kisa notlar."),
        ],
    )
    return document


def question(question_id: str, **overrides) -> Question:
    fields = {
        "subject": "anatomy",
        "topic_id": ARM,
        "stem": f"Humerus sorusu {question_id}",
        "options": [QuestionOption("A", "Capitulum humeri"), QuestionOption("B", "Trochlea humeri")],
        "correct_key": "A",
        "explanation": "Gerekce kayitli.",
    }
    fields.update(overrides)
    return Question(question_id=question_id, **fields)


ANALYSIS = json.dumps(
    {
        "title": "Ust Ekstremite",
        "subject": "anatomy",
        "summary": "Kol kemikleri ve eklemleri.",
        "topics": [{"title": "Kol", "page_from": 12, "page_to": 13, "concepts": ["humerus"]}],
        "key_terms": ["humerus", "capitulum humeri"],
        "high_yield": ["Capitulum radius ile eklemlesir."],
        "uncertainties": ["Sayfa 13 sekli okunamadi."],
    }
)


def comparison_reply(page: int) -> str:
    return json.dumps(
        {
            "findings": [
                {
                    "statement": "Capitulum ulna ile eklemlesir.",
                    "page": page,
                    "category": "possibly_incorrect",
                    "explanation": "Standart kaynaklarda capitulum radius ile eklemlesir.",
                    "standard_view": "Capitulum humeri radius basi ile eklemlesir.",
                    "support": "high",
                }
            ],
            "overall": "Bir bulgu disinda ders notu standart bilgiyle uyumlu.",
        }
    )


# ---------------------------------------------------------------------------
# what the screen opens with
# ---------------------------------------------------------------------------


def test_an_empty_academy_reports_itself_honestly(build) -> None:
    payload = build(None).dashboard()

    assert payload["available"]["model"] is False
    assert payload["available"]["structures"] > 0 and payload["available"]["terms"] > 0
    assert payload["counts"]["documents"] == 0
    assert payload["learning"]["accuracy"] is None
    assert payload["review_queue"] == [] and payload["insights"] == []
    assert payload["session"]["labels"]["subject"] == "Ders seçilmedi"


def test_the_dashboard_reports_a_provider_once_one_is_configured(build) -> None:
    assert build(Gateway()).dashboard()["available"]["model"] is True


def test_the_subject_tree_counts_only_the_material_that_exists(build) -> None:
    academy = build(None)
    seed(academy)
    academy.store.save_document(
        StudyDocument(document_id="d1", title="Ust Ekstremite Ders Notu", file_name="ust.pdf", sha256="sha-d1", status=DocumentStatus.READY, topic_ids=[ARM])
    )

    tree = academy.subjects()
    anatomy = next(node for node in tree if node["topic_id"] == "anatomy")

    assert anatomy["documents"] == 1
    assert anatomy["mastery"] == {"weak": 0, "moderate": 0, "strong": 0}
    assert {node["topic_id"] for node in tree} >= {"anatomy", "histology", "biochemistry"}


def test_a_topic_page_gathers_its_own_material_and_nothing_else(build) -> None:
    academy = build(None)
    academy.store.save_question(question("q1"))
    academy.store.save_question(question("q2", topic_id="biology.cell", subject="biology"))

    payload = academy.topic(ARM)

    assert payload["question_count"] == 1
    assert payload["subject_label"] == "Anatomi"
    assert [item["topic_id"] for item in payload["path"]][0] == "anatomy"
    assert payload["structures"], "the arm topic should carry the shipped bone structures"


def test_an_unknown_topic_is_reported_missing_rather_than_approximated(build) -> None:
    assert build(None).topic("anatomy.uydurma") is None


# ---------------------------------------------------------------------------
# search and terminology
# ---------------------------------------------------------------------------


def test_search_reaches_terms_topics_structures_and_lecture_text(build) -> None:
    academy = build(None)
    seed(academy)

    payload = academy.search("capitulum humeri")

    assert payload["terms"] and payload["terms"][0]["canonical"]
    assert any(hit["kind"] == "chunk" and hit["page_number"] == 12 for hit in payload["hits"])
    assert any(hit["title"] == "Ust Ekstremite Ders Notu" for hit in payload["hits"] if hit["kind"] == "chunk")


def test_an_empty_query_returns_empty_lists_rather_than_everything(build) -> None:
    payload = build(None).search("   ")

    assert payload == {"query": "", "terms": [], "topics": [], "structures": [], "hits": []}


def test_a_term_card_carries_a_turkish_explanation(build) -> None:
    payload = build(None).term("scapula")

    assert payload["entries"], "scapula ships with the anatomy data"
    assert payload["entries"][0]["explanation"]
    assert payload["entries"][0]["canonical"].lower().startswith("scapula")


def test_an_unknown_term_returns_no_entries_instead_of_a_guess(build) -> None:
    assert build(None).term("qwerty zxcv")["entries"] == []


def test_a_partial_turkish_word_still_finds_the_latin_terms_it_could_mean(build) -> None:
    found = {entry["canonical"] for entry in build(None).term("kemiği")["entries"]}

    assert "Humerus" in found and "Scapula" in found


# ---------------------------------------------------------------------------
# documents
# ---------------------------------------------------------------------------


def test_a_document_payload_lists_its_pages_and_visual_state(build) -> None:
    academy = build(None)
    seed(academy)

    payload = academy.document("d1")

    assert [page["page_number"] for page in payload["pages"]] == [12, 13]
    assert payload["pages"][1]["visual_status"] == "pending"
    assert payload["comparison"] is None and payload["questions"] == 0


def test_a_missing_document_or_page_is_reported_as_missing(build) -> None:
    academy = build(None)
    seed(academy)

    assert academy.document("d-missing") is None
    assert academy.page("d1", 99) is None


def test_a_text_document_page_carries_no_rendered_image(build) -> None:
    academy = build(None)
    seed(academy)

    payload = academy.page("d1", 12)

    assert payload["text"] == LECTURE and payload["image"] is None
    assert payload["headings"] == ["Humerus"]


def test_analysis_needs_a_provider_and_a_processed_document(build) -> None:
    offline = build(None)
    seed(offline)
    with pytest.raises(MedicalModelError):
        asyncio.run(offline.analyze_document("d1"))

    online = build(Gateway(ANALYSIS))
    seed(online, status=DocumentStatus.PENDING)
    with pytest.raises(DocumentError, match="işlenmedi"):
        asyncio.run(online.analyze_document("d1"))

    with pytest.raises(DocumentError, match="bulunamadı"):
        asyncio.run(online.analyze_document("d-missing"))


def test_analysis_files_the_document_under_a_topic_that_exists(build) -> None:
    academy = build(Gateway(ANALYSIS))
    seed(academy)

    analysis = asyncio.run(academy.analyze_document("d1"))

    assert analysis["summary"] == "Kol kemikleri ve eklemleri."
    assert analysis["uncertainties"] == ["Sayfa 13 sekli okunamadi."]
    stored = academy.store.get_document("d1")
    assert stored.topic_ids and all(academy.curriculum.exists(topic_id) for topic_id in stored.topic_ids)
    assert academy.document_analysis("d1") == analysis


def test_a_failed_analysis_clears_the_job_and_raises(build) -> None:
    academy = build(Gateway("bu JSON degil", "hala JSON degil"))
    seed(academy)
    events: list[dict] = []
    academy.subscribe(events.append)

    with pytest.raises(MedicalModelError):
        asyncio.run(academy.analyze_document("d1"))

    assert academy.document_analysis("d1") is None
    assert not [event for event in events if event.get("kind") == "document_analyzed"]


def test_comparison_labels_every_finding_and_keeps_its_caveat(build) -> None:
    academy = build(Gateway(comparison_reply(12)))
    seed(academy)

    result = asyncio.run(academy.compare_document("d1"))

    finding = result["findings"][0]
    assert finding["category"] == "possibly_incorrect" and finding["category_label"]
    assert finding["page"] == 12 and finding["page_unverified"] is False
    assert finding["support_label"]
    assert result["counts"] == {"possibly_incorrect": 1}
    assert "doğrula" in result["note"]
    assert academy.comparison("d1") == result


def test_a_comparison_page_the_material_never_had_is_marked_unverified(build) -> None:
    academy = build(Gateway(comparison_reply(999)))
    seed(academy)

    finding = asyncio.run(academy.compare_document("d1"))["findings"][0]

    assert finding["page"] is None and finding["page_unverified"] is True


def test_comparison_without_text_or_a_provider_refuses(build) -> None:
    offline = build(None)
    seed(offline)
    with pytest.raises(MedicalModelError):
        asyncio.run(offline.compare_document("d1"))

    empty = build(Gateway(comparison_reply(1)))
    empty.store.save_document(StudyDocument(document_id="d2", title="Bos", file_name="bos.pdf", sha256="sha-d2", status=DocumentStatus.READY))
    with pytest.raises(DocumentError, match="metin yok"):
        asyncio.run(empty.compare_document("d2"))


# ---------------------------------------------------------------------------
# notes
# ---------------------------------------------------------------------------


NOTES_REPLY = json.dumps(
    {
        "title": "Humerus özeti",
        "markdown": "## Humerus\n- Capitulum radius ile eklemleşir.",
        "cited_pages": [12],
        "high_yield": ["Capitulum radius ile eklemleşir."],
    }
)


def test_notes_cite_only_the_pages_the_model_actually_used(build) -> None:
    academy = build(Gateway(NOTES_REPLY))
    seed(academy)

    note = asyncio.run(academy.generate_notes(mode="short_notes", subject="anatomy", topic_id=ARM, document_ids=["d1"]))

    assert [reference.page_number for reference in note.references] == [12]
    assert note.content.startswith("## Humerus")
    assert academy.notes()[0]["note_id"] == note.note_id


def test_notes_need_a_provider(build) -> None:
    academy = build(None)
    seed(academy)

    with pytest.raises(MedicalModelError):
        asyncio.run(academy.generate_notes(mode="short_notes", subject="anatomy", topic_id=ARM, document_ids=["d1"]))


def test_a_note_can_be_deleted_once(build) -> None:
    academy = build(Gateway(NOTES_REPLY))
    seed(academy)
    note = asyncio.run(academy.generate_notes(mode="high_yield", subject="anatomy", topic_id=ARM, document_ids=["d1"]))

    assert academy.delete_note(note.note_id) is True
    assert academy.delete_note(note.note_id) is False
    assert academy.notes() == []


# ---------------------------------------------------------------------------
# the question bank
# ---------------------------------------------------------------------------


def test_the_bank_reports_the_defects_of_the_items_it_holds(build) -> None:
    academy = build(None)
    academy.store.save_question(question("q1"))
    academy.store.save_question(question("q2", options=[QuestionOption("A", "Ayni"), QuestionOption("B", "Ayni")]))

    payload = academy.question_bank()

    problems = {item["question_id"]: item["problems"] for item in payload["questions"]}
    assert problems["q1"] == []
    assert problems["q2"], "two identical options must be reported, not shown as a usable item"


def test_an_unknown_subject_filter_narrows_to_nothing_and_says_why(build) -> None:
    academy = build(None)
    academy.store.save_question(question("q1"))

    payload = academy.question_bank({"subject": "astroloji"})

    assert payload["questions"] == [] and payload["total"] == 0
    assert payload["problems"] == ["Bilinmeyen ders: astroloji"]


def test_the_bank_can_be_filtered_by_how_the_student_answered(build) -> None:
    academy = build(None)
    for index in range(3):
        academy.store.save_question(question(f"q{index}"))
    exam = asyncio.run(academy.generate_exam({"from_bank": True, "question_count": 3, "topic_ids": [ARM], "randomize": False}))
    academy.start_exam(exam["exam_id"])
    academy.answer(exam["exam_id"], "q0", "A")
    academy.answer(exam["exam_id"], "q1", "B")
    academy.finish_exam(exam["exam_id"])

    correct = academy.question_bank({"answered": "correct"})
    incorrect = academy.question_bank({"answered": "incorrect"})
    unanswered = academy.question_bank({"answered": "unanswered"})

    assert [item["question_id"] for item in correct["questions"]] == ["q0"]
    assert [item["question_id"] for item in incorrect["questions"]] == ["q1"]
    assert [item["question_id"] for item in unanswered["questions"]] == ["q2"]


def test_an_answer_key_can_be_set_but_never_to_a_letter_with_no_option(build) -> None:
    academy = build(None)
    academy.store.save_question(question("q1", correct_key=None))

    payload = academy.set_answer_key("q1", "b")
    assert payload["correct_key"] == "B"

    with pytest.raises(ValueError, match="seçenek yok"):
        academy.set_answer_key("q1", "F")

    assert academy.set_answer_key("q-missing", "A") is None


def test_clearing_an_answer_key_leaves_the_question_unkeyed(build) -> None:
    academy = build(None)
    academy.store.save_question(question("q1"))

    payload = academy.set_answer_key("q1", None)

    assert payload["correct_key"] is None
    assert academy.store.get_question("q1").correct_key is None


# ---------------------------------------------------------------------------
# professors
# ---------------------------------------------------------------------------


def test_a_new_profile_starts_empty_and_says_so(build) -> None:
    academy = build(None)

    payload = academy.create_professor("Prof. Dr. Ayşe Yılmaz", subject="anatomy")

    assert payload["sample_size"] == 0
    assert payload["basis"] == "Henüz soru yüklenmedi; profil boş."
    assert payload["directive"] is None
    assert payload["questions"] == []


def test_importing_questions_builds_a_profile_from_what_was_read(build) -> None:
    academy = build(None)
    profile = academy.create_professor("Hoca", subject="anatomy")
    stems = [
        "62 yasinda hasta acil servise basvurdu. Humerus cisim kirigi hangi siniri zedeler?",
        "Scapula uzerinde m. deltoideus'un baslangic yeri asagidakilerden hangisidir?",
        "Articulatio cubiti hangi eksende hangi harekete izin verir?",
    ]
    text = "\n\n".join(
        f"{index}. {stem}\nA) N. radialis\nB) N. medianus\nC) N. ulnaris\nD) N. axillaris"
        for index, stem in enumerate(stems, start=1)
    )

    result = asyncio.run(academy.import_questions(professor_id=profile["profile_id"], name=None, subject="anatomy", text=text, use_model=False))

    assert result["import"]["added"] == 3 and result["import"]["without_key"] == 3
    assert any("asla tahmin edilmez" in note for note in result["import"]["notes"])
    stored = academy.professor(profile["profile_id"])
    assert stored["sample_size"] == 3
    assert all(item["origin"] == QuestionOrigin.IMPORTED_EXAM for item in stored["questions"])
    assert all(item["correct_key"] is None for item in stored["questions"]), "no answer key was given, so none may be invented"


def test_importing_the_same_paper_twice_does_not_double_the_evidence(build) -> None:
    """Sample size is the basis of every claimed tendency, so a re-import
    must not make a two-question profile look like a four-question one."""
    academy = build(None)
    profile = academy.create_professor("Hoca", subject="anatomy")
    text = (
        "1. Humerus cisim kirigi hangi siniri zedeler?\nA) N. radialis\nB) N. medianus\nC) N. ulnaris\n\n"
        "2. Scapula uzerinde spina scapulae nerede sonlanir?\nA) Acromion\nB) Angulus inferior\nC) Processus coracoideus"
    )

    first = asyncio.run(academy.import_questions(professor_id=profile["profile_id"], name=None, subject="anatomy", text=text, use_model=False))
    second = asyncio.run(academy.import_questions(professor_id=profile["profile_id"], name=None, subject="anatomy", text=text, use_model=False))

    assert first["import"]["added"] == 2
    assert second["import"]["added"] == 0 and second["import"]["skipped"] == 2
    assert academy.professor(profile["profile_id"])["sample_size"] == 2


def test_resetting_a_profile_empties_its_evidence(build) -> None:
    academy = build(None)
    profile = academy.create_professor("Hoca", subject="anatomy")
    text = "1. Humerus kirigi hangi siniri zedeler?\nA) N. radialis\nB) N. medianus\nC) N. ulnaris"
    asyncio.run(academy.import_questions(professor_id=profile["profile_id"], name=None, subject="anatomy", text=text, use_model=False))

    reset = academy.reset_professor(profile["profile_id"])

    assert reset["sample_size"] == 0 and reset["questions"] == []
    assert academy.reset_professor("p-missing") is None


def test_deleting_a_profile_can_keep_or_drop_its_questions(build) -> None:
    academy = build(None)
    kept = academy.create_professor("Hoca A", subject="anatomy")
    dropped = academy.create_professor("Hoca B", subject="anatomy")
    text = "1. Humerus kirigi hangi siniri zedeler?\nA) N. radialis\nB) N. medianus\nC) N. ulnaris"
    asyncio.run(academy.import_questions(professor_id=kept["profile_id"], name=None, subject="anatomy", text=text, use_model=False))
    asyncio.run(academy.import_questions(professor_id=dropped["profile_id"], name=None, subject="anatomy", text=text, use_model=False))
    kept_ids = academy.store.get_professor(kept["profile_id"]).question_ids
    dropped_ids = academy.store.get_professor(dropped["profile_id"]).question_ids

    assert academy.delete_professor(kept["profile_id"]) is True
    assert academy.delete_professor(dropped["profile_id"], delete_questions=True) is True

    assert academy.store.get_questions(kept_ids), "questions outlive the profile unless asked otherwise"
    assert academy.store.get_questions(dropped_ids) == []
    assert academy.professors() == []


# ---------------------------------------------------------------------------
# progress and the anatomy lab
# ---------------------------------------------------------------------------


def test_progress_reports_only_attempts_that_happened(build) -> None:
    academy = build(None)
    empty = academy.progress()
    assert empty["summary"]["attempts"] == 0 and empty["weak"] == [] and empty["all"] == []

    academy.record_anatomy_answer("humerus", "caput_humeri", correct=False)
    academy.record_anatomy_answer("humerus", "caput_humeri", correct=False)

    filled = academy.progress()
    assert filled["summary"]["attempts"] == 2 and filled["summary"]["correct"] == 0
    assert filled["all"][0]["concept_id"] == "anatomy.humerus.caput_humeri"


def test_the_lab_reports_which_structures_have_a_licensed_model(build) -> None:
    academy = build(None)

    payload = academy.anatomy_structures()

    assert payload["hierarchy"], "the shipped anatomy data must produce a hierarchy"
    assert payload["assets"]["available"] == []
    assert payload["source"], "the data source must be named, not implied"


def test_a_structure_without_a_model_says_so_instead_of_drawing_one(build) -> None:
    academy = build(None)
    structure_id = academy.anatomy.all()[0].structure_id

    described = academy.anatomy_structure(structure_id)
    mesh = academy.anatomy_mesh(structure_id)

    assert described["model"]["available"] is False
    assert mesh["available"] is False and mesh["reason"]


def test_an_unknown_structure_has_no_card_and_no_quiz(build) -> None:
    academy = build(None)

    assert academy.anatomy_structure("zumrut_kemigi") is None
    assert academy.anatomy_quiz("zumrut_kemigi") == []


def test_an_anatomy_quiz_is_bounded_and_answerable(build) -> None:
    academy = build(None)
    structure_id = academy.anatomy.all()[0].structure_id

    items = academy.anatomy_quiz(structure_id, count=100)

    assert len(items) <= 20
    for item in items:
        assert item["options"] and any(option["key"] == item["correct_key"] for option in item["options"])
