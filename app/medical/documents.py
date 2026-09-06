"""The document engine: PDF and text ingestion, page rendering, chunking.

Pipeline: file → sha/duplicate check → copy into the academy directory →
page text (pypdfium2) → heading detection → image-area estimate → chunks
with page-anchored offsets → search-ready. Pages whose information is
mostly visual are queued for the vision pass, which the academy runs
through the model client (it needs a provider; this module does not).
Progress is reported as real stages, never as invented percentages.
"""

from __future__ import annotations

import hashlib
import io
import shutil
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.core.time import utc_now
from app.medical.models import (
    DOCUMENT_STATUS_LABELS_TR,
    DocumentChunk,
    DocumentPage,
    DocumentStatus,
    StudyDocument,
    new_id,
)
from app.medical.store import MedicalStore
from app.medical.text import chunk_text, clean_lines, is_heading
from app.vision.models import PixelImage

SUPPORTED_SUFFIXES = {".pdf", ".txt", ".md", ".text"}
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp"}
TEXT_PAGE_CHARS = 2800
VISUAL_MIN_IMAGE_RATIO = 0.18
VISUAL_TEXT_POOR_CHARS = 220
DEFAULT_RENDER_SCALE = 1.5
MAX_RENDER_SCALE = 3.0
PDF_OBJECT_IMAGE = 3

ProgressCallback = Callable[[str, str], None]


class DocumentError(RuntimeError):
    """A document could not be read, stored or processed."""


@dataclass(slots=True)
class ExtractedPage:
    page_number: int
    text: str
    image_area_ratio: float
    image_count: int


class PdfReader:
    """Thin adapter over pypdfium2 with a graceful absence message."""

    def __init__(self, source: bytes | Path) -> None:
        try:
            import pypdfium2 as pdfium
        except ImportError as exc:  # pragma: no cover - depends on the environment
            raise DocumentError(
                "PDF desteği kurulu değil (pypdfium2). Bağımlılıkları yeniden kur."
            ) from exc
        self._pdfium = pdfium
        try:
            self._document = pdfium.PdfDocument(source if isinstance(source, bytes) else str(source))
        except Exception as exc:
            raise DocumentError(f"PDF açılamadı ({type(exc).__name__}).") from exc

    def close(self) -> None:
        try:
            self._document.close()
        except Exception:
            pass

    def __enter__(self) -> "PdfReader":
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    @property
    def page_count(self) -> int:
        return len(self._document)

    def metadata(self) -> dict[str, str]:
        try:
            raw = self._document.get_metadata_dict()
        except Exception:
            return {}
        return {str(key): str(value) for key, value in raw.items() if value}

    def outline(self) -> list[tuple[int, str, int]]:
        """(level, title, page_number) from the PDF's bookmarks, when any."""
        entries: list[tuple[int, str, int]] = []
        try:
            for item in self._document.get_toc():
                page_index = getattr(item, "page_index", None)
                title = str(getattr(item, "title", "") or "").strip()
                level = int(getattr(item, "level", 0) or 0)
                if title and page_index is not None and page_index >= 0:
                    entries.append((level, title, int(page_index) + 1))
        except Exception:
            return []
        return entries

    def extract(self, page_number: int) -> ExtractedPage:
        page = self._document[page_number - 1]
        try:
            try:
                textpage = page.get_textpage()
                text = textpage.get_text_range() or ""
                textpage.close()
            except Exception:
                text = ""
            width, height = page.get_size()
            area = max(1.0, float(width) * float(height))
            image_area = 0.0
            image_count = 0
            try:
                for obj in page.get_objects():
                    if getattr(obj, "type", None) != PDF_OBJECT_IMAGE:
                        continue
                    left, bottom, right, top = obj.get_bounds()
                    image_area += max(0.0, (right - left)) * max(0.0, (top - bottom))
                    image_count += 1
            except Exception:
                pass
            ratio = min(1.0, image_area / area) if image_count else 0.0
            return ExtractedPage(page_number, text.replace("\r\n", "\n").replace("\r", "\n"), round(ratio, 3), image_count)
        finally:
            try:
                page.close()
            except Exception:
                pass

    def render_png(self, page_number: int, scale: float = DEFAULT_RENDER_SCALE) -> bytes:
        page = self._document[page_number - 1]
        try:
            bitmap = page.render(scale=max(0.2, min(MAX_RENDER_SCALE, float(scale))), rev_byteorder=True)
            try:
                width, height, stride = int(bitmap.width), int(bitmap.height), int(bitmap.stride)
                channels = int(getattr(bitmap, "n_channels", 3) or 3)
                raw = bytes(bitmap.buffer)
                rgb = bytearray(width * height * 3)
                for row in range(height):
                    source = raw[row * stride : row * stride + width * channels]
                    if channels == 3:
                        rgb[row * width * 3 : (row + 1) * width * 3] = source
                    else:
                        # BGRA/RGBA rendered with reversed byte order → RGB(A).
                        for column in range(width):
                            offset = column * channels
                            base = (row * width + column) * 3
                            rgb[base : base + 3] = source[offset : offset + 3]
                image = PixelImage(width, height, rgb, captured_at=utc_now())
                return bytes(image.to_png())
            finally:
                try:
                    bitmap.close()
                except Exception:
                    pass
        finally:
            try:
                page.close()
            except Exception:
                pass


def sha256_of(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def human_size(size: int) -> str:
    """A byte count in the unit the reader can act on.

    Whole megabytes hide every limit below one megabyte behind a "0 MB",
    which tells the student nothing about the file they just picked.
    """
    count = max(0, int(size))
    if count < 1024:
        return f"{count} bayt"
    if count < 1024 * 1024:
        return f"{round(count / 1024)} KB"
    megabytes = f"{count / (1024 * 1024):.1f}".replace(".", ",")
    if megabytes.endswith(",0"):
        megabytes = megabytes[:-2]
    return f"{megabytes} MB"


def split_text_pages(text: str, *, page_chars: int = TEXT_PAGE_CHARS) -> list[str]:
    """Plain text becomes pseudo-pages on form feeds, else by size."""
    normalized = str(text or "").replace("\r\n", "\n").replace("\r", "\n")
    if "\f" in normalized:
        pages = [part.strip("\n") for part in normalized.split("\f")]
        return [page for page in pages if page.strip()] or [""]
    lines = normalized.split("\n")
    pages: list[str] = []
    buffer: list[str] = []
    size = 0
    for line in lines:
        if size + len(line) > page_chars and buffer:
            pages.append("\n".join(buffer))
            buffer, size = [], 0
        buffer.append(line)
        size += len(line) + 1
    if buffer:
        pages.append("\n".join(buffer))
    return [page for page in pages if page.strip()] or [""]


def page_headings(text: str) -> list[str]:
    lines = clean_lines(text)
    headings: list[str] = []
    for index, line in enumerate(lines[:40]):
        nxt = lines[index + 1] if index + 1 < len(lines) else None
        if is_heading(line, next_line=nxt):
            headings.append(line)
        if len(headings) >= 6:
            break
    return headings


class DocumentPipeline:
    def __init__(
        self,
        store: MedicalStore,
        *,
        directory: Path | None = None,
        max_pages: int = 400,
        max_bytes: int = 60 * 1024 * 1024,
        vision_pages_per_document: int = 12,
        render_scale: float = DEFAULT_RENDER_SCALE,
    ) -> None:
        self._store = store
        self._directory = directory
        self._max_pages = max(1, int(max_pages))
        self._max_bytes = max(1024, int(max_bytes))
        self._vision_budget = max(0, int(vision_pages_per_document))
        self._render_scale = float(render_scale)
        self._memory_files: dict[str, bytes] = {}

    @property
    def directory(self) -> Path | None:
        return self._directory

    # ------------------------------------------------------------------
    # import
    # ------------------------------------------------------------------

    def import_file(
        self,
        path: str | Path,
        *,
        title: str | None = None,
        subject: str | None = None,
        professor_id: str | None = None,
        tags: list[str] | None = None,
    ) -> tuple[StudyDocument, bool]:
        """Register a file; returns ``(document, created)``. A file whose
        bytes were imported before is returned as-is, never duplicated."""
        source = Path(path)
        if not source.is_file():
            raise DocumentError("Dosya bulunamadı.")
        suffix = source.suffix.lower()
        if suffix not in SUPPORTED_SUFFIXES:
            raise DocumentError("Desteklenen türler: PDF ve düz metin (.txt, .md).")
        size = source.stat().st_size
        if size > self._max_bytes:
            raise DocumentError(f"Dosya çok büyük ({human_size(size)}); sınır {human_size(self._max_bytes)}.")
        data = source.read_bytes()
        digest = sha256_of(data)
        existing = self._store.find_document_by_sha(digest)
        if existing is not None:
            return existing, False
        document_id = new_id("doc")
        kind = "pdf" if suffix == ".pdf" else "text"
        stored_path: str | None = None
        if self._directory is not None:
            target_dir = self._directory / "documents"
            target_dir.mkdir(parents=True, exist_ok=True)
            target = target_dir / f"{document_id}{suffix}"
            shutil.copyfile(source, target)
            stored_path = str(target)
        else:
            self._memory_files[document_id] = data
        document = StudyDocument(
            document_id=document_id,
            title=(title or source.stem).strip()[:200] or source.stem,
            file_name=source.name,
            sha256=digest,
            kind=kind,
            subject=subject,
            tags=[str(item) for item in (tags or []) if str(item).strip()][:20],
            status=DocumentStatus.PENDING,
            status_detail=DOCUMENT_STATUS_LABELS_TR[DocumentStatus.PENDING],
            stored_path=stored_path,
            professor_id=professor_id,
        )
        self._store.save_document(document)
        return document, True

    def import_text(
        self,
        text: str,
        *,
        title: str,
        subject: str | None = None,
        professor_id: str | None = None,
        tags: list[str] | None = None,
    ) -> tuple[StudyDocument, bool]:
        data = str(text or "").encode("utf-8")
        if not data.strip():
            raise DocumentError("Metin boş.")
        if len(data) > self._max_bytes:
            raise DocumentError("Metin çok uzun.")
        digest = sha256_of(data)
        existing = self._store.find_document_by_sha(digest)
        if existing is not None:
            return existing, False
        document_id = new_id("doc")
        stored_path: str | None = None
        if self._directory is not None:
            target_dir = self._directory / "documents"
            target_dir.mkdir(parents=True, exist_ok=True)
            target = target_dir / f"{document_id}.txt"
            target.write_bytes(data)
            stored_path = str(target)
        else:
            self._memory_files[document_id] = data
        document = StudyDocument(
            document_id=document_id,
            title=title.strip()[:200] or "Metin",
            file_name=f"{title.strip()[:60] or 'metin'}.txt",
            sha256=digest,
            kind="text",
            subject=subject,
            tags=[str(item) for item in (tags or []) if str(item).strip()][:20],
            status=DocumentStatus.PENDING,
            status_detail=DOCUMENT_STATUS_LABELS_TR[DocumentStatus.PENDING],
            stored_path=stored_path,
            professor_id=professor_id,
        )
        self._store.save_document(document)
        return document, True

    # ------------------------------------------------------------------
    # bytes access
    # ------------------------------------------------------------------

    def _bytes(self, document: StudyDocument) -> bytes:
        if document.stored_path:
            path = Path(document.stored_path)
            if not path.is_file():
                raise DocumentError("Belgenin kopyası bulunamadı; yeniden içe aktar.")
            return path.read_bytes()
        data = self._memory_files.get(document.document_id)
        if data is None:
            raise DocumentError("Belgenin içeriği bellekte değil; yeniden içe aktar.")
        return data

    # ------------------------------------------------------------------
    # processing
    # ------------------------------------------------------------------

    def _set_status(
        self,
        document: StudyDocument,
        status: str,
        *,
        detail: str | None = None,
        progress: ProgressCallback | None = None,
    ) -> None:
        document.status = status
        document.status_detail = detail or DOCUMENT_STATUS_LABELS_TR.get(status, status)
        self._store.save_document(document)
        if progress is not None:
            try:
                progress(status, document.status_detail)
            except Exception:
                pass

    def process(self, document_id: str, *, progress: ProgressCallback | None = None) -> StudyDocument:
        document = self._store.get_document(document_id)
        if document is None:
            raise DocumentError("Belge bulunamadı.")
        try:
            self._set_status(document, DocumentStatus.READING, progress=progress)
            data = self._bytes(document)
            if document.kind == "pdf":
                pages = self._extract_pdf(document, data, progress)
            else:
                pages = self._extract_text(document, data)
            self._set_status(document, DocumentStatus.INDEXING, progress=progress)
            chunk_count = self._index(document, pages)
            document.page_count = len(pages)
            document.chunk_count = chunk_count
            pending = [page for page in pages if page.visual_status == "pending"]
            document.visual_pages_pending = len(pending)
            document.error = None
            document.indexed_at = utc_now()
            self._set_status(
                document,
                DocumentStatus.READY,
                detail=self._ready_detail(document),
                progress=progress,
            )
        except DocumentError as exc:
            document.error = str(exc)
            self._set_status(document, DocumentStatus.FAILED, detail=str(exc), progress=progress)
        except Exception as exc:
            document.error = f"{type(exc).__name__}"
            self._set_status(
                document,
                DocumentStatus.FAILED,
                detail=f"Belge işlenemedi ({type(exc).__name__}).",
                progress=progress,
            )
        return document

    @staticmethod
    def _ready_detail(document: StudyDocument) -> str:
        parts = [f"{document.page_count} sayfa", f"{document.chunk_count} parça"]
        if document.visual_pages_pending:
            parts.append(f"{document.visual_pages_pending} sayfa görsel inceleme bekliyor")
        return "Hazır · " + " · ".join(parts)

    def _extract_pdf(
        self,
        document: StudyDocument,
        data: bytes,
        progress: ProgressCallback | None,
    ) -> list[DocumentPage]:
        with PdfReader(data) as reader:
            count = reader.page_count
            if count == 0:
                raise DocumentError("PDF'de sayfa yok.")
            if count > self._max_pages:
                raise DocumentError(f"PDF {count} sayfa; sınır {self._max_pages} sayfa.")
            metadata = reader.metadata()
            if metadata.get("Title") and document.title == Path(document.file_name).stem:
                document.title = metadata["Title"].strip()[:200] or document.title
            outline = {page: title for _level, title, page in reader.outline()}
            self._set_status(document, DocumentStatus.EXTRACTING, detail=f"Sayfalar çıkarılıyor · 0 / {count}", progress=progress)
            pages: list[DocumentPage] = []
            for number in range(1, count + 1):
                extracted = reader.extract(number)
                headings = page_headings(extracted.text)
                if number in outline and outline[number] not in headings:
                    headings.insert(0, outline[number])
                page = DocumentPage(
                    document_id=document.document_id,
                    page_number=number,
                    text=extracted.text,
                    headings=headings,
                    image_area_ratio=extracted.image_area_ratio,
                    image_count=extracted.image_count,
                )
                pages.append(page)
                if number % 10 == 0 or number == count:
                    self._set_status(
                        document,
                        DocumentStatus.EXTRACTING,
                        detail=f"Sayfalar çıkarılıyor · {number} / {count}",
                        progress=progress,
                    )
            self._mark_visual_pages(pages)
            self._store.save_pages(pages)
            total_chars = sum(page.char_count for page in pages)
            if total_chars < 40 and not any(page.visual_status == "pending" for page in pages):
                raise DocumentError("PDF'den metin çıkarılamadı (taranmış belge olabilir) ve görsel inceleme için sayfa seçilemedi.")
            return pages

    def _extract_text(self, document: StudyDocument, data: bytes) -> list[DocumentPage]:
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            text = data.decode("cp1254", errors="replace")
        parts = split_text_pages(text)
        if len(parts) > self._max_pages:
            raise DocumentError(f"Metin {len(parts)} sayfaya bölündü; sınır {self._max_pages}.")
        pages = [
            DocumentPage(
                document_id=document.document_id,
                page_number=index,
                text=part,
                headings=page_headings(part),
            )
            for index, part in enumerate(parts, start=1)
        ]
        self._store.save_pages(pages)
        return pages

    def _mark_visual_pages(self, pages: list[DocumentPage]) -> None:
        candidates = [
            page
            for page in pages
            if page.image_count
            and (page.image_area_ratio >= VISUAL_MIN_IMAGE_RATIO or page.char_count < VISUAL_TEXT_POOR_CHARS)
        ]
        candidates.sort(key=lambda page: (-page.image_area_ratio, page.char_count, page.page_number))
        chosen = {page.page_number for page in candidates[: self._vision_budget]}
        for page in pages:
            if page.page_number in chosen:
                page.visual_status = "pending"
            elif page.image_count:
                page.visual_status = "skipped"
            else:
                page.visual_status = "not_needed"

    def _index(self, document: StudyDocument, pages: list[DocumentPage]) -> int:
        chunks: list[DocumentChunk] = []
        for page in pages:
            spans = chunk_text(page.text)
            for index, span in enumerate(spans):
                chunks.append(
                    DocumentChunk(
                        chunk_id=f"{document.document_id}:{page.page_number}:{index}",
                        document_id=document.document_id,
                        page_number=page.page_number,
                        index_in_page=index,
                        text=span.text,
                        heading=span.heading or (page.headings[0] if page.headings else ""),
                        start_char=span.start,
                        end_char=span.end,
                    )
                )
            if page.visual_summary:
                chunks.append(self._visual_chunk(page, len(spans)))
        return self._store.replace_chunks(document.document_id, chunks)

    @staticmethod
    def _visual_chunk(page: DocumentPage, index: int) -> DocumentChunk:
        labels = ", ".join(page.visual_labels)
        text = page.visual_summary + (f"\nEtiketler: {labels}" if labels else "")
        return DocumentChunk(
            chunk_id=f"{page.document_id}:{page.page_number}:visual",
            document_id=page.document_id,
            page_number=page.page_number,
            index_in_page=index,
            text=text,
            heading=page.headings[0] if page.headings else "Şekil",
            kind="visual",
        )

    # ------------------------------------------------------------------
    # vision support
    # ------------------------------------------------------------------

    def pages_needing_vision(self, document_id: str) -> list[DocumentPage]:
        return [page for page in self._store.get_pages(document_id) if page.visual_status == "pending"]

    def attach_visual_summary(
        self,
        document_id: str,
        page_number: int,
        *,
        summary: str,
        labels: list[str],
        status: str = "done",
    ) -> DocumentPage | None:
        page = self._store.get_page(document_id, page_number)
        if page is None:
            return None
        page.visual_summary = summary.strip()
        page.visual_labels = [str(label).strip() for label in labels if str(label).strip()][:60]
        page.visual_status = status
        self._store.save_page(page)
        # Re-index this page's chunks so the figure becomes searchable.
        all_chunks = [chunk for chunk in self._store.chunks(document_ids=[document_id]) if not (chunk.page_number == page_number and chunk.kind == "visual")]
        text_chunks_on_page = [chunk for chunk in all_chunks if chunk.page_number == page_number]
        if page.visual_summary:
            all_chunks.append(self._visual_chunk(page, len(text_chunks_on_page)))
        self._store.replace_chunks(document_id, all_chunks)
        document = self._store.get_document(document_id)
        if document is not None:
            pending = self.pages_needing_vision(document_id)
            document.visual_pages_pending = len(pending)
            document.visual_pages_analyzed = sum(1 for item in self._store.get_pages(document_id) if item.visual_status == "done")
            document.chunk_count = len(all_chunks)
            if document.status == DocumentStatus.READY:
                document.status_detail = self._ready_detail(document)
            self._store.save_document(document)
        return page

    def render_page(self, document_id: str, page_number: int, *, scale: float | None = None) -> bytes:
        document = self._store.get_document(document_id)
        if document is None:
            raise DocumentError("Belge bulunamadı.")
        if document.kind != "pdf":
            raise DocumentError("Yalnızca PDF sayfaları görüntülenebilir.")
        chosen = float(scale or self._render_scale)
        cached = self._store.get_page_image(document_id, page_number, chosen)
        if cached is not None:
            return cached
        with PdfReader(self._bytes(document)) as reader:
            if page_number < 1 or page_number > reader.page_count:
                raise DocumentError("Sayfa numarası aralık dışında.")
            png = reader.render_png(page_number, chosen)
        self._store.put_page_image(document_id, page_number, chosen, png)
        return png

    # ------------------------------------------------------------------
    # deletion
    # ------------------------------------------------------------------

    def delete(self, document_id: str) -> bool:
        document = self._store.get_document(document_id)
        removed = self._store.delete_document(document_id)
        self._memory_files.pop(document_id, None)
        if document is not None and document.stored_path:
            try:
                Path(document.stored_path).unlink(missing_ok=True)
            except OSError:
                pass
        return removed

    def payload(self, document: StudyDocument) -> dict[str, Any]:
        return {
            "document_id": document.document_id,
            "title": document.title,
            "file_name": document.file_name,
            "kind": document.kind,
            "page_count": document.page_count,
            "subject": document.subject,
            "topic_ids": list(document.topic_ids),
            "tags": list(document.tags),
            "status": document.status,
            "status_label": DOCUMENT_STATUS_LABELS_TR.get(document.status, document.status),
            "status_detail": document.status_detail,
            "error": document.error,
            "professor_id": document.professor_id,
            "visual_pages_analyzed": document.visual_pages_analyzed,
            "visual_pages_pending": document.visual_pages_pending,
            "chunk_count": document.chunk_count,
            "summary": document.summary,
            "key_terms": list(document.key_terms),
            "imported_at": document.imported_at.isoformat(),
            "indexed_at": document.indexed_at.isoformat() if document.indexed_at else None,
            "ready": document.status == DocumentStatus.READY,
        }
