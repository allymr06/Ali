from __future__ import annotations

import hashlib
import os
import subprocess
from pathlib import Path

import pytest

from app.core.models import RiskLevel, ToolExecutionStatus
from app.platform.windows.filesystem import BoundedFilesystemService
from app.tools.executor import ToolExecutor


def create_service(root: Path, **kwargs) -> BoundedFilesystemService:
    return BoundedFilesystemService({"workspace": root}, **kwargs)


def test_no_filesystem_roots_are_allowed_by_default(tmp_path: Path) -> None:
    service = BoundedFilesystemService()
    target = tmp_path / "secret.txt"
    target.write_text("secret", encoding="utf-8")

    result = service.read_text_file("workspace", "secret.txt")
    roots = service.list_allowed_roots()

    assert result.status is ToolExecutionStatus.BLOCKED
    assert result.error == "ROOT_NOT_ALLOWED"
    assert roots.succeeded
    assert roots.data["roots"] == []


def test_root_grant_requires_existing_absolute_directory(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="absolute"):
        BoundedFilesystemService({"workspace": "relative"})
    with pytest.raises(ValueError, match="exist"):
        BoundedFilesystemService({"workspace": tmp_path / "missing"})
    file_path = tmp_path / "file.txt"
    file_path.write_text("x", encoding="utf-8")
    with pytest.raises(ValueError, match="directory"):
        BoundedFilesystemService({"workspace": file_path})


def test_root_ids_are_strict_and_cannot_be_silently_replaced(tmp_path: Path) -> None:
    service = create_service(tmp_path)

    with pytest.raises(ValueError, match="cannot be replaced"):
        service.allow_root("WORKSPACE", tmp_path)
    with pytest.raises(ValueError):
        service.allow_root("not a root", tmp_path)

    assert service.revoke_root("WORKSPACE") is True
    assert service.revoke_root("workspace") is False


@pytest.mark.parametrize(
    "unsafe_path, expected_code",
    [
        ("../outside.txt", "PATH_TRAVERSAL_REJECTED"),
        ("folder/../outside.txt", "PATH_TRAVERSAL_REJECTED"),
        ("/outside.txt", "ABSOLUTE_PATH_REJECTED"),
        (r"C:\outside.txt", "ABSOLUTE_PATH_REJECTED"),
        (r"\\server\share\outside.txt", "ABSOLUTE_PATH_REJECTED"),
        ("file.txt:stream", "UNSAFE_PATH_COMPONENT"),
        ("CON.txt", "UNSAFE_PATH_COMPONENT"),
        ("ambiguous.", "UNSAFE_PATH_COMPONENT"),
        ("wild*.txt", "UNSAFE_PATH_COMPONENT"),
    ],
)
def test_absolute_traversal_and_windows_unsafe_paths_are_blocked(
    tmp_path: Path,
    unsafe_path: str,
    expected_code: str,
) -> None:
    service = create_service(tmp_path)

    result = service.write_text_file("workspace", unsafe_path, "private")

    assert result.status is ToolExecutionStatus.BLOCKED
    assert result.error == expected_code
    assert not any(tmp_path.iterdir())


def test_external_symlink_escape_is_blocked(tmp_path: Path) -> None:
    root = tmp_path / "root"
    outside = tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    (outside / "secret.txt").write_text("secret", encoding="utf-8")
    link = root / "escape"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"Creating symlinks is unavailable: {exc}")
    service = create_service(root)

    read = service.read_text_file("workspace", "escape/secret.txt")
    write = service.write_text_file("workspace", "escape/new.txt", "secret")

    assert read.status is ToolExecutionStatus.BLOCKED
    assert read.error == "REPARSE_PATH_REJECTED"
    assert write.status is ToolExecutionStatus.BLOCKED
    assert write.error == "REPARSE_PATH_REJECTED"
    assert not (outside / "new.txt").exists()


@pytest.mark.skipif(os.name != "nt", reason="Windows junction test")
def test_external_windows_junction_escape_is_blocked(tmp_path: Path) -> None:
    root = tmp_path / "root"
    outside = tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    (outside / "secret.txt").write_text("secret", encoding="utf-8")
    link = root / "junction"
    outcome = subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(link), str(outside)],
        capture_output=True,
        check=False,
        text=True,
    )
    if outcome.returncode != 0:
        pytest.skip("Creating a Windows junction is unavailable.")
    service = create_service(root)

    read = service.read_text_file("workspace", "junction/secret.txt")
    write = service.write_text_file("workspace", "junction/new.txt", "secret")

    assert read.status is ToolExecutionStatus.BLOCKED
    assert read.error == "REPARSE_PATH_REJECTED"
    assert write.status is ToolExecutionStatus.BLOCKED
    assert write.error == "REPARSE_PATH_REJECTED"
    assert not (outside / "new.txt").exists()


def test_read_text_is_utf8_paginated_and_verified(tmp_path: Path) -> None:
    target = tmp_path / "note.txt"
    target.write_text("abcğdef", encoding="utf-8")
    service = create_service(tmp_path, max_read_page_bytes=8)

    first = service.read_text_file(
        "workspace",
        "note.txt",
        offset_bytes=0,
        max_bytes=4,
    )
    second = service.read_text_file(
        "workspace",
        "note.txt",
        offset_bytes=first.data["next_offset_bytes"],
        max_bytes=4,
    )

    assert first.succeeded and first.verified
    assert first.data["content"] == "abc"
    assert first.data["bytes_returned"] == 3
    assert first.data["truncated"] is True
    assert first.data["verification"]["page_sha256"] == hashlib.sha256(
        b"abc"
    ).hexdigest()
    assert second.data["content"] == "ğde"


def test_read_rejects_invalid_offsets_limits_and_binary_text(tmp_path: Path) -> None:
    (tmp_path / "binary.bin").write_bytes(b"\xff\x00")
    service = create_service(tmp_path, max_read_page_bytes=8)

    bad_limit = service.read_text_file(
        "workspace",
        "binary.bin",
        max_bytes=9,
    )
    bad_offset = service.read_text_file(
        "workspace",
        "binary.bin",
        offset_bytes=3,
        max_bytes=4,
    )
    binary = service.read_text_file(
        "workspace",
        "binary.bin",
        max_bytes=4,
    )

    assert bad_limit.error == "INVALID_PAGE_LIMIT"
    assert bad_offset.error == "PAGE_OFFSET_OUT_OF_RANGE"
    assert binary.status is ToolExecutionStatus.FAILED
    assert binary.error == "NOT_UTF8_TEXT"


def test_read_rejects_incomplete_utf8_at_eof_without_stalled_pagination(
    tmp_path: Path,
) -> None:
    (tmp_path / "truncated.txt").write_bytes(b"abc\xc4")
    service = create_service(tmp_path, max_read_page_bytes=8)

    result = service.read_text_file(
        "workspace",
        "truncated.txt",
        max_bytes=4,
    )

    assert result.status is ToolExecutionStatus.FAILED
    assert result.error == "NOT_UTF8_TEXT"


def test_directory_listing_is_sorted_paginated_and_path_private(
    tmp_path: Path,
) -> None:
    for name in ("zeta.txt", "Alpha.txt", "mid.txt"):
        (tmp_path / name).write_text(name, encoding="utf-8")
    service = create_service(tmp_path, max_list_entries=2)

    first = service.list_directory("workspace", limit=2)
    second = service.list_directory(
        "workspace",
        offset=first.data["next_offset"],
        limit=2,
    )
    roots = service.list_allowed_roots()

    assert [entry["name"] for entry in first.data["entries"]] == [
        "Alpha.txt",
        "mid.txt",
    ]
    assert first.data["next_offset"] == 2
    assert [entry["name"] for entry in second.data["entries"]] == ["zeta.txt"]
    assert second.data["next_offset"] is None
    assert str(tmp_path) not in str(roots.data)


def test_directory_scan_and_page_limits_are_enforced(tmp_path: Path) -> None:
    for index in range(3):
        (tmp_path / f"{index}.txt").write_text("x", encoding="utf-8")
    service = create_service(
        tmp_path,
        max_list_entries=2,
        max_directory_scan=2,
    )

    scan = service.list_directory("workspace", limit=2)
    page = service.list_directory("workspace", limit=3)

    assert scan.status is ToolExecutionStatus.BLOCKED
    assert scan.error == "DIRECTORY_SCAN_LIMIT_EXCEEDED"
    assert page.status is ToolExecutionStatus.BLOCKED
    assert page.error == "INVALID_PAGE_LIMIT"


def test_write_is_atomic_bounded_verified_and_does_not_overwrite_by_default(
    tmp_path: Path,
) -> None:
    service = create_service(tmp_path, max_write_bytes=16)
    secret = "api-key-value"

    created = service.write_text_file("workspace", "note.txt", secret)
    refused = service.write_text_file("workspace", "note.txt", "replacement")
    replaced = service.write_text_file(
        "workspace",
        "note.txt",
        "new",
        overwrite=True,
    )
    too_large = service.write_text_file("workspace", "large.txt", "x" * 17)

    assert created.succeeded and created.verified
    assert created.data["verification"]["atomic"] is True
    assert created.data["verification"]["sha256"] == hashlib.sha256(
        secret.encode()
    ).hexdigest()
    assert refused.status is ToolExecutionStatus.FAILED
    assert refused.error == "DESTINATION_EXISTS"
    assert replaced.succeeded
    assert (tmp_path / "note.txt").read_text(encoding="utf-8") == "new"
    assert too_large.status is ToolExecutionStatus.BLOCKED
    assert too_large.error == "WRITE_SIZE_LIMIT_EXCEEDED"
    assert secret not in created.message
    assert secret not in (created.error or "")
    assert not list(tmp_path.glob(".jarvis-write-*.tmp"))


def test_post_mutation_verification_failure_is_reported_as_partial(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = create_service(tmp_path)

    def fail_verification(path: Path, *, maximum_bytes: int) -> tuple[str, int]:
        del path, maximum_bytes
        raise OSError("simulated verification failure")

    monkeypatch.setattr(
        BoundedFilesystemService,
        "_sha256_file",
        staticmethod(fail_verification),
    )

    result = service.write_text_file("workspace", "note.txt", "secret")

    assert result.status is ToolExecutionStatus.PARTIAL
    assert result.verified is False
    assert result.side_effects_may_continue is True
    assert result.error == "WRITE_VERIFICATION_FAILED"
    assert "secret" not in result.message
    assert (tmp_path / "note.txt").exists()


def test_create_directory_requires_existing_parent_and_verifies_postcondition(
    tmp_path: Path,
) -> None:
    service = create_service(tmp_path)

    created = service.create_directory("workspace", "documents")
    missing_parent = service.create_directory(
        "workspace",
        "missing/child",
    )

    assert created.succeeded and created.verified
    assert created.data["verification"]["is_directory"] is True
    assert (tmp_path / "documents").is_dir()
    assert missing_parent.status is ToolExecutionStatus.FAILED
    assert missing_parent.error == "PATH_NOT_FOUND"


def test_copy_is_bounded_no_overwrite_and_sha256_verified(tmp_path: Path) -> None:
    source = tmp_path / "source.txt"
    source.write_text("bounded-copy", encoding="utf-8")
    service = create_service(tmp_path, max_transfer_bytes=16)

    copied = service.copy_file("workspace", "source.txt", "copy.txt")
    refused = service.copy_file("workspace", "source.txt", "copy.txt")
    too_large = tmp_path / "large.txt"
    too_large.write_bytes(b"x" * 17)
    bounded = service.copy_file("workspace", "large.txt", "large-copy.txt")

    assert copied.succeeded and copied.verified
    assert copied.data["bytes_copied"] == len(b"bounded-copy")
    assert (tmp_path / "copy.txt").read_bytes() == source.read_bytes()
    assert refused.error == "DESTINATION_EXISTS"
    assert bounded.status is ToolExecutionStatus.BLOCKED
    assert bounded.error == "TRANSFER_SIZE_LIMIT_EXCEEDED"
    assert not (tmp_path / "large-copy.txt").exists()


def test_move_verifies_source_absence_and_destination_digest(tmp_path: Path) -> None:
    source = tmp_path / "source.txt"
    source.write_text("bounded-move", encoding="utf-8")
    service = create_service(tmp_path)

    moved = service.move_file("workspace", "source.txt", "moved.txt")

    assert moved.succeeded and moved.verified
    assert moved.data["verification"]["source_absent"] is True
    assert not source.exists()
    assert (tmp_path / "moved.txt").read_text(encoding="utf-8") == "bounded-move"


def test_delete_fails_closed_and_is_not_registered_as_a_tool(tmp_path: Path) -> None:
    target = tmp_path / "keep.txt"
    target.write_text("keep", encoding="utf-8")
    service = create_service(tmp_path)
    executor = ToolExecutor()
    service.register_tools(executor)

    result = service.delete_path("workspace", "keep.txt")

    assert result.status is ToolExecutionStatus.BLOCKED
    assert result.error == "RECOVERABLE_DELETE_UNAVAILABLE"
    assert target.exists()
    assert "delete_path" not in executor.list_names()


def test_registered_contracts_are_typed_scoped_and_risk_explicit(
    tmp_path: Path,
) -> None:
    executor = ToolExecutor()
    create_service(tmp_path).register_tools(executor)

    assert executor.list_names() == (
        "list_allowed_file_roots",
        "list_directory",
        "read_text_file",
        "write_text_file",
        "create_directory",
        "copy_file",
        "move_file",
    )
    contracts = {
        contract.definition.name: contract
        for contract in executor.get_contract_objects()
    }
    for name in ("list_allowed_file_roots", "list_directory", "read_text_file"):
        assert contracts[name].definition.risk_level is RiskLevel.READ_ONLY
        assert contracts[name].definition.requires_confirmation is False
    for name in ("write_text_file", "create_directory", "copy_file", "move_file"):
        definition = contracts[name].definition
        assert definition.risk_level is RiskLevel.MEDIUM
        assert definition.requires_confirmation is True
        assert definition.retry_max_attempts == 1
        assert definition.metadata["path_scope"] == (
            "allowed_root_id_and_relative_path_only"
        )
        assert definition.metadata["sensitive_content_logged"] is False
    write_schema = contracts["write_text_file"].input_schema
    assert write_schema["properties"]["root_id"]["type"] == "string"
    assert write_schema["properties"]["path"]["type"] == "string"
    assert write_schema["properties"]["content"]["type"] == "string"
    assert write_schema["properties"]["overwrite"]["default"] is False


def test_tool_executor_allows_read_only_and_blocks_unconfirmed_mutation(
    tmp_path: Path,
) -> None:
    (tmp_path / "note.txt").write_text("hello", encoding="utf-8")
    executor = ToolExecutor()
    create_service(tmp_path).register_tools(executor)

    read = executor.execute(
        "read_text_file",
        parameters={"root_id": "workspace", "path": "note.txt"},
    )
    write = executor.execute(
        "write_text_file",
        parameters={
            "root_id": "workspace",
            "path": "new.txt",
            "content": "new",
        },
    )

    assert read.succeeded and read.verified
    assert write.status is ToolExecutionStatus.BLOCKED
    assert not (tmp_path / "new.txt").exists()


@pytest.mark.parametrize(
    "kwargs",
    [
        {"max_read_page_bytes": 0},
        {"max_write_bytes": True},
        {"max_transfer_bytes": -1},
        {"max_list_entries": 0},
        {"max_directory_scan": 0},
        {"max_list_entries": 3, "max_directory_scan": 2},
    ],
)
def test_resource_limits_must_be_positive_and_consistent(kwargs) -> None:
    with pytest.raises(ValueError):
        BoundedFilesystemService(**kwargs)
