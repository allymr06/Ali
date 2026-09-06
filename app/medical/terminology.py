"""Anatomical terminology engine.

Official Latin terms stay Latin; Turkish and English names, abbreviations
and common misspellings are aliases that resolve to the same entry. The
index finds terms inside free text (Turkish suffixes included, so
``scapulayı`` still resolves to *Scapula*), explains them, and expands
search queries with their synonyms.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.medical.models import AnatomyStructure, Concept, structure_from_dict
from app.medical.text import fold, tokens

DATA_DIRECTORY = Path(__file__).resolve().parent / "data"
ANATOMY_FILE = DATA_DIRECTORY / "anatomy.json"

# Turkish case and possessive suffix chains that may follow a term in
# running text. Folded (dotless i → i) because matching happens on
# folded text. Deliberately a closed list: an open ``[a-z]*`` would turn
# "spina" into a match for "spinal".
TURKISH_SUFFIXES: frozenset[str] = frozenset(
    {
        "i", "u", "a", "e", "yi", "yu", "ya", "ye", "ni", "nu", "na", "ne",
        "nin", "nun", "in", "un", "da", "de", "ta", "te", "dan", "den", "tan",
        "ten", "nda", "nde", "ndan", "nden", "la", "le", "yla", "yle", "ile",
        "si", "su", "sini", "sunu", "sina", "suna", "sinin", "sunun", "siyla",
        "suyla", "lar", "ler", "lari", "leri", "larin", "lerin", "lara", "lere",
        "larda", "lerde", "lardan", "lerden", "larini", "lerini", "lariyla",
        "leriyle", "daki", "deki", "ndaki", "ndeki", "taki", "teki", "ca", "ce",
        "ki", "mi", "mu", "yla", "m", "im", "um", "n", "sin", "sun", "yim",
        "yum", "dir", "dur", "tir", "tur",
    }
)

# Abbreviations that are also ordinary Turkish or English words; they
# must never turn a plain sentence into a medical term match.
AMBIGUOUS_ALIASES: frozenset[str] = frozenset(
    {"alt", "ast", "ust", "art", "kas", "kan", "ter", "hem", "tan", "ten", "gen", "dis", "ic", "on"}
)

_KIND_LABELS_TR = {
    "bone": "Kemik",
    "joint": "Eklem",
    "muscle": "Kas",
    "ligament": "Bağ",
    "nerve": "Sinir",
    "artery": "Arter",
    "vein": "Ven",
    "region": "Bölge",
    "landmark": "Kemik işareti",
    "term": "Terim",
}

_CATEGORY_LABELS_TR = {
    "bone_term": "Kemik terimi",
    "joint_term": "Eklem terimi",
    "muscle_term": "Kas terimi",
    "plane": "Düzlem",
    "axis": "Eksen",
    "movement": "Hareket terimi",
    "position": "Konum terimi",
    "joint_type": "Eklem tipi",
    "general": "Genel terim",
}


@dataclass(slots=True)
class TermEntry:
    term_id: str
    canonical: str
    kind: str
    turkish: str = ""
    english: str = ""
    aliases: list[str] = field(default_factory=list)
    category: str = ""
    note: str = ""
    structure_id: str | None = None
    landmark_of: str | None = None
    concept_id: str | None = None

    @property
    def kind_label(self) -> str:
        if self.kind == "term":
            return _CATEGORY_LABELS_TR.get(self.category, _KIND_LABELS_TR["term"])
        return _KIND_LABELS_TR.get(self.kind, self.kind)

    def to_dict(self) -> dict[str, Any]:
        return {
            "term_id": self.term_id,
            "canonical": self.canonical,
            "kind": self.kind,
            "kind_label": self.kind_label,
            "turkish": self.turkish,
            "english": self.english,
            "aliases": list(self.aliases),
            "category": self.category,
            "note": self.note,
            "structure_id": self.structure_id,
            "landmark_of": self.landmark_of,
            "concept_id": self.concept_id,
        }


@dataclass(frozen=True, slots=True)
class TermMatch:
    entry: TermEntry
    alias: str
    start: int
    end: int


def latin_variants(canonical: str) -> list[str]:
    """Genitive/plural forms that appear in compound terms ("humeri")."""
    head = canonical.strip().split()[0] if canonical.strip() else ""
    folded = fold(head)
    variants: list[str] = []
    if folded.endswith("a") and len(folded) > 3:
        variants.append(folded + "e")            # scapula → scapulae
    if folded.endswith("us") and len(folded) > 4:
        variants.append(folded[:-2] + "i")       # humerus → humeri
    if folded.endswith("um") and len(folded) > 4:
        variants.append(folded[:-2] + "i")       # ligamentum → ligamenti
    if folded == "femur":
        variants.append("femoris")
    if folded == "os":
        variants.append("ossis")
    if folded == "pubis":
        variants.append("pubis")
    return variants


def load_anatomy_data(path: Path | None = None) -> tuple[list[AnatomyStructure], list[dict[str, Any]], str]:
    raw = json.loads((path or ANATOMY_FILE).read_text(encoding="utf-8"))
    structures = [structure_from_dict(item) for item in raw.get("structures", [])]
    return structures, list(raw.get("terms", [])), str(raw.get("source", ""))


class TerminologyIndex:
    """Alias-aware lookup and in-text recognition of medical terms."""

    def __init__(
        self,
        structures: Iterable[AnatomyStructure] = (),
        terms: Iterable[dict[str, Any]] = (),
        concepts: Iterable[Concept] = (),
    ) -> None:
        self._entries: dict[str, TermEntry] = {}
        self._alias_index: dict[str, set[str]] = {}
        # Folded aliases per term that survived the registration guards;
        # search expansion must offer these and not the raw list, which
        # still carries the ambiguous and too-short ones for display.
        self._indexed_aliases: dict[str, list[str]] = {}
        # Aliases are stored once and indexed by their first token, so
        # recognising terms in a sentence only tests the few aliases that
        # could start in it instead of all of them.
        self._aliases: list[tuple[str, str]] = []  # (folded alias, term_id)
        self._by_first_token: dict[str, list[int]] = {}
        self._patterns: dict[int, re.Pattern[str]] = {}
        # Concept ids already represented by a structure or landmark entry;
        # the anatomy catalogue contributes both, and one term must not be
        # indexed twice under two ids.
        self._known_concepts: set[str] = set()
        for structure in structures:
            self.add_structure(structure)
        for term in terms:
            self.add_term(term)
        for concept in concepts:
            self.add_concept(concept)

    # ------------------------------------------------------------------
    # building
    # ------------------------------------------------------------------

    def _register(self, entry: TermEntry, aliases: Iterable[str]) -> None:
        self._entries[entry.term_id] = entry
        if entry.concept_id:
            self._known_concepts.add(entry.concept_id)
        seen: set[str] = set()
        indexed: list[str] = []
        for alias in aliases:
            folded = " ".join(tokens(alias))
            if len(folded) < 3 or folded in seen or folded in AMBIGUOUS_ALIASES:
                continue
            seen.add(folded)
            indexed.append(folded)
            self._alias_index.setdefault(folded, set()).add(entry.term_id)
            index = len(self._aliases)
            self._aliases.append((folded, entry.term_id))
            self._by_first_token.setdefault(folded.split(" ", 1)[0], []).append(index)
        self._indexed_aliases[entry.term_id] = indexed

    def add_structure(self, structure: AnatomyStructure) -> None:
        aliases = [structure.canonical, structure.turkish, structure.english, *structure.synonyms, *structure.abbreviations]
        aliases.extend(latin_variants(structure.canonical))
        head = structure.canonical.split()[0] if structure.canonical.split() else ""
        if structure.kind == "muscle" and head.lower().startswith("musculus"):
            rest = " ".join(structure.canonical.split()[1:])
            if rest:
                aliases.append(rest)
                aliases.append("m. " + rest)
        if structure.kind == "nerve" and head.lower().startswith("nervus"):
            rest = " ".join(structure.canonical.split()[1:])
            if rest:
                aliases.append("n. " + rest)
        entry = TermEntry(
            term_id=structure.structure_id,
            canonical=structure.canonical,
            kind=structure.kind,
            turkish=structure.turkish,
            english=structure.english,
            aliases=[alias for alias in aliases if alias],
            structure_id=structure.structure_id,
            concept_id=structure.concept_id or f"anatomy.{structure.structure_id}",
        )
        self._register(entry, aliases)
        for landmark in structure.landmarks:
            landmark_entry = TermEntry(
                term_id=f"{structure.structure_id}.{landmark.landmark_id}",
                canonical=landmark.latin,
                kind="landmark",
                turkish=landmark.turkish,
                note=landmark.note,
                aliases=[landmark.latin],
                structure_id=structure.structure_id,
                landmark_of=structure.structure_id,
                concept_id=f"anatomy.{structure.structure_id}.{landmark.landmark_id}",
            )
            self._register(landmark_entry, [landmark.latin])

    def add_term(self, term: dict[str, Any]) -> None:
        canonical = str(term.get("canonical", "")).strip()
        if not canonical:
            return
        term_id = "term." + "_".join(tokens(canonical))
        turkish = str(term.get("turkish", ""))
        english = str(term.get("english", ""))
        # A student searches "Kenar", not "Margo": the translated names are
        # aliases exactly like a structure's are, subject to the same guards.
        aliases = [canonical, turkish, english, *[str(item) for item in term.get("synonyms", [])]]
        # "Facies" alone and each half of "Anterior / Posterior".
        for part in re.split(r"\s*/\s*", canonical):
            aliases.append(re.sub(r"\(.*?\)", "", part).strip())
        entry = TermEntry(
            term_id=term_id,
            canonical=canonical,
            kind="term",
            turkish=turkish,
            english=english,
            aliases=[alias for alias in aliases if alias],
            category=str(term.get("category", "general")),
            note=str(term.get("note", "")),
        )
        self._register(entry, aliases)

    def add_concept(self, concept: Concept) -> None:
        if concept.concept_id in self._entries or concept.concept_id in self._known_concepts:
            return
        aliases = [concept.name, *concept.aliases]
        entry = TermEntry(
            term_id=concept.concept_id,
            canonical=concept.name,
            kind="concept",
            aliases=list(aliases),
            category=concept.subject,
            concept_id=concept.concept_id,
        )
        self._register(entry, aliases)

    # ------------------------------------------------------------------
    # lookup
    # ------------------------------------------------------------------

    def __len__(self) -> int:
        return len(self._entries)

    def get(self, term_id: str) -> TermEntry | None:
        return self._entries.get(term_id)

    def entries(self, *, kind: str | None = None) -> list[TermEntry]:
        return [entry for entry in self._entries.values() if kind is None or entry.kind == kind]

    def lookup(self, query: str, *, limit: int = 8) -> list[TermEntry]:
        """Exact alias first, then prefix, then token overlap."""
        folded = " ".join(tokens(query))
        if not folded:
            return []
        ranked: dict[str, float] = {}
        for term_id in self._alias_index.get(folded, ()):
            ranked[term_id] = max(ranked.get(term_id, 0), 3.0)
        # Every stem a suffix chain could leave, not just the longest one:
        # "humerusun" hides "humerus" behind "un" as well as "humeru"
        # behind "sun", and only the former is a real term.
        for variant in self._suffix_variants(folded):
            if variant == folded:
                continue
            for term_id in self._alias_index.get(variant, ()):
                ranked[term_id] = max(ranked.get(term_id, 0), 2.8)
        query_tokens = set(folded.split())
        for alias, term_id in self._aliases:
            if term_id in ranked and ranked[term_id] >= 2.8:
                continue
            if (alias.startswith(folded) or folded.startswith(alias)) and len(alias) >= 4:
                ranked[term_id] = max(ranked.get(term_id, 0), 2.0 + len(alias) / 100)
                continue
            alias_tokens = set(alias.split())
            overlap = query_tokens & alias_tokens
            if overlap:
                score = len(overlap) / max(len(alias_tokens), len(query_tokens))
                if score >= 0.5:
                    ranked[term_id] = max(ranked.get(term_id, 0), 1.0 + score)
        ordered = sorted(ranked.items(), key=lambda item: (-item[1], item[0]))
        return [self._entries[term_id] for term_id, _score in ordered[: max(1, limit)]]

    @staticmethod
    def _suffix_variants(word: str) -> tuple[str, ...]:
        """The word plus every stem a known Turkish suffix could leave.

        More than one length can match ("humerusun" -> "humerusu" via
        "n", "humerus" via "un", "humeru" via "sun"), so all of them are
        offered to the index; only a real alias can turn into a match.
        """
        variants = [word]
        for length in range(1, min(7, max(1, len(word) - 2))):
            if word[-length:] in TURKISH_SUFFIXES:
                variants.append(word[:-length])
        return tuple(variants)

    def _pattern(self, index: int) -> re.Pattern[str]:
        pattern = self._patterns.get(index)
        if pattern is None:
            alias = self._aliases[index][0]
            head, separator, rest = alias.partition(" ")
            if separator and len(head) == 1:
                # Tokenising drops the dot of "m. deltoideus", so the stored
                # alias is "m deltoideus" while the text still carries the
                # dot; without it the abbreviation would never match itself.
                body = (
                    re.escape(head)
                    + r"(?:\.[\s\-]*|[\s\-]+)"
                    + re.escape(rest).replace(r"\ ", r"[\s\-]+")
                )
            else:
                body = re.escape(alias).replace(r"\ ", r"[\s\-]+")
            pattern = re.compile(r"(?<![a-z0-9])" + body + r"([a-z]*)")
            self._patterns[index] = pattern
        return pattern

    def candidates(self, text: str) -> list[int]:
        """Alias indices that could start somewhere in ``text``.

        A Turkish suffix chain only ever follows the alias's *last* word,
        so the first token is a safe index key once its own suffix is
        stripped ("scapulayı" -> "scapula").
        """
        found: set[int] = set()
        for token in tokens(text):
            for key in self._suffix_variants(token):
                indices = self._by_first_token.get(key)
                if indices:
                    found.update(indices)
        return sorted(found, key=lambda index: (-len(self._aliases[index][0]), self._aliases[index][0]))

    def find_in_text(self, text: str) -> list[TermMatch]:
        """Longest-first, non-overlapping term recognition in free text.

        A match must start at a word boundary and end either at a word
        boundary or before a known Turkish suffix, so "scapulayı" and
        "humerusun" resolve but "spinal" does not resolve to *Spina*.
        """
        folded = fold(text)
        if not folded.strip():
            return []
        taken: list[tuple[int, int]] = []
        matches: list[TermMatch] = []
        for index in self.candidates(text):
            alias, term_id = self._aliases[index]
            for found in self._pattern(index).finditer(folded):
                suffix = found.group(1)
                if suffix and suffix not in TURKISH_SUFFIXES:
                    continue
                start, end = found.start(), found.end()
                if any(not (end <= s or start >= e) for s, e in taken):
                    continue
                taken.append((start, end))
                matches.append(TermMatch(self._entries[term_id], alias, start, end))
        matches.sort(key=lambda item: item.start)
        return matches

    def expand(self, text: str) -> set[str]:
        """Folded aliases of every term found in the text (for search)."""
        expanded: set[str] = set()
        for match in self.find_in_text(text):
            expanded.update(self._indexed_aliases.get(match.entry.term_id, ()))
        return expanded

    def synonyms(self, term_id: str) -> list[str]:
        entry = self._entries.get(term_id)
        return list(entry.aliases) if entry else []

    def explain(self, entry: TermEntry) -> str:
        """`Latin — Türkçe açıklama` in the house format."""
        pieces = [entry.canonical]
        if entry.turkish:
            pieces.append("— " + entry.turkish)
        if entry.english and entry.english.lower() != entry.canonical.lower():
            pieces.append(f"(İng. {entry.english})")
        if entry.note:
            pieces.append("· " + entry.note)
        return " ".join(pieces)


def default_terminology(concepts: Iterable[Concept] = ()) -> TerminologyIndex:
    structures, terms, _source = load_anatomy_data()
    return TerminologyIndex(structures, terms, concepts)
