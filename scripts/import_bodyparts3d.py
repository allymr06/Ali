"""Import licensed BodyParts3D meshes into the Anatomy Lab's asset directory.

BodyParts3D (Database Center for Life Science, University of Tokyo) ships one
Wavefront OBJ per *element file* (``FJ1234.obj``); an anatomical concept is the
union of one or more element files, so a deltoid is three parts and a biceps is
two heads. This script extracts the element files a structure needs from the
99 %-reduced archive, merges them into one OBJ per structure, and writes the
``manifest.json`` the lab reads — with the licence, the source and the
attribution the database asks for, and a scene that puts the whole region in
one view.

Nothing here invents geometry: every vertex comes from the archive, every
structure lists the FMA concepts and element files it was built from, and a
structure whose files are missing is reported and skipped.

Usage:
    python scripts/import_bodyparts3d.py <isa_BP3D_4.0_obj_99.zip> [--assets DIR] [--scene upper_limb_right]

The archive and its licence: https://dbarchive.biosciencedbc.jp/en/bodyparts3d/
"""

from __future__ import annotations

import argparse
import json
import sys
import zipfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from app.config.paths import default_state_directory  # noqa: E402
from app.medical.anatomy import MAX_OBJ_BYTES, parse_obj  # noqa: E402

ARCHIVE_FOLDER = "isa_BP3D_4.0_obj_99"
SOURCE_URL = "https://dbarchive.biosciencedbc.jp/en/bodyparts3d/"
# The licence page (read 7 September 2026) says CC BY 4.0; the OBJ headers in
# the 4.0 archive still carry the earlier CC BY-SA 2.1 Japan notice. Both are
# recorded so the lab never claims less than the files say.
LICENSE = "CC BY 4.0 (dbarchive licence page); OBJ headers cite CC BY-SA 2.1 Japan"
ATTRIBUTION = "BodyParts3D, © The Database Center for Life Science licensed under CC Attribution 4.0 International"

# structure_id (app/medical/data/anatomy.json) -> the BodyParts3D concepts that
# make it up, as (FMA id, English name, element file ids). Right side only: the
# lab shows one limb, and a mirrored left copy would teach nothing extra.
UPPER_LIMB_RIGHT: dict[str, list[tuple[str, str, list[str]]]] = {
    "scapula": [("FMA13395", "right scapula", ["FJ3384"])],
    "clavicula": [("FMA13322", "right clavicle", ["FJ3362"])],
    "humerus": [("FMA23130", "right humerus", ["FJ3368"])],
    "radius": [("FMA23464", "right radius", ["FJ3349"])],
    "ulna": [("FMA23467", "right ulna", ["FJ3391"])],
    "m_deltoideus": [
        ("FMA34680", "clavicular part of right deltoid", ["FJ1467"]),
        ("FMA34682", "acromial part of right deltoid", ["FJ1468"]),
        ("FMA34684", "spinal part of right deltoid", ["FJ1513"]),
    ],
    "m_supraspinatus": [("FMA32544", "right supraspinatus", ["FJ1506"])],
    "m_infraspinatus": [("FMA32547", "right infraspinatus muscle", ["FJ1500"])],
    "m_teres_minor": [("FMA32553", "right teres minor", ["FJ1508"])],
    "m_teres_major": [("FMA32551", "right teres major", ["FJ1507"])],
    "m_subscapularis": [("FMA13414", "right subscapularis", ["FJ1504"])],
    "m_biceps_brachii": [
        ("FMA37684", "short head of right biceps brachii", ["FJ1478"]),
        ("FMA37686", "long head of right biceps brachii", ["FJ1512"]),
    ],
    "m_coracobrachialis": [("FMA37665", "right coracobrachialis", ["FJ1488"])],
    "m_brachialis": [("FMA37668", "right brachialis", ["FJ1486"])],
    "m_triceps_brachii": [
        ("FMA37699", "long head of right triceps brachii", ["FJ1477"]),
        ("FMA37697", "lateral head of right triceps brachii", ["FJ1479"]),
        ("FMA37695", "medial head of right triceps brachii", ["FJ1480"]),
    ],
    "m_brachioradialis": [("FMA38486", "right brachioradialis", ["FJ1487"])],
    "m_pectoralis_major": [
        ("FMA34690", "clavicular part of right pectoralis major", ["FJ1447"]),
        ("FMA79979", "sternocostal part of right pectoralis major", ["FJ1464"]),
        ("FMA45874", "abdominal part of right pectoralis major", ["FJ1446"]),
    ],
    "m_pectoralis_minor": [("FMA13375", "right pectoralis minor", ["FJ1456"])],
    "m_serratus_anterior": [("FMA13398", "right serratus anterior", ["FJ1459"])],
    "a_axillaris": [("FMA22655", "right axillary artery", ["FJ2268"])],
    "a_brachialis": [("FMA22691", "right brachial artery", ["FJ2271"])],
    "a_radialis": [("FMA22733", "right radial artery", ["FJ2294"])],
    "a_ulnaris": [("FMA22797", "right ulnar artery", ["FJ2310"])],
    "v_cephalica": [("FMA13325", "right cephalic vein", ["FJ2272"])],
    "v_basilica": [("FMA22909", "right basilic vein", ["FJ2270"])],
}

# The neurocranium: the eight bones of the braincase. Paired bones (parietal,
# temporal) are one card and one mesh each, merged from both sides' element
# files, so the card's text is not written twice and a layer chip hides both.
NEUROCRANIUM: dict[str, list[tuple[str, str, list[str]]]] = {
    "os_frontale": [("FMA52734", "frontal bone", ["FJ3200"])],
    "os_parietale": [
        ("FMA52788", "right parietal bone", ["FJ3380"]),
        ("FMA52789", "left parietal bone", ["FJ3274"]),
    ],
    "os_temporale": [
        ("FMA52738", "right temporal bone", ["FJ3386"]),
        ("FMA52739", "left temporal bone", ["FJ3281"]),
    ],
    "os_occipitale": [("FMA52735", "occipital bone", ["FJ3309"])],
    "os_sphenoidale": [("FMA52736", "sphenoid bone", ["FJ3394"])],
    "os_ethmoidale": [("FMA52740", "ethmoid", ["FJ3199"])],
}
# The atlas convention for a skull: one colour per bone, since every mesh is
# bone and the kind colour would paint the whole vault the same ivory.
NEUROCRANIUM_PALETTE: dict[str, list[float]] = {
    "os_frontale": [0.96, 0.78, 0.30],
    "os_parietale": [0.42, 0.74, 0.96],
    "os_temporale": [0.55, 0.86, 0.42],
    "os_occipitale": [0.92, 0.42, 0.38],
    "os_sphenoidale": [0.80, 0.52, 0.92],
    "os_ethmoidale": [0.36, 0.90, 0.84],
}

# Pins derived from the mesh's own geometry. BodyParts3D 4.0 ships no bony
# landmarks as parts, so a pin can only be put where the shape itself says it
# is: the most proximal point of a bone is its head, the most distal medial
# point of the humerus is its medial epicondyle. Each rule is a chain of cuts
# on the body frame (x grows medially on the right side, -y is anterior, z is
# up); the pin is the centroid of what survives. Every pin written this way is
# recorded as "approximate" with its method, the lab draws it with "≈", and a
# landmark that no cut can place (a groove, a crest) gets no pin at all.
#   ("z", "max", 0.06)  keep the top 6 % of the range along z
#   ("x", "min", 0.20)  keep the most lateral 20 % of what is left
#   ("z", "band", 0.40, 0.55)  keep the band 40–55 % up the range along z
LANDMARK_RULES: dict[str, dict[str, list[tuple]]] = {
    "humerus": {
        "caput_humeri": [("z", "max", 0.06), ("x", "max", 0.5)],
        "tuberculum_majus": [("z", "max", 0.08), ("x", "min", 0.2)],
        "tuberculum_minus": [("z", "max", 0.08), ("y", "min", 0.2)],
        "collum_chirurgicum": [("z", "band", 0.82, 0.86)],
        "tuberositas_deltoidea": [("z", "band", 0.45, 0.60), ("x", "min", 0.12)],
        "sulcus_nervi_radialis": [("z", "band", 0.40, 0.55), ("y", "max", 0.12)],
        "epicondylus_medialis": [("z", "min", 0.10), ("x", "max", 0.15)],
        "epicondylus_lateralis": [("z", "min", 0.10), ("x", "min", 0.15)],
        "trochlea_humeri": [("z", "min", 0.03), ("x", "max", 0.5)],
        "capitulum_humeri": [("z", "min", 0.03), ("x", "min", 0.5)],
        "fossa_olecrani": [("z", "min", 0.08), ("y", "max", 0.2)],
    },
    "scapula": {
        "acromion": [("z", "max", 0.12), ("x", "min", 0.15)],
        "processus_coracoideus": [("z", "max", 0.15), ("y", "min", 0.10)],
        "angulus_inferior": [("z", "min", 0.03)],
        "angulus_superior": [("z", "max", 0.06), ("x", "max", 0.15)],
        "cavitas_glenoidalis": [("z", "band", 0.65, 0.90), ("x", "min", 0.08)],
        "spina_scapulae": [("z", "band", 0.55, 0.75), ("y", "max", 0.10)],
        "margo_medialis": [("z", "band", 0.20, 0.70), ("x", "max", 0.05)],
        "margo_lateralis": [("z", "band", 0.20, 0.60), ("x", "min", 0.10)],
        "fossa_supraspinata": [("z", "band", 0.78, 0.95), ("y", "max", 0.25), ("x", "max", 0.5)],
        "fossa_infraspinata": [("z", "band", 0.30, 0.55), ("y", "max", 0.25)],
    },
    "clavicula": {
        "extremitas_sternalis": [("x", "max", 0.08)],
        "extremitas_acromialis": [("x", "min", 0.08)],
        "corpus_claviculae": [("x", "band", 0.40, 0.60)],
    },
    "radius": {
        "caput_radii": [("z", "max", 0.05)],
        "collum_radii": [("z", "band", 0.88, 0.94)],
        "tuberositas_radii": [("z", "band", 0.80, 0.88), ("y", "min", 0.25)],
        "corpus_radii": [("z", "band", 0.40, 0.60)],
        "processus_styloideus_radii": [("z", "min", 0.03), ("x", "min", 0.5)],
    },
    "ulna": {
        "olecranon": [("z", "max", 0.06), ("y", "max", 0.5)],
        "processus_coronoideus": [("z", "band", 0.86, 0.94), ("y", "min", 0.25)],
        "incisura_trochlearis": [("z", "band", 0.88, 0.96), ("y", "min", 0.5)],
        "tuberositas_ulnae": [("z", "band", 0.80, 0.88), ("y", "min", 0.25)],
        "corpus_ulnae": [("z", "band", 0.40, 0.60)],
        "caput_ulnae": [("z", "min", 0.06)],
        "processus_styloideus_ulnae": [("z", "min", 0.03), ("y", "max", 0.25)],
    },
    # Skull bones in the same body frame: z up, -y anterior, and x growing
    # towards the body's left, so "x min" is the right side of a paired mesh.
    # A hole (a foramen, a meatus) has no surface of its own and gets no rule;
    # the pins here sit on rims, tips, plates and the most prominent points.
    "os_frontale": {
        "glabella": [("y", "min", 0.05), ("x", "band", 0.40, 0.60)],
        "squama_frontalis": [("z", "max", 0.30), ("y", "min", 0.35)],
        "tuber_frontale": [("z", "band", 0.55, 0.85), ("y", "min", 0.25), ("x", "min", 0.25)],
        "arcus_superciliaris": [("z", "band", 0.20, 0.35), ("y", "min", 0.12), ("x", "min", 0.45)],
        "margo_supraorbitalis": [("z", "band", 0.15, 0.30), ("y", "min", 0.10), ("x", "band", 0.05, 0.45)],
        "processus_zygomaticus": [("z", "min", 0.25), ("x", "min", 0.06)],
        "pars_orbitalis": [("z", "min", 0.12), ("y", "max", 0.60), ("x", "min", 0.45)],
    },
    "os_parietale": {
        "margo_sagittalis": [("z", "max", 0.04)],
        "tuber_parietale": [("x", "min", 0.05)],
        "angulus_frontalis": [("y", "min", 0.06), ("x", "band", 0.40, 0.60)],
        "angulus_occipitalis": [("y", "max", 0.06), ("x", "band", 0.40, 0.60)],
        "angulus_sphenoidalis": [("z", "min", 0.15), ("y", "min", 0.15), ("x", "min", 0.30)],
        "angulus_mastoideus": [("z", "min", 0.15), ("y", "max", 0.15), ("x", "min", 0.30)],
        "margo_squamosus": [("z", "min", 0.06), ("x", "min", 0.40)],
        "linea_temporalis_superior": [("z", "band", 0.35, 0.55), ("x", "min", 0.15)],
    },
    "os_occipitale": {
        "foramen_magnum": [("z", "min", 0.12)],
        "condylus_occipitalis": [("z", "min", 0.06), ("x", "min", 0.50)],
        "pars_basilaris": [("y", "min", 0.08)],
        "clivus": [("y", "min", 0.15), ("z", "band", 0.15, 0.45)],
        "squama_occipitalis": [("y", "max", 0.30), ("z", "max", 0.30)],
        "protuberantia_occipitalis_externa": [("y", "max", 0.05)],
        "linea_nuchalis_superior": [("y", "max", 0.15), ("z", "band", 0.35, 0.50), ("x", "min", 0.35)],
        "linea_nuchalis_inferior": [("y", "max", 0.20), ("z", "band", 0.15, 0.30), ("x", "min", 0.40)],
    },
    "os_temporale": {
        "pars_squamosa": [("x", "min", 0.50), ("z", "max", 0.15)],
        "processus_zygomaticus": [("x", "min", 0.50), ("y", "min", 0.06)],
        "processus_mastoideus": [("x", "min", 0.50), ("z", "min", 0.10), ("y", "max", 0.30)],
        "processus_styloideus": [("x", "min", 0.50), ("z", "min", 0.05), ("y", "min", 0.50)],
        "porus_acusticus_externus": [("x", "min", 0.06), ("z", "band", 0.35, 0.55)],
        "fossa_mandibularis": [("x", "min", 0.50), ("y", "min", 0.15), ("z", "band", 0.30, 0.50)],
        "pars_petrosa": [("x", "band", 0.30, 0.50), ("z", "band", 0.20, 0.50)],
    },
    "os_sphenoidale": {
        "sella_turcica": [("x", "band", 0.44, 0.56), ("z", "max", 0.12)],
        "dorsum_sellae": [("x", "band", 0.44, 0.56), ("z", "max", 0.10), ("y", "max", 0.50)],
        "corpus": [("x", "band", 0.42, 0.58), ("z", "band", 0.35, 0.65)],
        "ala_minor": [("y", "min", 0.25), ("z", "max", 0.30), ("x", "min", 0.30)],
        "ala_major": [("x", "min", 0.12)],
        "processus_pterygoideus": [("z", "min", 0.15), ("x", "min", 0.50)],
        "hamulus_pterygoideus": [("z", "min", 0.04), ("x", "min", 0.50)],
        "processus_clinoideus_anterior": [("z", "max", 0.12), ("x", "band", 0.30, 0.44)],
    },
    "os_ethmoidale": {
        "crista_galli": [("z", "max", 0.06), ("x", "band", 0.40, 0.60)],
        "lamina_cribrosa": [("z", "band", 0.72, 0.88), ("x", "band", 0.40, 0.60)],
        "lamina_perpendicularis": [("x", "band", 0.46, 0.54), ("z", "min", 0.50)],
        "labyrinthus_ethmoidalis": [("x", "min", 0.35), ("z", "band", 0.20, 0.80)],
        "lamina_orbitalis": [("x", "min", 0.06)],
        "concha_nasalis_media": [("x", "band", 0.25, 0.45), ("z", "min", 0.15)],
    },
}
AXES = {"x": 0, "y": 1, "z": 2}
MIN_PIN_VERTICES = 4


def derive_landmarks(structure_id: str, positions: list[float]) -> dict[str, dict]:
    """Approximate pins for ``structure_id`` from its vertices, or nothing."""
    rules = LANDMARK_RULES.get(structure_id)
    if not rules or len(positions) < 3 * MIN_PIN_VERTICES:
        return {}
    vertices = [positions[index : index + 3] for index in range(0, len(positions) - 2, 3)]
    pins: dict[str, dict] = {}
    for landmark_id, chain in rules.items():
        kept = vertices
        for rule in chain:
            axis = AXES[rule[0]]
            values = [vertex[axis] for vertex in kept]
            low, high = min(values), max(values)
            span = high - low
            if span <= 0:
                break
            if rule[1] == "max":
                cut = high - span * rule[2]
                kept = [vertex for vertex in kept if vertex[axis] >= cut]
            elif rule[1] == "min":
                cut = low + span * rule[2]
                kept = [vertex for vertex in kept if vertex[axis] <= cut]
            else:  # band
                lower, upper = low + span * rule[2], low + span * rule[3]
                kept = [vertex for vertex in kept if lower <= vertex[axis] <= upper]
            if len(kept) < MIN_PIN_VERTICES:
                break
        if len(kept) < MIN_PIN_VERTICES:
            continue
        centroid = [round(sum(vertex[axis] for vertex in kept) / len(kept), 2) for axis in range(3)]
        pins[landmark_id] = {
            "anchor": centroid,
            "confidence": "approximate",
            "method": "geometric extreme of the mesh: " + " → ".join(
                f"{rule[0]} {rule[1]} {rule[2]}" + (f"–{rule[3]}" if rule[1] == "band" else "") for rule in chain
            ),
        }
    return pins


# A scene: which mapping it draws from, which side the meshes are, the card it
# opens on (a region's explanation for the skull, the first bone otherwise),
# an optional colour per structure, and the note the layer strip shows.
SCENES = {
    "upper_limb_right": {
        "title": "Üst ekstremite (sağ) · kemik, kas, arter, ven",
        "region": "upper_limb",
        "mapping": UPPER_LIMB_RIGHT,
        "structure_ids": list(UPPER_LIMB_RIGHT),
        "side": "right",
        "card": "",
        "palette": {},
        "note": "sağ taraf",
    },
    "neurocranium": {
        "title": "Nörokranyum · kafa tabanı ve kubbe",
        "region": "head_neck",
        "mapping": NEUROCRANIUM,
        "structure_ids": list(NEUROCRANIUM),
        "side": "both",
        "card": "neurocranium",
        "palette": NEUROCRANIUM_PALETTE,
        "note": "iki taraf · kemikleri tek tek kapat",
    },
}


def merge_obj(parts: list[str], *, comment: str) -> str:
    """Concatenate OBJ files into one, re-basing every face index.

    Only positions, normals and faces are kept; element files carry no
    texture coordinates. Negative (relative) indices are resolved before
    re-basing so a merged face never points into another part.
    """
    positions: list[str] = []
    normals: list[str] = []
    faces: list[str] = []
    for text in parts:
        position_base = len(positions)
        normal_base = len(normals)
        local_positions = 0
        local_normals = 0
        for line in text.splitlines():
            if line.startswith("v "):
                positions.append(line.strip())
                local_positions += 1
            elif line.startswith("vn "):
                normals.append(line.strip())
                local_normals += 1
            elif line.startswith("f "):
                corners = []
                for token in line.split()[1:]:
                    pieces = token.split("/")
                    vertex = int(pieces[0])
                    vertex = vertex if vertex > 0 else local_positions + vertex + 1
                    normal = int(pieces[2]) if len(pieces) >= 3 and pieces[2] else 0
                    if normal < 0:
                        normal = local_normals + normal + 1
                    corner = str(vertex + position_base)
                    if normal:
                        corner += f"//{normal + normal_base}"
                    corners.append(corner)
                faces.append("f " + " ".join(corners))
    header = [f"# {line}" for line in comment.splitlines()]
    return "\n".join(header + positions + normals + faces) + "\n"


def import_archive(archive: Path, assets: Path, scene_id: str) -> dict:
    scene = SCENES[scene_id]
    mapping = {structure_id: scene["mapping"][structure_id] for structure_id in scene["structure_ids"]}
    assets.mkdir(parents=True, exist_ok=True)
    manifest_path = assets / "manifest.json"
    manifest = {"assets": [], "scenes": []}
    if manifest_path.is_file():
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except ValueError:
            pass
    existing = {item.get("structure_id"): item for item in manifest.get("assets", []) if isinstance(item, dict)}
    report = {"written": [], "skipped": [], "triangles": 0}
    with zipfile.ZipFile(archive) as zipped:
        names = set(zipped.namelist())
        for structure_id, concepts in mapping.items():
            texts: list[str] = []
            missing: list[str] = []
            for _fma, _name, files in concepts:
                for element in files:
                    member = f"{ARCHIVE_FOLDER}/{element}.obj"
                    if member not in names:
                        missing.append(element)
                        continue
                    texts.append(zipped.read(member).decode("utf-8", errors="ignore"))
            if missing or not texts:
                report["skipped"].append((structure_id, missing))
                continue
            comment = (
                f"{structure_id}: merged from BodyParts3D element files "
                + ", ".join(f"{name} ({fma}: {'+'.join(files)})" for fma, name, files in concepts)
                + f"\n{ATTRIBUTION}\n{SOURCE_URL}"
            )
            merged = merge_obj(texts, comment=comment)
            mesh = parse_obj(merged)  # the same parser the lab uses; a bad merge fails here, not on screen
            encoded = merged.encode("utf-8")
            if len(encoded) > MAX_OBJ_BYTES:
                report["skipped"].append((structure_id, ["too large"]))
                continue
            file_name = f"{structure_id}.obj"
            (assets / file_name).write_bytes(encoded)
            existing[structure_id] = {
                "structure_id": structure_id,
                "file": file_name,
                "license": LICENSE,
                "source": SOURCE_URL,
                "attribution": ATTRIBUTION,
                "side": scene.get("side", "right"),
                # BodyParts3D's body frame is z-up (the limb's long axis is z); the
                # viewer is y-up and turns the mesh, its bounds and its anchors alike.
                "up_axis": "z",
                "provenance": {
                    "dataset": "BodyParts3D 4.0, polygon reduction 99% (IS-A tree)",
                    "concepts": [{"fma": fma, "name": name, "element_files": files} for fma, name, files in concepts],
                },
                # A pin the student confirmed or placed by hand outranks a derived
                # one; derived pins are only written where nothing is recorded yet.
                "landmarks": {
                    **derive_landmarks(structure_id, mesh["positions"]),
                    **{
                        key: value
                        for key, value in (existing.get(structure_id, {}).get("landmarks") or {}).items()
                        if not (isinstance(value, dict) and value.get("confidence") == "approximate")
                    },
                },
            }
            report["written"].append((structure_id, mesh["triangle_count"]))
            report.setdefault("pins", {})[structure_id] = len(existing[structure_id]["landmarks"])
            report["triangles"] += mesh["triangle_count"]
    manifest["assets"] = list(existing.values())
    scenes = {item.get("scene_id"): item for item in manifest.get("scenes", []) if isinstance(item, dict)}
    scenes[scene_id] = {
        "scene_id": scene_id,
        "title": scene["title"],
        "region": scene["region"],
        "structure_ids": [structure_id for structure_id in scene["structure_ids"] if structure_id in existing],
        "card": scene.get("card", ""),
        "palette": {structure_id: colour for structure_id, colour in scene.get("palette", {}).items() if structure_id in existing},
        "note": scene.get("note", ""),
    }
    manifest["scenes"] = list(scenes.values())
    manifest["note"] = (
        "Every mesh here was imported from BodyParts3D by scripts/import_bodyparts3d.py; "
        "the lab shows nothing that is not in this manifest."
    )
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("archive", type=Path, help="isa_BP3D_4.0_obj_99.zip")
    parser.add_argument("--assets", type=Path, default=default_state_directory() / "medical" / "anatomy_assets")
    parser.add_argument("--scene", default="upper_limb_right", choices=sorted(SCENES))
    args = parser.parse_args(argv)
    if not args.archive.is_file():
        print(f"archive not found: {args.archive}", file=sys.stderr)
        return 2
    report = import_archive(args.archive, args.assets, args.scene)
    for structure_id, triangles in report["written"]:
        pins = report.get("pins", {}).get(structure_id, 0)
        print(f"  {structure_id:22s} {triangles:7d} triangles" + (f", {pins} approximate pins" if pins else ""))
    for structure_id, missing in report["skipped"]:
        print(f"  {structure_id:22s} SKIPPED: {', '.join(missing)}")
    print(f"{len(report['written'])} structures, {report['triangles']} triangles -> {args.assets}")
    return 0 if report["written"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
