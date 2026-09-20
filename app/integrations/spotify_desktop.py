"""Spotify the way a person uses the desktop app.

Everything here drives the real Spotify window through UI Automation -
the same controls a hand reaches for: the volume and progress sliders,
the shuffle and repeat buttons, the heart on the current track, a
double-click on a library row, the "More options" menu of a search
result, the transport bar's own words about what is playing. No account
link is needed; nothing is estimated. Every action reads the window back
before it claims success, and reports PARTIAL when it cannot.

The synchronous UIA calls run off the event loop (callers wrap them in
``asyncio.to_thread``); ``SpotifyDucker`` lowers the music while JARVIS
speaks and restores it afterwards, the way a person turns the radio down
to talk.
"""
from __future__ import annotations

import asyncio
import re
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable

from app.core.models import ToolExecutionStatus, ToolResult

# Shuffle is one button whose name announces the NEXT step, which is how
# the current state is read: off -> "Enable Shuffle", on -> "Enable Smart
# Shuffle", smart -> "Disable Smart Shuffle". Names update a beat late.
SHUFFLE_STATES = {
    "off": ("enable shuffle",),
    "on": ("enable smart shuffle",),
    "smart": ("disable smart shuffle",),
}
REPEAT_STATES = {"off": 0, "list": 1, "track": 2}
_LIKE_BUTTON = "add to liked songs"
_SAVED_BUTTON = "add to playlist"
_TIME_TEXT = re.compile(r"^\d{1,2}:\d{2}(:\d{2})?$")
_LIBRARY_ROW = re.compile(r"^(?P<name>.+?) (?P<kind>Playlist|Artist|Album|Podcast|Audiobook)(?: • (?P<owner>.+))?$")


def parse_library_row(name: str) -> dict[str, str] | None:
    """'Dümen Playlist • Ali Mirişli' -> name/kind/owner."""
    match = _LIBRARY_ROW.match(" ".join(str(name).split()))
    if not match:
        return None
    return {
        "name": match.group("name"),
        "kind": match.group("kind").casefold(),
        "owner": match.group("owner") or "",
    }


def shuffle_mode_of(button_name: str | None) -> str | None:
    """Which shuffle mode a button name announces, or None if unknown."""
    if not button_name:
        return None
    lowered = button_name.casefold()
    for mode, prefixes in SHUFFLE_STATES.items():
        if any(lowered.startswith(prefix) for prefix in prefixes):
            return mode
    return None


def parse_seek(position: str, current_ms: float, duration_ms: float) -> float | None:
    """'+30', '-15', '1:30' or '90' -> a target in milliseconds, or None."""
    text = str(position or "").strip().replace(",", ".")
    if not text:
        return None
    relative = text[0] in "+-"
    body = text[1:] if relative else text
    seconds: float
    if ":" in body:
        parts = body.split(":")
        if len(parts) not in (2, 3) or not all(part.isdigit() for part in parts):
            return None
        seconds = 0.0
        for part in parts:
            seconds = seconds * 60 + int(part)
    else:
        try:
            seconds = float(body)
        except ValueError:
            return None
    target = current_ms + (seconds * 1000 if text[0] == "+" else -seconds * 1000) if relative else seconds * 1000
    return max(0.0, min(duration_ms, target))


def choose_play_button(names: list[str], query: str) -> str | None:
    """The row a person would press for the query: an exact title first,
    then the artist card, then the top result."""
    wanted = " ".join(query.split()).casefold()
    plays = [name for name in names if name.casefold().startswith(("play ", "çal "))]
    if not plays:
        return None
    for name in plays:
        title = name.split(" ", 1)[1] if " " in name else ""
        if title.casefold() == wanted or title.casefold().split(" by ")[0] == wanted:
            return name
    follows = {name.casefold()[len("follow "):] for name in names if name.casefold().startswith("follow ")}
    for name in plays:
        title = name.split(" ", 1)[1] if " " in name else ""
        if title.casefold() == wanted and title.casefold() in follows:
            return name
    for name in plays:
        title = (name.split(" ", 1)[1] if " " in name else "").casefold()
        if title in follows and wanted in title:
            return name
    return plays[0]


def play_title_of(button_name: str) -> str:
    """'Play Kufi by Duman' -> 'Kufi by Duman'."""
    return button_name.split(" ", 1)[1] if " " in button_name else button_name


def related_to_query(title: str, query: str) -> bool:
    """Whether a row title shares a real word with the query; a query
    without one (too short) relates to anything."""
    lowered = title.casefold()
    tokens = [token for token in query.casefold().split() if len(token) >= 3]
    return not tokens or any(token in lowered for token in tokens)


def more_options_button(names: list[str], title: str) -> str | None:
    """The 'More options for <title>' button, also when the row's play
    button carried a 'by Artist' suffix the menu button does not."""
    wanted = title.casefold()
    stripped = wanted.split(" by ")[0]
    candidates = [name for name in names if name.casefold().startswith("more options for ")]
    for name in candidates:
        remainder = name.casefold()[len("more options for "):]
        if remainder == wanted:
            return name
    for name in candidates:
        remainder = name.casefold()[len("more options for "):]
        if remainder == stripped:
            return name
    return None


def now_playing_from_bar(texts: list[str], controls: list[str]) -> dict[str, Any]:
    """What the transport bar says: track, artists, position, duration,
    whether it is playing (the button offers 'Pause' only then)."""
    times = [text for text in texts if _TIME_TEXT.match(text)]
    # Slider labels are texts too, and in some layouts they come before
    # the track link in document order.
    labels = {",", "•", "-", "Change progress", "Change volume", "Resize main navigation"}
    words = [text for text in texts if not _TIME_TEXT.match(text) and text not in labels]
    track = words[0] if words else None
    artists = words[1:6]
    lowered = [name.casefold() for name in controls]
    return {
        "track": track,
        "artists": artists,
        "position": times[0] if times else None,
        "duration": times[1] if len(times) > 1 else None,
        "playing": "pause" in lowered,
        "liked": _SAVED_BUTTON in lowered and _LIKE_BUTTON not in lowered,
    }


class SpotifyDucker:
    """Turn the music down while JARVIS speaks; turn it back afterwards.

    Driven by the voice session's state callback. The first SPEAKING
    remembers the volume and lowers it to ``level`` of itself; the next
    non-speaking state restores it. UIA work runs on one worker thread so
    a duck and a restore can never race each other.
    """

    def __init__(self, spotify: Any, *, level: float = 0.3) -> None:
        self._spotify = spotify
        self._level = max(0.0, min(1.0, float(level)))
        self._original: float | None = None
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="spotify-duck")

    @property
    def ducked(self) -> bool:
        return self._original is not None

    def on_voice_state(self, state: Any) -> None:
        value = getattr(state, "value", str(state)).casefold()
        if value == "speaking":
            self._executor.submit(self._duck)
        elif self._original is not None:
            self._executor.submit(self._restore)

    def _duck(self) -> None:
        if self._original is not None:
            return
        current = self._spotify.volume_sync()
        if current is None:
            return
        if not self._spotify.playing_sync():
            return
        self._original = current
        self._spotify.set_volume_sync(current * self._level)

    def _restore(self) -> None:
        original, self._original = self._original, None
        if original is not None:
            self._spotify.set_volume_sync(original)

    def restore(self) -> None:
        """Called when the voice session ends, whatever state it ended in."""
        self._executor.submit(self._restore)


class SleepTimer:
    """Fade out over the last half minute, then pause - and say so."""

    FADE_SECONDS = 30.0
    FADE_STEPS = 6

    def __init__(self, spotify: Any, *, sleep: Callable[[float], Any] = asyncio.sleep) -> None:
        self._spotify = spotify
        self._sleep = sleep
        self._task: asyncio.Task | None = None
        self.ends_at: float | None = None
        self.outcome: str | None = None

    @property
    def active(self) -> bool:
        return self._task is not None and not self._task.done()

    def start(self, minutes: float) -> None:
        self.cancel()
        self.outcome = None
        self.ends_at = time.monotonic() + minutes * 60
        self._task = asyncio.create_task(self._run(minutes * 60))

    def cancel(self) -> bool:
        if self._task is not None and not self._task.done():
            self._task.cancel()
            self._task = None
            self.ends_at = None
            self.outcome = "cancelled"
            return True
        return False

    async def _run(self, seconds: float) -> None:
        fade = min(self.FADE_SECONDS, seconds)
        await self._sleep(max(0.0, seconds - fade))
        original = await asyncio.to_thread(self._spotify.volume_sync)
        if original:
            for step in range(1, self.FADE_STEPS + 1):
                await asyncio.to_thread(
                    self._spotify.set_volume_sync, original * (1 - step / self.FADE_STEPS)
                )
                await self._sleep(fade / self.FADE_STEPS)
        paused = await self._spotify.pause_if_playing()
        if original:
            await asyncio.to_thread(self._spotify.set_volume_sync, original)
        self.outcome = "paused" if paused else "pause_unverified"
        self.ends_at = None


def result(tool: str, status: ToolExecutionStatus, message: str, **extra: Any) -> ToolResult:
    return ToolResult(status, tool, message=message, **extra)
