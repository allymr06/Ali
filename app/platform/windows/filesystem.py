from __future__ import annotations

import hashlib
import os
import re
import stat
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any, Callable

from app.core.models import (
    RiskLevel,
    ToolDefinition,
    ToolExecutionStatus,
    ToolResult,
)
from app.tools.executor import ToolExecutor


_ROOT_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,63}$")
_PATH_SEPARATOR_PATTERN = re.compile(r"[\\/]+")
_SOURCE = "platform:windows:bounded-filesystem"


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


class BoundedFilesystemService:
    """Filesystem access constrained to explicit roots and relative paths.

    No roots are granted by default. Root grants are configuration-time state;
    they are intentionally not exposed as model-callable tools.
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
        if max_directory_scan < max_list_entries:
            raise ValueError(
                "max_directory_scan cannot be smaller than max_list_entries."
            )

        self.max_read_page_bytes = max_read_page_bytes
        self.max_write_bytes = max_write_bytes
        self.max_transfer_bytes = max_transfer_bytes
        self.max_list_entries = max_list_entries
        self.max_directory_scan = max_directory_scan
        self._roots: dict[str, AllowedFilesystemRoot] = {}

        for root_id, path in (allowed_roots or {}).items():
            self.allow_root(root_id, path)

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
        except FilesystemPolicyError as exc:
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
                    details = entry.stat(follow_symlinks=False)
                    is_link = entry.is_symlink() or bool(
                        getattr(details, "st_file_attributes", 0)
                        & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
                    )
                    if is_link:
                        kind = "link"
                        size = None
                    elif stat.S_ISDIR(details.st_mode):
                        kind = "directory"
                        size = None
                    elif stat.S_ISREG(details.st_mode):
                        kind = "file"
                        size = details.st_size
                    else:
                        kind = "other"
                        size = None
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
        """Fail closed until a recoverable trash implementation is available."""
        del root_id, path
        return ToolResult(
            status=ToolExecutionStatus.BLOCKED,
            tool_name="delete_path",
            message="Deletion is unavailable without recoverable trash support.",
            data={"error_code": "RECOVERABLE_DELETE_UNAVAILABLE"},
            error="RECOVERABLE_DELETE_UNAVAILABLE",
            verified=False,
        )

    @staticmethod
    def _definition(
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
            version="1.0.0",
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
                "sensitive_output": action in {"directory:list", "file:read"},
                "recoverable_delete_supported": False,
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
                    name="write_text_file",
                    description=(
                        "Atomically write bounded UTF-8 content under an allowed root; "
                        "overwrite is false by default."
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
                        "by default."
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
                        "by default."
                    ),
                    action="file:move",
                    risk_level=RiskLevel.MEDIUM,
                    requires_confirmation=True,
                    verification_strategy="source_absent_destination_sha256",
                ),
                self.move_file,
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
]
