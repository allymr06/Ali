"""SQLite persistence for the Medical Academy.

One connection, one lock, JSON documents in typed tables. Everything the
academy learns about the student (documents, notes, questions, exams,
attempts, mastery, professor profiles, the study session) lives here and
nowhere in the chat history. ``path=None`` keeps it all in memory, which
is what tests and the demo use.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable, Iterable, Iterator
from pathlib import Path
from threading import RLock
from typing import Any

from app.core.time import utc_now
from app.medical.models import (
    ConceptMastery,
    DocumentChunk,
    DocumentPage,
    Exam,
    ExamAttempt,
    ProfessorProfile,
    Question,
    StudyDocument,
    StudyNote,
    StudySession,
    attempt_from_dict,
    chunk_from_dict,
    document_from_dict,
    dumps,
    exam_from_dict,
    mastery_from_dict,
    note_from_dict,
    page_from_dict,
    professor_from_dict,
    question_from_dict,
    session_from_dict,
    to_plain,
)

SCHEMA_VERSION = 1

# How many rows a filtered scan pulls per round trip. Filters that read the JSON
# body cannot run in SQL, so those queries walk the table in pages instead of
# guessing how many rows will survive them.
_SCAN_PAGE = 200

_SCHEMA = (
    "CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)",
    "CREATE TABLE IF NOT EXISTS documents (document_id TEXT PRIMARY KEY, imported_at TEXT NOT NULL, sha256 TEXT NOT NULL, body TEXT NOT NULL)",
    "CREATE INDEX IF NOT EXISTS documents_sha ON documents (sha256)",
    "CREATE TABLE IF NOT EXISTS pages (document_id TEXT NOT NULL, page_number INTEGER NOT NULL, body TEXT NOT NULL, PRIMARY KEY (document_id, page_number))",
    "CREATE TABLE IF NOT EXISTS chunks (chunk_id TEXT PRIMARY KEY, document_id TEXT NOT NULL, page_number INTEGER NOT NULL, body TEXT NOT NULL)",
    "CREATE INDEX IF NOT EXISTS chunks_document ON chunks (document_id, page_number)",
    "CREATE TABLE IF NOT EXISTS page_images (document_id TEXT NOT NULL, page_number INTEGER NOT NULL, scale REAL NOT NULL, png BLOB NOT NULL, PRIMARY KEY (document_id, page_number, scale))",
    "CREATE TABLE IF NOT EXISTS notes (note_id TEXT PRIMARY KEY, created_at TEXT NOT NULL, body TEXT NOT NULL)",
    "CREATE TABLE IF NOT EXISTS questions (question_id TEXT PRIMARY KEY, subject TEXT NOT NULL, topic_id TEXT, origin TEXT NOT NULL, professor_id TEXT, difficulty INTEGER NOT NULL, created_at TEXT NOT NULL, body TEXT NOT NULL)",
    "CREATE INDEX IF NOT EXISTS questions_subject ON questions (subject, topic_id)",
    "CREATE INDEX IF NOT EXISTS questions_professor ON questions (professor_id)",
    "CREATE TABLE IF NOT EXISTS exams (exam_id TEXT PRIMARY KEY, created_at TEXT NOT NULL, body TEXT NOT NULL)",
    "CREATE TABLE IF NOT EXISTS attempts (attempt_id TEXT PRIMARY KEY, exam_id TEXT NOT NULL, started_at TEXT NOT NULL, body TEXT NOT NULL)",
    "CREATE INDEX IF NOT EXISTS attempts_exam ON attempts (exam_id)",
    "CREATE TABLE IF NOT EXISTS mastery (concept_id TEXT PRIMARY KEY, body TEXT NOT NULL)",
    "CREATE TABLE IF NOT EXISTS professors (profile_id TEXT PRIMARY KEY, body TEXT NOT NULL)",
    "CREATE TABLE IF NOT EXISTS sessions (session_id TEXT PRIMARY KEY, body TEXT NOT NULL)",
    "CREATE TABLE IF NOT EXISTS learned_concepts (concept_id TEXT PRIMARY KEY, body TEXT NOT NULL)",
)

_LIKE_ESCAPE = "\\"


def _like_prefix(value: str) -> str:
    """Turn a literal into a LIKE pattern matching everything that starts with it.

    Topic ids carry underscores ("upper_limb") and ``_`` is a LIKE wildcard, so
    an unescaped pattern would also match unrelated ids of the same shape.
    """
    escaped = value
    for character in (_LIKE_ESCAPE, "%", "_"):
        escaped = escaped.replace(character, _LIKE_ESCAPE + character)
    return escaped + "%"


class MedicalStore:
    def __init__(self, path: str | Path | None = None) -> None:
        self._path = Path(path) if path else None
        if self._path is not None:
            self._path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = RLock()
        self._connection = sqlite3.connect(
            str(self._path) if self._path is not None else ":memory:",
            check_same_thread=False,
            timeout=5.0,
        )
        self._connection.row_factory = sqlite3.Row
        with self._lock:
            if self._path is not None:
                try:
                    self._connection.execute("PRAGMA journal_mode=WAL")
                except sqlite3.DatabaseError:
                    pass
            for statement in _SCHEMA:
                self._connection.execute(statement)
            self._connection.execute(
                "INSERT OR IGNORE INTO meta (key, value) VALUES ('schema_version', ?)",
                (str(SCHEMA_VERSION),),
            )
            self._connection.commit()
        self._revision = 0
        self._content_revision = 0

    @property
    def path(self) -> Path | None:
        return self._path

    @property
    def persistent(self) -> bool:
        return self._path is not None

    @property
    def revision(self) -> int:
        """Bumps on every write, whatever table it touched."""
        return self._revision

    @property
    def content_revision(self) -> int:
        """Bumps only on writes the search index is built from.

        Documents, chunks, notes and questions are the index; a session, page,
        page image, exam, attempt, mastery, professor or learned-concept write
        leaves it valid. The retriever watches this counter rather than
        ``revision`` so the index stays warm across the session save the tutor
        performs on every medical turn.
        """
        return self._content_revision

    def close(self) -> None:
        with self._lock:
            try:
                self._connection.close()
            except sqlite3.Error:
                pass

    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------

    def _bump(self, *, indexed: bool) -> None:
        """Record a write; ``indexed`` says it changed what the index reads.

        The retrieval index (app/medical/retrieval.py) is built from documents,
        chunks, notes and questions and reads no other table, so every write
        states which of the two counters it moves. The study session is why
        this is worth the ceremony: the tutor saves it on every medical turn,
        and a single shared counter made that rebuild the entire index.
        """
        self._revision += 1
        if indexed:
            self._content_revision += 1

    def _write(self, statement: str, parameters: tuple[Any, ...] = (), *, indexed: bool) -> int:
        with self._lock:
            cursor = self._connection.execute(statement, parameters)
            self._connection.commit()
            self._bump(indexed=indexed)
            return cursor.rowcount

    def _write_many(self, statement: str, rows: Iterable[tuple[Any, ...]], *, indexed: bool) -> None:
        with self._lock:
            self._connection.executemany(statement, rows)
            self._connection.commit()
            self._bump(indexed=indexed)

    def _rows(self, statement: str, parameters: tuple[Any, ...] = ()) -> list[sqlite3.Row]:
        with self._lock:
            return self._connection.execute(statement, parameters).fetchall()

    def _row(self, statement: str, parameters: tuple[Any, ...] = ()) -> sqlite3.Row | None:
        with self._lock:
            return self._connection.execute(statement, parameters).fetchone()

    def _paged_rows(
        self,
        statement: str,
        parameters: tuple[Any, ...] = (),
        *,
        page: int = _SCAN_PAGE,
    ) -> Iterator[sqlite3.Row]:
        """Yield the rows of an ordered statement page by page until it runs dry.

        The caller stops pulling once it has enough matches, so a filter that
        cannot be expressed in SQL still sees every candidate row it needs.
        """
        size = max(1, int(page))
        offset = 0
        while True:
            rows = self._rows(f"{statement} LIMIT ? OFFSET ?", (*parameters, size, offset))
            yield from rows
            if len(rows) < size:
                return
            offset += size

    @staticmethod
    def _load(row: sqlite3.Row | None) -> dict[str, Any] | None:
        if row is None:
            return None
        return json.loads(row["body"])

    # ------------------------------------------------------------------
    # documents, pages, chunks
    # ------------------------------------------------------------------

    def save_document(self, document: StudyDocument) -> StudyDocument:
        self._write(
            "INSERT INTO documents (document_id, imported_at, sha256, body) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(document_id) DO UPDATE SET body = excluded.body, sha256 = excluded.sha256, "
            "imported_at = excluded.imported_at",
            (document.document_id, document.imported_at.isoformat(), document.sha256, dumps(document)),
            indexed=True,
        )
        return document

    def get_document(self, document_id: str) -> StudyDocument | None:
        data = self._load(self._row("SELECT body FROM documents WHERE document_id = ?", (document_id,)))
        return document_from_dict(data) if data else None

    def find_document_by_sha(self, sha256: str) -> StudyDocument | None:
        data = self._load(self._row("SELECT body FROM documents WHERE sha256 = ? LIMIT 1", (sha256,)))
        return document_from_dict(data) if data else None

    def list_documents(self, *, subject: str | None = None) -> list[StudyDocument]:
        rows = self._rows("SELECT body FROM documents ORDER BY imported_at DESC")
        documents = [document_from_dict(json.loads(row["body"])) for row in rows]
        if subject:
            documents = [item for item in documents if item.subject == subject]
        return documents

    def delete_document(self, document_id: str) -> bool:
        with self._lock:
            existed = self._row("SELECT 1 FROM documents WHERE document_id = ?", (document_id,)) is not None
            for table in ("pages", "chunks", "page_images"):
                self._connection.execute(f"DELETE FROM {table} WHERE document_id = ?", (document_id,))
            self._connection.execute("DELETE FROM documents WHERE document_id = ?", (document_id,))
            self._connection.commit()
            self._bump(indexed=True)
        return existed

    def save_pages(self, pages: Iterable[DocumentPage]) -> None:
        self._write_many(
            "INSERT INTO pages (document_id, page_number, body) VALUES (?, ?, ?) "
            "ON CONFLICT(document_id, page_number) DO UPDATE SET body = excluded.body",
            ((page.document_id, page.page_number, dumps(page)) for page in pages),
            indexed=False,  # pages carry the raw text; only chunks reach the index
        )

    def save_page(self, page: DocumentPage) -> None:
        self.save_pages([page])

    def get_page(self, document_id: str, page_number: int) -> DocumentPage | None:
        data = self._load(
            self._row(
                "SELECT body FROM pages WHERE document_id = ? AND page_number = ?",
                (document_id, int(page_number)),
            )
        )
        return page_from_dict(data) if data else None

    def get_pages(self, document_id: str, *, page_from: int = 0, page_to: int = 0) -> list[DocumentPage]:
        rows = self._rows(
            "SELECT body FROM pages WHERE document_id = ? ORDER BY page_number",
            (document_id,),
        )
        pages = [page_from_dict(json.loads(row["body"])) for row in rows]
        if page_from or page_to:
            low = page_from or 1
            high = page_to or 10**9
            pages = [page for page in pages if low <= page.page_number <= high]
        return pages

    def replace_chunks(self, document_id: str, chunks: Iterable[DocumentChunk]) -> int:
        items = list(chunks)
        with self._lock:
            self._connection.execute("DELETE FROM chunks WHERE document_id = ?", (document_id,))
            self._connection.executemany(
                "INSERT INTO chunks (chunk_id, document_id, page_number, body) VALUES (?, ?, ?, ?)",
                ((chunk.chunk_id, chunk.document_id, chunk.page_number, dumps(chunk)) for chunk in items),
            )
            self._connection.commit()
            self._bump(indexed=True)
        return len(items)

    def get_chunk(self, chunk_id: str) -> DocumentChunk | None:
        data = self._load(self._row("SELECT body FROM chunks WHERE chunk_id = ?", (chunk_id,)))
        return chunk_from_dict(data) if data else None

    def chunks(
        self,
        *,
        document_ids: Iterable[str] | None = None,
        page_from: int = 0,
        page_to: int = 0,
    ) -> list[DocumentChunk]:
        ids = [str(item) for item in (document_ids or []) if item]
        if ids:
            placeholders = ",".join("?" for _ in ids)
            rows = self._rows(
                f"SELECT body FROM chunks WHERE document_id IN ({placeholders}) ORDER BY document_id, page_number, chunk_id",
                tuple(ids),
            )
        else:
            rows = self._rows("SELECT body FROM chunks ORDER BY document_id, page_number, chunk_id")
        chunks = [chunk_from_dict(json.loads(row["body"])) for row in rows]
        if page_from or page_to:
            low = page_from or 1
            high = page_to or 10**9
            chunks = [chunk for chunk in chunks if low <= chunk.page_number <= high]
        chunks.sort(key=lambda item: (item.document_id, item.page_number, item.index_in_page))
        return chunks

    def put_page_image(self, document_id: str, page_number: int, scale: float, png: bytes) -> None:
        self._write(
            "INSERT INTO page_images (document_id, page_number, scale, png) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(document_id, page_number, scale) DO UPDATE SET png = excluded.png",
            (document_id, int(page_number), float(scale), sqlite3.Binary(png)),
            indexed=False,
        )

    def get_page_image(self, document_id: str, page_number: int, scale: float) -> bytes | None:
        row = self._row(
            "SELECT png FROM page_images WHERE document_id = ? AND page_number = ? AND scale = ?",
            (document_id, int(page_number), float(scale)),
        )
        return bytes(row["png"]) if row is not None else None

    # ------------------------------------------------------------------
    # notes
    # ------------------------------------------------------------------

    def save_note(self, note: StudyNote) -> StudyNote:
        self._write(
            "INSERT INTO notes (note_id, created_at, body) VALUES (?, ?, ?) "
            "ON CONFLICT(note_id) DO UPDATE SET body = excluded.body, created_at = excluded.created_at",
            (note.note_id, note.created_at.isoformat(), dumps(note)),
            indexed=True,
        )
        return note

    def get_note(self, note_id: str) -> StudyNote | None:
        data = self._load(self._row("SELECT body FROM notes WHERE note_id = ?", (note_id,)))
        return note_from_dict(data) if data else None

    def list_notes(self, *, subject: str | None = None, limit: int = 200) -> list[StudyNote]:
        wanted = max(1, int(limit))
        notes: list[StudyNote] = []
        for row in self._paged_rows(
            "SELECT body FROM notes ORDER BY created_at DESC, note_id DESC",
            page=max(wanted, _SCAN_PAGE) if subject else wanted,
        ):
            note = note_from_dict(json.loads(row["body"]))
            if subject and note.subject != subject:
                continue
            notes.append(note)
            if len(notes) >= wanted:
                break
        return notes

    def delete_note(self, note_id: str) -> bool:
        return self._write("DELETE FROM notes WHERE note_id = ?", (note_id,), indexed=True) > 0

    # ------------------------------------------------------------------
    # questions
    # ------------------------------------------------------------------

    def save_question(self, question: Question) -> Question:
        self._write(
            "INSERT INTO questions (question_id, subject, topic_id, origin, professor_id, difficulty, created_at, body) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(question_id) DO UPDATE SET "
            "subject = excluded.subject, topic_id = excluded.topic_id, origin = excluded.origin, "
            "professor_id = excluded.professor_id, difficulty = excluded.difficulty, "
            "created_at = excluded.created_at, body = excluded.body",
            (
                question.question_id,
                question.subject,
                question.topic_id,
                question.origin,
                question.professor_id,
                int(question.difficulty),
                question.created_at.isoformat(),
                dumps(question),
            ),
            indexed=True,
        )
        return question

    def save_questions(self, questions: Iterable[Question]) -> list[Question]:
        saved = [self.save_question(question) for question in questions]
        return saved

    def get_question(self, question_id: str) -> Question | None:
        data = self._load(self._row("SELECT body FROM questions WHERE question_id = ?", (question_id,)))
        return question_from_dict(data) if data else None

    def get_questions(self, question_ids: Iterable[str]) -> list[Question]:
        found: list[Question] = []
        for question_id in question_ids:
            question = self.get_question(str(question_id))
            if question is not None:
                found.append(question)
        return found

    def delete_question(self, question_id: str) -> bool:
        return self._write("DELETE FROM questions WHERE question_id = ?", (question_id,), indexed=True) > 0

    def query_questions(
        self,
        *,
        subject: str | None = None,
        topic_id: str | None = None,
        origin: str | None = None,
        professor_id: str | None = None,
        difficulty: int | None = None,
        document_id: str | None = None,
        concept_id: str | None = None,
        text: str | None = None,
        with_answer_key: bool | None = None,
        limit: int = 200,
    ) -> list[Question]:
        clauses: list[str] = []
        parameters: list[Any] = []
        if subject:
            clauses.append("subject = ?")
            parameters.append(subject)
        if topic_id:
            clauses.append(f"(topic_id = ? OR topic_id LIKE ? ESCAPE '{_LIKE_ESCAPE}')")
            parameters.extend([topic_id, _like_prefix(topic_id + ".")])
        if origin:
            clauses.append("origin = ?")
            parameters.append(origin)
        if professor_id:
            clauses.append("professor_id = ?")
            parameters.append(professor_id)
        if difficulty:
            clauses.append("difficulty = ?")
            parameters.append(int(difficulty))
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""

        # These four live inside the JSON body, so SQL cannot narrow on them.
        refine: list[Callable[[Question], bool]] = []
        if document_id:
            refine.append(lambda q: any(ref.document_id == document_id for ref in q.references))
        if concept_id:
            refine.append(lambda q: concept_id in q.concept_ids)
        if with_answer_key is not None:
            refine.append(lambda q: q.has_answer_key == with_answer_key)
        if text:
            needle = text.casefold()
            refine.append(
                lambda q: needle in q.stem.casefold()
                or any(needle in option.text.casefold() for option in q.options)
            )

        wanted = max(1, int(limit))
        questions: list[Question] = []
        for row in self._paged_rows(
            f"SELECT body FROM questions{where} ORDER BY created_at DESC, question_id DESC",
            tuple(parameters),
            page=max(wanted, _SCAN_PAGE) if refine else wanted,
        ):
            question = question_from_dict(json.loads(row["body"]))
            if not all(keep(question) for keep in refine):
                continue
            questions.append(question)
            if len(questions) >= wanted:
                break
        return questions

    def count_questions(self) -> dict[str, int]:
        rows = self._rows("SELECT origin, COUNT(*) AS n FROM questions GROUP BY origin")
        counts = {str(row["origin"]): int(row["n"]) for row in rows}
        counts["total"] = sum(counts.values())
        return counts

    # ------------------------------------------------------------------
    # exams and attempts
    # ------------------------------------------------------------------

    def save_exam(self, exam: Exam) -> Exam:
        self._write(
            "INSERT INTO exams (exam_id, created_at, body) VALUES (?, ?, ?) "
            "ON CONFLICT(exam_id) DO UPDATE SET body = excluded.body, created_at = excluded.created_at",
            (exam.exam_id, exam.created_at.isoformat(), dumps(exam)),
            indexed=False,
        )
        return exam

    def get_exam(self, exam_id: str) -> Exam | None:
        data = self._load(self._row("SELECT body FROM exams WHERE exam_id = ?", (exam_id,)))
        return exam_from_dict(data) if data else None

    def list_exams(self, *, limit: int = 50) -> list[Exam]:
        rows = self._rows("SELECT body FROM exams ORDER BY created_at DESC LIMIT ?", (max(1, int(limit)),))
        return [exam_from_dict(json.loads(row["body"])) for row in rows]

    def delete_exam(self, exam_id: str) -> bool:
        with self._lock:
            self._connection.execute("DELETE FROM attempts WHERE exam_id = ?", (exam_id,))
            removed = self._connection.execute("DELETE FROM exams WHERE exam_id = ?", (exam_id,)).rowcount
            self._connection.commit()
            self._bump(indexed=False)
        return removed > 0

    def save_attempt(self, attempt: ExamAttempt) -> ExamAttempt:
        self._write(
            "INSERT INTO attempts (attempt_id, exam_id, started_at, body) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(attempt_id) DO UPDATE SET body = excluded.body, started_at = excluded.started_at",
            (attempt.attempt_id, attempt.exam_id, attempt.started_at.isoformat(), dumps(attempt)),
            indexed=False,
        )
        return attempt

    def get_attempt(self, attempt_id: str) -> ExamAttempt | None:
        data = self._load(self._row("SELECT body FROM attempts WHERE attempt_id = ?", (attempt_id,)))
        return attempt_from_dict(data) if data else None

    def attempts_for_exam(self, exam_id: str) -> list[ExamAttempt]:
        # rowid breaks the tie: the Windows clock is coarse enough that two
        # attempts started in the same tick share an ISO timestamp, and
        # ordering on the timestamp alone can hand back the finished one --
        # which carries the answer key -- as the current sitting.
        rows = self._rows(
            "SELECT body FROM attempts WHERE exam_id = ? ORDER BY started_at DESC, rowid DESC",
            (exam_id,),
        )
        return [attempt_from_dict(json.loads(row["body"])) for row in rows]

    def latest_attempt(self, exam_id: str) -> ExamAttempt | None:
        attempts = self.attempts_for_exam(exam_id)
        return attempts[0] if attempts else None

    def list_attempts(self, *, limit: int = 100) -> list[ExamAttempt]:
        rows = self._rows("SELECT body FROM attempts ORDER BY started_at DESC, rowid DESC LIMIT ?", (max(1, int(limit)),))
        return [attempt_from_dict(json.loads(row["body"])) for row in rows]

    # ------------------------------------------------------------------
    # mastery
    # ------------------------------------------------------------------

    def save_mastery(self, mastery: ConceptMastery) -> ConceptMastery:
        self._write(
            "INSERT INTO mastery (concept_id, body) VALUES (?, ?) ON CONFLICT(concept_id) DO UPDATE SET body = excluded.body",
            (mastery.concept_id, dumps(mastery)),
            indexed=False,
        )
        return mastery

    def get_mastery(self, concept_id: str) -> ConceptMastery | None:
        data = self._load(self._row("SELECT body FROM mastery WHERE concept_id = ?", (concept_id,)))
        return mastery_from_dict(data) if data else None

    def list_mastery(self) -> list[ConceptMastery]:
        rows = self._rows("SELECT body FROM mastery")
        return [mastery_from_dict(json.loads(row["body"])) for row in rows]

    def clear_mastery(self) -> int:
        return self._write("DELETE FROM mastery", indexed=False)

    # ------------------------------------------------------------------
    # professors
    # ------------------------------------------------------------------

    def save_professor(self, profile: ProfessorProfile) -> ProfessorProfile:
        self._write(
            "INSERT INTO professors (profile_id, body) VALUES (?, ?) ON CONFLICT(profile_id) DO UPDATE SET body = excluded.body",
            (profile.profile_id, dumps(profile)),
            indexed=False,
        )
        return profile

    def get_professor(self, profile_id: str) -> ProfessorProfile | None:
        data = self._load(self._row("SELECT body FROM professors WHERE profile_id = ?", (profile_id,)))
        return professor_from_dict(data) if data else None

    def list_professors(self) -> list[ProfessorProfile]:
        rows = self._rows("SELECT body FROM professors")
        profiles = [professor_from_dict(json.loads(row["body"])) for row in rows]
        profiles.sort(key=lambda item: item.name.casefold())
        return profiles

    def delete_professor(self, profile_id: str) -> bool:
        return self._write("DELETE FROM professors WHERE profile_id = ?", (profile_id,), indexed=False) > 0

    # ------------------------------------------------------------------
    # session
    # ------------------------------------------------------------------

    def get_session(self, session_id: str = "default") -> StudySession:
        data = self._load(self._row("SELECT body FROM sessions WHERE session_id = ?", (session_id,)))
        if data:
            return session_from_dict(data)
        return StudySession(session_id=session_id)

    def save_session(self, session: StudySession) -> StudySession:
        session.updated_at = utc_now()
        self._write(
            "INSERT INTO sessions (session_id, body) VALUES (?, ?) ON CONFLICT(session_id) DO UPDATE SET body = excluded.body",
            (session.session_id, dumps(session)),
            indexed=False,  # the session is not in the index: this is the every-turn write
        )
        return session

    # ------------------------------------------------------------------
    # learned concepts (documents can teach the graph new nodes)
    # ------------------------------------------------------------------

    def save_learned_concept(self, concept: dict[str, Any]) -> None:
        concept_id = str(concept.get("concept_id", "")).strip()
        if not concept_id:
            return
        self._write(
            "INSERT INTO learned_concepts (concept_id, body) VALUES (?, ?) ON CONFLICT(concept_id) DO UPDATE SET body = excluded.body",
            (concept_id, json.dumps(to_plain(concept), ensure_ascii=False)),
            indexed=False,
        )

    def list_learned_concepts(self) -> list[dict[str, Any]]:
        rows = self._rows("SELECT body FROM learned_concepts")
        return [json.loads(row["body"]) for row in rows]

    # ------------------------------------------------------------------
    # summary
    # ------------------------------------------------------------------

    def summary(self) -> dict[str, Any]:
        counts = {
            "documents": int(self._row("SELECT COUNT(*) AS n FROM documents")["n"]),
            "pages": int(self._row("SELECT COUNT(*) AS n FROM pages")["n"]),
            "chunks": int(self._row("SELECT COUNT(*) AS n FROM chunks")["n"]),
            "notes": int(self._row("SELECT COUNT(*) AS n FROM notes")["n"]),
            "questions": int(self._row("SELECT COUNT(*) AS n FROM questions")["n"]),
            "exams": int(self._row("SELECT COUNT(*) AS n FROM exams")["n"]),
            "attempts": int(self._row("SELECT COUNT(*) AS n FROM attempts")["n"]),
            "mastery": int(self._row("SELECT COUNT(*) AS n FROM mastery")["n"]),
            "professors": int(self._row("SELECT COUNT(*) AS n FROM professors")["n"]),
        }
        counts["persistent"] = self.persistent
        return counts
