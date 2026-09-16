"""Mobile companion: a narrow, authenticated HTTP surface over the desktop core.

The desktop application owns the JARVIS backend. This package adds a
loopback-only web server inside that same process, so a phone reaches the
same conversations, tasks and permission pipeline the desktop uses - and
nothing runs when the PC is off. Reaching the loopback port from outside
the PC is Tailscale Serve's job (see docs/MOBILE.md); authorising a device
is this package's job (see ``sessions``).
"""

from app.mobile.sessions import MobileSessionStore, PairingError

__all__ = ["MobileSessionStore", "PairingError"]
