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
    assert state.voice_active is False
    assert state.voice_status == "IDLE"
    assert state.voice_messages == []


def test_chat_message_validates_role_and_text() -> None:
    assert ChatMessage("user", "hello").text == "hello"
    with pytest.raises(ValueError, match="role"):
        ChatMessage("tool", "hello")
    with pytest.raises(ValueError, match="empty"):
        ChatMessage("assistant", " ")


def test_theme_tokens_match_mission_interface_palette() -> None:
    assert tokens(UITheme.DARK) is DARK
    assert tokens(UITheme.LIGHT) is LIGHT
    assert DARK.background == "#05080b"
    assert DARK.accent == "#a9efff"
    assert DARK.accent_strong == "#45cde9"
    assert DARK.warning == "#efc37d"
    assert LIGHT.background == "#edf4f6"
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
            palette.accent,
            palette.accent_strong,
            palette.warning,
        ):
            assert value.startswith("#")
            assert len(value) == 7
            int(value[1:], 16)


def test_ui_state_allows_motion_to_be_reduced() -> None:
    state = UIState()

    assert state.reduced_motion is False
    state.reduced_motion = True
    assert state.reduced_motion is True
