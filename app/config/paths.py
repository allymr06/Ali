from __future__ import annotations

import os
import shutil
import sqlite3
from pathlib import Path


def default_state_directory() -> Path:
    """Return a stable per-user data directory independent of the CWD."""
    override = os.getenv("JARVIS_STATE_DIRECTORY")
    if override:
        return Path(override).expanduser().resolve()

    local_app_data = os.getenv("LOCALAPPDATA")
    if local_app_data:
        return (Path(local_app_data) / "JARVIS").resolve()

    return (Path.home() / ".jarvis").resolve()


def default_state_path(name: str) -> str:
    return str(default_state_directory() / name)


def project_data_directory() -> Path:
    return Path(__file__).resolve().parents[2] / "data"


def migrate_default_sqlite(path: str | Path | None, name: str) -> bool:
    """Copy a legacy project database into the canonical state directory once."""
    if path is None:
        return False
    destination = Path(path).expanduser().resolve()
    expected = (default_state_directory() / name).resolve()
    legacy = (project_data_directory() / name).resolve()
    if destination != expected or destination.exists() or not legacy.is_file():
        return False
    if destination == legacy:
        return False

    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".migrate")
    temporary.unlink(missing_ok=True)
    source = sqlite3.connect(
        f"file:{legacy.as_posix()}?mode=ro",
        uri=True,
        timeout=5.0,
    )
    target = sqlite3.connect(temporary, timeout=5.0)
    try:
        source.backup(target)
        if target.execute("PRAGMA quick_check").fetchone()[0] != "ok":
            raise sqlite3.DatabaseError("Migrated database failed quick_check.")
    finally:
        target.close()
        source.close()
    temporary.replace(destination)
    return True


def migrate_default_directory(path: str | Path | None, name: str) -> bool:
    if path is None:
        return False
    destination = Path(path).expanduser().resolve()
    expected = (default_state_directory() / name).resolve()
    legacy = (project_data_directory() / name).resolve()
    if destination != expected or destination.exists() or not legacy.is_dir():
        return False
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(legacy, destination)
    return True


def migrate_default_file(path: str | Path, name: str) -> bool:
    destination = Path(path).expanduser().resolve()
    expected = (default_state_directory() / name).resolve()
    legacy = (project_data_directory() / name).resolve()
    if destination != expected or destination.exists() or not legacy.is_file():
        return False
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".migrate")
    shutil.copy2(legacy, temporary)
    temporary.replace(destination)
    return True
