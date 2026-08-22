from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.platform.windows.filesystem import BoundedFilesystemService
from app.platform.windows.root_grants import FilesystemRootGrantStore
from app.platform.windows.service import WindowsIntegrationService


def test_root_store_persists_and_revokes_explicit_grant(tmp_path) -> None:
    granted = tmp_path / "Belgeler"
    granted.mkdir()
    store_path = tmp_path / "grants.json"
    store = FilesystemRootGrantStore(store_path)

    grant = store.grant(granted)
    restored = FilesystemRootGrantStore(store_path)

    assert restored.load() == (grant,)
    assert restored.revoke(grant.root_id) is True
    assert json.loads(store_path.read_text(encoding="utf-8"))["roots"] == []


def test_root_store_fails_closed_for_corrupt_or_relative_state(tmp_path) -> None:
    store_path = tmp_path / "grants.json"
    store_path.write_text("not-json", encoding="utf-8")
    assert FilesystemRootGrantStore(store_path).load() == ()
    store_path.write_text(
        json.dumps(
            {
                "version": 1,
                "roots": [{"root_id": "unsafe", "path": "relative"}],
            }
        ),
        encoding="utf-8",
    )
    assert FilesystemRootGrantStore(store_path).load() == ()


def test_root_store_rejects_drive_wide_grant(tmp_path) -> None:
    drive_root = Path(tmp_path.anchor)
    with pytest.raises(ValueError, match="too broad"):
        FilesystemRootGrantStore(tmp_path / "grants.json").grant(drive_root)


def test_windows_service_updates_live_bounded_root_set(tmp_path) -> None:
    granted = tmp_path / "Çalışma"
    granted.mkdir()
    store = FilesystemRootGrantStore(tmp_path / "grants.json")
    filesystem = BoundedFilesystemService()
    service = WindowsIntegrationService(
        applications=object(),
        processes=object(),
        launcher=object(),
        filesystem=filesystem,
        root_grants=store,
    )

    grant = service.grant_filesystem_root(str(granted))
    roots = filesystem.list_allowed_roots()

    assert roots.verified is True
    assert roots.data["roots"] == [{"root_id": grant.root_id}]
    assert service.revoke_filesystem_root(grant.root_id) is True
    assert filesystem.list_allowed_roots().data["roots"] == []
