"""Repair the Medical Academy's learning summaries against the scoring policy.

Backs up the medical database, takes unscored questions out of mastery and
findings, re-links histology answers to their exact concept, recomputes the
stored results of finished papers, and prints what it did. Idempotent: run
it again and it reports "already applied". Never deletes an answer.

    .venv\\Scripts\\python.exe scripts\\repair_medical_learning.py [--directory DIR] [--dry-run] [--no-backup]

Without ``--directory`` the configured medical directory is used
(``JARVIS_MEDICAL_DIRECTORY`` or the default under %LOCALAPPDATA%).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--directory", help="medical directory holding jarvis_medical.sqlite3")
    parser.add_argument("--dry-run", action="store_true", help="report what would change on a temporary copy; the real database is not touched")
    parser.add_argument("--no-backup", action="store_true", help="skip the SQLite backup (not recommended)")
    args = parser.parse_args(argv)

    from app.config.paths import default_state_directory
    from app.config.settings import Settings
    from app.medical.academy import create_medical_academy
    from app.medical.repair import backup_database, format_report, repair_learning

    if args.directory:
        directory = Path(args.directory).expanduser()
    else:
        settings = Settings.from_environment()
        directory = Path(settings.medical_directory).expanduser() if settings.medical_directory else default_state_directory() / "medical"
    database = directory / "jarvis_medical.sqlite3"
    if not database.is_file():
        print(f"Veritabanı bulunamadı: {database}")
        return 2

    work_directory = directory
    if args.dry_run:
        import tempfile

        scratch = Path(tempfile.mkdtemp(prefix="jarvis-repair-"))
        academy_probe = create_medical_academy(settings=SimpleNamespace(medical_directory=str(directory), medical_source_review=True), provider_gateway=None)
        try:
            copy = backup_database(academy_probe.store, directory=scratch)
        finally:
            academy_probe.close()
        if copy is None:
            print("Kopya alınamadı.")
            return 2
        (scratch / "jarvis_medical.sqlite3").write_bytes(copy.read_bytes())
        work_directory = scratch
        print(f"Deneme çalışması: {work_directory}")

    academy = create_medical_academy(settings=SimpleNamespace(medical_directory=str(work_directory), medical_source_review=True), provider_gateway=None)
    try:
        report = repair_learning(academy, backup=not args.no_backup and not args.dry_run)
    finally:
        academy.close()
    print(format_report(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
