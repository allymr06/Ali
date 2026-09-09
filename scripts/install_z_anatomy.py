"""Validate and atomically activate a locally exported, pinned anatomy pack.

New immutable directory per import; existing meshes are never overwritten.
An exact manifest snapshot is saved before activation for rollback. Stop JARVIS
before running this developer command; Blender is NOT a runtime dependency.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.medical.anatomy import MAX_OBJ_BYTES, parse_obj  # noqa: E402
from app.medical.terminology import load_anatomy_data  # noqa: E402
from scripts.z_anatomy_source import FULL_ATTRIBUTION, OBJECTS, REVISION, SCENES, SOURCE  # noqa: E402


def specification(full: bool = False) -> tuple[dict, list, dict]:
    objects, scenes, sides = dict(OBJECTS), list(SCENES), {sid: "right" for sid in OBJECTS}
    if full:
        from app.medical.atlas import catalog, scenes as atlas_scenes
        objects.update({c["structure_id"]: [c["object"]] for c in catalog()})
        sides.update({c["structure_id"]: c["side"] for c in catalog()})
        scenes.extend(atlas_scenes())
    return objects, scenes, sides


def curated_landmarks() -> dict[str, set[str]]:
    """Which landmarks the curriculum data defines, per structure.

    A pin the lab cannot name is a pin nobody can learn from: the pack is
    refused rather than installed with an anchor that floats unlabelled.
    """
    structures, _terms, _source = load_anatomy_data()
    return {item.structure_id: {mark.landmark_id for mark in item.landmarks} for item in structures}


def validate_pack(directory: Path) -> tuple[dict, dict[str, bytes]]:
    pack = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
    entries = pack["assets"]
    objects, scenes, sides = specification(pack.get("full_atlas") is True)
    if len(entries) != len(objects) or {e["structure_id"] for e in entries} != set(objects):
        raise ValueError("Incomplete or duplicate atlas allowlist.")
    if pack["scenes"] != scenes:
        raise ValueError("Unexpected scene definition.")
    curated = curated_landmarks()
    if pack.get("full_atlas") is True:
        curated.update({sid: set() for sid in objects if sid.startswith("za_")})
    files = {}
    for entry in entries:
        sid = entry["structure_id"]
        if sid not in curated:
            raise ValueError(f"Unknown structure for this curriculum: {sid}")
        if entry["file"] != f"{sid}.obj":
            raise ValueError("Unexpected asset path.")
        path = directory / entry["file"]
        if path.is_symlink() or not 0 < path.stat().st_size <= MAX_OBJ_BYTES:
            raise ValueError("Invalid asset file.")
        data = path.read_bytes()
        if hashlib.sha256(data).hexdigest() != entry["sha256"]:
            raise ValueError("Mesh checksum mismatch.")
        if entry["source"] != SOURCE or entry["provenance"]["revision"] != REVISION or entry["provenance"]["objects"] != objects[sid]:
            raise ValueError("Source provenance mismatch.")
        if not entry.get("license") or not entry.get("attribution") or entry.get("side") != sides[sid] or entry.get("up_axis") != "z":
            raise ValueError("Missing licensing or coordinate frame.")
        mesh = parse_obj(data.decode("utf-8"))
        if not all(math.isfinite(v) for v in mesh["positions"] + mesh["normals"]):
            raise ValueError("Nonfinite geometry.")
        if any(i < 0 or i >= mesh["vertex_count"] for i in mesh["indices"]):
            raise ValueError("Invalid mesh index.")
        if any(i < -1 or i >= len(mesh["normals"]) // 3 for i in mesh["normal_indices"]):
            raise ValueError("Invalid normal index.")
        if mesh["triangle_count"] != entry["provenance"]["triangles"]:
            raise ValueError("Geometry count mismatch.")
        for landmark_id, pin in entry.get("landmarks", {}).items():
            if landmark_id not in curated[sid]:
                raise ValueError(f"Landmark the curriculum data does not define: {sid}.{landmark_id}")
            point = pin["anchor"]
            if len(point) != 3 or any(not math.isfinite(v) or v < mesh["bounds"]["min"][i] - 1e-6 or v > mesh["bounds"]["max"][i] + 1e-6 for i, v in enumerate(point)):
                raise ValueError("Landmark outside its source bone.")
        files[entry["file"]] = data
        if pack.get("full_atlas") is True:
            entry["attribution"] = FULL_ATTRIBUTION
    return pack, files


def install(directory: Path, assets: Path) -> Path | None:
    pack, files = validate_pack(directory)
    objects, scenes, _sides = specification(pack.get("full_atlas") is True)
    assets.mkdir(parents=True, exist_ok=True)
    if assets.is_symlink() or assets.is_junction():
        raise ValueError("Asset destination cannot be a link.")
    manifest = assets / "manifest.json"
    if manifest.is_symlink():
        raise ValueError("Manifest cannot be a link.")
    original = manifest.read_bytes() if manifest.exists() else None
    previous = json.loads(original) if original else {"assets": [], "scenes": []}
    token = uuid.uuid4().hex
    version_name = f"z-anatomy-{token}"
    version = assets / version_name
    version.mkdir()
    for name, data in files.items():
        with (version / name).open("xb") as stream:
            stream.write(data)
    for entry in pack["assets"]:
        entry["file"] = f"{version_name}/{entry['file']}"
    merged = dict(previous)
    merged["assets"] = [e for e in previous.get("assets", []) if e["structure_id"] not in objects] + pack["assets"]
    replaced_scenes = {s["scene_id"] for s in scenes}
    merged["scenes"] = [s for s in previous.get("scenes", []) if s["scene_id"] not in replaced_scenes] + scenes
    # Do not silently mix frames in a user's custom scene.
    for scene in merged["scenes"]:
        ids = set(scene["structure_ids"])
        if ids & set(objects) and not ids <= set(objects):
            raise ValueError(f"Custom scene mixes source frames: {scene['scene_id']}")
    snapshot = assets / f"manifest-before-z-anatomy-{token}.json" if original else None
    if snapshot:
        with snapshot.open("xb") as stream:
            stream.write(original)
    pending = version / "manifest.pending.json"
    with pending.open("x", encoding="utf-8") as stream:
        json.dump(merged, stream, ensure_ascii=False, indent=2)
        stream.flush()
        os.fsync(stream.fileno())
    if (manifest.read_bytes() if manifest.exists() else None) != original:
        raise RuntimeError("Manifest changed during import; activation cancelled.")
    os.replace(pending, manifest)
    return snapshot


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("export", type=Path)
    parser.add_argument("--assets", type=Path, required=True)
    args = parser.parse_args()
    print(f"Activated atlas. Previous manifest snapshot: {install(args.export, args.assets)}")
