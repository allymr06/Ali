"""Source-scoped retrieval with page-anchored citations.

Answers grounded in lecture material are assembled from evidence blocks:
the best-matching chunks within the requested documents and page range,
each carrying its neighbouring chunk for context and a ``SourceReference``
that names the document and page. Nothing here invents a page number:
every reference points at a chunk that exists in the store.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

from app.medical.models import DocumentChunk, EvidenceBlock, SourceReference, StudyDocument
from app.medical.search import SearchDocument, SearchHit, SearchIndex
from app.medical.store import MedicalStore
from app.medical.terminology import TerminologyIndex
from app.medical.text import excerpt

MAX_EVIDENCE_CHARS = 7000
MAX_BLOCK_CHARS = 1600
TRUNCATION_MARK = " […kısaltıldı]"


def _truncated(text: str, limit: int) -> str:
    """Cut ``text`` to ``limit`` and say so in the text itself.

    A silently shortened passage reads like the whole of it, which invites
    an answer that claims the page said nothing more.
    """
    room = max(1, limit - len(TRUNCATION_MARK))
    return text[:room].rstrip() + TRUNCATION_MARK


@dataclass(slots=True)
class RetrievalScope:
    document_ids: list[str] = field(default_factory=list)
    page_from: int = 0
    page_to: int = 0
    subject: str | None = None

    @property
    def restricted(self) -> bool:
        return bool(self.document_ids)


class Retriever:
    def __init__(self, store: MedicalStore, terminology: TerminologyIndex | None = None) -> None:
        self._store = store
        self._terminology = terminology
        self._index = SearchIndex(terminology)
        self._index_revision = -1
        self._titles: dict[str, str] = {}
        self._subjects: dict[str, str | None] = {}

    # ------------------------------------------------------------------
    # index lifecycle
    # ------------------------------------------------------------------

    def refresh(self, *, force: bool = False) -> None:
        if not force and self._index_revision == self._store.revision:
            return
        documents = self._store.list_documents()
        self._titles = {document.document_id: document.title for document in documents}
        self._subjects = {document.document_id: document.subject for document in documents}
        entries: list[SearchDocument] = []
        for chunk in self._store.chunks():
            entries.append(
                SearchDocument(
                    doc_id=chunk.chunk_id,
                    kind="chunk",
                    text=chunk.text,
                    title=chunk.heading,
                    subject=self._subjects.get(chunk.document_id),
                    document_id=chunk.document_id,
                    page_number=chunk.page_number,
                    payload={"kind": chunk.kind},
                )
            )
        for note in self._store.list_notes(limit=500):
            entries.append(
                SearchDocument(
                    doc_id=note.note_id,
                    kind="note",
                    text=note.content,
                    title=note.title,
                    subject=note.subject,
                    payload={"topic_id": note.topic_id},
                )
            )
        for question in self._store.query_questions(limit=2000):
            entries.append(
                SearchDocument(
                    doc_id=question.question_id,
                    kind="question",
                    text=question.stem + " " + " ".join(option.text for option in question.options),
                    title="",
                    subject=question.subject,
                    payload={"origin": question.origin, "topic_id": question.topic_id, "professor_id": question.professor_id},
                )
            )
        self._index.build(entries)
        self._index_revision = self._store.revision

    @property
    def index(self) -> SearchIndex:
        self.refresh()
        return self._index

    # ------------------------------------------------------------------
    # evidence
    # ------------------------------------------------------------------

    def retrieve(
        self,
        query: str,
        scope: RetrievalScope | None = None,
        *,
        limit: int = 6,
        neighbours: bool = True,
    ) -> list[EvidenceBlock]:
        self.refresh()
        active = scope or RetrievalScope()
        hits = self._index.search(
            query,
            kinds=("chunk",),
            document_ids=active.document_ids or None,
            page_from=active.page_from,
            page_to=active.page_to,
            subject=None,
            limit=max(1, limit) * 2,
        )
        blocks: list[EvidenceBlock] = []
        seen_pages: set[tuple[str, int]] = set()
        total = 0
        for hit in hits:
            document = hit.document
            key = (document.document_id or "", document.page_number)
            if key in seen_pages:
                continue
            chunk = self._store.get_chunk(document.doc_id)
            if chunk is None:
                continue
            text = chunk.text
            if neighbours and chunk.kind == "text":
                text = self._with_neighbours(chunk)
            text = text[:MAX_BLOCK_CHARS]
            if total + len(text) > MAX_EVIDENCE_CHARS and blocks:
                break
            seen_pages.add(key)
            reference = SourceReference(
                document_id=chunk.document_id,
                page_number=chunk.page_number,
                chunk_id=chunk.chunk_id,
                quote=excerpt(chunk.text, 160),
                title=self._titles.get(chunk.document_id, chunk.document_id),
            )
            blocks.append(EvidenceBlock(reference=reference, text=text, score=hit.score, kind=chunk.kind))
            total += len(text)
            if len(blocks) >= limit:
                break
        return blocks

    def _with_neighbours(self, chunk: DocumentChunk) -> str:
        siblings = [
            item
            for item in self._store.chunks(document_ids=[chunk.document_id], page_from=chunk.page_number, page_to=chunk.page_number)
            if item.kind == "text"
        ]
        siblings.sort(key=lambda item: item.index_in_page)
        position = next((index for index, item in enumerate(siblings) if item.chunk_id == chunk.chunk_id), None)
        if position is None:
            return chunk.text
        parts = [chunk.text]
        if position > 0:
            previous = siblings[position - 1].text
            parts.insert(0, previous[-400:])
        if position + 1 < len(siblings):
            following = siblings[position + 1].text
            parts.append(following[:400])
        return " … ".join(part for part in parts if part.strip())

    def page_evidence(self, document: StudyDocument, page_from: int, page_to: int, *, max_chars: int = MAX_EVIDENCE_CHARS) -> list[EvidenceBlock]:
        """Every chunk of a page range, in order, bounded by characters.

        A page whose very first chunk already overruns the budget still
        yields one block: dropping it would leave the caller with no
        evidence at all and an answer that only looks grounded.
        """
        blocks: list[EvidenceBlock] = []
        total = 0
        for chunk in self._store.chunks(document_ids=[document.document_id], page_from=page_from, page_to=page_to):
            text = chunk.text
            if len(text) > MAX_BLOCK_CHARS:
                text = _truncated(text, MAX_BLOCK_CHARS)
            if total + len(text) > max_chars:
                if blocks:
                    break
                text = _truncated(text, max_chars)
            blocks.append(
                EvidenceBlock(
                    reference=SourceReference(
                        document_id=chunk.document_id,
                        page_number=chunk.page_number,
                        chunk_id=chunk.chunk_id,
                        quote=excerpt(chunk.text, 160),
                        title=document.title,
                    ),
                    text=text,
                    score=1.0,
                    kind=chunk.kind,
                )
            )
            total += len(text)
        return blocks

    @staticmethod
    def format_evidence(blocks: Iterable[EvidenceBlock]) -> str:
        lines: list[str] = []
        for number, block in enumerate(blocks, start=1):
            label = "şekil açıklaması" if block.kind == "visual" else "ders notu"
            lines.append(f"[Kaynak {number}] {block.reference.title}, s. {block.reference.page_number} ({label})")
            lines.append(block.text.strip())
            lines.append("")
        return "\n".join(lines).strip()

    @staticmethod
    def references(blocks: Iterable[EvidenceBlock]) -> list[dict[str, Any]]:
        return [
            {
                "index": number,
                "document_id": block.reference.document_id,
                "title": block.reference.title,
                "page_number": block.reference.page_number,
                "chunk_id": block.reference.chunk_id,
                "quote": block.reference.quote,
                "kind": block.kind,
                "score": block.score,
            }
            for number, block in enumerate(blocks, start=1)
        ]

    # ------------------------------------------------------------------
    # general search (page)
    # ------------------------------------------------------------------

    def search(self, query: str, *, kinds: Iterable[str] | None = None, limit: int = 20) -> list[SearchHit]:
        self.refresh()
        return self._index.search(query, kinds=kinds, limit=limit)

    def title_of(self, document_id: str) -> str:
        self.refresh()
        return self._titles.get(document_id, document_id)
