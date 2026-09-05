"""Safe-filesystem extensions: recoverable delete and undo, snapshots on
overwrite, bounded name search, dry-run plans applied by digest, and the
critical-directory block."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

import pytest

from app.core.models import RiskLevel, ToolExecutionStatus
from app.platform.windows.filesystem import (
    BoundedFilesystemService,
    default_critical_paths,
)
from app.platform.windows.snapshots import FilesystemSnapshotStore
from app.tools.executor import ToolExecutor


def service_with_snapshots(tmp_path: Path, **kwargs) -> tuple[BoundedFilesystemService, Path]:
    root = tmp_path / "workspace"
    root.mkdir(exist_ok=True)
    snapshots = FilesystemSnapshotStore(tmp_path / "snapshots", max_file_bytes=1024 * 1024)
    service = BoundedFilesystemService(
        {"workspace": root}, snapshots=snapshots, critical_paths=(), **kwargs
    )
    return service, root


# ---------------------------------------------------------------------------
# recoverable delete and undo
# ---------------------------------------------------------------------------


def test_delete_seals_a_snapshot_and_undo_restores_the_bytes(tmp_path: Path) -> None:
    service, root = service_with_snapshots(tmp_path)
    (root / "keep.txt").write_text("precious", encoding="utf-8")

    deleted = service.delete_path("workspace", "keep.txt")

    assert deleted.succeeded and deleted.verified
    assert not (root / "keep.txt").exists()
    snapshot_id = deleted.data["snapshot"]["snapshot_id"]
    assert deleted.data["verification"]["recoverable"] is True
    listing = service.list_filesystem_snapshots()
    assert listing.data["snapshots"][0]["snapshot_id"] == snapshot_id
    assert listing.data["snapshots"][0]["path"] == "keep.txt"

    restored = service.undo_filesystem_change(snapshot_id)

    assert restored.succeeded and restored.verified
    assert (root / "keep.txt").read_text(encoding="utf-8") == "precious"
    assert restored.data["restored_snapshot_id"] == snapshot_id
    assert restored.data["replaced"] is None
    assert restored.data["verification"]["sha256"] == hashlib.sha256(b"precious").hexdigest()


def test_undo_snapshots_the_current_file_so_it_can_be_undone_too(tmp_path: Path) -> None:
    service, root = service_with_snapshots(tmp_path)
    (root / "note.txt").write_text("v1", encoding="utf-8")
    replaced = service.write_text_file("workspace", "note.txt", "v2", overwrite=True)
    first = replaced.data["snapshot"]["snapshot_id"]

    undone = service.undo_filesystem_change(first)

    assert undone.succeeded
    assert (root / "note.txt").read_text(encoding="utf-8") == "v1"
    assert undone.data["replaced"]["reason"] == "undo"
    redo = service.undo_filesystem_change(undone.data["replaced"]["snapshot_id"])
    assert redo.succeeded
    assert (root / "note.txt").read_text(encoding="utf-8") == "v2"


def test_delete_refuses_directories_with_content_and_reparse_points(tmp_path: Path) -> None:
    service, root = service_with_snapshots(tmp_path)
    (root / "folder").mkdir()
    (root / "folder" / "inner.txt").write_text("x", encoding="utf-8")
    (root / "empty").mkdir()

    full = service.delete_path("workspace", "folder")
    empty = service.delete_path("workspace", "empty")
    missing = service.delete_path("workspace", "nothing")

    assert full.status is ToolExecutionStatus.BLOCKED
    assert full.error == "DIRECTORY_NOT_EMPTY"
    assert (root / "folder" / "inner.txt").exists()
    assert empty.succeeded and empty.data["kind"] == "directory"
    assert empty.data["snapshot"] is None
    assert missing.status is ToolExecutionStatus.FAILED
    assert missing.error == "PATH_NOT_FOUND"

    link = root / "link.txt"
    try:
        link.symlink_to(root / "folder" / "inner.txt")
    except OSError as exc:
        pytest.skip(f"Creating symlinks is unavailable: {exc}")
    linked = service.delete_path("workspace", "link.txt")
    assert linked.status is ToolExecutionStatus.BLOCKED
    assert linked.error in {"REPARSE_PATH_REJECTED", "REPARSE_SOURCE_REJECTED"}
    assert link.is_symlink()


def test_file_delete_fails_closed_without_a_snapshot_store(tmp_path: Path) -> None:
    root = tmp_path / "workspace"
    root.mkdir()
    (root / "keep.txt").write_text("keep", encoding="utf-8")
    service = BoundedFilesystemService({"workspace": root}, critical_paths=())

    result = service.delete_path("workspace", "keep.txt")
    undo = service.undo_filesystem_change("0" * 32)
    listing = service.list_filesystem_snapshots()

    assert result.status is ToolExecutionStatus.BLOCKED
    assert result.error == "RECOVERABLE_DELETE_UNAVAILABLE"
    assert (root / "keep.txt").exists()
    assert undo.status is ToolExecutionStatus.BLOCKED
    assert undo.error == "SNAPSHOT_STORE_UNAVAILABLE"
    assert listing.succeeded and listing.data["available"] is False


def test_overwrites_seal_the_replaced_file(tmp_path: Path) -> None:
    service, root = service_with_snapshots(tmp_path)
    (root / "a.txt").write_text("alpha", encoding="utf-8")
    (root / "b.txt").write_text("beta", encoding="utf-8")
    (root / "c.txt").write_text("gamma", encoding="utf-8")

    written = service.write_text_file("workspace", "a.txt", "alpha2", overwrite=True)
    copied = service.copy_file("workspace", "a.txt", "b.txt", overwrite=True)
    moved = service.move_file("workspace", "b.txt", "c.txt", overwrite=True)
    fresh = service.write_text_file("workspace", "d.txt", "delta")

    assert written.data["snapshot"]["reason"] == "overwrite"
    assert copied.data["snapshot"]["sha256"] == hashlib.sha256(b"beta").hexdigest()
    assert moved.data["snapshot"]["sha256"] == hashlib.sha256(b"gamma").hexdigest()
    assert fresh.data["snapshot"] is None
    reasons = [item["reason"] for item in service.list_filesystem_snapshots().data["snapshots"]]
    assert sorted(reasons) == ["overwrite", "overwrite", "overwrite"]


def test_oversized_file_blocks_the_mutation_instead_of_losing_it(tmp_path: Path) -> None:
    root = tmp_path / "workspace"
    root.mkdir()
    (root / "big.txt").write_bytes(b"x" * 2048)
    snapshots = FilesystemSnapshotStore(tmp_path / "snapshots", max_file_bytes=1024)
    service = BoundedFilesystemService({"workspace": root}, snapshots=snapshots, critical_paths=())

    blocked = service.write_text_file("workspace", "big.txt", "small", overwrite=True)
    deleted = service.delete_path("workspace", "big.txt")

    assert blocked.status is ToolExecutionStatus.BLOCKED
    assert blocked.error == "SNAPSHOT_TOO_LARGE"
    assert deleted.status is ToolExecutionStatus.BLOCKED
    assert (root / "big.txt").stat().st_size == 2048


# ---------------------------------------------------------------------------
# search
# ---------------------------------------------------------------------------


def test_search_matches_names_and_globs_without_following_links(tmp_path: Path) -> None:
    service, root = service_with_snapshots(tmp_path)
    (root / "docs").mkdir()
    (root / "docs" / "Rapor-2026.md").write_text("r", encoding="utf-8")
    (root / "docs" / "notlar.txt").write_text("n", encoding="utf-8")
    (root / "rapor.txt").write_text("r", encoding="utf-8")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret-rapor.txt").write_text("s", encoding="utf-8")
    try:
        (root / "escape").symlink_to(outside, target_is_directory=True)
        linked = True
    except OSError:
        linked = False

    result = service.search_files("workspace", "rapor")
    globbed = service.search_files("workspace", "*.md")
    scoped = service.search_files("workspace", "rapor", path="docs")
    by_path = service.search_files("workspace", "docs/not")

    assert result.succeeded and result.verified
    paths = [item["path"] for item in result.data["matches"]]
    assert paths == ["docs/Rapor-2026.md", "rapor.txt"]
    assert "escape/secret-rapor.txt" not in paths
    assert result.data["verification"]["links_followed"] is False
    assert [item["path"] for item in globbed.data["matches"]] == ["docs/Rapor-2026.md"]
    assert [item["path"] for item in scoped.data["matches"]] == ["docs/Rapor-2026.md"]
    assert scoped.data["path"] == "docs"
    assert [item["path"] for item in by_path.data["matches"]] == ["docs/notlar.txt"]
    if linked:
        kinds = {item["path"]: item["kind"] for item in service.search_files("workspace", "escape").data["matches"]}
        assert kinds == {"escape": "link"}


def test_search_is_bounded_and_validated(tmp_path: Path) -> None:
    service, root = service_with_snapshots(tmp_path, max_index_entries=3, max_list_entries=50)
    for index in range(5):
        (root / f"file{index}.txt").write_text("x", encoding="utf-8")

    limited = service.search_files("workspace", "file", limit=2)
    assert limited.data["index_truncated"] is True
    assert limited.data["indexed_entries"] == 3
    assert len(limited.data["matches"]) == 2
    assert limited.data["truncated_results"] is True

    assert service.search_files("workspace", "   ").error == "INVALID_SEARCH_QUERY"
    assert service.search_files("workspace", "x" * 201).error == "INVALID_SEARCH_QUERY"
    assert service.search_files("workspace", "file", limit=99).error == "INVALID_PAGE_LIMIT"
    assert service.search_files("workspace", "file", path="../x").error == "PATH_TRAVERSAL_REJECTED"
    assert service.search_files("nope", "file").error == "ROOT_NOT_ALLOWED"

    (root / "file9.txt").write_text("x", encoding="utf-8")
    cached = service.search_files("workspace", "file9")
    refreshed = service.search_files("workspace", "file9", refresh=True)
    assert cached.data["total_matches"] == 0  # index still cached
    assert refreshed.data["indexed_entries"] == 3 and refreshed.data["index_age_seconds"] < 5


# ---------------------------------------------------------------------------
# plans
# ---------------------------------------------------------------------------


def test_plan_is_a_dry_run_and_apply_needs_the_digest(tmp_path: Path) -> None:
    service, root = service_with_snapshots(tmp_path)
    (root / "old.txt").write_text("old", encoding="utf-8")
    operations = [
        {"action": "create_directory", "path": "out"},
        {"action": "write", "path": "out/new.txt", "content": "hello"},
        {"action": "copy", "source_path": "old.txt", "destination_path": "out/copy.txt"},
        {"action": "move", "source_path": "out/copy.txt", "destination_path": "out/moved.txt"},
        {"action": "delete", "path": "old.txt"},
    ]

    plan = service.plan_filesystem_changes("workspace", operations)

    assert plan.succeeded and plan.data["ready"] is True
    assert plan.data["mutated"] is False
    assert not (root / "out").exists() and (root / "old.txt").exists()
    assert plan.data["target_count"] == 5
    assert plan.data["snapshots_required"] == 1
    assert [item["status"] for item in plan.data["operations"]] == ["ready"] * 5
    plan_id, digest = plan.data["plan_id"], plan.data["digest"]

    wrong = service.apply_filesystem_plan(plan_id, "0" * 64)
    assert wrong.status is ToolExecutionStatus.BLOCKED
    assert wrong.error == "PLAN_DIGEST_MISMATCH"
    assert not (root / "out").exists()

    applied = service.apply_filesystem_plan(plan_id, digest)

    assert applied.succeeded and applied.verified
    assert (root / "out" / "new.txt").read_text(encoding="utf-8") == "hello"
    assert (root / "out" / "moved.txt").read_text(encoding="utf-8") == "old"
    assert not (root / "out" / "copy.txt").exists()
    assert not (root / "old.txt").exists()
    assert applied.data["applied"][-1]["snapshot"]["reason"] == "delete"
    assert applied.data["remaining"] == 0

    again = service.apply_filesystem_plan(plan_id, digest)
    assert again.status is ToolExecutionStatus.BLOCKED
    assert again.error == "PLAN_NOT_FOUND"


def test_plan_reports_conflicts_and_refuses_when_targets_drift(tmp_path: Path) -> None:
    service, root = service_with_snapshots(tmp_path)
    (root / "a.txt").write_text("a", encoding="utf-8")

    report = service.plan_filesystem_changes(
        "workspace",
        [
            {"action": "write", "path": "a.txt", "content": "x"},
            {"action": "move", "source_path": "missing.txt", "destination_path": "b.txt"},
            {"action": "write", "path": "nested/deep.txt", "content": "x"},
            {"action": "delete", "path": "a.txt"},
            {"action": "delete", "path": "a.txt"},
        ],
    )

    assert report.succeeded and report.data["ready"] is False
    codes = [item["code"] for item in report.data["operations"]]
    assert codes == [
        "DESTINATION_EXISTS", "PATH_NOT_FOUND", "PATH_NOT_FOUND", None, "PATH_NOT_FOUND",
    ]
    assert "plan_id" not in report.data
    assert (root / "a.txt").read_text(encoding="utf-8") == "a"

    ready = service.plan_filesystem_changes(
        "workspace", [{"action": "write", "path": "a.txt", "content": "new", "overwrite": True}]
    )
    (root / "a.txt").write_text("changed meanwhile", encoding="utf-8")
    drifted = service.apply_filesystem_plan(ready.data["plan_id"], ready.data["digest"])

    assert drifted.status is ToolExecutionStatus.BLOCKED
    assert drifted.error == "PLAN_TARGETS_CHANGED"
    assert (root / "a.txt").read_text(encoding="utf-8") == "changed meanwhile"


def test_plan_validation_is_strict(tmp_path: Path) -> None:
    service, _ = service_with_snapshots(tmp_path, max_plan_operations=2)

    assert service.plan_filesystem_changes("workspace", []).error == "INVALID_PLAN_OPERATIONS"
    assert service.plan_filesystem_changes("workspace", [{}] * 3).error == "PLAN_TOO_LARGE"
    assert service.plan_filesystem_changes("workspace", [{"action": "format"}]).error == "INVALID_PLAN_ACTION"
    assert service.plan_filesystem_changes(
        "workspace", [{"action": "write", "path": "../x", "content": ""}]
    ).error == "PATH_TRAVERSAL_REJECTED"
    assert service.plan_filesystem_changes(
        "workspace", [{"action": "write", "path": "x", "content": "", "extra": 1}]
    ).error == "INVALID_PLAN_OPERATION"
    assert service.apply_filesystem_plan("nope", "nope").error == "PLAN_NOT_FOUND"


def test_plan_stops_at_the_first_failure_and_reports_what_was_applied(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service, root = service_with_snapshots(tmp_path)
    plan = service.plan_filesystem_changes(
        "workspace",
        [
            {"action": "write", "path": "one.txt", "content": "1"},
            {"action": "write", "path": "two.txt", "content": "2"},
        ],
    )
    original = service.write_text_file
    calls = {"count": 0}

    def flaky(root_id, path, content, overwrite=False):
        calls["count"] += 1
        if calls["count"] == 2:
            (root / "two.txt").write_text("stale", encoding="utf-8")  # a race
        return original(root_id, path, content, overwrite=overwrite)

    monkeypatch.setattr(service, "write_text_file", flaky)
    applied = service.apply_filesystem_plan(plan.data["plan_id"], plan.data["digest"])

    assert applied.status is ToolExecutionStatus.PARTIAL
    assert applied.error == "DESTINATION_EXISTS"
    assert [item["index"] for item in applied.data["applied"]] == [0]
    assert applied.data["failed"]["index"] == 1
    assert (root / "one.txt").read_text(encoding="utf-8") == "1"


# ---------------------------------------------------------------------------
# critical directories
# ---------------------------------------------------------------------------


def test_critical_directories_cannot_be_granted(tmp_path: Path) -> None:
    system = tmp_path / "Windows"
    (system / "System32").mkdir(parents=True)
    profile = tmp_path / "Profile"
    (profile / "Documents").mkdir(parents=True)
    recycle = tmp_path / "$Recycle.Bin" / "S-1"
    recycle.mkdir(parents=True)
    critical = ((system.resolve(), True), (profile.resolve(), False))
    service = BoundedFilesystemService(critical_paths=critical)

    with pytest.raises(ValueError, match="critical"):
        service.allow_root("system", system)
    with pytest.raises(ValueError, match="critical"):
        service.allow_root("system32", system / "System32")
    with pytest.raises(ValueError, match="critical"):
        service.allow_root("profile", profile)
    with pytest.raises(ValueError, match="critical"):
        service.allow_root("bin", recycle)
    assert service.allow_root("documents", profile / "Documents").root_id == "documents"
    assert service.list_allowed_roots().data["roots"] == [{"root_id": "documents"}]


def test_snapshot_store_cannot_live_inside_a_granted_root(tmp_path: Path) -> None:
    root = tmp_path / "workspace"
    root.mkdir()
    snapshots = FilesystemSnapshotStore(root / "snapshots")
    service = BoundedFilesystemService(snapshots=snapshots, critical_paths=())

    with pytest.raises(ValueError, match="snapshot store"):
        service.allow_root("workspace", root)


@pytest.mark.skipif(os.name != "nt", reason="Windows environment variables")
def test_default_critical_paths_cover_windows_and_the_state_directory(monkeypatch) -> None:
    monkeypatch.setenv("JARVIS_STATE_DIRECTORY", str(Path.home() / "jarvis-state-test"))
    paths = dict(default_critical_paths())
    windows_root = Path(os.environ["SystemRoot"]).resolve()

    assert paths.get(windows_root) is True
    assert paths.get((Path.home() / "jarvis-state-test").resolve()) is True
    assert paths.get(Path(os.environ["USERPROFILE"]).resolve()) is False


# ---------------------------------------------------------------------------
# contracts
# ---------------------------------------------------------------------------


def test_new_tools_are_registered_with_explicit_risk(tmp_path: Path) -> None:
    service, root = service_with_snapshots(tmp_path)
    executor = ToolExecutor()
    service.register_tools(executor)
    contracts = {
        contract.definition.name: contract.definition
        for contract in executor.get_contract_objects()
    }

    for name in ("search_files", "list_filesystem_snapshots", "plan_filesystem_changes"):
        assert contracts[name].risk_level is RiskLevel.READ_ONLY
        assert contracts[name].requires_confirmation is False
    for name in ("delete_path", "undo_filesystem_change", "apply_filesystem_plan"):
        assert contracts[name].risk_level is RiskLevel.HIGH
        assert contracts[name].requires_confirmation is True
        assert contracts[name].metadata["recoverable_delete_supported"] is True

    (root / "keep.txt").write_text("keep", encoding="utf-8")
    blocked = executor.execute(
        "delete_path", parameters={"root_id": "workspace", "path": "keep.txt"}
    )
    assert blocked.status is ToolExecutionStatus.BLOCKED
    assert (root / "keep.txt").exists()
    searched = executor.execute(
        "search_files", parameters={"root_id": "workspace", "query": "keep"}
    )
    assert searched.succeeded
    planned = executor.execute(
        "plan_filesystem_changes",
        parameters={
            "root_id": "workspace",
            "operations": [{"action": "delete", "path": "keep.txt"}],
        },
    )
    assert planned.succeeded and planned.data["ready"] is True
    assert (root / "keep.txt").exists()
    apply_blocked = executor.execute(
        "apply_filesystem_plan",
        parameters={"plan_id": planned.data["plan_id"], "digest": planned.data["digest"]},
    )
    assert apply_blocked.status is ToolExecutionStatus.BLOCKED
    assert (root / "keep.txt").exists()
