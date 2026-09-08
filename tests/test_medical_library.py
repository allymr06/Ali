"""Lecture sets: a folder of course material imported as one unit.

The folder tree is the student's own organisation of a semester, so the
academy keeps it: folder names become tags, the nearest folder that names
a subject sets the subject, and the set remembers what it took, what it
skipped and why. The counts a set shows are computed from the documents
themselves, never from what the import hoped for.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from app.medical.academy import create_medical_academy, folder_subject
from app.medical.documents import DocumentError
from app.medical.models import DocumentStatus

LECTURE = "Scapula omuz kusagindaki yassi ucgen kemiktir. Cavitas glenoidalis humerus ile eklem yapar.\n" * 10


@pytest.fixture()
def academy(tmp_path):
    # PowerPoint stays out of the tests: a deck is refused, as on a machine without it.
    built = create_medical_academy(settings=SimpleNamespace(medical_directory=str(tmp_path / "medical"), medical_office_conversion=False), provider_gateway=None)
    yield built
    built.close()


def make_folder(root):
    (root / "Komite 4" / "Anatomi").mkdir(parents=True)
    (root / "Komite 4" / "Histoloji ve Embriyoloji").mkdir(parents=True)
    (root / "HUP").mkdir()
    (root / "Komite 4" / "Anatomi" / "Anatomi 1 - Terminoloji.txt").write_text(LECTURE, encoding="utf-8")
    (root / "Komite 4" / "Anatomi" / "Anatomi 2 - Kemikler.md").write_text("# Kemikler\n" + LECTURE, encoding="utf-8")
    (root / "Komite 4" / "Histoloji ve Embriyoloji" / "Histoloji 3 - Epitel.txt").write_text("Epitel doku bazal membran uzerine oturur.\n" * 10, encoding="utf-8")
    (root / "HUP" / "Stresle Basa Cikma.txt").write_text("Stres yonetimi ve iletisim.\n" * 10, encoding="utf-8")
    (root / "HUP" / "sunum.pptx").write_bytes(b"PK\x03\x04")
    (root / "HUP" / "notlar.docx").write_bytes(b"x")
    (root / "HUP" / ".gizli.txt").write_text("x", encoding="utf-8")
    (root / "HUP" / "bos.txt").write_bytes(b"")
    return root


def test_folder_names_map_to_subjects_innermost_first() -> None:
    assert folder_subject(["Komite 4", "Anatomi Laboratuvar", "2 - Neurocranium"]) == "anatomy"
    assert folder_subject(["Komite 3", "Tıbbi Mikrobiyoloji", "Mikrobiyoloji 1"]) == "microbiology"
    assert folder_subject(["Komite 2", "Tıbbi Biyoloji", "Biyoloji 3"]) == "biology"
    assert folder_subject(["Komite 4", "Histoloji ve Embriyoloji", "Histoloji 5"]) == "histology"
    assert folder_subject(["Komite 5", "Biyokimya", "Biyokimya 1 - Kas"]) == "biochemistry"
    assert folder_subject(["Komite 5", "Anatomi", "Fizyoloji 1"]) == "physiology", "the file name is nearer than the folder"
    assert folder_subject(["HUP", "Stresle Başa Çıkma"]) is None
    assert folder_subject([]) is None


def test_a_folder_becomes_a_lecture_set_with_tags_subjects_and_an_honest_report(academy, tmp_path) -> None:
    root = make_folder(tmp_path / "dersler")
    events: list[dict] = []
    academy.subscribe(events.append)
    progress: list[tuple[int, int, str]] = []

    record = academy.import_folder(str(root), source="https://drive.google.com/x", progress=lambda done, total, current: progress.append((done, total, current)))

    assert record["name"] == "dersler" and record["source"] == "https://drive.google.com/x"
    # The empty file is filed too: what is wrong with it shows when it is processed.
    assert record["counts"] == {"files": 7, "imported": 5, "duplicates": 0, "skipped": 2, "failed": 0}
    assert {item["reason"] for item in record["skipped"]} == {"sunum için PowerPoint gerekli", "desteklenmeyen tür"}
    assert progress and progress[-1][1] == 7

    documents = {document.title: document for document in academy.store.list_documents()}
    assert documents["Anatomi 1 - Terminoloji"].subject == "anatomy"
    assert documents["Anatomi 1 - Terminoloji"].tags == ["dersler", "Komite 4", "Anatomi"]
    assert documents["Histoloji 3 - Epitel"].subject == "histology"
    assert documents["Stresle Basa Cikma"].subject is None and documents["Stresle Basa Cikma"].tags == ["dersler", "HUP"]
    assert all(document.status == DocumentStatus.PENDING for document in documents.values()), "importing files nothing is processed yet"
    assert events[-1]["kind"] == "lecture_set_imported" and events[-1]["imported"] == 5

    sets = academy.lecture_sets()
    assert len(sets) == 1
    listed = sets[0]
    assert listed["set_id"] == record["set_id"] and listed["documents_total"] == 5
    assert listed["ready"] == 0 and listed["pending"] == 5 and listed["failed"] == 0
    assert listed["subjects"] == [{"subject": "anatomy", "label": "Anatomi", "count": 2}, {"subject": "histology", "label": "Histoloji", "count": 1}]
    assert academy.dashboard()["lecture_sets"][0]["name"] == "dersler"
    assert academy.lecture_set("nope") is None
    detail = academy.lecture_set(record["set_id"])
    titles = [item["title"] for item in detail["documents"]]
    assert titles == sorted(titles, key=lambda title: {"bos": 0, "Stresle Basa Cikma": 1}.get(title, 2)) and len(titles) == 5, "members keep the folder walk order"
    assert {"Anatomi 1 - Terminoloji", "Anatomi 2 - Kemikler"} <= set(titles)

    # Importing the same folder again adds nothing and says so.
    again = academy.import_folder(str(root), name="Tekrar")
    assert again["counts"]["imported"] == 0 and again["counts"]["duplicates"] == 5
    assert "Tekrar" in academy.store.get_document(documents["Anatomi 1 - Terminoloji"].document_id).tags
    assert len(academy.lecture_sets()) == 2

    with pytest.raises(DocumentError, match="Klasör bulunamadı"):
        academy.import_folder(str(tmp_path / "yok"))


def test_processing_a_set_walks_its_pending_documents_and_notifies_once(academy, tmp_path) -> None:
    root = make_folder(tmp_path / "dersler")
    record = academy.import_folder(str(root))
    events: list[dict] = []
    academy.subscribe(events.append)

    report = asyncio.run(academy.process_lecture_set(record["set_id"]))

    assert report["queued"] == 5 and report["processed"] == 4 and report["failed"] == 1
    assert report["failures"][0]["title"] == "bos" and "boş" in report["failures"][0]["error"]
    kinds = [event["kind"] for event in events]
    assert kinds.count("lecture_set_progress") == 5 and kinds[-1] == "lecture_set_processed"
    ready = [event for event in events if event["kind"] == "document_ready"]
    assert len(ready) == 4 and all(event["quiet"] is True for event in ready)
    listed = academy.lecture_sets()[0]
    assert listed["ready"] == 4 and listed["pending"] == 0 and listed["failed"] == 1 and listed["processed_at"]

    # Nothing pending: the second run is an empty, honest report, and a failed
    # document is retried only when asked.
    again = asyncio.run(academy.process_lecture_set(record["set_id"]))
    assert again["queued"] == 0 and again["processed"] == 0
    retried = asyncio.run(academy.process_lecture_set(record["set_id"], retry_failed=True))
    assert retried["queued"] == 1 and retried["failed"] == 1
    with pytest.raises(DocumentError, match="Ders seti bulunamadı"):
        asyncio.run(academy.process_lecture_set("nope"))

    assert academy.delete_lecture_set(record["set_id"]) is True
    assert academy.lecture_sets() == [] and len(academy.store.list_documents()) == 5
    assert academy.delete_lecture_set(record["set_id"]) is False


def test_the_folder_job_imports_off_the_loop_then_processes_and_reports(academy, tmp_path) -> None:
    root = make_folder(tmp_path / "dersler")
    events: list[dict] = []
    academy.subscribe(events.append)

    outcome = asyncio.run(academy.import_folder_job(str(root), name="Dönem 1"))

    block = outcome["folder_import"]
    assert block["name"] == "Dönem 1" and block["imported"] == 5 and block["processed"] == 4
    assert block["skipped"] == 2 and block["failed"] == 0 and block["process_failed"] == 1
    assert any("Atlanan dosyalar" in note for note in block["notes"])
    assert any("İşlenemeyen belgeler" in note and "bos" in note for note in block["notes"])
    stages = [event.get("stage") for event in events if event["kind"] == "lecture_set_progress"]
    assert "importing" in stages, "the page hears the import walking the folder"
    assert academy.lecture_set(outcome["set_id"])["ready"] == 4



# ---------------------------------------------------------------------------
# professors from the material
# ---------------------------------------------------------------------------


ANATOMY_LECTURE = (
    "ALT EKSTREMİTE DAMARLARI\nProf. Dr. Ayla KÜRKÇÜOĞLU\n"
    "Arteria femoralis, arteria iliaca externa'nın devamıdır ve trigonum femorale içinde seyreder.\n" * 6
    + "\nTekrar soruları\n"
    "1. Arteria femoralis hangi arterin devamıdır?\nA) A. iliaca interna\nB) A. iliaca externa\nC) A. poplitea\n"
    "2. Şekilde okla işaretli damar hangisidir?\nA) V. saphena magna\nB) A. femoralis\n"
)
SECOND_LECTURE = (
    "BACAK ARKA BÖLGESİ\nPROF. DR. A. KÜRKÇÜOĞLU\n"
    "Musculus gastrocnemius ve musculus soleus tendo calcaneus ile calcaneus'a tutunur.\n" * 6
)
HISTOLOGY_LECTURE = "EPİTEL DOKU\nDoktor Öğretim Üyesi Pınar ŞAHİN\n" + "Epitel doku bazal membran üzerine oturur; hücreler arası madde azdır.\n" * 6
NAMELESS_LECTURE = "Mikrobiyoloji Görkem Cengiz 1 - Parazitoloji\n" + "Parazitler konak organizmada yaşar.\n" * 6
PLAIN_LECTURE = "SU VE pH\n" + "Su polar bir moleküldür; pH hidrojen iyonu konsantrasyonunun negatif logaritmasıdır.\n" * 6


def lecture_folder(root):
    (root / "Komite 5" / "Anatomi").mkdir(parents=True)
    (root / "Komite 4" / "Histoloji").mkdir(parents=True)
    (root / "Komite 3" / "Mikrobiyoloji").mkdir(parents=True)
    (root / "Komite 1").mkdir(parents=True)
    (root / "Komite 5" / "Anatomi" / "Anatomi 26 - Alt ekstremite damarları.txt").write_text(ANATOMY_LECTURE, encoding="utf-8")
    (root / "Komite 5" / "Anatomi" / "Anatomi 23 - Bacak arka bölge.txt").write_text(SECOND_LECTURE, encoding="utf-8")
    (root / "Komite 4" / "Histoloji" / "Histoloji 3 - Epitel.txt").write_text(HISTOLOGY_LECTURE, encoding="utf-8")
    (root / "Komite 3" / "Mikrobiyoloji" / "Mikrobiyoloji Görkem Cengiz 1 - Parazitoloji.txt").write_text(NAMELESS_LECTURE, encoding="utf-8")
    (root / "Komite 1" / "SU ve pH.txt").write_text(PLAIN_LECTURE, encoding="utf-8")
    return root


def test_lectures_are_attributed_to_the_lecturer_they_name_and_their_questions_are_filed(academy, tmp_path) -> None:
    root = lecture_folder(tmp_path / "dersler")
    record = academy.import_folder(str(root))
    asyncio.run(academy.process_lecture_set(record["set_id"]))
    events: list[dict] = []
    academy.subscribe(events.append)

    report = academy.mine_questions(set_id=record["set_id"])

    assert report["documents"] == 5 and report["attributed"] == 4
    by_name = {entry["name"]: entry for entry in report["professors"]}
    # Two spellings, one initial: one professor with two lectures.
    assert by_name["Prof. Dr. Ayla Kürkçüoğlu"]["documents"] == 2 and by_name["Prof. Dr. Ayla Kürkçüoğlu"]["questions_added"] == 2
    assert by_name["Dr. Öğr. Üyesi Pınar Şahin"]["documents"] == 1
    assert by_name["Görkem Cengiz"]["documents"] == 1, "the file name named the lecturer"
    assert report["unattributed"] == ["SU ve pH"] and report["incomplete"] == []
    assert report["questions_added"] == 2 and report["without_key"] == 2 and report["with_image"] == 0
    assert any("hoca adı bulunamadı" in note for note in report["notes"])
    assert any("cevap anahtarı" in note for note in report["notes"])

    profiles = {profile.name: profile for profile in academy.store.list_professors()}
    assert set(profiles) == {"Prof. Dr. Ayla Kürkçüoğlu", "Dr. Öğr. Üyesi Pınar Şahin", "Görkem Cengiz"}
    ayla = profiles["Prof. Dr. Ayla Kürkçüoğlu"]
    assert ayla.subject == "anatomy" and ayla.sample_size == 2 and "ders notlarındaki" in ayla.notes
    questions = academy.store.get_questions(ayla.question_ids)
    assert {question.origin for question in questions} == {"lecture_derived"}
    assert all(question.metadata.get("page_number") == 1 for question in questions), "each question knows its page"
    assert all(question.image_ref is None for question in questions), "a text page cannot be a figure"
    documents = {document.title: document for document in academy.store.list_documents()}
    assert documents["Anatomi 26 - Alt ekstremite damarları"].professor_id == ayla.profile_id
    assert documents["Anatomi 23 - Bacak arka bölge"].professor_id == ayla.profile_id
    assert documents["SU ve pH"].professor_id is None
    assert academy.professor(ayla.profile_id)["documents"][0]["title"].startswith("Anatomi")
    assert events[-1]["kind"] == "professors_mined" and events[-1]["professors"] == 3
    assert academy.lecture_set(record["set_id"])["mined"]["questions_added"] == 2

    # Running it again adds nothing and creates no second profile.
    again = academy.mine_questions(set_id=record["set_id"])
    assert again["questions_added"] == 0 and again["skipped"] == 2
    assert len(academy.store.list_professors()) == 3
    with pytest.raises(DocumentError, match="Ders seti bulunamadı"):
        academy.mine_questions(set_id="nope")

    job = asyncio.run(academy.mine_questions_job(document_ids=[documents["SU ve pH"].document_id]))
    assert job["mine"]["documents"] == 1 and job["mine"]["attributed"] == 0


def test_a_professor_style_exam_draws_on_that_professors_own_lectures(academy, tmp_path) -> None:
    root = lecture_folder(tmp_path / "dersler")
    record = academy.import_folder(str(root))
    asyncio.run(academy.process_lecture_set(record["set_id"]))
    academy.mine_questions(set_id=record["set_id"])
    ayla = next(profile for profile in academy.store.list_professors() if "Ayla" in profile.name)
    # The lecture questions carry no key, so a bank paper needs one keyed question of hers
    # (a different stem: the lecture's own review question would be skipped as a duplicate).
    keyed = "1. Trigonum femorale tabanını hangi yapı oluşturur?\nA) Ligamentum inguinale\nB) Musculus sartorius\nCevap: A\n"
    asyncio.run(academy.import_questions(professor_id=ayla.profile_id, name=None, subject="anatomy", text=keyed, use_model=False))

    exam = asyncio.run(academy.generate_exam({"professor_id": ayla.profile_id, "from_bank": True, "question_count": 5}))

    assert exam["config"]["professor_id"] == ayla.profile_id
    assert any("hocanın kendi ders notları (2 belge)" in note for note in exam["notes"])
    assert exam["config"]["subjects"] == ["anatomy"], "the professor's subject stands in for an unchosen one"



def test_a_lecture_that_quotes_a_doctor_is_not_cut_at_that_name(academy, tmp_path) -> None:
    root = tmp_path / "dersler"
    (root / "Komite 1").mkdir(parents=True)
    history = (
        "TIP TARİHİ\nProf. Dr. Şükrü Oğuz ÖZDAMAR\n"
        + "Cumhuriyet döneminde sağlık örgütlenmesi kuruldu.\n" * 12
        + "Dr. Refik Saydam\nSağlık Bakanlığı'nın kurucusu olarak anılır; sıtma ve verem savaşını başlatmıştır.\n"
        + "Dr. Behçet Uz\nÇocuk hastanesinin kurucusu.\n"
        + "Tekrar soruları\n1. Sağlık Bakanlığı'nın ilk bakanı kimdir?\nA) Refik Saydam\nB) Behçet Uz\n"
    )
    (root / "Komite 1" / "Tip Tarihi 3 - Cumhuriyet.txt").write_text(history, encoding="utf-8")
    record = academy.import_folder(str(root))
    asyncio.run(academy.process_lecture_set(record["set_id"]))

    report = academy.mine_questions(set_id=record["set_id"])

    names = [profile.name for profile in academy.store.list_professors()]
    assert names == ["Prof. Dr. Şükrü Oğuz Özdamar"], "the historical doctors are content, not lecturers"
    assert report["professors"][0]["questions_added"] == 1



# ---------------------------------------------------------------------------
# the vision pass against a provider that stops answering
# ---------------------------------------------------------------------------


class TogglingGateway:
    """Answers, refuses like a spent quota, or talks nonsense — as told."""

    def __init__(self) -> None:
        self.mode = "ok"
        self.calls = 0

    async def generate(self, request, context, **kwargs):
        from app.providers.base import ProviderUnavailableError

        self.calls += 1
        if self.mode == "outage":
            raise ProviderUnavailableError("429 RESOURCE_EXHAUSTED")
        if self.mode == "nonsense":
            return SimpleNamespace(text="not json")
        return SimpleNamespace(text='{"has_educational_figure": true, "legibility": "clear", "figure_type": "anatomy_diagram", "description": "Scapula arkadan.", "labels": ["spina scapulae"], "structures": ["scapula"], "educational_points": ["Spina scapulae iki fossayi ayirir."]}')


def figure_document(academy, tmp_path):
    from tests.test_medical_documents import make_pdf

    source = tmp_path / "sekiller.pdf"
    source.write_bytes(make_pdf([("Scapula posterior", True), ("Scapula anterior", True), ("Clavicula", True)]))
    document, _ = academy.import_document(str(source), subject="anatomy")
    processed = academy.pipeline.process(document.document_id)
    assert processed.status == DocumentStatus.READY and processed.visual_pages_pending == 3
    return processed


def test_a_quota_outage_leaves_the_figure_pages_pending_and_the_pass_can_be_resumed(tmp_path) -> None:
    gateway = TogglingGateway()
    academy = create_medical_academy(settings=SimpleNamespace(medical_directory=str(tmp_path / "m"), medical_office_conversion=False), provider_gateway=gateway)
    try:
        document = figure_document(academy, tmp_path)
        events: list[dict] = []
        academy.subscribe(events.append)

        gateway.mode = "outage"
        described = asyncio.run(academy._vision_pass(document, pace_seconds=0, retry_wait_seconds=0))
        assert described == 0 and gateway.calls == 2, "two refusals in a row end the pass"
        assert academy.vision_state["outage"] is True and "kota" in academy.vision_state["detail"]
        pages = academy.store.get_pages(document.document_id)
        assert [page.visual_status for page in pages] == ["pending", "pending", "pending"], "an outage is not a verdict on the page"
        assert any("Şekil incelemesi durdu" in event.get("detail", "") for event in events if event["kind"] == "document_status")

        # A page the model reads as nonsense is a failure of that page, not an outage.
        gateway.mode = "nonsense"
        asyncio.run(academy._vision_pass(document, pace_seconds=0, retry_wait_seconds=0))
        assert academy.vision_state["outage"] is False
        assert {page.visual_status for page in academy.store.get_pages(document.document_id)} == {"failed"}

        # Resuming later describes what is still pending and analyses the document.
        for page in academy.store.get_pages(document.document_id):
            academy.pipeline.attach_visual_summary(document.document_id, page.page_number, summary="", labels=[], status="pending")
        gateway.mode = "ok"
        outcome = asyncio.run(academy.continue_processing(document_id=document.document_id, analysis=False))
        report = outcome["continue"]
        assert report["described"] == 3 and report["stopped"] is None and report["queued"] == 1
        refreshed = academy.store.get_document(document.document_id)
        assert refreshed.visual_pages_pending == 0 and refreshed.visual_pages_analyzed == 3
        assert events[-1]["kind"] == "vision_resumed" and events[-1]["described"] == 3
        # Nothing left: an honest empty report, and unknown ids are refused.
        assert asyncio.run(academy.continue_processing(document_id=document.document_id, analysis=False))["continue"]["queued"] == 0
        with pytest.raises(DocumentError, match="Belge bulunamadı"):
            asyncio.run(academy.continue_processing(document_id="nope"))
        with pytest.raises(DocumentError, match="Ders seti bulunamadı"):
            asyncio.run(academy.continue_processing(set_id="nope"))
    finally:
        academy.close()



BOOK = (
    "Halk Sağlığı\nEditör\nDoç. Dr. Birgül Piyal\nYazarlar\nProf. Dr. Recep Akdur\nANKARA ÜNİVERSİTESİ YAYINLARI\n"
    "ISBN: 978-975-482-970-9\n© Ankara Üniversitesi, 2011\n1. Baskı\n"
    + "Halk sağlığı, toplumun sağlığını koruma ve geliştirme bilimidir.\n" * 8
    + "Değerlendirme Soruları\n1. Halk sağlığının tanımı aşağıdakilerden hangisidir?\nA) Toplumun sağlığını koruma bilimi\nB) Yalnızca tedavi hizmeti\n"
)
SCANNED_DECK = "Bakteri Metabolizmasi\n"


def test_a_published_book_keeps_its_questions_but_its_authors_are_not_lecturers(academy, tmp_path) -> None:
    root = tmp_path / "dersler"
    (root / "Komite 1").mkdir(parents=True)
    (root / "Komite 1" / "Halk Sağlığı.txt").write_text(BOOK, encoding="utf-8")
    record = academy.import_folder(str(root))
    asyncio.run(academy.process_lecture_set(record["set_id"]))

    report = academy.mine_questions(set_id=record["set_id"])

    assert academy.store.list_professors() == [], "a book's editor is not the student's lecturer"
    assert report["books"] == ["Halk Sağlığı"] and report["professors"] == []
    assert report["unattributed"] == [], "a book is not reported as a lecture nobody signed"
    assert report["questions_added"] == 1
    question = academy.store.query_questions(limit=10)[0]
    assert question.professor_id is None and "kitaptan" in question.tags
    assert any("yayımlanmış kitap" in note for note in report["notes"])


def test_a_lecturer_named_only_on_a_pictured_cover_is_read_from_the_figure(academy, tmp_path) -> None:
    root = tmp_path / "dersler"
    (root / "Komite 3" / "Mikrobiyoloji").mkdir(parents=True)
    (root / "Komite 3" / "Mikrobiyoloji" / "Mikrobiyoloji 5 - Bakteri metabolizması.txt").write_text(SCANNED_DECK, encoding="utf-8")
    record = academy.import_folder(str(root))
    asyncio.run(academy.process_lecture_set(record["set_id"]))
    document = academy.store.list_documents()[0]

    # Nothing on the page names anybody, so the document has no lecturer yet.
    assert academy.professor_for_document(document) is None

    # The vision pass describes the cover; the name is in that description.
    academy.pipeline.attach_visual_summary(
        document.document_id,
        1,
        summary="Şekil (other): Sunumun kapak slaytı. Başlıkta 'Bakteri Metabolizması', altında Prof. Dr. Özgül Kısa yazıyor.",
        labels=["Bakteri Metabolizması"],
    )
    mention = academy.professor_for_document(document)
    assert mention is not None and mention.name == "Prof. Dr. Özgül Kısa" and mention.source == "visual"

    report = academy.mine_questions(set_id=record["set_id"])
    assert [entry["name"] for entry in report["professors"]] == ["Prof. Dr. Özgül Kısa"]
    assert report["from_pictures"] == 1 and any("görüntüden okunduğu" in note for note in report["notes"])
    assert academy.store.get_document(document.document_id).professor_id == academy.store.list_professors()[0].profile_id

    # A page that is not a cover names nobody, however many people it pictures.
    academy.pipeline.attach_visual_summary(
        document.document_id,
        1,
        summary="Görselde Dr. Refik Saydam'ın portresi ve sağlık örgütlenmesi şeması var.",
        labels=[],
    )
    for profile in academy.store.list_professors():
        academy.store.delete_professor(profile.profile_id)
    document = academy.store.get_document(document.document_id)
    document.professor_id = None
    academy.store.save_document(document)
    assert academy.professor_for_document(document) is None



# ---------------------------------------------------------------------------
# an exam export: every question to its own owner, keys from the paper's marks
# ---------------------------------------------------------------------------

EXPORT = (
    "KOMITE 5\n\n1. soru:\nEklem tipi art. sellaris olan ve discus articularis taşıyan eklem hangisidir?\nSoru Sahibi : RABET GÖZİL\nAnabilimdalı : Anatomi\n\n"
    "A) Art. sternoclavicularis-Doğru Seçenek\nB) Art. acromioclavicularis-Öğrencinin işaretlediği\nC) Skapulotorakal eklem\nD) Artt. costochondrales\nE) Artt. costotransversaria\n\n"
    " 2. soru:\nSulcus arteriae vertebralis hangi kemikte bulunur?\nSoru Sahibi : HAKKI YEŞİLYURT\nAnabilimdalı : Anatomi\n\n"
    "A) Os occipitale\nB) Os temporale\nC) Os sphenoidale\nD) Atlas-Doğru Seçenek\nE) Axis\n\n"
    " 3. soru:\nHangi yapı sadece servikal vertebralarda bulunur?\nSoru Sahibi : HAKKI YEŞİLYURT\nAnabilimdalı : Anatomi\n\n"
    "A) Foramen vertebrale\nB) Foramen transversarium-Doğru Seçenek\nC) Processus transversus\nD) Incisura vertebralis inferior\nE) Processus articularis superior\n"
    "KOMITE 1\n\n29. soru:\nAlkanların genel formülü hangisidir?\nSoru Sahibi : CUMHUR BİLGİ\nAnabilimdalı : Tıbbi Biyokimya\n\n"
    "A) CnH2n+2-Doğru Seçenek\nB) CnH2n-2\nC) CnH2n+1\nD) CnH2n-1\nE) CnH2n\n"
)


def test_an_exam_export_files_every_question_under_its_own_owner_with_the_papers_key(academy, tmp_path) -> None:
    root = tmp_path / "cikmislar"
    root.mkdir()
    (root / "Anatomi Tüm Komiteler Çıkmış.txt").write_text(EXPORT, encoding="utf-8")
    # A lecturer already known from a lecture merges with the owner line's spelling.
    known = academy.profiler.profile("Prof. Dr. Rabet Gözil", [], subject="anatomy")
    academy.store.save_professor(known)
    record = academy.import_folder(str(root), name="Çıkmış sorular")
    asyncio.run(academy.process_lecture_set(record["set_id"]))

    report = academy.mine_questions(set_id=record["set_id"])

    assert report["papers"] == ["Anatomi Tüm Komiteler Çıkmış"] and report["unattributed"] == []
    by_name = {entry["name"]: entry for entry in report["professors"]}
    assert set(by_name) == {"Prof. Dr. Rabet Gözil", "Hakkı Yeşilyurt", "Cumhur Bilgi"}
    assert by_name["Hakkı Yeşilyurt"]["questions_added"] == 2 and by_name["Cumhur Bilgi"]["questions_added"] == 1
    assert report["questions_added"] == 4 and report["without_key"] == 0
    assert len(academy.store.list_professors()) == 3, "the owner line merged into the known lecturer instead of a second profile"
    document = academy.store.list_documents()[0]
    assert document.professor_id is None, "a paper with many owners belongs to nobody as a whole"

    gozil = academy.store.get_professor(known.profile_id)
    questions = academy.store.get_questions(gozil.question_ids)
    assert len(questions) == 1 and questions[0].correct_key == "A" and questions[0].origin == "imported_exam"
    assert {"çıkmış", "Komite 5", "Anatomi"} <= set(questions[0].tags)
    assert questions[0].metadata["owner"] == "RABET GÖZİL" and questions[0].metadata["student_marked"] == "B" and questions[0].metadata["committee"] == "5"
    assert questions[0].subject == "anatomy"
    assert "çıkmış sınav kâğıtlarından" in gozil.notes
    bilgi = next(profile for profile in academy.store.list_professors() if profile.name == "Cumhur Bilgi")
    assert bilgi.subject == "biochemistry", "the department line names the subject"
    assert academy.store.get_questions(bilgi.question_ids)[0].correct_key == "A"
    # The style profile now rests on keyed questions and says so honestly.
    payload = academy.professor(gozil.profile_id)
    assert payload["sample_size"] == 1 and payload["confidence"] == "limited"
    assert any("çıkmış" in note for note in report["notes"])


def test_scanned_pages_are_transcribed_and_then_mined_like_any_paper(tmp_path) -> None:
    from tests.test_medical_documents import make_pdf

    class OcrGateway:
        def __init__(self) -> None:
            self.mode = "ok"
            self.calls = 0

        async def generate(self, request, context, **kwargs):
            from app.providers.base import ProviderUnavailableError

            self.calls += 1
            if self.mode == "outage":
                raise ProviderUnavailableError("503 overloaded")
            return SimpleNamespace(text=EXPORT)

    gateway = OcrGateway()
    academy = create_medical_academy(settings=SimpleNamespace(medical_directory=str(tmp_path / "m"), medical_office_conversion=False), provider_gateway=gateway)
    try:
        source = tmp_path / "Fizyoloji Tüm Komiteler Çıkmış.pdf"
        source.write_bytes(make_pdf([("", True), ("", True)]))
        document, _ = academy.import_document(str(source))
        processed = academy.pipeline.process(document.document_id)
        assert processed.status == DocumentStatus.READY and processed.chunk_count == 0
        assert academy._needs_ocr(processed) is True

        gateway.mode = "outage"
        read = asyncio.run(academy.transcribe_document(document.document_id, pace_seconds=0, retry_wait_seconds=0))
        assert read["transcribed"] == 0 and read["stopped"] and "kota" in read["stopped"]
        assert all(page.char_count == 0 for page in academy.store.get_pages(document.document_id)), "an outage leaves the page as it was"

        gateway.mode = "ok"
        outcome = asyncio.run(academy.continue_processing(document_id=document.document_id, vision=False, analysis=False))
        assert outcome["continue"]["transcribed"] == 2 and outcome["continue"]["stopped"] is None
        refreshed = academy.store.get_document(document.document_id)
        assert refreshed.chunk_count > 0 and "taranmış metin" in refreshed.tags
        assert academy._needs_ocr(refreshed) is False
        assert "Sulcus arteriae vertebralis" in academy.store.get_page(document.document_id, 1).text
        # The transcribed paper mines like a text one; the questions remember they came from a scan.
        report = academy.mine_questions(document_ids=[document.document_id])
        assert report["questions_added"] == 4 and report["papers"] == [refreshed.title]
        question = academy.store.query_questions(limit=10)[0]
        assert question.metadata.get("ocr") is True and question.correct_key is not None
        # A second pass has nothing to read.
        assert asyncio.run(academy.continue_processing(document_id=document.document_id, vision=False, analysis=False))["continue"]["queued"] == 0
    finally:
        academy.close()


def test_mining_tells_apart_questions_that_share_a_generic_stem(academy, tmp_path) -> None:
    root = tmp_path / "cikmislar"
    root.mkdir()
    paper = (
        "KOMİTE 2\n\n27. soru:\nAşağıdaki ifadelerden hangisi yanlıştır?\nSoru Sahibi : AYŞE CANSEVEN\nAnabilimdalı : Biyofizik\n\n"
        "A) Isı bir enerji türüdür\nB) Entropi düzensizliktir\nC) Sıcaklık bir enerjidir-Doğru Seçenek\nD) İş yol bağımlıdır\nE) Enerji korunur\n\n"
        "28. soru:\nAşağıdaki ifadelerden hangisi yanlıştır?\nSoru Sahibi : AYŞE CANSEVEN\nAnabilimdalı : Biyofizik\n\n"
        "A) Kas bir dönüştürücüdür\nB) Kemik esnektir-Doğru Seçenek\nC) Tendon gerilir\nD) Kıkırdak yumuşaktır\nE) Deri katmanlıdır\n\n"
        "29. soru:\nAşağıdaki ifadelerden hangisi yanlıştır?\nSoru Sahibi : AYŞE CANSEVEN\nAnabilimdalı : Biyofizik\n\n"
        "A) Kas bir dönüştürücüdür\nB) Kemik esnektir-Doğru Seçenek\nC) Tendon gerilir\nD) Kıkırdak yumuşaktır\nE) Deri katmanlıdır\n"
    )
    (root / "Biyofizik Tüm Komiteler Çıkmış.txt").write_text(paper, encoding="utf-8")
    record = academy.import_folder(str(root), name="Çıkmış sorular")
    asyncio.run(academy.process_lecture_set(record["set_id"]))

    report = academy.mine_questions(set_id=record["set_id"])

    # Two questions with the same stem and different options are two questions;
    # the third repeats the second word for word and is the only one skipped.
    assert report["questions_added"] == 2 and report["skipped"] == 1
    profile = next(item for item in academy.store.list_professors() if "Canseven" in item.name)
    assert sorted(question.correct_key for question in academy.store.get_questions(profile.question_ids)) == ["B", "C"]
    assert academy.mine_questions(set_id=record["set_id"])["questions_added"] == 0, "a second pass files nothing twice"
