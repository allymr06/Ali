"""Medical Academy catalogue: the curriculum tree and the concept graph."""

from __future__ import annotations

import json

import pytest

from app.medical.catalog import Curriculum, default_curriculum, valid_subject
from app.medical.concepts import (
    RELATION_LABELS_TR,
    ConceptGraph,
    default_concept_graph,
    load_concepts,
    relation_label,
    structure_concepts,
)
from app.medical.models import SUBJECT_LABELS_TR, AnatomyStructure, Concept, Landmark, Subject

OSSIFICATION = "anatomy.musculoskeletal.skeleton.ossification"
JOINTS = "anatomy.musculoskeletal.joints"
JOINT_TYPES = "anatomy.musculoskeletal.joints.joint_types"
CARTILAGE_QUERY = "hiyalin kıkırdak elastik kıkırdak fibröz kıkırdak perikondriyum"


def _structure(structure_id: str, canonical: str, turkish: str, english: str, **extra) -> AnatomyStructure:
    """A minimal anatomical structure carrying only what a test needs."""
    return AnatomyStructure(
        structure_id=structure_id, canonical=canonical, kind="bone", region="upper_limb",
        turkish=turkish, english=english, topic_id="anatomy.musculoskeletal.skeleton", **extra,
    )


# ---------------------------------------------------------------------------
# curriculum shape
# ---------------------------------------------------------------------------


def test_every_subject_exists_as_a_root_with_a_turkish_label() -> None:
    curriculum = default_curriculum()

    assert curriculum.subject_ids() == [subject.value for subject in Subject]
    for root in curriculum.subjects():
        assert root.parent_id is None
        assert root.subject == root.topic_id
        assert root.title_tr == SUBJECT_LABELS_TR[root.topic_id]
        assert root.title_en and root.keywords
    # The catalogue is a shared singleton: page, tutor and question bank agree.
    assert default_curriculum() is curriculum


def test_dotted_ids_are_unique_and_agree_with_parents_and_order() -> None:
    curriculum = default_curriculum()
    topics = curriculum.all_topics()

    assert len(topics) > 100
    assert len({topic.topic_id for topic in topics}) == len(topics)
    for topic in topics:
        assert curriculum.subject_of(topic.topic_id) == topic.subject
        assert curriculum.is_within(topic.topic_id, topic.subject)
        children = curriculum.children(topic.topic_id)
        # Children keep the order they were written in, without gaps.
        assert [child.order for child in children] == list(range(len(children)))
        if topic.parent_id is None:
            continue
        head, _, tail = topic.topic_id.rpartition(".")
        assert head == topic.parent_id and tail
        parent = curriculum.get(topic.parent_id)
        assert parent is not None and parent.subject == topic.subject
        assert topic in curriculum.children(topic.parent_id)


def test_navigation_walks_the_hierarchy_and_rejects_junk_ids() -> None:
    curriculum = default_curriculum()

    assert [topic.topic_id for topic in curriculum.path(OSSIFICATION)] == [
        "anatomy", "anatomy.musculoskeletal", "anatomy.musculoskeletal.skeleton", OSSIFICATION
    ]
    assert curriculum.breadcrumb(OSSIFICATION) == (
        "Anatomi › Hareket sistemi › İskelet sistemi ve kemikler › Kemikleşme"
    )
    assert curriculum.breadcrumb(None) == ""

    descendants = curriculum.descendants("anatomy")
    assert {topic.topic_id for topic in descendants} == {
        topic.topic_id for topic in curriculum.all_topics() if topic.subject == "anatomy"
    } - {"anatomy"}
    # Breadth first: a level is never emitted before a shallower one.
    depths = [topic.topic_id.count(".") for topic in descendants]
    assert depths == sorted(depths)
    assert curriculum.descendants(OSSIFICATION) == [] and curriculum.children(OSSIFICATION) == []

    assert curriculum.is_within("anatomy", "anatomy")
    assert curriculum.is_within(OSSIFICATION, "anatomy.musculoskeletal")
    # A shared prefix is not an ancestor; only whole segments count.
    assert not curriculum.is_within(OSSIFICATION, "anatomy.musculo")
    assert not curriculum.is_within("anatomy", OSSIFICATION)

    found = curriculum.get("  anatomy  ")
    assert found is not None and found.topic_id == "anatomy"
    assert curriculum.get("") is None and curriculum.get(None) is None
    assert curriculum.get("anatomy.") is None and curriculum.subject_of("anatomy.nope") is None
    assert curriculum.exists("anatomy") and not curriculum.exists("anatomy.nope")
    assert curriculum.path("anatomy.nope") == [] and curriculum.breadcrumb("anatomy.nope") == ""
    assert curriculum.children("anatomy.nope") == [] and curriculum.descendants("anatomy.nope") == []


def test_tree_mirrors_the_flat_catalogue() -> None:
    curriculum = default_curriculum()
    tree = curriculum.tree()
    counted = 0

    assert [node["topic_id"] for node in tree] == curriculum.subject_ids()
    assert set(tree[0]) == {"topic_id", "subject", "title", "title_en", "keywords", "children"}

    def visit(node: dict) -> None:
        nonlocal counted
        counted += 1
        topic = curriculum.get(node["topic_id"])
        assert topic is not None and node["subject"] == topic.subject
        assert node["title"] == topic.title_tr and node["title_en"] == topic.title_en
        assert [child["topic_id"] for child in node["children"]] == [
            child.topic_id for child in curriculum.children(topic.topic_id)]
        for child in node["children"]:
            visit(child)

    for node in tree:
        visit(node)
    assert counted == len(curriculum.all_topics())


# ---------------------------------------------------------------------------
# search and subject resolution
# ---------------------------------------------------------------------------


def test_search_ranks_the_specific_topic_above_its_parent() -> None:
    curriculum = default_curriculum()

    hits = [topic.topic_id for topic in curriculum.search("eklem tipleri")]
    assert hits[0] == JOINT_TYPES
    assert hits.index(JOINT_TYPES) < hits.index(JOINTS) < hits.index("anatomy")
    assert [topic.topic_id for topic in curriculum.search("kemikleşme")][0] == OSSIFICATION
    assert [t.topic_id for t in curriculum.search("glikoliz")] == ["biochemistry.carbohydrate_metabolism"]

    scoped = curriculum.search("kemik", subject="histology")
    assert scoped and all(topic.subject == "histology" for topic in scoped)
    assert "histology.bone_tissue" in {topic.topic_id for topic in scoped}
    assert curriculum.search("kemik", subject="astrology") == []
    assert len(curriculum.search("kemik", limit=2)) == 2
    # Asking for no rows yields none, and a negative limit is not a tail slice.
    assert curriculum.search("kemik", limit=0) == []
    assert curriculum.search("kemik", limit=-2) == []


def test_search_refuses_matches_that_are_only_prefix_stems() -> None:
    curriculum = default_curriculum()

    # "şıklı" and "siklin" share a five-letter stem but nothing real.
    assert curriculum.search("şıklı") == []
    # "kemikçik" stems to "kemik" yet is not the token "kemik".
    assert curriculum.search("kemikçik") == []
    assert curriculum.search("bugün hava nasıl") == []
    assert curriculum.search("") == [] and curriculum.search("   ") == []
    assert curriculum.search("bu ve bir") == []


def test_resolve_subject_prefers_the_named_subject_over_topic_keywords() -> None:
    curriculum = default_curriculum()

    assert curriculum.resolve_subject_detail("anatomi dersini anlat") == ("anatomy", "name")
    assert curriculum.resolve_subject_detail("Biyokimya") == ("biochemistry", "name")
    # Only topic vocabulary appears, so the basis says so.
    assert curriculum.resolve_subject_detail("glikoliz nedir") == ("biochemistry", "keyword")
    assert curriculum.resolve_subject_detail("hiyalin kıkırdak nedir") == ("histology", "keyword")
    # A named subject wins even when another subject's keyword is present.
    assert curriculum.resolve_subject_detail("histoloji dersinde glikoliz") == ("histology", "name")
    assert curriculum.resolve_subject_detail("hava nasıl") == (None, "none")
    assert curriculum.resolve_subject_detail("") == (None, "none")
    assert curriculum.resolve_subject("hava nasil") is None
    assert curriculum.resolve_subject("merhaba nasılsın") is None
    assert curriculum.resolve_subject("anatomi dersini anlat") == "anatomy"


def test_resolve_subject_reads_a_keyword_prefix_as_a_hint_not_as_a_name() -> None:
    curriculum = default_curriculum()

    # "kemik" is the anatomy root's own keyword, written whole: that names it.
    assert curriculum.resolve_subject_detail("kemik nedir") == ("anatomy", "name")
    # "kemikçik" merely starts with it, so the subject follows but the basis
    # must not claim the user chose it: the caller reads "name" as a choice.
    assert curriculum.resolve_subject_detail("kemikçik nedir") == ("anatomy", "keyword")
    assert curriculum.resolve_subject("kemikçik nedir") == "anatomy"
    assert curriculum.resolve_subject_detail("proteinler nedir") == ("biochemistry", "keyword")
    # A prefix hit on an earlier subject no longer masks a subject truly named.
    assert curriculum.resolve_subject_detail("kemikçik histoloji dersinde") == ("histology", "name")


def test_valid_subject_accepts_the_id_or_the_turkish_label() -> None:
    assert valid_subject("anatomy") == "anatomy" and valid_subject("  ANATOMY  ") == "anatomy"
    assert valid_subject("Anatomi") == "anatomy" and valid_subject("biyokimya") == "biochemistry"
    assert valid_subject(Subject.HISTOLOGY) == "histology"
    assert valid_subject("astroloji") is None and valid_subject("anatomiler") is None
    assert valid_subject("") is None and valid_subject(None) is None and valid_subject(7) is None


def test_a_custom_curriculum_file_falls_back_and_rejects_duplicate_ids(tmp_path) -> None:
    topics = [{"id": "general", "title_tr": "Genel", "topics": [{"id": "planes"}]}]
    path = tmp_path / "curriculum.json"
    path.write_text(json.dumps({"subjects": [{"id": "anatomy", "topics": topics}]}), encoding="utf-8")

    curriculum = Curriculum(path)
    root = curriculum.get("anatomy")
    leaf = curriculum.get("anatomy.general.planes")
    assert curriculum.subject_ids() == ["anatomy"]
    # A missing subject title falls back to the known Turkish label.
    assert root is not None and root.title_tr == "Anatomi" and root.title_en == "anatomy"
    assert leaf is not None and leaf.title_tr == "planes" and leaf.subject == "anatomy"
    assert curriculum.breadcrumb(leaf.topic_id) == "Anatomi › Genel › planes"

    duplicate = tmp_path / "duplicate.json"
    twins = {"subjects": [{"id": "biology", "topics": [{"id": "cell"}, {"id": "cell"}]}]}
    duplicate.write_text(json.dumps(twins), encoding="utf-8")
    with pytest.raises(ValueError, match="Duplicate topic id"):
        Curriculum(duplicate)

    empty = tmp_path / "empty.json"
    empty.write_text("{}", encoding="utf-8")
    assert Curriculum(empty).subjects() == [] and Curriculum(empty).search("kemik") == []


# ---------------------------------------------------------------------------
# concept graph
# ---------------------------------------------------------------------------


def test_every_relation_target_in_the_data_resolves_to_a_real_concept() -> None:
    concepts = load_concepts()
    graph = default_concept_graph()
    curriculum = default_curriculum()
    subjects = {subject.value for subject in Subject}

    assert len(concepts) == len(graph) > 0
    assert len({concept.concept_id for concept in concepts}) == len(concepts)
    for concept in concepts:
        assert concept.concept_id in graph
        assert concept.subject in subjects
        assert concept.name.strip() and concept.kind
        assert curriculum.exists(concept.topic_id)
        assert curriculum.subject_of(concept.topic_id) == concept.subject
        for relation in concept.relations:
            target = relation["target"]
            assert relation["relation"] in RELATION_LABELS_TR and target != concept.concept_id
            assert graph.get(target) is not None, f"{concept.concept_id} -> {target}"


def test_graph_indexes_concepts_by_subject_and_by_topic() -> None:
    graph = default_concept_graph()

    histology = graph.by_subject("histology")
    assert histology and all(concept.subject == "histology" for concept in histology)
    assert graph.by_subject("astrology") == []

    cartilage = {concept.concept_id for concept in graph.by_topic("histology.cartilage")}
    assert "histology.hyaline_cartilage" in cartilage
    # A topic query also collects everything filed under its descendants.
    assert cartilage < {concept.concept_id for concept in graph.by_topic("histology")}
    # A truncated topic id is not a prefix match.
    assert graph.by_topic("histology.cartil") == []
    assert graph.get(None) is None and graph.get("") is None
    assert graph.get("histology.nope") is None and "histology.nope" not in graph
    assert len(graph.all()) == len(graph)


def test_related_returns_outgoing_relations_and_named_inverses() -> None:
    graph = default_concept_graph()
    resting = graph.related("physiology.resting_membrane_potential")

    assert graph.related("biochemistry.tca_cycle") == [
        ("feeds", graph.get("biochemistry.oxidative_phosphorylation")),
        ("fed_by", graph.get("biochemistry.glycolysis")),
    ]
    assert ("supports", graph.get("physiology.action_potential")) in resting
    assert ("depends_on", graph.get("biophysics.nernst")) in resting
    assert graph.related("physiology.refractory_period") == [
        ("involved_in", graph.get("physiology.action_potential"))
    ]
    assert ("generalizes", graph.get("biophysics.nernst")) in graph.related("biophysics.goldman")
    # A symmetric relation reads the same from either side.
    assert graph.related("histology.elastic_cartilage") == [
        ("contrasts_with", graph.get("histology.hyaline_cartilage"))
    ]
    assert graph.related("histology.nope") == []


def test_register_adds_a_concept_with_a_labelled_inverse_edge() -> None:
    alfa = Concept(
        concept_id="a", subject="anatomy", name="Alfa yapısı", aliases=["alpha structure"],
        relations=[{"relation": "part_of", "target": "b"}, {"target": "c"}],
    )
    graph = ConceptGraph([alfa, Concept(concept_id="b", subject="anatomy", name="Beta")])
    assert len(graph) == 2
    # A dangling target is simply not answered until it is registered.
    assert graph.related("a") == [("part_of", graph.get("b"))]

    graph.register(Concept(concept_id="c", subject="anatomy", name="Gama"))
    assert len(graph) == 3
    assert graph.related("a")[-1] == ("related_to", graph.get("c"))
    assert graph.related("b") == [("contains", graph.get("a"))]
    # An unnamed relation has no inverse name, so it stays itself.
    assert graph.related("c") == [("related_to", graph.get("a"))]
    assert [concept.concept_id for concept in graph.find("alpha structure nedir")] == ["a"]

    payload = graph.to_dict(graph.get("b"))
    assert payload["concept_id"] == "b" and payload["subject"] == "anatomy"
    assert payload["relations"] == [
        {"relation": "contains", "label": "içerir", "concept_id": "a", "name": "Alfa yapısı"}
    ]
    # Inverse relation names carry their own Turkish labels; the rest degrade
    # to a readable phrase instead of blowing up.
    assert relation_label("part_of") == "parçasıdır" and relation_label("innervated_by") == "sinir"
    assert relation_label("fed_by") == "beslendiği yol" and relation_label("has_landmark") == "işareti"
    assert relation_label("kaynagi_bilinmiyor") == "kaynagi bilinmiyor" and relation_label("") == ""


def test_structure_concepts_derive_anatomy_concepts_and_merge_into_the_graph() -> None:
    scapula = _structure(
        "scapula", "Scapula", "Kürek kemiği", "Shoulder blade",
        abbreviations=["sc"], synonyms=["omoplat"],
        landmarks=[Landmark(landmark_id="acromion", latin="Acromion", turkish="Akromiyon")],
        # The second relation has no target and must be dropped.
        relations=[{"relation": "articulates_with", "target": "humerus"}, {"relation": "part_of", "target": ""}],
    )
    humerus = _structure("humerus", "Humerus", "Kol kemiği", "Humerus")
    clavicula = _structure(
        "clavicula", "Clavicula", "Köprücük kemiği", "Clavicle", concept_id="anatomy.custom.clavicula"
    )

    derived = structure_concepts([scapula, humerus, clavicula])
    bone, landmark = derived[0], derived[1]
    assert [concept.concept_id for concept in derived] == [
        "anatomy.scapula", "anatomy.scapula.acromion", "anatomy.humerus", "anatomy.custom.clavicula"
    ]
    assert bone.subject == "anatomy" and bone.kind == "bone" and bone.name == "Scapula"
    assert bone.aliases == ["Kürek kemiği", "Shoulder blade", "omoplat", "sc"]
    assert bone.relations == [{"relation": "articulates_with", "target": "anatomy.humerus"}]
    assert landmark.kind == "landmark" and landmark.name == "Acromion"
    assert landmark.aliases == ["Akromiyon"] and landmark.topic_id == bone.topic_id
    assert landmark.relations == [{"relation": "landmark_of", "target": "anatomy.scapula"}]

    graph = ConceptGraph(derived)
    assert graph.related("anatomy.scapula") == [
        ("articulates_with", graph.get("anatomy.humerus")),
        ("has_landmark", graph.get("anatomy.scapula.acromion")),
    ]
    assert graph.related("anatomy.humerus") == [("articulates_with", graph.get("anatomy.scapula"))]
    assert [concept.concept_id for concept in graph.find("kürek kemiği nerede")] == ["anatomy.scapula"]

    baseline = len(default_concept_graph())
    merged = default_concept_graph([humerus])
    assert len(merged) == baseline + 1 and merged.by_subject("anatomy") == [merged.get("anatomy.humerus")]
    # Each call builds a fresh graph; the shipped one keeps its own contents.
    assert len(default_concept_graph()) == baseline and "anatomy.humerus" not in default_concept_graph()


def test_structure_relations_follow_the_target_structure_own_concept_id() -> None:
    scapula = _structure(
        "scapula", "Scapula", "Kürek kemiği", "Shoulder blade",
        relations=[{"relation": "articulates_with", "target": "clavicula"}],
    )
    clavicula = _structure(
        "clavicula", "Clavicula", "Köprücük kemiği", "Clavicle",
        concept_id="anatomy.custom.clavicula",
        landmarks=[
            Landmark(landmark_id="extremitas_acromialis", latin="Extremitas acromialis", turkish="Akromiyal uç")
        ],
    )

    derived = structure_concepts([scapula, clavicula])
    graph = ConceptGraph(derived)

    # The edge lands on the custom id instead of dangling at "anatomy.clavicula".
    assert derived[0].relations == [{"relation": "articulates_with", "target": "anatomy.custom.clavicula"}]
    assert graph.related("anatomy.scapula") == [("articulates_with", graph.get("anatomy.custom.clavicula"))]
    assert graph.related("anatomy.custom.clavicula") == [
        ("articulates_with", graph.get("anatomy.scapula")),
        ("has_landmark", graph.get("anatomy.custom.clavicula.extremitas_acromialis")),
    ]

    # A target outside the batch keeps the default id rather than vanishing.
    orphan = _structure(
        "humerus", "Humerus", "Kol kemiği", "Humerus",
        relations=[{"relation": "part_of", "target": "membrum_superius"}],
    )
    assert structure_concepts([orphan])[0].relations == [
        {"relation": "part_of", "target": "anatomy.membrum_superius"}
    ]


def test_find_matches_an_alias_inside_a_sentence() -> None:
    graph = default_concept_graph()

    assert [c.concept_id for c in graph.find("Hocam glikoliz nedir")] == ["biochemistry.glycolysis"]
    assert [c.concept_id for c in graph.find("gram boyama nasıl yapılır")] == ["microbiology.gram_stain"]
    assert {c.concept_id for c in graph.find(CARTILAGE_QUERY)} == {
        "histology.hyaline_cartilage", "histology.elastic_cartilage",
        "histology.fibrocartilage", "histology.perichondrium",
    }
    assert len(graph.find(CARTILAGE_QUERY, limit=2)) == 2
    assert graph.find("bugün hava durumu nasıl") == [] and graph.find("") == []
