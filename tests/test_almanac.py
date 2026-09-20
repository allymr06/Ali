"""The almanac answers from its two services, caches briefly, and never invents."""
from __future__ import annotations

import json

import pytest

from app.integrations.almanac import CACHE_SECONDS, AlmanacService
from app.research.fetcher import TransportResponse
from app.research.url_policy import URLPolicy


def policy() -> URLPolicy:
    return URLPolicy(resolver=lambda _host, _port: ("93.184.216.34",))


class CannedTransport:
    def __init__(self, answers: dict[str, object]) -> None:
        self.answers = answers
        self.urls: list[str] = []

    def request(self, target, *, address, timeout_seconds, max_bytes, user_agent, accept=None):
        self.urls.append(target.url)
        for key, answer in self.answers.items():
            if key in target.url:
                if isinstance(answer, Exception):
                    raise answer
                if isinstance(answer, TransportResponse):
                    return answer
                return TransportResponse(200, {"content-type": "application/json"}, json.dumps(answer).encode("utf-8"))
        return TransportResponse(404, {}, b"")


GEOCODE = {"results": [{"name": "İstanbul", "latitude": 41.01, "longitude": 28.95, "country_code": "TR"}]}
FORECAST = {
    "current": {"temperature_2m": 21.4, "apparent_temperature": 19.8, "weather_code": 2},
    "daily": {"temperature_2m_max": [24.3], "temperature_2m_min": [17.6]},
}
RATES = {"base": "TRY", "date": "2026-09-19", "rates": {"USD": 0.024272, "EUR": 0.022321}}


def service(answers: dict[str, object], *, city: str = "istanbul", clock=None) -> tuple[AlmanacService, CannedTransport]:
    transport = CannedTransport(answers)
    kwargs = {"city": city, "policy": policy(), "transport": transport, "user_agent": "JARVIS/test"}
    if clock is not None:
        kwargs["clock"] = clock
    return AlmanacService(**kwargs), transport


def test_the_weather_names_the_city_the_sky_and_the_range() -> None:
    almanac, transport = service({"geocoding-api.open-meteo.com": GEOCODE, "api.open-meteo.com": FORECAST})

    weather = almanac.weather()

    assert weather == {
        "available": True, "city": "İstanbul", "temperature": 21, "feels_like": 20,
        "label": "parçalı bulutlu", "high": 24, "low": 18,
    }
    assert "language=tr" in transport.urls[0]
    assert "latitude=41.0100" in transport.urls[1] and "forecast_days=1" in transport.urls[1]


def test_the_rates_say_what_one_dollar_and_one_euro_cost_in_lira() -> None:
    almanac, transport = service({"api.frankfurter.dev": RATES})

    rates = almanac.rates()

    assert rates["available"] is True
    assert rates["usd_try"] == pytest.approx(41.2, abs=0.01)
    assert rates["eur_try"] == pytest.approx(44.8, abs=0.01)
    assert rates["date"] == "2026-09-19"
    assert "base=TRY" in transport.urls[0] and "symbols=USD%2CEUR" in transport.urls[0]


def test_each_half_fails_alone_and_says_why_in_turkish() -> None:
    almanac, _ = service({
        "geocoding-api.open-meteo.com": GEOCODE,
        "api.open-meteo.com": TransportResponse(503, {}, b""),
        "api.frankfurter.dev": RATES,
    })

    snapshot = almanac.snapshot()

    assert snapshot["weather"] == {"available": False, "reason": "Hava servisi yanıt vermedi (HTTP 503)."}
    assert snapshot["rates"]["available"] is True

    unfound, _ = service({"geocoding-api.open-meteo.com": {"results": []}})
    assert unfound.weather() == {"available": False, "reason": "Şehir bulunamadı: istanbul."}

    cityless, transport = service({}, city="")
    assert cityless.weather() == {"available": False, "reason": "Şehir ayarlanmadı."}
    assert transport.urls == [], "no city, no network"


def test_answers_and_failures_are_cached_for_half_an_hour() -> None:
    now = [0.0]
    almanac, transport = service(
        {"geocoding-api.open-meteo.com": GEOCODE, "api.open-meteo.com": FORECAST, "api.frankfurter.dev": RATES},
        clock=lambda: now[0],
    )

    first = almanac.snapshot()
    again = almanac.snapshot()
    assert first == again
    assert len(transport.urls) == 3, "geocode + forecast + rates, once"

    now[0] = CACHE_SECONDS + 1
    almanac.snapshot()
    assert len(transport.urls) == 5, "the cache expired; the city is still geocoded once"

    broken, broken_transport = service({"api.frankfurter.dev": TransportResponse(500, {}, b"")}, city="")
    broken.rates()
    broken.rates()
    assert len(broken_transport.urls) == 1, "a dead service is not knocked on every glance"


def test_missing_fields_are_an_honest_refusal_not_a_zero() -> None:
    almanac, _ = service({
        "geocoding-api.open-meteo.com": GEOCODE,
        "api.open-meteo.com": {"current": {"weather_code": 2}},
        "api.frankfurter.dev": {"rates": {"USD": 0.0}},
    })

    snapshot = almanac.snapshot()

    assert snapshot["weather"] == {"available": False, "reason": "Hava servisi eksik veri döndürdü."}
    assert snapshot["rates"] == {"available": False, "reason": "Kur servisi eksik veri döndürdü."}


def test_an_unknown_weather_code_is_shown_as_nothing() -> None:
    forecast = {"current": {"temperature_2m": 10.0, "weather_code": 42}, "daily": {}}
    almanac, _ = service({"geocoding-api.open-meteo.com": GEOCODE, "api.open-meteo.com": forecast})

    weather = almanac.weather()

    assert weather["available"] is True and weather["label"] == ""
    assert weather["feels_like"] is None and weather["high"] is None and weather["low"] is None
