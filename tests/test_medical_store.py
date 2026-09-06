"""Medical Academy store: typed round trips, honest filters, durable reopen."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from app.medical import store as store_module
from app.medical.models import (
    ConceptMastery, Difficulty, DocumentChunk, DocumentPage, DocumentStatus, EvidenceSupport,
    Exam, ExamAttempt, ExamConfig, MasteryLevel, ProfessorProfile, Question, QuestionAttempt,
    QuestionOption, QuestionOrigin, SourceReference, StudyDocument, StudyMode, StudyNote,
    StudySession, StyleFeature, Subject, attempt_from_dict, document_from_dict, dumps,
    exam_from_dict, mastery_from_dict, note_from_dict, professor_from_dict, question_from_dict,
    session_from_dict, to_plain,
)
from app.medical.store import MedicalStore

BASE = datetime(2026, 4, 1, 8, 0, tzinfo=timezone.utc)


def make_question(index: int, **overrides) -> Question:
    """A minimal answerable question with a distinct creation time."""
    fields = {
        "subject": "anatomy",
        "stem": f"Soru {index}",
        "options": [QuestionOption("A", f"secenek {index}"), QuestionOption("B", "diger")],
        "correct_key": "A",
        "created_at": BASE + timedelta(minutes=index),
    }
    fields.update(overrides)
    return Question(question_id=f"q{index}", **fields)


# ---------------------------------------------------------------------------
# serialization
# ---------------------------------------------------------------------------


def test_to_plain_and_dumps_flatten_a_model_into_sorted_readable_json() -> None:
    config = ExamConfig(subjects=[Subject.ANATOMY], difficulty=Difficulty.HARD, title="Deneme")
    plain = to_plain(Exam("e1", "Final", config, ["q1"], created_at=BASE))

    assert plain["created_at"] == "2026-04-01T08:00:00+00:00" and plain["finished_at"] is None
    assert plain["config"]["subjects"] == ["anatomy"]  # StrEnum collapses to its value
    assert plain["config"]["difficulty"] == 5  # IntEnum collapses to its number
    assert plain["config"]["knowledge_priority"] == "balanced"
    assert to_plain({"level": MasteryLevel.WEAK, "at": BASE}) == {"level": "weak", "at": plain["created_at"]}

    note = StudyNote("n1", "Kalp döngüsü", "Özet", subject="anatomy", created_at=BASE,
                     references=[SourceReference("d1", 7, title="Atlas")])
    payload = json.loads(dumps(note))
    assert "Kalp döngüsü" in dumps(note)  # Turkish stays readable, never \u escaped
    assert list(payload) == sorted(payload) and payload["mode"] == StudyMode.SHORT_NOTES.value
    assert payload["references"] == [{"document_id": "d1", "page_number": 7, "chunk_id": None, "quote": "", "title": "Atlas"}]
    assert note_from_dict(payload) == note  # and the whole note comes back unchanged


def test_builders_ignore_unknown_keys_and_rebuild_nested_collections() -> None:
    question = question_from_dict({
        "question_id": "q1", "subject": "anatomy", "stem": "Soru", "correct_key": "A", "difficulty": None,
        "options": [{"key": "a", "text": "ilk"}, "bozuk"],
        "references": [{"document_id": "d1", "page_number": "3"}, 5],
        "unknown_column": "yoksay",
    })
    assert question.options == [QuestionOption("A", "ilk")]  # keys normalised, junk entries dropped
    assert question.references == [SourceReference("d1", 3)] and question.difficulty == Difficulty.MEDIUM
    assert question.has_answer_key is True and question.option("a").text == "ilk"

    attempt = attempt_from_dict({
        "attempt_id": "a1", "exam_id": "e1", "score": 50.0, "unknown_column": 1,
        "answers": {"q1": {"question_id": "q1", "answer_key": "B", "correct": False, "junk": 1}, "q2": "bozuk"},
    })
    assert list(attempt.answers) == ["q1"] and attempt.answers["q1"].correct is False
    assert attempt.answers["q1"].answered_at.tzinfo is not None

    mastery = mastery_from_dict({"concept_id": "c1", "attempts": 4, "correct": 3, "recent": [1, 0], "junk": 1})
    assert mastery.recent == [True, False] and mastery.accuracy == 0.75

    feature = {"feature_id": "f", "label_tr": "L", "observed": 1, "total": 2, "level": "orta"}
    professor = professor_from_dict({"profile_id": "p1", "name": "Ahmet", "features": [feature, 3]})
    assert [item.ratio for item in professor.features] == [0.5]
    assert professor.confidence == EvidenceSupport.NONE

    assert exam_from_dict({"exam_id": "e1", "title": "t", "config": "bozuk", "question_ids": []}).config == ExamConfig()
    assert session_from_dict({"session_id": "s1", "junk": 1}).mode == StudyMode.TEACH


def test_a_missing_or_unparsable_timestamp_keeps_the_field_default() -> None:
    document = document_from_dict(
        {"document_id": "d1", "title": "T", "file_name": "f.pdf", "sha256": "s", "imported_at": "yarın", "indexed_at": ""}
    )
    assert document.imported_at.tzinfo is not None  # a required time falls back to utc_now, not None
    assert document.indexed_at is None  # an optional time stays optional

    bare = document_from_dict({"document_id": "d1", "title": "T", "file_name": "f.pdf", "sha256": "s"})
    assert bare.imported_at.tzinfo is not None and bare.status == DocumentStatus.PENDING
    assert session_from_dict({"session_id": "s1", "updated_at": None}).updated_at.tzinfo is not None
    assert mastery_from_dict({"concept_id": "c", "last_attempt_at": "bozuk"}).last_attempt_at is None


# ---------------------------------------------------------------------------
# documents, pages, chunks, images
# ---------------------------------------------------------------------------


def test_memory_store_keeps_everything_off_disk_and_round_trips_documents(tmp_path) -> None:
    store = MedicalStore()
    assert store.path is None and store.persistent is False and store.revision == 0

    early = StudyDocument("d1", "Atlas", "a.pdf", "sha-a", subject="anatomy", imported_at=BASE)
    late = StudyDocument("d2", "Histoloji", "b.pdf", "sha-b", subject="histology", imported_at=BASE + timedelta(days=1))
    store.save_document(early)
    store.save_document(late)
    assert store.revision == 2  # every write bumps the global revision

    assert dumps(store.get_document("d1")) == dumps(early) and store.get_document("nope") is None
    assert [item.document_id for item in store.list_documents()] == ["d2", "d1"]  # newest import first
    assert [item.document_id for item in store.list_documents(subject="anatomy")] == ["d1"]
    assert store.find_document_by_sha("sha-b").document_id == "d2"
    assert store.find_document_by_sha("sha-missing") is None
    assert list(tmp_path.rglob("*")) == []  # an in-memory store never touches the filesystem


def test_only_writes_the_search_index_reads_bump_the_content_revision() -> None:
    """Rebuilding the index costs a pass over the whole library, so the counter
    the retriever watches must move for those four tables and nothing else."""
    store = MedicalStore()
    assert store.revision == 0 and store.content_revision == 0

    store.save_document(StudyDocument("d1", "Atlas", "a.pdf", "sha-a", imported_at=BASE))
    store.replace_chunks("d1", [DocumentChunk("c1", "d1", 1, 0, "Humerus govdesi.")])
    store.save_note(StudyNote("n1", "Not", "İçerik"))
    store.save_question(make_question(1))
    assert store.content_revision == 4  # documents, chunks, notes, questions: all indexed

    indexed, writes = store.content_revision, store.revision
    store.save_page(DocumentPage("d1", 1, "sayfa metni"))
    store.put_page_image("d1", 1, 1.5, b"\x00png")
    store.save_exam(Exam("e1", "Deneme", ExamConfig(), ["q1"], created_at=BASE))
    store.save_attempt(ExamAttempt("a1", "e1", started_at=BASE))
    store.save_mastery(ConceptMastery("c1", attempts=1, correct=1))
    store.save_professor(ProfessorProfile("p1", "Ahmet"))
    store.save_learned_concept({"concept_id": "lc1", "name": "Kalp"})
    store.save_session(StudySession(subject="anatomy"))
    # None of these is in the index. The session is the one that matters: the
    # tutor saves it on every medical turn, and it used to cost a full rebuild.
    assert store.content_revision == indexed
    assert store.revision == writes + 8  # they are still writes, just not indexed ones

    # Removals invalidate too: an index that keeps a deleted page cites it.
    store.delete_question("q1")
    store.delete_note("n1")
    store.delete_document("d1")
    assert store.content_revision == indexed + 3

    store.delete_exam("e1")
    store.clear_mastery()
    store.delete_professor("p1")
    assert store.content_revision == indexed + 3


def test_pages_round_trip_and_are_addressable_by_number_and_range() -> None:
    store = MedicalStore()
    store.save_page(DocumentPage("d1", 2, text="iki"))
    store.save_pages([DocumentPage("d1", 1, text="bir", headings=["Giriş"], visual_status="pending")])
    assert [page.page_number for page in store.get_pages("d1")] == [1, 2]
    assert store.get_page("d1", 1).needs_visual_analysis is True
    assert store.get_page("d1", 1).headings == ["Giriş"] and store.get_page("d1", 9) is None
    assert [page.page_number for page in store.get_pages("d1", page_from=2)] == [2]
    assert [page.page_number for page in store.get_pages("d1", page_to=1)] == [1]

    store.save_page(DocumentPage("d1", 1, text="bir (düzeltildi)"))  # the same key upserts
    assert store.get_page("d1", 1).text == "bir (düzeltildi)" and len(store.get_pages("d1")) == 2

    store.put_page_image("d1", 1, 2.0, b"\x89PNG-buyuk")
    store.put_page_image("d1", 1, 1.0, b"\x89PNG-kucuk")
    assert store.get_page_image("d1", 1, 2.0) == b"\x89PNG-buyuk"  # raw bytes come back as bytes
    assert store.get_page_image("d1", 1, 1.0) == b"\x89PNG-kucuk"  # scale is part of the key
    assert store.get_page_image("d1", 2, 2.0) is None
    store.put_page_image("d1", 1, 2.0, b"yenilendi")
    assert store.get_page_image("d1", 1, 2.0) == b"yenilendi"


def test_replace_chunks_is_total_and_chunks_are_filtered_by_document_and_page() -> None:
    store = MedicalStore()
    assert store.replace_chunks("d1", [DocumentChunk("c2", "d1", 1, 1, "ikinci"), DocumentChunk("c1", "d1", 1, 0, "birinci")]) == 2
    assert [chunk.chunk_id for chunk in store.chunks()] == ["c1", "c2"]  # ordered within the page
    assert store.get_chunk("c1").text == "birinci" and store.get_chunk("nope") is None

    assert store.replace_chunks("d1", [DocumentChunk("c1", "d1", 1, 0, "bir"), DocumentChunk("c2", "d1", 5, 0, "bes")]) == 2
    store.replace_chunks("d2", [DocumentChunk("c3", "d2", 2, 0, "iki")])
    assert [chunk.chunk_id for chunk in store.chunks(document_ids=["d2"])] == ["c3"]
    assert [chunk.chunk_id for chunk in store.chunks(document_ids=[])] == ["c1", "c2", "c3"]
    assert [chunk.chunk_id for chunk in store.chunks(page_from=2, page_to=5)] == ["c2", "c3"]
    assert store.chunks(document_ids=["d1"], page_from=2, page_to=4) == []

    assert store.replace_chunks("d1", [DocumentChunk("c9", "d1", 3, 0, "yeni")]) == 1
    assert [chunk.chunk_id for chunk in store.chunks()] == ["c9", "c3"]  # d1's old set is gone, not merged
    assert store.get_chunk("c1") is None
    assert store.replace_chunks("d1", []) == 0 and [c.chunk_id for c in store.chunks()] == ["c3"]


def test_delete_document_takes_its_pages_chunks_and_images_with_it() -> None:
    store = MedicalStore()
    for suffix in ("1", "2"):
        store.save_document(StudyDocument(f"d{suffix}", "Atlas", "a.pdf", f"sha-{suffix}"))
        store.save_page(DocumentPage(f"d{suffix}", 1, text="bir"))
        store.replace_chunks(f"d{suffix}", [DocumentChunk(f"c{suffix}", f"d{suffix}", 1, 0, "bir")])
        store.put_page_image(f"d{suffix}", 1, 1.0, b"png")

    assert store.delete_document("d1") is True and store.get_document("d1") is None
    assert store.get_pages("d1") == [] and store.chunks(document_ids=["d1"]) == []
    assert store.get_page_image("d1", 1, 1.0) is None
    assert store.get_pages("d2") and store.chunks(document_ids=["d2"])  # the neighbour is untouched
    assert store.get_page_image("d2", 1, 1.0) == b"png"
    assert store.delete_document("d1") is False


# ---------------------------------------------------------------------------
# questions
# ---------------------------------------------------------------------------


def test_query_questions_narrows_on_every_filter() -> None:
    store = MedicalStore()
    store.save_questions([
        make_question(1, topic_id="anatomi.ust", origin=QuestionOrigin.GENERATED, professor_id="p1",
                      difficulty=Difficulty.EASY, concept_ids=["c1"], references=[SourceReference("d1", 3)]),
        make_question(2, topic_id="anatomi.ust.kol", origin=QuestionOrigin.IMPORTED_EXAM, professor_id="p2",
                      difficulty=Difficulty.HARD, concept_ids=["c2"], correct_key=None,
                      references=[SourceReference("d2", 1)]),
        make_question(3, subject="histology", topic_id="histo.epitel", stem="Epitel nedir", origin=QuestionOrigin.MANUAL),
    ])

    def ids(**filters) -> list[str]:
        return [item.question_id for item in store.query_questions(**filters)]

    assert ids() == ["q3", "q2", "q1"]  # newest first
    assert ids(subject="anatomy") == ["q2", "q1"] and ids(subject="histology") == ["q3"]
    assert ids(topic_id="anatomi.ust") == ["q2", "q1"]  # a topic filter also takes its children
    assert ids(topic_id="anatomi.ust.kol") == ["q2"] and ids(origin="imported_exam") == ["q2"]
    assert ids(professor_id="p1") == ["q1"] and ids(difficulty=5) == ["q2"]
    assert ids(document_id="d2") == ["q2"] and ids(concept_id="c1") == ["q1"]
    assert ids(text="EPITEL") == ["q3"] and ids(text="secenek 1") == ["q1"]  # stem and options, case-blind
    assert ids(text="bulunmayan") == [] and ids(document_id="yok") == []
    assert ids(with_answer_key=True) == ["q3", "q1"] and ids(with_answer_key=False) == ["q2"]
    assert ids(subject="anatomy", difficulty=1) == ["q1"]  # filters compose
    assert ids(subject="histology", professor_id="p1") == []
    assert ids(limit=2) == ["q3", "q2"] and ids(limit=0) == ["q3"]  # a nonsense limit still yields one row


def test_query_questions_reaches_matches_that_sit_behind_a_wall_of_newer_rows() -> None:
    store = MedicalStore()
    store.save_questions([make_question(index) for index in range(3, 40)])  # newer, matching nothing below
    store.save_questions([
        make_question(1, concept_ids=["hedef"], references=[SourceReference("d1", 1)], stem="Aranan gövde"),
        make_question(2, concept_ids=["hedef"], references=[SourceReference("d1", 2)], correct_key=None),
    ])

    def ids(**filters) -> list[str]:
        return [item.question_id for item in store.query_questions(**filters)]

    assert ids(concept_id="hedef", limit=2) == ["q2", "q1"]  # both, though 37 newer rows sit in front
    assert ids(document_id="d1", limit=2) == ["q2", "q1"]
    assert ids(text="aranan", limit=2) == ["q1"]
    assert ids(with_answer_key=False, limit=5) == ["q2"]
    assert ids(subject="anatomy", concept_id="hedef", limit=2) == ["q2", "q1"]  # sql and body filters compose
    assert ids(concept_id="hedef", limit=1) == ["q2"]  # the limit still caps the answer
    assert ids(concept_id="yok", limit=2) == []  # an exhausted table just ends the scan


def test_a_filtered_scan_walks_past_its_page_size_to_the_oldest_match() -> None:
    store = MedicalStore()
    store.save_questions([make_question(index) for index in range(2, 220)])
    store.save_question(make_question(1, concept_ids=["hedef"]))  # older than every one of them

    assert [item.question_id for item in store.query_questions(concept_id="hedef", limit=5)] == ["q1"]
    assert store.query_questions(concept_id="hedef", subject="histology", limit=5) == []


def test_a_topic_prefix_match_reads_underscores_as_letters_not_as_wildcards() -> None:
    store = MedicalStore()
    store.save_questions([
        make_question(1, topic_id="upper_limb"),
        make_question(2, topic_id="upper_limb.kol"),
        make_question(3, topic_id="upperXlimb.kol"),
        make_question(4, topic_id="upper_limbX.kol"),
        make_question(5, topic_id="100%kemik.el"),
        make_question(6, topic_id="100Xkemik.el"),
    ])

    def ids(**filters) -> list[str]:
        return [item.question_id for item in store.query_questions(**filters)]

    assert ids(topic_id="upper_limb") == ["q2", "q1"]  # the topic and its children, nothing shaped like them
    assert ids(topic_id="upperXlimb") == ["q3"]
    assert ids(topic_id="100%kemik") == ["q5"]  # % is a literal in a topic id as well


def test_re_saving_a_record_moves_it_to_the_front_of_the_indexed_ordering() -> None:
    store = MedicalStore()
    later = BASE + timedelta(days=1)
    newest = later + timedelta(hours=1)

    store.save_document(StudyDocument("d1", "Atlas", "a.pdf", "sha-a", imported_at=BASE))
    store.save_document(StudyDocument("d2", "Histoloji", "b.pdf", "sha-b", imported_at=later))
    store.save_document(StudyDocument("d1", "Atlas", "a.pdf", "sha-a", imported_at=newest))
    assert [item.document_id for item in store.list_documents()] == ["d1", "d2"]

    store.save_note(StudyNote("n1", "Kalp", "İçerik", created_at=BASE))
    store.save_note(StudyNote("n2", "Epitel", "İçerik", created_at=later))
    store.save_note(StudyNote("n1", "Kalp", "Güncel içerik", created_at=newest))
    assert [item.note_id for item in store.list_notes()] == ["n1", "n2"]

    store.save_questions([make_question(1), make_question(2)])
    store.save_question(make_question(1, created_at=newest))
    assert [item.question_id for item in store.query_questions()] == ["q1", "q2"]

    store.save_exam(Exam("e1", "Sınav", ExamConfig(), [], created_at=BASE))
    store.save_exam(Exam("e2", "İkinci", ExamConfig(), [], created_at=later))
    store.save_exam(Exam("e1", "Sınav", ExamConfig(), ["q1"], created_at=newest))
    assert [item.exam_id for item in store.list_exams()] == ["e1", "e2"]

    store.save_attempt(ExamAttempt("a1", "e1", started_at=BASE))
    store.save_attempt(ExamAttempt("a2", "e1", started_at=later))
    store.save_attempt(ExamAttempt("a1", "e1", started_at=newest, score=90.0))
    assert [item.attempt_id for item in store.attempts_for_exam("e1")] == ["a1", "a2"]
    assert [item.attempt_id for item in store.list_attempts(limit=1)] == ["a1"]
    assert store.latest_attempt("e1").score == 90.0  # one row, the fresh content


def test_questions_round_trip_upsert_and_are_counted_by_origin() -> None:
    store = MedicalStore()
    question = make_question(1, references=[SourceReference("d1", 4, quote="alıntı")])
    store.save_question(question)

    stored = store.get_question("q1")
    assert dumps(stored) == dumps(question) and store.get_question("nope") is None
    assert stored.references[0].label() == "d1, s. 4"  # a citation keeps its page anchor
    assert [item.question_id for item in store.get_questions(["q1", "nope"])] == ["q1"]

    store.save_question(make_question(1, subject="histology", origin=QuestionOrigin.MANUAL, stem="Yeni gövde"))
    assert store.get_question("q1").stem == "Yeni gövde"  # same id, one row, new content
    assert store.query_questions(subject="histology") and store.query_questions(subject="anatomy") == []

    store.save_question(make_question(2, origin=QuestionOrigin.MANUAL))
    assert store.count_questions() == {"manual": 2, "total": 2}
    assert store.delete_question("q2") is True and store.delete_question("q2") is False
    assert store.count_questions() == {"manual": 1, "total": 1}
    assert MedicalStore().count_questions() == {"total": 0}


# ---------------------------------------------------------------------------
# notes, exams, attempts
# ---------------------------------------------------------------------------


def test_list_notes_selects_the_subject_before_it_applies_the_limit() -> None:
    store = MedicalStore()
    store.save_note(StudyNote("n0", "Epitel", "İçerik", subject="histology", created_at=BASE))
    for index in range(1, 6):
        store.save_note(
            StudyNote(f"n{index}", "Kalp", "İçerik", subject="anatomy", created_at=BASE + timedelta(hours=index))
        )

    assert [item.note_id for item in store.list_notes(subject="histology", limit=1)] == ["n0"]  # oldest, still found
    assert [item.note_id for item in store.list_notes(subject="anatomy", limit=2)] == ["n5", "n4"]
    assert [item.note_id for item in store.list_notes(limit=2)] == ["n5", "n4"]  # unfiltered, the limit stands
    assert store.list_notes(subject="yok", limit=3) == []


def test_notes_exams_and_attempts_round_trip_with_the_newest_first() -> None:
    store = MedicalStore()
    note = StudyNote("n1", "Kalp", "İçerik", subject="anatomy", references=[SourceReference("d1", 2)], created_at=BASE)
    store.save_note(note)
    store.save_note(StudyNote("n2", "Epitel", "İçerik", subject="histology", created_at=BASE + timedelta(hours=1)))
    assert store.get_note("n1") == note and store.get_note("nope") is None
    assert [item.note_id for item in store.list_notes()] == ["n2", "n1"]
    assert [item.note_id for item in store.list_notes(limit=1)] == ["n2"]
    assert store.delete_note("n1") is True and store.delete_note("n1") is False
    assert [item.note_id for item in store.list_notes()] == ["n2"]

    exam = Exam("e1", "Sınav", ExamConfig(subjects=["anatomy"], question_count=2, title="Deneme"), ["q1", "q2"], created_at=BASE)
    store.save_exam(exam)
    store.save_exam(Exam("e2", "İkinci", ExamConfig(), [], created_at=BASE + timedelta(days=1)))
    assert store.get_exam("e1") == exam and store.get_exam("nope") is None
    assert store.get_exam("e1").config.title == "Deneme"  # the nested config survives storage
    assert [item.exam_id for item in store.list_exams()] == ["e2", "e1"]

    first = ExamAttempt("a1", "e1", started_at=BASE, answers={"q1": QuestionAttempt("q1", "A", True, answered_at=BASE)}, score=50.0)
    store.save_attempt(first)
    store.save_attempt(ExamAttempt("a2", "e1", started_at=BASE + timedelta(hours=1), score=80.0))
    assert store.get_attempt("a1") == first
    assert [item.attempt_id for item in store.attempts_for_exam("e1")] == ["a2", "a1"]
    assert store.latest_attempt("e1").attempt_id == "a2"
    assert store.latest_attempt("e2") is None  # no attempt, no invented one
    assert [item.attempt_id for item in store.list_attempts(limit=1)] == ["a2"]

    assert store.delete_exam("e1") is True and store.delete_exam("e1") is False
    assert store.attempts_for_exam("e1") == [] and store.get_attempt("a1") is None  # attempts die with their exam


# ---------------------------------------------------------------------------
# mastery, professors, session, learned concepts
# ---------------------------------------------------------------------------


def test_mastery_professors_and_learned_concepts_round_trip() -> None:
    store = MedicalStore()
    mastery = ConceptMastery("c1", "anatomy", attempts=4, correct=3, recent=[True, False],
                             level=MasteryLevel.MODERATE, confusions={"c2": 2})
    store.save_mastery(mastery)
    assert store.get_mastery("c1") == mastery and store.get_mastery("nope") is None
    assert store.get_mastery("c1").accuracy == 0.75
    store.save_mastery(ConceptMastery("c1", attempts=6))  # an upsert, not a second row
    assert len(store.list_mastery()) == 1
    assert store.clear_mastery() == 1 and store.list_mastery() == []

    store.save_professor(ProfessorProfile("p2", "zeynep", features=[StyleFeature("f", "L", 1, 2, "orta")]))
    store.save_professor(ProfessorProfile("p1", "Ahmet"))
    assert [item.profile_id for item in store.list_professors()] == ["p1", "p2"]  # by name, case-blind
    assert store.get_professor("p2").features[0].ratio == 0.5 and store.get_professor("nope") is None
    assert store.delete_professor("p1") is True and store.delete_professor("p1") is False

    learned = {"concept_id": "lc1", "name": "Kalp", "seen_at": BASE.isoformat()}
    store.save_learned_concept({"concept_id": "lc1", "name": "Kalp", "seen_at": BASE})
    assert store.list_learned_concepts() == [learned]
    revision = store.revision
    store.save_learned_concept({"concept_id": "   "})
    store.save_learned_concept({})
    assert store.list_learned_concepts() == [learned] and store.revision == revision  # a nameless concept is not a write


def test_a_session_is_defaulted_before_it_is_saved_and_keyed_after() -> None:
    store = MedicalStore()
    fresh = store.get_session()
    assert fresh.session_id == "default" and fresh.mode == StudyMode.TEACH
    assert fresh.subject is None and fresh.document_ids == []
    stale = datetime(2020, 1, 1, tzinfo=timezone.utc)
    fresh.subject, fresh.document_ids, fresh.updated_at = "anatomy", ["d1"], stale
    saved = store.save_session(fresh)
    assert saved.updated_at > stale  # saving stamps the session itself

    reloaded = store.get_session()
    assert reloaded.subject == "anatomy" and reloaded.document_ids == ["d1"]
    assert reloaded.updated_at == saved.updated_at
    assert store.get_session("other").subject is None  # sessions are keyed, never shared


# ---------------------------------------------------------------------------
# persistence
# ---------------------------------------------------------------------------


def test_a_reopened_store_holds_the_same_records_and_the_same_counts(tmp_path) -> None:
    tables = ("documents", "pages", "chunks", "notes", "questions", "exams", "attempts", "mastery", "professors")
    assert MedicalStore().summary() == dict.fromkeys(tables, 0) | {"persistent": False}

    path = tmp_path / "state" / "medical.sqlite3"
    store = MedicalStore(path)
    assert store.persistent is True and store.path == path and path.exists()

    document = StudyDocument("d1", "Anatomi Atlası", "a.pdf", "sha-a", subject="anatomy", topic_ids=["t1"],
                             key_terms=["kalp"], page_count=3, status=DocumentStatus.READY,
                             imported_at=BASE, indexed_at=BASE + timedelta(minutes=5))
    store.save_document(document)
    store.save_pages([DocumentPage("d1", 1, text="bir"), DocumentPage("d1", 2, text="iki")])
    store.replace_chunks("d1", [DocumentChunk("c1", "d1", 1, 0, "birinci")])
    store.put_page_image("d1", 1, 1.5, b"\x00\x01png")
    store.save_question(make_question(1, references=[SourceReference("d1", 2, quote="alıntı")]))
    store.save_note(StudyNote("n1", "Kalp", "İçerik", created_at=BASE))
    store.save_exam(Exam("e1", "Sınav", ExamConfig(title="Deneme"), ["q1"], created_at=BASE))
    store.save_attempt(ExamAttempt("a1", "e1", started_at=BASE, score=60.0))
    store.save_mastery(ConceptMastery("c1", attempts=2, correct=1))
    store.save_professor(ProfessorProfile("p1", "Ahmet"))
    store.save_session(StudySession(subject="anatomy"))
    counts = dict.fromkeys(tables, 1) | {"pages": 2, "persistent": True}
    assert store.summary() == counts
    store.close()
    store.close()  # closing twice is harmless

    reopened = MedicalStore(path)
    assert reopened.revision == 0  # a fresh handle starts a fresh revision count
    assert reopened.summary() == counts  # nothing was lost between the two handles
    assert dumps(reopened.get_document("d1")) == dumps(document)
    assert reopened.get_document("d1").indexed_at == document.indexed_at
    assert reopened.get_page("d1", 1).text == "bir" and reopened.get_chunk("c1").text == "birinci"
    assert reopened.get_page_image("d1", 1, 1.5) == b"\x00\x01png"
    assert reopened.get_question("q1").references[0].quote == "alıntı"
    assert reopened.get_note("n1").title == "Kalp" and reopened.get_exam("e1").config.title == "Deneme"
    assert reopened.latest_attempt("e1").score == 60.0
    assert reopened.get_mastery("c1").attempts == 2 and reopened.get_professor("p1").name == "Ahmet"
    assert reopened.get_session().subject == "anatomy"

    reopened.delete_document("d1")
    after = reopened.summary()
    assert (after["documents"], after["pages"], after["chunks"]) == (0, 0, 0)
    assert after["questions"] == 1 and after["notes"] == 1  # unrelated tables are untouched
    reopened.close()


# ---------------------------------------------------------------------------
# module surface
# ---------------------------------------------------------------------------


def test_the_store_module_carries_no_unused_timestamp_parser() -> None:
    assert not hasattr(store_module, "parse_timestamp")  # timestamp parsing belongs to the model builders
    assert document_from_dict({"document_id": "d1", "title": "T", "file_name": "f.pdf", "sha256": "s",
                               "imported_at": BASE.isoformat()}).imported_at == BASE
