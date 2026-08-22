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


DARK = ThemeTokens(
    "#090909",
    "#111111",
    "#181818",
    "#f4f4f4",
    "#9b9b9b",
    "#2d2d2d",
    "#090909",
    "#222222",
    "#d8d8d8",
    "#676767",
)
LIGHT = ThemeTokens(
    "#f4f4f4",
    "#ffffff",
    "#e9e9e9",
    "#111111",
    "#666666",
    "#cccccc",
    "#ffffff",
    "#dedede",
    "#303030",
    "#909090",
)


def tokens(theme: UITheme) -> ThemeTokens:
    return DARK if theme is UITheme.DARK else LIGHT
