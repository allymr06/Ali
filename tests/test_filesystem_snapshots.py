"""Recoverable filesystem snapshots: capture, verify, restore, bounds."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from app.platform.windows.snapshots import (
    FilesystemSnapshotStore,
    SnapshotError,
    SnapshotRecord,
)


def store(tmp_path: Path, **kwargs) -> FilesystemSnapshotStore:
    return FilesystemSnapshotStore(tmp_path / "snapshots", **kwargs)


def test_capture_seals_bytes_with_a_verified_manifest(tmp_path: Path) -> None:
    source = tmp_path / "note.txt"
    source.write_bytes(b"first version\n")
    snapshots = store(tmp_path)

    record = snapshots.capture(
        root_id="workspace", path="note.txt", source=source,
        reason="overwrite", tool_name="write_text_file",
    )

    assert isinstance(record, SnapshotRecord)
    assert record.size_bytes == 14
    assert record.sha256 == hashlib.sha256(b"first version\n").hexdigest()
    assert record.reason == "overwrite" and record.tool_name == "write_text_file"
    listed = snapshots.list()
    assert [item.snapshot_id for item in listed] == [record.snapshot_id]
    assert snapshots.get(record.snapshot_id) == record
    again, payload = snapshots.read_payload(record.snapshot_id)
    assert again == record and payload == b"first version\n"
    manifest = json.loads((snapshots.directory / f"{record.snapshot_id}.json").read_text("utf-8"))
    assert manifest["version"] == 1 and manifest["path"] == "note.txt"
    assert not list(snapshots.directory.glob("*.tmp"))


def test_capture_refuses_oversized_and_invalid_input(tmp_path: Path) -> None:
    source = tmp_path / "big.bin"
    source.write_bytes(b"x" * 33)
    snapshots = store(tmp_path, max_file_bytes=32, max_total_bytes=64)

    with pytest.raises(SnapshotError, match="SNAPSHOT_TOO_LARGE"):
        snapshots.capture(root_id="w", path="big.bin", source=source, reason="delete", tool_name="t")
    with pytest.raises(SnapshotError, match="INVALID_SNAPSHOT_REASON"):
        snapshots.capture(root_id="w", path="big.bin", source=source, reason="oops", tool_name="t")
    assert snapshots.list() == ()
    assert not any(snapshots.directory.glob("*")) if snapshots.directory.exists() else True


def test_corrupted_payload_is_never_handed_back(tmp_path: Path) -> None:
    source = tmp_path / "note.txt"
    source.write_bytes(b"trusted")
    snapshots = store(tmp_path)
    record = snapshots.capture(root_id="w", path="note.txt", source=source, reason="delete", tool_name="t")

    (snapshots.directory / f"{record.snapshot_id}.bin").write_bytes(b"tampered")

    with pytest.raises(SnapshotError, match="SNAPSHOT_CORRUPT"):
        snapshots.read_payload(record.snapshot_id)
    with pytest.raises(SnapshotError, match="SNAPSHOT_NOT_FOUND"):
        snapshots.read_payload("0" * 32)
    with pytest.raises(SnapshotError, match="INVALID_SNAPSHOT_ID"):
        snapshots.read_payload("../etc/passwd")


def test_unreadable_manifests_are_ignored(tmp_path: Path) -> None:
    snapshots = store(tmp_path)
    snapshots.directory.mkdir(parents=True)
    (snapshots.directory / ("a" * 32 + ".json")).write_text("{not json", encoding="utf-8")
    (snapshots.directory / ("b" * 32 + ".json")).write_text(
        json.dumps({"version": 1, "snapshot_id": "b" * 32, "root_id": "w", "path": "x",
                    "size_bytes": 1, "sha256": "00", "reason": "delete",
                    "created_at": "now", "tool_name": "t"}),
        encoding="utf-8",
    )  # manifest without its payload

    assert snapshots.list() == ()
    assert snapshots.get("b" * 32) is None


def test_prune_drops_the_oldest_until_bounds_fit(tmp_path: Path) -> None:
    snapshots = store(tmp_path, max_entries=2, max_total_bytes=64, max_file_bytes=32)
    records = []
    for index in range(4):
        source = tmp_path / f"file{index}.txt"
        source.write_bytes(bytes([index]) * 10)
        records.append(
            snapshots.capture(root_id="w", path=source.name, source=source, reason="delete", tool_name="t")
        )

    remaining = [item.snapshot_id for item in snapshots.list()]
    assert len(remaining) == 2
    assert records[-1].snapshot_id in remaining and records[0].snapshot_id not in remaining
    usage = snapshots.usage()
    assert usage["entries"] == 2 and usage["bytes"] == 20
    assert snapshots.discard(records[-1].snapshot_id) is True
    assert snapshots.discard(records[-1].snapshot_id) is False


def test_store_bounds_are_validated(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        store(tmp_path, max_entries=0)
    with pytest.raises(ValueError):
        store(tmp_path, max_file_bytes=10, max_total_bytes=5)
