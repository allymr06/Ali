from __future__ import annotations

from dataclasses import dataclass

from app.ui.models import UITheme


@dataclass(frozen=True, slots=True)
class ThemeTokens:
    background: str
    surface: str
    surface_alt: str
    ink: str
    muted: str
    line: str
    inverse: str
    hover: str
    focus: str
    faint: str
    accent: str
    accent_strong: str
    warning: str
    # Fine-grain tokens used by the animated shell.
    line_soft: str = "#14212c"
    glow: str = "#173544"
    success: str = "#7fe0b2"


DARK = ThemeTokens(
    background="#05090f",
    surface="#0a111a",
    surface_alt="#101b27",
    ink="#eaf6fa",
    muted="#8299a6",
    line="#1d2f3d",
    inverse="#04121a",
    hover="#152331",
    focus="#4dd6f2",
    faint="#54707f",
    accent="#7ee8ff",
    accent_strong="#38c8e8",
    warning="#f0c684",
    line_soft="#141f2b",
    glow="#12303f",
    success="#7fe0b2",
)
LIGHT = ThemeTokens(
    background="#eef4f7",
    surface="#f9fcfd",
    surface_alt="#e3edf2",
    ink="#0a171d",
    muted="#51686f",
    line="#c3d4da",
    inverse="#ffffff",
    hover="#dbe9ee",
    focus="#0f7f9c",
    faint="#7d949c",
    accent="#0c667d",
    accent_strong="#128aa8",
    warning="#9a651f",
    line_soft="#d5e2e7",
    glow="#cfe6ec",
    success="#1d7d55",
)


def tokens(theme: UITheme) -> ThemeTokens:
    return DARK if theme is UITheme.DARK else LIGHT
