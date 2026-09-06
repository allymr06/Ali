"""Anatomy Lab: curated structures, landmark maps, quizzes and 3D assets.

Geometry is never invented. A mesh is shown only when a licensed asset
is registered in the assets manifest next to the academy data; without
one the lab still offers the structure card, the landmark map (data
drawn as a diagram, not a fake bone), relations and the landmark quiz.
"""

from __future__ import annotations

import json
import random
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from app.medical.catalog import Curriculum
from app.medical.models import AnatomyStructure, Landmark
from app.medical.text import fold, normalize, tokens

KIND_LABELS_TR = {
    "bone": "Kemik",
    "joint": "Eklem",
    "muscle": "Kas",
    "ligament": "Bağ",
    "nerve": "Sinir",
    "artery": "Arter",
    "vein": "Ven",
    "region": "Bölge",
}
REGION_LABELS_TR = {
    "upper_limb": "Üst ekstremite",
    "lower_limb": "Alt ekstremite",
    "trunk": "Gövde",
    "head_neck": "Baş ve boyun",
}
KIND_ORDER = ("region", "bone", "joint", "muscle", "artery", "vein", "nerve", "ligament")
FACT_ORDER: dict[str, list[tuple[str, str]]] = {
    "bone": [
        ("location", "Konum"), ("orientation", "Yön"), ("parts", "Bölümler"), ("surfaces", "Yüzler"),
        ("borders", "Kenarlar"), ("angles", "Açılar"), ("articulations", "Eklemler"),
        ("muscle_attachments", "Kas tutunmaları"), ("ligament_attachments", "Bağ tutunmaları"),
        ("high_yield", "Yüksek verim"),
    ],
    "muscle": [
        ("group", "Grup"), ("origin", "Origo"), ("insertion", "Insertio"), ("innervation", "Innervatio"),
        ("arterial_supply", "Arter"), ("action", "Functio"), ("relations", "Komşuluklar"), ("high_yield", "Yüksek verim"),
    ],
    "joint": [
        ("joint_type", "Eklem tipi"), ("articulating_surfaces", "Eklem yüzleri"), ("capsule", "Kapsül"),
        ("ligaments", "Bağlar"), ("movements", "Hareketler"), ("muscles", "Hareketi yapan kaslar"),
        ("axes_planes", "Eksen ve düzlemler"), ("relations", "Komşuluklar"), ("high_yield", "Yüksek verim"),
    ],
    "nerve": [("origin", "Köken"), ("course", "Seyir"), ("motor", "Motor"), ("sensory", "Duyu"), ("high_yield", "Yüksek verim")],
    "artery": [("origin", "Köken"), ("course", "Seyir"), ("branches", "Dallar"), ("supply", "Beslediği alan"), ("relations", "Komşuluklar"), ("high_yield", "Yüksek verim")],
    "vein": [("origin", "Başlangıç"), ("course", "Seyir"), ("tributaries", "Katılan venler"), ("drains_into", "Döküldüğü yer"), ("relations", "Komşuluklar"), ("high_yield", "Yüksek verim")],
    "region": [
        ("location", "Tanım"), ("parts", "Bölümler"), ("sutures", "Sütürler ve kraniyometrik noktalar"),
        ("articulations", "Eklemler"), ("high_yield", "Yüksek verim"), ("study_notes", "Nasıl çalışılır"),
    ],
}
MOVEMENT_PATTERNS = (
    ("Fleksiyon", "Ekstansiyon", "transvers eksen", "sagittal düzlem"),
    ("Abduksiyon", "Adduksiyon", "sagittal eksen", "frontal (koronal) düzlem"),
    ("İç rotasyon", "Dış rotasyon", "longitudinal eksen", "transvers düzlem"),
    ("Pronasyon", "Supinasyon", "longitudinal eksen (radius)", "transvers düzlem"),
    ("Dorsifleksiyon", "Plantar fleksiyon", "transvers eksen", "sagittal düzlem"),
)
FACT_QUIZ_FIELDS: dict[str, tuple[str, str, tuple[tuple[str, str], ...]]] = {
    "muscle": (
        "muscle_fact",
        "kasının",
        (("innervation", "innervasyonu"), ("origin", "origosu"), ("insertion", "insertio'su"), ("action", "işlevi")),
    ),
    "nerve": (
        "nerve_fact",
        "sinirinin",
        (("origin", "kökeni"), ("course", "seyri"), ("motor", "motor innervasyonu")),
    ),
}
MAX_OBJ_BYTES = 40 * 1024 * 1024
MANIFEST_NAME = "manifest.json"


class AnatomyAssetRegistry:
    """Licensed 3D assets registered by hand in ``manifest.json``.

    Each entry: ``structure_id``, ``file`` (OBJ, relative to the assets
    directory), ``license``, ``source``, ``attribution``, optional
    ``scale`` and ``landmarks`` (landmark_id → [x, y, z] anchor). Missing
    files are reported, never replaced by generated geometry.
    """

    def __init__(self, directory: Path | None) -> None:
        self._directory = directory
        self._entries: dict[str, dict[str, Any]] = {}
        self._scenes: list[dict[str, Any]] = []
        self._problems: list[str] = []
        self.reload()

    @property
    def directory(self) -> Path | None:
        return self._directory

    def reload(self) -> None:
        self._entries = {}
        self._scenes = []
        self._problems = []
        if self._directory is None:
            return
        manifest = self._directory / MANIFEST_NAME
        if not manifest.is_file():
            return
        try:
            raw = json.loads(manifest.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            self._problems.append(f"manifest okunamadı: {type(exc).__name__}")
            return
        for item in raw.get("assets", []) if isinstance(raw, dict) else []:
            if not isinstance(item, dict):
                continue
            structure_id = str(item.get("structure_id", "")).strip()
            file_name = str(item.get("file", "")).strip()
            if not structure_id or not file_name:
                self._problems.append("structure_id veya file eksik olan bir kayıt atlandı")
                continue
            if not item.get("license") or not item.get("source"):
                self._problems.append(f"{structure_id}: lisans ve kaynak belirtilmeyen model kabul edilmez")
                continue
            entry = dict(item)
            entry["structure_id"] = structure_id
            entry["path"] = str((self._directory / file_name).resolve())
            entry["available"] = (self._directory / file_name).is_file()
            self._entries[structure_id] = entry
        # A scene names the structures one region shows together; a structure
        # the manifest does not carry is dropped from it rather than drawn empty.
        for item in raw.get("scenes", []) if isinstance(raw, dict) else []:
            if not isinstance(item, dict):
                continue
            scene_id = str(item.get("scene_id", "")).strip()
            wanted = [str(value) for value in (item.get("structure_ids") or []) if str(value) in self._entries]
            if not scene_id or not wanted:
                self._problems.append("scene_id veya kayıtlı yapısı olmayan bir sahne atlandı")
                continue
            # A colour per structure for a scene whose kinds would not tell
            # them apart (a skull is all bone); anything that is not three
            # numbers, or names a structure outside the scene, is dropped.
            palette: dict[str, list[float]] = {}
            raw_palette = item.get("palette")
            for key, value in (raw_palette.items() if isinstance(raw_palette, dict) else []):
                if str(key) not in wanted or not isinstance(value, (list, tuple)) or len(value) != 3:
                    continue
                try:
                    palette[str(key)] = [min(1.0, max(0.0, float(channel))) for channel in value]
                except (TypeError, ValueError):
                    continue
            self._scenes.append(
                {
                    "scene_id": scene_id,
                    "title": str(item.get("title") or scene_id),
                    "region": str(item.get("region") or ""),
                    "structure_ids": wanted,
                    "available": [structure_id for structure_id in wanted if self._entries[structure_id].get("available")],
                    # The card the scene opens on (a region's explanation rather
                    # than one mesh) and the note the layer strip shows.
                    "card": str(item.get("card") or ""),
                    "palette": palette,
                    "note": str(item.get("note") or ""),
                }
            )

    @property
    def problems(self) -> list[str]:
        return list(self._problems)

    def entry(self, structure_id: str) -> dict[str, Any] | None:
        return self._entries.get(structure_id)

    def available_ids(self) -> list[str]:
        return [key for key, entry in self._entries.items() if entry.get("available")]

    def scenes(self) -> list[dict[str, Any]]:
        return [dict(scene) for scene in self._scenes]

    def describe(self, structure_id: str) -> dict[str, Any]:
        entry = self._entries.get(structure_id)
        if entry is None:
            return {
                "available": False,
                "reason": "Bu yapı için kayıtlı 3B model yok. Lisanslı bir OBJ modelini anatomi varlık klasörüne koyup manifest.json'a ekleyince burada görünür.",
                "directory": str(self._directory) if self._directory else None,
            }
        payload = {key: value for key, value in entry.items() if key != "path"}
        if not entry.get("available"):
            payload["reason"] = "Manifestte kayıtlı model dosyası bulunamadı."
        return payload

    def load_mesh(self, structure_id: str) -> dict[str, Any]:
        entry = self._entries.get(structure_id)
        if entry is None or not entry.get("available"):
            raise FileNotFoundError("Bu yapı için kullanılabilir model yok.")
        path = Path(entry["path"])
        if path.stat().st_size > MAX_OBJ_BYTES:
            raise ValueError("Model dosyası çok büyük.")
        mesh = parse_obj(path.read_text(encoding="utf-8", errors="ignore"))
        anchors, meta = self._anchors(entry.get("landmarks"))
        mesh.update(
            {
                "structure_id": structure_id,
                "license": entry.get("license"),
                "source": entry.get("source"),
                "attribution": entry.get("attribution", ""),
                "scale": float(entry.get("scale", 1.0) or 1.0),
                "up_axis": str(entry.get("up_axis") or "y").lower(),
                "landmarks": anchors,
                "landmark_meta": meta,
            }
        )
        return mesh

    @staticmethod
    def _anchors(raw: Any) -> tuple[dict[str, list[float]], dict[str, dict[str, str]]]:
        """Landmark anchors in either manifest form.

        A hand-written entry is ``landmark_id: [x, y, z]``; the importer writes
        ``landmark_id: {"anchor": [x, y, z], "confidence": "approximate",
        "method": ...}`` so the lab can say which pins were derived from the
        shape rather than placed by someone who knew. Anything else is ignored.
        """
        anchors: dict[str, list[float]] = {}
        meta: dict[str, dict[str, str]] = {}
        for key, value in (raw or {}).items() if isinstance(raw, dict) else []:
            point = value.get("anchor") if isinstance(value, dict) else value
            if not isinstance(point, (list, tuple)) or len(point) != 3:
                continue
            try:
                anchors[str(key)] = [float(item) for item in point]
            except (TypeError, ValueError):
                continue
            if isinstance(value, dict):
                meta[str(key)] = {
                    "confidence": str(value.get("confidence") or "confirmed"),
                    "method": str(value.get("method") or ""),
                }
            else:
                meta[str(key)] = {"confidence": "confirmed", "method": "manifest"}
        return anchors, meta


def parse_obj(text: str) -> dict[str, Any]:
    """Minimal Wavefront OBJ parser: positions, normals, triangulated faces."""
    positions: list[float] = []
    normals: list[float] = []
    faces: list[int] = []
    face_normals: list[int] = []
    for line in text.splitlines():
        if not line or line[0] not in "vf":
            continue
        parts = line.split()
        if parts[0] == "v" and len(parts) >= 4:
            positions.extend((float(parts[1]), float(parts[2]), float(parts[3])))
        elif parts[0] == "vn" and len(parts) >= 4:
            normals.extend((float(parts[1]), float(parts[2]), float(parts[3])))
        elif parts[0] == "f" and len(parts) >= 4:
            corners = []
            for token in parts[1:]:
                pieces = token.split("/")
                try:
                    vertex = int(pieces[0])
                except ValueError:
                    continue
                normal = int(pieces[2]) if len(pieces) >= 3 and pieces[2] else 0
                corners.append((vertex, normal))
            for index in range(1, len(corners) - 1):
                for vertex, normal in (corners[0], corners[index], corners[index + 1]):
                    faces.append(vertex - 1 if vertex > 0 else len(positions) // 3 + vertex)
                    face_normals.append(normal - 1 if normal > 0 else -1)
    if not positions or not faces:
        raise ValueError("OBJ dosyasında geometri yok.")
    xs, ys, zs = positions[0::3], positions[1::3], positions[2::3]
    return {
        "vertex_count": len(positions) // 3,
        "triangle_count": len(faces) // 3,
        "positions": positions,
        "normals": normals,
        "indices": faces,
        "normal_indices": face_normals if normals else [],
        "bounds": {"min": [min(xs), min(ys), min(zs)], "max": [max(xs), max(ys), max(zs)]},
    }


def _movement_pattern(text: str) -> tuple[str, str, str, str] | None:
    """The axis/plane pattern a movement line announces, or ``None``.

    The data names the movement at the head of the line ("Fleksiyon –
    ekstansiyon: transvers eksen"), so the pattern word has to head it too: a
    line that only mentions a movement in passing ("Kilitlenme: tam
    ekstansiyonda ...") describes something else and must not be handed an
    axis and a plane it does not have. A head may drop the word the pair
    shares ("İç – dış rotasyon").
    """
    head = tokens(str(text).split(":", 1)[0])
    if not head:
        return None
    for pattern in MOVEMENT_PATTERNS:
        for name in pattern[:2]:
            words = tokens(name)
            if head[: len(words)] == words:
                return pattern
            if len(words) > 1 and head[0] == words[0] and words[-1] in head:
                return pattern
    return None


def _words(text: str) -> set[str]:
    """The distinct words of a statement, folded for comparison."""
    return set(tokens(text))


def _states_the_same(answer_words: set[str], candidate_words: set[str]) -> bool:
    """Does one of the two statements say everything the other says?

    Peer facts are curated, so a distractor is normally a different nerve,
    origin or landmark. But several peers state the *same* fact at different
    lengths — "N. musculocutaneus (C5, C6)." next to "N. musculocutaneus
    (C5, C6); lateral küçük parça n. radialis'ten dal alır." — and the shorter
    one is then a complete, correct answer to the question the longer one
    keys: the student is marked wrong for being right, and record_anatomy_answer
    writes that into the mastery model. The mirror (a peer that only adds a
    clause to the answer) is refused too — the two options then differ by a
    detail belonging to another structure, which is a trap rather than a
    discrimination, and the shipped data loses no question by refusing it.

    Containment is measured over words, not characters: "Condylus medialis"
    and "Epicondylus medialis" are two different landmarks of the same femur
    and have to stay tellable apart.
    """
    if not answer_words or not candidate_words:
        return False
    return answer_words <= candidate_words or candidate_words <= answer_words


def _distinct_distractors(answer: str, candidates: Iterable[str]) -> list[str]:
    """Peer facts that differ from the answer and from one another.

    Structures often share a fact — two muscles run off the same nerve — and
    offering both would put the same option in the list twice, exactly what
    ``validate_question`` refuses to show a student. A peer that only states
    the answer's fact more (or less) fully is refused for a heavier reason: it
    is itself a correct answer to the question being asked.
    """
    seen = {normalize(answer)}
    answer_words = _words(answer)
    distinct: list[str] = []
    for candidate in candidates:
        key = normalize(candidate)
        if not key or key in seen or _states_the_same(answer_words, _words(candidate)):
            continue
        seen.add(key)
        distinct.append(candidate)
    return distinct


class AnatomyLab:
    def __init__(
        self,
        structures: Iterable[AnatomyStructure],
        curriculum: Curriculum,
        *,
        assets_directory: Path | None = None,
        source_note: str = "",
    ) -> None:
        self._structures: dict[str, AnatomyStructure] = {item.structure_id: item for item in structures}
        self._curriculum = curriculum
        self._assets = AnatomyAssetRegistry(assets_directory)
        self._source_note = source_note
        self._inbound: dict[str, list[tuple[str, str]]] = {}
        for structure in self._structures.values():
            for relation in structure.relations:
                target = str(relation.get("target", ""))
                if target:
                    self._inbound.setdefault(target, []).append((str(relation.get("relation", "")), structure.structure_id))

    @property
    def assets(self) -> AnatomyAssetRegistry:
        return self._assets

    def __len__(self) -> int:
        return len(self._structures)

    def get(self, structure_id: str | None) -> AnatomyStructure | None:
        if not structure_id:
            return None
        return self._structures.get(str(structure_id))

    def all(self) -> list[AnatomyStructure]:
        return list(self._structures.values())

    def find_landmark(self, structure: AnatomyStructure, landmark_id: str) -> Landmark | None:
        wanted = landmark_id.split(".")[-1]
        for landmark in structure.landmarks:
            if landmark.landmark_id == wanted:
                return landmark
        return None

    # ------------------------------------------------------------------
    # presentation
    # ------------------------------------------------------------------

    def summary(self, structure: AnatomyStructure) -> dict[str, Any]:
        asset = self._assets.entry(structure.structure_id)
        return {
            "structure_id": structure.structure_id,
            "canonical": structure.canonical,
            "kind": structure.kind,
            "kind_label": KIND_LABELS_TR.get(structure.kind, structure.kind),
            "region": structure.region,
            "region_label": REGION_LABELS_TR.get(structure.region, structure.region),
            "turkish": structure.turkish,
            "english": structure.english,
            "landmark_count": len(structure.landmarks),
            "has_model": bool(asset and asset.get("available")),
            "topic_id": structure.topic_id,
        }

    def hierarchy(self) -> list[dict[str, Any]]:
        regions: dict[str, dict[str, Any]] = {}
        for structure in self._structures.values():
            region = regions.setdefault(
                structure.region,
                {"region": structure.region, "label": REGION_LABELS_TR.get(structure.region, structure.region), "kinds": {}},
            )
            kind = region["kinds"].setdefault(
                structure.kind,
                {"kind": structure.kind, "label": KIND_LABELS_TR.get(structure.kind, structure.kind), "structures": []},
            )
            kind["structures"].append(self.summary(structure))
        ordered = []
        for region_key in ("upper_limb", "lower_limb", "trunk", "head_neck"):
            region = regions.pop(region_key, None)
            if region:
                ordered.append(region)
        ordered.extend(regions.values())
        for region in ordered:
            kinds = region["kinds"]
            region["kinds"] = [kinds[key] for key in KIND_ORDER if key in kinds] + [value for key, value in kinds.items() if key not in KIND_ORDER]
            for kind in region["kinds"]:
                kind["structures"].sort(key=lambda item: item["canonical"])
        return ordered

    def describe(self, structure_id: str) -> dict[str, Any] | None:
        structure = self.get(structure_id)
        if structure is None:
            return None
        sections = []
        for key, label in FACT_ORDER.get(structure.kind, FACT_ORDER["bone"]):
            value = structure.facts.get(key)
            if not value:
                continue
            sections.append({"key": key, "label": label, "items": value if isinstance(value, list) else [value]})
        relations = []
        for relation in structure.relations:
            target = self.get(relation.get("target"))
            if target is not None:
                relations.append({"relation": relation.get("relation"), "structure_id": target.structure_id, "canonical": target.canonical, "kind": target.kind})
        for relation, source_id in self._inbound.get(structure.structure_id, []):
            source = self.get(source_id)
            if source is not None:
                relations.append({"relation": f"inverse:{relation}", "structure_id": source.structure_id, "canonical": source.canonical, "kind": source.kind})
        payload = self.summary(structure)
        payload.update(
            {
                "abbreviations": list(structure.abbreviations),
                "synonyms": list(structure.synonyms),
                "landmarks": [
                    {"landmark_id": landmark.landmark_id, "latin": landmark.latin, "turkish": landmark.turkish, "note": landmark.note}
                    for landmark in structure.landmarks
                ],
                "sections": sections,
                "relations": relations,
                "topic_path": self._curriculum.breadcrumb(structure.topic_id) if structure.topic_id else "",
                "model": self._assets.describe(structure.structure_id),
                "movements": self.movements(structure) if structure.kind == "joint" else [],
                "landmark_map": self.landmark_map(structure),
                "tables": self.tables(structure),
                "source": structure.source or self._source_note,
            }
        )
        return payload

    @staticmethod
    def tables(structure: AnatomyStructure) -> list[dict[str, Any]]:
        """Curated tables (a region's fossae, its foramina and what passes
        through them) in one fixed shape: a title, its columns, and rows of
        exactly that many cells. A table without columns or rows, or a row
        that is not a list, is dropped rather than rendered askew."""
        result: list[dict[str, Any]] = []
        for raw in structure.facts.get("tables") or []:
            if not isinstance(raw, dict):
                continue
            columns = [str(column) for column in raw.get("columns") or [] if str(column).strip()]
            if not columns:
                continue
            rows: list[list[str]] = []
            for row in raw.get("rows") or []:
                if not isinstance(row, (list, tuple)):
                    continue
                cells = [str(cell) for cell in row][: len(columns)]
                cells.extend([""] * (len(columns) - len(cells)))
                rows.append(cells)
            if rows:
                result.append({"title": str(raw.get("title") or ""), "columns": columns, "rows": rows})
        return result

    def movements(self, structure: AnatomyStructure) -> list[dict[str, Any]]:
        raw = structure.facts.get("movements") or []
        muscles = structure.facts.get("muscles") or []
        result = []
        for line in raw if isinstance(raw, list) else [raw]:
            text = str(line)
            entry: dict[str, Any] = {"text": text, "axis": "", "plane": "", "muscles": []}
            pattern = _movement_pattern(text)
            if pattern is not None:
                first, second, axis, plane = pattern
                entry["axis"] = axis
                entry["plane"] = plane
                entry["pair"] = [first, second]
                for muscle_line in muscles if isinstance(muscles, list) else []:
                    muscle_folded = fold(str(muscle_line))
                    if muscle_folded.startswith(fold(first)) or muscle_folded.startswith(fold(second)):
                        entry["muscles"].append(str(muscle_line))
            result.append(entry)
        return result

    def landmark_map(self, structure: AnatomyStructure) -> dict[str, Any]:
        """Data for a schematic diagram: the structure, its landmarks, and
        the muscles/ligaments/nerves attached to it. A relationship map,
        not anatomical geometry."""
        nodes = [{"id": structure.structure_id, "label": structure.canonical, "kind": structure.kind, "central": True}]
        edges = []
        for landmark in structure.landmarks:
            nodes.append({"id": landmark.landmark_id, "label": landmark.latin, "kind": "landmark", "detail": landmark.turkish})
            edges.append({"from": structure.structure_id, "to": landmark.landmark_id, "relation": "landmark"})
        for relation, source_id in self._inbound.get(structure.structure_id, []):
            source = self.get(source_id)
            if source is None or source.kind not in {"muscle", "nerve", "joint", "ligament"}:
                continue
            nodes.append({"id": source.structure_id, "label": source.canonical, "kind": source.kind})
            edges.append({"from": source.structure_id, "to": structure.structure_id, "relation": relation})
        for relation in structure.relations:
            target = self.get(relation.get("target"))
            if target is None:
                continue
            nodes.append({"id": target.structure_id, "label": target.canonical, "kind": target.kind})
            edges.append({"from": structure.structure_id, "to": target.structure_id, "relation": str(relation.get("relation", ""))})
        unique: dict[str, dict[str, Any]] = {}
        for node in nodes:
            unique.setdefault(node["id"], node)
        return {"nodes": list(unique.values()), "edges": edges, "schematic": True}

    def search(self, text: str, *, limit: int = 10) -> list[dict[str, Any]]:
        folded = fold(text)
        query_tokens = set(tokens(text))
        scored: list[tuple[float, AnatomyStructure]] = []
        for structure in self._structures.values():
            haystack = " ".join([structure.canonical, structure.turkish, structure.english, *structure.synonyms, *structure.abbreviations])
            folded_hay = fold(haystack)
            score = 0.0
            if folded and folded in folded_hay:
                score += 3
            overlap = query_tokens & set(tokens(haystack))
            score += len(overlap)
            for landmark in structure.landmarks:
                if folded and folded in fold(landmark.latin):
                    score += 1.5
            if score:
                scored.append((score, structure))
        scored.sort(key=lambda item: (-item[0], item[1].canonical))
        return [self.summary(structure) for _score, structure in scored[: max(1, limit)]]

    # ------------------------------------------------------------------
    # prompt support
    # ------------------------------------------------------------------

    def facts_for_prompt(self, structure_ids: Iterable[str], *, limit: int = 2, max_chars: int = 3200) -> str:
        blocks: list[str] = []
        total = 0
        for structure_id in list(structure_ids)[:limit]:
            structure = self.get(structure_id)
            if structure is None:
                continue
            lines = [f"{structure.canonical} ({KIND_LABELS_TR.get(structure.kind, structure.kind)}; TR: {structure.turkish}; EN: {structure.english})"]
            for key, label in FACT_ORDER.get(structure.kind, FACT_ORDER["bone"]):
                value = structure.facts.get(key)
                if not value:
                    continue
                if isinstance(value, list):
                    lines.append(f"- {label}: " + " | ".join(str(item) for item in value))
                else:
                    lines.append(f"- {label}: {value}")
            if structure.landmarks:
                lines.append("- Landmarks: " + "; ".join(f"{landmark.latin} ({landmark.turkish})" for landmark in structure.landmarks))
            block = "\n".join(lines)
            if total + len(block) > max_chars:
                block = block[: max(0, max_chars - total)]
            if block:
                blocks.append(block)
                total += len(block)
            if total >= max_chars:
                break
        return "\n\n".join(blocks)

    # ------------------------------------------------------------------
    # quiz
    # ------------------------------------------------------------------

    def quiz(self, structure_id: str, *, count: int = 5, seed: str | None = None, option_count: int = 5) -> list[dict[str, Any]]:
        """Deterministic identification questions built from the data.

        Empty when the structure is unknown or its data carries neither three
        landmarks nor a fact the peers can supply distinct distractors for.
        """
        structure = self.get(structure_id)
        if structure is None:
            return []
        rng = random.Random(seed or structure_id)
        items: list[dict[str, Any]] = []
        landmarks = list(structure.landmarks)
        if len(landmarks) >= 3:
            order = list(landmarks)
            rng.shuffle(order)
            names = {landmark.latin: _words(landmark.latin) for landmark in landmarks}
            for landmark in order[: max(1, count)]:
                distractors = [item for item in landmarks if item is not landmark]
                rng.shuffle(distractors)
                # A sibling landmark whose Latin name says the same thing as the
                # answer's would be a second correct option, so it is dropped —
                # and a landmark left without two tellable peers is not asked.
                answer_words = names[landmark.latin]
                distractors = [item for item in distractors if not _states_the_same(answer_words, names[item.latin])]
                if len(distractors) < 2:
                    continue
                choices = [landmark, *distractors[: max(1, option_count - 1)]]
                rng.shuffle(choices)
                keys = "ABCDEF"
                correct_key = keys[choices.index(landmark)]
                items.append(
                    {
                        "kind": "landmark_identify",
                        "structure_id": structure.structure_id,
                        "landmark_id": landmark.landmark_id,
                        "stem": f"{structure.canonical} üzerinde işaretlenen yapı: {landmark.turkish}. Bu yapının Latince adı nedir?",
                        "highlight": landmark.landmark_id,
                        "options": [{"key": keys[index], "text": choice.latin} for index, choice in enumerate(choices)],
                        "correct_key": correct_key,
                        "explanation": f"{landmark.latin} — {landmark.turkish}" + (f" · {landmark.note}" if landmark.note else ""),
                    }
                )
        # The fact questions are drawn once: a second pass over the same facts
        # walks the same fields again and asks what the first pass already asked.
        if len(items) < count:
            items.extend(self._fact_quiz(structure, rng, count - len(items), option_count))
        return items[:count]

    def _fact_quiz(self, structure: AnatomyStructure, rng: random.Random, count: int, option_count: int) -> list[dict[str, Any]]:
        """Innervation/origin/course/insertion/joint-type questions with peers as distractors."""
        items: list[dict[str, Any]] = []
        peers = [item for item in self._structures.values() if item.kind == structure.kind and item.structure_id != structure.structure_id]
        rng.shuffle(peers)
        if structure.kind in FACT_QUIZ_FIELDS:
            item_kind, noun, fields = FACT_QUIZ_FIELDS[structure.kind]
            for key, label in fields:
                value = structure.facts.get(key)
                if not value:
                    continue
                distractors = _distinct_distractors(str(value), (str(peer.facts.get(key)) for peer in peers if peer.facts.get(key)))
                if len(distractors) < 2:
                    continue
                choices = [str(value), *distractors[: option_count - 1]]
                rng.shuffle(choices)
                keys = "ABCDEF"
                items.append(
                    {
                        "kind": item_kind,
                        "structure_id": structure.structure_id,
                        "stem": f"{structure.canonical} {noun} {label} aşağıdakilerden hangisidir?",
                        "options": [{"key": keys[index], "text": choice} for index, choice in enumerate(choices)],
                        "correct_key": keys[choices.index(str(value))],
                        "explanation": f"{structure.canonical}: {label} {value}",
                    }
                )
                if len(items) >= count:
                    break
        elif structure.kind == "joint":
            value = structure.facts.get("joint_type")
            distractors = _distinct_distractors(str(value or ""), (str(peer.facts.get("joint_type")) for peer in peers if peer.facts.get("joint_type")))
            if value and len(distractors) >= 2:
                choices = [str(value), *distractors[: option_count - 1]]
                rng.shuffle(choices)
                keys = "ABCDEF"
                items.append(
                    {
                        "kind": "joint_type",
                        "structure_id": structure.structure_id,
                        "stem": f"{structure.canonical} hangi eklem tipindedir?",
                        "options": [{"key": keys[index], "text": choice} for index, choice in enumerate(choices)],
                        "correct_key": keys[choices.index(str(value))],
                        "explanation": f"{structure.canonical}: {value}",
                    }
                )
        return items[:count]
