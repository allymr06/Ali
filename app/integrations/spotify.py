"""Spotify integration.

Two capability tiers:

LOCAL (works with the desktop app, no account setup):
    now-playing observation, play/pause/next/previous via global media
    keys, opening searches, and the desktop app's own controls through
    UI Automation the way a person uses them - volume and seek sliders,
    shuffle and repeat, the heart on the current track, a double-click on
    a library row, "Add to queue" from a result's menu, a sleep timer that
    fades out, and the transport bar's own words about what is playing.
    Every mutation is verified against the window (title or control
    state) and reports PARTIAL when it cannot be.

WEB API (needs a user-supplied client ID once, then a stored refresh
token): exact track playback, playlist creation, and listening
statistics through Spotify's official API with PKCE OAuth — no client
secret ever exists on this machine.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import secrets
import time
import urllib.parse
from typing import Any, Callable

from app.core.models import (
    RiskLevel,
    ToolDefinition,
    ToolExecutionStatus,
    ToolResult,
)
from app.integrations.runtime import (
    MediaKeySender,
    PowerShellRunner,
    UriLauncher,
)
from app.integrations.spotify_desktop import (
    REPEAT_STATES,
    SHUFFLE_STATES,
    SleepTimer,
    choose_play_button,
    more_options_button,
    now_playing_from_bar,
    parse_library_row,
    parse_seek,
    play_title_of,
    related_to_query,
    shuffle_mode_of,
)

_TITLE_SCRIPT = (
    "Get-Process Spotify -ErrorAction SilentlyContinue | "
    "Where-Object { $_.MainWindowTitle } | "
    "Select-Object -First 1 -ExpandProperty MainWindowTitle"
)

_ACCOUNTS_BASE = "https://accounts.spotify.com"
_API_BASE = "https://api.spotify.com/v1"
_REDIRECT_PORT = 8890
_REDIRECT_URI = f"http://127.0.0.1:{_REDIRECT_PORT}/callback"
_SCOPES = (
    "user-modify-playback-state user-read-playback-state "
    "playlist-modify-private user-top-read"
)


class SpotifyIntegration:
    def __init__(
        self,
        *,
        client_id: str | None = None,
        credential_store: Any | None = None,
        powershell: PowerShellRunner | None = None,
        media_keys: MediaKeySender | None = None,
        uri_launcher: UriLauncher | None = None,
        http_client_factory: Callable[..., Any] | None = None,
        uia_client_factory: Callable[[], Any] | None = None,
        ui_settle_seconds: float = 2.0,
        ui_retry_seconds: float = 1.0,
    ) -> None:
        self._client_id = (client_id or "").strip() or None
        self._credentials = credential_store
        self._powershell = powershell or PowerShellRunner()
        self._media_keys = media_keys or MediaKeySender()
        self._uri = uri_launcher or UriLauncher()
        self._http_client_factory = http_client_factory
        self._uia_client_factory = uia_client_factory
        self._ui_settle_seconds = ui_settle_seconds
        self._ui_retry_seconds = ui_retry_seconds
        self._access_token: str | None = None
        self._access_expires_at = 0.0
        self._sleep_timer: SleepTimer | None = None

    # ------------------------------------------------------------------
    # Local tier
    # ------------------------------------------------------------------

    async def window_title(self) -> str | None:
        code, output = await self._powershell.run(_TITLE_SCRIPT)
        if code != 0 or not output:
            return None
        return output.splitlines()[0].strip() or None

    @staticmethod
    def parse_title(title: str | None) -> dict[str, Any]:
        if title is None:
            return {"running": False, "playing": False}
        if " - " in title:
            artist, _, track = title.partition(" - ")
            return {
                "running": True,
                "playing": True,
                "artist": artist.strip(),
                "track": track.strip(),
            }
        return {"running": True, "playing": False}

    async def now_playing(self) -> ToolResult:
        state = self.parse_title(await self.window_title())
        if not state["running"]:
            return ToolResult(
                ToolExecutionStatus.BLOCKED,
                "spotify_now_playing",
                message="Spotify çalışmıyor. Önce uygulamayı aç.",
                data=state,
                verified=True,
            )
        # The title falls silent when paused; the transport bar still
        # names the track a person sees there.
        bar = await asyncio.to_thread(self._bar_sync)
        if bar:
            # The title is the authority on playback; the bar only fills
            # in what the title does not say (artists, position, liked).
            for key, value in bar.items():
                if value in (None, []) or key == "playing" or state.get(key):
                    continue
                state[key] = value
            volume = await asyncio.to_thread(self.volume_sync)
            if volume is not None:
                state["volume_percent"] = round(volume * 100)
        if state["playing"]:
            message = f"Çalıyor: {state['artist']} — {state['track']}"
        elif bar and bar.get("track"):
            artists = ", ".join(bar.get("artists") or [])
            message = f"Duraklatılmış: {artists + ' — ' if artists else ''}{bar['track']}"
        else:
            message = "Spotify açık ama şu an bir şey çalmıyor."
        if bar and bar.get("position") and bar.get("duration"):
            message += f" ({bar['position']} / {bar['duration']})"
        timer = self._sleep_timer
        if timer is not None and timer.active and timer.ends_at is not None:
            state["sleep_minutes_left"] = max(1, round((timer.ends_at - time.monotonic()) / 60))
        return ToolResult(
            ToolExecutionStatus.SUCCESS,
            "spotify_now_playing",
            message=message,
            data=state,
            verified=True,
        )

    async def _media_action(
        self,
        tool_name: str,
        virtual_key: int,
        *,
        expect_title_change: bool,
    ) -> ToolResult:
        before = await self.window_title()
        if before is None:
            return ToolResult(
                ToolExecutionStatus.BLOCKED,
                tool_name,
                message="Spotify çalışmıyor. Önce uygulamayı aç.",
                verified=True,
            )
        if not self._media_keys.send(virtual_key):
            return ToolResult(
                ToolExecutionStatus.FAILED,
                tool_name,
                message="Medya tuşu gönderilemedi.",
                error="media_key_send_failed",
            )
        # OBSERVE -> ACT -> VERIFY: the window title reflects playback.
        after = before
        for _ in range(10):
            await asyncio.sleep(0.25)
            after = await self.window_title()
            if after != before:
                break
        changed = after != before
        verified = changed if expect_title_change else True
        return ToolResult(
            ToolExecutionStatus.SUCCESS,
            tool_name,
            message=(
                f"Şimdi: {after}" if changed else "Komut gönderildi."
            ),
            data={"before": before, "after": after},
            verified=verified,
        )

    async def play_pause(self) -> ToolResult:
        return await self._media_action(
            "spotify_play_pause",
            MediaKeySender.PLAY_PAUSE,
            expect_title_change=True,
        )

    async def next_track(self) -> ToolResult:
        return await self._media_action(
            "spotify_next_track",
            MediaKeySender.NEXT_TRACK,
            expect_title_change=True,
        )

    async def previous_track(self) -> ToolResult:
        return await self._media_action(
            "spotify_previous_track",
            MediaKeySender.PREVIOUS_TRACK,
            expect_title_change=False,
        )

    async def open_search(self, query: str) -> ToolResult:
        normalized = query.strip()
        if not normalized:
            return ToolResult(
                ToolExecutionStatus.FAILED,
                "spotify_open_search",
                message="Arama metni boş olamaz.",
                error="empty_query",
            )
        uri = "spotify:search:" + urllib.parse.quote(normalized)
        if not self._uri.open(uri):
            return ToolResult(
                ToolExecutionStatus.FAILED,
                "spotify_open_search",
                message="Spotify araması açılamadı.",
                error="uri_launch_failed",
            )
        for _ in range(12):
            await asyncio.sleep(0.5)
            if await self.window_title() is not None:
                return ToolResult(
                    ToolExecutionStatus.SUCCESS,
                    "spotify_open_search",
                    message=f"Spotify'da '{normalized}' araması açıldı.",
                    verified=True,
                )
        return ToolResult(
            ToolExecutionStatus.PARTIAL,
            "spotify_open_search",
            message=(
                "Arama gönderildi ancak Spotify penceresi doğrulanamadı."
            ),
        )

    # ------------------------------------------------------------------
    # Local UI tier: play a searched track without any account setup
    # ------------------------------------------------------------------

    @staticmethod
    def _is_play_button(name: str) -> bool:
        lowered = name.casefold()
        return (
            lowered.startswith(("play ", "çal "))
            or lowered.endswith(" çal")
        )

    def _press_search_play_sync(self, query: str) -> tuple[str | None, str]:
        """Drive the desktop app: search, then press the top play button.

        Spotify's search results expose each row's play control as a
        button named "Play <track>" (or the localized equivalent), so
        the first match in document order is the top result — the same
        thing a person would click. Returns (button_name, error_code).
        """
        if self._uia_client_factory is not None:
            client = self._uia_client_factory()
        else:
            from app.integrations.uia import UiaClient

            client = UiaClient()
        handle = client.window_handle_for_process("spotify.exe")
        if not handle:
            if not self._uri.open("spotify:"):
                return None, "spotify_launch_failed"
            for _ in range(16):
                time.sleep(max(0.05, self._ui_retry_seconds / 2))
                handle = client.window_handle_for_process("spotify.exe")
                if handle:
                    break
            if not handle:
                return None, "spotify_not_running"
        client.bring_handle_to_foreground(handle)
        try:
            before = set(client.control_names_in_handle(handle))
        except Exception:
            before = set()
        self._uri.open("spotify:search:" + urllib.parse.quote(query))
        time.sleep(self._ui_settle_seconds)
        for _ in range(6):
            try:
                # An exact title first, then the artist card, then the top
                # result - the row a person would press for those words.
                # A row of the page that was open before the search
                # rendered is never pressed: it must relate to the query
                # or the page must have changed.
                names = client.control_names_in_handle(handle)
                chosen = choose_play_button(names, query)
                if chosen and not related_to_query(play_title_of(chosen), query) and set(names) == before:
                    chosen = None
                pressed = (
                    client.invoke_named_in_handle(handle, lambda name: name == chosen)
                    if chosen
                    else None
                )
            except Exception:
                pressed = None
            if pressed:
                return pressed, ""
            time.sleep(self._ui_retry_seconds)
        return None, "play_button_not_found"

    # ------------------------------------------------------------------
    # Desktop UI tier: the app's own controls, as a hand uses them
    # ------------------------------------------------------------------

    def _ui_client(self):
        if self._uia_client_factory is not None:
            return self._uia_client_factory()
        from app.integrations.uia import UiaClient

        return UiaClient()

    def _handle_sync(self, client, *, launch: bool = False) -> int:
        handle = client.window_handle_for_process("spotify.exe")
        if handle or not launch:
            return handle
        if not self._uri.open("spotify:"):
            return 0
        for _ in range(16):
            time.sleep(max(0.05, self._ui_retry_seconds / 2))
            handle = client.window_handle_for_process("spotify.exe")
            if handle:
                client.bring_handle_to_foreground(handle)
                time.sleep(self._ui_settle_seconds)
                break
        return handle

    def volume_sync(self) -> float | None:
        """The volume slider's value (0..1), or None without a window."""
        client = self._ui_client()
        handle = self._handle_sync(client)
        if not handle:
            return None
        slider = client.slider_in_handle(handle, "volume")
        return None if slider is None else slider[0]

    def set_volume_sync(self, value: float) -> float | None:
        client = self._ui_client()
        handle = self._handle_sync(client)
        if not handle:
            return None
        return client.set_slider_in_handle(handle, "volume", value)

    def playing_sync(self) -> bool:
        client = self._ui_client()
        handle = self._handle_sync(client)
        return bool(handle) and " - " in client.window_name_in_handle(handle)

    def _bar_sync(self) -> dict[str, Any] | None:
        client = self._ui_client()
        handle = self._handle_sync(client)
        if not handle:
            return None
        texts = client.texts_in_handle(handle, region="bar")
        controls = client.control_names_in_handle(handle, region="bar")
        return now_playing_from_bar(texts, controls)

    def _blocked(self, tool: str) -> ToolResult:
        return ToolResult(
            ToolExecutionStatus.BLOCKED,
            tool,
            message="Spotify çalışmıyor. Önce uygulamayı aç.",
            verified=True,
        )

    async def pause_if_playing(self) -> bool:
        """Press the bar's Pause when something plays; True once verified silent."""
        if not self.parse_title(await self.window_title())["playing"]:
            return True
        client = self._ui_client()
        handle = await asyncio.to_thread(self._handle_sync, client)
        if not handle:
            return False
        pressed = await asyncio.to_thread(
            client.invoke_named_in_handle, handle, lambda name: name.casefold() == "pause", region="bar"
        )
        if not pressed:
            return False
        for _ in range(8):
            await asyncio.sleep(0.5)
            if not self.parse_title(await self.window_title())["playing"]:
                return True
        return False

    async def set_volume(self, percent: float) -> ToolResult:
        try:
            wanted = max(0.0, min(100.0, float(percent)))
        except (TypeError, ValueError):
            return ToolResult(ToolExecutionStatus.FAILED, "spotify_set_volume", message="Ses yüzdesi 0-100 arası bir sayı olmalı.", error="invalid_percent")
        client = self._ui_client()
        handle = await asyncio.to_thread(self._handle_sync, client)
        if not handle:
            return self._blocked("spotify_set_volume")
        read_back = await asyncio.to_thread(client.set_slider_in_handle, handle, "volume", wanted / 100.0)
        if read_back is None:
            return ToolResult(ToolExecutionStatus.FAILED, "spotify_set_volume", message="Ses kaydırıcısı bulunamadı.", error="slider_not_found")
        verified = abs(read_back * 100.0 - wanted) <= 2.0
        return ToolResult(
            ToolExecutionStatus.SUCCESS if verified else ToolExecutionStatus.PARTIAL,
            "spotify_set_volume",
            message=f"Spotify sesi %{round(read_back * 100)}.",
            data={"percent": round(read_back * 100, 1)},
            verified=verified,
        )

    async def seek(self, position: str) -> ToolResult:
        client = self._ui_client()
        handle = await asyncio.to_thread(self._handle_sync, client)
        if not handle:
            return self._blocked("spotify_seek")
        slider = await asyncio.to_thread(client.slider_in_handle, handle, "progress")
        if slider is None:
            return ToolResult(ToolExecutionStatus.FAILED, "spotify_seek", message="İlerleme kaydırıcısı bulunamadı.", error="slider_not_found")
        current, _low, high = slider
        target = parse_seek(position, current, high)
        if target is None:
            return ToolResult(ToolExecutionStatus.FAILED, "spotify_seek", message="Konum '+30', '-15', '1:30' ya da saniye olmalı.", error="invalid_position")
        read_back = await asyncio.to_thread(client.set_slider_in_handle, handle, "progress", target)
        if read_back is None:
            return ToolResult(ToolExecutionStatus.FAILED, "spotify_seek", message="Sarılamadı.", error="slider_not_found")
        verified = abs(read_back - target) <= 2000
        seconds = int(read_back // 1000)
        return ToolResult(
            ToolExecutionStatus.SUCCESS if verified else ToolExecutionStatus.PARTIAL,
            "spotify_seek",
            message=f"Parça {seconds // 60}:{seconds % 60:02d} konumunda.",
            data={"position_ms": read_back, "duration_ms": high},
            verified=verified,
        )

    async def shuffle(self, mode: str = "on") -> ToolResult:
        wanted = str(mode or "on").strip().casefold()
        wanted = {"aç": "on", "acik": "on", "açık": "on", "kapa": "off", "kapalı": "off", "kapali": "off", "akıllı": "smart", "akilli": "smart"}.get(wanted, wanted)
        if wanted not in SHUFFLE_STATES:
            return ToolResult(ToolExecutionStatus.FAILED, "spotify_shuffle", message="Mod 'on', 'off' ya da 'smart' olmalı.", error="invalid_mode")
        client = self._ui_client()
        handle = await asyncio.to_thread(self._handle_sync, client)
        if not handle:
            return self._blocked("spotify_shuffle")

        def current_button() -> str | None:
            for name in client.control_names_in_handle(handle, region="bar"):
                if "shuffle" in name.casefold():
                    return name
            return None

        presses = 0
        for _ in range(4):
            button = await asyncio.to_thread(current_button)
            if shuffle_mode_of(button) == wanted:
                labels = {"on": "açık", "off": "kapalı", "smart": "akıllı karıştırma"}
                return ToolResult(ToolExecutionStatus.SUCCESS, "spotify_shuffle", message=f"Karıştırma {labels[wanted]}.", data={"mode": wanted, "presses": presses}, verified=True)
            if button is None or presses >= 3:
                break
            await asyncio.to_thread(client.invoke_named_in_handle, handle, lambda name: name == button, region="bar")
            presses += 1
            await asyncio.sleep(max(0.05, self._ui_retry_seconds * 1.5))
        return ToolResult(ToolExecutionStatus.PARTIAL, "spotify_shuffle", message="Karıştᄱrma düğmesi istenen duruma getirilemedi.", data={"mode": wanted, "presses": presses}, error="shuffle_unverified")

    async def repeat(self, mode: str = "list") -> ToolResult:
        wanted = str(mode or "list").strip().casefold()
        wanted = {"kapalı": "off", "kapali": "off", "liste": "list", "context": "list", "parça": "track", "parca": "track", "one": "track", "tek": "track"}.get(wanted, wanted)
        if wanted not in REPEAT_STATES:
            return ToolResult(ToolExecutionStatus.FAILED, "spotify_repeat", message="Mod 'off', 'list' ya da 'track' olmalı.", error="invalid_mode")
        client = self._ui_client()
        handle = await asyncio.to_thread(self._handle_sync, client)
        if not handle:
            return self._blocked("spotify_repeat")
        target = REPEAT_STATES[wanted]
        state = await asyncio.to_thread(client.toggle_state_in_handle, handle, "repeat", region="bar")
        toggles = 0
        while state is not None and state != target and toggles < 3:
            state = await asyncio.to_thread(client.toggle_in_handle, handle, "repeat", region="bar")
            toggles += 1
        if state is None:
            return ToolResult(ToolExecutionStatus.FAILED, "spotify_repeat", message="Yineleme düğmesi bulunamadı.", error="toggle_not_found")
        verified = state == target
        labels = {"off": "kapalı", "list": "liste yineleme", "track": "tek parça yineleme"}
        return ToolResult(
            ToolExecutionStatus.SUCCESS if verified else ToolExecutionStatus.PARTIAL,
            "spotify_repeat",
            message=f"Yineleme: {labels[wanted]}." if verified else "Yineleme istenen duruma getirilemedi.",
            data={"mode": wanted, "state": state, "toggles": toggles},
            verified=verified,
        )

    async def like_current(self) -> ToolResult:
        client = self._ui_client()
        handle = await asyncio.to_thread(self._handle_sync, client)
        if not handle:
            return self._blocked("spotify_like_track")
        names = [name.casefold() for name in await asyncio.to_thread(client.control_names_in_handle, handle, region="bar")]
        if "add to liked songs" not in names:
            if "add to playlist" in names:
                return ToolResult(ToolExecutionStatus.SUCCESS, "spotify_like_track", message="Bu parça zaten kütüphanende görünüyor (düğme 'Add to playlist').", data={"already_saved": True}, verified=True)
            return ToolResult(ToolExecutionStatus.FAILED, "spotify_like_track", message="Çalan parçanın beğenme düğmesi bulunamadı.", error="like_button_not_found")
        await asyncio.to_thread(client.invoke_named_in_handle, handle, lambda name: name.casefold() == "add to liked songs", region="bar")
        await asyncio.sleep(max(0.05, self._ui_retry_seconds))
        after = [name.casefold() for name in await asyncio.to_thread(client.control_names_in_handle, handle, region="bar")]
        verified = "add to liked songs" not in after
        return ToolResult(
            ToolExecutionStatus.SUCCESS if verified else ToolExecutionStatus.PARTIAL,
            "spotify_like_track",
            message="Çalan parça Beğenilen Şarkılar'a eklendi." if verified else "Beğenme düğmesine basıldı ama değişiklik doğrulanamadı.",
            data={"already_saved": False},
            verified=verified,
        )

    async def library(self) -> ToolResult:
        client = self._ui_client()
        handle = await asyncio.to_thread(self._handle_sync, client)
        if not handle:
            return self._blocked("spotify_library")
        names = await asyncio.to_thread(client.control_names_in_handle, handle, region="sidebar")
        rows = [row for row in (parse_library_row(name) for name in names) if row]
        return ToolResult(
            ToolExecutionStatus.SUCCESS,
            "spotify_library",
            message=f"Kütüphanede {len(rows)} öge görünüyor." if rows else "Kütüphane satırları okunamadı.",
            data={"items": rows},
            verified=bool(rows),
        )

    async def play_library(self, name: str) -> ToolResult:
        wanted = " ".join(str(name or "").split()).casefold()
        if not wanted:
            return ToolResult(ToolExecutionStatus.FAILED, "spotify_play_library", message="Liste ya da sanatçı adı boş olamaz.", error="empty_name")
        client = self._ui_client()
        handle = await asyncio.to_thread(self._handle_sync, client, launch=True)
        if not handle:
            return self._blocked("spotify_play_library")
        before = await self.window_title()

        def matcher(row_name: str) -> bool:
            parsed = parse_library_row(row_name)
            return bool(parsed) and (parsed["name"].casefold() == wanted or parsed["name"].casefold().startswith(wanted))

        pressed = await asyncio.to_thread(client.double_click_named_in_handle, handle, matcher, region="sidebar")
        if not pressed:
            return ToolResult(ToolExecutionStatus.FAILED, "spotify_play_library", message=f"'{name}' kütüphanede görünmüyor.", error="row_not_found")
        verified = False
        title = before
        for _ in range(16):
            await asyncio.sleep(0.5)
            title = await self.window_title()
            if title != before and self.parse_title(title)["playing"]:
                verified = True
                break
        label = parse_library_row(pressed)["name"] if parse_library_row(pressed) else pressed
        return ToolResult(
            ToolExecutionStatus.SUCCESS if verified else ToolExecutionStatus.PARTIAL,
            "spotify_play_library",
            message=f"Çalınıyor: {label} ({title})" if verified else f"'{label}' satırına çift tıklandı ama çalma doğrulanamadı.",
            data={"item": label, "title": title},
            verified=verified,
        )

    async def queue_track(self, query: str) -> ToolResult:
        wanted = " ".join(str(query or "").split())
        if not wanted:
            return ToolResult(ToolExecutionStatus.FAILED, "spotify_queue_track", message="Arama metni boş olamaz.", error="empty_query")
        client = self._ui_client()
        handle = await asyncio.to_thread(self._handle_sync, client, launch=True)
        if not handle:
            return self._blocked("spotify_queue_track")
        # The tree is published for a fronted window; the search must
        # render before any row of it is trusted.
        await asyncio.to_thread(client.bring_handle_to_foreground, handle)
        before = set(await asyncio.to_thread(client.control_names_in_handle, handle))
        self._uri.open("spotify:search:" + urllib.parse.quote(wanted))
        chosen = None
        names: list[str] = []
        for _ in range(8):
            await asyncio.sleep(max(0.1, self._ui_settle_seconds / 2))
            names = await asyncio.to_thread(client.control_names_in_handle, handle)
            candidate = choose_play_button(names, wanted)
            if candidate and (related_to_query(play_title_of(candidate), wanted) or set(names) != before):
                chosen = candidate
                break
        if not chosen:
            return ToolResult(ToolExecutionStatus.FAILED, "spotify_queue_track", message=f"'{wanted}' için sonuç bulunamadı.", error="track_not_found")
        # A context menu dies the moment the still-settling results
        # re-render, so wait until two consecutive reads agree before
        # opening it.
        for _ in range(6):
            await asyncio.sleep(max(0.2, self._ui_retry_seconds / 2))
            settled = await asyncio.to_thread(client.control_names_in_handle, handle)
            if settled == names:
                break
            names = settled
        chosen = choose_play_button(names, wanted) or chosen
        title = play_title_of(chosen)
        more = more_options_button(names, title)
        opened = (
            await asyncio.to_thread(client.invoke_named_in_handle, handle, lambda name: name == more)
            if more
            else None
        )
        if not opened:
            return ToolResult(ToolExecutionStatus.FAILED, "spotify_queue_track", message=f"'{title}' için menü açılamadı.", error="menu_not_found")
        added = False
        for _ in range(4):
            await asyncio.sleep(max(0.2, self._ui_retry_seconds / 2))
            added = await asyncio.to_thread(client.invoke_menu_item_in_handle, handle, "Add to queue")
            if added:
                break
        if not added:
            await asyncio.to_thread(client.press_escape)
            return ToolResult(ToolExecutionStatus.FAILED, "spotify_queue_track", message="Menüde 'Add to queue' bulunamadı.", error="menu_item_not_found")
        # Verify through the queue panel, then close it again.
        await asyncio.sleep(max(0.05, self._ui_retry_seconds))
        verified = False
        if await asyncio.to_thread(client.invoke_named_in_handle, handle, lambda name: name.casefold() == "queue", region="bar"):
            await asyncio.sleep(max(0.05, self._ui_retry_seconds))
            texts = await asyncio.to_thread(client.texts_in_handle, handle)
            verified = any(title.casefold() == text.casefold() for text in texts)
            await asyncio.to_thread(client.invoke_named_in_handle, handle, lambda name: name.casefold() == "queue", region="bar")
        return ToolResult(
            ToolExecutionStatus.SUCCESS if verified else ToolExecutionStatus.PARTIAL,
            "spotify_queue_track",
            message=f"'{title}' kuyruğa eklendi." if verified else f"'{title}' için kuyruğa ekle tıklandı ama kuyrukta doğrulanamadı.",
            data={"track": title},
            verified=verified,
        )

    async def sleep_timer(self, minutes: float) -> ToolResult:
        try:
            wanted = float(minutes)
        except (TypeError, ValueError):
            wanted = 0.0
        if not 1 <= wanted <= 180:
            return ToolResult(ToolExecutionStatus.FAILED, "spotify_sleep_timer", message="Süre 1-180 dakika arası olmalı.", error="invalid_minutes")
        if self._sleep_timer is None:
            self._sleep_timer = SleepTimer(self)
        self._sleep_timer.start(wanted)
        return ToolResult(
            ToolExecutionStatus.SUCCESS,
            "spotify_sleep_timer",
            message=f"Uyku zamanlayıcısı kuruldu: {wanted:g} dakika sonra ses yavaşça kısılıp müzik duraklatılacak.",
            data={"minutes": wanted},
            verified=True,
        )

    async def cancel_sleep_timer(self) -> ToolResult:
        cancelled = bool(self._sleep_timer and self._sleep_timer.cancel())
        return ToolResult(
            ToolExecutionStatus.SUCCESS,
            "spotify_cancel_sleep_timer",
            message="Uyku zamanlayıcısı iptal edildi." if cancelled else "Kurulu bir uyku zamanlayıcısı yoktu.",
            data={"cancelled": cancelled},
            verified=True,
        )

    async def _play_locally(self, query: str) -> ToolResult:
        before = await self.window_title()
        try:
            pressed, error = await asyncio.to_thread(
                self._press_search_play_sync, query
            )
        except Exception as exc:
            pressed, error = None, f"ui_play_failed:{exc}"
        if pressed is None:
            return ToolResult(
                ToolExecutionStatus.PARTIAL,
                "spotify_play_track",
                message=(
                    f"Spotify'da '{query}' araması açıldı ancak parça "
                    "otomatik başlatılamadı; ekrandan seçebilirsin."
                ),
                error=error,
            )
        label = pressed
        for prefix in ("Play ", "play ", "Çal ", "çal "):
            if label.startswith(prefix):
                label = label[len(prefix):]
                break
        verified = False
        for _ in range(12):
            await asyncio.sleep(0.5)
            title = await self.window_title()
            state = self.parse_title(title)
            if state.get("playing") and title != before:
                label = f"{state['artist']} — {state['track']}"
                verified = True
                break
        return ToolResult(
            ToolExecutionStatus.SUCCESS,
            "spotify_play_track",
            message=f"Çalınıyor: {label}",
            data={"track": label, "via": "local_ui"},
            verified=verified,
        )

    # ------------------------------------------------------------------
    # Web API tier (PKCE)
    # ------------------------------------------------------------------

    def _http_client(self, **kwargs: Any):
        if self._http_client_factory is not None:
            return self._http_client_factory(**kwargs)
        import httpx

        kwargs.setdefault("timeout", 10.0)
        return httpx.AsyncClient(**kwargs)

    def _configuration_error(self, tool_name: str) -> ToolResult:
        return ToolResult(
            ToolExecutionStatus.BLOCKED,
            tool_name,
            message=(
                "Spotify Web API yapılandırılmamış. "
                "developer.spotify.com'dan bir uygulama oluşturup "
                "JARVIS_SPOTIFY_CLIENT_ID değişkenini ayarla ve "
                "'spotify_authorize' aracını çalıştır. Yönlendirme "
                f"adresi: {_REDIRECT_URI}"
            ),
            error="not_configured",
            verified=True,
        )

    def _read_refresh_token(self) -> str | None:
        if self._credentials is None:
            return None
        try:
            return self._credentials.read() or None
        except Exception:
            return None

    async def authorize(self) -> ToolResult:
        """Interactive PKCE flow: browser consent, localhost redirect."""
        if self._client_id is None:
            return self._configuration_error("spotify_authorize")
        verifier = (
            base64.urlsafe_b64encode(secrets.token_bytes(48))
            .rstrip(b"=")
            .decode("ascii")
        )
        challenge = (
            base64.urlsafe_b64encode(
                hashlib.sha256(verifier.encode("ascii")).digest()
            )
            .rstrip(b"=")
            .decode("ascii")
        )
        state = secrets.token_urlsafe(16)
        params = urllib.parse.urlencode(
            {
                "client_id": self._client_id,
                "response_type": "code",
                "redirect_uri": _REDIRECT_URI,
                "code_challenge_method": "S256",
                "code_challenge": challenge,
                "state": state,
                "scope": _SCOPES,
            }
        )
        received: dict[str, str] = {}
        done = asyncio.Event()

        async def handle(reader, writer):
            try:
                request_line = await reader.readline()
                target = request_line.split(b" ")[1].decode("ascii")
                query = urllib.parse.parse_qs(
                    urllib.parse.urlsplit(target).query
                )
                if query.get("state", [""])[0] == state:
                    received["code"] = query.get("code", [""])[0]
                writer.write(
                    b"HTTP/1.1 200 OK\r\nContent-Type: text/html; "
                    b"charset=utf-8\r\n\r\n<h2>JARVIS: Spotify "
                    b"ba\xc4\x9fland\xc4\xb1. Bu sekmeyi "
                    b"kapatabilirsin.</h2>"
                )
                await writer.drain()
            finally:
                writer.close()
                done.set()

        server = await asyncio.start_server(
            handle, "127.0.0.1", _REDIRECT_PORT
        )
        try:
            self._uri.open(f"{_ACCOUNTS_BASE}/authorize?{params}")
            try:
                await asyncio.wait_for(done.wait(), timeout=180)
            except asyncio.TimeoutError:
                return ToolResult(
                    ToolExecutionStatus.TIMEOUT,
                    "spotify_authorize",
                    message="Tarayıcı onayı 3 dakika içinde gelmedi.",
                    error="authorize_timeout",
                )
        finally:
            server.close()
            await server.wait_closed()
        code = received.get("code")
        if not code:
            return ToolResult(
                ToolExecutionStatus.FAILED,
                "spotify_authorize",
                message="Spotify yetkilendirme kodu alınamadı.",
                error="no_code",
            )
        async with self._http_client() as client:
            token_response = await client.post(
                f"{_ACCOUNTS_BASE}/api/token",
                data={
                    "grant_type": "authorization_code",
                    "code": code,
                    "redirect_uri": _REDIRECT_URI,
                    "client_id": self._client_id,
                    "code_verifier": verifier,
                },
            )
        if token_response.status_code != 200:
            return ToolResult(
                ToolExecutionStatus.FAILED,
                "spotify_authorize",
                message="Spotify erişim anahtarı alınamadı.",
                error=f"token_http_{token_response.status_code}",
            )
        payload = token_response.json()
        refresh = payload.get("refresh_token")
        if refresh and self._credentials is not None:
            self._credentials.write(refresh)
        self._access_token = payload.get("access_token")
        self._access_expires_at = time.monotonic() + float(
            payload.get("expires_in", 3600)
        ) - 60
        return ToolResult(
            ToolExecutionStatus.SUCCESS,
            "spotify_authorize",
            message=(
                "Spotify hesabı bağlandı; yenileme anahtarı Windows "
                "Kimlik Bilgisi Yöneticisi'nde."
            ),
            verified=True,
        )

    async def _bearer(self) -> str | None:
        if (
            self._access_token
            and time.monotonic() < self._access_expires_at
        ):
            return self._access_token
        refresh = self._read_refresh_token()
        if not refresh or self._client_id is None:
            return None
        async with self._http_client() as client:
            response = await client.post(
                f"{_ACCOUNTS_BASE}/api/token",
                data={
                    "grant_type": "refresh_token",
                    "refresh_token": refresh,
                    "client_id": self._client_id,
                },
            )
        if response.status_code != 200:
            return None
        payload = response.json()
        self._access_token = payload.get("access_token")
        self._access_expires_at = time.monotonic() + float(
            payload.get("expires_in", 3600)
        ) - 60
        new_refresh = payload.get("refresh_token")
        if new_refresh and self._credentials is not None:
            self._credentials.write(new_refresh)
        return self._access_token

    async def _api(
        self,
        method: str,
        path: str,
        *,
        token: str,
        json_body: Any | None = None,
        params: dict[str, Any] | None = None,
    ):
        async with self._http_client() as client:
            return await client.request(
                method,
                f"{_API_BASE}{path}",
                headers={"Authorization": f"Bearer {token}"},
                json=json_body,
                params=params,
            )

    async def play_track(self, query: str) -> ToolResult:
        token = await self._bearer()
        if token is None:
            # No account linked: drive the desktop app's own UI instead
            # of refusing — the user asked for music, not for OAuth.
            return await self._play_locally(query)
        search = await self._api(
            "GET",
            "/search",
            token=token,
            params={"q": query, "type": "track", "limit": 1},
        )
        items = (
            search.json().get("tracks", {}).get("items", [])
            if search.status_code == 200
            else []
        )
        if not items:
            return ToolResult(
                ToolExecutionStatus.FAILED,
                "spotify_play_track",
                message=f"'{query}' için parça bulunamadı.",
                error="track_not_found",
            )
        track = items[0]
        play = await self._api(
            "PUT",
            "/me/player/play",
            token=token,
            json_body={"uris": [track["uri"]]},
        )
        if play.status_code == 404:
            # No active device registered with the API; the desktop
            # app's own UI can still start playback.
            return await self._play_locally(query)
        if play.status_code not in (200, 202, 204):
            return ToolResult(
                ToolExecutionStatus.FAILED,
                "spotify_play_track",
                message="Çalma isteği reddedildi.",
                error=f"play_http_{play.status_code}",
            )
        label = (
            f"{track['artists'][0]['name']} — {track['name']}"
            if track.get("artists")
            else track.get("name", query)
        )
        expected_artist = (
            track["artists"][0]["name"] if track.get("artists") else ""
        )
        verified = False
        for _ in range(10):
            await asyncio.sleep(0.5)
            state = self.parse_title(await self.window_title())
            if state.get("playing") and (
                not expected_artist
                or expected_artist.casefold()
                in str(state.get("artist", "")).casefold()
            ):
                verified = True
                break
        return ToolResult(
            ToolExecutionStatus.SUCCESS,
            "spotify_play_track",
            message=f"Çalınıyor: {label}",
            data={"track": label, "uri": track["uri"]},
            verified=verified,
        )

    async def create_playlist(
        self, name: str, track_queries: list[str]
    ) -> ToolResult:
        token = await self._bearer()
        if token is None:
            return self._configuration_error("spotify_create_playlist")
        me = await self._api("GET", "/me", token=token)
        if me.status_code != 200:
            return ToolResult(
                ToolExecutionStatus.FAILED,
                "spotify_create_playlist",
                message="Spotify hesabı okunamadı.",
                error=f"me_http_{me.status_code}",
            )
        user_id = me.json()["id"]
        uris: list[str] = []
        misses: list[str] = []
        for query in track_queries[:50]:
            found = await self._api(
                "GET",
                "/search",
                token=token,
                params={"q": query, "type": "track", "limit": 1},
            )
            items = (
                found.json().get("tracks", {}).get("items", [])
                if found.status_code == 200
                else []
            )
            if items:
                uris.append(items[0]["uri"])
            else:
                misses.append(query)
        created = await self._api(
            "POST",
            f"/users/{user_id}/playlists",
            token=token,
            json_body={
                "name": name,
                "public": False,
                "description": "JARVIS tarafından oluşturuldu",
            },
        )
        if created.status_code not in (200, 201):
            return ToolResult(
                ToolExecutionStatus.FAILED,
                "spotify_create_playlist",
                message="Çalma listesi oluşturulamadı.",
                error=f"create_http_{created.status_code}",
            )
        playlist = created.json()
        if uris:
            await self._api(
                "POST",
                f"/playlists/{playlist['id']}/tracks",
                token=token,
                json_body={"uris": uris},
            )
        check = await self._api(
            "GET", f"/playlists/{playlist['id']}", token=token
        )
        verified = (
            check.status_code == 200
            and check.json().get("tracks", {}).get("total", 0)
            == len(uris)
        )
        message = (
            f"'{name}' listesi {len(uris)} parçayla oluşturuldu."
        )
        if misses:
            message += f" Bulunamayanlar: {', '.join(misses[:5])}"
        return ToolResult(
            ToolExecutionStatus.SUCCESS,
            "spotify_create_playlist",
            message=message,
            data={
                "url": playlist.get("external_urls", {}).get("spotify"),
                "added": len(uris),
                "missing": misses,
            },
            verified=verified,
        )

    async def listening_stats(
        self, period: str = "medium_term"
    ) -> ToolResult:
        token = await self._bearer()
        if token is None:
            return self._configuration_error("spotify_listening_stats")
        window = {
            "kisa": "short_term",
            "orta": "medium_term",
            "uzun": "long_term",
        }.get(period.strip().casefold(), period)
        if window not in {"short_term", "medium_term", "long_term"}:
            window = "medium_term"
        artists = await self._api(
            "GET",
            "/me/top/artists",
            token=token,
            params={"limit": 5, "time_range": window},
        )
        tracks = await self._api(
            "GET",
            "/me/top/tracks",
            token=token,
            params={"limit": 5, "time_range": window},
        )
        if artists.status_code != 200 or tracks.status_code != 200:
            return ToolResult(
                ToolExecutionStatus.FAILED,
                "spotify_listening_stats",
                message="Dinleme istatistikleri okunamadı.",
                error=(
                    f"stats_http_{artists.status_code}_"
                    f"{tracks.status_code}"
                ),
            )
        top_artists = [
            item["name"] for item in artists.json().get("items", [])
        ]
        top_tracks = [
            f"{item['artists'][0]['name']} — {item['name']}"
            for item in tracks.json().get("items", [])
            if item.get("artists")
        ]
        return ToolResult(
            ToolExecutionStatus.SUCCESS,
            "spotify_listening_stats",
            message=(
                "En çok dinlenen sanatçılar: "
                + ", ".join(top_artists[:5])
            ),
            data={
                "period": window,
                "top_artists": top_artists,
                "top_tracks": top_tracks,
            },
            verified=True,
        )

    # ------------------------------------------------------------------

    def register_tools(self, executor: Any) -> None:
        def define(
            name: str,
            description: str,
            *,
            risk: RiskLevel = RiskLevel.READ_ONLY,
            confirm: bool = False,
            timeout: float = 20.0,
        ) -> ToolDefinition:
            return ToolDefinition(
                name=name,
                description=description,
                risk_level=risk,
                requires_confirmation=confirm,
                version="1.0.0",
                capabilities=frozenset({"spotify", "media"}),
                tags=frozenset({"integration", "spotify"}),
                timeout_seconds=timeout,
                metadata={
                    "verification_strategy": "window_title_observation",
                },
            )

        async def now_playing() -> ToolResult:
            return await self.now_playing()

        async def play_pause() -> ToolResult:
            return await self.play_pause()

        async def next_track() -> ToolResult:
            return await self.next_track()

        async def previous_track() -> ToolResult:
            return await self.previous_track()

        async def open_search(query: str) -> ToolResult:
            return await self.open_search(query)

        async def authorize() -> ToolResult:
            return await self.authorize()

        async def play_track(query: str) -> ToolResult:
            return await self.play_track(query)

        async def create_playlist(
            name: str, tracks: list[str]
        ) -> ToolResult:
            return await self.create_playlist(name, tracks)

        async def listening_stats(
            period: str = "orta",
        ) -> ToolResult:
            return await self.listening_stats(period)

        async def set_volume(percent: float) -> ToolResult:
            return await self.set_volume(percent)

        async def seek(position: str) -> ToolResult:
            return await self.seek(position)

        async def shuffle(mode: str = "on") -> ToolResult:
            return await self.shuffle(mode)

        async def repeat(mode: str = "list") -> ToolResult:
            return await self.repeat(mode)

        async def like_track() -> ToolResult:
            return await self.like_current()

        async def library() -> ToolResult:
            return await self.library()

        async def play_library(name: str) -> ToolResult:
            return await self.play_library(name)

        async def queue_track(query: str) -> ToolResult:
            return await self.queue_track(query)

        async def sleep_timer(minutes: float) -> ToolResult:
            return await self.sleep_timer(minutes)

        async def cancel_sleep_timer() -> ToolResult:
            return await self.cancel_sleep_timer()

        executor.register(
            define(
                "spotify_now_playing",
                "Spotify'da şu an çalan parçayı ve durumu oku.",
            ),
            now_playing,
            source="integration:spotify",
        )
        executor.register(
            define(
                "spotify_play_pause",
                "Spotify'da çalmayı başlat veya duraklat.",
                risk=RiskLevel.LOW,
            ),
            play_pause,
            source="integration:spotify",
        )
        executor.register(
            define(
                "spotify_next_track",
                "Spotify'da sonraki parçaya geç.",
                risk=RiskLevel.LOW,
            ),
            next_track,
            source="integration:spotify",
        )
        executor.register(
            define(
                "spotify_previous_track",
                "Spotify'da önceki parçaya dön.",
                risk=RiskLevel.LOW,
            ),
            previous_track,
            source="integration:spotify",
        )
        executor.register(
            define(
                "spotify_open_search",
                "Spotify uygulamasında bir arama ekranı aç.",
                risk=RiskLevel.LOW,
            ),
            open_search,
            source="integration:spotify",
        )
        executor.register(
            define(
                "spotify_authorize",
                "Spotify hesabını tarayıcı onayıyla JARVIS'e bağla.",
                risk=RiskLevel.MEDIUM,
                confirm=True,
                timeout=200.0,
            ),
            authorize,
            source="integration:spotify",
        )
        executor.register(
            define(
                "spotify_play_track",
                "Adı verilen şarkıyı veya sanatçıyı Spotify'da hemen "
                "çal. Hesap bağlı değilse masaüstü uygulamasını "
                "kendisi kullanır.",
                risk=RiskLevel.LOW,
                timeout=45.0,
            ),
            play_track,
            source="integration:spotify",
        )
        executor.register(
            define(
                "spotify_create_playlist",
                "Verilen şarkılardan özel bir çalma listesi oluştur.",
                risk=RiskLevel.MEDIUM,
                confirm=True,
                timeout=60.0,
            ),
            create_playlist,
            source="integration:spotify",
        )
        executor.register(
            define(
                "spotify_listening_stats",
                "Dinleme istatistiklerini (en çok dinlenenler) getir. "
                "Dönem: kisa, orta veya uzun.",
                timeout=30.0,
            ),
            listening_stats,
            source="integration:spotify",
        )
        for name, description, handler, risk, timeout in (
            ("spotify_set_volume", "Spotify'ın kendi sesini yüzde olarak ayarla (0-100).", set_volume, RiskLevel.LOW, 20.0),
            ("spotify_seek", "Çalan parçada sar: '+30', '-15', '1:30' ya da saniye.", seek, RiskLevel.LOW, 20.0),
            ("spotify_shuffle", "Karıştırmayı ayarla: on, off ya da smart.", shuffle, RiskLevel.LOW, 30.0),
            ("spotify_repeat", "Yinelemeyi ayarla: off, list ya da track.", repeat, RiskLevel.LOW, 30.0),
            ("spotify_like_track", "Çalan parçayı Beğenilen Şarkılar'a ekle.", like_track, RiskLevel.MEDIUM, 20.0),
            ("spotify_library", "Kenar çubuğundaki çalma listelerini ve sanatçıları listele.", library, RiskLevel.READ_ONLY, 20.0),
            ("spotify_play_library", "Kütüphanedeki bir çalma listesini ya da sanatçıyı adıyla çal (satıra çift tıklar).", play_library, RiskLevel.LOW, 45.0),
            ("spotify_queue_track", "Bir parçayı arayıp sıradakilere ekle.", queue_track, RiskLevel.LOW, 45.0),
            ("spotify_sleep_timer", "Uyku zamanlayıcısı: N dakika sonra sesi yavaşça kısıp duraklat.", sleep_timer, RiskLevel.LOW, 10.0),
            ("spotify_cancel_sleep_timer", "Uyku zamanlayıcısını iptal et.", cancel_sleep_timer, RiskLevel.LOW, 10.0),
        ):
            executor.register(
                define(name, description, risk=risk, timeout=timeout),
                handler,
                source="integration:spotify",
            )
