"""Build reproducible discovery metadata from the pinned atlas inventory.

Only meshes/curves in the anatomical system collections are candidates. Source
annotation pointers, muscle attachments and regional surfaces are not organs.
The resulting catalogue is reviewed before the geometry exporter consumes it.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
from pathlib import Path

KINDS = {"bone": "Kemik", "muscle": "Kas", "nerve": "Sinir", "artery": "Arter", "vein": "Ven"}
REGIONS = {"upper_limb": "Üst ekstremite", "lower_limb": "Alt ekstremite", "head_neck": "Baş ve boyun", "trunk": "Gövde"}


def classification(item: dict) -> tuple[str | None, str]:
    name = item["name"]
    if item["type"] not in {"MESH", "CURVE"} or item.get("faces") == 0:
        return None, "annotation_or_no_surface"
    suffix = name.rsplit(".", 1)[-1] if "." in name else ""
    if suffix and suffix not in {"r", "l"}:
        return None, "attachment_or_helper_suffix"
    collections = set(item["collections"])
    if "Systemic arteries" in collections or "Pulmonary arteries" in collections:
        return "artery", "vascular_collection"
    if "Systemic veins" in collections or "Pulmonary veins" in collections:
        return "vein", "vascular_collection"
    if "Nerves" in collections or "Cranial nerves" in collections:
        return "nerve", "nerve_collection"
    if "Muscles" in collections:
        if re.search(r"tendon|aponeurosis|ligament|retinaculum", name, re.I):
            return None, "nonmuscle_connective_structure"
        return "muscle", "muscle_collection"
    if collections & {"Axial skeleton", "Appendicular skeleton"}:
        if re.search(r"cartilage|ligament|membrane|suture|disc|fontanelle|region|bursa|joint|septum|symphysis|sinus|tooth|molar|premolar|incisor|canine", name, re.I):
            return None, "nonbone_skeletal_structure"
        return "bone", "skeleton_collection"
    return None, "outside_requested_systems"


def build(inventory: list[dict], terminology: dict[str, str]) -> dict:
    cards, exclusions = [], []
    for item in sorted(inventory, key=lambda x: x["name"]):
        kind, reason = classification(item)
        if not kind:
            exclusions.append({"name": item["name"], "reason": reason})
            continue
        name = item["name"]
        side = "right" if name.endswith(".r") or name.startswith("Right ") else "left" if name.endswith(".l") or name.startswith("Left ") else "midline"
        base = re.sub(r"\.[rl]$", "", name).strip("()")
        cols = item["collections"]
        if any("hand" in c.lower() for c in cols):
            area, region = "hand", "upper_limb"
        elif any("foot" in c.lower() for c in cols):
            area, region = "foot", "lower_limb"
        elif any("upper limb" in c.lower() for c in cols):
            area, region = "upper_limb", "upper_limb"
        elif any("lower limb" in c.lower() for c in cols):
            area, region = "lower_limb", "lower_limb"
        elif "Head" in cols or "Muscles of head" in cols or "Cranial nerves" in cols:
            area, region = "head", "head_neck"
        elif "Neck" in cols or "Muscles of neck" in cols:
            area, region = "neck", "head_neck"
        else:
            area, region = "trunk", "trunk"
        sid = "za_" + hashlib.sha256(name.encode()).hexdigest()[:16]
        latin = terminology.get(base.casefold(), "")
        side_label = {"right": "sağ", "left": "sol", "midline": "tek/iki taraflı yapı"}[side]
        cards.append({"structure_id": sid, "object": name, "canonical": (latin or base) + " · " + side_label,
                      "english": base, "turkish": KINDS[kind] + " · " + REGIONS[region] + " · " + side_label,
                      "kind": kind, "region": region, "area": area, "side": side,
                      "nomenclature": "TA2" if latin else "source English name"})
    return {"structures": cards, "excluded": exclusions}


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("inventory", type=Path)
    p.add_argument("terminology", type=Path)
    p.add_argument("output", type=Path)
    args = p.parse_args()
    # The source CSV quotes each entire semicolon-separated record.
    rows = csv.reader(args.terminology.read_text(encoding="utf-8-sig").splitlines())
    terms = {}
    for row in rows:
        fields = row[0].split(";") if row else []
        if len(fields) > 2 and fields[0].isdigit():
            terms[fields[1].strip().casefold()] = fields[2].strip()
    result = build(json.loads(args.inventory.read_text(encoding="utf-8")), terms)
    with args.output.open("w", encoding="utf-8") as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2)
    from collections import Counter
    print(dict(Counter(c["kind"] for c in result["structures"])))
    print("No Latin source match:", sum(c["nomenclature"] != "TA2" for c in result["structures"]))
