"""Prerequisites with provenance, and the diagnosis that walks them.

An association is not a prerequisite; a suggested edge is pending until the
student confirms it and a cycle is refused; traversal survives a cycle and an
unknown concept; a diagnosis asks a bounded set of short questions, locates
the deepest failing foundation, lays a short path back to the objective,
lets the student skip, shorten or redirect, and survives a restart.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from app.medical.catalog import Curriculum
from app.medical.concepts import default_concept_graph
from app.medical.learning import LearningEngine
from app.medical.model import MedicalModelClient
from app.medical.models import ConceptMastery, Question, QuestionOption
from app.medical.prerequisites import (
    MAX_DIAGNOSTIC_QUESTIONS,
    PrerequisiteDiagnosis,
    PrerequisiteGraph,
    load_seed_edges,
)
from app.medical.retrieval import Retriever
from app.medical.store import MedicalStore
from app.medical.understanding import UnderstandingEngine

AP = "physiology.action_potential"
RMP = "physiology.resting_membrane_potential"
NERNST = "biophysics.nernst"
PUMP = "physiology.na_k_atpase"
TRANSPORT = "physiology.transport_types"
BASE = datetime(2026, 9, 8, 9, 0, tzinfo=timezone.utc)


class Clock:
    def __init__(self) -> None:
        self.now = BASE

    def __call__(self) -> datetime:
        return self.now

    def advance(self, **delta) -> None:
        self.now = self.now + timedelta(**delta)


def question(question_id: str, concept: str, subject: str, stem: str) -> Question:
    options = [QuestionOption(key, text) for key, text in zip("ABCD", ["Bir", "İki", "Üç", "Dört"])]
    return Question(question_id=question_id, subject=subject, stem=stem, options=options, correct_key="B", concept_ids=[concept], explanation="Açıklama burada durur.")


def build(path=None, *, with_questions: bool = True):
    store = MedicalStore(path)
    concepts = default_concept_graph()
    curriculum = Curriculum()
    clock = Clock()
    learning = LearningEngine(store, curriculum, concepts, clock=clock)
    understanding = UnderstandingEngine(store, learning, concepts, curriculum, MedicalModelClient(None), Retriever(store), clock=clock)
    graph = PrerequisiteGraph(concepts, store, clock=clock)
    diagnosis = PrerequisiteDiagnosis(graph, store, learning, understanding, curriculum, clock=clock)
    if with_questions:
        store.save_question(question("q-rmp", RMP, "physiology", "Dinlenim potansiyeli hangi iyonun denge potansiyeline yakındır?"))
        store.save_question(question("q-nernst", NERNST, "biophysics", "Nernst denklemi neyi verir?"))
        store.save_question(question("q-pump", PUMP, "physiology", "Na+/K+-ATPaz bir döngüde kaç Na+ atar?"))
        store.save_question(question("q-ap", AP, "physiology", "Aksiyon potansiyelinin yükselen fazı?"))
    return graph, diagnosis, store, learning, understanding, clock


# ---------------------------------------------------------------------------
# the curated edges
# ---------------------------------------------------------------------------


def test_every_curated_prerequisite_names_known_concepts_and_the_set_has_no_cycle() -> None:
    concepts = default_concept_graph()
    edges = load_seed_edges()
    assert len(edges) >= 40
    for edge in edges:
        assert concepts.get(edge.concept_id) is not None, edge.concept_id
        assert concepts.get(edge.requires) is not None, edge.requires
        assert edge.concept_id != edge.requires and edge.provenance == "curriculum" and edge.status == "reviewed" and edge.note
    graph = PrerequisiteGraph(concepts)
    for edge in edges:
        assert edge.concept_id not in {item["concept_id"] for item in graph.prerequisites_of(edge.requires, depth=60)}, f"cycle through {edge.edge_id}"
    assert len({edge.edge_id for edge in edges}) == len(edges)


def test_an_association_is_not_a_prerequisite() -> None:
    concepts = default_concept_graph()
    graph = PrerequisiteGraph(concepts)
    # Hyaline cartilage contrasts with elastic cartilage in the concept graph…
    assert any(kind == "contrasts_with" for kind, _concept in concepts.related("histology.hyaline_cartilage"))
    # …and that relation does not make either a prerequisite of the other.
    assert graph.prerequisites_of("histology.hyaline_cartilage") == []
    assert "histology.hyaline_cartilage" not in {item["concept_id"] for item in graph.prerequisites_of("histology.elastic_cartilage")}
    # 'depends_on' in the concept data is likewise left alone unless curated here.
    assert all(edge.provenance == "curriculum" for edge in graph.edges())


def test_prerequisites_are_walked_nearest_first_across_subjects_and_bounded_by_depth() -> None:
    graph = PrerequisiteGraph(default_concept_graph())

    found = graph.prerequisites_of(AP)

    by_id = {item["concept_id"]: item for item in found}
    assert by_id[RMP]["depth"] == 1 and by_id[RMP]["via"] == AP and by_id[RMP]["provenance"] == "curriculum"
    assert by_id[NERNST]["depth"] == 2 and by_id[NERNST]["subject"] == "biophysics", "a physiology difficulty may rest on biophysics"
    assert by_id[NERNST]["path"] == [AP, RMP, NERNST]
    assert {PUMP, TRANSPORT} <= set(by_id)
    assert [item["concept_id"] for item in graph.prerequisites_of(AP, depth=1)] == [RMP]
    assert graph.path_to(NERNST, AP) == [NERNST, RMP, AP] and graph.path_to("histology.he_stain", AP) == []
    assert [edge.concept_id for edge in graph.dependents_of(RMP)] == [AP]
    assert graph.prerequisites_of("no.such.concept") == [] and graph.known("no.such.concept") is False


def test_a_suggested_edge_waits_for_confirmation_and_a_cycle_is_refused(tmp_path) -> None:
    graph, _diagnosis, store, _learning, _understanding, _clock = build(tmp_path / "medical.sqlite3", with_questions=False)

    edge = graph.suggest("physiology.synapse", "biology.fluid_mosaic", note="Ders 4 böyle diyor", source={"document_id": "d1", "page_number": 3}, provenance="imported")

    assert edge.status == "pending" and edge.provenance == "imported"
    assert "biology.fluid_mosaic" not in {item["concept_id"] for item in graph.prerequisites_of("physiology.synapse")}, "pending edges are not walked"
    assert graph.payload("physiology.synapse")["pending"][0]["provenance_label"] == "Ders materyalinde belirtilmiş"
    assert graph.suggest("physiology.synapse", "biology.fluid_mosaic", provenance="imported").edge_id == edge.edge_id, "one suggestion per pair"

    confirmed = graph.confirm(edge.edge_id)
    assert confirmed.status == "reviewed"
    assert "biology.fluid_mosaic" in {item["concept_id"] for item in graph.prerequisites_of("physiology.synapse")}

    # RMP is a prerequisite of AP; making AP a prerequisite of RMP would close a loop.
    loop = graph.suggest(RMP, AP, note="model önerisi")
    with pytest.raises(ValueError, match="döngü"):
        graph.confirm(loop.edge_id)
    assert graph.reject(loop.edge_id).status == "rejected"
    with pytest.raises(ValueError):
        graph.suggest("physiology.synapse", "physiology.synapse")
    with pytest.raises(ValueError):
        graph.suggest("physiology.synapse", "no.such")
    with pytest.raises(ValueError):
        graph.reject(f"seed:{AP}<-{RMP}")

    # A reopened store still knows the confirmed edge and the rejection.
    store.close()
    again = PrerequisiteGraph(default_concept_graph(), MedicalStore(tmp_path / "medical.sqlite3"))
    assert again.edge(edge.edge_id).status == "reviewed" and again.edge(loop.edge_id).status == "rejected"


def test_a_cycle_in_the_data_does_not_hang_the_walk(tmp_path) -> None:
    seeds = tmp_path / "loop.json"
    seeds.write_text(json.dumps({"version": 0, "edges": [{"concept_id": "a", "requires": "b"}, {"concept_id": "b", "requires": "c"}, {"concept_id": "c", "requires": "a"}]}), encoding="utf-8")
    graph = PrerequisiteGraph(default_concept_graph(), seeds=seeds)
    assert [item["concept_id"] for item in graph.prerequisites_of("a", depth=10)] == ["b", "c"]
    assert graph.path_to("c", "a") == ["c", "b", "a"] and graph.path_to("zzz", "a") == []
    assert graph.would_cycle("a", "b") is True


# ---------------------------------------------------------------------------
# diagnosis
# ---------------------------------------------------------------------------


def test_a_diagnosis_asks_short_questions_and_the_answers_decide_the_starting_point(tmp_path) -> None:
    graph, diagnosis, store, learning, understanding, clock = build(tmp_path / "medical.sqlite3")

    record = diagnosis.start(AP, objective={"question_stem": "Aksiyon potansiyelinin yükselen fazı?"}, reason="Üç yanlış cevap")

    assert record["status"] == "open" and record["intro"] == "Aksiyon potansiyeli konusuna dönmeden önce şu temel kavramı kontrol edelim."
    candidates = {item["concept_id"]: item for item in record["candidates"]}
    assert set(candidates) == {RMP, NERNST, PUMP} or set(candidates) == {RMP, NERNST, TRANSPORT} or set(candidates) == {RMP, PUMP, TRANSPORT}
    assert candidates[RMP]["depth"] == 1 and candidates[RMP]["question"]["question_id"] == "q-rmp"
    assert "correct_key" not in candidates[RMP]["question"]
    assert diagnosis.start(AP)["diagnosis_id"] == record["diagnosis_id"], "one open diagnosis per concept"

    diagnosis.answer(record["diagnosis_id"], RMP, "B", confidence="sure")
    deep = NERNST if NERNST in candidates else next(identifier for identifier in candidates if identifier != RMP)
    diagnosis.answer(record["diagnosis_id"], deep, "A", confidence="guess")
    last = next(identifier for identifier in candidates if identifier not in (RMP, deep))
    located = diagnosis.answer(record["diagnosis_id"], last, "B")

    assert located["status"] == "located" and located["located"] == deep
    assert located["path"] == graph.path_to(deep, AP)
    assert located["path"][0] == deep and located["path"][-1] == AP
    assert located["steps"][-1]["objective"] is True and "Orijinal hedefe dön" in located["steps"][-1]["activity"]
    assert all(step["estimate_label"] == "tahmini" for step in located["steps"])
    assert located["verdict"].startswith(f"Başlangıç noktası: {graph.name(deep)}")
    assert learning.summary()["attempts"] == 3
    assert {event["source"] for event in understanding.events()} == {"diagnosis"}
    assert diagnosis.answer(record["diagnosis_id"], RMP, "A")["candidates"][0]["answer"]["answer_key"] in ("B", "A") and located["asked"] == 3

    # Walking the path and returning.
    for step in located["steps"]:
        diagnosis.complete_step(record["diagnosis_id"], step["concept_id"])
    assert diagnosis.get(record["diagnosis_id"])["status"] == "closed"
    store.close()
    _g, again, _s, _l, _u, _c = build(tmp_path / "medical.sqlite3", with_questions=False)
    assert again.get(record["diagnosis_id"])["status"] == "closed" and again.get(record["diagnosis_id"])["located"] == deep


def test_correct_foundations_send_the_student_straight_back_to_the_concept() -> None:
    _graph, diagnosis, _store, _learning, _understanding, _clock = build()
    record = diagnosis.start(AP)
    for item in record["candidates"]:
        record = diagnosis.answer(record["diagnosis_id"], item["concept_id"], "B")
    assert record["status"] == "located" and record["located"] is None and record["path"] == [AP]
    assert record["verdict"].startswith("Ön koşullar sağlam")
    assert [step["concept_id"] for step in record["steps"]] == [AP, AP]


def test_the_student_may_skip_shorten_or_redirect_and_the_bound_holds() -> None:
    graph, diagnosis, _store, _learning, _understanding, _clock = build()
    record = diagnosis.start(AP)
    ids = [item["concept_id"] for item in record["candidates"]]

    skipped = diagnosis.skip(record["diagnosis_id"], ids[0])
    assert skipped["candidates"][0]["skipped"] is True and skipped["status"] == "open"
    diagnosis.answer(record["diagnosis_id"], ids[1], "A")
    located = diagnosis.answer(record["diagnosis_id"], ids[2], "B")
    assert located["status"] == "located" and located["located"] == ids[1]

    if len(located["steps"]) > 2:
        shortened = diagnosis.shorten(record["diagnosis_id"])
        assert shortened["steps"][0]["status"] == "planned" and shortened["steps"][-1]["status"] == "planned"
        assert all(step["status"] == "skipped" for step in shortened["steps"][1:-1])

    redirected = diagnosis.redirect(record["diagnosis_id"], PUMP)
    assert redirected["path"][0] == PUMP and redirected["path"][-1] == AP and "öğrenci seçimi" in redirected["verdict"]
    with pytest.raises(ValueError):
        diagnosis.redirect(record["diagnosis_id"], "no.such")
    finished = diagnosis.finish(record["diagnosis_id"])
    assert finished["status"] == "closed" and finished["objective"]["concept_id"] == AP
    assert MAX_DIAGNOSTIC_QUESTIONS == 3


def test_sparse_data_is_answered_honestly() -> None:
    graph, diagnosis, store, learning, understanding, clock = build(with_questions=False)

    unknown = diagnosis.start("no.such.concept")
    assert unknown["status"] == "no_prerequisites" and "kayıtlı değil" in unknown["limitations"][0] and len(unknown["fallback"]) == 3

    bare = diagnosis.start("histology.he_stain")
    assert bare["status"] == "no_prerequisites" and "kayıtlı ön koşul yok" in bare["limitations"][0]

    # Prerequisites are known but the bank has no keyed question for them.
    no_questions = diagnosis.start(AP)
    assert no_questions["status"] == "located" and no_questions["located"] == RMP
    assert no_questions["path"] == [RMP, AP] and "anahtarlı soru yok" in no_questions["limitations"][0]


def test_struggling_concepts_come_from_findings_and_repeated_errors() -> None:
    _graph, diagnosis, store, learning, understanding, _clock = build()
    store.save_mastery(ConceptMastery(RMP, subject="physiology", attempts=4, correct=1, recent=[False, False, True, False], level="weak"))
    store.save_mastery(ConceptMastery(PUMP, subject="physiology", attempts=1, correct=0, recent=[False], level="unknown"))
    for identifier in ("q-ap", "q-rmp"):
        understanding.record_event(store.get_question(identifier), correct=False, answer_key="A", confidence="sure")
    for item in understanding.findings(concept_id=AP):
        understanding.record_event(store.get_question("q-ap"), correct=False, answer_key="C", reasoning="", confidence="sure", submission_id="again")

    items = {item["concept_id"]: item for item in diagnosis.struggling()}

    assert RMP in items and "denemede" in items[RMP]["reason"]
    assert PUMP not in items, "one wrong click is not a struggle"
    assert all(item["prerequisites"] >= 0 for item in items.values())
