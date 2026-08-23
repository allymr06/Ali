"""Automatic long-term memory extraction.

The deterministic analyzer only fires on literal "hatırla ..."
prefixes, so in practice nothing was ever remembered. This extractor
runs after each completed turn, off the latency path: a cheap model
pass decides whether the user's message contains a durable personal
fact (identity, preference, decision, ongoing project) and stores at
most one short sentence. Failures are swallowed — remembering is a
bonus, never a reason for a turn to error.
"""

from __future__ import annotations

import asyncio
from typing import Any

from app.core.models import Context, Request, RequestSource
from app.memory.models import MemorySource, MemoryType

_NEGATIVE = "YOK"

_SYSTEM_PROMPT = (
    "Sen bir hafıza süzgecisin. Kullanıcının mesajında UZUN VADEDE "
    "hatırlanmaya değer kalıcı kişisel bilgi olup olmadığına karar "
    "verirsin: kimlik bilgisi, kalıcı tercih/beğeni, alınmış karar, "
    "süregelen proje veya durum. Anlık komutlar (şarkı çal, mesaj "
    "gönder, ekranı izle, soru sorma) ve geçici durumlar KALICI "
    "DEĞİLDİR. Şifre, kart numarası, kimlik numarası gibi hassas "
    "veriler ASLA kaydedilmez.\n"
    "Kalıcı bilgi varsa onu üçüncü şahıs TEK KISA cümleyle yaz "
    "(örnek: 'Kullanıcı rock müzik seviyor.'). Yoksa sadece YOK yaz. "
    "Başka hiçbir şey yazma."
)


class AutoMemoryExtractor:
    """Post-turn, model-assisted memory capture."""

    def __init__(
        self,
        *,
        provider_gateway: Any,
        memory_manager: Any,
        model: str,
        max_length: int = 240,
    ) -> None:
        self._gateway = provider_gateway
        self._memory = memory_manager
        self._model = model
        self._max_length = max_length

    @staticmethod
    def _word_set(text: str) -> set[str]:
        return {
            word.strip(".,!?;:'\"()")
            for word in text.casefold().split()
            if len(word) > 2
        }

    def _already_known(self, content: str) -> bool:
        normalized = " ".join(content.casefold().split())
        words = self._word_set(content)
        try:
            existing = self._memory.recall(content, limit=3)
        except Exception:
            return False
        for entry in existing:
            known = " ".join(str(entry.content).casefold().split())
            if not known:
                continue
            if known == normalized or known in normalized or (
                normalized in known
            ):
                return True
            # Paraphrases of the same fact share most of their words;
            # storing both would silt the store with duplicates.
            known_words = self._word_set(known)
            union = words | known_words
            if union and len(words & known_words) / len(union) >= 0.6:
                return True
        return False

    async def extract(self, user_text: str, request_id: str) -> bool:
        """Analyze one user message; True when a memory was stored."""
        text = user_text.strip()
        # Too short to carry a durable fact; skip the model round trip.
        if len(text) < 12:
            return False
        probe = Request(
            f"Kullanıcının mesajı:\n{text}",
            source=RequestSource.SYSTEM,
            metadata={"reasoning_task_type": "simple"},
        )
        response = await self._gateway.generate(
            probe,
            Context(),
            model=self._model,
            system_prompt=_SYSTEM_PROMPT,
        )
        answer = (getattr(response, "text", "") or "").strip()
        if not answer or answer.upper().startswith(_NEGATIVE):
            return False
        content = " ".join(answer.split())[: self._max_length]
        if self._already_known(content):
            return False
        self._memory.remember(
            content,
            memory_type=MemoryType.FACT,
            importance=0.6,
            confidence=0.6,
            source=MemorySource.INFERENCE,
            source_reference=f"auto:{request_id}",
            metadata={"reason": "auto_extracted"},
        )
        return True

    def extract_in_background(
        self, user_text: str, request_id: str
    ) -> asyncio.Task | None:
        """Fire-and-forget wrapper used from the engine's hot path."""
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return None

        async def run() -> None:
            try:
                await self.extract(user_text, request_id)
            except Exception:
                pass

        return loop.create_task(run())
