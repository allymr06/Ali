"""Spotify's desktop tier: pure helpers, the ducker and the sleep timer."""
from __future__ import annotations

import asyncio
import time

import pytest

from app.integrations.spotify_desktop import (
    SleepTimer,
    SpotifyDucker,
    choose_play_button,
    now_playing_from_bar,
    parse_library_row,
    parse_seek,
    shuffle_mode_of,
)


def test_library_rows_and_shuffle_names_are_read_as_seen_in_the_window() -> None:
    assert parse_library_row("D\u00fcmen Playlist \u2022 Ali Miri\u015fli") == {"name": "D\u00fcmen", "kind": "playlist", "owner": "Ali Miri\u015fli"}
    assert parse_library_row("Kaan Tang\u00f6ze Artist") == {"name": "Kaan Tang\u00f6ze", "kind": "artist", "owner": ""}
    assert parse_library_row("Your Top Songs 2025 Playlist \u2022 Made for Ali") ["name"] == "Your Top Songs 2025"
    assert parse_library_row("Home") is None

    assert shuffle_mode_of("Enable Shuffle for Liked Songs") == "off"
    assert shuffle_mode_of("Enable Smart Shuffle for Liked Songs") == "on"
    assert shuffle_mode_of("Disable Smart Shuffle for D\u00fcmen") == "smart"
    assert shuffle_mode_of(None) is None and shuffle_mode_of("Play") is None


def test_seek_targets_are_relative_or_absolute_and_clamped() -> None:
    assert parse_seek("+30", 10_000, 223_000) == 40_000
    assert parse_seek("-15", 10_000, 223_000) == 0
    assert parse_seek("1:30", 10_000, 223_000) == 90_000
    assert parse_seek("90", 10_000, 223_000) == 90_000
    assert parse_seek("+999", 10_000, 223_000) == 223_000
    assert parse_seek("abc", 10_000, 223_000) is None
    assert parse_seek("", 10_000, 223_000) is None


def test_the_play_button_a_person_would_press() -> None:
    names = ["Play Her \u015eeyi Yak", "Play Dibine Kadar", "Play Kufi by Duman", "Play Duman", "Follow Duman", "More options for Duman"]
    assert choose_play_button(names, "duman") == "Play Duman", "the artist card, not the first song"
    assert choose_play_button(names, "dibine kadar") == "Play Dibine Kadar"
    assert choose_play_button(names, "kufi") == "Play Kufi by Duman"
    assert choose_play_button(names, "duman rock") == "Play Her \u015eeyi Yak", "no match: the top result"
    assert choose_play_button(["Home", "Search"], "duman") is None


def test_menu_buttons_match_rows_with_or_without_the_artist_suffix() -> None:
    from app.integrations.spotify_desktop import more_options_button, play_title_of, related_to_query

    names = ["Play Kufi by Duman", "More options for Kufi", "Play Hatun", "More options for Hatun"]
    assert play_title_of("Play Kufi by Duman") == "Kufi by Duman"
    assert more_options_button(names, "Kufi by Duman") == "More options for Kufi"
    assert more_options_button(names, "Hatun") == "More options for Hatun"
    assert more_options_button(names, "Yok") is None
    assert related_to_query("Seni Kendime Sakladım by Duman", "kufi") is False
    assert related_to_query("Kufi by Duman", "kufi") is True
    assert related_to_query("Anything", "ab") is True, "a query with no real word relates to anything"


def test_the_transport_bar_is_read_in_its_own_words() -> None:
    texts = ["Unutulanlar V3", "Sh!t", ",", "Ka\u011fan", "0:12", "Change progress", "3:43", "Change volume"]
    playing = now_playing_from_bar(texts, ["Enable Smart Shuffle for Liked Songs", "Previous", "Pause", "Next", "Enable repeat", "Add to playlist", "Mute"])
    assert playing == {"track": "Unutulanlar V3", "artists": ["Sh!t", "Ka\u011fan"], "position": "0:12", "duration": "3:43", "playing": True, "liked": True}
    labels_first = now_playing_from_bar(["0:00", "Change progress", "2:48", "Change volume", "Balık", "Duman"], ["Play"])
    assert labels_first["track"] == "Balık" and labels_first["artists"] == ["Duman"], "slider labels never read as the track"
    paused = now_playing_from_bar(texts, ["Play", "Add to Liked Songs"])
    assert paused["playing"] is False and paused["liked"] is False
    assert now_playing_from_bar([], [])["track"] is None


class FakeSpotify:
    def __init__(self, *, volume=1.0, playing=True):
        self.volume = volume
        self.playing = playing
        self.set_calls = []
        self.paused = 0

    def volume_sync(self):
        return self.volume

    def set_volume_sync(self, value):
        self.volume = value
        self.set_calls.append(round(value, 3))
        return value

    def playing_sync(self):
        return self.playing

    async def pause_if_playing(self):
        self.paused += 1
        self.playing = False
        return True


def wait_for(predicate, seconds=2.0):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return predicate()


def test_the_ducker_turns_the_music_down_to_talk_and_back_up_after() -> None:
    spotify = FakeSpotify(volume=0.8)
    ducker = SpotifyDucker(spotify, level=0.25)
    ducker.on_voice_state("speaking")
    assert wait_for(lambda: spotify.set_calls == [0.2])
    assert ducker.ducked is True
    ducker.on_voice_state("speaking")  # a second SPEAKING does not stack
    ducker.on_voice_state("listening")
    assert wait_for(lambda: spotify.set_calls == [0.2, 0.8])
    assert ducker.ducked is False
    ducker.on_voice_state("idle")  # nothing to restore: no call
    ducker.restore()
    time.sleep(0.1)
    assert spotify.set_calls == [0.2, 0.8]


def test_the_ducker_leaves_silence_and_a_missing_window_alone() -> None:
    quiet = FakeSpotify(volume=0.8, playing=False)
    ducker = SpotifyDucker(quiet)
    ducker.on_voice_state("speaking")
    time.sleep(0.2)
    assert quiet.set_calls == [] and ducker.ducked is False

    class NoWindow(FakeSpotify):
        def volume_sync(self):
            return None

    ducker = SpotifyDucker(NoWindow())
    ducker.on_voice_state("speaking")
    time.sleep(0.2)
    assert ducker.ducked is False


@pytest.mark.asyncio
async def test_the_sleep_timer_fades_then_pauses_and_puts_the_volume_back() -> None:
    spotify = FakeSpotify(volume=0.6)
    slept = []

    async def sleep(seconds):
        slept.append(round(seconds, 3))

    timer = SleepTimer(spotify, sleep=sleep)
    timer.start(1)
    assert timer.active is True
    await timer._task
    assert slept[0] == 30.0, "a minute: wait 30 s, fade over the last 30"
    assert slept[1:] == [5.0] * 6
    assert spotify.set_calls[:6] == [0.5, 0.4, 0.3, 0.2, 0.1, 0.0]
    assert spotify.set_calls[-1] == 0.6 and spotify.paused == 1
    assert timer.outcome == "paused" and timer.active is False
    assert timer.cancel() is False


@pytest.mark.asyncio
async def test_the_sleep_timer_can_be_cancelled() -> None:
    spotify = FakeSpotify()

    async def sleep(seconds):
        await asyncio.sleep(0.05)

    timer = SleepTimer(spotify, sleep=sleep)
    timer.start(5)
    assert timer.cancel() is True and timer.outcome == "cancelled"
    await asyncio.sleep(0.1)
    assert spotify.paused == 0
