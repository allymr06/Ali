"""Anatomy Lab: curated cards, schematic maps, quizzes and licensed 3D assets.

The lab promises never to invent geometry: a mesh appears only when a
licensed asset is registered by hand, and everything else on the page is
the curated data drawn as a diagram. These tests hold it to that.
"""

from __future__ import annotations

import inspect
import json

import pytest

from app.medical.anatomy import (
    FACT_ORDER,
    FACT_QUIZ_FIELDS,
    MANIFEST_NAME,
    AnatomyAssetRegistry,
    AnatomyLab,
    parse_obj,
)
from app.medical.catalog import default_curriculum
from app.medical.models import AnatomyStructure, Landmark
from app.medical.terminology import load_anatomy_data
from app.medical.text import normalize, tokens

# A hand-written quad: four vertices, one shared normal, one face to triangulate.
TINY_OBJ = """# tiny quad
v 0 0 0
v 2 0 0
v 2 3 0
v 0 3 1
vn 0 0 1
f 1//1 2//1 3//1 4//1
"""


@pytest.fixture(scope="module")
def lab() -> AnatomyLab:
    """The shipped catalogue with no assets; every test here only reads it."""
    structures, _terms, source_note = load_anatomy_data()
    return AnatomyLab(structures, default_curriculum(), source_note=source_note)


def section_keys(card: dict) -> list[str]:
    return [section["key"] for section in card["sections"]]


def write_manifest(directory, assets: list) -> None:
    (directory / MANIFEST_NAME).write_text(json.dumps({"assets": assets}), encoding="utf-8")


def muscle(structure_id: str, innervation: str) -> AnatomyStructure:
    """A muscle whose only quizzable fact is the nerve that runs it."""
    return AnatomyStructure(
        structure_id=structure_id,
        canonical=f"Musculus {structure_id}",
        kind="muscle",
        region="upper_limb",
        turkish=f"{structure_id} kası",
        english=f"{structure_id} muscle",
        facts={"innervation": innervation},
    )


def states_the_same(left: str, right: str) -> bool:
    """Does one option say everything the other says, word for word?

    Written out here rather than imported from the lab: the property test has
    to be able to catch an option the lab's own filter let through.
    """
    return set(tokens(left)) <= set(tokens(right)) or set(tokens(right)) <= set(tokens(left))


def asked_field(structure: AnatomyStructure, item: dict) -> str:
    """The fact field an item asks about, read back from the stem it printed."""
    if structure.kind not in FACT_QUIZ_FIELDS:
        assert item["stem"] == f"{structure.canonical} hangi eklem tipindedir?"
        return "joint_type"
    _kind, noun, fields = FACT_QUIZ_FIELDS[structure.kind]
    asked = [
        key
        for key, label in fields
        if item["stem"] == f"{structure.canonical} {noun} {label} aşağıdakilerden hangisidir?"
    ]
    assert len(asked) == 1, item["stem"]  # the stem names exactly one fact
    return asked[0]


def expected_item_count(structure: AnatomyStructure, count: int) -> int:
    """Every question the structure's data can carry, capped at ``count``.

    Refusing a distractor must never be paid for with a missing question, so
    the property test states the whole catalogue's expected yield.
    """
    landmark_items = min(count, len(structure.landmarks)) if len(structure.landmarks) >= 3 else 0
    if structure.kind in FACT_QUIZ_FIELDS:
        facts = sum(1 for key, _label in FACT_QUIZ_FIELDS[structure.kind][2] if structure.facts.get(key))
    else:
        facts = 1 if structure.kind == "joint" and structure.facts.get("joint_type") else 0
    return min(count, landmark_items + facts)


# ---------------------------------------------------------------------------
# structure cards
# ---------------------------------------------------------------------------


def test_a_bone_card_keeps_the_documented_section_order_and_its_landmarks(lab: AnatomyLab) -> None:
    card = lab.describe("scapula")
    assert card["canonical"] == "Scapula" and card["kind_label"] == "Kemik"
    assert card["region_label"] == "Üst ekstremite"
    assert card["turkish"] == "Kürek kemiği" and card["english"] == "Shoulder blade"
    assert section_keys(card) == [key for key, _label in FACT_ORDER["bone"]]
    labels = {section["key"]: section["label"] for section in card["sections"]}
    assert labels["location"] == "Konum" and labels["muscle_attachments"] == "Kas tutunmaları"
    assert card["landmark_count"] == len(card["landmarks"]) == 17
    acromion = next(item for item in card["landmarks"] if item["landmark_id"] == "acromion")
    assert acromion["latin"] == "Acromion" and acromion["turkish"] and acromion["note"]
    assert lab.find_landmark(lab.get("scapula"), "scapula.acromion").latin == "Acromion"
    assert lab.find_landmark(lab.get("scapula"), "yok") is None
    assert card["topic_path"].startswith("Anatomi › Hareket sistemi › Üst ekstremite")
    assert "Terminologia Anatomica" in card["source"]
    assert card["movements"] == []  # only joints move
    assert lab.describe("hayalet") is None


def test_every_kind_gets_its_own_section_order(lab: AnatomyLab) -> None:
    muscle = lab.describe("m_biceps_brachii")
    joint = lab.describe("articulatio_cubiti")
    nerve = lab.describe("n_axillaris")
    assert section_keys(muscle) == "group origin insertion innervation arterial_supply action relations high_yield".split()
    assert [section["label"] for section in muscle["sections"]][1:4] == ["Origo", "Insertio", "Innervatio"]
    assert section_keys(joint) == (
        "joint_type articulating_surfaces capsule ligaments movements muscles axes_planes relations high_yield".split()
    )
    assert section_keys(nerve) == ["origin", "course", "motor", "sensory", "high_yield"]
    assert [section["label"] for section in nerve["sections"]] == ["Köken", "Seyir", "Motor", "Duyu", "Yüksek verim"]

    for card in (muscle, joint, nerve):
        documented = [key for key, _label in FACT_ORDER[card["kind"]]]
        assert section_keys(card) == [key for key in documented if key in section_keys(card)]
        assert all(isinstance(section["items"], list) and section["items"] for section in card["sections"])


def test_relations_carry_the_inverse_of_incoming_edges(lab: AnatomyLab) -> None:
    bone = {(item["relation"], item["structure_id"]) for item in lab.describe("scapula")["relations"]}
    assert {("articulates_with", "humerus"), ("part_of", "shoulder_girdle")} <= bone
    assert {("inverse:originates_from", "m_deltoideus"), ("inverse:formed_by", "articulatio_humeri")} <= bone
    muscle = {(item["relation"], item["structure_id"]) for item in lab.describe("m_biceps_brachii")["relations"]}
    nerve = {(item["relation"], item["structure_id"]) for item in lab.describe("n_musculocutaneus")["relations"]}
    assert {("innervated_by", "n_musculocutaneus"), ("inverse:innervates", "n_musculocutaneus")} <= muscle
    assert {("innervates", "m_biceps_brachii"), ("inverse:innervated_by", "m_biceps_brachii")} <= nerve
    known = {structure.structure_id for structure in lab.all()}
    for structure in lab.all():
        for relation in lab.describe(structure.structure_id)["relations"]:
            assert relation["structure_id"] in known
            assert relation["canonical"] and relation["kind"]


def test_the_landmark_map_is_schematic_and_never_geometry(lab: AnatomyLab) -> None:
    card = lab.describe("scapula")
    diagram = card["landmark_map"]
    nodes = {node["id"]: node for node in diagram["nodes"]}
    edges = {(edge["from"], edge["to"], edge["relation"]) for edge in diagram["edges"]}

    assert diagram["schematic"] is True
    assert len(nodes) == len(diagram["nodes"])
    assert nodes["scapula"]["central"] is True
    assert nodes["acromion"]["kind"] == "landmark" and nodes["acromion"]["label"] == "Acromion"
    assert nodes["m_deltoideus"]["kind"] == "muscle" and nodes["articulatio_humeri"]["kind"] == "joint"
    assert len([node for node in diagram["nodes"] if node["kind"] == "landmark"]) == card["landmark_count"]
    assert {("scapula", "acromion", "landmark"), ("m_deltoideus", "scapula", "originates_from")} <= edges
    assert all(edge["from"] in nodes and edge["to"] in nodes for edge in diagram["edges"])
    # A relationship map carries no coordinates: nothing here claims to know
    # where a structure sits in space.
    assert not {key for node in diagram["nodes"] for key in node} & {"x", "y", "z", "position", "vertices", "mesh"}


def test_a_card_never_invents_a_missing_relation_or_topic_path() -> None:
    orphan = AnatomyStructure(
        structure_id="lig_test", canonical="Ligamentum testum", kind="ligament", region="upper_limb",
        turkish="Deneme bağı", english="Test ligament",
        landmarks=[Landmark("tuberculum_testum", "Tuberculum testum", "Deneme çıkıntısı")],
        facts={"location": "Bir yer.", "innervation": "Bir sinir.", "high_yield": ["Bir not."]},
        relations=[{"relation": "attaches_to", "target": "hayalet"}],
        topic_id="anatomy.olmayan.konu",
    )
    small = AnatomyLab([orphan], default_curriculum())
    card = small.describe("lig_test")

    assert card["relations"] == []  # a dangling target is dropped, not guessed
    assert card["topic_path"] == ""  # an unknown topic gets no invented breadcrumb
    assert card["source"] == "" and card["kind_label"] == "Bağ"
    # An unlisted kind falls back to the bone order, so "innervation" has no slot.
    assert section_keys(card) == ["location", "high_yield"]
    assert [node["id"] for node in card["landmark_map"]["nodes"]] == ["lig_test", "tuberculum_testum"]
    assert small.get(None) is None and small.get("") is None
    assert small.quiz("lig_test", count=3) == []  # one landmark and no quizzable facts


# ---------------------------------------------------------------------------
# search and prompt facts
# ---------------------------------------------------------------------------


def test_search_finds_a_structure_by_latin_turkish_and_english_name(lab: AnatomyLab) -> None:
    for query in ("Scapula", "scapula", "Kürek kemiği", "kurek kemigi", "shoulder blade", "omoplat", "acromion"):
        hits = lab.search(query, limit=3)
        assert hits, query
        assert hits[0]["structure_id"] == "scapula", query
    assert lab.search("m. biceps brachii", limit=1)[0]["structure_id"] == "m_biceps_brachii"
    hit = lab.search("biseps", limit=1)[0]
    assert hit["kind_label"] == "Kas" and hit["region_label"] == "Üst ekstremite"
    assert hit["landmark_count"] == 0 and hit["has_model"] is False

    assert lab.search("") == [] and lab.search("zzzzz") == []
    assert len(lab.search("kas", limit=3)) == 3
    assert len(lab.search("scapula", limit=0)) == 1  # a non-positive limit still answers once


def test_facts_for_prompt_is_bounded_and_states_origo_insertio_innervatio(lab: AnatomyLab) -> None:
    full = lab.facts_for_prompt(["m_biceps_brachii"], max_chars=100_000)
    header, *lines = full.splitlines()
    assert header == "Musculus biceps brachii (Kas; TR: Biseps kası (kolun ön iki başlı kası); EN: Biceps brachii muscle)"
    assert any(line.startswith("- Origo: Caput longum") for line in lines)
    assert any(line.startswith("- Insertio: Tuberositas radii") for line in lines)
    assert any(line.startswith("- Innervatio: N. musculocutaneus") for line in lines)
    bounded = lab.facts_for_prompt(["m_biceps_brachii", "scapula"], limit=2, max_chars=800)
    assert len(bounded) <= 800
    assert full.startswith(bounded)  # cut off, never reflowed
    wide = lab.facts_for_prompt(["m_biceps_brachii", "scapula"], limit=2, max_chars=3200)
    blocks = wide.split("\n\n")
    assert len(blocks) == 2 and sum(len(block) for block in blocks) <= 3200
    assert blocks[1].startswith("Scapula (Kemik;")
    bone = lab.facts_for_prompt(["scapula"], max_chars=100_000)
    assert bone.splitlines()[-1].startswith("- Landmarks: Spina scapulae (Scapula dikeni")

    assert lab.facts_for_prompt(["m_biceps_brachii", "scapula"], limit=1, max_chars=3200).count("EN:") == 1
    assert lab.facts_for_prompt(["hayalet"]) == "" and lab.facts_for_prompt([]) == ""


# ---------------------------------------------------------------------------
# movements
# ---------------------------------------------------------------------------


def test_joint_movements_carry_axis_plane_and_the_muscles_that_make_them(lab: AnatomyLab) -> None:
    flexion, rotation = lab.describe("articulatio_cubiti")["movements"]
    assert flexion["pair"] == ["Fleksiyon", "Ekstansiyon"]
    assert flexion["axis"] == "transvers eksen" and flexion["plane"] == "sagittal düzlem"
    assert any(line.startswith("Fleksiyon:") for line in flexion["muscles"])
    assert any("m. triceps brachii" in line for line in flexion["muscles"])
    assert rotation["pair"] == ["Pronasyon", "Supinasyon"]
    assert rotation["axis"] == "longitudinal eksen (radius)" and rotation["plane"] == "transvers düzlem"
    shoulder = lab.movements(lab.get("articulatio_humeri"))
    assert [item["axis"] for item in shoulder[:3]] == ["transvers eksen", "sagittal eksen", "longitudinal eksen"]
    assert [item["plane"] for item in shoulder[:3]] == ["sagittal düzlem", "frontal (koronal) düzlem", "transvers düzlem"]
    circumduction = shoulder[3]
    assert circumduction["text"] == "Sirkumduksiyon"
    assert circumduction["axis"] == "" and circumduction["plane"] == "" and circumduction["muscles"] == []
    assert "pair" not in circumduction  # no axis is guessed for a movement it cannot name
    assert lab.movements(lab.get("scapula")) == []


def test_a_line_that_only_mentions_a_movement_is_not_presented_as_one(lab: AnatomyLab) -> None:
    flexion, rotation, locking = lab.movements(lab.get("articulatio_genus"))
    assert flexion["pair"] == ["Fleksiyon", "Ekstansiyon"] and flexion["axis"] == "transvers eksen"
    assert any(line.startswith("Fleksiyon:") for line in flexion["muscles"])
    assert rotation["text"].startswith("Rotasyon yalnızca diz fleksiyondayken")
    assert locking["text"].startswith("Kilitlenme: tam ekstansiyonda")
    for entry in (rotation, locking):
        # Prose that mentions a movement is not that movement: the knee does not
        # flex around an axis because the line says "ekstansiyonda".
        assert entry["axis"] == "" and entry["plane"] == "" and entry["muscles"] == []
        assert "pair" not in entry

    # A head that drops the word its pair shares still names its movement.
    hip = lab.movements(lab.get("articulatio_coxae"))[2]
    assert hip["text"].startswith("İç") and hip["text"].endswith("dış rotasyon (longitudinal eksen)")
    assert hip["pair"] == ["İç rotasyon", "Dış rotasyon"] and hip["axis"] == "longitudinal eksen"
    # "Dorsifleksiyon" heads its own pair; the flexion hiding inside the word is not it.
    ankle = lab.movements(lab.get("articulatio_talocruralis"))[0]
    assert ankle["pair"] == ["Dorsifleksiyon", "Plantar fleksiyon"]
    assert ankle["axis"] == "transvers eksen" and ankle["plane"] == "sagittal düzlem"


# ---------------------------------------------------------------------------
# quiz
# ---------------------------------------------------------------------------


def test_the_landmark_quiz_is_deterministic_and_has_one_correct_answer(lab: AnatomyLab) -> None:
    latin = {item.landmark_id: item.latin for item in lab.get("scapula").landmarks}
    items = lab.quiz("scapula", count=4, seed="quiz-1")
    assert items == lab.quiz("scapula", count=4, seed="quiz-1")
    assert items != lab.quiz("scapula", count=4, seed="quiz-2")
    assert len(items) == 4 and len({item["landmark_id"] for item in items}) == 4

    for item in items:
        assert item["kind"] == "landmark_identify" and item["structure_id"] == "scapula"
        assert item["highlight"] == item["landmark_id"]
        assert item["stem"].startswith("Scapula üzerinde işaretlenen yapı:")
        assert [option["key"] for option in item["options"]] == ["A", "B", "C", "D", "E"]
        texts = [option["text"] for option in item["options"]]
        assert len(set(texts)) == 5  # the answer is never offered twice
        assert set(texts) <= set(latin.values())  # distractors are real landmarks
        correct = [option for option in item["options"] if option["key"] == item["correct_key"]]
        assert len(correct) == 1 and correct[0]["text"] == latin[item["landmark_id"]]
        assert latin[item["landmark_id"]] in item["explanation"]

    # Three landmarks is the lower bound for the identification format.
    assert [item["kind"] for item in lab.quiz("patella", count=9, seed="q")] == ["landmark_identify"] * 3
    assert [len(item["options"]) for item in lab.quiz("scapula", count=2, seed="q", option_count=2)] == [2, 2]
    assert lab.quiz("scapula", count=0, seed="q") == []


def test_a_structure_without_landmarks_falls_back_to_fact_questions(lab: AnatomyLab) -> None:
    muscle = lab.get("m_biceps_brachii")
    assert muscle.landmarks == []
    items = lab.quiz("m_biceps_brachii", count=4, seed="fact-1")
    assert items == lab.quiz("m_biceps_brachii", count=4, seed="fact-1")
    assert [item["kind"] for item in items] == ["muscle_fact"] * 4

    innervation = items[0]
    assert innervation["stem"] == "Musculus biceps brachii kasının innervasyonu aşağıdakilerden hangisidir?"
    texts = [option["text"] for option in innervation["options"]]
    correct = [option for option in innervation["options"] if option["key"] == innervation["correct_key"]]
    assert len(texts) == 5 and len(correct) == 1
    assert correct[0]["text"] == muscle.facts["innervation"]
    assert texts.count(correct[0]["text"]) == 1  # the answer is never offered twice
    real = {str(item.facts.get("innervation")) for item in lab.all() if item.facts.get("innervation")}
    assert set(texts) <= real  # distractors are other muscles' real innervations
    knee = lab.quiz("articulatio_genus", count=1, seed="fact-2")[0]
    assert knee["kind"] == "joint_type"
    assert knee["stem"] == "Articulatio genus hangi eklem tipindedir?"
    types = {str(item.facts.get("joint_type")) for item in lab.all() if item.facts.get("joint_type")}
    assert {option["text"] for option in knee["options"]} <= types
    picked = [option for option in knee["options"] if option["key"] == knee["correct_key"]]
    assert picked[0]["text"] == lab.get("articulatio_genus").facts["joint_type"]

    # Nothing is fabricated when the data cannot carry a question: a region
    # with no landmarks and no quizzable fact stays silent.
    assert lab.quiz("shoulder_girdle", count=3, seed="fact-3") == []
    assert lab.quiz("hayalet") == []


def test_a_quiz_too_short_to_fill_is_never_topped_up_with_its_own_questions(lab: AnatomyLab) -> None:
    items = lab.quiz("m_biceps_brachii", count=8, seed="top-up")
    stems = [item["stem"] for item in items]
    assert len(set(stems)) == len(stems)  # asked once each, not asked again to fill the gap
    assert len(items) == 4  # four quizzable facts is all the muscle has
    assert items == lab.quiz("m_biceps_brachii", count=8, seed="top-up")
    # The landmark format runs out the same way: a short quiz, not a repeated one.
    landmarks = lab.quiz("scapula", count=25, seed="top-up")
    assert len({item["landmark_id"] for item in landmarks}) == len(landmarks) == 17


def test_two_muscles_that_share_a_nerve_never_offer_the_same_option_twice() -> None:
    shared = AnatomyLab(
        [
            muscle("alpha", "N. alpha (C5)."),
            *(muscle(name, "N. beta (C6).") for name in ("beta_bir", "beta_iki", "beta_uc", "beta_dort")),
            muscle("gamma", "N. gamma (C7)."),
        ],
        default_curriculum(),
    )
    item = shared.quiz("alpha", count=1, seed="dup")[0]
    texts = [option["text"] for option in item["options"]]
    assert len({normalize(text) for text in texts}) == len(texts)  # the rule validate_question enforces
    assert sorted(texts) == ["N. alpha (C5).", "N. beta (C6).", "N. gamma (C7)."]
    assert [option["key"] for option in item["options"]] == ["A", "B", "C"]  # keys stay sequential
    correct = [option for option in item["options"] if option["key"] == item["correct_key"]]
    assert len(correct) == 1 and correct[0]["text"] == "N. alpha (C5)."

    # One distinct distractor is no question: two of the options would read the
    # same, so the question is dropped rather than shown to a student.
    thin = AnatomyLab(
        [muscle("alpha", "N. alpha (C5)."), muscle("beta_bir", "N. beta (C6)."), muscle("beta_iki", "N. beta (C6).")],
        default_curriculum(),
    )
    assert thin.quiz("alpha", count=1, seed="dup") == []


def test_a_peer_that_states_the_answer_in_another_length_is_never_a_distractor() -> None:
    """The shorter form of the answer is still the answer.

    Curated peers state one fact at two lengths — "N. musculocutaneus (C5,
    C6)." next to the same nerve with a branch clause after it. Offering the
    other length marks a student wrong for a correct answer and writes that
    failure into the mastery model, so the peer is dropped instead.
    """
    shared = AnatomyLab(
        [
            muscle("alpha", "N. alpha (C5, C6); lateral parça n. beta'dan dal alır."),
            muscle("kisa", "N. alpha (C5, C6)."),  # the same nerve, said shorter
            muscle("gamma", "N. gamma (C7)."),
            muscle("delta", "N. delta (C8)."),
        ],
        default_curriculum(),
    )
    item = shared.quiz("alpha", count=1, seed="subsume")[0]
    texts = [option["text"] for option in item["options"]]
    assert "N. alpha (C5, C6)." not in texts  # a true answer is not a wrong one
    assert sorted(texts) == [
        "N. alpha (C5, C6); lateral parça n. beta'dan dal alır.",
        "N. delta (C8).",
        "N. gamma (C7).",
    ]
    correct = [option for option in item["options"] if option["key"] == item["correct_key"]]
    assert correct[0]["text"] == shared.get("alpha").facts["innervation"]

    # The mirror is refused too: asked about the short form, the fuller peer
    # differs only by a clause belonging to another muscle, which is a trap
    # rather than a discrimination — and the shipped data loses no question
    # by refusing it.
    mirror = shared.quiz("kisa", count=1, seed="subsume")[0]
    assert sorted(option["text"] for option in mirror["options"]) == [
        "N. alpha (C5, C6).",
        "N. delta (C8).",
        "N. gamma (C7).",
    ]

    # With only one peer left to tell the answer apart, the question is
    # dropped rather than shown — the same rule as identical options.
    thin = AnatomyLab(
        [
            muscle("alpha", "N. alpha (C5, C6); lateral parça n. beta'dan dal alır."),
            muscle("kisa", "N. alpha (C5, C6)."),
            muscle("uzun", "N. alpha (C5, C6); lateral parça n. beta'dan dal alır ve derinde seyreder."),
            muscle("gamma", "N. gamma (C7)."),
        ],
        default_curriculum(),
    )
    assert thin.quiz("alpha", count=1, seed="subsume") == []


def test_a_landmark_is_never_asked_against_a_sibling_that_says_its_name(lab: AnatomyLab) -> None:
    bone = AnatomyStructure(
        structure_id="os_test", canonical="Os testum", kind="bone", region="trunk",
        turkish="Deneme kemiği", english="Test bone",
        landmarks=[
            Landmark("tuberculum_majus", "Tuberculum majus", "Büyük çıkıntı"),
            Landmark("tuberculum_majus_ossis", "Tuberculum majus ossis testi", "Kemiğin büyük çıkıntısı"),
            Landmark("fossa_testi", "Fossa testi", "Deneme çukuru"),
            Landmark("crista_testi", "Crista testi", "Deneme kabartısı"),
        ],
    )
    small = AnatomyLab([bone], default_curriculum())
    items = small.quiz("os_test", count=4, seed="lm")
    assert len(items) == 4  # every landmark is still asked
    for item in items:
        answer = next(option["text"] for option in item["options"] if option["key"] == item["correct_key"])
        others = [option["text"] for option in item["options"] if option["key"] != item["correct_key"]]
        # The twin is a second true answer only when the other twin is keyed;
        # as two distractors under a third landmark both are simply wrong.
        assert not any(states_the_same(text, answer) for text in others)
    keyed_on_twin = next(item for item in items if item["landmark_id"] == "tuberculum_majus")
    assert "Tuberculum majus ossis testi" not in [option["text"] for option in keyed_on_twin["options"]]

    # A landmark left with a single tellable peer is not asked at all.
    crowded = AnatomyLab(
        [
            AnatomyStructure(
                structure_id="os_test", canonical="Os testum", kind="bone", region="trunk",
                turkish="Deneme kemiği", english="Test bone",
                landmarks=[
                    Landmark("tuberculum_majus", "Tuberculum majus", "Büyük çıkıntı"),
                    Landmark("tuberculum_majus_ossis", "Tuberculum majus ossis testi", "Kemiğin büyük çıkıntısı"),
                    Landmark("fossa_testi", "Fossa testi", "Deneme çukuru"),
                ],
            )
        ],
        default_curriculum(),
    )
    thin = crowded.quiz("os_test", count=3, seed="lm")
    assert [item["landmark_id"] for item in thin] == ["fossa_testi"]

    # The rule reads words, not characters: "Condylus medialis" sits inside
    # "Epicondylus medialis" as text but names another landmark of the same
    # femur, so the two are still asked against each other.
    pair = next(
        item
        for index in range(20)
        for item in lab.quiz("femur", count=5, seed=f"aq-{index}")
        if item["landmark_id"] == "condylus_medialis"
        and "Epicondylus medialis" in [option["text"] for option in item["options"]]
    )
    assert next(option["text"] for option in pair["options"] if option["key"] == pair["correct_key"]) == "Condylus medialis"


def test_no_option_but_the_key_is_a_true_answer_for_the_structure_it_asks_about(lab: AnatomyLab) -> None:
    """The whole shipped catalogue, every question it can ask.

    Options are curated data, never generated text, so a distractor can only
    be true for the structure in the stem by saying what the key says. That is
    what this walks: every structure, twelve quiz seeds, every item kind. It
    is the guard on ``app/medical/data/anatomy.json`` — a fact added there
    that restates another structure's fact has to fail here rather than mark a
    student wrong for a correct answer and record it as a failure.
    """
    curated: dict[tuple[str, str], dict[str, str]] = {}
    for structure in lab.all():
        fields = (
            [key for key, _label in FACT_QUIZ_FIELDS[structure.kind][2]]
            if structure.kind in FACT_QUIZ_FIELDS
            else ["joint_type"]
        )
        for field in fields:
            if structure.facts.get(field):
                curated.setdefault((structure.kind, field), {})[structure.structure_id] = str(structure.facts[field])

    # Every complaint is collected and reported together: a data edit that
    # breaks the rule usually breaks it in several places, and the whole list
    # is what tells the editor which fact to reword.
    second_answers: list[str] = []
    invented: list[str] = []
    checked = 0
    kinds_seen: set[str] = set()
    for structure in lab.all():
        latin = {landmark.landmark_id: landmark.latin for landmark in structure.landmarks}
        for seed in tuple(f"aq-{index}" for index in range(12)):
            items = lab.quiz(structure.structure_id, count=5, seed=seed)
            # Refusing an option must not cost a question: the guarantee is
            # not allowed to be bought by asking less.
            assert len(items) == expected_item_count(structure, 5), (structure.structure_id, seed)
            for item in items:
                options = item["options"]
                assert [option["key"] for option in options] == list("ABCDEF"[: len(options)])
                keyed = [option for option in options if option["key"] == item["correct_key"]]
                assert len(keyed) == 1
                answer = keyed[0]["text"]
                others = [option["text"] for option in options if option["key"] != item["correct_key"]]
                assert len(others) >= 2
                kinds_seen.add(item["kind"])
                checked += 1

                if item["kind"] == "landmark_identify":
                    assert answer == latin[item["landmark_id"]]
                    real = set(latin.values()) - {answer}  # real siblings of the same bone
                else:
                    field = asked_field(structure, item)
                    assert answer == str(structure.facts[field])  # the structure's own curated fact
                    real = {
                        text
                        for peer_id, text in curated[(structure.kind, field)].items()
                        if peer_id != structure.structure_id
                    }
                invented.extend(f"{structure.structure_id}/{seed}: {text!r}" for text in others if text not in real)
                second_answers.extend(
                    f"{structure.structure_id}/{seed} {item['stem']} — {text!r} next to the key {answer!r}"
                    for text in others
                    if states_the_same(text, answer)
                )

    assert invented == []  # every option is curated data, nothing is written for the quiz
    assert second_answers == []
    assert kinds_seen == {"landmark_identify", "muscle_fact", "nerve_fact", "joint_type"}
    assert checked > 2000  # the whole catalogue, not a lucky sample


def test_a_nerve_is_quizzed_on_its_origin_course_and_motor_field(lab: AnatomyLab) -> None:
    nerve = lab.get("n_axillaris")
    assert nerve.landmarks == []
    items = lab.quiz("n_axillaris", count=3, seed="nerve-1")
    assert items == lab.quiz("n_axillaris", count=3, seed="nerve-1")
    assert [item["kind"] for item in items] == ["nerve_fact"] * 3
    assert [item["stem"] for item in items] == [
        "Nervus axillaris sinirinin kökeni aşağıdakilerden hangisidir?",
        "Nervus axillaris sinirinin seyri aşağıdakilerden hangisidir?",
        "Nervus axillaris sinirinin motor innervasyonu aşağıdakilerden hangisidir?",
    ]
    for item, key in zip(items, ("origin", "course", "motor"), strict=True):
        texts = [option["text"] for option in item["options"]]
        real = {str(other.facts.get(key)) for other in lab.all() if other.kind == "nerve" and other.facts.get(key)}
        assert item["structure_id"] == "n_axillaris"
        assert len(set(texts)) == len(texts) == 5
        assert set(texts) <= real  # distractors are other nerves' real facts
        correct = [option for option in item["options"] if option["key"] == item["correct_key"]]
        assert len(correct) == 1 and correct[0]["text"] == nerve.facts[key]
        assert str(nerve.facts[key]) in item["explanation"]


# ---------------------------------------------------------------------------
# 3D assets
# ---------------------------------------------------------------------------


def test_without_an_asset_directory_the_lab_offers_no_geometry(lab: AnatomyLab) -> None:
    registry = AnatomyAssetRegistry(None)
    described = registry.describe("scapula")
    assert registry.available_ids() == [] and registry.problems == []
    assert described["available"] is False and described["directory"] is None
    assert "manifest.json" in described["reason"] and "lisanslı" in described["reason"].casefold()
    with pytest.raises(FileNotFoundError):
        registry.load_mesh("scapula")

    card = lab.describe("scapula")
    assert card["has_model"] is False and card["model"]["available"] is False
    assert all(lab.summary(structure)["has_model"] is False for structure in lab.all())
    # The card still teaches without a mesh.
    assert card["landmarks"] and card["sections"] and card["landmark_map"]["nodes"]


def test_manifest_entries_without_a_licence_or_source_are_refused(tmp_path) -> None:
    write_manifest(tmp_path, [
        {"structure_id": "femur", "file": "femur.obj", "source": "https://example.invalid/femur"},
        {"structure_id": "tibia", "file": "tibia.obj", "license": "CC BY 4.0"},
        {"file": "orphan.obj", "license": "CC BY 4.0", "source": "https://example.invalid/orphan"},
        {"structure_id": "scapula", "file": "scapula.obj", "license": "CC BY 4.0", "source": "https://example.invalid/s"},
        "bir kayıt değil",
    ])
    registry = AnatomyAssetRegistry(tmp_path)
    assert registry.entry("femur") is None and registry.entry("tibia") is None
    assert sorted(registry.problems) == [
        "femur: lisans ve kaynak belirtilmeyen model kabul edilmez",
        "structure_id veya file eksik olan bir kayıt atlandı",
        "tibia: lisans ve kaynak belirtilmeyen model kabul edilmez",
    ]

    # Registered, but the file is not there: reported, never replaced.
    missing = registry.describe("scapula")
    assert missing["available"] is False
    assert missing["reason"] == "Manifestte kayıtlı model dosyası bulunamadı."
    assert "path" not in missing  # the local filesystem path stays out of the payload
    assert registry.available_ids() == []
    with pytest.raises(FileNotFoundError):
        registry.load_mesh("scapula")
    (tmp_path / MANIFEST_NAME).write_text("{ bozuk", encoding="utf-8")
    registry.reload()
    assert registry.problems == ["manifest okunamadı: JSONDecodeError"]
    assert registry.entry("scapula") is None


def test_a_licensed_obj_loads_with_positions_triangles_and_bounds(tmp_path) -> None:
    (tmp_path / "patella.obj").write_text(TINY_OBJ, encoding="utf-8")
    write_manifest(tmp_path, [{
        "structure_id": "patella", "file": "patella.obj", "license": "CC BY 4.0",
        "source": "https://example.invalid/patella", "attribution": "Bir bağışçı", "scale": 2.0,
        "landmarks": {"apex_patellae": [0, 1, 2], "bozuk": [1, 2]},
    }])
    structures, _terms, source_note = load_anatomy_data()
    equipped = AnatomyLab(structures, default_curriculum(), assets_directory=tmp_path, source_note=source_note)
    assert equipped.assets.available_ids() == ["patella"] and equipped.assets.problems == []
    mesh = equipped.assets.load_mesh("patella")
    assert mesh["vertex_count"] == 4 and mesh["triangle_count"] == 2
    assert mesh["positions"][:6] == [0.0, 0.0, 0.0, 2.0, 0.0, 0.0]
    assert mesh["indices"] == [0, 1, 2, 0, 2, 3]  # the quad is triangulated
    assert mesh["bounds"] == {"min": [0.0, 0.0, 0.0], "max": [2.0, 3.0, 1.0]}
    assert mesh["normals"] == [0.0, 0.0, 1.0] and mesh["normal_indices"] == [0] * 6
    assert mesh["license"] == "CC BY 4.0" and mesh["source"] == "https://example.invalid/patella"
    assert mesh["attribution"] == "Bir bağışçı" and mesh["scale"] == 2.0
    assert mesh["landmarks"] == {"apex_patellae": [0.0, 1.0, 2.0]}  # the malformed anchor is dropped

    assert equipped.summary(equipped.get("patella"))["has_model"] is True
    assert equipped.describe("patella")["model"]["available"] is True
    assert equipped.describe("scapula")["has_model"] is False


def test_parse_obj_refuses_a_file_without_geometry() -> None:
    for text in ("", "# yalnızca yorum\n", "v 0 0 0\nv 1 0 0\nv 1 1 0\n", "f 1 2 3\n"):
        with pytest.raises(ValueError, match="geometri"):
            parse_obj(text)

    relative = parse_obj("v 0 0 0\nv 1 0 0\nv 1 1 0\nf -3 -2 -1\n")
    assert relative["indices"] == [0, 1, 2]  # negative indices count back from the last vertex
    assert relative["triangle_count"] == 1
    assert relative["normals"] == [] and relative["normal_indices"] == []


def test_the_obj_parser_reads_every_vertex_once_and_keeps_no_dead_line() -> None:
    mesh = parse_obj(TINY_OBJ)
    assert mesh["vertex_count"] == 4 and len(mesh["positions"]) == 12
    # A no-op leaves nothing behind in the output, so the source is the assertion:
    # an extend over an empty range only invites a reader to look for its purpose.
    assert "range(0)" not in inspect.getsource(parse_obj)
