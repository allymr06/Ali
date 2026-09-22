"""The palette's Turkish dictionary: TDK's Güncel Türkçe Sözlük, live.

sozluk.gov.tr answers a keyless JSON query per headword. This service
asks it through the same URL policy and pinned transport as web
research - it can never talk to a private address, follow a redirect,
or exceed a byte budget - and reshapes one entry for the lookup card:
the headword, its origin, up to six senses with one example each, and
the compound words the entry names. Nothing is stored beyond a short
in-memory cache, nothing is rephrased, and a word the dictionary does
not know is reported in the dictionary's own words.
"""
from __future__ import annotations

import json
from threading import Lock
from time import monotonic
from typing import Any, Callable, Mapping
from urllib.parse import urlencode

from app.research.errors import FetchError
from app.research.fetcher import PinnedHTTPTransport, WebTransport
from app.research.url_policy import URLPolicy

ENDPOINT = "https://sozluk.gov.tr/gts"
CACHE_SECONDS = 1800.0
CACHE_ENTRIES = 128
MAX_WORD_LENGTH = 64
MAX_SENSES = 6
MAX_COMPOUNDS = 10

_TURKISH_CASE = str.maketrans({"I": "ı", "İ": "i"})


def _turkish_lower(word: str) -> str:
    """Lowercase the way the dictionary spells its headwords.

    Python's casefold turns İ into i plus a combining dot and I into i;
    both would miss the entry. The two letters are mapped first, so
    HEKİM asks for hekim and ISPARTA for ısparta.
    """
    return word.translate(_TURKISH_CASE).lower()


class DictionaryService:
    """One TDK lookup per word, cached briefly, failures named honestly."""

    def __init__(
        self,
        *,
        policy: URLPolicy | None = None,
        transport: WebTransport | None = None,
        timeout_seconds: float = 8.0,
        max_response_bytes: int = 200_000,
        user_agent: str = "JARVIS/0.1",
        clock: Callable[[], float] = monotonic,
    ) -> None:
        if timeout_seconds <= 0 or max_response_bytes <= 0:
            raise ValueError("Dictionary limits must be positive.")
        self._policy = policy or URLPolicy()
        self._transport = transport or PinnedHTTPTransport()
        self._timeout = timeout_seconds
        self._max_bytes = max_response_bytes
        self._user_agent = user_agent
        self._clock = clock
        self._lock = Lock()
        self._cache: dict[str, tuple[float, dict[str, Any]]] = {}

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

    # -------------------------------------------------------------- lookup
    def lookup(self, word: str) -> dict[str, Any]:
        cleaned = str(word or "").strip()
        if not cleaned:
            return {"ok": False, "reason": "Aranacak kelime boş."}
        if len(cleaned) > MAX_WORD_LENGTH:
            return {"ok": False, "reason": "Kelime bir sözlük maddesi için fazla uzun."}
        queried = _turkish_lower(cleaned)
        key = queried
        now = self._clock()
        with self._lock:
            entry = self._cache.get(key)
            if entry is not None and now - entry[0] < CACHE_SECONDS:
                return entry[1]
        value = self._lookup_uncached(queried)
        with self._lock:
            # A miss and a dead service are remembered too, briefly: typing
            # in the palette must not knock on TDK once per keystroke.
            self._cache[key] = (now, value)
            while len(self._cache) > CACHE_ENTRIES:
                oldest = min(self._cache, key=lambda item: self._cache[item][0])
                del self._cache[oldest]
        return value

    def _lookup_uncached(self, word: str) -> dict[str, Any]:
        try:
            payload = self._json(f"{ENDPOINT}?{urlencode({'ara': word})}")
        except Exception as exc:  # noqa: BLE001 - every failure becomes a reason
            named = exc if isinstance(exc, FetchError) else type(exc).__name__
            return {"ok": False, "reason": f"Sözlük servisi yanıt vermedi ({named})."}
        if isinstance(payload, Mapping):
            # The service reports a miss as {"error": "Sonuç bulunamadı"}.
            return {"ok": False, "reason": f"Sözlükte bulunamadı: {word}."}
        if not isinstance(payload, list) or not payload or not isinstance(payload[0], Mapping):
            return {"ok": False, "reason": "Sözlük servisi beklenmedik veri döndürdü."}
        entry = payload[0]
        meanings: list[dict[str, str]] = []
        for meaning in entry.get("anlamlarListe") or []:
            if not isinstance(meaning, Mapping):
                continue
            sense = str(meaning.get("anlam") or "").strip()
            if not sense:
                continue
            features = ", ".join(
                str(feature.get("tam_adi") or "").strip()
                for feature in meaning.get("ozelliklerListe") or []
                if isinstance(feature, Mapping) and str(feature.get("tam_adi") or "").strip()
            )
            examples = meaning.get("orneklerListe") or []
            example = ""
            if examples and isinstance(examples[0], Mapping):
                example = str(examples[0].get("ornek") or "").strip()
            meanings.append({"features": features, "sense": sense, "example": example})
            if len(meanings) >= MAX_SENSES:
                break
        if not meanings:
            return {"ok": False, "reason": f"Sözlükte bulunamadı: {word}."}
        compounds_raw = entry.get("birlesikler")
        if isinstance(compounds_raw, str):
            compounds = [piece.strip() for piece in compounds_raw.split(",") if piece.strip()]
        elif isinstance(compounds_raw, list):
            compounds = [str(piece).strip() for piece in compounds_raw if str(piece).strip()]
        else:
            compounds = []
        return {
            "ok": True,
            "word": str(entry.get("madde") or word),
            "origin": str(entry.get("lisan") or "").strip(),
            "meanings": meanings,
            "compounds": compounds[:MAX_COMPOUNDS],
        }
