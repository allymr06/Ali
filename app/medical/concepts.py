"""A lightweight medical concept graph.

Concepts come from ``concepts.json`` plus every catalogued anatomical
structure and landmark. Relations are stored in one direction and
answered in both; the graph is meant to grow (documents can register
new concepts at run time) rather than to be complete on day one.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from app.medical.models import AnatomyStructure, Concept
from app.medical.text import content_tokens, fold, stem, tokens

DATA_DIRECTORY = Path(__file__).resolve().parent / "data"
CONCEPTS_FILE = DATA_DIRECTORY / "concepts.json"

RELATION_LABELS_TR: dict[str, str] = {
    "articulates_with": "eklem yapar",
    "part_of": "parçasıdır",
    "contains": "içerir",
    "formed_by": "oluşturan yapı",
    "originates_from": "origo",
    "inserts_on": "insertio",
    "innervated_by": "sinir",
    "innervates": "innerve eder",
    "acts_on": "etki ettiği eklem",
    "contrasts_with": "karıştırılır",
    "depends_on": "dayanır",
    "feeds": "besler",
    "involves": "içerir",
    "generalized_by": "genelleştiren",
    "landmark_of": "işareti",
    "related_to": "ilişkili",
}

_INVERSE: dict[str, str] = {
    "articulates_with": "articulates_with",
    "part_of": "contains",
    "contains": "part_of",
    "formed_by": "forms",
    "originates_from": "origin_of",
    "inserts_on": "insertion_of",
    "innervated_by": "innervates",
    "innervates": "innervated_by",
    "acts_on": "moved_by",
    "contrasts_with": "contrasts_with",
    "depends_on": "supports",
    "feeds": "fed_by",
    "involves": "involved_in",
    "generalized_by": "generalizes",
    "landmark_of": "has_landmark",
}

_INVERSE_LABELS_TR: dict[str, str] = {
    "forms": "oluşturduğu eklem",
    "origin_of": "buradan başlayan kas",
    "insertion_of": "buraya yapışan kas",
    "moved_by": "hareket ettiren kas",
    "supports": "temelini oluşturur",
    "fed_by": "beslendiği yol",
    "involved_in": "yer aldığı süreç",
    "generalizes": "genelleştirir",
    "has_landmark": "işareti",
}


def relation_label(relation: str) -> str:
    return RELATION_LABELS_TR.get(relation) or _INVERSE_LABELS_TR.get(relation) or relation.replace("_", " ")


def load_concepts(path: Path | None = None) -> list[Concept]:
    raw = json.loads((path or CONCEPTS_FILE).read_text(encoding="utf-8"))
    concepts: list[Concept] = []
    for item in raw.get("concepts", []):
        concepts.append(
            Concept(
                concept_id=str(item["concept_id"]),
                subject=str(item.get("subject", "")),
                name=str(item.get("name", item["concept_id"])),
                topic_id=item.get("topic_id"),
                aliases=[str(alias) for alias in item.get("aliases", [])],
                kind=str(item.get("kind", "concept")),
                relations=[dict(relation) for relation in item.get("relations", []) if isinstance(relation, dict)],
            )
        )
    return concepts


def structure_concepts(structures: Iterable[AnatomyStructure]) -> list[Concept]:
    catalogued = list(structures)
    # Relations name a structure, not a concept, so a target must be resolved
    # through the same rule as the node's own id; otherwise an edge into a
    # structure carrying a custom concept id points at nothing.
    concept_ids = {
        structure.structure_id: structure.concept_id or f"anatomy.{structure.structure_id}"
        for structure in catalogued
    }
    concepts: list[Concept] = []
    for structure in catalogued:
        concept_id = structure.concept_id or f"anatomy.{structure.structure_id}"
        relations = [
            {
                "relation": str(item.get("relation", "related_to")),
                "target": concept_ids.get(str(item.get("target")), f"anatomy.{item.get('target')}"),
            }
            for item in structure.relations
            if item.get("target")
        ]
        concepts.append(
            Concept(
                concept_id=concept_id,
                subject="anatomy",
                name=structure.canonical,
                topic_id=structure.topic_id,
                aliases=[alias for alias in [structure.turkish, structure.english, *structure.synonyms, *structure.abbreviations] if alias],
                kind=structure.kind,
                relations=relations,
            )
        )
        for landmark in structure.landmarks:
            concepts.append(
                Concept(
                    concept_id=f"{concept_id}.{landmark.landmark_id}",
                    subject="anatomy",
                    name=landmark.latin,
                    topic_id=structure.topic_id,
                    aliases=[landmark.turkish] if landmark.turkish else [],
                    kind="landmark",
                    relations=[{"relation": "landmark_of", "target": concept_id}],
                )
            )
    return concepts


class ConceptGraph:
    def __init__(self, concepts: Iterable[Concept] = ()) -> None:
        self._concepts: dict[str, Concept] = {}
        self._alias_index: dict[str, set[str]] = {}
        # Aliases indexed by first token, so finding concepts in a
        # sentence does not walk the whole vocabulary.
        self._by_first_token: dict[str, set[str]] = {}
        self._stems: dict[str, set[str]] = {}
        self._inbound: dict[str, list[tuple[str, str]]] = {}
        for concept in concepts:
            self.register(concept)

    def register(self, concept: Concept) -> None:
        self._concepts[concept.concept_id] = concept
        for alias in [concept.name, *concept.aliases]:
            folded = " ".join(tokens(alias))
            if len(folded) >= 2:
                self._alias_index.setdefault(folded, set()).add(concept.concept_id)
                self._by_first_token.setdefault(folded.split(" ", 1)[0], set()).add(folded)
        self._stems[concept.concept_id] = {
            stem(token) for token in content_tokens(" ".join([concept.name, *concept.aliases]))
        }
        for relation in concept.relations:
            target = str(relation.get("target", ""))
            kind = str(relation.get("relation", "related_to"))
            if target:
                self._inbound.setdefault(target, []).append((kind, concept.concept_id))

    def __len__(self) -> int:
        return len(self._concepts)

    def __contains__(self, concept_id: object) -> bool:
        return concept_id in self._concepts

    def get(self, concept_id: str | None) -> Concept | None:
        if not concept_id:
            return None
        return self._concepts.get(str(concept_id))

    def all(self) -> list[Concept]:
        return list(self._concepts.values())

    def by_subject(self, subject: str) -> list[Concept]:
        return [concept for concept in self._concepts.values() if concept.subject == subject]

    def by_topic(self, topic_id: str) -> list[Concept]:
        prefix = topic_id + "."
        return [
            concept
            for concept in self._concepts.values()
            if concept.topic_id and (concept.topic_id == topic_id or concept.topic_id.startswith(prefix))
        ]

    def related(self, concept_id: str) -> list[tuple[str, Concept]]:
        """Outgoing relations followed by the inverse of incoming ones."""
        concept = self.get(concept_id)
        if concept is None:
            return []
        found: list[tuple[str, Concept]] = []
        seen: set[tuple[str, str]] = set()
        for relation in concept.relations:
            target = self.get(relation.get("target"))
            kind = str(relation.get("relation", "related_to"))
            if target is not None and (kind, target.concept_id) not in seen:
                seen.add((kind, target.concept_id))
                found.append((kind, target))
        for kind, source_id in self._inbound.get(concept_id, []):
            source = self.get(source_id)
            inverse = _INVERSE.get(kind, kind)
            if source is not None and (inverse, source_id) not in seen:
                seen.add((inverse, source_id))
                found.append((inverse, source))
        return found

    def find(self, text: str, *, limit: int = 6) -> list[Concept]:
        """Concepts whose name or alias appears in the text (longest first)."""
        folded = fold(text)
        text_tokens = tokens(text)
        folded_tokens = " ".join(text_tokens)
        hits: dict[str, float] = {}
        candidates: set[str] = set()
        for token in text_tokens:
            candidates.update(self._by_first_token.get(token, ()))
        for alias in candidates:
            if len(alias) < 3:
                continue
            if f" {alias} " in f" {folded_tokens} " or (len(alias) >= 6 and alias in folded):
                for concept_id in self._alias_index.get(alias, ()):
                    hits[concept_id] = max(hits.get(concept_id, 0), len(alias))
        if not hits:
            query_stems = {stem(token) for token in content_tokens(text)}
            for concept_id, name_stems in self._stems.items():
                overlap = query_stems & name_stems
                if len(overlap) >= 2 or (overlap and len(name_stems) == 1):
                    hits[concept_id] = len(overlap)
        ordered = sorted(hits.items(), key=lambda item: (-item[1], item[0]))
        return [self._concepts[concept_id] for concept_id, _ in ordered[: max(1, limit)]]

    def to_dict(self, concept: Concept) -> dict[str, Any]:
        return {
            "concept_id": concept.concept_id,
            "subject": concept.subject,
            "name": concept.name,
            "topic_id": concept.topic_id,
            "aliases": list(concept.aliases),
            "kind": concept.kind,
            "relations": [
                {"relation": kind, "label": relation_label(kind), "concept_id": target.concept_id, "name": target.name}
                for kind, target in self.related(concept.concept_id)
            ],
        }


def default_concept_graph(structures: Iterable[AnatomyStructure] = ()) -> ConceptGraph:
    graph = ConceptGraph(load_concepts())
    for concept in structure_concepts(structures):
        graph.register(concept)
    return graph
