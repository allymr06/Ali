"""What makes a delegated WhatsApp conversation read like a person.

Pure functions and small value objects, no I/O: the style the user
actually writes in (learned from their own bubbles in the open chat),
whether a fresh message deserves a reply at all, when a person would
simply be asleep, how long they would take to read and to type, and how
a reply splits into the short bubbles people really send. The agent
composes these; nothing here talks to the model or the window, and
nothing here invents facts - a style with too few samples says so.
"""
from __future__ import annotations

import random
import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, time as dtime
from typing import Callable, Iterable

MIN_STYLE_SAMPLES = 3
MAX_BUBBLES = 3
MAX_BUBBLE_CHARACTERS = 200

# Replies a person would leave unanswered: the conversation closed on a
# nod. Question marks and anything longer than a nod get an answer.
_ACK_WORDS = frozenset({
    "ok", "okey", "okay", "oke", "tamam", "tmm", "tm", "tamamdır", "peki", "olur",
    "eyv", "eyvallah", "eyw", "sağol", "saol", "sağ", "ol", "teşekkürler", "tşk", "tsk",
    "haha", "hahaha", "hahah", "hah", "he", "hee", "hmm", "hm", "evet", "aynen", "aynn",
    "iyi", "güzel", "süper", "tamam.", "ok.", "np", "yok", "yo", "görüşürüz", "gorusuruz",
    "bb", "bye", "iyi geceler", "iyi", "geceler", "iyiki", "anladım", "anladim",
})
# Ways of addressing someone that carry no content: "eyvallah kanka" is
# still a nod.
_FILLERS = frozenset({
    "kanka", "kanki", "abi", "abla", "aga", "canım", "reis", "hocam", "dostum", "bro",
    "moruk", "kardeşim", "ya", "yaa", "be", "lan", "canim", "kardesim",
})
_EMOJI = re.compile(
    "[\U0001F300-\U0001FAFF☀-➿\U0001F1E6-\U0001F1FF⭐⬆↔-↪❤️]+"
)
_WORD = re.compile(r"[^\W\d_]+", re.UNICODE)
_SENTENCE_END = re.compile(r"(?<=[.!?…])\s+")


def is_acknowledgement(text: str) -> bool:
    """A nod, an emoji, or a laugh - something a person leaves be."""
    stripped = _EMOJI.sub(" ", text).strip()
    if not stripped:
        return bool(text.strip())  # emoji only
    if "?" in stripped:
        return False
    tokens = [token.strip(".,!;:'\"()").casefold() for token in stripped.split()]
    tokens = [token for token in tokens if token and token not in _FILLERS]
    if not tokens or len(tokens) > 3:
        return False
    return all(token in _ACK_WORDS for token in tokens)


# ----------------------------------------------------------------- style


@dataclass(frozen=True, slots=True)
class StyleProfile:
    """How the user writes, measured on their own recent bubbles."""

    samples: int = 0
    average_words: float = 0.0
    lowercase_starts: float = 0.0
    trailing_punctuation: float = 0.0
    emoji_share: float = 0.0
    favourite_words: tuple[str, ...] = ()

    @classmethod
    def from_messages(cls, messages: Iterable[str]) -> "StyleProfile":
        texts = [" ".join(str(item).split()) for item in messages]
        texts = [text for text in texts if text]
        if not texts:
            return cls()
        words = [len(text.split()) for text in texts]
        lowercase = sum(1 for text in texts if text[0].isalpha() and text[0].islower())
        punctuated = sum(1 for text in texts if text[-1] in ".!?")
        with_emoji = sum(1 for text in texts if _EMOJI.search(text))
        counter: Counter[str] = Counter()
        for text in texts:
            for word in _WORD.findall(text.casefold()):
                if len(word) >= 3 and word not in _STOPWORDS:
                    counter[word] += 1
        favourites = tuple(word for word, count in counter.most_common(5) if count >= 2)
        return cls(
            samples=len(texts),
            average_words=round(sum(words) / len(words), 1),
            lowercase_starts=round(lowercase / len(texts), 2),
            trailing_punctuation=round(punctuated / len(texts), 2),
            emoji_share=round(with_emoji / len(texts), 2),
            favourite_words=favourites,
        )

    @property
    def reliable(self) -> bool:
        return self.samples >= MIN_STYLE_SAMPLES

    def describe(self) -> str:
        """Prompt lines in Turkish; honest about thin evidence."""
        if not self.reliable:
            return (
                f"Kullanıcının üslubu için yeterli örnek yok ({self.samples} mesaj); "
                "doğal, samimi ve kısa yaz."
            )
        lines = [f"Kullanıcının kendi mesajlarından ölçülen üslup ({self.samples} örnek):"]
        lines.append(
            "- Cümleye " + ("küçük harfle başlar" if self.lowercase_starts >= 0.6 else "büyük harfle başlar")
            + (", sona noktalama koymaz" if self.trailing_punctuation <= 0.3 else ", cümleyi noktalamayla bitirir")
            + "."
        )
        if self.average_words <= 4:
            length = "çok kısa (birkaç kelime)"
        elif self.average_words <= 9:
            length = "kısa (bir cümle)"
        else:
            length = "orta uzunlukta (birkaç cümle)"
        lines.append(f"- Mesajları genelde {length}; ortalama {self.average_words:g} kelime.")
        if self.emoji_share >= 0.5:
            lines.append("- Sık emoji kullanır.")
        elif self.emoji_share >= 0.2:
            lines.append("- Ara sıra emoji kullanır.")
        else:
            lines.append("- Emoji kullanmaz.")
        if self.favourite_words:
            lines.append("- Sık kullandığı sözcükler: " + ", ".join(self.favourite_words) + ".")
        return "\n".join(lines)


_STOPWORDS = frozenset({
    "bir", "ben", "sen", "biz", "siz", "ama", "için", "ile", "gibi", "çok", "daha", "var", "yok",
    "bu", "şu", "ne", "mi", "mı", "mu", "mü", "de", "da", "ki", "ve", "veya", "olan", "sonra",
    "the", "and", "you", "are", "for", "that", "this", "with", "not", "but",
})


# ---------------------------------------------------------------- pacing


@dataclass(frozen=True, slots=True)
class QuietHours:
    """When a person is asleep; the agent holds replies instead of sending."""

    start: dtime = dtime(0, 30)
    end: dtime = dtime(8, 0)

    def contains(self, moment: datetime) -> bool:
        now = moment.time()
        if self.start <= self.end:
            return self.start <= now < self.end
        return now >= self.start or now < self.end


@dataclass(slots=True)
class Pacing:
    """Human-like delays; a seeded RNG makes them reproducible in tests."""

    enabled: bool = True
    rng: random.Random = field(default_factory=random.Random)
    seconds_per_char: tuple[float, float] = (0.04, 0.07)
    max_typing_seconds: float = 25.0
    max_read_seconds: float = 12.0

    @classmethod
    def instant(cls) -> "Pacing":
        return cls(enabled=False)

    def read_delay(self, incoming_characters: int) -> float:
        """Time to notice and read what arrived: a beat plus reading speed."""
        if not self.enabled:
            return 0.0
        base = 1.5 + 0.03 * max(0, incoming_characters)
        return round(min(self.max_read_seconds, base * self.rng.uniform(0.8, 1.3)), 2)

    def typing_rate(self, reply_characters: int) -> float:
        """Seconds per character, capped so a long reply still lands in time."""
        if not self.enabled:
            return 0.0
        low, high = self.seconds_per_char
        rate = self.rng.uniform(low, high)
        if reply_characters > 0:
            rate = min(rate, self.max_typing_seconds / reply_characters)
        return round(rate, 4)

    def between_bubbles(self) -> float:
        if not self.enabled:
            return 0.0
        return round(self.rng.uniform(0.8, 2.0), 2)


# ---------------------------------------------------------------- shaping


def split_bubbles(draft: str, *, limit: int = MAX_BUBBLES) -> list[str]:
    """The bubbles a person would send for this reply.

    The model is asked to write one bubble per line; lines are the split.
    A single long paragraph is split at sentence ends only when every
    piece stays short. Never more than ``limit`` bubbles - the rest is
    folded into the last one rather than dropped.
    """
    lines = [" ".join(line.split()) for line in draft.splitlines()]
    lines = [line for line in lines if line]
    if not lines:
        return []
    if len(lines) == 1 and len(lines[0]) > MAX_BUBBLE_CHARACTERS // 2:
        sentences = [piece.strip() for piece in _SENTENCE_END.split(lines[0]) if piece.strip()]
        if len(sentences) >= 2 and all(len(piece) <= MAX_BUBBLE_CHARACTERS for piece in sentences):
            lines = sentences
    if len(lines) > limit:
        lines = lines[: limit - 1] + [" ".join(lines[limit - 1 :])]
    return lines


@dataclass(frozen=True, slots=True)
class ReplyDecision:
    reply: bool
    reason: str


def decide_reply(
    incoming: Iterable[str],
    *,
    now: datetime,
    quiet: QuietHours | None = None,
) -> ReplyDecision:
    """Whether a person would answer this batch right now, and why not."""
    texts = [" ".join(str(item).split()) for item in incoming]
    texts = [text for text in texts if text]
    if not texts:
        return ReplyDecision(False, "nothing_new")
    if quiet is not None and quiet.contains(now):
        return ReplyDecision(False, "quiet_hours")
    if all(is_acknowledgement(text) for text in texts):
        return ReplyDecision(False, "acknowledged")
    return ReplyDecision(True, "fresh_message")


Clock = Callable[[], datetime]
