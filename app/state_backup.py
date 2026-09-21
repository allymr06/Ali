"""Safety copies of the general state databases.

The Medical Academy already keeps weekly copies of its own store; the
rest of what the user cannot recreate — conversations, reminders,
routines, notifications, memory, tasks — lives in SQLite files in the
state directory and had none. Every run copies each database with
SQLite's backup API (consistent even while the app holds them open),
into one stamped folder, and keeps the newest three folders.

Restoring stays manual by design: put the copied file back in place of
the broken one while JARVIS is closed. An automatic restore that picks
the wrong moment does more damage than a missing backup.
"""

from __future__ import annotations

import shutil
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

STATE_BACKUP_DIRNAME = "state-backups"
STATE_BACKUP_PREFIX = "state-"
STATE_BACKUP_KEEP = 3
STATE_BACKUP_EVERY_DAYS = 7
_STAMP_FORMAT = "%Y%m%d-%H%M%S-%f"


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def state_databases(state_dir: Path) -> list[Path]:
    """The SQLite files worth copying, by name, no guesses.

    Only first-level ``*.sqlite3`` files: the medical store keeps its own
    rotation, and WAL/SHM sidecars are consumed by the backup API, not
    copied as files.
    """
    if not state_dir.is_dir():
        return []
    return sorted(
        file
        for file in state_dir.glob("*.sqlite3")
        if file.is_file()
    )


def list_state_backups(state_dir: Path) -> list[dict[str, Any]]:
    """The stamped backup folders, newest first, with their real sizes."""
    root = state_dir / STATE_BACKUP_DIRNAME
    if not root.is_dir():
        return []
    rows: list[dict[str, Any]] = []
    for folder in root.glob(f"{STATE_BACKUP_PREFIX}*"):
        if not folder.is_dir() or folder.name.endswith(".tmp"):
            # A half-written copy from an interrupted run is not a backup.
            continue
        files = [file for file in folder.glob("*.sqlite3") if file.is_file()]
        rows.append(
            {
                "folder": folder.name,
                "path": str(folder),
                "files": len(files),
                "bytes": sum(file.stat().st_size for file in files),
                "at": datetime.fromtimestamp(folder.stat().st_mtime)
                .astimezone()
                .isoformat(),
            }
        )
    rows.sort(key=lambda row: row["folder"], reverse=True)
    return rows


def backup_state_now(
    state_dir: Path, *, keep: int = STATE_BACKUP_KEEP
) -> dict[str, Any]:
    """Copy every state database into one stamped folder, then rotate.

    Refuses when the disk could not hold another full copy safely, and
    copies into a ``.tmp`` folder first so a run cut short by shutdown
    never sits in the rotation looking like a good backup.
    """
    databases = state_databases(state_dir)
    if not databases:
        raise ValueError("Yedeklenecek durum veritabanı bulunamadı.")
    root = state_dir / STATE_BACKUP_DIRNAME
    root.mkdir(parents=True, exist_ok=True)
    total = sum(file.stat().st_size for file in databases)
    free = shutil.disk_usage(root).free
    if free < total * 2:
        raise ValueError(
            "Diskte yer yok: yedek için en az "
            f"{2 * total // (1024 * 1024)} MB boş alan gerekli, "
            f"{free // (1024 * 1024)} MB var."
        )
    # A crash between mkdir and rename leaves a .tmp folder behind;
    # sweep the leftovers of previous runs before starting a new one.
    for leftover in root.glob(f"{STATE_BACKUP_PREFIX}*.tmp"):
        shutil.rmtree(leftover, ignore_errors=True)
    stamp = _utc_now().strftime(_STAMP_FORMAT)
    target = root / f"{STATE_BACKUP_PREFIX}{stamp}"
    # Windows serves the clock in ~16 ms steps, so two quick backups can
    # share a stamp to the microsecond - and on Windows replace() onto
    # the existing folder fails with WinError 5, while elsewhere it
    # would silently eat a kept copy. A same-instant sibling takes a
    # lettered counter: "b2" sorts after the plain name, so the name
    # order stays the creation order the rotation relies on.
    counter = 2
    while target.exists():
        target = root / f"{STATE_BACKUP_PREFIX}{stamp}b{counter}"
        counter += 1
    partial = root / f"{target.name}.tmp"
    partial.mkdir()
    copied: list[str] = []
    try:
        for database in databases:
            # sqlite3's context manager commits but does not close, and
            # Windows cannot rename a folder holding open files.
            source = sqlite3.connect(f"file:{database.as_posix()}?mode=ro", uri=True)
            destination = sqlite3.connect(partial / database.name)
            try:
                source.backup(destination)
            finally:
                destination.close()
                source.close()
            copied.append(database.name)
    except BaseException:
        shutil.rmtree(partial, ignore_errors=True)
        raise
    partial.replace(target)
    removed: list[str] = []
    blocked: list[str] = []
    folders = sorted(
        (
            folder
            for folder in root.glob(f"{STATE_BACKUP_PREFIX}*")
            if folder.is_dir() and not folder.name.endswith(".tmp")
        ),
        key=lambda folder: folder.name,
        reverse=True,
    )
    for stale in folders[max(1, int(keep)) :]:
        try:
            shutil.rmtree(stale)
            removed.append(stale.name)
        except OSError:
            # A scanner may hold a file for a moment; the fresh copy
            # stands either way and the stale one is reported, not fatal.
            blocked.append(stale.name)
    return {
        "path": str(target),
        "folder": target.name,
        "files": copied,
        "bytes": sum(file.stat().st_size for file in target.glob("*.sqlite3")),
        "kept": min(len(folders), max(1, int(keep))),
        "removed": removed,
        "blocked": blocked,
    }


def state_backup_due(
    state_dir: Path, *, every_days: int = STATE_BACKUP_EVERY_DAYS
) -> bool:
    """True when no state backup is younger than the interval."""
    newest = next(iter(list_state_backups(state_dir)), None)
    if newest is None:
        return True
    age = _utc_now() - datetime.fromisoformat(newest["at"])
    return age >= timedelta(days=max(1, int(every_days)))


def state_backup_summary(state_dir: Path) -> dict[str, Any]:
    """What the settings card shows: the newest copy and the count."""
    backups = list_state_backups(state_dir)
    return {
        "count": len(backups),
        "newest_at": backups[0]["at"] if backups else None,
        "newest_bytes": backups[0]["bytes"] if backups else 0,
        "databases": len(state_databases(state_dir)),
    }


__all__ = [
    "STATE_BACKUP_EVERY_DAYS",
    "STATE_BACKUP_KEEP",
    "backup_state_now",
    "list_state_backups",
    "state_backup_due",
    "state_backup_summary",
    "state_databases",
]
