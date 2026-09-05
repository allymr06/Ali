"""Plugin discovery: trusted directory only, no links, no code execution."""

from __future__ import annotations

import os

import pytest

from app.plugins.discovery import discover_plugins
from tests.plugin_helpers import MARKER_PLUGIN, install_plugin


def test_discovery_reports_valid_and_rejected_plugins(tmp_path) -> None:
    root = tmp_path / "plugins"
    install_plugin(root, "echo")
    install_plugin(root, "broken", manifest_text="{ not json")
    (root / "Bad Name").mkdir()
    (root / "Bad Name" / "plugin.json").write_text("{}", encoding="utf-8")
    (root / "loose.txt").write_text("not a plugin", encoding="utf-8")
    (root / "empty").mkdir()

    found = discover_plugins(root)

    assert [(item.plugin_id, item.accepted) for item in found] == [
        ("broken", False),
        ("echo", True),
        ("empty", False),
    ]
    assert "not valid" in found[0].error
    assert found[1].manifest is not None
    assert "missing" in found[2].error


def test_discovery_handles_missing_root(tmp_path) -> None:
    assert discover_plugins(tmp_path / "nowhere") == ()


def test_discovery_never_imports_plugin_code(tmp_path) -> None:
    root = tmp_path / "plugins"
    directory = install_plugin(root, "echo", code=MARKER_PLUGIN)

    found = discover_plugins(root)

    assert found[0].accepted
    assert not (directory / "IMPORTED").exists()


def test_discovery_rejects_junctions(tmp_path) -> None:
    winapi = pytest.importorskip("_winapi")
    if not hasattr(winapi, "CreateJunction"):
        pytest.skip("junctions are unavailable on this platform")
    root = tmp_path / "plugins"
    target = install_plugin(tmp_path / "elsewhere", "echo")
    root.mkdir()
    try:
        winapi.CreateJunction(str(target), str(root / "echo"))
    except OSError as exc:  # pragma: no cover - environment dependent
        pytest.skip(f"junction creation unavailable: {exc}")

    found = discover_plugins(root)

    assert len(found) == 1
    assert found[0].plugin_id == "echo"
    assert not found[0].accepted
    assert "junction" in found[0].error


def test_discovery_rejects_symlinked_directories(tmp_path) -> None:
    root = tmp_path / "plugins"
    target = install_plugin(tmp_path / "elsewhere", "echo")
    root.mkdir()
    try:
        os.symlink(target, root / "echo", target_is_directory=True)
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"symlink creation unavailable: {exc}")

    found = discover_plugins(root)

    assert len(found) == 1
    assert not found[0].accepted
    assert "link" in found[0].error
