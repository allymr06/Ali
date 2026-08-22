from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from threading import RLock

from app.config.paths import default_state_directory, migrate_default_file


_ROOT_ID_CLEANER = re.compile(r"[^a-z0-9]+")
_ROOT_ID_PATTERN = re.compile(r"[a-z0-9][a-z0-9._-]{0,63}")


@dataclass(frozen=True, slots=True)
class FilesystemRootGrant:
    root_id: str
    path: Path

    @property
    def display_name(self) -> str:
        return self.path.name or str(self.path)


@dataclass(slots=True)
class FilesystemRootGrantStore:
    """User-owned, non-model-callable persistence for allowed roots."""

    path: Path
    _grants: dict[str, FilesystemRootGrant] = field(
        default_factory=dict,
        init=False,
        repr=False,
    )
    _lock: RLock = field(default_factory=RLock, init=False, repr=False)

    @classmethod
    def create_default(cls) -> FilesystemRootGrantStore:
        path = default_state_directory() / "filesystem_root_grants.json"
        migrate_default_file(path, "filesystem_root_grants.json")
        store = cls(path)
        store.load()
        return store

    def load(self) -> tuple[FilesystemRootGrant, ...]:
        with self._lock:
            self._grants.clear()
            if not self.path.exists():
                return ()
            try:
                payload = json.loads(self.path.read_text(encoding="utf-8"))
            except (OSError, UnicodeError, json.JSONDecodeError):
                return ()
            if not isinstance(payload, dict) or payload.get("version") != 1:
                return ()
            items = payload.get("roots")
            if not isinstance(items, list):
                return ()
            for item in items:
                if not isinstance(item, dict):
                    continue
                root_id = item.get("root_id")
                raw_path = item.get("path")
                if not isinstance(root_id, str) or not isinstance(raw_path, str):
                    continue
                normalized_id = root_id.strip().casefold()
                candidate = Path(raw_path)
                if (
                    not _ROOT_ID_PATTERN.fullmatch(normalized_id)
                    or not candidate.is_absolute()
                    or str(candidate).startswith(("\\\\", "//"))
                    or candidate.parent == candidate
                    or normalized_id in self._grants
                ):
                    continue
                self._grants[normalized_id] = FilesystemRootGrant(
                    normalized_id,
                    candidate,
                )
            return tuple(self._grants.values())

    def list(self) -> tuple[FilesystemRootGrant, ...]:
        with self._lock:
            return tuple(
                sorted(self._grants.values(), key=lambda item: item.root_id)
            )

    @staticmethod
    def _root_id(path: Path) -> str:
        label = _ROOT_ID_CLEANER.sub("-", path.name.casefold()).strip("-")
        if not label:
            label = "root"
        identity = os.path.normcase(str(path)).casefold().encode("utf-8")
        suffix = hashlib.sha256(identity).hexdigest()[:10]
        return f"{label[:32]}-{suffix}"

    def grant(self, path: str | os.PathLike[str]) -> FilesystemRootGrant:
        candidate = Path(path)
        if not candidate.is_absolute():
            raise ValueError("Allowed root must be an absolute path.")
        if str(candidate).startswith(("\\\\", "//")):
            raise ValueError("Network roots are not allowed.")
        try:
            canonical = candidate.resolve(strict=True)
        except (OSError, RuntimeError) as exc:
            raise ValueError("Allowed root must exist.") from exc
        if not canonical.is_dir():
            raise ValueError("Allowed root must be a directory.")
        if canonical.parent == canonical:
            raise ValueError("A drive root is too broad to grant.")
        grant = FilesystemRootGrant(self._root_id(canonical), canonical)
        with self._lock:
            existing = self._grants.get(grant.root_id)
            if existing is not None:
                return existing
            self._grants[grant.root_id] = grant
            try:
                self._save_locked()
            except Exception:
                self._grants.pop(grant.root_id, None)
                raise
        return grant

    def revoke(self, root_id: str) -> bool:
        normalized = root_id.strip().casefold()
        with self._lock:
            removed = self._grants.pop(normalized, None)
            if removed is None:
                return False
            try:
                self._save_locked()
            except Exception:
                self._grants[normalized] = removed
                raise
            return True

    def _save_locked(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": 1,
            "roots": [
                {"root_id": item.root_id, "path": str(item.path)}
                for item in sorted(
                    self._grants.values(), key=lambda value: value.root_id
                )
            ],
        }
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        os.replace(temporary, self.path)


__all__ = ["FilesystemRootGrant", "FilesystemRootGrantStore"]
