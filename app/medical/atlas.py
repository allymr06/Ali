"""Source-labelled atlas cards supplement, never replace, the curated lessons."""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

CATALOG = Path(__file__).with_name("data") / "atlas_catalog.json"
SOURCE = "https://github.com/Z-Anatomy/Models-of-human-anatomy/tree/b22c56c340eaf72d8031e5c364b6ceb38da44cd6"
AREA_TR = {"hand": "El", "foot": "Ayak", "upper_limb": "Üst ekstremite", "lower_limb": "Alt ekstremite", "head": "Baş ve yüz", "neck": "Boyun", "trunk": "Gövde"}
KIND_TR = {"bone": "Kemikler", "muscle": "Kaslar", "nerve": "Sinirler", "artery": "Arterler", "vein": "Venler"}


@lru_cache(maxsize=1)
def catalog() -> tuple[dict, ...]:
    return tuple(json.loads(CATALOG.read_text(encoding="utf-8"))["structures"])


def scenes() -> list[dict]:
    """Keep large systems in bounded regional sets, instead of loading a body at once."""
    result = []
    for area, area_title in AREA_TR.items():
        for kind, kind_title in KIND_TR.items():
            for side, label in (("right", "sağ"), ("left", "sol"), ("midline", "tek/iki taraflı yapılar")):
                selected = [c for c in catalog() if (c["area"], c["kind"], c["side"]) == (area, kind, side)]
                for offset in range(0, len(selected), 80):
                    chunk = selected[offset:offset + 80]
                    result.append({"scene_id": f"atlas_{area}_{kind}_{side}_{offset // 80}",
                                   "title": f"{area_title} · {kind_title} · {label}" + (f" · {offset // 80 + 1}" if len(selected) > 80 else ""),
                                   "region": chunk[0]["region"], "structure_ids": [c["structure_id"] for c in chunk],
                                   "note": "Z-Anatomy atlası · adlar kaynak terminolojisinden alınmıştır. Ayrıntılar model çözünürlüğüyle sınırlıdır."})
    return result


def structure_card(item: dict) -> dict:
    return {key: item[key] for key in ("structure_id", "canonical", "english", "turkish", "kind", "region")} | {
        "source": "Z-Anatomy — CC BY-SA 4.0; BodyParts3D — CC BY-SA 2.1 Japan; kraniyal sinirler: University of Dundee, CAHID — CC BY 4.0. " + SOURCE,
        "facts": {"high_yield": ["Bu kart kaynak atlasın 3D yapısını tanımlar; ayrıntılı origo, insertio ve klinik bilgi için ders kartlarını kullanın."]},
    }
