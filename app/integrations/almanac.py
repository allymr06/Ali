"""The morning almanac: today's weather and the lira's exchange rates.

Two keyless public services feed the daily brief - Open-Meteo for the
weather of the one city the user named, and Frankfurter (the ECB's
reference rates) for what a dollar and a euro cost in lira. Both are
reached through the same URL policy and pinned transport as web
research, so this module can never talk to a private address, follow a
redirect, or exceed a byte budget. Each half reports independently and
honestly: no city means "no city", a service that does not answer is
named, and nothing is estimated.

Answers are cached in memory for half an hour: the brief is glanced at,
not watched, and neither reference changes faster than that.
"""
from __future__ import annotations

import json
from threading import Lock
from time import monotonic
from typing import Any, Callable, Mapping
from urllib.parse import urlencode

from app.research.errors import ContentRejectedError, FetchError, UnsafeURLError
from app.research.fetcher import PinnedHTTPTransport, WebTransport
from app.research.url_policy import URLPolicy

CACHE_SECONDS = 1800.0
GEOCODING_ENDPOINT = "https://geocoding-api.open-meteo.com/v1/search"
FORECAST_ENDPOINT = "https://api.open-meteo.com/v1/forecast"
RATES_ENDPOINT = "https://api.frankfurter.dev/v1/latest"

# WMO weather interpretation codes, in the words a Turkish forecast uses.
# An unknown code is shown as nothing rather than guessed.
WEATHER_CODES_TR: dict[int, str] = {
    0: "açık",
    1: "az bulutlu",
    2: "parçalı bulutlu",
    3: "çok bulutlu",
    45: "sisli",
    48: "kırağılı sis",
    51: "hafif çisenti",
    53: "çisenti",
    55: "yoğun çisenti",
    61: "hafif yağmurlu",
    63: "yağmurlu",
    65: "kuvvetli yağmurlu",
    66: "dondurucu yağmur",
    67: "kuvvetli dondurucu yağmur",
    71: "hafif kar",
    73: "karlı",
    75: "yoğun kar",
    77: "kar taneleri",
    80: "sağanak",
    81: "sağanak yağmur",
    82: "şiddetli sağanak",
    85: "kar sağanağı",
    86: "yoğun kar sağanağı",
    95: "gök gürültülü fırtına",
    96: "dolu ile fırtına",
    99: "şiddetli dolu fırtınası",
}


def _clock(values: Any) -> str | None:
    """The HH:MM tail of the first ISO stamp in a daily list, or None."""
    if not isinstance(values, list) or not values:
        return None
    text = str(values[0] or "")
    tail = text.split("T")[-1][:5]
    if len(tail) == 5 and tail[2] == ":" and tail.replace(":", "").isdigit():
        return tail
    return None


def _clock(values: Any) -> str | None:
    """The HH:MM tail of the first ISO stamp in a daily list, or None."""
    if not isinstance(values, list) or not values:
        return None
    text = str(values[0] or "")
    tail = text.split("T")[-1][:5]
    if len(tail) == 5 and tail[2] == ":" and tail.replace(":", "").isdigit():
        return tail
    return None


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


class AlmanacService:
    """Weather for one named city and TRY reference rates, cached briefly."""

    def __init__(
        self,
        *,
        city: str = "",
        policy: URLPolicy | None = None,
        transport: WebTransport | None = None,
        timeout_seconds: float = 8.0,
        max_response_bytes: int = 200_000,
        user_agent: str = "JARVIS/0.1",
        clock: Callable[[], float] = monotonic,
    ) -> None:
        if timeout_seconds <= 0 or max_response_bytes <= 0:
            raise ValueError("Almanac limits must be positive.")
        self.city = str(city or "").strip()
        self._policy = policy or URLPolicy()
        self._transport = transport or PinnedHTTPTransport()
        self._timeout = timeout_seconds
        self._max_bytes = max_response_bytes
        self._user_agent = user_agent
        self._clock = clock
        self._lock = Lock()
        self._cache: dict[str, tuple[float, dict[str, Any]]] = {}
        self._geocoded: dict[str, dict[str, Any]] = {}

    # ------------------------------------------------------------ plumbing
    def _json(self, url: str) -> Any:
        target = self._policy.validate(url)
        response = self._transport.request(
            target,
            address=target.addresses[0],
            timeout_seconds=self._timeout,
            max_bytes=self._max_bytes,
            user_agent=self._user_agent,
            accept="application/json",
        )
        if response.status != 200:
            raise FetchError(f"HTTP {response.status}")
        return json.loads(response.body.decode("utf-8", "replace"))

    def _cached(self, key: str, build: Callable[[], dict[str, Any]]) -> dict[str, Any]:
        with self._lock:
            entry = self._cache.get(key)
            if entry is not None and self._clock() - entry[0] < CACHE_SECONDS:
                return entry[1]
        value = build()
        with self._lock:
            # A failure is remembered too: a dead service is not knocked on
            # every glance at the home screen.
            self._cache[key] = (self._clock(), value)
        return value

    # ------------------------------------------------------------- weather
    def _geocode(self, city: str) -> dict[str, Any] | None:
        cached = self._geocoded.get(city.casefold())
        if cached is not None:
            return cached
        params = {"name": city, "count": "1", "language": "tr", "format": "json"}
        payload = self._json(f"{GEOCODING_ENDPOINT}?{urlencode(params)}")
        results = payload.get("results") if isinstance(payload, Mapping) else None
        if not results or not isinstance(results[0], Mapping):
            return None
        place = results[0]
        latitude = _number(place.get("latitude"))
        longitude = _number(place.get("longitude"))
        if latitude is None or longitude is None:
            return None
        found = {
            "name": str(place.get("name") or city),
            "latitude": latitude,
            "longitude": longitude,
        }
        self._geocoded[city.casefold()] = found
        return found

    def weather(self) -> dict[str, Any]:
        if not self.city:
            return {"available": False, "reason": "Şehir ayarlanmadı."}
        return self._cached(f"weather:{self.city.casefold()}", self._weather_uncached)

    def _weather_uncached(self) -> dict[str, Any]:
        try:
            place = self._geocode(self.city)
            if place is None:
                return {"available": False, "reason": f"Şehir bulunamadı: {self.city}."}
            params = {
                "latitude": f"{place['latitude']:.4f}",
                "longitude": f"{place['longitude']:.4f}",
                "current": "temperature_2m,apparent_temperature,weather_code",
                "daily": "temperature_2m_max,temperature_2m_min,sunrise,sunset",
                "timezone": "auto",
                "forecast_days": "1",
            }
            payload = self._json(f"{FORECAST_ENDPOINT}?{urlencode(params)}")
        except (FetchError, ContentRejectedError, UnsafeURLError, OSError, TimeoutError, json.JSONDecodeError) as exc:
            return {"available": False, "reason": f"Hava servisi yanıt vermedi ({exc if isinstance(exc, FetchError) else type(exc).__name__})."}
        current = payload.get("current") if isinstance(payload, Mapping) else None
        daily = payload.get("daily") if isinstance(payload, Mapping) else None
        temperature = _number(current.get("temperature_2m")) if isinstance(current, Mapping) else None
        if temperature is None:
            return {"available": False, "reason": "Hava servisi eksik veri döndürdü."}
        code = current.get("weather_code") if isinstance(current, Mapping) else None
        highs = daily.get("temperature_2m_max") if isinstance(daily, Mapping) else None
        lows = daily.get("temperature_2m_min") if isinstance(daily, Mapping) else None
        return {
            "available": True,
            "city": place["name"],
            "temperature": round(temperature),
            "feels_like": (
                round(_number(current.get("apparent_temperature")))
                if isinstance(current, Mapping) and _number(current.get("apparent_temperature")) is not None
                else None
            ),
            "label": WEATHER_CODES_TR.get(int(code), "") if isinstance(code, int) and not isinstance(code, bool) else "",
            "high": round(_number(highs[0])) if isinstance(highs, list) and highs and _number(highs[0]) is not None else None,
            "low": round(_number(lows[0])) if isinstance(lows, list) and lows and _number(lows[0]) is not None else None,
            # The day's frame, from the same daily answer; absent stays None.
            "sunrise": _clock(daily.get("sunrise") if isinstance(daily, Mapping) else None),
            "sunset": _clock(daily.get("sunset") if isinstance(daily, Mapping) else None),
        }

    # --------------------------------------------------------------- rates
    def rates(self) -> dict[str, Any]:
        return self._cached("rates", self._rates_uncached)

    def _rates_uncached(self) -> dict[str, Any]:
        try:
            params = {"base": "TRY", "symbols": "USD,EUR"}
            payload = self._json(f"{RATES_ENDPOINT}?{urlencode(params)}")
        except (FetchError, ContentRejectedError, UnsafeURLError, OSError, TimeoutError, json.JSONDecodeError) as exc:
            return {"available": False, "reason": f"Kur servisi yanıt vermedi ({exc if isinstance(exc, FetchError) else type(exc).__name__})."}
        table = payload.get("rates") if isinstance(payload, Mapping) else None
        usd = _number(table.get("USD")) if isinstance(table, Mapping) else None
        eur = _number(table.get("EUR")) if isinstance(table, Mapping) else None
        if not usd or not eur:
            return {"available": False, "reason": "Kur servisi eksik veri döndürdü."}
        # Frankfurter answers how much foreign currency one lira buys; the
        # brief says it the way the street does - one dollar in lira.
        return {
            "available": True,
            "usd_try": round(1.0 / usd, 2),
            "eur_try": round(1.0 / eur, 2),
            "date": str(payload.get("date") or ""),
        }

    # ------------------------------------------------------------ together
    def snapshot(self) -> dict[str, Any]:
        return {"weather": self.weather(), "rates": self.rates()}
