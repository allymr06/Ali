from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest

from app.ui.nova import shell as nova_shell
from scripts import build_windows


def test_clean_directory_rejects_project_root() -> None:
    with pytest.raises(ValueError, match="Unsafe build path"):
        build_windows.clean_directory(build_windows.PROJECT_ROOT)


def test_clean_directory_rejects_path_outside_project(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="Unsafe build path"):
        build_windows.clean_directory(tmp_path)


def test_verify_smoke_report_accepts_complete_frozen_result(tmp_path: Path) -> None:
    path = tmp_path / "smoke.json"
    expected = {
        "ok": True,
        "frozen": True,
        "health": "healthy",
        "screens": 11,
        "nova_assets": ["index.html", "nova.css", "nova.js"],
        "tcl": "8.6.14",
    }
    path.write_text(json.dumps(expected), encoding="utf-8")

    assert build_windows.verify_smoke_report(path) == expected


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("ok", False),
        ("frozen", False),
        ("health", "degraded"),
        ("screens", 10),
        ("nova_assets", ["index.html"]),
        ("nova_assets", None),
        ("tcl", ""),
    ],
)
def test_verify_smoke_report_rejects_incomplete_result(
    tmp_path: Path,
    field: str,
    value: object,
) -> None:
    path = tmp_path / "smoke.json"
    report = {
        "ok": True,
        "frozen": True,
        "health": "healthy",
        "screens": 11,
        "nova_assets": ["index.html", "nova.css", "nova.js"],
        "tcl": "8.6.14",
    }
    report[field] = value
    path.write_text(json.dumps(report), encoding="utf-8")

    with pytest.raises(RuntimeError, match="smoke report"):
        build_windows.verify_smoke_report(path)


def test_environment_limited_smoke_requires_complete_static_runtime(
    tmp_path: Path,
) -> None:
    report_path = tmp_path / "smoke.json"
    report_path.write_text(
        json.dumps(
            {
                "ok": False,
                "frozen": True,
                "error_type": "TclError",
                "error": "native file access unavailable",
            }
        ),
        encoding="utf-8",
    )
    required = (
        "_internal/_tcl_data/init.tcl",
        "_internal/_tk_data/tk.tcl",
        "_internal/tcl86t.dll",
        "_internal/tk86t.dll",
        "_internal/assets/jarvis.ico",
    )
    for relative in required:
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"present")

    report = build_windows.verify_smoke_report(
        report_path,
        allow_environment_limited=True,
        bundle_root=tmp_path,
    )

    assert report["qualification"] == "environment_limited"
    assert report["native_ui_rendered"] is False


def test_environment_limited_smoke_fails_when_runtime_file_is_missing(
    tmp_path: Path,
) -> None:
    report_path = tmp_path / "smoke.json"
    report_path.write_text(
        json.dumps({"ok": False, "frozen": True, "error_type": "TclError"}),
        encoding="utf-8",
    )

    with pytest.raises(RuntimeError, match="smoke report"):
        build_windows.verify_smoke_report(
            report_path,
            allow_environment_limited=True,
            bundle_root=tmp_path,
        )


def test_portable_archive_has_single_product_root(tmp_path: Path) -> None:
    source = tmp_path / "source"
    (source / "nested").mkdir(parents=True)
    (source / "JARVIS.exe").write_bytes(b"exe")
    (source / "nested" / "data.txt").write_text("data", encoding="utf-8")
    destination = tmp_path / "portable.zip"

    build_windows.create_portable_archive(source, destination)

    with zipfile.ZipFile(destination) as archive:
        assert archive.namelist() == [
            "JARVIS/JARVIS.exe",
            "JARVIS/nested/data.txt",
        ]


def test_release_evidence_hashes_only_supplied_artifacts(tmp_path: Path) -> None:
    artifact = tmp_path / "artifact.bin"
    artifact.write_bytes(b"JARVIS")
    smoke = {"ok": True, "frozen": True}

    path = build_windows.write_release_evidence(tmp_path, [artifact], smoke)
    manifest = json.loads(path.read_text(encoding="utf-8"))

    assert manifest["version"] == "0.1.0"
    assert manifest["smoke_test"] == smoke
    assert manifest["code_signing"]["applied"] is False
    assert manifest["artifacts"] == [
        {
            "path": "artifact.bin",
            "size": 6,
            "sha256": build_windows.sha256_file(artifact),
        }
    ]
    assert (tmp_path / "SHA256SUMS.txt").read_text(encoding="utf-8") == (
        f"{build_windows.sha256_file(artifact)}  artifact.bin\n"
    )


def test_nova_asset_list_is_shared_with_the_shell() -> None:
    assert build_windows.NOVA_WEB_ASSETS == nova_shell.WEB_ASSETS


def test_spec_bundles_nova_web_assets_where_the_shell_looks() -> None:
    spec = (build_windows.PROJECT_ROOT / "installer" / "JARVIS.spec").read_text(
        encoding="utf-8"
    )
    assert 'nova_web = root / "app" / "ui" / "nova" / "web"' in spec
    for name in build_windows.NOVA_WEB_ASSETS:
        assert f'(str(nova_web / "{name}"), "app/ui/nova/web")' in spec, name
    for hidden in ('"app.ui.nova"', '"app.ui.nova.shell"', '"webview"'):
        assert hidden in spec, hidden
    assert str(nova_shell.WEB_RELATIVE_PATH).replace("\\", "/") == "app/ui/nova/web"
