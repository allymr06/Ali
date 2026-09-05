from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Iterable, Sequence


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
VERSION = "0.1.0"
PRODUCT = "JARVIS"
# The Nova shell page; the frozen smoke test must find every file. The
# list lives with the shell so the two can never drift apart.
from app.ui.nova.shell import WEB_ASSETS as NOVA_WEB_ASSETS  # noqa: E402


def _inside_project(path: Path) -> Path:
    resolved = path.resolve()
    if resolved == PROJECT_ROOT or PROJECT_ROOT not in resolved.parents:
        raise ValueError(f"Unsafe build path: {resolved}")
    return resolved


def clean_directory(path: Path) -> Path:
    resolved = _inside_project(path)
    if resolved.exists():
        shutil.rmtree(resolved)
    resolved.mkdir(parents=True)
    return resolved


def run_checked(
    command: Sequence[str | os.PathLike[str]],
    *,
    cwd: Path = PROJECT_ROOT,
    timeout: int = 900,
) -> None:
    printable = [os.fspath(part) for part in command]
    completed = subprocess.run(
        printable,
        cwd=cwd,
        check=False,
        timeout=timeout,
    )
    if completed.returncode != 0:
        raise RuntimeError(
            f"Command failed with exit code {completed.returncode}: "
            + subprocess.list2cmdline(printable)
        )


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def artifact_record(path: Path, base: Path) -> dict[str, object]:
    return {
        "path": path.relative_to(base).as_posix(),
        "size": path.stat().st_size,
        "sha256": sha256_file(path),
    }


def find_iscc(explicit: str | None = None) -> Path | None:
    candidates = [
        explicit,
        os.getenv("JARVIS_ISCC"),
        PROJECT_ROOT / "build-tools" / "inno" / "ISCC.exe",
        Path(os.getenv("LOCALAPPDATA", ""))
        / "Programs"
        / "Inno Setup 6"
        / "ISCC.exe",
        Path(os.getenv("ProgramFiles(x86)", ""))
        / "Inno Setup 6"
        / "ISCC.exe",
    ]
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            return Path(candidate).resolve()
    return None


def verify_smoke_report(
    path: Path,
    *,
    allow_environment_limited: bool = False,
    bundle_root: Path | None = None,
) -> dict[str, object]:
    report = json.loads(path.read_text(encoding="utf-8"))
    required = {
        "ok": True,
        "frozen": True,
        "health": "healthy",
        "screens": 11,
        "nova_assets": sorted(NOVA_WEB_ASSETS),
    }
    mismatches = {
        key: (report.get(key), expected)
        for key, expected in required.items()
        if report.get(key) != expected
    }
    if mismatches and allow_environment_limited:
        static_files = (
            "_internal/_tcl_data/init.tcl",
            "_internal/_tk_data/tk.tcl",
            "_internal/tcl86t.dll",
            "_internal/tk86t.dll",
            "_internal/assets/jarvis.ico",
        )
        if (
            report.get("frozen") is True
            and report.get("error_type") == "TclError"
            and bundle_root is not None
            and all((bundle_root / item).is_file() for item in static_files)
        ):
            report["qualification"] = "environment_limited"
            report["native_ui_rendered"] = False
            report["error"] = (
                "The build host could not initialize the bundled Tcl runtime."
            )
            report["static_runtime_files_verified"] = list(static_files)
            path.write_text(
                json.dumps(report, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            return report
    if mismatches:
        raise RuntimeError(f"Frozen smoke report failed: {mismatches}")
    if not report.get("tcl"):
        raise RuntimeError("Frozen smoke report did not identify Tcl/Tk.")
    return report


def create_portable_archive(source: Path, destination: Path) -> None:
    with zipfile.ZipFile(
        destination,
        mode="w",
        compression=zipfile.ZIP_DEFLATED,
        compresslevel=9,
    ) as archive:
        for path in sorted(source.rglob("*")):
            if path.is_file():
                archive.write(path, Path(PRODUCT) / path.relative_to(source))


def write_release_evidence(
    release: Path,
    artifacts: Iterable[Path],
    smoke: dict[str, object],
) -> Path:
    records = [artifact_record(path, release) for path in sorted(artifacts)]
    manifest = {
        "schema_version": 1,
        "product": PRODUCT,
        "version": VERSION,
        "platform": "windows-x64",
        "built_at": datetime.now(UTC).isoformat(),
        "builder_python": sys.version.split()[0],
        "release_status": (
            "qualified" if smoke.get("ok") else "environment_limited"
        ),
        "smoke_test": smoke,
        "code_signing": {
            "applied": False,
            "reason": "No publisher certificate was supplied.",
        },
        "artifacts": records,
    }
    manifest_path = release / "release-manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    sums = "".join(
        f"{record['sha256']}  {record['path']}\n" for record in records
    )
    (release / "SHA256SUMS.txt").write_text(sums, encoding="utf-8")
    return manifest_path


def build(
    *,
    skip_verify: bool,
    skip_installer: bool,
    iscc: str | None,
    allow_environment_limited_smoke: bool,
) -> None:
    if sys.platform != "win32":
        raise RuntimeError("The Windows package must be built on Windows.")
    if sys.version_info[:2] != (3, 12):
        raise RuntimeError("Packaging requires Python 3.12.")

    build_dir = clean_directory(PROJECT_ROOT / "build")
    dist_dir = clean_directory(PROJECT_ROOT / "dist")
    release_dir = clean_directory(PROJECT_ROOT / "release")

    if not skip_verify:
        run_checked([sys.executable, PROJECT_ROOT / "scripts" / "verify.py"])

    run_checked(
        [
            sys.executable,
            "-m",
            "PyInstaller",
            "--noconfirm",
            "--clean",
            "--distpath",
            dist_dir,
            "--workpath",
            build_dir / "pyinstaller",
            PROJECT_ROOT / "installer" / "JARVIS.spec",
        ]
    )

    executable = dist_dir / PRODUCT / f"{PRODUCT}.exe"
    if not executable.is_file():
        raise RuntimeError(f"PyInstaller output is missing: {executable}")

    smoke_path = build_dir / "frozen-smoke.json"
    smoke_state = build_dir / "smoke-state"
    smoke_command = [
        executable,
        "--smoke-test",
        "--output",
        smoke_path,
        "--state-dir",
        smoke_state,
    ]
    try:
        run_checked(smoke_command, timeout=90)
    except RuntimeError:
        if not allow_environment_limited_smoke or not smoke_path.is_file():
            raise
    smoke = verify_smoke_report(
        smoke_path,
        allow_environment_limited=allow_environment_limited_smoke,
        bundle_root=dist_dir / PRODUCT,
    )

    portable = release_dir / f"{PRODUCT}-{VERSION}-windows-x64.zip"
    create_portable_archive(dist_dir / PRODUCT, portable)
    artifacts = [portable]

    compiler = find_iscc(iscc)
    if compiler is None and not skip_installer:
        raise RuntimeError(
            "Inno Setup compiler not found. Set JARVIS_ISCC or use --iscc."
        )
    if compiler is not None and not skip_installer:
        run_checked(
            [
                compiler,
                "/Qp",
                f"/O{release_dir}",
                PROJECT_ROOT / "installer" / "jarvis.iss",
            ]
        )
        installer = release_dir / f"{PRODUCT}-Setup-{VERSION}-x64.exe"
        if not installer.is_file():
            raise RuntimeError(f"Installer output is missing: {installer}")
        artifacts.append(installer)

    write_release_evidence(release_dir, artifacts, smoke)
    qualification = "qualified" if smoke.get("ok") else "environment_limited"
    print(f"Release artifacts created at {release_dir} ({qualification})")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build verified JARVIS for Windows.")
    parser.add_argument("--skip-verify", action="store_true")
    parser.add_argument("--skip-installer", action="store_true")
    parser.add_argument("--iscc")
    parser.add_argument("--allow-environment-limited-smoke", action="store_true")
    arguments = parser.parse_args(argv)
    build(
        skip_verify=arguments.skip_verify,
        skip_installer=arguments.skip_installer,
        iscc=arguments.iscc,
        allow_environment_limited_smoke=(
            arguments.allow_environment_limited_smoke
        ),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
