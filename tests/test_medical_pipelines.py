"""The pipeline layer: schema validation, structured model calls, search, retrieval.

These four modules stand between the study material and every answer the
academy gives. A silent failure here is the dangerous kind: a schema that
accepts a malformed answer key, a repair round that never happens, a
retriever that returns a page number for a chunk that does not exist, or
an excerpt trimmed so quietly that the tutor reports the page as complete.
"""

from __future__ import annotations

import asyncio
import json

import pytest

from app.medical.model import (
    MedicalModelClient,
    MedicalModelError,
    extract_json,
)
from app.medical.models import DocumentChunk, StudyDocument
from app.medical.retrieval import (
    MAX_BLOCK_CHARS,
    TRUNCATION_MARK,
    RetrievalScope,
    Retriever,
)
from app.medical.schemas import (
    QUESTION_ITEM_SCHEMA,
    QUESTIONS_SCHEMA,
    coerce_strings,
    validate,
)
from app.medical.search import SYNONYM_WEIGHT, SearchDocument, SearchIndex
from app.medical.text import stem
from app.medical.store import MedicalStore
from app.medical.terminology import default_terminology


def item(**overrides) -> dict:
    """A question item that validates cleanly before any override."""
    data = {
        "stem": "Humerus distal ucunda radius ile eklemlesen yapi hangisidir?",
        "options": [
            {"key": "A", "text": "Capitulum humeri"},
            {"key": "B", "text": "Trochlea humeri"},
        ],
        "correct_key": "A",
        "explanation": "Capitulum, radius basi ile eklemlesir.",
        "concept": "Articulatio cubiti",
    }
    data.update(overrides)
    return data


class Reply:
    def __init__(self, text: str) -> None:
        self.text = text


class Gateway:
    """Records what the client asked for and replies from a script."""

    def __init__(self, *replies: str) -> None:
        self.replies = list(replies)
        self.calls: list[dict] = []

    async def generate(self, request, context, **kwargs):
        self.calls.append(
            {
                "prompt": request.text,
                "metadata": dict(request.metadata),
                "source": request.source,
                "kwargs": kwargs,
            }
        )
        if not self.replies:
            raise AssertionError("the client asked for more replies than the test scripted")
        return Reply(self.replies.pop(0))


class Hanging:
    async def generate(self, request, context, **kwargs):
        await asyncio.sleep(30)
        raise AssertionError("the timeout should have fired first")


class Exploding:
    async def generate(self, request, context, **kwargs):
        raise ConnectionResetError("provider dropped the connection")


def chunk(chunk_id: str, page: int, text: str, *, index: int = 0, kind: str = "text", document_id: str = "d1") -> DocumentChunk:
    return DocumentChunk(
        chunk_id=chunk_id,
        document_id=document_id,
        page_number=page,
        index_in_page=index,
        text=text,
        kind=kind,
    )


def document(document_id: str = "d1", title: str = "Kemik Sistemi") -> StudyDocument:
    return StudyDocument(
        document_id=document_id,
        title=title,
        file_name=f"{document_id}.pdf",
        sha256=f"sha-{document_id}",
        page_count=40,
    )


# ---------------------------------------------------------------------------
# schemas: validation
# ---------------------------------------------------------------------------


def test_a_well_formed_question_item_produces_no_problems() -> None:
    assert validate(item(), QUESTION_ITEM_SCHEMA) == []


def test_a_missing_required_field_is_named_with_its_path() -> None:
    data = item()
    del data["correct_key"]

    problems = validate(data, QUESTION_ITEM_SCHEMA)

    assert problems == ["$.correct_key: missing"]


def test_an_answer_key_outside_the_enum_is_rejected() -> None:
    problems = validate(item(correct_key="Z"), QUESTION_ITEM_SCHEMA)

    assert problems and "correct_key" in problems[0] and "not in" in problems[0]


def test_a_wrong_type_stops_the_walk_at_that_node_instead_of_cascading() -> None:
    problems = validate(item(options="A) Capitulum"), QUESTION_ITEM_SCHEMA)

    assert problems == ["$.options: expected array"]


def test_nested_option_problems_carry_their_index() -> None:
    problems = validate(
        item(options=[{"key": "A", "text": "Capitulum"}, {"key": "B"}]),
        QUESTION_ITEM_SCHEMA,
    )

    assert problems == ["$.options[1].text: missing"]


def test_an_integer_below_its_minimum_is_reported() -> None:
    assert validate(item(difficulty=0), QUESTION_ITEM_SCHEMA) == ["$.difficulty: below minimum 1"]


def test_an_integer_above_its_maximum_is_reported() -> None:
    assert validate(item(difficulty=9), QUESTION_ITEM_SCHEMA) == ["$.difficulty: above maximum 5"]


def test_a_boolean_is_not_treated_as_a_number_for_range_checks() -> None:
    schema = {"type": "boolean", "minimum": 1}

    assert validate(True, schema) == []


def test_an_over_long_string_is_reported_with_its_limit() -> None:
    problems = validate(item(concept="x" * 200), QUESTION_ITEM_SCHEMA)

    assert problems == ["$.concept: longer than 160 characters"]


def test_too_few_options_are_rejected_so_a_one_option_question_never_survives() -> None:
    problems = validate(item(options=[{"key": "A", "text": "Tek secenek"}]), QUESTION_ITEM_SCHEMA)

    assert problems == ["$.options: fewer than 2 items"]


def test_an_empty_question_list_is_rejected_by_the_batch_schema() -> None:
    assert validate({"questions": []}, QUESTIONS_SCHEMA) == ["$.questions: fewer than 1 items"]


def test_unknown_extra_keys_are_ignored_rather_than_failing_the_reply() -> None:
    assert validate(item(model_note="I was unsure here"), QUESTION_ITEM_SCHEMA) == []


def test_every_problem_in_a_batch_is_reported_not_only_the_first() -> None:
    problems = validate(
        {"questions": [item(correct_key="Z"), item(difficulty=0)]},
        QUESTIONS_SCHEMA,
    )

    assert len(problems) == 2
    assert "questions[0].correct_key" in problems[0]
    assert "questions[1].difficulty" in problems[1]


# ---------------------------------------------------------------------------
# schemas: coercion
# ---------------------------------------------------------------------------


def test_coercion_trims_an_over_long_string_so_the_reply_survives() -> None:
    data = coerce_strings(item(concept="x" * 200), QUESTION_ITEM_SCHEMA)

    assert len(data["concept"]) == 160
    assert validate(data, QUESTION_ITEM_SCHEMA) == []


def test_coercion_reaches_into_nested_arrays_of_objects() -> None:
    data = coerce_strings(
        item(options=[{"key": "A", "text": "y" * 500}, {"key": "B", "text": "kisa"}]),
        QUESTION_ITEM_SCHEMA,
    )

    assert len(data["options"][0]["text"]) == 400
    assert data["options"][1]["text"] == "kisa"


def test_coercion_drops_items_beyond_the_array_maximum() -> None:
    many = {"questions": [item() for _ in range(70)]}

    data = coerce_strings(many, QUESTIONS_SCHEMA)

    assert len(data["questions"]) == 60
    assert validate(data, QUESTIONS_SCHEMA) == []


def test_coercion_leaves_values_it_has_no_rule_for_untouched() -> None:
    data = coerce_strings(item(difficulty=4, source_page=12), QUESTION_ITEM_SCHEMA)

    assert data["difficulty"] == 4 and data["source_page"] == 12


def test_coercion_never_invents_a_missing_required_field() -> None:
    data = item()
    del data["explanation"]

    assert "explanation" not in coerce_strings(data, QUESTION_ITEM_SCHEMA)


# ---------------------------------------------------------------------------
# model: JSON extraction
# ---------------------------------------------------------------------------


def test_plain_json_is_parsed() -> None:
    assert extract_json('{"a": 1}') == {"a": 1}


def test_a_fenced_json_block_is_unwrapped() -> None:
    assert extract_json('```json\n{"a": 1}\n```') == {"a": 1}


def test_an_unlabelled_fence_is_unwrapped_too() -> None:
    assert extract_json('```\n{"a": 1}\n```') == {"a": 1}


def test_json_buried_in_prose_is_recovered() -> None:
    reply = 'Elbette, iste sonuc:\n{"questions": [1]}\nUmarim yardimci olur.'

    assert extract_json(reply) == {"questions": [1]}


def test_a_bare_array_reply_is_recovered() -> None:
    assert extract_json("Iste liste: [1, 2, 3] hepsi bu.") == [1, 2, 3]


@pytest.mark.parametrize("reply", ["", "   ", None])
def test_an_empty_reply_is_refused_rather_than_read_as_an_empty_result(reply) -> None:
    with pytest.raises(ValueError, match="empty reply"):
        extract_json(reply)


def test_a_reply_with_no_json_at_all_is_refused() -> None:
    with pytest.raises(ValueError, match="no JSON object"):
        extract_json("Bu konuda yardimci olamam.")


def test_broken_json_inside_braces_raises_rather_than_returning_a_guess() -> None:
    with pytest.raises(json.JSONDecodeError):
        extract_json('{"a": 1,,}')


# ---------------------------------------------------------------------------
# model: structured calls
# ---------------------------------------------------------------------------


def test_a_client_without_a_gateway_reports_itself_unavailable() -> None:
    client = MedicalModelClient(None)

    assert client.available is False
    with pytest.raises(MedicalModelError, match="yapılandırılmamış"):
        asyncio.run(client.structured("t", "p", QUESTIONS_SCHEMA))


def test_a_valid_first_reply_is_returned_without_a_second_call() -> None:
    gateway = Gateway(json.dumps({"questions": [item()]}))
    client = MedicalModelClient(gateway)

    data = asyncio.run(client.structured("question_generation", "Uret", QUESTIONS_SCHEMA))

    assert len(gateway.calls) == 1
    assert data["questions"][0]["correct_key"] == "A"


def test_the_schema_is_sent_as_a_response_format_and_named_without_dots() -> None:
    gateway = Gateway(json.dumps({"questions": [item()]}))

    asyncio.run(MedicalModelClient(gateway).structured("medical.gen", "Uret", QUESTIONS_SCHEMA))

    response_format = gateway.calls[0]["kwargs"]["response_format"]
    assert response_format["json_schema"]["name"] == "medical_gen"
    assert response_format["json_schema"]["schema"] is QUESTIONS_SCHEMA


def test_a_pipeline_call_never_offers_the_model_the_assistant_tools() -> None:
    gateway = Gateway(json.dumps({"questions": [item()]}))

    asyncio.run(MedicalModelClient(gateway).structured("gen", "Uret", QUESTIONS_SCHEMA))

    metadata = gateway.calls[0]["metadata"]
    assert metadata["tool_schema_selection"] is False
    assert metadata["medical_pipeline"] == "gen" and metadata["structured_output"] is True


def test_images_are_forwarded_and_marked_as_a_vision_call() -> None:
    gateway = Gateway(json.dumps({"questions": [item()]}))
    images = [{"mime_type": "image/png", "data": "AAA"}]

    asyncio.run(MedicalModelClient(gateway).structured("visual", "Bak", QUESTIONS_SCHEMA, images=images))

    metadata = gateway.calls[0]["metadata"]
    assert metadata["vision"] is True and metadata["images"] == images


def test_a_configured_model_is_passed_through_and_an_empty_one_is_not() -> None:
    gateway = Gateway(json.dumps({"questions": [item()]}), json.dumps({"questions": [item()]}))

    asyncio.run(MedicalModelClient(gateway, model="gemini-x").structured("g", "p", QUESTIONS_SCHEMA))
    asyncio.run(MedicalModelClient(gateway, model="  ").structured("g", "p", QUESTIONS_SCHEMA))

    assert gateway.calls[0]["kwargs"]["model"] == "gemini-x"
    assert "model" not in gateway.calls[1]["kwargs"]


def test_an_unparsable_reply_is_repaired_on_a_second_call_that_quotes_the_problem() -> None:
    gateway = Gateway("Bunu yapamam.", json.dumps({"questions": [item()]}))

    data = asyncio.run(MedicalModelClient(gateway).structured("gen", "Uret", QUESTIONS_SCHEMA))

    assert len(gateway.calls) == 2
    assert "rejected" in gateway.calls[1]["prompt"]
    assert "not valid JSON" in gateway.calls[1]["prompt"]
    assert data["questions"][0]["stem"]


def test_a_schema_violation_is_repaired_with_the_violation_named() -> None:
    gateway = Gateway(
        json.dumps({"questions": [item(correct_key="Z")]}),
        json.dumps({"questions": [item()]}),
    )

    asyncio.run(MedicalModelClient(gateway).structured("gen", "Uret", QUESTIONS_SCHEMA))

    assert "correct_key" in gateway.calls[1]["prompt"]


def test_two_bad_replies_raise_instead_of_returning_the_malformed_data() -> None:
    bad = json.dumps({"questions": [item(correct_key="Z")]})
    gateway = Gateway(bad, bad)

    with pytest.raises(MedicalModelError) as caught:
        asyncio.run(MedicalModelClient(gateway).structured("gen", "Uret", QUESTIONS_SCHEMA))

    assert len(gateway.calls) == 2
    assert caught.value.problems and "correct_key" in caught.value.problems[0]
    assert "Z" in caught.value.raw


def test_a_single_attempt_client_does_not_retry() -> None:
    gateway = Gateway("not json")

    with pytest.raises(MedicalModelError):
        asyncio.run(MedicalModelClient(gateway).structured("gen", "p", QUESTIONS_SCHEMA, max_attempts=1))

    assert len(gateway.calls) == 1


def test_an_over_long_string_is_trimmed_before_validation_rather_than_rejected() -> None:
    gateway = Gateway(json.dumps({"questions": [item(concept="x" * 400)]}))

    data = asyncio.run(MedicalModelClient(gateway).structured("gen", "p", QUESTIONS_SCHEMA))

    assert len(data["questions"][0]["concept"]) == 160


def test_a_slow_provider_times_out_with_a_turkish_message() -> None:
    client = MedicalModelClient(Hanging(), timeout_seconds=0.05)

    with pytest.raises(MedicalModelError, match="zaman aşımına"):
        asyncio.run(client.structured("gen", "p", QUESTIONS_SCHEMA))


def test_a_transport_failure_is_wrapped_with_its_type_and_not_leaked_raw() -> None:
    with pytest.raises(MedicalModelError, match="ConnectionResetError"):
        asyncio.run(MedicalModelClient(Exploding()).structured("gen", "p", QUESTIONS_SCHEMA))


def test_a_free_text_call_sends_no_response_format_and_returns_the_reply() -> None:
    gateway = Gateway("Kisa bir aciklama.")

    text = asyncio.run(MedicalModelClient(gateway).text("explain", "Anlat"))

    assert text == "Kisa bir aciklama."
    assert "response_format" not in gateway.calls[0]["kwargs"]
    assert "structured_output" not in gateway.calls[0]["metadata"]


def test_a_reply_object_without_text_becomes_an_empty_string_not_an_exception() -> None:
    class Empty:
        async def generate(self, request, context, **kwargs):
            return object()

    assert asyncio.run(MedicalModelClient(Empty()).text("t", "p")) == ""


# ---------------------------------------------------------------------------
# search
# ---------------------------------------------------------------------------


def make_index(*documents: SearchDocument, terminology=None) -> SearchIndex:
    index = SearchIndex(terminology)
    index.build(documents)
    return index


def test_an_empty_index_returns_nothing_instead_of_raising() -> None:
    assert SearchIndex().search("humerus") == []


def test_a_query_of_only_stopwords_matches_nothing() -> None:
    index = make_index(SearchDocument("c1", "chunk", "Humerus bir uzun kemiktir."))

    assert index.search("ve ile bu") == []


def test_the_document_that_mentions_the_term_most_ranks_first() -> None:
    index = make_index(
        SearchDocument("c1", "chunk", "Scapula hakkinda kisa bir not."),
        SearchDocument("c2", "chunk", "Scapula scapula scapula uzerine ayrintili anlatim."),
    )

    hits = index.search("scapula")

    assert [hit.document.doc_id for hit in hits][0] == "c2"
    assert hits[0].matched == [stem("scapula")]


def test_a_heading_match_outranks_the_same_evidence_buried_in_body_text() -> None:
    body = "Bu bolumde eklem yuzeyleri ve baglar ele alinir."
    index = make_index(
        SearchDocument("titled", "chunk", body, title="Articulatio humeri"),
        SearchDocument("plain", "chunk", body + " Articulatio humeri burada gecer."),
    )

    hits = index.search("articulatio humeri")

    assert hits[0].document.doc_id == "titled"


def test_a_turkish_name_finds_the_latin_passage_through_the_terminology_index() -> None:
    index = make_index(
        SearchDocument("c1", "chunk", "Scapula, sirtin ust bolumunde yer alan yassi bir yapidir."),
        terminology=default_terminology(),
    )

    hits = index.search("kürek kemiği")

    assert [hit.document.doc_id for hit in hits] == ["c1"]


def test_a_synonym_match_scores_below_the_same_hit_found_directly() -> None:
    entry = SearchDocument("c1", "chunk", "Scapula, sirtin ust bolumunde yer alan yassi bir yapidir.")
    index = make_index(entry, terminology=default_terminology())

    direct = index.search("scapula")[0].score
    through_synonym = index.search("kürek kemiği")[0].score

    assert through_synonym == pytest.approx(direct * SYNONYM_WEIGHT, rel=0.01)


def test_search_can_be_limited_to_one_kind() -> None:
    index = make_index(
        SearchDocument("c1", "chunk", "Humerus govdesi."),
        SearchDocument("n1", "note", "Humerus notlarim."),
        SearchDocument("q1", "question", "Humerus hangi kemiktir?"),
    )

    assert [hit.document.doc_id for hit in index.search("humerus", kinds=("note",))] == ["n1"]


def test_search_can_be_confined_to_chosen_documents() -> None:
    index = make_index(
        SearchDocument("c1", "chunk", "Humerus govdesi.", document_id="d1"),
        SearchDocument("c2", "chunk", "Humerus basi.", document_id="d2"),
    )

    hits = index.search("humerus", document_ids=["d2"])

    assert [hit.document.doc_id for hit in hits] == ["c2"]


def test_a_page_range_excludes_pages_outside_it() -> None:
    index = make_index(
        SearchDocument("c1", "chunk", "Humerus govdesi.", document_id="d1", page_number=5),
        SearchDocument("c2", "chunk", "Humerus basi.", document_id="d1", page_number=40),
    )

    hits = index.search("humerus", page_from=1, page_to=10)

    assert [hit.document.doc_id for hit in hits] == ["c1"]


def test_a_subject_filter_keeps_documents_that_declare_no_subject() -> None:
    index = make_index(
        SearchDocument("c1", "chunk", "Hucre zari yapisi.", subject="biology"),
        SearchDocument("c2", "chunk", "Hucre zari gecirgenligi.", subject="biophysics"),
        SearchDocument("c3", "chunk", "Hucre zari notu.", subject=None),
    )

    found = {hit.document.doc_id for hit in index.search("hucre zari", subject="biology")}

    assert found == {"c1", "c3"}


def test_the_limit_caps_the_result_list() -> None:
    index = make_index(*[SearchDocument(f"c{i}", "chunk", "Humerus kemigi anlatimi.") for i in range(8)])

    assert len(index.search("humerus", limit=3)) == 3


def test_ties_break_on_document_id_so_the_order_is_reproducible() -> None:
    index = make_index(*[SearchDocument(f"c{i}", "chunk", "Humerus kemigi.") for i in "badc"])

    assert [hit.document.doc_id for hit in index.search("humerus")] == ["ca", "cb", "cc", "cd"]


def test_rebuilding_the_index_forgets_the_previous_material() -> None:
    index = make_index(SearchDocument("old", "chunk", "Eski metin humerus."))
    index.build([SearchDocument("new", "chunk", "Yeni metin humerus.")])

    assert len(index) == 1
    assert [hit.document.doc_id for hit in index.search("humerus")] == ["new"]


# ---------------------------------------------------------------------------
# retrieval
# ---------------------------------------------------------------------------


def seeded_store(*chunks: DocumentChunk, doc: StudyDocument | None = None) -> MedicalStore:
    store = MedicalStore()
    study = doc or document()
    store.save_document(study)
    store.replace_chunks(study.document_id, chunks)
    return store


def test_every_reference_points_at_a_chunk_that_exists_in_the_store() -> None:
    store = seeded_store(
        chunk("c1", 12, "Humerus, kolun uzun kemigidir ve distalde radius ile eklemlesir."),
    )
    retriever = Retriever(store)

    blocks = retriever.retrieve("humerus radius")

    assert len(blocks) == 1
    reference = blocks[0].reference
    assert reference.page_number == 12 and reference.chunk_id == "c1"
    assert store.get_chunk(reference.chunk_id) is not None
    assert reference.title == "Kemik Sistemi"


def test_a_page_yields_one_block_even_when_several_of_its_chunks_match() -> None:
    store = seeded_store(
        chunk("c1", 7, "Humerus govdesi hakkinda ayrintili anlatim.", index=0),
        chunk("c2", 7, "Humerus distal ucu hakkinda ayrintili anlatim.", index=1),
        chunk("c3", 9, "Humerus proksimal ucu hakkinda anlatim.", index=0),
    )

    blocks = Retriever(store).retrieve("humerus")

    assert sorted(block.reference.page_number for block in blocks) == [7, 9]


def test_a_matched_text_chunk_carries_its_neighbours_for_context() -> None:
    store = seeded_store(
        chunk("c0", 4, "ONCEKI PARAGRAF.", index=0),
        chunk("c1", 4, "Sulcus intertubercularis burada tarif edilir.", index=1),
        chunk("c2", 4, "SONRAKI PARAGRAF.", index=2),
    )

    text = Retriever(store).retrieve("sulcus intertubercularis")[0].text

    assert "ONCEKI PARAGRAF." in text and "SONRAKI PARAGRAF." in text


def test_a_visual_chunk_is_returned_alone_without_borrowing_neighbouring_text() -> None:
    store = seeded_store(
        chunk("c0", 4, "Sayfa metni.", index=0),
        chunk("c1", 4, "Sekil: scapula arka yuzu, spina scapulae isaretli.", index=1, kind="visual"),
    )

    blocks = Retriever(store).retrieve("spina scapulae")

    assert blocks[0].kind == "visual"
    assert "Sayfa metni." not in blocks[0].text


def test_retrieval_can_be_confined_to_a_document_and_page_range() -> None:
    other = document("d2", "Baska Kitap")
    store = seeded_store(chunk("c1", 5, "Humerus anlatimi burada."))
    store.save_document(other)
    store.replace_chunks("d2", [chunk("c2", 5, "Humerus anlatimi baska kitapta.", document_id="d2")])
    retriever = Retriever(store)

    inside = retriever.retrieve("humerus", RetrievalScope(document_ids=["d2"]))
    outside = retriever.retrieve("humerus", RetrievalScope(document_ids=["d1"], page_from=90, page_to=99))

    assert [block.reference.document_id for block in inside] == ["d2"]
    assert outside == []


def test_a_long_chunk_is_cut_to_the_block_limit() -> None:
    store = seeded_store(chunk("c1", 3, "Humerus " + "x" * 4000))

    assert len(Retriever(store).retrieve("humerus")[0].text) <= MAX_BLOCK_CHARS


def test_page_evidence_returns_the_range_in_page_order() -> None:
    store = seeded_store(
        chunk("c2", 11, "Ikinci sayfa metni.", index=0),
        chunk("c1", 10, "Birinci sayfa metni.", index=0),
        chunk("c3", 30, "Aralik disinda kalan metin.", index=0),
    )

    blocks = Retriever(store).page_evidence(document(), 10, 11)

    assert [block.reference.page_number for block in blocks] == [10, 11]


def test_a_page_that_overruns_the_budget_still_yields_one_marked_block() -> None:
    store = seeded_store(chunk("c1", 10, "y" * 5000))

    blocks = Retriever(store).page_evidence(document(), 10, 10, max_chars=300)

    assert len(blocks) == 1
    assert blocks[0].text.endswith(TRUNCATION_MARK)
    assert len(blocks[0].text) <= 300


def test_the_budget_stops_the_range_once_something_has_been_collected() -> None:
    store = seeded_store(
        chunk("c1", 10, "a" * 400, index=0),
        chunk("c2", 11, "b" * 400, index=0),
        chunk("c3", 12, "c" * 400, index=0),
    )

    blocks = Retriever(store).page_evidence(document(), 10, 12, max_chars=500)

    assert [block.reference.page_number for block in blocks] == [10]


def test_a_truncated_block_says_so_rather_than_reading_as_the_whole_page() -> None:
    store = seeded_store(chunk("c1", 10, "z" * (MAX_BLOCK_CHARS + 500)))

    text = Retriever(store).page_evidence(document(), 10, 10)[0].text

    assert text.endswith(TRUNCATION_MARK) and len(text) <= MAX_BLOCK_CHARS


def test_the_quote_keeps_the_start_of_the_original_chunk_not_the_trimmed_text() -> None:
    store = seeded_store(chunk("c1", 10, "Collum chirurgicum humeri kirilgan bolgedir. " + "z" * 4000))

    reference = Retriever(store).page_evidence(document(), 10, 10)[0].reference

    assert reference.quote.startswith("Collum chirurgicum")
    assert len(reference.quote) <= 170


def test_formatted_evidence_numbers_the_sources_and_names_page_and_kind() -> None:
    store = seeded_store(
        chunk("c1", 12, "Humerus govdesi.", index=0),
        chunk("c2", 13, "Sekil aciklamasi.", index=0, kind="visual"),
    )
    retriever = Retriever(store)

    text = retriever.format_evidence(retriever.page_evidence(document(), 12, 13))

    assert "[Kaynak 1] Kemik Sistemi, s. 12 (ders notu)" in text
    assert "[Kaynak 2] Kemik Sistemi, s. 13 (şekil açıklaması)" in text


def test_formatted_evidence_of_nothing_is_empty_rather_than_a_bare_header() -> None:
    assert Retriever(MedicalStore()).format_evidence([]) == ""


def test_reference_payloads_are_numbered_from_one_and_keep_the_chunk_id() -> None:
    store = seeded_store(chunk("c1", 12, "Humerus govdesi."))
    retriever = Retriever(store)

    payload = retriever.references(retriever.page_evidence(document(), 12, 12))

    assert payload[0]["index"] == 1
    assert payload[0]["chunk_id"] == "c1" and payload[0]["page_number"] == 12
    assert payload[0]["title"] == "Kemik Sistemi"


def build_counter(monkeypatch) -> list[int]:
    """Count rebuilds of the search index.

    ``build`` fills the same SearchIndex instance in place, so the identity of
    ``retriever.index`` is the same object whether or not a rebuild happened;
    only a counter can tell a warm cache from a cold one.
    """
    calls = [0]
    original = SearchIndex.build

    def counted(self, documents) -> None:
        calls[0] += 1
        original(self, documents)

    monkeypatch.setattr(SearchIndex, "build", counted)
    return calls


def test_the_index_is_rebuilt_only_after_the_store_changes(monkeypatch) -> None:
    store = seeded_store(chunk("c1", 3, "Humerus anlatimi."))
    retriever = Retriever(store)
    builds = build_counter(monkeypatch)
    retriever.refresh()
    assert builds[0] == 1

    retriever.refresh()
    assert builds[0] == 1

    store.replace_chunks("d1", [chunk("c1", 3, "Humerus anlatimi."), chunk("c2", 4, "Scapula anlatimi.")])
    retriever.refresh()

    assert builds[0] == 2 and len(retriever.index) == 2


def test_a_study_session_write_leaves_the_index_warm(monkeypatch) -> None:
    """The tutor saves the study session on every medical turn. While that
    counted as an index change, the whole library was re-read and re-tokenised
    before every single question the student asked."""
    from app.medical.models import StudyNote, StudySession

    store = seeded_store(chunk("c1", 3, "Humerus govdesinin anatomisi."))
    retriever = Retriever(store)
    retriever.refresh()
    builds = build_counter(monkeypatch)

    for _ in range(3):
        store.save_session(StudySession(subject="anatomy"))
        assert [block.reference.chunk_id for block in retriever.retrieve("humerus")] == ["c1"]
    assert builds[0] == 0  # three turns, not one rebuild

    # Content the index is built from still reaches it on the very next query.
    store.save_note(StudyNote(note_id="n1", title="Humerus", content="Humerus ozet notum.", subject="anatomy"))

    assert [hit.document.doc_id for hit in retriever.search("ozet")] == ["n1"]
    assert builds[0] == 1


def test_a_deleted_document_disappears_from_retrieval_after_a_refresh() -> None:
    store = seeded_store(chunk("c1", 3, "Humerus anlatimi."))
    retriever = Retriever(store)
    assert retriever.retrieve("humerus")

    store.delete_document("d1")

    assert retriever.retrieve("humerus") == []


def test_notes_and_questions_are_searchable_but_are_not_evidence_blocks() -> None:
    from app.medical.models import Question, QuestionOption, StudyNote

    store = seeded_store(chunk("c1", 3, "Humerus ders notu metni."))
    store.save_note(StudyNote(note_id="n1", title="Humerus", content="Humerus ozet notum.", subject="anatomy"))
    store.save_question(
        Question(
            question_id="q1",
            subject="anatomy",
            stem="Humerus hangi kemiktir?",
            options=[QuestionOption("A", "Uzun"), QuestionOption("B", "Kisa")],
            correct_key="A",
        )
    )
    retriever = Retriever(store)

    kinds = {hit.document.kind for hit in retriever.search("humerus")}
    evidence = retriever.retrieve("humerus")

    assert kinds == {"chunk", "note", "question"}
    assert [block.reference.chunk_id for block in evidence] == ["c1"]


def test_an_unknown_document_id_is_echoed_rather_than_titled_with_a_guess() -> None:
    assert Retriever(MedicalStore()).title_of("d-missing") == "d-missing"
