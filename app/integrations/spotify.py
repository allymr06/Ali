"""Spotify integration.

Two capability tiers:

LOCAL (works with the desktop app, no account setup):
    now-playing observation, play/pause/next/previous via global media
    keys, and opening searches. Every mutation is verified against the
    Spotify window title, which carries "Artist - Track" while playing.

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
    ) -> None:
        self._client_id = (client_id or "").strip() or None
        self._credentials = credential_store
        self._powershell = powershell or PowerShellRunner()
        self._media_keys = media_keys or MediaKeySender()
        self._uri = uri_launcher or UriLauncher()
        self._http_client_factory = http_client_factory
        self._access_token: str | None = None
        self._access_expires_at = 0.0

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
        message = (
            f"Çalıyor: {state['artist']} — {state['track']}"
            if state["playing"]
            else "Spotify açık ama şu an bir şey çalmıyor."
        )
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
            return self._configuration_error("spotify_play_track")
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
            return ToolResult(
                ToolExecutionStatus.BLOCKED,
                "spotify_play_track",
                message=(
                    "Aktif bir Spotify cihazı yok. Uygulamayı açıp "
                    "bir kez oynat, sonra tekrar dene."
                ),
                error="no_active_device",
            )
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
                "Adı verilen şarkıyı Spotify'da hemen çal "
                "(Web API, Premium gerektirir).",
                risk=RiskLevel.LOW,
                timeout=30.0,
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
