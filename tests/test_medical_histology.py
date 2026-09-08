"""Histology practicals: a specimen is a region of the student's own page.

The crop opens its exact source and never alters the page; a mask is kept
beside the image; a description the model wrote is not an answer until the
student confirms one; an uncertain specimen cannot be scored; the answer is
hidden during a test; identification and explanation are two outcomes; a
repeated image is not new evidence; and it all survives a restart.
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from app.medical.catalog import Curriculum
from app.medical.concepts import default_concept_graph
from app.medical.documents import DocumentError, DocumentPipeline
from app.medical.histology import EMPTY_STATE_TR, HistologyBank, identification_matches
from app.medical.learning import LearningEngine
from app.medical.model import MedicalModelClient
from app.medical.retrieval import Retriever
from app.medical.store import MedicalStore
from app.medical.understanding import UnderstandingEngine
from tests.test_medical_documents import make_pdf

BASE = datetime(2026, 9, 8, 9, 0, tzinfo=timezone.utc)


class Gateway:
    def __init__(self, *replies: str) -> None:
        self.replies = list(replies)
        self.prompts: list[str] = []

    async def generate(self, request, context, **kwargs):
        self.prompts.append(request.text)
        reply = self.replies.pop(0) if len(self.replies) > 1 else self.replies[0]
        return SimpleNamespace(text=reply)


def build(tmp_path, gateway=None, *, path=None):
    store = MedicalStore(path)
    pipeline = DocumentPipeline(store, directory=tmp_path / "academy")
    curriculum = Curriculum()
    concepts = default_concept_graph()
    learning = LearningEngine(store, curriculum, concepts, clock=lambda: BASE)
    model = MedicalModelClient(gateway)
    understanding = UnderstandingEngine(store, learning, concepts, curriculum, model, Retriever(store), clock=lambda: BASE)
    bank = HistologyBank(store, pipeline, learning, understanding, concepts, model, clock=lambda: BASE)
    return bank, store, pipeline, learning, understanding


def imported_page(pipeline: DocumentPipeline, tmp_path, name: str = "histoloji.pdf") -> str:
    source = tmp_path / name
    source.write_bytes(make_pdf([("Hiyalin kikirdak, HE boyasi, 100x", True), ("Elastik kikirdak", True)]))
    document, _created = pipeline.import_file(source)
    pipeline.process(document.document_id)
    return document.document_id


REGION = {"x": 0.05, "y": 0.3, "w": 0.9, "h": 0.6}


def test_a_lenient_examiner_forgives_spelling_but_not_another_tissue() -> None:
    assert identification_matches("hiyalin kıkırdak", ["Hiyalin kıkırdak"])
    assert identification_matches("HIYALIN KIKIRDAK", ["Hiyalin kıkırdak"])
    assert identification_matches("cartilago hyalina", ["Hiyalin kıkırdak", "Cartilago hyalina"])
    assert identification_matches("hiyali kıkırd", ["Hiyalin kıkırdak"]), "five letters may stand for the word"
    assert not identification_matches("elastik kıkırdak", ["Hiyalin kıkırdak"])
    assert not identification_matches("kıkırdak", ["Hiyalin kıkırdak"]), "naming the family is not naming the tissue"
    assert not identification_matches("", ["Hiyalin kıkırdak"])


def test_a_specimen_keeps_its_source_and_its_crop_never_touches_the_page(tmp_path) -> None:
    bank, store, pipeline, _learning, _understanding = build(tmp_path)
    document_id = imported_page(pipeline, tmp_path)
    page_before = pipeline.render_page(document_id, 1)

    specimen = bank.add_specimen(document_id, 1, REGION, label="Hiyalin kıkırdak", latin="Cartilago hyalina", basis="page_caption", features=["Homojen bazofilik matriks", "İzogen gruplar"])

    assert specimen["status"] == "eligible" and specimen["basis_label"] == "Sayfadaki başlık ya da etiket"
    assert specimen["stain"] is None and specimen["magnification"] is None, "unknown metadata stays unknown"
    assert "Hiyalin kikirdak" in specimen["caption_excerpt"] and specimen["source_hash"]
    crop = bank.crop(specimen["specimen_id"])
    assert crop[:8] == bytes.fromhex("89504e470d0a1a0a") and len(crop) < len(page_before) * 2
    assert bank.crop(specimen["specimen_id"]) == crop, "cached"
    assert pipeline.render_page(document_id, 1) == page_before, "the page image is what it was"
    source = bank.source(specimen["specimen_id"])
    assert source == {"document_id": document_id, "title": source["title"], "page_number": 1, "region": REGION, "available": True, "changed": False}
    assert bank.crop_data_url(specimen["specimen_id"]).startswith("data:image/png;base64,")
    with pytest.raises(ValueError):
        bank.add_specimen(document_id, 1, {"x": 0.5, "y": 0.5, "w": 0.7, "h": 0.2})
    with pytest.raises(ValueError):
        bank.add_specimen(document_id, 9, REGION)
    with pytest.raises(DocumentError):
        pipeline.render_region(document_id, 1, (0.5, 0.5, 0.7, 0.2))


def test_a_mask_lives_beside_the_image_and_a_description_is_not_an_answer(tmp_path) -> None:
    bank, store, pipeline, _learning, _understanding = build(tmp_path)
    document_id = imported_page(pipeline, tmp_path)
    page = store.get_page(document_id, 1)
    page.visual_summary = "Şekil (histology_micrograph): hiyalin kıkırdak kesiti, kondrositler lakünalarda."
    page.visual_labels = ["kondrosit", "perikondriyum"]
    store.save_page(page)

    specimen = bank.add_specimen(document_id, 1, REGION)
    assert specimen["label"] == "" and specimen["status"] == "study_only" and specimen["basis"] == "none"
    assert "hiyalin kıkırdak kesiti" in specimen["model_description"] and specimen["model_labels"] == ["kondrosit", "perikondriyum"]
    assert bank.start_session(mode="timed") is None, "an unconfirmed specimen cannot be scored"

    crop_before = bank.crop(specimen["specimen_id"])
    mask = bank.add_mask(specimen["specimen_id"], kind="rect", points=[[0.1, 0.1], [0.4, 0.4]], label="Lakün")
    assert bank.specimen(specimen["specimen_id"])["masks"] == [mask] and bank.crop(specimen["specimen_id"]) == crop_before
    with pytest.raises(ValueError):
        bank.add_mask(specimen["specimen_id"], kind="polygon", points=[[0, 0], [1, 1]])
    with pytest.raises(ValueError):
        bank.add_mask(specimen["specimen_id"], kind="rect", points=[[0, 0], [2, 1]])
    assert bank.remove_mask(specimen["specimen_id"], mask["mask_id"])["masks"] == []

    with pytest.raises(ValueError, match="dayanağı"):
        bank.add_specimen(document_id, 1, REGION, label="Hiyalin kıkırdak")
    with pytest.raises(ValueError):
        bank.update_specimen(specimen["specimen_id"], {"label": "Hiyalin kıkırdak"})
    suggested = bank.update_specimen(specimen["specimen_id"], {"label": "Hiyalin kıkırdak", "basis": "model_description"})
    assert suggested["status"] == "study_only", "the model's description alone is not ground truth"

    confirmed = bank.confirm_label(specimen["specimen_id"], "Hiyalin kıkırdak", latin="Cartilago hyalina", features=["İzogen gruplar"], stain="HE")
    assert confirmed["status"] == "eligible" and confirmed["basis"] == "user_confirmed" and confirmed["stain"] == "HE"
    unreadable = bank.update_specimen(specimen["specimen_id"], {"unreadable": True})
    assert unreadable["status"] == "unreadable" and bank.start_session(mode="timed") is None
    assert bank.update_specimen(specimen["specimen_id"], {"unreadable": False})["status"] == "eligible"


def test_a_timed_session_hides_the_answer_and_keeps_identification_and_explanation_apart(tmp_path) -> None:
    gateway = Gateway(json.dumps({"quality": "specific", "features_named": ["İzogen gruplar"], "note": "İyi."}), json.dumps({"quality": "generic", "features_named": [], "note": "Her dokuya uyar."}))
    bank, store, pipeline, learning, understanding = build(tmp_path, gateway)
    document_id = imported_page(pipeline, tmp_path)
    hyaline = bank.add_specimen(document_id, 1, REGION, label="Hiyalin kıkırdak", basis="user_confirmed", features=["İzogen gruplar", "Homojen matriks"])
    elastic = bank.add_specimen(document_id, 2, REGION, label="Elastik kıkırdak", basis="page_caption", features=["Elastik lifler"])
    bank.update_specimen(hyaline["specimen_id"], {"confusable_with": [elastic["specimen_id"]]})

    session = bank.start_session(mode="timed", seconds=45, count=5)

    assert session["mode"] == "timed" and session["seconds"] == 45 and [item["specimen_id"] for item in session["items"]] == [hyaline["specimen_id"], elastic["specimen_id"]]
    shown = bank.show(session["session_id"], 0)
    first = shown["items"][0]["specimen"]
    assert "label" not in first and "features" not in first and "latin" not in first, "the answer sheet is not on screen"
    assert bank.specimen(hyaline["specimen_id"])["exposures"] == 1

    after = asyncio.run(bank.answer(session["session_id"], hyaline["specimen_id"], "hiyalin kıkırdak", confidence="sure", explanation="İzogen gruplar ve homojen matriks görüyorum."))
    answer = after["items"][0]["answer"]
    assert answer["correct"] is True and answer["explanation_quality"] == "specific" and answer["features_named"] == ["İzogen gruplar"]
    assert after["items"][0]["specimen"]["label"] == "Hiyalin kıkırdak", "revealed once answered"
    assert learning.summary()["attempts"] == 1 and understanding.events()[0]["source"] == "histology" and understanding.events()[0]["confidence"] == "sure"
    again = asyncio.run(bank.answer(session["session_id"], hyaline["specimen_id"], "elastik", confidence="guess"))
    assert again["items"][0]["answer"]["given"] == "hiyalin kıkırdak", "answered once"

    done = asyncio.run(bank.answer(session["session_id"], elastic["specimen_id"], "elastik kıkırdak", explanation="Kıkırdak işte."))
    results = done["results"]
    assert done["status"] == "closed" and results["scored"] == 2 and results["identified"] == 2 and results["identification_accuracy"] == 1.0
    assert results["explanation_quality"] == {"specific": 1, "generic": 1}, "a right name with a generic reason is recorded as such"
    assert results["repeated_specimens"] == 0
    comparison = bank.compare(hyaline["specimen_id"])
    assert comparison["confusable"][0]["label"] == "Elastik kıkırdak" and comparison["confusable"][0]["features"] == ["Elastik lifler"]


def test_a_study_session_scores_nothing_uncertain_and_a_repeat_is_named(tmp_path) -> None:
    bank, store, pipeline, learning, _understanding = build(tmp_path)
    document_id = imported_page(pipeline, tmp_path)
    eligible = bank.add_specimen(document_id, 1, REGION, label="Hiyalin kıkırdak", basis="user_confirmed")
    uncertain = bank.add_specimen(document_id, 2, REGION)

    first = bank.start_session(mode="study")
    assert [item["scored"] for item in first["items"]] == [True, False]
    bank.show(first["session_id"], 0)
    asyncio.run(bank.answer(first["session_id"], eligible["specimen_id"], "hiyalin kıkırdak"))
    asyncio.run(bank.answer(first["session_id"], uncertain["specimen_id"], "bir şey", explanation="Bilmiyorum."))
    results = bank.session(first["session_id"])["results"]
    assert results["scored"] == 1 and results["study_only"] == 1 and "yalnız çalışma" in results["note"]
    assert learning.summary()["attempts"] == 1, "the uncertain specimen moved nothing"
    assert bank.session(first["session_id"])["items"][1]["answer"]["explanation_quality"] == "unassessed"

    second = bank.start_session(mode="study", count=1)
    assert second["items"][0]["specimen_id"] == uncertain["specimen_id"], "the least-seen specimen comes first"
    third = bank.start_session(mode="timed")
    bank.show(third["session_id"], 0)
    done = asyncio.run(bank.answer(third["session_id"], eligible["specimen_id"], "hiyalin kıkırdak"))
    assert done["results"]["repeated_specimens"] == 1 and "daha önce görülmüştü" in done["results"]["note"]


def test_the_empty_state_explains_how_to_add_material_and_nothing_is_invented(tmp_path) -> None:
    bank, _store, _pipeline, _learning, _understanding = build(tmp_path)
    overview = bank.overview()
    assert overview["total"] == 0 and overview["specimens"] == [] and overview["empty_state"] == EMPTY_STATE_TR
    assert overview["counts"] == {"eligible": 0, "study_only": 0, "unreadable": 0}


def test_specimens_and_sessions_survive_a_restart_and_a_changed_source_is_flagged(tmp_path) -> None:
    path = tmp_path / "medical.sqlite3"
    bank, store, pipeline, _learning, _understanding = build(tmp_path, path=path)
    document_id = imported_page(pipeline, tmp_path)
    specimen = bank.add_specimen(document_id, 1, REGION, label="Hiyalin kıkırdak", basis="user_confirmed")
    session = bank.start_session(mode="timed")
    asyncio.run(bank.answer(session["session_id"], specimen["specimen_id"], "hiyalin kıkırdak"))
    store.close()

    again, store2, _p, _l, _u = build(tmp_path, path=path)
    assert again.specimen(specimen["specimen_id"])["label"] == "Hiyalin kıkırdak"
    assert again.session(session["session_id"])["results"]["identified"] == 1
    document = store2.get_document(document_id)
    document.sha256 = "sha-new"
    store2.save_document(document)
    assert again.source(specimen["specimen_id"])["changed"] is True
    assert again.payload(again.specimen(specimen["specimen_id"]), reveal=False)["source_changed"] is True
    assert again.delete_specimen(specimen["specimen_id"]) is True and again.specimen(specimen["specimen_id"]) is None
