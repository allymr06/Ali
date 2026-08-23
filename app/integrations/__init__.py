"""Application integrations: per-app tool families isolated from Core.

Each integration registers structured tools through the shared
ToolExecutor so every action passes the permission engine, approval
gate, timeout, and verification contracts like any other tool.
"""

from app.integrations.spotify import SpotifyIntegration
from app.integrations.whatsapp import WhatsAppIntegration

__all__ = [
    "SpotifyIntegration",
    "WhatsAppIntegration",
]
