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
