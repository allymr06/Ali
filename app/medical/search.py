"""Lexical search across study material.

A BM25 index over document chunks, notes, questions, terminology and
topics with synonym expansion from the terminology engine, so
"shoulder blade" finds *scapula* and "kürek kemiği" finds the same
passages. Deterministic, offline and rebuilt only when the store
changes; a semantic layer can be added on top without replacing it.
"""

from __future__ import annotations

import math
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

from app.medical.terminology import TerminologyIndex
from app.medical.text import content_tokens, stem

BM25_K1 = 1.5
BM25_B = 0.75
SYNONYM_WEIGHT = 0.7


@dataclass(slots=True)
class SearchDocument:
    doc_id: str
    kind: str
    text: str
    title: str = ""
    subject: str | None = None
    document_id: str | None = None
    page_number: int = 0
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class SearchHit:
    document: SearchDocument
    score: float
    matched: list[str] = field(default_factory=list)


class SearchIndex:
    def __init__(self, terminology: TerminologyIndex | None = None) -> None:
        self._terminology = terminology
        self._documents: list[SearchDocument] = []
        self._postings: dict[str, dict[int, int]] = {}
        self._lengths: list[int] = []
        self._title_stems: list[set[str]] = []
        self._average_length = 0.0

    def __len__(self) -> int:
        return len(self._documents)

    @staticmethod
    def _stems(text: str) -> list[str]:
        return [stem(token) for token in content_tokens(text)]

    def build(self, documents: Iterable[SearchDocument]) -> None:
        self._documents = list(documents)
        self._postings = {}
        self._lengths = []
        self._title_stems = []
        total = 0
        for index, document in enumerate(self._documents):
            stems = self._stems(document.text)
            if document.title:
                stems.extend(self._stems(document.title))
            counts = Counter(stems)
            for token, count in counts.items():
                self._postings.setdefault(token, {})[index] = count
            self._lengths.append(len(stems))
            self._title_stems.append(set(self._stems(document.title)) if document.title else set())
            total += len(stems)
        self._average_length = total / len(self._documents) if self._documents else 0.0

    def query_terms(self, query: str) -> list[tuple[str, float]]:
        """Query stems plus synonym stems at a lower weight."""
        primary = self._stems(query)
        weighted: dict[str, float] = {}
        for token in primary:
            weighted[token] = max(weighted.get(token, 0.0), 1.0)
        if self._terminology is not None:
            for alias in self._terminology.expand(query):
                for token in self._stems(alias):
                    if token not in weighted:
                        weighted[token] = SYNONYM_WEIGHT
        return list(weighted.items())

    def search(
        self,
        query: str,
        *,
        kinds: Iterable[str] | None = None,
        document_ids: Iterable[str] | None = None,
        page_from: int = 0,
        page_to: int = 0,
        subject: str | None = None,
        limit: int = 10,
    ) -> list[SearchHit]:
        if not self._documents:
            return []
        terms = self.query_terms(query)
        if not terms:
            return []
        allowed_kinds = set(kinds) if kinds else None
        allowed_documents = {str(item) for item in document_ids} if document_ids else None
        total = len(self._documents)
        scores: dict[int, float] = {}
        matched: dict[int, list[str]] = {}
        for token, weight in terms:
            postings = self._postings.get(token)
            if not postings:
                continue
            document_frequency = len(postings)
            idf = math.log(1 + (total - document_frequency + 0.5) / (document_frequency + 0.5))
            for index, term_frequency in postings.items():
                length = self._lengths[index] or 1
                denominator = term_frequency + BM25_K1 * (1 - BM25_B + BM25_B * length / (self._average_length or 1))
                contribution = idf * (term_frequency * (BM25_K1 + 1)) / denominator
                if token in self._title_stems[index]:
                    contribution *= 1.25
                scores[index] = scores.get(index, 0.0) + weight * contribution
                matched.setdefault(index, []).append(token)
        hits: list[SearchHit] = []
        for index, score in scores.items():
            document = self._documents[index]
            if allowed_kinds is not None and document.kind not in allowed_kinds:
                continue
            if allowed_documents is not None and (document.document_id or "") not in allowed_documents:
                continue
            if (page_from or page_to) and document.page_number:
                if document.page_number < (page_from or 1) or document.page_number > (page_to or 10**9):
                    continue
            if subject and document.subject and document.subject != subject:
                continue
            hits.append(SearchHit(document, round(score, 4), sorted(set(matched.get(index, [])))))
        hits.sort(key=lambda hit: (-hit.score, hit.document.doc_id))
        return hits[: max(1, limit)]
