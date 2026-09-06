"""Medical Academy documents: ingestion, page-anchored evidence, lexical search."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.medical.documents import DocumentError, DocumentPipeline, human_size, page_headings, split_text_pages
from app.medical.models import DocumentStatus, StudyNote
from app.medical.retrieval import TRUNCATION_MARK, RetrievalScope, Retriever
from app.medical.search import SearchDocument, SearchIndex
from app.medical.store import MedicalStore
from app.medical.terminology import TerminologyIndex

SCAPULA_PAGE = (
    "SCAPULA\nScapula omuz kusagindaki yassi ucgen kemiktir. Glenoid kavite humerus basi ile eklem yapar.\n"
    "Akromion klavikula ile eklemlesir ve omuz catisini olusturur."
)
HUMERUS_PAGE = (
    "HUMERUS\nHumerus kolun tek uzun kemigidir. Caput humeri glenoid kaviteye oturur.\n"
    "Nervus radialis sulcus nervi radialis icinde seyreder."
)
MITOCHONDRIA_PAGE = "MITOKONDRI\nMitokondri hucrenin enerji santralidir. ATP uretimi burada gerceklesir."


def make_pdf(pages: list[tuple[str, bool]]) -> bytes:
    """A minimal but valid PDF; each page is ``(one line of text, draws a figure)``."""
    count = len(pages)
    font, figure = 3 + 2 * count, 4 + 2 * count
    kids = " ".join(f"{3 + 2 * position} 0 R" for position in range(count))
    objects: dict[int, bytes] = {
        1: b"<< /Type /Catalog /Pages 2 0 R >>",
        2: f"<< /Type /Pages /Kids [{kids}] /Count {count} >>".encode(),
        font: b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        figure: b"<< /Type /XObject /Subtype /Image /Width 2 /Height 2 /ColorSpace /DeviceRGB"
        b" /BitsPerComponent 8 /Length 12 >>\nstream\n" + bytes(range(12)) + b"\nendstream",
    }
    for position, (text, draws_a_figure) in enumerate(pages):
        page_id = 3 + 2 * position
        figures = f" /XObject << /Im1 {figure} 0 R >>" if draws_a_figure else ""
        objects[page_id] = (
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 220 220] /Contents {page_id + 1} 0 R"
            f" /Resources << /Font << /F1 {font} 0 R >>{figures} >> >>"
        ).encode()
        stream = f"BT /F1 11 Tf 12 200 Td ({text}) Tj ET\n"
        stream += "q 200 0 0 140 10 20 cm /Im1 Do Q\n" if draws_a_figure else ""
        body = stream.encode("latin-1")
        objects[page_id + 1] = b"<< /Length %d >>\nstream\n%s\nendstream" % (len(body), body)
    out, offsets = bytearray(b"%PDF-1.4\n"), {}
    for number in sorted(objects):
        offsets[number] = len(out)
        out += b"%d 0 obj\n%s\nendobj\n" % (number, objects[number])
    start, size = len(out), max(objects) + 1
    out += b"xref\n0 %d\n0000000000 65535 f \n" % size
    out += b"".join(b"%010d 00000 n \n" % offsets[number] for number in range(1, size))
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (size, start)
    return bytes(out)


def ready_document(pipeline: DocumentPipeline, text: str, **kwargs):
    """Import plain text and process it, asserting it reached READY."""
    document, _created = pipeline.import_text(text, **kwargs)
    processed = pipeline.process(document.document_id)
    assert processed.status == DocumentStatus.READY
    return processed


# ---------------------------------------------------------------------------
# import
# ---------------------------------------------------------------------------


def test_import_file_dedupes_by_content_and_refuses_bad_input(tmp_path) -> None:
    pipeline = DocumentPipeline(MedicalStore(), directory=tmp_path / "academy")
    source = tmp_path / "anatomi.txt"
    source.write_text(SCAPULA_PAGE, encoding="utf-8")

    document, created = pipeline.import_file(source, subject="anatomy", tags=["anatomi", "   ", "kemik"])
    assert created is True
    assert document.kind == "text" and document.title == "anatomi" and document.file_name == "anatomi.txt"
    assert document.tags == ["anatomi", "kemik"] and document.subject == "anatomy"
    assert document.status == DocumentStatus.PENDING and document.status_detail == "Bekliyor"
    stored = tmp_path / "academy" / "documents"
    assert [item.name for item in stored.iterdir()] == [f"{document.document_id}.txt"]

    # The same bytes under another name and title are the same document.
    twin = tmp_path / "kopya.txt"
    twin.write_bytes(source.read_bytes())
    again, created_again = pipeline.import_file(twin, title="Baska bir ad")
    assert created_again is False
    assert again.document_id == document.document_id and again.title == "anatomi"
    assert len(list(stored.iterdir())) == 1

    unsupported = tmp_path / "sunum.docx"
    unsupported.write_text("x", encoding="utf-8")
    with pytest.raises(DocumentError, match="Desteklenen türler"):
        pipeline.import_file(unsupported)
    with pytest.raises(DocumentError, match="Dosya bulunamadı"):
        pipeline.import_file(tmp_path / "olmayan.pdf")
    oversized = tmp_path / "buyuk.txt"
    oversized.write_bytes(b"x" * 4096)
    with pytest.raises(DocumentError, match="çok büyük"):
        DocumentPipeline(MedicalStore(), max_bytes=1024).import_file(oversized)


def test_re_import_repairs_a_document_whose_stored_copy_vanished(tmp_path) -> None:
    store = MedicalStore()
    pipeline = DocumentPipeline(store, directory=tmp_path / "academy")
    source = tmp_path / "ders.txt"
    source.write_text(f"{SCAPULA_PAGE}\f{HUMERUS_PAGE}", encoding="utf-8")
    document, _ = pipeline.import_file(source, subject="anatomy")
    assert pipeline.process(document.document_id).status == DocumentStatus.READY
    stored = tmp_path / "academy" / "documents"
    # An antivirus quarantine, a cleanup tool, a state folder restored without
    # its documents: the copy is gone and the document fails.
    (stored / f"{document.document_id}.txt").unlink()
    failed = pipeline.process(document.document_id)
    assert failed.error == "Belgenin kopyası bulunamadı; yeniden içe aktar."

    again, needs_processing = pipeline.import_file(source, subject="anatomy")

    # Doing exactly what the failure told the student to do has to work: the
    # digest still matches, but returning the record untouched made re-import a
    # silent no-op and left the document failed forever.
    assert needs_processing is True and again.document_id == document.document_id
    assert again.status == DocumentStatus.PENDING and again.status_detail == "Bekliyor"
    assert again.error is None
    assert [item.name for item in stored.iterdir()] == [f"{document.document_id}.txt"]
    assert Path(again.stored_path).read_bytes() == source.read_bytes()
    assert pipeline.process(again.document_id).status == DocumentStatus.READY
    # A healthy duplicate is still a duplicate: nothing to repair, nothing to do.
    assert pipeline.import_file(source)[1] is False


def test_re_import_restores_a_ready_document_without_re_extracting_it(tmp_path) -> None:
    store = MedicalStore()
    pipeline = DocumentPipeline(store, directory=tmp_path / "academy")
    source = tmp_path / "ders.txt"
    source.write_text(f"{SCAPULA_PAGE}\f{HUMERUS_PAGE}", encoding="utf-8")
    document, _ = pipeline.import_file(source)
    pipeline.process(document.document_id)
    pipeline.attach_visual_summary(document.document_id, 2, summary="Şekilde caput humeri gösterilmiş.", labels=["caput humeri"])
    copy = tmp_path / "academy" / "documents" / f"{document.document_id}.txt"
    copy.unlink()

    again, needs_processing = pipeline.import_file(source)

    # Pages, chunks and the vision pass outlived the lost copy — only page
    # rendering was broken — so the copy comes back but nothing is re-extracted
    # over the study material.
    assert needs_processing is False and again.status == DocumentStatus.READY
    assert copy.read_bytes() == source.read_bytes()
    assert store.get_page(document.document_id, 2).visual_summary.startswith("Şekilde")
    assert store.get_chunk(f"{document.document_id}:2:visual") is not None


def test_re_import_writes_the_repaired_copy_under_the_current_directory(tmp_path) -> None:
    store = MedicalStore()
    first = DocumentPipeline(store, directory=tmp_path / "eski")
    document, _ = first.import_text(SCAPULA_PAGE, title="Anatomi Ders 1")
    assert first.process(document.document_id).status == DocumentStatus.READY
    # The store was restored beside a new academy directory; stored_path is an
    # absolute string still pointing into the old one, which is gone.
    (tmp_path / "eski" / "documents" / f"{document.document_id}.txt").unlink()
    moved = DocumentPipeline(store, directory=tmp_path / "yeni")
    assert moved.process(document.document_id).status == DocumentStatus.FAILED

    again, needs_processing = moved.import_text(SCAPULA_PAGE, title="Anatomi Ders 1")

    assert needs_processing is True
    assert again.stored_path == str(tmp_path / "yeni" / "documents" / f"{document.document_id}.txt")
    assert Path(again.stored_path).is_file()
    # The copy is never written back into the directory the record points at.
    assert list((tmp_path / "eski" / "documents").iterdir()) == []
    assert moved.process(again.document_id).status == DocumentStatus.READY


def test_oversized_file_reports_a_size_the_reader_can_act_on(tmp_path) -> None:
    assert human_size(900) == "900 bayt"
    assert human_size(4096) == "4 KB"
    assert human_size(3 * 1024 * 1024 + 512 * 1024) == "3,5 MB"
    assert human_size(60 * 1024 * 1024) == "60 MB"
    oversized = tmp_path / "buyuk.txt"
    oversized.write_bytes(b"x" * 4096)

    with pytest.raises(DocumentError) as failure:
        DocumentPipeline(MedicalStore(), max_bytes=1024).import_file(oversized)

    # A sub-megabyte limit used to render as "(0 MB); sınır 0 MB".
    assert str(failure.value) == "Dosya çok büyük (4 KB); sınır 1 KB."


def test_import_text_splits_pages_on_form_feeds_and_refuses_empty_text() -> None:
    store = MedicalStore()
    pipeline = DocumentPipeline(store)
    with pytest.raises(DocumentError, match="Metin boş"):
        pipeline.import_text("   \n  ", title="Boş")
    with pytest.raises(DocumentError, match="Metin çok uzun"):
        DocumentPipeline(MedicalStore(), max_bytes=1024).import_text("y" * 2048, title="Uzun")

    body = f"{SCAPULA_PAGE}\f   \f{HUMERUS_PAGE}"
    document, created = pipeline.import_text(body, title="Anatomi Ders 1", subject="anatomy")
    assert created is True and document.kind == "text"
    assert pipeline.import_text(body, title="Aynı metin")[1] is False

    pages = store.get_pages(pipeline.process(document.document_id).document_id)
    # The whitespace-only page between the two form feeds is dropped.
    assert [page.page_number for page in pages] == [1, 2]
    assert pages[0].headings == ["SCAPULA"] and pages[1].headings == ["HUMERUS"]
    assert "Akromion" in pages[0].text and "Nervus radialis" in pages[1].text


def test_split_text_pages_and_page_headings() -> None:
    assert split_text_pages("bir\niki\f\n \n\fuc") == ["bir\niki", "uc"]
    assert split_text_pages("") == [""] and split_text_pages("   ") == [""]
    sized = split_text_pages("\n".join(f"satir {index}" for index in range(200)), page_chars=60)
    assert len(sized) > 1 and all(len(page) <= 80 for page in sized)
    assert "".join(sized).replace("\n", "") == "".join(f"satir {index}" for index in range(200))
    assert page_headings(SCAPULA_PAGE) == ["SCAPULA"]
    assert page_headings("Bu bir cumledir ve baslik degildir.") == []
    assert len(page_headings("\n".join(f"BOLUM {index}" for index in range(20)))) == 6


# ---------------------------------------------------------------------------
# processing
# ---------------------------------------------------------------------------


def test_process_walks_the_real_status_sequence_and_ends_ready() -> None:
    store = MedicalStore()
    pipeline = DocumentPipeline(store)
    document, _ = pipeline.import_text(f"{SCAPULA_PAGE}\f{HUMERUS_PAGE}", title="Anatomi Ders 1")
    events: list[tuple[str, str]] = []

    processed = pipeline.process(document.document_id, progress=lambda status, detail: events.append((status, detail)))

    assert [status for status, _ in events] == [DocumentStatus.READING, DocumentStatus.INDEXING, DocumentStatus.READY]
    assert events[0][1] == "Belge okunuyor" and events[1][1] == "Kavramlar dizinleniyor"
    assert processed.status == DocumentStatus.READY
    assert processed.status_detail == events[-1][1] == "Hazır · 2 sayfa · 2 parça"
    assert processed.page_count == len(store.get_pages(document.document_id)) == 2
    assert processed.chunk_count == len(store.chunks()) == 2
    assert processed.error is None and processed.indexed_at is not None
    assert pipeline.payload(processed)["ready"] is True
    # Chunks stay anchored to the page they came from.
    for chunk in store.chunks():
        page = store.get_page(document.document_id, chunk.page_number)
        assert page is not None and chunk.text in " ".join(page.text.split())


def test_process_records_failure_honestly_and_never_raises(tmp_path) -> None:
    store = MedicalStore()
    pipeline = DocumentPipeline(store, directory=tmp_path / "academy")
    source = tmp_path / "ders.txt"
    source.write_text(SCAPULA_PAGE, encoding="utf-8")
    document, _ = pipeline.import_file(source)
    (tmp_path / "academy" / "documents" / f"{document.document_id}.txt").unlink()
    events: list[tuple[str, str]] = []

    failed = pipeline.process(document.document_id, progress=lambda status, detail: events.append((status, detail)))

    assert failed.status == DocumentStatus.FAILED
    assert failed.status_detail == failed.error == "Belgenin kopyası bulunamadı; yeniden içe aktar."
    assert events[-1] == (DocumentStatus.FAILED, failed.status_detail)
    # The document survives the failure so the student can re-import it.
    assert store.get_document(document.document_id).status == DocumentStatus.FAILED
    assert pipeline.payload(failed)["ready"] is False
    with pytest.raises(DocumentError, match="Belge bulunamadı"):
        pipeline.process("doc-yok")


def test_pdf_pages_are_extracted_and_the_vision_budget_is_honoured(tmp_path) -> None:
    # Without the native wheel the pipeline reports a DocumentError, which would
    # surface here as a confusing "FAILED == READY" instead of a skip.
    pytest.importorskip("pypdfium2")
    store = MedicalStore()
    pipeline = DocumentPipeline(store, directory=tmp_path / "academy", vision_pages_per_document=2)
    source = tmp_path / "atlas.pdf"
    text_pages = [("SCAPULA", False), ("Scapula is a flat triangular bone of the shoulder girdle.", False)]
    source.write_bytes(make_pdf(text_pages + [(f"Fig {number}", True) for number in (3, 4, 5)]))
    document, created = pipeline.import_file(source, subject="anatomy")
    assert created is True and document.kind == "pdf"
    events: list[tuple[str, str]] = []

    processed = pipeline.process(document.document_id, progress=lambda status, detail: events.append((status, detail)))

    assert processed.status == DocumentStatus.READY and processed.page_count == 5
    assert [detail for status, detail in events if status == DocumentStatus.EXTRACTING] == [
        "Sayfalar çıkarılıyor · 0 / 5",
        "Sayfalar çıkarılıyor · 5 / 5",
    ]
    assert processed.status_detail == "Hazır · 5 sayfa · 1 parça · 2 sayfa görsel inceleme bekliyor"
    pages = store.get_pages(document.document_id)
    assert "shoulder girdle" in pages[1].text
    # Three pages carry a figure but the budget only covers the first two.
    assert [page.visual_status for page in pages] == ["not_needed", "not_needed", "pending", "pending", "skipped"]
    assert [page.page_number for page in pipeline.pages_needing_vision(document.document_id)] == [3, 4]
    assert processed.visual_pages_pending == 2 and processed.visual_pages_analyzed == 0


def test_delete_removes_the_document_its_pages_chunks_and_stored_copy(tmp_path) -> None:
    store = MedicalStore()
    pipeline = DocumentPipeline(store, directory=tmp_path / "academy")
    source = tmp_path / "ders.txt"
    source.write_text(f"{SCAPULA_PAGE}\f{HUMERUS_PAGE}", encoding="utf-8")
    document, _ = pipeline.import_file(source)
    pipeline.process(document.document_id)
    copy = tmp_path / "academy" / "documents" / f"{document.document_id}.txt"
    assert copy.is_file() and store.chunks()

    assert pipeline.delete(document.document_id) is True

    assert store.get_document(document.document_id) is None and store.chunks() == []
    assert store.get_pages(document.document_id) == []
    assert not copy.exists() and source.is_file()
    assert pipeline.delete(document.document_id) is False


def test_attach_visual_summary_makes_a_figure_searchable() -> None:
    store = MedicalStore()
    pipeline = DocumentPipeline(store)
    document = ready_document(pipeline, f"{SCAPULA_PAGE}\f{HUMERUS_PAGE}", title="Anatomi Ders 1")
    assert pipeline.attach_visual_summary(document.document_id, 99, summary="yok", labels=[]) is None

    page = pipeline.attach_visual_summary(
        document.document_id,
        2,
        summary="  Şekilde tuberculum majus ve caput humeri gösterilmiş.  ",
        labels=["tuberculum majus", "   ", "caput humeri"],
    )

    assert page.visual_status == "done"
    assert page.visual_summary == "Şekilde tuberculum majus ve caput humeri gösterilmiş."
    assert page.visual_labels == ["tuberculum majus", "caput humeri"]
    visual = store.get_chunk(f"{document.document_id}:2:visual")
    assert visual is not None and visual.kind == "visual" and visual.page_number == 2
    assert "Etiketler: tuberculum majus, caput humeri" in visual.text
    updated = store.get_document(document.document_id)
    assert updated.chunk_count == len(store.chunks()) == 3
    assert updated.visual_pages_analyzed == 1 and updated.visual_pages_pending == 0
    assert updated.status_detail == "Hazır · 2 sayfa · 3 parça"

    blocks = Retriever(store).retrieve("tuberculum majus")
    assert [block.reference.chunk_id for block in blocks] == [visual.chunk_id] and blocks[0].kind == "visual"
    assert "(şekil açıklaması)" in Retriever.format_evidence(blocks)


# ---------------------------------------------------------------------------
# retrieval
# ---------------------------------------------------------------------------


def test_retrieval_is_scoped_to_documents_and_page_ranges() -> None:
    store = MedicalStore()
    pipeline = DocumentPipeline(store)
    anatomy = ready_document(pipeline, f"{SCAPULA_PAGE}\f{HUMERUS_PAGE}", title="Anatomi Ders 1", subject="anatomy")
    biology = ready_document(pipeline, MITOCHONDRIA_PAGE, title="Biyoloji Ders 1", subject="biology")
    retriever = Retriever(store)
    assert RetrievalScope().restricted is False
    assert RetrievalScope(document_ids=[anatomy.document_id]).restricted is True
    assert retriever.retrieve("mitokondri", RetrievalScope(document_ids=[anatomy.document_id])) == []
    assert [block.reference.document_id for block in retriever.retrieve("mitokondri")] == [biology.document_id]

    both = retriever.retrieve("glenoid", RetrievalScope(document_ids=[anatomy.document_id]))
    assert sorted(block.reference.page_number for block in both) == [1, 2]
    scoped = RetrievalScope(document_ids=[anatomy.document_id], page_from=2, page_to=2)
    assert [block.reference.page_number for block in retriever.retrieve("glenoid", scoped)] == [2]
    assert retriever.retrieve("glenoid", RetrievalScope(page_from=9, page_to=9)) == []
    assert retriever.retrieve("porfirinojen") == []


def test_evidence_never_invents_a_page_and_labels_its_sources() -> None:
    store = MedicalStore()
    pipeline = DocumentPipeline(store)
    long_page = " ".join(f"Cumle {n} scapula ile ilgili aciklama icerir ve yeterince uzundur." for n in range(30))
    document = ready_document(pipeline, f"{long_page}\f{HUMERUS_PAGE}", title="Uzun Ders")
    assert len([chunk for chunk in store.chunks() if chunk.page_number == 1]) > 1
    retriever = Retriever(store)

    blocks = retriever.retrieve("scapula", limit=5)

    # One block per page, and every citation resolves to a stored chunk.
    assert len(blocks) == 1
    for block in blocks:
        chunk = store.get_chunk(block.reference.chunk_id)
        assert chunk is not None and block.reference.page_number == chunk.page_number
        assert block.reference.document_id == chunk.document_id == document.document_id
        assert block.reference.title == retriever.title_of(document.document_id) == "Uzun Ders"
        # The quote is a bounded excerpt of that chunk, never free-hand text.
        assert 0 < len(block.reference.quote) <= 160
        assert block.reference.quote.rstrip("…") in " ".join(chunk.text.split())
    reference = retriever.references(blocks)[0]
    assert reference["index"] == 1 and reference["kind"] == "text" and reference["score"] == blocks[0].score

    # Neighbouring context widens the passage but never moves the citation.
    matched = store.get_chunk(blocks[0].reference.chunk_id)
    assert blocks[0].text.startswith(matched.text[:80]) and len(blocks[0].text) > len(matched.text)
    assert retriever.retrieve("scapula", limit=5, neighbours=False)[0].text == matched.text
    formatted = retriever.format_evidence(blocks)
    assert formatted.startswith(f"[Kaynak 1] Uzun Ders, s. {matched.page_number} (ders notu)")
    assert retriever.format_evidence([]) == "" and retriever.title_of("doc-yok") == "doc-yok"


def test_page_evidence_returns_a_page_in_order_within_its_character_budget() -> None:
    store = MedicalStore()
    pipeline = DocumentPipeline(store)
    document = ready_document(pipeline, f"{SCAPULA_PAGE}\f{HUMERUS_PAGE}", title="Anatomi Ders 1")
    retriever = Retriever(store)

    blocks = retriever.page_evidence(document, 1, 2)
    assert [block.reference.page_number for block in blocks] == [1, 2]
    assert all(block.score == 1.0 and block.reference.title == "Anatomi Ders 1" for block in blocks)
    assert all(store.get_chunk(block.reference.chunk_id) is not None for block in blocks)

    budget = len(blocks[0].text) + 10
    bounded = retriever.page_evidence(document, 1, 2, max_chars=budget)
    assert [block.reference.page_number for block in bounded] == [1]
    assert sum(len(block.text) for block in bounded) <= budget
    assert retriever.page_evidence(document, 9, 9) == []


def test_page_evidence_marks_the_cut_instead_of_dropping_an_oversized_first_chunk() -> None:
    store = MedicalStore()
    pipeline = DocumentPipeline(store)
    document = ready_document(pipeline, f"{SCAPULA_PAGE}\f{HUMERUS_PAGE}", title="Anatomi Ders 1")
    retriever = Retriever(store)
    chunk = store.chunks(document_ids=[document.document_id], page_from=1, page_to=1)[0]
    assert len(chunk.text) > 120
    assert retriever.page_evidence(document, 1, 1)[0].text == chunk.text

    blocks = retriever.page_evidence(document, 1, 2, max_chars=120)

    # The page is still quoted instead of silently contributing nothing,
    # and the passage admits it was cut rather than reading as the whole page.
    assert [block.reference.page_number for block in blocks] == [1]
    assert blocks[0].reference.chunk_id == chunk.chunk_id
    assert blocks[0].text.startswith(chunk.text[:60]) and blocks[0].text.endswith(TRUNCATION_MARK)
    assert len(blocks[0].text) <= 120
    assert blocks[0].text in retriever.format_evidence(blocks)
    # Even a budget smaller than the mark itself grounds the answer somewhere.
    starved = retriever.page_evidence(document, 1, 2, max_chars=1)
    assert [block.reference.page_number for block in starved] == [1]
    assert starved[0].text.endswith(TRUNCATION_MARK)


def test_the_search_index_rebuilds_when_the_store_changes() -> None:
    store = MedicalStore()
    pipeline = DocumentPipeline(store)
    ready_document(pipeline, SCAPULA_PAGE, title="Anatomi Ders 1")
    retriever = Retriever(store)
    assert retriever.search("mitokondri") == []
    size_before, revision_before = len(retriever.index), store.revision

    store.save_note(StudyNote(note_id="n1", title="Tekrar notu", content=MITOCHONDRIA_PAGE, subject="biology"))

    assert store.revision > revision_before
    assert [(hit.document.doc_id, hit.document.kind) for hit in retriever.search("mitokondri")] == [("n1", "note")]
    assert len(retriever.index) == size_before + 1
    # A note is not lecture material, so evidence retrieval still ignores it.
    assert retriever.retrieve("mitokondri") == []


# ---------------------------------------------------------------------------
# lexical search
# ---------------------------------------------------------------------------


def corpus() -> list[SearchDocument]:
    scapula = "The scapula is a flat triangular bone. The scapula articulates with the humerus."
    femur = (
        "The femur is the longest bone of the lower limb and carries the body weight through"
        " the hip joint; the scapula is mentioned here only in passing."
    )
    return [
        SearchDocument("c1", "chunk", scapula, title="Scapula", document_id="d1", page_number=3),
        SearchDocument("c2", "chunk", femur, document_id="d2", page_number=9),
        SearchDocument("n1", "note", "Bone revision note.", title="Not"),
    ]


def test_bm25_ranks_the_better_match_first_and_filters_apply() -> None:
    index = SearchIndex()
    assert index.search("scapula") == []
    index.build(corpus())
    assert len(index) == 3
    ranked = index.search("scapula")
    assert [hit.document.doc_id for hit in ranked] == ["c1", "c2"]
    assert ranked[0].score > ranked[1].score and ranked[0].matched == ["scapu"]
    assert [hit.document.doc_id for hit in index.search("scapula", limit=1)] == ["c1"]
    assert [hit.document.doc_id for hit in index.search("bone", kinds=("note",))] == ["n1"]
    assert [hit.document.doc_id for hit in index.search("bone", document_ids=["d2"])] == ["c2"]
    # A page filter drops the out-of-range chunk but leaves pageless notes alone.
    assert sorted(hit.document.doc_id for hit in index.search("bone", page_from=1, page_to=5)) == ["c1", "n1"]
    assert index.search("ve bir") == []


def test_synonym_expansion_finds_a_turkish_query_in_an_english_chunk() -> None:
    terminology = TerminologyIndex()
    terminology.add_term({"canonical": "Scapula", "synonyms": ["kürek kemiği", "omuz kemiği"]})
    assert "scapula" in terminology.expand("kürek kemiği nerededir")
    plain, expanded = SearchIndex(), SearchIndex(terminology)
    plain.build(corpus())
    expanded.build(corpus())
    assert plain.search("kürek kemiği") == []
    hits = expanded.search("kürek kemiği")
    assert [hit.document.doc_id for hit in hits] == ["c1", "c2"]
    # A synonym match is weighted below a direct one on the same passage.
    assert hits[0].score < expanded.search("scapula")[0].score
    assert dict(expanded.query_terms("scapula"))["scapu"] == 1.0


def test_a_drawn_diagram_joins_the_vision_pass_behind_the_pictured_pages(tmp_path) -> None:
    """A slide exported from a drawing tool carries its figure as paths, not
    pixels; enough paths with little text is a figure, a text page with a few
    rules is not, and the budget goes to pictured pages first."""
    from app.medical.models import DocumentPage

    pipeline = DocumentPipeline(MedicalStore(), directory=tmp_path / "academy", vision_pages_per_document=2)
    pages = [
        DocumentPage(document_id="d", page_number=1, text="Uzun bir metin sayfasi. " * 40, path_count=30),   # ruled text
        DocumentPage(document_id="d", page_number=2, text="Humerus - on yuz", path_count=10),                 # drawn diagram
        DocumentPage(document_id="d", page_number=3, text="Fig", image_count=1, image_area_ratio=0.6),       # pictured
        DocumentPage(document_id="d", page_number=4, text="Sekil 2", path_count=25),                          # drawn diagram
        DocumentPage(document_id="d", page_number=5, text="Kisa not", path_count=3),                          # a few rules
    ]

    pipeline._mark_visual_pages(pages)

    assert [page.visual_status for page in pages] == ["not_needed", "skipped", "pending", "pending", "not_needed"]
