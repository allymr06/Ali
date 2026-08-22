from __future__ import annotations

import json
import shutil
import sqlite3
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from threading import RLock
from uuid import UUID

from app.conversation.models import (
    Conversation,
    ConversationStatus,
    ConversationTurn,
    MessageRole,
)
from app.conversation.store import ConversationStore


class ConversationPersistenceError(RuntimeError):
    """Raised when durable conversation state is unreadable or incompatible."""


class SQLiteConversationStore(ConversationStore):
    """
    Durable SQLite conversation history.

    Goals:
    - survive application restart
    - preserve exact conversation/turn identity
    - remain thread-safe for desktop + async runtime usage
    - fail explicitly on corrupted rows
    """

    SCHEMA_VERSION = 1

    def __init__(
        self,
        path: str | Path,
    ) -> None:
        self.path = Path(path).expanduser().resolve()

        self.path.parent.mkdir(
            parents=True,
            exist_ok=True,
        )

        self._lock = RLock()
        self._closed = False

        try:
            self._connection = sqlite3.connect(
                str(self.path),
                timeout=5.0,
                check_same_thread=False,
            )
            self._connection.row_factory = sqlite3.Row
            self._connection.execute("PRAGMA foreign_keys = ON")
            self._connection.execute("PRAGMA journal_mode = WAL")
            self._connection.execute("PRAGMA synchronous = FULL")
            self._connection.execute("PRAGMA busy_timeout = 5000")
            self._migrate()
            self._validate_schema()
            self.check_integrity()
        except (sqlite3.DatabaseError, ConversationPersistenceError) as exc:
            connection = getattr(self, "_connection", None)
            if connection is not None:
                connection.close()
            if isinstance(exc, ConversationPersistenceError):
                raise
            raise ConversationPersistenceError(
                f"Conversation database is unreadable: {self.path}"
            ) from exc

    def _migrate(self) -> None:
        version = int(
            self._connection.execute("PRAGMA user_version").fetchone()[0]
        )
        if version > self.SCHEMA_VERSION:
            raise ConversationPersistenceError(
                f"Conversation schema {version} is newer than supported "
                f"version {self.SCHEMA_VERSION}."
            )
        if version == 0:
            with self._connection:
                self._connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS conversations (
                    conversation_id TEXT PRIMARY KEY,
                    status TEXT NOT NULL,
                    summary TEXT,
                    summary_turn_count INTEGER NOT NULL,
                    metadata_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS conversation_turns (
                    turn_id TEXT PRIMARY KEY,
                    conversation_id TEXT NOT NULL,
                    role TEXT NOT NULL,
                    content TEXT,
                    request_id TEXT,
                    response_id TEXT,
                    tool_call_id TEXT,
                    tool_calls_json TEXT NOT NULL,
                    metadata_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    ordinal INTEGER NOT NULL,

                    FOREIGN KEY(conversation_id)
                        REFERENCES conversations(conversation_id)
                        ON DELETE CASCADE
                );

                CREATE INDEX IF NOT EXISTS
                    idx_conversation_turns_conversation
                ON conversation_turns(
                    conversation_id,
                    ordinal
                );

                CREATE INDEX IF NOT EXISTS
                    idx_conversations_updated
                ON conversations(
                    updated_at DESC
                );
                PRAGMA user_version = 1;
                """
                )

    def _validate_schema(self) -> None:
        expected = {
            "conversations": {
                "conversation_id", "status", "summary", "summary_turn_count",
                "metadata_json", "created_at", "updated_at",
            },
            "conversation_turns": {
                "turn_id", "conversation_id", "role", "content", "request_id",
                "response_id", "tool_call_id", "tool_calls_json",
                "metadata_json", "created_at", "ordinal",
            },
        }
        for table, required_columns in expected.items():
            rows = self._connection.execute(
                f"PRAGMA table_info({table})"
            ).fetchall()
            columns = {str(row[1]) for row in rows}
            missing = required_columns - columns
            if missing:
                raise ConversationPersistenceError(
                    f"Conversation table '{table}' is missing columns: "
                    f"{', '.join(sorted(missing))}."
                )

    def check_integrity(self) -> None:
        with self._lock:
            quick_check = self._connection.execute(
                "PRAGMA quick_check"
            ).fetchone()[0]
            foreign_key_errors = self._connection.execute(
                "PRAGMA foreign_key_check"
            ).fetchall()
        if quick_check != "ok":
            raise ConversationPersistenceError(
                f"Conversation database integrity check failed: {quick_check}"
            )
        if foreign_key_errors:
            raise ConversationPersistenceError(
                "Conversation database contains invalid turn references."
            )

    def backup_to(self, destination: str | Path) -> Path:
        self._ensure_open()
        backup_path = Path(destination).expanduser().resolve()
        if backup_path == self.path:
            raise ValueError("Backup destination must differ from the live database.")
        backup_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = backup_path.with_suffix(backup_path.suffix + ".tmp")
        temporary.unlink(missing_ok=True)
        target = sqlite3.connect(temporary, timeout=5.0)
        try:
            with self._lock:
                self._connection.backup(target)
            if target.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                raise ConversationPersistenceError(
                    "Conversation backup failed its integrity check."
                )
        finally:
            target.close()
        temporary.replace(backup_path)
        return backup_path

    @classmethod
    def restore_from_backup(
        cls,
        backup: str | Path,
        destination: str | Path,
    ) -> SQLiteConversationStore:
        backup_path = Path(backup).expanduser().resolve()
        destination_path = Path(destination).expanduser().resolve()
        if not backup_path.is_file():
            raise FileNotFoundError(backup_path)
        destination_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination_path.with_suffix(
            destination_path.suffix + ".restore"
        )
        shutil.copy2(backup_path, temporary)
        probe = sqlite3.connect(temporary, timeout=5.0)
        try:
            if probe.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                raise ConversationPersistenceError(
                    "Conversation backup failed its integrity check."
                )
        finally:
            probe.close()
        temporary.replace(destination_path)
        return cls(destination_path)

    @staticmethod
    def _json(value) -> str:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        )

    @staticmethod
    def _loads(
        value: str | None,
        default,
    ):
        if not value:
            return default

        return json.loads(value)

    def _ensure_open(self) -> None:
        if self._closed:
            raise RuntimeError(
                "Conversation store is closed."
            )

    def save(
        self,
        conversation: Conversation,
    ) -> Conversation:
        self._ensure_open()

        with self._lock, self._connection:
            self._connection.execute(
                """
                INSERT INTO conversations (
                    conversation_id,
                    status,
                    summary,
                    summary_turn_count,
                    metadata_json,
                    created_at,
                    updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)

                ON CONFLICT(conversation_id)
                DO UPDATE SET
                    status = excluded.status,
                    summary = excluded.summary,
                    summary_turn_count =
                        excluded.summary_turn_count,
                    metadata_json =
                        excluded.metadata_json,
                    created_at =
                        excluded.created_at,
                    updated_at =
                        excluded.updated_at
                """,
                (
                    str(
                        conversation.conversation_id
                    ),
                    conversation.status.value,
                    conversation.summary,
                    conversation.summary_turn_count,
                    self._json(
                        conversation.metadata
                    ),
                    conversation.created_at.isoformat(),
                    conversation.updated_at.isoformat(),
                ),
            )

            self._connection.execute(
                """
                DELETE FROM conversation_turns
                WHERE conversation_id = ?
                """,
                (
                    str(
                        conversation.conversation_id
                    ),
                ),
            )

            self._connection.executemany(
                """
                INSERT INTO conversation_turns (
                    turn_id,
                    conversation_id,
                    role,
                    content,
                    request_id,
                    response_id,
                    tool_call_id,
                    tool_calls_json,
                    metadata_json,
                    created_at,
                    ordinal
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        str(turn.turn_id),
                        str(turn.conversation_id),
                        turn.role.value,
                        turn.content,
                        (
                            str(turn.request_id)
                            if turn.request_id
                            else None
                        ),
                        (
                            str(turn.response_id)
                            if turn.response_id
                            else None
                        ),
                        turn.tool_call_id,
                        self._json(
                            turn.tool_calls
                        ),
                        self._json(
                            turn.metadata
                        ),
                        turn.created_at.isoformat(),
                        ordinal,
                    )
                    for ordinal, turn
                    in enumerate(
                        conversation.turns
                    )
                ],
            )

        return deepcopy(conversation)

    def _load_conversation(
        self,
        row: sqlite3.Row,
    ) -> Conversation:
        conversation_id = UUID(
            row["conversation_id"]
        )

        turn_rows = self._connection.execute(
            """
            SELECT *
            FROM conversation_turns
            WHERE conversation_id = ?
            ORDER BY ordinal ASC
            """,
            (
                str(conversation_id),
            ),
        ).fetchall()

        turns = [
            ConversationTurn(
                conversation_id=conversation_id,
                role=MessageRole(
                    turn_row["role"]
                ),
                content=turn_row["content"],
                turn_id=UUID(
                    turn_row["turn_id"]
                ),
                request_id=(
                    UUID(
                        turn_row[
                            "request_id"
                        ]
                    )
                    if turn_row[
                        "request_id"
                    ]
                    else None
                ),
                response_id=(
                    UUID(
                        turn_row[
                            "response_id"
                        ]
                    )
                    if turn_row[
                        "response_id"
                    ]
                    else None
                ),
                tool_call_id=(
                    turn_row[
                        "tool_call_id"
                    ]
                ),
                tool_calls=self._loads(
                    turn_row[
                        "tool_calls_json"
                    ],
                    [],
                ),
                metadata=self._loads(
                    turn_row[
                        "metadata_json"
                    ],
                    {},
                ),
                created_at=datetime.fromisoformat(
                    turn_row[
                        "created_at"
                    ]
                ),
            )
            for turn_row
            in turn_rows
        ]

        return Conversation(
            conversation_id=conversation_id,
            status=ConversationStatus(
                row["status"]
            ),
            turns=turns,
            summary=row["summary"],
            summary_turn_count=int(
                row[
                    "summary_turn_count"
                ]
            ),
            metadata=self._loads(
                row["metadata_json"],
                {},
            ),
            created_at=datetime.fromisoformat(
                row["created_at"]
            ),
            updated_at=datetime.fromisoformat(
                row["updated_at"]
            ),
        )

    def get(
        self,
        conversation_id: UUID,
    ) -> Conversation:
        self._ensure_open()

        with self._lock:
            row = self._connection.execute(
                """
                SELECT *
                FROM conversations
                WHERE conversation_id = ?
                """,
                (
                    str(conversation_id),
                ),
            ).fetchone()

            if row is None:
                raise KeyError(
                    "Unknown conversation: "
                    f"{conversation_id}"
                )

            return deepcopy(
                self._load_conversation(
                    row
                )
            )

    def delete(
        self,
        conversation_id: UUID,
    ) -> Conversation:
        self._ensure_open()

        with self._lock:
            conversation = self.get(
                conversation_id
            )

            with self._connection:
                self._connection.execute(
                    """
                    DELETE FROM conversations
                    WHERE conversation_id = ?
                    """,
                    (
                        str(
                            conversation_id
                        ),
                    ),
                )

            return conversation

    def list(
        self,
    ) -> tuple[
        Conversation,
        ...,
    ]:
        self._ensure_open()

        with self._lock:
            rows = self._connection.execute(
                """
                SELECT *
                FROM conversations
                ORDER BY
                    updated_at DESC,
                    created_at DESC,
                    rowid DESC
                """
            ).fetchall()

            return tuple(
                deepcopy(
                    self._load_conversation(
                        row
                    )
                )
                for row
                in rows
            )

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return

            self._connection.close()
            self._closed = True

    def __enter__(
        self,
    ) -> SQLiteConversationStore:
        self._ensure_open()
        return self

    def __exit__(
        self,
        _exc_type,
        _exc,
        _tb,
    ) -> None:
        self.close()
