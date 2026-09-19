"""Atlas coverage and classification, independent of optional local mesh files."""
from collections import Counter

import pytest

from app.medical.atlas import catalog, scenes
from scripts.build_atlas_catalog import build, classification


def test_requested_regions_and_bilateral_hand_foot_bones_are_present():
    cards = catalog()
    names = {c["object"] for c in cards}
    for side in ("r", "l"):
        for name in ("Scaphoid bone", "Lunate bone", "Triquetrum bone", "Pisiform bone", "Trapezium bone", "Trapezoid bone", "Capitate bone", "Hamate bone",
                     "Talus", "Calcaneus", "Navicular bone", "Cuboid bone", "Medial cuneiform bone", "Intermediate cuneiform bone", "Lateral cuneiform bone",
                     "Sternohyoid muscle", "Platysma", "Superficial part of masseter", "Orbicularis oris muscle", "Gluteus maximus muscle", "Rectus femoris muscle",
                     "Median nerve", "Ulnar nerve", "Femoral nerve", "Internal carotid artery", "Femoral vein"):
            assert name + "." + side in names, name
        for ordinal in ("First", "Second", "Third", "Fourth", "Fifth"):
            for bone in ("metacarpal", "metatarsal"):
                assert f"{ordinal} {bone} bone.{side}" in names
        for extremity in ("hand", "foot"):
            phalanges = [n for n in names if "phalanx" in n and n.endswith(f"of {extremity}.{side}")]
            assert len(phalanges) == 14
            assert not any("Middle phalanx of first" in n for n in phalanges)


def test_every_model_has_exactly_one_bounded_discovery_scene():
    cards = catalog()
    ids = [c["structure_id"] for c in cards]
    assert len(ids) == len(set(ids)) == len({c["object"] for c in cards})
    counts = Counter(i for s in scenes() for i in s["structure_ids"])
    assert counts == Counter(ids)
    assert all(1 <= len(s["structure_ids"]) <= 80 for s in scenes())
    assert {c["kind"] for c in cards} == {"bone", "muscle", "nerve", "artery", "vein"}
    for c in cards:
        assert c["canonical"] and c["turkish"] and c["english"]
        if c["object"].startswith("Right ") or c["object"].endswith(".r"):
            assert c["side"] == "right"
        if c["object"].startswith("Left ") or c["object"].endswith(".l"):
            assert c["side"] == "left"


def test_annotations_teeth_cavities_and_tendons_are_not_mislabelled_as_bones_or_muscles():
    def item(name, groups, faces=40):
        return {"name": name, "type": "MESH", "faces": faces, "collections": groups}
    for specimen in (item("Greater tubercle.j", ["Axial skeleton"], 0),
                     item("Sinus of frontal bone", ["Axial skeleton"]),
                     item("Lower canine.r", ["Axial skeleton"]),
                     item("Calcaneal tendon.r", ["Muscles"]),
                     item("Inguinal ligament.r", ["Muscles"]),
                     item("Flexor retinaculum of wrist.r", ["Muscles"]),
                     item("Soleus muscle.or", ["Muscles"])):
        assert classification(specimen)[0] is None
    assert classification(item("Tensor fasciae latae.r", ["Muscles"]))[0] == "muscle"
    result = build([item("Scaphoid bone.r", ["Appendicular skeleton", "Right hand"])], {"scaphoid bone": "Os scaphoideum"})
    assert result["structures"][0]["canonical"] == "Os scaphoideum · sağ"
    assert result["structures"][0]["area"] == "hand"


def test_inventory_rejects_unpinned_source_before_importing_blender(tmp_path):
    from scripts.inventory_z_anatomy import inventory

    source = tmp_path / "untrusted.blend"
    source.write_bytes(b"not the pinned atlas")
    destination = tmp_path / "inventory.json"
    with pytest.raises(ValueError, match="pinned atlas"):
        inventory(source, destination)
    assert not destination.exists()


def test_a_lesson_is_linked_only_when_the_kind_agrees_too():
    """The atlas name alone is not enough to hand a structure someone's lesson.

    "Anterior tibial artery" matches the curated *muscle* tibialis anterior by
    name; without the kind guard the artery would be shown a muscle's origin,
    insertion and innervation.
    """
    from app.medical.atlas import catalog, curated_links
    from app.medical.terminology import load_anatomy_data

    curated, _terms, _source = load_anatomy_data()
    kinds = {item.structure_id: item.kind for item in curated}
    cards = {c["structure_id"]: c for c in catalog()}
    links = curated_links()

    assert links, "the curriculum and the atlas do describe some of the same structures"
    for atlas_id, lesson_id in links.items():
        assert kinds[lesson_id] == cards[atlas_id]["kind"], (cards[atlas_id]["english"], lesson_id)

    artery = next(c for c in catalog() if c["english"] == "Anterior tibial artery")
    assert kinds[links[artery["structure_id"]]] == "artery"
    assert links[artery["structure_id"]] != "m_tibialis_anterior"


def test_a_linked_structure_teaches_the_lesson_and_still_says_what_it_is(tmp_path):
    """The mesh and the name stay the atlas's; the teaching comes from the
    curriculum, and the card names the lesson it is showing so a group card is
    never mistaken for one written about this single structure."""
    import json

    from app.medical.anatomy import MANIFEST_NAME, AnatomyLab
    from app.medical.atlas import catalog, curated_links
    from app.medical.catalog import Curriculum
    from app.medical.terminology import load_anatomy_data

    structures, _terms, note = load_anatomy_data()
    lessons = {item.structure_id: item for item in structures}
    links = curated_links()
    linked_id, lesson_id = next((a, c) for a, c in links.items() if c == "m_brachialis")
    cards = {c["structure_id"]: c for c in catalog()}
    unlinked_id = next(sid for sid in cards if sid not in links)

    assets = tmp_path / "assets"
    assets.mkdir()
    entries = []
    for structure_id in (linked_id, unlinked_id):
        (assets / f"{structure_id}.obj").write_text("v 0 0 0\nv 1 0 0\nv 0 1 0\nf 1 2 3\n", encoding="utf-8")
        entries.append({"structure_id": structure_id, "file": f"{structure_id}.obj", "license": "CC BY-SA 4.0", "source": "Z-Anatomy"})
    (assets / MANIFEST_NAME).write_text(json.dumps({"assets": entries, "scenes": []}), encoding="utf-8")

    lab = AnatomyLab(structures, Curriculum(), assets_directory=assets, source_note=note)

    linked = lab.describe(linked_id)
    lesson = lessons[lesson_id]
    assert linked["structure_id"] == linked_id and linked["canonical"] == cards[linked_id]["canonical"]
    assert linked["english"] == cards[linked_id]["english"]
    assert {"Origo", "Insertio", "Innervatio"} <= {section["label"] for section in linked["sections"]}
    high_yield = next(section["items"] for section in linked["sections"] if section["key"] == "high_yield")
    assert high_yield[0].startswith(f"Ders kartı: {lesson.canonical}")
    assert linked["topic_path"], "the lesson's topic travels with it"

    # An atlas structure the curriculum does not teach keeps its plain card.
    plain = lab.describe(unlinked_id)
    assert [section["key"] for section in plain["sections"]] == ["high_yield"]
    assert "ders kartlarını kullanın" in plain["sections"][0]["items"][0]

    # The lesson itself is untouched: same id, same name, no atlas line.
    original = lab.describe(lesson_id)
    assert original["canonical"] == lesson.canonical
    first = next((s["items"][0] for s in original["sections"] if s["key"] == "high_yield"), "")
    assert not first.startswith("Ders kartı:")


def test_group_lesson_is_a_reference_not_facts_or_quiz_for_one_muscle(tmp_path):
    import json
    from app.medical.anatomy import AnatomyLab
    from app.medical.catalog import Curriculum
    from app.medical.terminology import load_anatomy_data

    card = next(c for c in catalog() if c["object"] == "Rectus femoris muscle.r")
    sid = card["structure_id"]
    (tmp_path / "rectus.obj").write_text("v 0 0 0\nv 1 0 0\nv 0 1 0\nf 1 2 3\n")
    (tmp_path / "manifest.json").write_text(json.dumps({"assets": [{
        "structure_id": sid, "file": "rectus.obj", "license": "CC BY-SA 4.0",
        "source": "Z-Anatomy"}], "scenes": []}))
    lab = AnatomyLab(load_anatomy_data()[0], Curriculum(), assets_directory=tmp_path)
    model = lab.get(sid)
    assert "origin" not in model.facts, "a whole quadriceps origin is not rectus femoris's origin"
    assert model.english == "Rectus femoris muscle"
    assert not lab.quiz(sid), "group facts must not become single-muscle exam answers"
    assert any(r["target"] == "m_quadriceps_femoris" for r in model.relations)
    assert lab.get("m_quadriceps_femoris").facts["origin"]


def test_ambiguous_aliases_do_not_choose_a_lesson_by_iteration_order():
    from dataclasses import replace
    from app.medical.atlas import match_curated_links
    from app.medical.models import AnatomyStructure

    one = AnatomyStructure("one", "One", "muscle", "upper_limb", "Bir", "First", synonyms=["Shared"])
    two = replace(one, structure_id="two", canonical="Two", english="Second")
    cards = ({"structure_id": "atlas", "canonical": "Shared", "english": "Shared", "kind": "muscle"},)
    assert match_curated_links(cards, [one, two]) == {}
    assert match_curated_links(cards, [two, one]) == {}
    assert match_curated_links(cards, [one, replace(two, kind="artery")]) == {"atlas": "one"}


def test_synonym_group_members_never_inherit_facts_for_the_whole_group():
    from app.medical.atlas import curated_links, same_lesson_subject
    from app.medical.terminology import load_anatomy_data

    lessons = {s.structure_id: s for s in load_anatomy_data()[0]}
    links = curated_links()
    for source in ("Rectus femoris muscle.r", "Adductor brevis.l", "Soleus muscle.r", "Iliacus muscle.l"):
        card = next(c for c in catalog() if c["object"] == source)
        assert not same_lesson_subject(card, lessons[links[card["structure_id"]]])
    card = next(c for c in catalog() if c["object"] == "Brachialis muscle.l")
    assert same_lesson_subject(card, lessons[links[card["structure_id"]]])
