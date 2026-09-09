"""Source-labelled atlas cards supplement, never replace, the curated lessons."""
from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path

from app.medical.text import fold

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


PREFIX = re.compile(r"^(musculus|nervus|arteria|vena|os|right|left)\s+", re.I)


def _names(item: dict) -> set[str]:
    latin = item["canonical"].rsplit(" · ", 1)[0]
    english = item["english"]
    return {fold(name) for name in (latin, english, PREFIX.sub("", latin), PREFIX.sub("", english)) if fold(name)}


def same_lesson_subject(item: dict, lesson: object) -> bool:
    """Synonyms can name members of a group, not just equivalent subjects.

    Require a primary-name match before copying teaching facts. A synonym-only
    match remains a useful reference but is not evidence of identical anatomy.
    """
    return item["kind"] == lesson.kind and bool(_names(item) & _names({
        "canonical": lesson.canonical, "english": lesson.english,
    }))


def match_curated_links(cards: tuple[dict, ...], curated: list) -> dict[str, str]:
    """Refuse ambiguous same-kind aliases instead of choosing by set/file order."""
    by_kind: dict[str, dict[str, set[str]]] = {}
    for structure in curated:
        table = by_kind.setdefault(structure.kind, {})
        for name in (structure.canonical, structure.english, *structure.synonyms):
            if name:
                table.setdefault(fold(name), set()).add(structure.structure_id)
    links = {}
    for item in cards:
        table = by_kind.get(item["kind"], {})
        candidates = set().union(*(table.get(name, set()) for name in _names(item)))
        if len(candidates) == 1:
            links[item["structure_id"]] = next(iter(candidates))
    return links


@lru_cache(maxsize=1)
def curated_links() -> dict[str, str]:
    """Atlas structures that are the same anatomy as a curated lesson card.

    The atlas gives geometry and the source name; the curriculum gives the
    teaching. Where both describe the same structure the student should get
    the lesson, so an exact name match — of the Latin, the English, or either
    without its "musculus/nervus/arteria/vena/os/right/left" prefix — links
    them. The kind must agree: without that guard "Anterior tibial artery"
    matches the *muscle* card for tibialis anterior, which would put a
    muscle's origin and insertion on an artery. A near match is no match; an
    unlinked structure keeps its plain source card.
    """
    from app.medical.terminology import load_anatomy_data

    curated, _terms, _source = load_anatomy_data()
    return match_curated_links(catalog(), curated)


def structure_card(item: dict) -> dict:
    return {key: item[key] for key in ("structure_id", "canonical", "english", "turkish", "kind", "region")} | {
        "source": "Z-Anatomy — CC BY-SA 4.0; BodyParts3D — CC BY-SA 2.1 Japan; kraniyal sinirler: University of Dundee, CAHID — CC BY 4.0. " + SOURCE,
        "facts": {"high_yield": ["Bu kart kaynak atlasın 3D yapısını tanımlar; ayrıntılı origo, insertio ve klinik bilgi için ders kartlarını kullanın."]},
    }
