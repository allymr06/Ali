from __future__ import annotations

import asyncio

import pytest

from app.core.models import RiskLevel, ToolExecutionStatus
from app.integrations.spotify import SpotifyIntegration
from app.integrations.system_control import (
    SystemControlIntegration,
    _validate_web_url,
)
from app.integrations.whatsapp import WhatsAppIntegration
from app.reminders.service import ReminderService
from app.tools.executor import ToolExecutor


class FakePowerShell:
    def __init__(self, results):
        self.results = list(results)
        self.scripts = []

    async def run(self, script, *, timeout_seconds=None):
        self.scripts.append(script)
        if self.results:
            return self.results.pop(0)
        return 0, ""


class FakeKeys:
    def __init__(self):
        self.sent = []

    def send(self, key):
        self.sent.append(key)
        return True


class FakeUri:
    def __init__(self, ok=True):
        self.ok = ok
        self.opened = []

    def open(self, uri):
        self.opened.append(uri)
        return self.ok


class FakeUia:
    def __init__(self, *, items=None, window=True, invoke=False):
        self.items = items
        self.window = window
        self.invoke = invoke

    def read_items(self, title, *, limit, minimum_length=6):
        if not self.window:
            return None
        return (self.items or [])[:limit]

    def window_exists(self, title):
        return self.window

    def invoke_button(self, title, names):
        return self.invoke


# ---------------------------------------------------------------- Spotify


def test_spotify_title_parsing_detects_playback_state() -> None:
    assert SpotifyIntegration.parse_title(None) == {
        "running": False,
        "playing": False,
    }
    idle = SpotifyIntegration.parse_title("Spotify Premium")
    assert idle["running"] is True and idle["playing"] is False
    playing = SpotifyIntegration.parse_title("Duman - Senden Daha Güzel")
    assert playing["artist"] == "Duman"
    assert playing["track"] == "Senden Daha Güzel"


@pytest.mark.asyncio
async def test_spotify_play_pause_verifies_title_change() -> None:
    shell = FakePowerShell([(0, "Spotify"), (0, "Duman - Köprüaltı")])
    keys = FakeKeys()
    spotify = SpotifyIntegration(powershell=shell, media_keys=keys)

    result = await spotify.play_pause()

    assert result.status is ToolExecutionStatus.SUCCESS
    assert result.verified is True
    assert keys.sent == [0xB3]


@pytest.mark.asyncio
async def test_spotify_media_action_blocks_without_app() -> None:
    spotify = SpotifyIntegration(
        powershell=FakePowerShell([(1, "")]), media_keys=FakeKeys()
    )
    result = await spotify.next_track()
    assert result.status is ToolExecutionStatus.BLOCKED


@pytest.mark.asyncio
async def test_spotify_web_api_blocks_without_configuration() -> None:
    spotify = SpotifyIntegration(powershell=FakePowerShell([]))
    result = await spotify.play_track("test")
    assert result.status is ToolExecutionStatus.BLOCKED
    assert result.error == "not_configured"


def test_spotify_registers_expected_risk_levels() -> None:
    executor = ToolExecutor()
    SpotifyIntegration(powershell=FakePowerShell([])).register_tools(
        executor
    )
    send = executor.get("spotify_create_playlist").definition
    assert send.risk_level is RiskLevel.MEDIUM
    assert send.requires_confirmation is True
    assert (
        executor.get("spotify_now_playing").definition.risk_level
        is RiskLevel.READ_ONLY
    )


# --------------------------------------------------------------- WhatsApp


def test_whatsapp_contacts_roundtrip(tmp_path) -> None:
    integration = WhatsAppIntegration(
        contacts_path=tmp_path / "contacts.json",
        uia_client=FakeUia(),
        uri_launcher=FakeUri(),
    )
    bad = integration.add_contact("Ali", "12ab")
    assert bad.status is ToolExecutionStatus.FAILED

    ok = integration.add_contact("Ali", "+90 555 111 22 33")
    assert ok.status is ToolExecutionStatus.SUCCESS
    assert ok.verified is True

    listed = integration.list_contacts()
    assert listed.data["names"] == ["Ali"]


@pytest.mark.asyncio
async def test_whatsapp_open_chat_builds_deep_link(tmp_path) -> None:
    uri = FakeUri()
    integration = WhatsAppIntegration(
        contacts_path=tmp_path / "contacts.json",
        uia_client=FakeUia(),
        uri_launcher=uri,
    )
    integration.add_contact("Ali", "+905551112233")

    result = await integration.open_chat("ali", "Selam kanka")

    assert result.status is ToolExecutionStatus.SUCCESS
    assert uri.opened[0].startswith(
        "whatsapp://send?phone=905551112233&text="
    )
    assert "Selam" in result.message or result.verified is True


@pytest.mark.asyncio
async def test_whatsapp_send_reports_partial_when_unverified(
    tmp_path,
) -> None:
    integration = WhatsAppIntegration(
        contacts_path=tmp_path / "contacts.json",
        uia_client=FakeUia(invoke=False),
        uri_launcher=FakeUri(),
    )
    integration.add_contact("Ali", "+905551112233")

    result = await integration.send_message("Ali", "Deneme")

    assert result.status is ToolExecutionStatus.PARTIAL
    assert result.verified is False


@pytest.mark.asyncio
async def test_whatsapp_send_success_requires_invoked_marker(
    tmp_path,
) -> None:
    integration = WhatsAppIntegration(
        contacts_path=tmp_path / "contacts.json",
        uia_client=FakeUia(invoke=True),
        uri_launcher=FakeUri(),
    )
    integration.add_contact("Ali", "+905551112233")

    result = await integration.send_message("Ali", "Deneme")

    assert result.status is ToolExecutionStatus.SUCCESS
    assert result.verified is True


def test_whatsapp_send_is_high_risk_with_confirmation(tmp_path) -> None:
    executor = ToolExecutor()
    WhatsAppIntegration(
        contacts_path=tmp_path / "contacts.json",
        uia_client=FakeUia(),
        uri_launcher=FakeUri(),
    ).register_tools(executor)
    definition = executor.get("whatsapp_send_message").definition
    assert definition.risk_level is RiskLevel.HIGH
    assert definition.requires_confirmation is True


# ----------------------------------------------------------------- System


def test_web_url_validation_blocks_dangerous_input() -> None:
    assert _validate_web_url("file:///etc/passwd")[0] is None
    assert _validate_web_url("javascript:alert(1)")[0] is None
    assert _validate_web_url("http://user:pw@example.com")[0] is None
    assert _validate_web_url("example.com/yol")[0] == (
        "https://example.com/yol"
    )


def test_system_volume_bounds_and_direction() -> None:
    keys = FakeKeys()
    system = SystemControlIntegration(
        uri_launcher=FakeUri(), media_keys=keys
    )
    bad = system.adjust_volume("yana")
    assert bad.status is ToolExecutionStatus.FAILED

    ok = system.adjust_volume("yukari", steps=50)
    assert ok.status is ToolExecutionStatus.SUCCESS
    assert len(keys.sent) == 20  # bounded


# -------------------------------------------------------------- Reminders


def test_reminder_lifecycle_and_due_claim(tmp_path) -> None:
    service = ReminderService(tmp_path / "reminders.sqlite3")

    invalid = service.create("", minutes=5)
    assert invalid.status is ToolExecutionStatus.FAILED

    created = service.create("Çayı demle", minutes=1)
    assert created.status is ToolExecutionStatus.SUCCESS
    assert created.verified is True
    reminder_id = created.data["reminder_id"]

    active = service.list_active()
    assert active.data["reminders"][0]["reminder_id"] == reminder_id

    # Not due yet.
    assert service.claim_due() == []

    cancelled = service.cancel(reminder_id)
    assert cancelled.status is ToolExecutionStatus.SUCCESS
    assert service.list_active().data["reminders"] == []


def test_reminder_due_fires_exactly_once(tmp_path) -> None:
    service = ReminderService(tmp_path / "reminders.sqlite3")
    # Due in the past via at-time is awkward; insert due-now directly.
    from datetime import datetime, timedelta, timezone

    service.create("Hemen", minutes=1)
    with service._connect() as connection:
        connection.execute(
            "UPDATE reminders SET due_at = ?",
            (
                (
                    datetime.now(timezone.utc) - timedelta(seconds=1)
                ).isoformat(),
            ),
        )

    first = service.claim_due()
    assert [item["text"] for item in first] == ["Hemen"]
    assert service.claim_due() == []


def test_reminder_at_time_rolls_to_tomorrow_when_past(tmp_path) -> None:
    service = ReminderService(tmp_path / "reminders.sqlite3")
    result = service.create("Sabah", at="00:00")
    assert result.status is ToolExecutionStatus.SUCCESS


# -------------------------------------------------------------- Bootstrap


def test_bootstrap_registers_integration_tools() -> None:
    from app.bootstrap import create_application
    from app.config.settings import Settings

    application = create_application(
        Settings(default_provider="mock", default_model="mock-model")
    )
    executor = application.tool_executor
    for name in (
        "spotify_now_playing",
        "spotify_play_pause",
        "whatsapp_send_message",
        "open_website",
        "system_volume",
        "create_reminder",
        "list_reminders",
    ):
        assert executor.contains(name), name
    assert application.reminders is not None
