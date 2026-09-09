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
