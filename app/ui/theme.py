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


DARK = ThemeTokens(
    "#05080b",
    "#091016",
    "#111d25",
    "#eaf8fb",
    "#7f969e",
    "#223640",
    "#061014",
    "#14252d",
    "#45cde9",
    "#5f7780",
    "#a9efff",
    "#45cde9",
    "#efc37d",
)
LIGHT = ThemeTokens(
    "#edf4f6",
    "#f8fcfd",
    "#e2edf0",
    "#0a171c",
    "#536970",
    "#b7cbd1",
    "#ffffff",
    "#d7e8ec",
    "#167f98",
    "#789198",
    "#0c667d",
    "#168ba6",
    "#9a651f",
)


def tokens(theme: UITheme) -> ThemeTokens:
    return DARK if theme is UITheme.DARK else LIGHT
