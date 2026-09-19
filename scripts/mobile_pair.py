"""Manage the mobile companion's device sessions from the PC's own terminal.

    python scripts/mobile_pair.py code              # mint a single-use pairing code
    python scripts/mobile_pair.py sessions          # list paired phones
    python scripts/mobile_pair.py revoke <id|all>   # revoke one phone, or every phone

The store is the same SQLite file the running desktop uses, so a code
minted here is accepted by the running JARVIS immediately. The code is
printed once and never written anywhere else; hand it to your own phone.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.config.paths import default_state_path  # noqa: E402
from app.config.settings import Settings  # noqa: E402
from app.mobile.sessions import MobileSessionStore, format_pairing_code  # noqa: E402


def _store() -> MobileSessionStore:
    settings = Settings.from_environment()
    return MobileSessionStore(
        default_state_path("jarvis_mobile.sqlite3"),
        pairing_ttl_seconds=settings.mobile_pairing_ttl_seconds,
        session_days=settings.mobile_session_days,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="JARVIS mobile companion: pairing codes and device sessions.")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("code", help="mint a single-use pairing code")
    commands.add_parser("sessions", help="list paired phones")
    revoke = commands.add_parser("revoke", help="revoke one session id, or 'all'")
    revoke.add_argument("target")
    args = parser.parse_args(argv)
    store = _store()

    if args.command == "code":
        code, expires = store.create_pairing_code(label="cli")
        print("Eşleştirme kodu:", format_pairing_code(code))
        print("Geçerlilik:", expires.astimezone().strftime("%H:%M"), "· tek kullanımlık")
        print("Telefonda JARVIS sayfasını aç, kodu yaz. JARVIS masaüstü uygulaması açık olmalı.")
        return 0

    if args.command == "sessions":
        sessions = store.list_sessions(include_revoked=True)
        if not sessions:
            print("Bağlı cihaz yok.")
            return 0
        for session in sessions:
            state = "iptal" if session.revoked_at else ("aktif" if session.active else "süresi dolmuş")
            print(f"{session.session_id}  {session.label:<20}  {state:<12}  son görülme {session.last_seen_at.astimezone():%d.%m %H:%M}")
        return 0

    if args.command == "revoke":
        if args.target == "all":
            print(f"{store.revoke_all()} oturum iptal edildi.")
        elif store.revoke(args.target):
            print("Oturum iptal edildi.")
        else:
            print("Oturum bulunamadı ya da zaten kapalı.")
            return 1
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
