"""The palette's dictionary asks TDK once per word, reshapes honestly, and
names every failure."""
from __future__ import annotations

import json

from app.integrations.dictionary import CACHE_ENTRIES, DictionaryService, _turkish_lower
from app.research.fetcher import TransportResponse
from app.research.url_policy import URLPolicy


def policy() -> URLPolicy:
    return URLPolicy(resolver=lambda _host, _port: ("93.184.216.34",))


class CannedTransport:
    """One endpoint, answers keyed by the ``ara=`` word in the URL."""

    def __init__(self, answers: dict[str, object]) -> None:
        self.answers = answers
        self.urls: list[str] = []

    def request(self, target, *, address, timeout_seconds, max_bytes, user_agent, accept=None):
        self.urls.append(target.url)
        for key, answer in self.answers.items():
            if "ara=" + key in target.url:
                if isinstance(answer, Exception):
                    raise answer
                if isinstance(answer, TransportResponse):
                    return answer
                return TransportResponse(200, {"content-type": "application/json"}, json.dumps(answer).encode("utf-8"))
        return TransportResponse(200, {}, json.dumps({"error": "Sonuç bulunamadı"}).encode("utf-8"))


# Trimmed from a live sozluk.gov.tr answer for "kalp" (2026-09-20): the real
# shape - a list of entries, senses under anlamlarListe, features and
# examples nested one level deeper, compounds as one comma-joined string.
KALP = [{
    "madde": "kalp",
    "lisan": "Arapça ḳalb",
    "anlamlarListe": [
        {
            "anlam": "Göğüs orta boşluğunda, vücudun her yanından gelen kanı akciğerlere yollayan organ",
            "ozelliklerListe": [{"tam_adi": "isim"}, {"tam_adi": "anatomi"}],
            "orneklerListe": [{"ornek": "Bak kalbime nasıl çarpıyor."}],
        },
        {"anlam": "► kalp hastalığı", "orneklerListe": [{"ornek": "Kalpten öldü."}]},
        {"anlam": "► gönül", "ozelliklerListe": [{"tam_adi": "mecaz"}]},
        {"anlam": ""},
    ],
    "birlesikler": "kalp ağrısı, kalp atışı, kalp bloku",
}]


def service(answers: dict[str, object], clock=None) -> tuple[DictionaryService, CannedTransport]:
    transport = CannedTransport(answers)
    kwargs = {"policy": policy(), "transport": transport, "user_agent": "JARVIS/test"}
    if clock is not None:
        kwargs["clock"] = clock
    return DictionaryService(**kwargs), transport


def test_one_entry_is_reshaped_with_nothing_invented() -> None:
    dictionary, transport = service({"kalp": KALP})

    entry = dictionary.lookup("kalp")

    assert entry["ok"] is True and entry["word"] == "kalp"
    assert entry["origin"] == "Arapça ḳalb"
    first = entry["meanings"][0]
    assert first["features"] == "isim, anatomi"
    assert first["sense"].startswith("Göğüs orta boşluğunda")
    assert first["example"] == "Bak kalbime nasıl çarpıyor."
    # TDK's own cross-reference arrows pass through untouched; the empty
    # fourth sense is dropped, not padded.
    assert entry["meanings"][1] == {"features": "", "sense": "► kalp hastalığı", "example": "Kalpten öldü."}
    assert entry["meanings"][2]["features"] == "mecaz"
    assert len(entry["meanings"]) == 3
    assert entry["compounds"] == ["kalp ağrısı", "kalp atışı", "kalp bloku"]
    assert transport.urls == ["https://sozluk.gov.tr/gts?ara=kalp"]


def test_uppercase_turkish_finds_the_same_entry_from_the_cache() -> None:
    # Python's casefold maps İ to i plus a combining dot; the Turkish fold
    # must map İ→i and I→ı or HEKİM and ISPARTA would both miss.
    assert _turkish_lower("HEKİM") == "hekim"
    assert _turkish_lower("ISPARTA") == "ısparta"
    dictionary, transport = service({"kalp": KALP})

    assert dictionary.lookup("KALP")["ok"] is True
    assert dictionary.lookup("kalp")["ok"] is True
    assert dictionary.lookup("Kalp")["ok"] is True
    assert len(transport.urls) == 1, "one live query serves every casing"


def test_a_miss_a_dead_service_and_junk_all_answer_with_reasons() -> None:
    dictionary, transport = service({
        "yokkelime": {"error": "Sonuç bulunamadı"},
        "kapali": OSError("connection refused"),
        "bozuk": ["not-a-mapping"],
        "bes": TransportResponse(500, {}, b""),
    })

    assert dictionary.lookup("yokkelime") == {"ok": False, "reason": "Sözlükte bulunamadı: yokkelime."}
    assert dictionary.lookup("kapali") == {"ok": False, "reason": "Sözlük servisi yanıt vermedi (OSError)."}
    assert dictionary.lookup("bozuk") == {"ok": False, "reason": "Sözlük servisi beklenmedik veri döndürdü."}
    assert dictionary.lookup("bes") == {"ok": False, "reason": "Sözlük servisi yanıt vermedi (HTTP 500)."}
    # The failure is cached too: the palette must not knock per keystroke.
    assert dictionary.lookup("kapali")["ok"] is False
    assert len([url for url in transport.urls if "kapali" in url]) == 1


def test_guards_answer_before_any_network_is_touched() -> None:
    dictionary, transport = service({})

    assert dictionary.lookup("") == {"ok": False, "reason": "Aranacak kelime boş."}
    assert dictionary.lookup("   ") == {"ok": False, "reason": "Aranacak kelime boş."}
    assert dictionary.lookup("a" * 65) == {"ok": False, "reason": "Kelime bir sözlük maddesi için fazla uzun."}
    assert transport.urls == []


def test_the_cache_is_capped_and_drops_its_oldest_word() -> None:
    beat = {"now": 0.0}

    def clock() -> float:
        beat["now"] += 1.0
        return beat["now"]

    dictionary, transport = service({"kelime": {"error": "Sonuç bulunamadı"}}, clock=clock)
    for index in range(CACHE_ENTRIES + 10):
        dictionary.lookup(f"kelime{index}")

    assert len(dictionary._cache) <= CACHE_ENTRIES
    assert "kelime0" not in dictionary._cache and f"kelime{CACHE_ENTRIES + 9}" in dictionary._cache
