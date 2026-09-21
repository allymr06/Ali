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
    def __init__(
        self,
        *,
        items=None,
        window=True,
        invoke=False,
        rows=(),
        composer=True,
        message_rows=(),
        composer_label=None,
        header=None,
    ):
        self.items = items
        self.window = window
        self.invoke = invoke
        self.rows = list(rows)
        self.composer = composer
        self.message_rows = list(message_rows)
        self.composer_label = composer_label
        self.header = header
        self.clicked = []
        self.typed = []
        self.paces = []
        self.shown = 0

    def read_items(self, title, *, limit, minimum_length=6):
        if not self.window:
            return None
        return (self.items or [])[:limit]

    def show_without_activating(self, title):
        self.shown += 1
        return self.window

    def read_chat_rows(self, title, *, limit):
        if not self.window:
            return None
        return (self.items or [])[:limit]

    def read_message_rows(self, title):
        if not self.window:
            return None
        return list(self.message_rows)

    def composer_name(self, title):
        return self.composer_label

    def chat_title(self, title):
        return self.header

    def window_exists(self, title):
        return self.window

    def invoke_button(self, title, names):
        return self.invoke

    def click_item_by_name(self, title, name_contains):
        for row in self.rows:
            if name_contains.casefold() in row.casefold():
                self.clicked.append(row)
                return row
        return None

    def find_edit_value(self, title, name_contains):
        return self.composer

    def type_into_edit(self, title, name_contains, text, *, per_char_seconds=0.004):
        if not self.composer:
            return False
        self.typed.append(text)
        self.paces.append(per_char_seconds)
        return True


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


class FakeSpotifyUia:
    """Stands in for the desktop app's UI Automation surface."""

    def __init__(
        self,
        *,
        handle=101,
        play_button="Play Test Song",
        names=None,
        sliders=None,
        toggles=None,
        texts=(),
        menu_items=(),
        window_name="Spotify Premium",
        press_effects=None,
    ):
        self.handle = handle
        self.play_button = play_button
        self.names = list(names) if names is not None else ([play_button] if play_button else [])
        self.sliders = {key: list(value) for key, value in (sliders or {}).items()}
        self.toggles = dict(toggles or {})
        self.texts = list(texts)
        self.menu_items = set(menu_items)
        self.window_name = window_name
        self.press_effects = dict(press_effects or {})
        self.pressed = []
        self.double_clicked = []
        self.menu_invoked = []
        self.escapes = 0

    def window_handle_for_process(self, executable):
        return self.handle

    def bring_handle_to_foreground(self, handle):
        return bool(handle)

    def window_name_in_handle(self, handle):
        return self.window_name

    def control_names_in_handle(self, handle, *, region="any"):
        return list(self.names)

    def invoke_named_in_handle(self, handle, matcher, *, region="any"):
        for index, name in enumerate(self.names):
            if matcher(name):
                self.pressed.append(name)
                if name in self.press_effects:
                    self.names[index] = self.press_effects[name]
                return name
        return None

    def toggle_state_in_handle(self, handle, needle, *, region="any"):
        for name, state in self.toggles.items():
            if needle in name.casefold():
                return state
        return None

    def toggle_in_handle(self, handle, needle, *, region="any"):
        for name, state in self.toggles.items():
            if needle in name.casefold():
                self.toggles[name] = (state + 1) % 3
                return self.toggles[name]
        return None

    def slider_in_handle(self, handle, needle):
        for name, values in self.sliders.items():
            if needle in name.casefold():
                return tuple(values)
        return None

    def set_slider_in_handle(self, handle, needle, value):
        for name, values in self.sliders.items():
            if needle in name.casefold():
                values[0] = max(values[1], min(values[2], float(value)))
                return values[0]
        return None

    def double_click_named_in_handle(self, handle, matcher, *, region="any"):
        for name in self.names:
            if matcher(name):
                self.double_clicked.append(name)
                return name
        return None

    def texts_in_handle(self, handle, *, region="any"):
        return list(self.texts)

    def invoke_menu_item_in_handle(self, handle, name):
        self.menu_invoked.append(name)
        return name in self.menu_items

    def press_escape(self):
        self.escapes += 1


@pytest.mark.asyncio
async def test_spotify_play_track_uses_local_ui_without_account() -> None:
    """No OAuth setup must not refuse: the desktop app gets driven."""
    shell = FakePowerShell(
        [(0, "Spotify Premium"), (0, "Test Artist - Test Song")]
    )
    uia = FakeSpotifyUia(play_button="Play Test Song")
    uri = FakeUri()
    spotify = SpotifyIntegration(
        powershell=shell,
        uri_launcher=uri,
        uia_client_factory=lambda: uia,
        ui_settle_seconds=0.0,
        ui_retry_seconds=0.0,
    )

    result = await spotify.play_track("test song")

    assert result.status is ToolExecutionStatus.SUCCESS
    assert result.verified is True
    assert "Test Artist — Test Song" in result.message
    assert uia.pressed == ["Play Test Song"]
    assert any(u.startswith("spotify:search:") for u in uri.opened)


@pytest.mark.asyncio
async def test_spotify_local_play_partial_when_no_button() -> None:
    spotify = SpotifyIntegration(
        powershell=FakePowerShell([]),
        uri_launcher=FakeUri(),
        uia_client_factory=lambda: FakeSpotifyUia(play_button=None),
        ui_settle_seconds=0.0,
        ui_retry_seconds=0.0,
    )
    result = await spotify.play_track("obscure query")
    assert result.status is ToolExecutionStatus.PARTIAL
    assert result.error == "play_button_not_found"


def test_spotify_play_button_matcher_ignores_transport() -> None:
    matcher = SpotifyIntegration._is_play_button
    assert matcher("Play Hayatı Yaşa") is True
    assert matcher("Çal Hayatı Yaşa") is True
    assert matcher("Play") is False  # bare transport button
    assert matcher("Playlist Duman") is False
    assert matcher("Enable Shuffle for Her Şeyi Yak") is False


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
async def test_whatsapp_open_chat_by_visible_name(tmp_path) -> None:
    """An empty contact book must not block people already in the list."""
    uia = FakeUia(rows=["Mehmet 21:30 Selam"])
    integration = WhatsAppIntegration(
        contacts_path=tmp_path / "contacts.json",
        uia_client=uia,
        uri_launcher=FakeUri(),
    )

    result = await integration.open_chat("Mehmet", "Naber?")

    assert result.status is ToolExecutionStatus.SUCCESS
    assert result.data["via"] == "chat_list"
    assert result.verified is True
    assert uia.typed == ["Naber?"]


@pytest.mark.asyncio
async def test_whatsapp_unknown_name_fails_actionably(tmp_path) -> None:
    integration = WhatsAppIntegration(
        contacts_path=tmp_path / "contacts.json",
        uia_client=FakeUia(rows=[]),
        uri_launcher=FakeUri(),
    )
    result = await integration.open_chat("Bilinmeyen Kişi")
    assert result.status is ToolExecutionStatus.FAILED
    assert result.error == "contact_not_found"
    assert "whatsapp_add_contact" in result.message


@pytest.mark.asyncio
async def test_whatsapp_send_by_name_end_to_end(tmp_path) -> None:
    uia = FakeUia(rows=["Mehmet 21:30 Selam"], invoke=True)
    integration = WhatsAppIntegration(
        contacts_path=tmp_path / "contacts.json",
        uia_client=uia,
        uri_launcher=FakeUri(),
    )

    result = await integration.send_message("Mehmet", "Selam!")

    assert result.status is ToolExecutionStatus.SUCCESS
    assert result.verified is True
    assert uia.typed == ["Selam!"]


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


# ------------------------------------------- WhatsApp: reading like a person


def test_chat_rows_parse_into_who_how_many_and_when() -> None:
    from app.integrations.whatsapp import parse_chat_row

    row = parse_chat_row("1.022 okunmam\u0131\u015f mesaj T\u0131p 2030 Grubu 23:51 Ay\u015fe : hocam yar\u0131n s\u0131nav var m\u0131")
    assert row == {"name": "T\u0131p 2030 Grubu", "unread": 1022, "when": "23:51", "preview": "Ay\u015fe : hocam yar\u0131n s\u0131nav var m\u0131"}
    assert parse_chat_row("Annem D\u00fcn Foto\u011fraf") == {"name": "Annem", "unread": 0, "when": "D\u00fcn", "preview": "Foto\u011fraf"}
    assert parse_chat_row("2 okunmam\u0131\u015f mesaj Mehmet (Okul) Cumartesi ok") == {"name": "Mehmet (Okul)", "unread": 2, "when": "Cumartesi", "preview": "ok"}
    assert parse_chat_row("Kendinize mesaj 20.08.2026 not") == {"name": "Kendinize mesaj", "unread": 0, "when": "20.08.2026", "preview": "not"}
    # The badge can trail the time when the preview wraps.
    late = parse_chat_row("Okul Grubu 20:35 2 okunmam\u0131\u015f mesaj Ali : ders var m\u0131")
    assert late == {"name": "Okul Grubu", "unread": 2, "when": "20:35", "preview": "Ali : ders var m\u0131"}
    assert parse_chat_row("Sadece ad")["name"] == "Sadece ad"


def test_message_rows_parse_author_text_time_and_direction() -> None:
    from app.integrations.whatsapp import messages_from_rows, parse_message_row

    own = parse_message_row("Siz:\nmerhaba\n23:51\n\u25be", True)
    assert own == {"author": "Siz", "text": "merhaba", "time": "23:51", "outgoing": True, "kind": "text"}
    theirs = parse_message_row("Ahmet (Okul):\nnaber\nbug\u00fcn ders var m\u0131\n23:52", False)
    assert theirs["author"] == "Ahmet (Okul)" and theirs["text"] == "naber bug\u00fcn ders var m\u0131"
    assert theirs["outgoing"] is False and theirs["time"] == "23:52"
    continuation = parse_message_row("nas\u0131ls\u0131n?\n23:53", True)
    assert continuation["author"] is None and continuation["outgoing"] is True
    unknown = parse_message_row("devam\u0131\n23:54", False)
    assert unknown["outgoing"] is None, "no label, no status: the caller inherits"
    media = parse_message_row("Ahmet (Okul):\n23:55", False)
    assert media["kind"] == "media" and media["text"] == ""
    assert parse_message_row("   \n", False) is None

    rows = [
        ("Ahmet:\nselam\n10:00", False),
        ("naber\n10:00", False),
        ("Siz:\niyiyim\n10:01", True),
        ("sen?\n10:01", True),
        ("Ahmet:\n10:02", False),
    ]
    messages = messages_from_rows(rows)
    assert [(m["author"], m["outgoing"], m["text"]) for m in messages] == [
        ("Ahmet", False, "selam"), ("Ahmet", False, "naber"),
        ("Siz", True, "iyiyim"), ("Siz", True, "sen?"), ("Ahmet", False, ""),
    ]


def test_the_composer_names_the_open_chat() -> None:
    from app.integrations.whatsapp import parse_composer_name

    assert parse_composer_name("T\u0131p 2030 grup sohbetine bir mesaj yaz\u0131n") == ("T\u0131p 2030", True)
    assert parse_composer_name("Ahmet sohbetine bir mesaj yaz\u0131n") == ("Ahmet", False)
    assert parse_composer_name("Type a message") is None
    assert parse_composer_name(None) is None


@pytest.mark.asyncio
async def test_whatsapp_reads_chats_and_conversation_without_taking_focus(tmp_path) -> None:
    uia = FakeUia(
        items=["3 okunmam\u0131\u015f mesaj Ahmet 21:30 selam", "Annem D\u00fcn Foto\u011fraf"],
        message_rows=[("Ahmet:\nselam\n21:30", False), ("Siz:\nselam kanka\n21:31", True)],
        composer_label="Ahmet sohbetine bir mesaj yaz\u0131n",
    )
    integration = WhatsAppIntegration(
        contacts_path=tmp_path / "contacts.json", uia_client=uia, uri_launcher=FakeUri()
    )

    quiet = await WhatsAppIntegration(
        contacts_path=tmp_path / "kapali.json", uia_client=FakeUia(window=False), uri_launcher=FakeUri()
    ).read_recent_chats(limit=5, launch=False)
    assert quiet.status is ToolExecutionStatus.BLOCKED and quiet.message == "WhatsApp kapalı."

    chats = await integration.read_recent_chats(limit=5)
    assert chats.status is ToolExecutionStatus.SUCCESS and chats.verified is True
    assert chats.data["unread_chats"] == 1
    assert chats.data["entries"][0] == {"name": "Ahmet", "unread": 3, "when": "21:30", "preview": "selam"}
    assert "1 sohbette okunmam\u0131\u015f mesaj var" in chats.message

    conversation = await integration.read_open_conversation(limit=10)
    assert conversation.status is ToolExecutionStatus.SUCCESS
    assert conversation.data["chat"] == "Ahmet" and conversation.data["group"] is False
    assert conversation.data["lines"] == ["Ahmet: selam", "Siz: selam kanka"]
    assert conversation.data["messages"][1]["outgoing"] is True
    assert "(Ahmet)" in conversation.message
    # FakeUia has no bring_to_foreground at all: a read that fronted the
    # window would have raised. Reading never takes the user's focus.
    assert not hasattr(uia, "bring_to_foreground")

    # The header strip is the fast path; the composer only fills in for it.
    uia.header = ("T\u0131p 2030", True)
    assert await integration.current_chat() == {"title": "T\u0131p 2030", "group": True}
    uia.header = None
    uia.composer_label = None
    assert await integration.current_chat() is None


@pytest.mark.asyncio
async def test_whatsapp_send_at_a_human_pace_types_instead_of_prefilling(tmp_path) -> None:
    uri = FakeUri()
    uia = FakeUia(invoke=True)
    integration = WhatsAppIntegration(contacts_path=tmp_path / "contacts.json", uia_client=uia, uri_launcher=uri)
    integration.add_contact("Ali", "+905551112233")

    result = await integration.send_message("Ali", "selam  kanka\nnaber", typing_seconds_per_char=0.05)

    assert result.status is ToolExecutionStatus.SUCCESS and result.verified is True
    assert uri.opened[0] == "whatsapp://send?phone=905551112233", "no prefilled text: the words are typed"
    assert uia.typed == ["selam kanka naber"] and uia.paces == [0.05]

    pasted = await integration.send_message("Ali", "h\u0131zl\u0131")
    assert pasted.status is ToolExecutionStatus.SUCCESS
    assert uri.opened[-1].startswith("whatsapp://send?phone=905551112233&text=")


# ------------------------------------------- Spotify: the desktop app's own controls


def desktop_spotify(uia, titles=("Spotify Premium",), **kwargs):
    return SpotifyIntegration(
        powershell=FakePowerShell([(0, title) for title in titles]),
        uri_launcher=FakeUri(),
        uia_client_factory=lambda: uia,
        ui_settle_seconds=0.0,
        ui_retry_seconds=0.0,
        **kwargs,
    )


@pytest.mark.asyncio
async def test_spotify_volume_and_seek_read_the_sliders_back() -> None:
    uia = FakeSpotifyUia(sliders={"Change volume": [1.0, 0.0, 1.0], "Change progress": [10_000.0, 0.0, 223_000.0]})
    spotify = desktop_spotify(uia)

    volume = await spotify.set_volume(40)
    assert volume.status is ToolExecutionStatus.SUCCESS and volume.verified is True
    assert volume.message == "Spotify sesi %40." and uia.sliders["Change volume"][0] == 0.4
    assert (await spotify.set_volume("yüksek")).error == "invalid_percent"

    seek = await spotify.seek("+30")
    assert seek.status is ToolExecutionStatus.SUCCESS and seek.data["position_ms"] == 40_000
    assert seek.message == "Parça 0:40 konumunda."
    assert (await spotify.seek("abc")).error == "invalid_position"
    assert (await desktop_spotify(FakeSpotifyUia(handle=0)).set_volume(10)).status is ToolExecutionStatus.BLOCKED


@pytest.mark.asyncio
async def test_spotify_shuffle_and_repeat_press_until_the_window_agrees() -> None:
    uia = FakeSpotifyUia(
        names=["Enable Shuffle for Liked Songs", "Previous", "Play", "Next"],
        press_effects={"Enable Shuffle for Liked Songs": "Enable Smart Shuffle for Liked Songs"},
        toggles={"Enable repeat": 0},
    )
    spotify = desktop_spotify(uia)

    on = await spotify.shuffle("aç")
    assert on.status is ToolExecutionStatus.SUCCESS and on.data == {"mode": "on", "presses": 1}
    already = await spotify.shuffle("on")
    assert already.data["presses"] == 0, "already there: nothing is pressed"
    stuck = await spotify.shuffle("smart")
    assert stuck.status is ToolExecutionStatus.PARTIAL and stuck.error == "shuffle_unverified"
    assert (await spotify.shuffle("belki")).error == "invalid_mode"

    track = await spotify.repeat("track")
    assert track.status is ToolExecutionStatus.SUCCESS and track.data == {"mode": "track", "state": 2, "toggles": 2}
    off = await spotify.repeat("kapalı")
    assert off.data["state"] == 0 and off.data["toggles"] == 1
    assert (await spotify.repeat("hep")).error == "invalid_mode"


@pytest.mark.asyncio
async def test_spotify_likes_the_current_track_and_knows_when_it_already_did() -> None:
    uia = FakeSpotifyUia(names=["Pause", "Add to Liked Songs"], press_effects={"Add to Liked Songs": "Add to playlist"})
    liked = await desktop_spotify(uia).like_current()
    assert liked.status is ToolExecutionStatus.SUCCESS and liked.verified is True and uia.pressed == ["Add to Liked Songs"]

    again = await desktop_spotify(FakeSpotifyUia(names=["Pause", "Add to playlist"])).like_current()
    assert again.status is ToolExecutionStatus.SUCCESS and again.data == {"already_saved": True}
    missing = await desktop_spotify(FakeSpotifyUia(names=["Pause"])).like_current()
    assert missing.status is ToolExecutionStatus.FAILED and missing.error == "like_button_not_found"


@pytest.mark.asyncio
async def test_spotify_library_rows_are_listed_and_played_by_double_click() -> None:
    uia = FakeSpotifyUia(names=["Home", "Dümen Playlist • Ali Mirişli", "Kaan Tangöze Artist", "Play Kufi"])
    spotify = desktop_spotify(uia, titles=("Spotify Premium", "Spotify Premium", "Duman - Balık"))

    listed = await spotify.library()
    assert listed.data["items"] == [
        {"name": "Dümen", "kind": "playlist", "owner": "Ali Mirişli"},
        {"name": "Kaan Tangöze", "kind": "artist", "owner": ""},
    ]

    played = await spotify.play_library("dümen")
    assert played.status is ToolExecutionStatus.SUCCESS and played.verified is True
    assert uia.double_clicked == ["Dümen Playlist • Ali Mirişli"]
    assert played.message == "Çalınıyor: Dümen (Duman - Balık)"
    missing = await spotify.play_library("yok böyle liste")
    assert missing.status is ToolExecutionStatus.FAILED and missing.error == "row_not_found"


@pytest.mark.asyncio
async def test_spotify_queues_a_track_and_checks_the_queue_panel() -> None:
    uia = FakeSpotifyUia(
        names=["Play Kufi by Duman", "More options for Kufi by Duman", "Queue", "Play Hatun"],
        menu_items={"Add to queue"},
        texts=["Next in queue", "Kufi by Duman"],
    )
    spotify = desktop_spotify(uia)
    queued = await spotify.queue_track("kufi")
    assert queued.status is ToolExecutionStatus.SUCCESS and queued.verified is True
    assert uia.menu_invoked == ["Add to queue"]
    assert uia.pressed == ["More options for Kufi by Duman", "Queue", "Queue"], "the panel is opened to check and closed again"

    no_menu = await desktop_spotify(FakeSpotifyUia(names=["Play Kufi", "More options for Kufi"], menu_items=())).queue_track("kufi")
    assert no_menu.status is ToolExecutionStatus.FAILED and no_menu.error == "menu_item_not_found"

    # The page open before the search never lends its rows: unrelated
    # names that do not change are not a result.
    stale = await desktop_spotify(FakeSpotifyUia(names=["Play Seni Kendime Sakladım by Duman", "More options for Seni Kendime Sakladım"])).queue_track("kufi")
    assert stale.status is ToolExecutionStatus.FAILED and stale.error == "track_not_found"


@pytest.mark.asyncio
async def test_spotify_now_playing_reads_the_bar_when_the_title_is_silent() -> None:
    uia = FakeSpotifyUia(
        names=["Play", "Add to playlist"],
        texts=["Unutulanlar V3", "Sh!t", ",", "Kağan", "0:12", "Change progress", "3:43"],
    )
    paused = await desktop_spotify(uia).now_playing()
    assert paused.message == "Duraklatılmış: Sh!t, Kağan — Unutulanlar V3 (0:12 / 3:43)"
    assert paused.data["playing"] is False and paused.data["liked"] is True

    playing = await desktop_spotify(uia, titles=("Duman - Balık",)).now_playing()
    assert playing.message.startswith("Çalıyor: Duman — Balık")


@pytest.mark.asyncio
async def test_spotify_play_track_prefers_the_row_a_person_would_press() -> None:
    uia = FakeSpotifyUia(names=["Play Her Şeyi Yak", "Play Duman", "Follow Duman"])
    spotify = desktop_spotify(uia, titles=("Spotify Premium", "Duman - Her Şeyi Yak"))
    result = await spotify.play_track("Duman")
    assert result.status is ToolExecutionStatus.SUCCESS
    assert uia.pressed == ["Play Duman"]


@pytest.mark.asyncio
async def test_spotify_sleep_timer_tools_start_and_cancel() -> None:
    spotify = desktop_spotify(FakeSpotifyUia())
    assert (await spotify.sleep_timer(0)).error == "invalid_minutes"
    started = await spotify.sleep_timer(45)
    assert started.status is ToolExecutionStatus.SUCCESS and spotify._sleep_timer.active is True
    cancelled = await spotify.cancel_sleep_timer()
    assert cancelled.data == {"cancelled": True}
    assert (await spotify.cancel_sleep_timer()).data == {"cancelled": False}


def test_spotify_desktop_tools_are_registered_with_honest_risks() -> None:
    executor = ToolExecutor()
    SpotifyIntegration(powershell=FakePowerShell([])).register_tools(executor)
    names = {contract.definition.name for contract in executor.get_contract_objects(include_disabled=True)}
    for tool in ("spotify_set_volume", "spotify_seek", "spotify_shuffle", "spotify_repeat", "spotify_like_track",
                 "spotify_library", "spotify_play_library", "spotify_queue_track", "spotify_sleep_timer",
                 "spotify_cancel_sleep_timer"):
        assert tool in names, tool
    assert executor.get("spotify_like_track").definition.risk_level is RiskLevel.MEDIUM
    assert executor.get("spotify_library").definition.risk_level is RiskLevel.READ_ONLY
    assert executor.get("spotify_set_volume").definition.requires_confirmation is False
