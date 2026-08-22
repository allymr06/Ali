from __future__ import annotations

import pytest

from app.ui.models import ChatMessage, UIScreen, UIState, UITheme
from app.ui.theme import DARK, LIGHT, tokens


def test_ui_screens_cover_approved_prototype_navigation() -> None:
    assert {screen.value for screen in UIScreen} == {
        "home",
        "chat",
        "tasks",
        "memory",
        "voice",
        "vision",
        "research",
        "tools",
        "integrations",
        "diagnostics",
        "settings",
    }


def test_ui_state_defaults_are_safe_and_local() -> None:
    state = UIState()

    assert state.screen is UIScreen.HOME
    assert state.theme is UITheme.DARK
    assert state.busy is False
    assert state.status == "LOCAL CORE READY"


def test_chat_message_validates_role_and_text() -> None:
    assert ChatMessage("user", "hello").text == "hello"
    with pytest.raises(ValueError, match="role"):
        ChatMessage("tool", "hello")
    with pytest.raises(ValueError, match="empty"):
        ChatMessage("assistant", " ")


def test_theme_tokens_are_strictly_grayscale_and_distinct() -> None:
    assert tokens(UITheme.DARK) is DARK
    assert tokens(UITheme.LIGHT) is LIGHT
    assert DARK.background == "#090909"
    assert LIGHT.background == "#f4f4f4"
    for palette in (DARK, LIGHT):
        for value in (
            palette.background,
            palette.surface,
            palette.surface_alt,
            palette.ink,
            palette.muted,
            palette.line,
            palette.inverse,
            palette.hover,
            palette.focus,
            palette.faint,
        ):
            red, green, blue = int(value[1:3], 16), int(value[3:5], 16), int(value[5:7], 16)
            assert red == green == blue


def test_ui_state_allows_motion_to_be_reduced() -> None:
    state = UIState()

    assert state.reduced_motion is False
    state.reduced_motion = True
    assert state.reduced_motion is True
