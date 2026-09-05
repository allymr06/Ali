"""Recoverable snapshots for the bounded filesystem service.

Before a bounded mutation replaces or removes a file, a copy of that file
is sealed here so the change can be undone. The store lives below the
JARVIS state directory, never inside a granted root, and is bounded by
entry count and total size; the oldest snapshots are pruned first.

Each snapshot is two files: ``<id>.bin`` (the exact bytes) and
``<id>.json`` (the manifest: root, relative path, size, SHA-256, reason,
time, tool). A payload is only ever handed back after its digest and size
are re-verified against the manifest, so a corrupted or tampered snapshot
can never be restored silently.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from threading import RLock
from uuid import uuid4

SNAPSHOT_DIRECTORY_NAME = "filesystem_snapshots"
SNAPSHOT_REASONS = frozenset({"overwrite", "delete", "move", "plan", "undo"})
_MANIFEST_VERSION = 1


class SnapshotError(ValueError):
    """A snapshot operation was refused; ``code`` is safe to expose."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True, slots=True)
class SnapshotRecord:
    snapshot_id: str
    root_id: str
    path: str
    size_bytes: int
    sha256: str
    reason: str
    created_at: str
    tool_name: str

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


class FilesystemSnapshotStore:
    """Bounded, verified copies of files that bounded mutations replace."""

    def __init__(
        self,
        directory: str | os.PathLike[str],
        *,
        max_entries: int = 200,
        max_total_bytes: int = 512 * 1024 * 1024,
        max_file_bytes: int = 16 * 1024 * 1024,
    ) -> None:
        for name, value in (
            ("max_entries", max_entries),
            ("max_total_bytes", max_total_bytes),
            ("max_file_bytes", max_file_bytes),
        ):
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise ValueError(f"{name} must be a positive integer.")
        if max_file_bytes > max_total_bytes:
            raise ValueError("max_file_bytes cannot exceed max_total_bytes.")
        self.directory = Path(directory).expanduser().resolve()
        self.max_entries = max_entries
        self.max_total_bytes = max_total_bytes
        self.max_file_bytes = max_file_bytes
        self._lock = RLock()

    # ------------------------------------------------------------------
    # paths
    # ------------------------------------------------------------------
    @staticmethod
    def _validate_id(snapshot_id: object) -> str:
        if not isinstance(snapshot_id, str):
            raise SnapshotError("INVALID_SNAPSHOT_ID")
        normalized = snapshot_id.strip().lower()
        if len(normalized) != 32 or any(
            character not in "0123456789abcdef" for character in normalized
        ):
            raise SnapshotError("INVALID_SNAPSHOT_ID")
        return normalized

    def _payload_path(self, snapshot_id: str) -> Path:
        return self.directory / f"{snapshot_id}.bin"

    def _manifest_path(self, snapshot_id: str) -> Path:
        return self.directory / f"{snapshot_id}.json"

    # ------------------------------------------------------------------
    # capture
    # ------------------------------------------------------------------
    def capture(
        self,
        *,
        root_id: str,
        path: str,
        source: Path,
        reason: str,
        tool_name: str,
    ) -> SnapshotRecord:
        """Seal a copy of ``source`` and return its record.

        The source is read through one stable handle: identity, size and
        modification time are checked before and after so a file that
        changes while it is being copied is refused rather than half
        captured.
        """
        if reason not in SNAPSHOT_REASONS:
            raise SnapshotError("INVALID_SNAPSHOT_REASON")
        if not isinstance(root_id, str) or not root_id:
            raise SnapshotError("INVALID_ROOT_ID")
        if not isinstance(path, str) or not path:
            raise SnapshotError("INVALID_RELATIVE_PATH")
        expected = source.stat()
        if expected.st_size > self.max_file_bytes:
            raise SnapshotError("SNAPSHOT_TOO_LARGE")
        with self._lock:
            self.directory.mkdir(parents=True, exist_ok=True)
            snapshot_id = uuid4().hex
            payload_path = self._payload_path(snapshot_id)
            manifest_path = self._manifest_path(snapshot_id)
            digest = hashlib.sha256()
            total = 0
            descriptor, temporary_name = tempfile.mkstemp(
                prefix=".snapshot-", suffix=".tmp", dir=self.directory
            )
            temporary = Path(temporary_name)
            try:
                with source.open("rb") as stream, os.fdopen(descriptor, "wb") as target:
                    before = os.fstat(stream.fileno())
                    if (
                        expected.st_dev != before.st_dev
                        or expected.st_ino != before.st_ino
                    ):
                        raise SnapshotError("FILE_IDENTITY_CHANGED")
                    while chunk := stream.read(128 * 1024):
                        total += len(chunk)
                        if total > self.max_file_bytes:
                            raise SnapshotError("SNAPSHOT_TOO_LARGE")
                        digest.update(chunk)
                        target.write(chunk)
                    target.flush()
                    os.fsync(target.fileno())
                    after = os.fstat(stream.fileno())
                if (
                    before.st_size != after.st_size
                    or before.st_mtime_ns != after.st_mtime_ns
                    or total != before.st_size
                ):
                    raise SnapshotError("FILE_CHANGED_DURING_SNAPSHOT")
                os.replace(temporary, payload_path)
            except BaseException:
                try:
                    temporary.unlink(missing_ok=True)
                except OSError:
                    pass
                raise
            record = SnapshotRecord(
                snapshot_id=snapshot_id,
                root_id=root_id,
                path=path,
                size_bytes=total,
                sha256=digest.hexdigest(),
                reason=reason,
                created_at=datetime.now(UTC).isoformat(),
                tool_name=tool_name,
            )
            try:
                self._write_manifest(manifest_path, record)
                self._verify_payload(record)
            except BaseException:
                for stale in (payload_path, manifest_path):
                    try:
                        stale.unlink(missing_ok=True)
                    except OSError:
                        pass
                raise
            self.prune(keep=snapshot_id)
            return record

    def _write_manifest(self, manifest_path: Path, record: SnapshotRecord) -> None:
        payload = {"version": _MANIFEST_VERSION, **record.to_dict()}
        temporary = manifest_path.with_suffix(".json.tmp")
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        os.replace(temporary, manifest_path)

    # ------------------------------------------------------------------
    # read back
    # ------------------------------------------------------------------
    def _load_manifest(self, manifest_path: Path) -> SnapshotRecord | None:
        try:
            payload = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            return None
        if not isinstance(payload, dict) or payload.get("version") != _MANIFEST_VERSION:
            return None
        try:
            record = SnapshotRecord(
                snapshot_id=self._validate_id(payload.get("snapshot_id")),
                root_id=str(payload["root_id"]),
                path=str(payload["path"]),
                size_bytes=int(payload["size_bytes"]),
                sha256=str(payload["sha256"]),
                reason=str(payload["reason"]),
                created_at=str(payload["created_at"]),
                tool_name=str(payload["tool_name"]),
            )
        except (KeyError, TypeError, ValueError, SnapshotError):
            return None
        if record.snapshot_id != manifest_path.stem or record.size_bytes < 0:
            return None
        return record

    def list(self) -> tuple[SnapshotRecord, ...]:
        """Every readable snapshot, newest first."""
        with self._lock:
            if not self.directory.is_dir():
                return ()
            records = []
            for manifest_path in self.directory.glob("*.json"):
                record = self._load_manifest(manifest_path)
                if record is not None and self._payload_path(record.snapshot_id).is_file():
                    records.append(record)
        records.sort(key=lambda item: (item.created_at, item.snapshot_id), reverse=True)
        return tuple(records)

    def get(self, snapshot_id: str) -> SnapshotRecord | None:
        normalized = self._validate_id(snapshot_id)
        with self._lock:
            manifest_path = self._manifest_path(normalized)
            if not manifest_path.is_file():
                return None
            record = self._load_manifest(manifest_path)
            if record is None or not self._payload_path(normalized).is_file():
                return None
            return record

    def _verify_payload(self, record: SnapshotRecord) -> None:
        payload_path = self._payload_path(record.snapshot_id)
        digest = hashlib.sha256()
        total = 0
        with payload_path.open("rb") as stream:
            while chunk := stream.read(128 * 1024):
                total += len(chunk)
                if total > self.max_file_bytes:
                    raise SnapshotError("SNAPSHOT_CORRUPT")
                digest.update(chunk)
        if total != record.size_bytes or digest.hexdigest() != record.sha256:
            raise SnapshotError("SNAPSHOT_CORRUPT")

    def read_payload(self, snapshot_id: str) -> tuple[SnapshotRecord, bytes]:
        """Return the verified bytes of a snapshot."""
        record = self.get(snapshot_id)
        if record is None:
            raise SnapshotError("SNAPSHOT_NOT_FOUND")
        with self._lock:
            self._verify_payload(record)
            payload = self._payload_path(record.snapshot_id).read_bytes()
        if len(payload) != record.size_bytes or hashlib.sha256(payload).hexdigest() != record.sha256:
            raise SnapshotError("SNAPSHOT_CORRUPT")
        return record, payload

    # ------------------------------------------------------------------
    # housekeeping
    # ------------------------------------------------------------------
    def discard(self, snapshot_id: str) -> bool:
        normalized = self._validate_id(snapshot_id)
        with self._lock:
            removed = False
            for candidate in (self._manifest_path(normalized), self._payload_path(normalized)):
                try:
                    candidate.unlink()
                    removed = True
                except FileNotFoundError:
                    continue
            return removed

    def usage(self) -> dict[str, int]:
        records = self.list()
        return {
            "entries": len(records),
            "bytes": sum(record.size_bytes for record in records),
            "max_entries": self.max_entries,
            "max_total_bytes": self.max_total_bytes,
        }

    def prune(self, *, keep: str | None = None) -> tuple[str, ...]:
        """Drop the oldest snapshots until count and size fit the bounds."""
        removed: list[str] = []
        with self._lock:
            records = list(self.list())   # newest first
            total = sum(record.size_bytes for record in records)
            while records and (
                len(records) > self.max_entries or total > self.max_total_bytes
            ):
                oldest = records[-1]
                if oldest.snapshot_id == keep and len(records) == 1:
                    break
                records.pop()
                total -= oldest.size_bytes
                if self.discard(oldest.snapshot_id):
                    removed.append(oldest.snapshot_id)
        return tuple(removed)


__all__ = [
    "SNAPSHOT_DIRECTORY_NAME",
    "SNAPSHOT_REASONS",
    "FilesystemSnapshotStore",
    "SnapshotError",
    "SnapshotRecord",
]
