from __future__ import annotations

import fnmatch
import hashlib
import hmac
import json
import os
import re
import stat
import tempfile
import time
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath, PureWindowsPath
from threading import RLock
from typing import Any, Callable
from uuid import uuid4

from app.config.paths import default_state_directory
from app.core.models import (
    RiskLevel,
    ToolDefinition,
    ToolExecutionStatus,
    ToolResult,
)
from app.platform.windows.snapshots import (
    FilesystemSnapshotStore,
    SnapshotError,
    SnapshotRecord,
)
from app.tools.executor import ToolExecutor


_ROOT_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,63}$")
_PATH_SEPARATOR_PATTERN = re.compile(r"[\\/]+")
_SOURCE = "platform:windows:bounded-filesystem"
# Directory names that mark operating-system internals wherever they sit.
_CRITICAL_COMPONENT_NAMES = frozenset({"$recycle.bin", "system volume information"})
_PLAN_ACTIONS = frozenset({"write", "create_directory", "copy", "move", "delete"})
_MAX_SEARCH_QUERY_CHARACTERS = 200


class FilesystemPolicyError(ValueError):
    """A fail-closed rejection which is safe to expose as an error code."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class FilesystemMutationUncertain(RuntimeError):
    """A modifying operation started but its postcondition was not proven."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True, slots=True)
class AllowedFilesystemRoot:
    """One explicitly granted filesystem root and its canonical target."""

    root_id: str
    configured_path: Path
    canonical_path: Path


@dataclass(frozen=True, slots=True)
class _RootIndex:
    """A bounded name index of one root, rebuilt when it goes stale."""

    built_at: float
    entries: tuple[tuple[str, str, int | None], ...]
    truncated: bool


@dataclass(frozen=True, slots=True)
class _FilesystemPlan:
    """A validated dry run, applied only with its digest and before expiry."""

    plan_id: str
    root_id: str
    digest: str
    operations: tuple[dict[str, Any], ...]
    fingerprints: tuple[tuple[str, tuple[Any, ...]], ...]
    created_at: float
    target_count: int


def default_critical_paths() -> tuple[tuple[Path, bool], ...]:
    """Directories JARVIS must never be granted or reach.

    Each entry is ``(path, subtree)``: with ``subtree`` the whole tree is
    blocked (Windows, Program Files, ProgramData, the JARVIS state
    directory); otherwise only that exact directory is blocked (the user
    profile root, whose ordinary subfolders remain grantable).
    """
    candidates: list[tuple[str | None, bool]] = [
        (os.environ.get("SystemRoot") or os.environ.get("WINDIR"), True),
        (os.environ.get("ProgramFiles"), True),
        (os.environ.get("ProgramFiles(x86)"), True),
        (os.environ.get("ProgramW6432"), True),
        (os.environ.get("ProgramData"), True),
        (os.environ.get("USERPROFILE"), False),
    ]
    paths: list[tuple[Path, bool]] = []
    try:
        paths.append((default_state_directory(), True))
    except Exception:
        pass
    for raw, subtree in candidates:
        if not raw:
            continue
        try:
            paths.append((Path(raw).expanduser().resolve(strict=False), subtree))
        except (OSError, RuntimeError):
            continue
    return tuple(paths)


class BoundedFilesystemService:
    """Filesystem access constrained to explicit roots and relative paths.

    No roots are granted by default. Root grants are configuration-time state;
    they are intentionally not exposed as model-callable tools. With a
    :class:`FilesystemSnapshotStore` attached, every file a mutation replaces
    or removes is sealed first so the change can be undone.
    """

    def __init__(
        self,
        allowed_roots: Mapping[str, str | os.PathLike[str]] | None = None,
        *,
        max_read_page_bytes: int = 256 * 1024,
        max_write_bytes: int = 4 * 1024 * 1024,
        max_transfer_bytes: int = 16 * 1024 * 1024,
        max_list_entries: int = 200,
        max_directory_scan: int = 10_000,
        max_index_entries: int = 20_000,
        max_plan_operations: int = 50,
        index_refresh_seconds: float = 60.0,
        plan_ttl_seconds: float = 600.0,
        snapshots: FilesystemSnapshotStore | None = None,
        critical_paths: Iterable[tuple[Path, bool]] | None = None,
    ) -> None:
        self._validate_positive_limit(
            "max_read_page_bytes",
            max_read_page_bytes,
        )
        self._validate_positive_limit("max_write_bytes", max_write_bytes)
        self._validate_positive_limit(
            "max_transfer_bytes",
            max_transfer_bytes,
        )
        self._validate_positive_limit("max_list_entries", max_list_entries)
        self._validate_positive_limit("max_directory_scan", max_directory_scan)
        self._validate_positive_limit("max_index_entries", max_index_entries)
        self._validate_positive_limit("max_plan_operations", max_plan_operations)
        if max_directory_scan < max_list_entries:
            raise ValueError(
                "max_directory_scan cannot be smaller than max_list_entries."
            )
        for name, value in (
            ("index_refresh_seconds", index_refresh_seconds),
            ("plan_ttl_seconds", plan_ttl_seconds),
        ):
            if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
                raise ValueError(f"{name} must be a positive number.")

        self.max_read_page_bytes = max_read_page_bytes
        self.max_write_bytes = max_write_bytes
        self.max_transfer_bytes = max_transfer_bytes
        self.max_list_entries = max_list_entries
        self.max_directory_scan = max_directory_scan
        self.max_index_entries = max_index_entries
        self.max_plan_operations = max_plan_operations
        self.index_refresh_seconds = float(index_refresh_seconds)
        self.plan_ttl_seconds = float(plan_ttl_seconds)
        self._snapshots = snapshots
        self._critical: tuple[tuple[Path, bool], ...] = tuple(
            critical_paths if critical_paths is not None else default_critical_paths()
        )
        self._roots: dict[str, AllowedFilesystemRoot] = {}
        self._indexes: dict[str, _RootIndex] = {}
        self._plans: dict[str, _FilesystemPlan] = {}
        self._lock = RLock()

        for root_id, path in (allowed_roots or {}).items():
            self.allow_root(root_id, path)

    @property
    def snapshots(self) -> FilesystemSnapshotStore | None:
        return self._snapshots

    @staticmethod
    def _validate_positive_limit(name: str, value: int) -> None:
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            raise ValueError(f"{name} must be a positive integer.")

    @staticmethod
    def _normalize_root_id(root_id: str) -> str:
        if not isinstance(root_id, str):
            raise FilesystemPolicyError("INVALID_ROOT_ID")
        normalized = root_id.strip().lower()
        if not _ROOT_ID_PATTERN.fullmatch(normalized):
            raise FilesystemPolicyError("INVALID_ROOT_ID")
        return normalized

    # ------------------------------------------------------------------
    # critical system directories
    # ------------------------------------------------------------------
    def _is_critical(self, candidate: Path) -> bool:
        parents = set(candidate.parents)
        for path, subtree in self._critical:
            if candidate == path or (subtree and path in parents):
                return True
        return any(
            part.casefold() in _CRITICAL_COMPONENT_NAMES for part in candidate.parts
        )

    def _assert_not_critical(self, candidate: Path) -> None:
        if self._is_critical(candidate):
            raise FilesystemPolicyError("CRITICAL_PATH_REJECTED")

    def allow_root(
        self,
        root_id: str,
        path: str | os.PathLike[str],
    ) -> AllowedFilesystemRoot:
        """Grant a root from trusted application configuration."""
        normalized_id = self._normalize_root_id(root_id)
        configured = Path(path).expanduser()
        if not configured.is_absolute():
            raise ValueError("An allowed filesystem root must be absolute.")
        if str(configured).startswith(("\\\\", "//")):
            raise ValueError("Network filesystem roots are not allowed.")
        try:
            canonical = configured.resolve(strict=True)
        except (OSError, RuntimeError) as exc:
            raise ValueError("An allowed filesystem root must exist.") from exc
        if not canonical.is_dir():
            raise ValueError("An allowed filesystem root must be a directory.")
        if canonical.parent == canonical:
            raise ValueError("A drive root is too broad to grant.")
        if self._is_critical(canonical):
            raise ValueError("A critical system directory cannot be granted.")
        if self._snapshots is not None and (
            canonical == self._snapshots.directory
            or self._snapshots.directory in canonical.parents
            or canonical in self._snapshots.directory.parents
        ):
            raise ValueError("The snapshot store cannot be inside a granted root.")
        if normalized_id in self._roots:
            raise ValueError("An allowed filesystem root ID cannot be replaced.")
        grant = AllowedFilesystemRoot(
            root_id=normalized_id,
            configured_path=configured,
            canonical_path=canonical,
        )
        self._roots[normalized_id] = grant
        return grant

    def revoke_root(self, root_id: str) -> bool:
        """Revoke an existing configuration-time root grant."""
        try:
            normalized_id = self._normalize_root_id(root_id)
        except FilesystemPolicyError:
            return False
        with self._lock:
            self._indexes.pop(normalized_id, None)
            for plan_id in [
                identifier
                for identifier, plan in self._plans.items()
                if plan.root_id == normalized_id
            ]:
                self._plans.pop(plan_id, None)
        return self._roots.pop(normalized_id, None) is not None

    def _root(self, root_id: str) -> AllowedFilesystemRoot:
        normalized_id = self._normalize_root_id(root_id)
        root = self._roots.get(normalized_id)
        if root is None:
            raise FilesystemPolicyError("ROOT_NOT_ALLOWED")
        try:
            current = root.configured_path.resolve(strict=True)
        except (OSError, RuntimeError) as exc:
            raise FilesystemPolicyError("ROOT_UNAVAILABLE") from exc
        if current != root.canonical_path or not current.is_dir():
            raise FilesystemPolicyError("ROOT_CHANGED")
        return root

    @staticmethod
    def _relative_parts(relative_path: str, *, allow_empty: bool) -> tuple[str, ...]:
        if not isinstance(relative_path, str) or "\x00" in relative_path:
            raise FilesystemPolicyError("INVALID_RELATIVE_PATH")
        if not relative_path:
            if allow_empty:
                return ()
            raise FilesystemPolicyError("EMPTY_RELATIVE_PATH")

        windows_path = PureWindowsPath(relative_path)
        posix_path = PurePosixPath(relative_path)
        if (
            windows_path.is_absolute()
            or bool(windows_path.drive)
            or posix_path.is_absolute()
        ):
            raise FilesystemPolicyError("ABSOLUTE_PATH_REJECTED")

        parts = tuple(_PATH_SEPARATOR_PATTERN.split(relative_path))
        if any(not part or part in {".", ".."} for part in parts):
            raise FilesystemPolicyError("PATH_TRAVERSAL_REJECTED")
        for part in parts:
            contains_windows_invalid_character = any(
                character in '<>:"|?*' or ord(character) < 32
                for character in part
            )
            if (
                contains_windows_invalid_character
                or part.endswith((" ", "."))
                or PureWindowsPath(part).is_reserved()
            ):
                raise FilesystemPolicyError("UNSAFE_PATH_COMPONENT")
        return parts

    @staticmethod
    def _assert_within(root: Path, candidate: Path) -> None:
        try:
            candidate.relative_to(root)
        except ValueError as exc:
            raise FilesystemPolicyError("ROOT_ESCAPE_REJECTED") from exc

    def _resolve_existing(
        self,
        root_id: str,
        relative_path: str,
        *,
        allow_empty: bool = False,
    ) -> tuple[AllowedFilesystemRoot, Path, str]:
        root = self._root(root_id)
        parts = self._relative_parts(relative_path, allow_empty=allow_empty)
        requested = root.canonical_path.joinpath(*parts)
        self._assert_no_reparse_below_root(
            root.canonical_path,
            requested,
            allow_missing=False,
        )
        try:
            resolved = requested.resolve(strict=True)
        except FileNotFoundError:
            raise
        except (OSError, RuntimeError) as exc:
            raise FilesystemPolicyError("PATH_RESOLUTION_REJECTED") from exc
        self._assert_within(root.canonical_path, resolved)
        return root, resolved, "/".join(parts)

    @staticmethod
    def _is_link_or_reparse(path: Path, *, missing_ok: bool = False) -> bool:
        try:
            details = path.lstat()
        except FileNotFoundError:
            if missing_ok:
                return False
            raise
        except OSError as exc:
            raise FilesystemPolicyError("REPARSE_CHECK_FAILED") from exc
        if stat.S_ISLNK(details.st_mode):
            return True
        attributes = getattr(details, "st_file_attributes", 0)
        reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
        return bool(attributes & reparse_flag)

    @classmethod
    def _assert_no_reparse_below_root(
        cls,
        root: Path,
        candidate: Path,
        *,
        allow_missing: bool,
    ) -> None:
        cls._assert_within(root, candidate)
        current = root
        for part in candidate.relative_to(root).parts:
            current /= part
            try:
                if cls._is_link_or_reparse(current):
                    raise FilesystemPolicyError("REPARSE_PATH_REJECTED")
            except FileNotFoundError:
                if allow_missing:
                    return
                raise

    def _resolve_destination(
        self,
        root_id: str,
        relative_path: str,
    ) -> tuple[AllowedFilesystemRoot, Path, str]:
        root = self._root(root_id)
        parts = self._relative_parts(relative_path, allow_empty=False)
        parent_request = root.canonical_path.joinpath(*parts[:-1])
        self._assert_no_reparse_below_root(
            root.canonical_path,
            parent_request,
            allow_missing=False,
        )
        try:
            parent = parent_request.resolve(strict=True)
        except FileNotFoundError:
            raise
        except (OSError, RuntimeError) as exc:
            raise FilesystemPolicyError("PATH_RESOLUTION_REJECTED") from exc
        self._assert_within(root.canonical_path, parent)
        if not parent.is_dir():
            raise NotADirectoryError

        destination = parent / parts[-1]
        if destination.exists() or destination.is_symlink():
            if self._is_link_or_reparse(destination):
                raise FilesystemPolicyError("REPARSE_TARGET_REJECTED")
            try:
                resolved = destination.resolve(strict=True)
            except (OSError, RuntimeError) as exc:
                raise FilesystemPolicyError("PATH_RESOLUTION_REJECTED") from exc
            self._assert_within(root.canonical_path, resolved)
            destination = resolved
        else:
            self._assert_within(
                root.canonical_path,
                destination.resolve(strict=False),
            )
        return root, destination, "/".join(parts)

    @staticmethod
    def _safe_result(
        tool_name: str,
        operation: Callable[[], dict[str, Any]],
        success_message: str,
    ) -> ToolResult:
        try:
            data = operation()
        except (FilesystemPolicyError, SnapshotError) as exc:
            return ToolResult(
                status=ToolExecutionStatus.BLOCKED,
                tool_name=tool_name,
                message="Filesystem policy blocked the request.",
                data={"error_code": exc.code},
                error=exc.code,
                verified=False,
            )
        except FilesystemMutationUncertain as exc:
            return ToolResult(
                status=ToolExecutionStatus.PARTIAL,
                tool_name=tool_name,
                message="Filesystem mutation could not be fully verified.",
                data={"error_code": exc.code},
                error=exc.code,
                verified=False,
                side_effects_may_continue=True,
            )
        except FileNotFoundError:
            code = "PATH_NOT_FOUND"
        except FileExistsError:
            code = "DESTINATION_EXISTS"
        except IsADirectoryError:
            code = "EXPECTED_FILE"
        except NotADirectoryError:
            code = "EXPECTED_DIRECTORY"
        except UnicodeDecodeError:
            code = "NOT_UTF8_TEXT"
        except (OSError, ValueError):
            code = "FILESYSTEM_OPERATION_FAILED"
        else:
            return ToolResult(
                status=ToolExecutionStatus.SUCCESS,
                tool_name=tool_name,
                message=success_message,
                data=data,
                verified=True,
            )

        return ToolResult(
            status=ToolExecutionStatus.FAILED,
            tool_name=tool_name,
            message="Filesystem operation failed safely.",
            data={"error_code": code},
            error=code,
            verified=False,
        )

    def list_allowed_roots(self) -> ToolResult:
        def operation() -> dict[str, Any]:
            return {
                "roots": [
                    {"root_id": root_id}
                    for root_id in sorted(self._roots)
                ],
                "absolute_paths_exposed": False,
                "verification": {"strategy": "configured_grant_snapshot"},
            }

        return self._safe_result(
            "list_allowed_file_roots",
            operation,
            "Allowed filesystem root identifiers observed.",
        )

    def list_directory(
        self,
        root_id: str,
        path: str = "",
        offset: int = 0,
        limit: int = 100,
    ) -> ToolResult:
        def operation() -> dict[str, Any]:
            if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0:
                raise FilesystemPolicyError("INVALID_PAGE_OFFSET")
            if (
                isinstance(limit, bool)
                or not isinstance(limit, int)
                or not 1 <= limit <= self.max_list_entries
            ):
                raise FilesystemPolicyError("INVALID_PAGE_LIMIT")
            root, directory, safe_path = self._resolve_existing(
                root_id,
                path,
                allow_empty=True,
            )
            if not directory.is_dir():
                raise NotADirectoryError
            self._assert_no_reparse_below_root(
                root.canonical_path,
                directory,
                allow_missing=False,
            )

            entries: list[dict[str, Any]] = []
            with os.scandir(directory) as iterator:
                for index, entry in enumerate(iterator):
                    if index >= self.max_directory_scan:
                        raise FilesystemPolicyError("DIRECTORY_SCAN_LIMIT_EXCEEDED")
                    kind, size = self._classify_entry(entry)
                    entries.append(
                        {"name": entry.name, "kind": kind, "size_bytes": size}
                    )

            entries.sort(key=lambda item: str(item["name"]).casefold())
            page = entries[offset : offset + limit]
            next_offset = offset + len(page)
            if next_offset >= len(entries):
                next_offset = None
            return {
                "root_id": root.root_id,
                "path": safe_path,
                "entries": page,
                "offset": offset,
                "limit": limit,
                "next_offset": next_offset,
                "total_entries": len(entries),
                "verification": {
                    "strategy": "bounded_directory_snapshot",
                    "entries_returned": len(page),
                },
            }

        return self._safe_result(
            "list_directory",
            operation,
            "Bounded directory page observed.",
        )

    @staticmethod
    def _classify_entry(entry: os.DirEntry[str]) -> tuple[str, int | None]:
        details = entry.stat(follow_symlinks=False)
        is_link = entry.is_symlink() or bool(
            getattr(details, "st_file_attributes", 0)
            & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
        )
        if is_link:
            return "link", None
        if stat.S_ISDIR(details.st_mode):
            return "directory", None
        if stat.S_ISREG(details.st_mode):
            return "file", details.st_size
        return "other", None

    def read_text_file(
        self,
        root_id: str,
        path: str,
        offset_bytes: int = 0,
        max_bytes: int = 65_536,
    ) -> ToolResult:
        def operation() -> dict[str, Any]:
            if (
                isinstance(offset_bytes, bool)
                or not isinstance(offset_bytes, int)
                or offset_bytes < 0
            ):
                raise FilesystemPolicyError("INVALID_PAGE_OFFSET")
            if (
                isinstance(max_bytes, bool)
                or not isinstance(max_bytes, int)
                or not 4 <= max_bytes <= self.max_read_page_bytes
            ):
                raise FilesystemPolicyError("INVALID_PAGE_LIMIT")
            root, target, safe_path = self._resolve_existing(root_id, path)
            if not target.is_file():
                raise IsADirectoryError

            self._assert_no_reparse_below_root(
                root.canonical_path,
                target,
                allow_missing=False,
            )
            expected = target.stat()
            with target.open("rb") as stream:
                before = os.fstat(stream.fileno())
                if (
                    expected.st_dev != before.st_dev
                    or expected.st_ino != before.st_ino
                ):
                    raise FilesystemPolicyError("FILE_IDENTITY_CHANGED")
                if offset_bytes > before.st_size:
                    raise FilesystemPolicyError("PAGE_OFFSET_OUT_OF_RANGE")
                stream.seek(offset_bytes)
                raw = stream.read(max_bytes)
                while raw:
                    try:
                        content = raw.decode("utf-8")
                        break
                    except UnicodeDecodeError as exc:
                        reached_file_end = (
                            offset_bytes + len(raw) >= before.st_size
                        )
                        if (
                            exc.reason != "unexpected end of data"
                            or exc.end != len(raw)
                            or reached_file_end
                        ):
                            raise
                        raw = raw[: exc.start]
                else:
                    content = ""
                after = os.fstat(stream.fileno())
            if (
                before.st_size != after.st_size
                or before.st_mtime_ns != after.st_mtime_ns
            ):
                raise FilesystemPolicyError("FILE_CHANGED_DURING_READ")

            next_offset = offset_bytes + len(raw)
            truncated = next_offset < before.st_size
            return {
                "root_id": root.root_id,
                "path": safe_path,
                "content": content,
                "offset_bytes": offset_bytes,
                "bytes_returned": len(raw),
                "total_bytes": before.st_size,
                "next_offset_bytes": next_offset if truncated else None,
                "truncated": truncated,
                "verification": {
                    "strategy": "stable_file_handle_and_page_sha256",
                    "page_sha256": hashlib.sha256(raw).hexdigest(),
                },
            }

        return self._safe_result(
            "read_text_file",
            operation,
            "Bounded UTF-8 file page observed.",
        )

    # ------------------------------------------------------------------
    # search: a bounded name index per root
    # ------------------------------------------------------------------
    def _build_index(self, root: AllowedFilesystemRoot) -> _RootIndex:
        entries: list[tuple[str, str, int | None]] = []
        truncated = False
        pending: list[tuple[Path, str]] = [(root.canonical_path, "")]
        while pending and not truncated:
            directory, prefix = pending.pop()
            try:
                iterator = os.scandir(directory)
            except OSError:
                continue
            with iterator:
                for entry in iterator:
                    if len(entries) >= self.max_index_entries:
                        truncated = True
                        break
                    try:
                        kind, size = self._classify_entry(entry)
                    except OSError:
                        continue
                    relative = f"{prefix}{entry.name}"
                    entries.append((relative, kind, size))
                    # Links and reparse points are listed but never followed,
                    # so the index can never reach outside the root.
                    if kind == "directory":
                        pending.append((Path(entry.path), relative + "/"))
        entries.sort(key=lambda item: item[0].casefold())
        return _RootIndex(built_at=time.monotonic(), entries=tuple(entries), truncated=truncated)

    def _index_for(self, root: AllowedFilesystemRoot, *, refresh: bool) -> _RootIndex:
        with self._lock:
            cached = self._indexes.get(root.root_id)
            if (
                cached is not None
                and not refresh
                and time.monotonic() - cached.built_at < self.index_refresh_seconds
            ):
                return cached
        index = self._build_index(root)
        with self._lock:
            self._indexes[root.root_id] = index
        return index

    def search_files(
        self,
        root_id: str,
        query: str,
        path: str = "",
        limit: int = 50,
        refresh: bool = False,
    ) -> ToolResult:
        def operation() -> dict[str, Any]:
            if (
                not isinstance(query, str)
                or not query.strip()
                or len(query) > _MAX_SEARCH_QUERY_CHARACTERS
                or "\x00" in query
            ):
                raise FilesystemPolicyError("INVALID_SEARCH_QUERY")
            if (
                isinstance(limit, bool)
                or not isinstance(limit, int)
                or not 1 <= limit <= self.max_list_entries
            ):
                raise FilesystemPolicyError("INVALID_PAGE_LIMIT")
            if not isinstance(refresh, bool):
                raise FilesystemPolicyError("INVALID_REFRESH_FLAG")
            root = self._root(root_id)
            parts = self._relative_parts(path, allow_empty=True)
            prefix = "/".join(parts)
            if prefix:
                scope = root.canonical_path.joinpath(*parts)
                self._assert_no_reparse_below_root(
                    root.canonical_path, scope, allow_missing=False
                )
                if not scope.is_dir():
                    raise NotADirectoryError
                prefix += "/"

            index = self._index_for(root, refresh=refresh)
            needle = query.strip().casefold()
            wildcard = any(character in needle for character in "*?[")
            against_path = "/" in needle

            def matches(relative: str) -> bool:
                subject = relative.casefold() if against_path else relative.rsplit("/", 1)[-1].casefold()
                if wildcard:
                    return fnmatch.fnmatchcase(subject, needle)
                return needle in subject

            found = [
                {"path": relative, "kind": kind, "size_bytes": size}
                for relative, kind, size in index.entries
                if relative.startswith(prefix) and matches(relative)
            ]
            return {
                "root_id": root.root_id,
                "query": query.strip(),
                "path": prefix.rstrip("/"),
                "matches": found[:limit],
                "total_matches": len(found),
                "limit": limit,
                "truncated_results": len(found) > limit,
                "indexed_entries": len(index.entries),
                "index_truncated": index.truncated,
                "index_age_seconds": round(time.monotonic() - index.built_at, 1),
                "verification": {
                    "strategy": "bounded_name_index",
                    "links_followed": False,
                },
            }

        return self._safe_result(
            "search_files",
            operation,
            "Bounded filesystem search completed.",
        )

    # ------------------------------------------------------------------
    # shared verification helpers
    # ------------------------------------------------------------------
    @staticmethod
    def _sha256_file(path: Path, *, maximum_bytes: int) -> tuple[str, int]:
        digest = hashlib.sha256()
        total = 0
        expected = path.stat()
        with path.open("rb") as stream:
            observed = os.fstat(stream.fileno())
            if (
                expected.st_dev != observed.st_dev
                or expected.st_ino != observed.st_ino
            ):
                raise FilesystemPolicyError("FILE_IDENTITY_CHANGED")
            while chunk := stream.read(128 * 1024):
                total += len(chunk)
                if total > maximum_bytes:
                    raise FilesystemPolicyError("TRANSFER_SIZE_LIMIT_EXCEEDED")
                digest.update(chunk)
        return digest.hexdigest(), total

    @staticmethod
    def _read_stable_payload(
        path: Path,
        *,
        maximum_bytes: int,
    ) -> tuple[bytes, str]:
        expected = path.stat()
        with path.open("rb") as stream:
            before = os.fstat(stream.fileno())
            if (
                expected.st_dev != before.st_dev
                or expected.st_ino != before.st_ino
            ):
                raise FilesystemPolicyError("FILE_IDENTITY_CHANGED")
            if before.st_size > maximum_bytes:
                raise FilesystemPolicyError("TRANSFER_SIZE_LIMIT_EXCEEDED")
            payload = stream.read(maximum_bytes + 1)
            after = os.fstat(stream.fileno())
        if len(payload) > maximum_bytes:
            raise FilesystemPolicyError("TRANSFER_SIZE_LIMIT_EXCEEDED")
        if (
            before.st_size != after.st_size
            or before.st_mtime_ns != after.st_mtime_ns
        ):
            raise FilesystemPolicyError("FILE_CHANGED_DURING_READ")
        return payload, hashlib.sha256(payload).hexdigest()

    @staticmethod
    def _atomic_write(destination: Path, payload: bytes, *, overwrite: bool) -> None:
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=".jarvis-write-",
            suffix=".tmp",
            dir=destination.parent,
        )
        temporary = Path(temporary_name)
        mutation_completed = False
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            if overwrite:
                os.replace(temporary, destination)
                mutation_completed = True
            else:
                os.link(temporary, destination)
                mutation_completed = True
                temporary.unlink()
        except FilesystemMutationUncertain:
            raise
        except OSError as exc:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
            if mutation_completed:
                raise FilesystemMutationUncertain(
                    "ATOMIC_WRITE_CLEANUP_UNCERTAIN"
                ) from exc
            raise
        else:
            try:
                temporary.unlink(missing_ok=True)
            except OSError as exc:
                raise FilesystemMutationUncertain(
                    "ATOMIC_WRITE_CLEANUP_UNCERTAIN"
                ) from exc

    def _snapshot_before_change(
        self,
        root: AllowedFilesystemRoot,
        target: Path,
        safe_path: str,
        *,
        reason: str,
        tool_name: str,
    ) -> SnapshotRecord | None:
        """Seal the file at ``target`` before it is replaced or removed.

        Returns ``None`` when nothing needs sealing (no file there, or no
        store attached). A file the store cannot hold blocks the mutation:
        better refused than irreversibly changed.
        """
        if self._snapshots is None or not target.is_file():
            return None
        self._assert_no_reparse_below_root(root.canonical_path, target, allow_missing=False)
        return self._snapshots.capture(
            root_id=root.root_id,
            path=safe_path,
            source=target,
            reason=reason,
            tool_name=tool_name,
        )

    @staticmethod
    def _snapshot_summary(record: SnapshotRecord | None) -> dict[str, Any] | None:
        if record is None:
            return None
        return {
            "snapshot_id": record.snapshot_id,
            "sha256": record.sha256,
            "size_bytes": record.size_bytes,
            "reason": record.reason,
        }

    # ------------------------------------------------------------------
    # mutations
    # ------------------------------------------------------------------
    def write_text_file(
        self,
        root_id: str,
        path: str,
        content: str,
        overwrite: bool = False,
    ) -> ToolResult:
        def operation() -> dict[str, Any]:
            if not isinstance(content, str):
                raise FilesystemPolicyError("CONTENT_MUST_BE_TEXT")
            if not isinstance(overwrite, bool):
                raise FilesystemPolicyError("INVALID_OVERWRITE_FLAG")
            payload = content.encode("utf-8")
            if len(payload) > self.max_write_bytes:
                raise FilesystemPolicyError("WRITE_SIZE_LIMIT_EXCEEDED")
            root, destination, safe_path = self._resolve_destination(root_id, path)
            if destination.exists() and not overwrite:
                raise FileExistsError
            if destination.exists() and not destination.is_file():
                raise IsADirectoryError

            self._assert_no_reparse_below_root(
                root.canonical_path,
                destination.parent,
                allow_missing=False,
            )
            snapshot = (
                self._snapshot_before_change(
                    root, destination, safe_path, reason="overwrite", tool_name="write_text_file"
                )
                if overwrite
                else None
            )
            self._atomic_write(destination, payload, overwrite=overwrite)
            try:
                verified_path = destination.resolve(strict=True)
                self._assert_within(root.canonical_path, verified_path)
                digest, size = self._sha256_file(
                    verified_path,
                    maximum_bytes=self.max_write_bytes,
                )
                expected_digest = hashlib.sha256(payload).hexdigest()
                if digest != expected_digest or size != len(payload):
                    raise FilesystemMutationUncertain(
                        "WRITE_VERIFICATION_FAILED"
                    )
            except FilesystemMutationUncertain:
                raise
            except (FilesystemPolicyError, OSError, RuntimeError) as exc:
                raise FilesystemMutationUncertain(
                    "WRITE_VERIFICATION_FAILED"
                ) from exc
            return {
                "root_id": root.root_id,
                "path": safe_path,
                "bytes_written": size,
                "overwrite": overwrite,
                "snapshot": self._snapshot_summary(snapshot),
                "verification": {
                    "strategy": "atomic_write_and_sha256_read_back",
                    "sha256": digest,
                    "atomic": True,
                },
            }

        return self._safe_result(
            "write_text_file",
            operation,
            "UTF-8 file written and verified.",
        )

    def create_directory(self, root_id: str, path: str) -> ToolResult:
        def operation() -> dict[str, Any]:
            root, destination, safe_path = self._resolve_destination(root_id, path)
            self._assert_no_reparse_below_root(
                root.canonical_path,
                destination.parent,
                allow_missing=False,
            )
            destination.mkdir()
            try:
                verified_path = destination.resolve(strict=True)
                self._assert_within(root.canonical_path, verified_path)
                if not verified_path.is_dir():
                    raise FilesystemMutationUncertain(
                        "DIRECTORY_VERIFICATION_FAILED"
                    )
            except FilesystemMutationUncertain:
                raise
            except (FilesystemPolicyError, OSError, RuntimeError) as exc:
                raise FilesystemMutationUncertain(
                    "DIRECTORY_VERIFICATION_FAILED"
                ) from exc
            return {
                "root_id": root.root_id,
                "path": safe_path,
                "verification": {
                    "strategy": "directory_postcondition",
                    "exists": True,
                    "is_directory": True,
                },
            }

        return self._safe_result(
            "create_directory",
            operation,
            "Directory created and verified.",
        )

    def copy_file(
        self,
        root_id: str,
        source_path: str,
        destination_path: str,
        overwrite: bool = False,
    ) -> ToolResult:
        def operation() -> dict[str, Any]:
            if not isinstance(overwrite, bool):
                raise FilesystemPolicyError("INVALID_OVERWRITE_FLAG")
            root, source, safe_source = self._resolve_existing(
                root_id,
                source_path,
            )
            if not source.is_file():
                raise IsADirectoryError
            _, destination, safe_destination = self._resolve_destination(
                root_id,
                destination_path,
            )
            if source == destination:
                raise FilesystemPolicyError("SOURCE_EQUALS_DESTINATION")
            if destination.exists() and not overwrite:
                raise FileExistsError
            if destination.exists() and not destination.is_file():
                raise IsADirectoryError

            self._assert_no_reparse_below_root(
                root.canonical_path,
                source,
                allow_missing=False,
            )
            self._assert_no_reparse_below_root(
                root.canonical_path,
                destination.parent,
                allow_missing=False,
            )
            payload, source_digest = self._read_stable_payload(
                source,
                maximum_bytes=self.max_transfer_bytes,
            )
            source_size = len(payload)
            snapshot = (
                self._snapshot_before_change(
                    root, destination, safe_destination, reason="overwrite", tool_name="copy_file"
                )
                if overwrite
                else None
            )
            self._atomic_write(destination, payload, overwrite=overwrite)
            try:
                verified_path = destination.resolve(strict=True)
                self._assert_within(root.canonical_path, verified_path)
                destination_digest, destination_size = self._sha256_file(
                    verified_path,
                    maximum_bytes=self.max_transfer_bytes,
                )
                if (
                    source_digest != destination_digest
                    or source_size != destination_size
                ):
                    raise FilesystemMutationUncertain(
                        "COPY_VERIFICATION_FAILED"
                    )
            except FilesystemMutationUncertain:
                raise
            except (FilesystemPolicyError, OSError, RuntimeError) as exc:
                raise FilesystemMutationUncertain(
                    "COPY_VERIFICATION_FAILED"
                ) from exc
            return {
                "root_id": root.root_id,
                "source_path": safe_source,
                "destination_path": safe_destination,
                "bytes_copied": destination_size,
                "overwrite": overwrite,
                "snapshot": self._snapshot_summary(snapshot),
                "verification": {
                    "strategy": "source_destination_sha256",
                    "sha256": destination_digest,
                    "destination_exists": True,
                },
            }

        return self._safe_result(
            "copy_file",
            operation,
            "File copied and verified.",
        )

    def move_file(
        self,
        root_id: str,
        source_path: str,
        destination_path: str,
        overwrite: bool = False,
    ) -> ToolResult:
        def operation() -> dict[str, Any]:
            if not isinstance(overwrite, bool):
                raise FilesystemPolicyError("INVALID_OVERWRITE_FLAG")
            root, source, safe_source = self._resolve_existing(
                root_id,
                source_path,
            )
            if self._is_link_or_reparse(
                root.canonical_path.joinpath(
                    *self._relative_parts(source_path, allow_empty=False)
                )
            ):
                raise FilesystemPolicyError("REPARSE_SOURCE_REJECTED")
            if not source.is_file():
                raise IsADirectoryError
            _, destination, safe_destination = self._resolve_destination(
                root_id,
                destination_path,
            )
            if source == destination:
                raise FilesystemPolicyError("SOURCE_EQUALS_DESTINATION")
            if destination.exists() and not overwrite:
                raise FileExistsError
            if destination.exists() and not destination.is_file():
                raise IsADirectoryError

            self._assert_no_reparse_below_root(
                root.canonical_path,
                source,
                allow_missing=False,
            )
            self._assert_no_reparse_below_root(
                root.canonical_path,
                destination.parent,
                allow_missing=False,
            )
            source_details = source.stat()
            if source_details.st_size > self.max_transfer_bytes:
                raise FilesystemPolicyError("TRANSFER_SIZE_LIMIT_EXCEEDED")
            snapshot = (
                self._snapshot_before_change(
                    root, destination, safe_destination, reason="overwrite", tool_name="move_file"
                )
                if overwrite
                else None
            )
            mutation_started = False
            try:
                if overwrite:
                    os.replace(source, destination)
                    mutation_started = True
                else:
                    os.rename(source, destination)
                    mutation_started = True
            except OSError as exc:
                if mutation_started:
                    raise FilesystemMutationUncertain(
                        "MOVE_POSTCONDITION_UNCERTAIN"
                    ) from exc
                raise

            try:
                verified_path = destination.resolve(strict=True)
                self._assert_within(root.canonical_path, verified_path)
                destination_details = verified_path.stat()
                destination_digest, destination_size = self._sha256_file(
                    verified_path,
                    maximum_bytes=self.max_transfer_bytes,
                )
                same_identity = (
                    source_details.st_dev == destination_details.st_dev
                    and source_details.st_ino == destination_details.st_ino
                )
                if (
                    source.exists()
                    or not same_identity
                    or source_details.st_size != destination_size
                ):
                    raise FilesystemMutationUncertain(
                        "MOVE_VERIFICATION_FAILED"
                    )
            except FilesystemMutationUncertain:
                raise
            except (FilesystemPolicyError, OSError, RuntimeError) as exc:
                raise FilesystemMutationUncertain(
                    "MOVE_VERIFICATION_FAILED"
                ) from exc
            return {
                "root_id": root.root_id,
                "source_path": safe_source,
                "destination_path": safe_destination,
                "bytes_moved": destination_size,
                "overwrite": overwrite,
                "snapshot": self._snapshot_summary(snapshot),
                "verification": {
                    "strategy": "source_absent_destination_sha256",
                    "sha256": destination_digest,
                    "source_absent": True,
                    "destination_exists": True,
                },
            }

        return self._safe_result(
            "move_file",
            operation,
            "File moved and verified.",
        )

    def delete_path(self, root_id: str, path: str) -> ToolResult:
        """Recoverable delete: a file is sealed in the snapshot store first.

        Only files and empty directories are removed. Without a snapshot
        store a file deletion is refused, never performed unprotected.
        """

        def operation() -> dict[str, Any]:
            root, target, safe_path = self._resolve_existing(root_id, path)
            requested = root.canonical_path.joinpath(
                *self._relative_parts(path, allow_empty=False)
            )
            if self._is_link_or_reparse(requested):
                raise FilesystemPolicyError("REPARSE_SOURCE_REJECTED")
            if target == root.canonical_path:
                raise FilesystemPolicyError("ROOT_DELETE_REJECTED")
            self._assert_no_reparse_below_root(
                root.canonical_path, target, allow_missing=False
            )
            if target.is_dir():
                with os.scandir(target) as iterator:
                    if any(True for _ in iterator):
                        raise FilesystemPolicyError("DIRECTORY_NOT_EMPTY")
                target.rmdir()
                kind = "directory"
                snapshot = None
            elif target.is_file():
                if self._snapshots is None:
                    raise FilesystemPolicyError("RECOVERABLE_DELETE_UNAVAILABLE")
                snapshot = self._snapshot_before_change(
                    root, target, safe_path, reason="delete", tool_name="delete_path"
                )
                if snapshot is None:
                    raise FilesystemMutationUncertain("DELETE_SNAPSHOT_MISSING")
                target.unlink()
                kind = "file"
            else:
                raise FilesystemPolicyError("UNSUPPORTED_ENTRY_KIND")
            if target.exists() or target.is_symlink():
                raise FilesystemMutationUncertain("DELETE_VERIFICATION_FAILED")
            return {
                "root_id": root.root_id,
                "path": safe_path,
                "kind": kind,
                "snapshot": self._snapshot_summary(snapshot),
                "verification": {
                    "strategy": "snapshot_then_absence",
                    "absent": True,
                    "recoverable": snapshot is not None,
                },
            }

        return self._safe_result(
            "delete_path",
            operation,
            "Entry removed; a file is recoverable from its snapshot.",
        )

    # ------------------------------------------------------------------
    # snapshots
    # ------------------------------------------------------------------
    def list_filesystem_snapshots(self, limit: int = 50) -> ToolResult:
        def operation() -> dict[str, Any]:
            if (
                isinstance(limit, bool)
                or not isinstance(limit, int)
                or not 1 <= limit <= self.max_list_entries
            ):
                raise FilesystemPolicyError("INVALID_PAGE_LIMIT")
            if self._snapshots is None:
                return {"available": False, "snapshots": [], "usage": None}
            records = self._snapshots.list()
            return {
                "available": True,
                "snapshots": [record.to_dict() for record in records[:limit]],
                "total_snapshots": len(records),
                "usage": self._snapshots.usage(),
                "verification": {"strategy": "manifest_listing"},
            }

        return self._safe_result(
            "list_filesystem_snapshots",
            operation,
            "Filesystem snapshots observed.",
        )

    def undo_filesystem_change(self, snapshot_id: str) -> ToolResult:
        """Write a sealed snapshot back to its original relative path.

        The file currently at that path, if any, is itself sealed first, so
        an undo can be undone. The root must still be granted and unchanged.
        """

        def operation() -> dict[str, Any]:
            if self._snapshots is None:
                raise FilesystemPolicyError("SNAPSHOT_STORE_UNAVAILABLE")
            record, payload = self._snapshots.read_payload(snapshot_id)
            root, destination, safe_path = self._resolve_destination(
                record.root_id, record.path
            )
            if destination.exists() and not destination.is_file():
                raise IsADirectoryError
            self._assert_no_reparse_below_root(
                root.canonical_path, destination.parent, allow_missing=False
            )
            replaced = self._snapshot_before_change(
                root, destination, safe_path, reason="undo", tool_name="undo_filesystem_change"
            )
            self._atomic_write(destination, payload, overwrite=True)
            try:
                verified_path = destination.resolve(strict=True)
                self._assert_within(root.canonical_path, verified_path)
                digest, size = self._sha256_file(
                    verified_path, maximum_bytes=self._snapshots.max_file_bytes
                )
                if digest != record.sha256 or size != record.size_bytes:
                    raise FilesystemMutationUncertain("UNDO_VERIFICATION_FAILED")
            except FilesystemMutationUncertain:
                raise
            except (FilesystemPolicyError, OSError, RuntimeError) as exc:
                raise FilesystemMutationUncertain("UNDO_VERIFICATION_FAILED") from exc
            return {
                "root_id": root.root_id,
                "path": safe_path,
                "restored_snapshot_id": record.snapshot_id,
                "bytes_restored": size,
                "replaced": self._snapshot_summary(replaced),
                "verification": {
                    "strategy": "snapshot_sha256_write_back",
                    "sha256": digest,
                    "atomic": True,
                },
            }

        return self._safe_result(
            "undo_filesystem_change",
            operation,
            "Snapshot restored and verified.",
        )

    # ------------------------------------------------------------------
    # plans: dry run, then apply with the digest that was approved
    # ------------------------------------------------------------------
    def _probe(self, root: AllowedFilesystemRoot, parts: tuple[str, ...]) -> tuple[str | None, int | None]:
        """(kind, size) of a path below the root without following links."""
        candidate = root.canonical_path.joinpath(*parts)
        self._assert_no_reparse_below_root(root.canonical_path, candidate, allow_missing=True)
        try:
            details = candidate.lstat()
        except FileNotFoundError:
            return None, None
        if stat.S_ISDIR(details.st_mode):
            return "directory", None
        if stat.S_ISREG(details.st_mode):
            return "file", details.st_size
        return "other", None

    @staticmethod
    def _plan_digest(root_id: str, operations: list[dict[str, Any]]) -> str:
        encoded = json.dumps(
            {"root_id": root_id, "operations": operations},
            sort_keys=True,
            ensure_ascii=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    def _normalize_operation(self, index: int, operation: Any) -> dict[str, Any]:
        if not isinstance(operation, dict):
            raise FilesystemPolicyError("INVALID_PLAN_OPERATION")
        action = operation.get("action")
        if not isinstance(action, str) or action not in _PLAN_ACTIONS:
            raise FilesystemPolicyError("INVALID_PLAN_ACTION")
        overwrite = operation.get("overwrite", False)
        if not isinstance(overwrite, bool):
            raise FilesystemPolicyError("INVALID_OVERWRITE_FLAG")
        normalized: dict[str, Any] = {"index": index, "action": action, "overwrite": overwrite}
        if action in {"write", "create_directory", "delete"}:
            normalized["path"] = "/".join(
                self._relative_parts(operation.get("path"), allow_empty=False)
            )
        else:
            normalized["source_path"] = "/".join(
                self._relative_parts(operation.get("source_path"), allow_empty=False)
            )
            normalized["destination_path"] = "/".join(
                self._relative_parts(operation.get("destination_path"), allow_empty=False)
            )
        if action == "write":
            content = operation.get("content", "")
            if not isinstance(content, str):
                raise FilesystemPolicyError("CONTENT_MUST_BE_TEXT")
            if len(content.encode("utf-8")) > self.max_write_bytes:
                raise FilesystemPolicyError("WRITE_SIZE_LIMIT_EXCEEDED")
            normalized["content"] = content
        unknown = set(operation) - {
            "action", "overwrite", "path", "source_path", "destination_path", "content"
        }
        if unknown:
            raise FilesystemPolicyError("INVALID_PLAN_OPERATION")
        return normalized

    def plan_filesystem_changes(
        self, root_id: str, operations: list[dict[str, Any]]
    ) -> ToolResult:
        """Dry-run a batch of bounded operations without touching the disk."""

        def operation_body() -> dict[str, Any]:
            if not isinstance(operations, list) or not operations:
                raise FilesystemPolicyError("INVALID_PLAN_OPERATIONS")
            if len(operations) > self.max_plan_operations:
                raise FilesystemPolicyError("PLAN_TOO_LARGE")
            root = self._root(root_id)
            normalized = [
                self._normalize_operation(index, item)
                for index, item in enumerate(operations)
            ]

            state: dict[str, str | None] = {}
            fingerprints: dict[str, tuple[Any, ...]] = {}
            touched: set[str] = set()

            def probe(safe_path: str) -> str | None:
                if safe_path in state:
                    return state[safe_path]
                parts = tuple(safe_path.split("/"))
                kind, size = self._probe(root, parts)
                state[safe_path] = kind
                candidate = root.canonical_path.joinpath(*parts)
                if kind is None:
                    fingerprints[safe_path] = (False, None, None)
                else:
                    details = candidate.lstat()
                    fingerprints[safe_path] = (True, size, details.st_mtime_ns)
                return kind

            def parent_ready(safe_path: str) -> bool:
                parent = safe_path.rsplit("/", 1)[0] if "/" in safe_path else ""
                if not parent:
                    return True
                return probe(parent) == "directory"

            report: list[dict[str, Any]] = []
            for item in normalized:
                entry: dict[str, Any] = {
                    "index": item["index"],
                    "action": item["action"],
                    "overwrite": item["overwrite"],
                    "status": "ready",
                    "code": None,
                    "snapshot": False,
                }
                action = item["action"]
                try:
                    if action in {"write", "create_directory", "delete"}:
                        target = item["path"]
                        entry["path"] = target
                        kind = probe(target)
                        if action == "write":
                            entry["bytes"] = len(item["content"].encode("utf-8"))
                            if not parent_ready(target):
                                raise FilesystemPolicyError("PATH_NOT_FOUND")
                            if kind == "directory" or kind == "other":
                                raise FilesystemPolicyError("EXPECTED_FILE")
                            if kind == "file" and not item["overwrite"]:
                                raise FilesystemPolicyError("DESTINATION_EXISTS")
                            entry["snapshot"] = kind == "file"
                            state[target] = "file"
                        elif action == "create_directory":
                            if not parent_ready(target):
                                raise FilesystemPolicyError("PATH_NOT_FOUND")
                            if kind is not None:
                                raise FilesystemPolicyError("DESTINATION_EXISTS")
                            state[target] = "directory"
                        else:
                            if kind is None:
                                raise FilesystemPolicyError("PATH_NOT_FOUND")
                            if kind == "other":
                                raise FilesystemPolicyError("UNSUPPORTED_ENTRY_KIND")
                            if kind == "directory":
                                if any(
                                    other.startswith(target + "/") and other_kind is not None
                                    for other, other_kind in state.items()
                                ):
                                    raise FilesystemPolicyError("DIRECTORY_NOT_EMPTY")
                                real = root.canonical_path.joinpath(*target.split("/"))
                                with os.scandir(real) as iterator:
                                    if any(True for _ in iterator):
                                        raise FilesystemPolicyError("DIRECTORY_NOT_EMPTY")
                            else:
                                if self._snapshots is None:
                                    raise FilesystemPolicyError(
                                        "RECOVERABLE_DELETE_UNAVAILABLE"
                                    )
                                entry["snapshot"] = True
                            state[target] = None
                        touched.add(target)
                    else:
                        source, destination = item["source_path"], item["destination_path"]
                        entry["source_path"] = source
                        entry["destination_path"] = destination
                        if source == destination:
                            raise FilesystemPolicyError("SOURCE_EQUALS_DESTINATION")
                        source_kind = probe(source)
                        if source_kind is None:
                            raise FilesystemPolicyError("PATH_NOT_FOUND")
                        if source_kind != "file":
                            raise FilesystemPolicyError("EXPECTED_FILE")
                        destination_kind = probe(destination)
                        if not parent_ready(destination):
                            raise FilesystemPolicyError("PATH_NOT_FOUND")
                        if destination_kind in {"directory", "other"}:
                            raise FilesystemPolicyError("EXPECTED_FILE")
                        if destination_kind == "file" and not item["overwrite"]:
                            raise FilesystemPolicyError("DESTINATION_EXISTS")
                        entry["snapshot"] = destination_kind == "file"
                        state[destination] = "file"
                        if action == "move":
                            state[source] = None
                        touched.update({source, destination})
                except FilesystemPolicyError as exc:
                    entry["status"] = "blocked"
                    entry["code"] = exc.code
                except FileNotFoundError:
                    entry["status"] = "blocked"
                    entry["code"] = "PATH_NOT_FOUND"
                report.append(entry)

            ready = all(entry["status"] == "ready" for entry in report)
            result: dict[str, Any] = {
                "root_id": root.root_id,
                "operations": report,
                "operation_count": len(report),
                "target_count": len(touched),
                "ready": ready,
                "snapshots_required": sum(1 for entry in report if entry["snapshot"]),
                "mutated": False,
                "verification": {"strategy": "dry_run_no_mutation"},
            }
            if ready:
                digest = self._plan_digest(root.root_id, normalized)
                plan = _FilesystemPlan(
                    plan_id=uuid4().hex,
                    root_id=root.root_id,
                    digest=digest,
                    operations=tuple(normalized),
                    fingerprints=tuple(sorted(fingerprints.items())),
                    created_at=time.monotonic(),
                    target_count=len(touched),
                )
                with self._lock:
                    self._expire_plans()
                    self._plans[plan.plan_id] = plan
                result.update(
                    {
                        "plan_id": plan.plan_id,
                        "digest": digest,
                        "expires_in_seconds": int(self.plan_ttl_seconds),
                        "apply_with": {
                            "tool": "apply_filesystem_plan",
                            "plan_id": plan.plan_id,
                            "digest": digest,
                        },
                    }
                )
            return result

        return self._safe_result(
            "plan_filesystem_changes",
            operation_body,
            "Filesystem plan evaluated without touching the disk.",
        )

    def _expire_plans(self) -> None:
        now = time.monotonic()
        for plan_id in [
            identifier
            for identifier, plan in self._plans.items()
            if now - plan.created_at > self.plan_ttl_seconds
        ]:
            self._plans.pop(plan_id, None)
        while len(self._plans) > 20:
            oldest = min(self._plans.values(), key=lambda plan: plan.created_at)
            self._plans.pop(oldest.plan_id, None)

    def _take_plan(self, plan_id: Any, digest: Any) -> _FilesystemPlan:
        if not isinstance(plan_id, str) or not isinstance(digest, str):
            raise FilesystemPolicyError("INVALID_PLAN_REFERENCE")
        with self._lock:
            self._expire_plans()
            plan = self._plans.get(plan_id.strip().lower())
            if plan is None:
                raise FilesystemPolicyError("PLAN_NOT_FOUND")
            if not hmac.compare_digest(plan.digest, digest.strip().lower()):
                raise FilesystemPolicyError("PLAN_DIGEST_MISMATCH")
            # Single use: the plan is consumed before anything is applied.
            self._plans.pop(plan.plan_id, None)
        return plan

    def apply_filesystem_plan(self, plan_id: str, digest: str) -> ToolResult:
        """Apply a dry-run plan whose digest the user approved.

        Every target is re-checked against the fingerprint taken when the
        plan was made; any drift blocks the whole plan. Operations run in
        order, each with its own snapshot; the first failure stops the run
        and the result lists what was applied so it can be undone.
        """
        try:
            plan = self._take_plan(plan_id, digest)
            root = self._root(plan.root_id)
            for safe_path, expected in plan.fingerprints:
                parts = tuple(safe_path.split("/"))
                kind, size = self._probe(root, parts)
                if kind is None:
                    current: tuple[Any, ...] = (False, None, None)
                else:
                    details = root.canonical_path.joinpath(*parts).lstat()
                    current = (True, size, details.st_mtime_ns)
                if tuple(expected) != current:
                    raise FilesystemPolicyError("PLAN_TARGETS_CHANGED")
        except FilesystemPolicyError as exc:
            return ToolResult(
                status=ToolExecutionStatus.BLOCKED,
                tool_name="apply_filesystem_plan",
                message="Filesystem policy blocked the plan.",
                data={"error_code": exc.code},
                error=exc.code,
                verified=False,
            )

        applied: list[dict[str, Any]] = []
        failure: dict[str, Any] | None = None
        for item in plan.operations:
            action = item["action"]
            if action == "write":
                result = self.write_text_file(
                    plan.root_id, item["path"], item["content"], overwrite=item["overwrite"]
                )
            elif action == "create_directory":
                result = self.create_directory(plan.root_id, item["path"])
            elif action == "copy":
                result = self.copy_file(
                    plan.root_id,
                    item["source_path"],
                    item["destination_path"],
                    overwrite=item["overwrite"],
                )
            elif action == "move":
                result = self.move_file(
                    plan.root_id,
                    item["source_path"],
                    item["destination_path"],
                    overwrite=item["overwrite"],
                )
            else:
                result = self.delete_path(plan.root_id, item["path"])
            summary = {
                "index": item["index"],
                "action": action,
                "status": result.status.value,
                "error": result.error,
                "snapshot": (result.data or {}).get("snapshot") if isinstance(result.data, dict) else None,
            }
            if result.succeeded:
                applied.append(summary)
                continue
            failure = summary
            break

        data = {
            "root_id": plan.root_id,
            "plan_id": plan.plan_id,
            "digest": plan.digest,
            "applied": applied,
            "failed": failure,
            "remaining": max(0, len(plan.operations) - len(applied) - (1 if failure else 0)),
            "verification": {
                "strategy": "per_operation_verification",
                "all_applied": failure is None,
            },
        }
        if failure is None:
            return ToolResult(
                status=ToolExecutionStatus.SUCCESS,
                tool_name="apply_filesystem_plan",
                message="Filesystem plan applied and every operation verified.",
                data=data,
                verified=True,
            )
        if applied:
            return ToolResult(
                status=ToolExecutionStatus.PARTIAL,
                tool_name="apply_filesystem_plan",
                message="Filesystem plan stopped at a failing operation; earlier operations are recoverable.",
                data=data,
                error=str(failure.get("error") or "PLAN_OPERATION_FAILED"),
                verified=False,
            )
        return ToolResult(
            status=ToolExecutionStatus.FAILED,
            tool_name="apply_filesystem_plan",
            message="Filesystem plan failed before changing anything.",
            data=data,
            error=str(failure.get("error") or "PLAN_OPERATION_FAILED"),
            verified=False,
        )

    # ------------------------------------------------------------------
    # tool registration
    # ------------------------------------------------------------------
    def _definition(
        self,
        *,
        name: str,
        description: str,
        action: str,
        risk_level: RiskLevel = RiskLevel.READ_ONLY,
        requires_confirmation: bool = False,
        verification_strategy: str,
    ) -> ToolDefinition:
        return ToolDefinition(
            name=name,
            description=description,
            risk_level=risk_level,
            requires_confirmation=requires_confirmation,
            timeout_seconds=10.0,
            version="1.1.0",
            capabilities=frozenset({"windows", "filesystem", action}),
            tags=frozenset(
                {
                    "windows",
                    "filesystem",
                    "action" if requires_confirmation else "read-only",
                }
            ),
            max_concurrency=1 if requires_confirmation else None,
            retry_max_attempts=1,
            idempotent=False,
            metadata={
                "path_scope": "allowed_root_id_and_relative_path_only",
                "verification_strategy": verification_strategy,
                "sensitive_content_logged": False,
                "sensitive_output": action in {"directory:list", "file:read", "file:search"},
                "recoverable_delete_supported": self._snapshots is not None,
            },
        )

    def register_tools(self, executor: ToolExecutor) -> None:
        """Register bounded operations with typed, risk-explicit contracts."""
        definitions = (
            (
                self._definition(
                    name="list_allowed_file_roots",
                    description=(
                        "List filesystem root identifiers explicitly granted to JARVIS; "
                        "absolute paths are not exposed."
                    ),
                    action="roots:observe",
                    verification_strategy="configured_grant_snapshot",
                ),
                self.list_allowed_roots,
            ),
            (
                self._definition(
                    name="list_directory",
                    description=(
                        "List one bounded page under an allowed root using root_id and "
                        "a relative path."
                    ),
                    action="directory:list",
                    verification_strategy="bounded_directory_snapshot",
                ),
                self.list_directory,
            ),
            (
                self._definition(
                    name="read_text_file",
                    description=(
                        "Read one bounded UTF-8 page under an allowed root using root_id "
                        "and a relative path."
                    ),
                    action="file:read",
                    verification_strategy="stable_file_handle_and_page_sha256",
                ),
                self.read_text_file,
            ),
            (
                self._definition(
                    name="search_files",
                    description=(
                        "Search file and directory names under an allowed root using a "
                        "case-insensitive substring or glob; links are never followed."
                    ),
                    action="file:search",
                    verification_strategy="bounded_name_index",
                ),
                self.search_files,
            ),
            (
                self._definition(
                    name="write_text_file",
                    description=(
                        "Atomically write bounded UTF-8 content under an allowed root; "
                        "overwrite is false by default and a replaced file is snapshotted."
                    ),
                    action="file:write",
                    risk_level=RiskLevel.MEDIUM,
                    requires_confirmation=True,
                    verification_strategy="atomic_write_and_sha256_read_back",
                ),
                self.write_text_file,
            ),
            (
                self._definition(
                    name="create_directory",
                    description=(
                        "Create one directory under an allowed root using a relative path."
                    ),
                    action="directory:create",
                    risk_level=RiskLevel.MEDIUM,
                    requires_confirmation=True,
                    verification_strategy="directory_postcondition",
                ),
                self.create_directory,
            ),
            (
                self._definition(
                    name="copy_file",
                    description=(
                        "Copy one bounded file within an allowed root; overwrite is false "
                        "by default and a replaced file is snapshotted."
                    ),
                    action="file:copy",
                    risk_level=RiskLevel.MEDIUM,
                    requires_confirmation=True,
                    verification_strategy="source_destination_sha256",
                ),
                self.copy_file,
            ),
            (
                self._definition(
                    name="move_file",
                    description=(
                        "Move one bounded file within an allowed root; overwrite is false "
                        "by default and a replaced file is snapshotted."
                    ),
                    action="file:move",
                    risk_level=RiskLevel.MEDIUM,
                    requires_confirmation=True,
                    verification_strategy="source_absent_destination_sha256",
                ),
                self.move_file,
            ),
            (
                self._definition(
                    name="delete_path",
                    description=(
                        "Delete one file (sealed in a recoverable snapshot first) or one "
                        "empty directory under an allowed root."
                    ),
                    action="file:delete",
                    risk_level=RiskLevel.HIGH,
                    requires_confirmation=True,
                    verification_strategy="snapshot_then_absence",
                ),
                self.delete_path,
            ),
            (
                self._definition(
                    name="list_filesystem_snapshots",
                    description=(
                        "List recoverable snapshots of files that bounded operations "
                        "replaced or removed."
                    ),
                    action="snapshot:list",
                    verification_strategy="manifest_listing",
                ),
                self.list_filesystem_snapshots,
            ),
            (
                self._definition(
                    name="undo_filesystem_change",
                    description=(
                        "Restore a snapshot to its original relative path; the current "
                        "file there is snapshotted first so the undo can be undone."
                    ),
                    action="snapshot:restore",
                    risk_level=RiskLevel.HIGH,
                    requires_confirmation=True,
                    verification_strategy="snapshot_sha256_write_back",
                ),
                self.undo_filesystem_change,
            ),
            (
                self._definition(
                    name="plan_filesystem_changes",
                    description=(
                        "Dry-run up to 50 write/create_directory/copy/move/delete operations "
                        "under one allowed root: reports what would change and returns a "
                        "plan_id and digest; nothing is modified."
                    ),
                    action="plan:dry-run",
                    verification_strategy="dry_run_no_mutation",
                ),
                self.plan_filesystem_changes,
            ),
            (
                self._definition(
                    name="apply_filesystem_plan",
                    description=(
                        "Apply a previously planned batch by plan_id and digest; every "
                        "target is re-checked and each operation is snapshotted and "
                        "verified in order."
                    ),
                    action="plan:apply",
                    risk_level=RiskLevel.HIGH,
                    requires_confirmation=True,
                    verification_strategy="per_operation_verification",
                ),
                self.apply_filesystem_plan,
            ),
        )
        for definition, handler in definitions:
            executor.register(
                definition,
                handler,
                source=_SOURCE,
            )


__all__ = [
    "AllowedFilesystemRoot",
    "BoundedFilesystemService",
    "FilesystemMutationUncertain",
    "FilesystemPolicyError",
    "default_critical_paths",
]
