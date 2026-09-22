"""The human layer of a delegated WhatsApp chat is pure and honest."""
from __future__ import annotations

import random
from datetime import datetime, time as dtime

from app.integrations.whatsapp_human import (
    Pacing,
    QuietHours,
    StyleProfile,
    decide_reply,
    is_acknowledgement,
    split_bubbles,
)

NOON = datetime(2026, 9, 21, 12, 0)
NIGHT = datetime(2026, 9, 21, 2, 0)


def test_a_nod_is_left_alone_but_a_question_is_answered() -> None:
    for nod in ("ok", "tamam", "Tamam.", "tamam \U0001F44D", "hahaha", "\U0001F44D", "eyvallah kanka", "evet"):
        assert is_acknowledgement(nod), nod
    for real in ("naber?", "yar\u0131n gelecek misin", "tamam ama saat ka\u00e7ta?", "bug\u00fcn dersten sonra kahve i\u00e7elim", ""):
        assert not is_acknowledgement(real), real


def test_style_is_measured_on_the_users_own_bubbles_and_says_when_it_is_thin() -> None:
    thin = StyleProfile.from_messages(["selam", "naber"])
    assert thin.reliable is False
    assert "yeterli \u00f6rnek yok (2 mesaj)" in thin.describe()

    profile = StyleProfile.from_messages([
        "kanka naber", "iyiyim kanka sen", "gelirim akşam", "tamam kanka", "yarın görüşürüz",
    ])
    assert profile.reliable and profile.samples == 5
    assert profile.lowercase_starts == 1.0 and profile.trailing_punctuation == 0.0
    assert profile.average_words <= 4 and profile.emoji_share == 0.0
    assert "kanka" in profile.favourite_words
    text = profile.describe()
    assert "k\u00fc\u00e7\u00fck harfle ba\u015flar" in text and "noktalama koymaz" in text
    assert "Emoji kullanmaz" in text and "kanka" in text

    formal = StyleProfile.from_messages(["Merhaba, nas\u0131ls\u0131n\u0131z?", "Yar\u0131n g\u00f6r\u00fc\u015fmek \u00fczere.", "Te\u015fekk\u00fcr ederim. \U0001F60A"])
    described = formal.describe()
    assert "b\u00fcy\u00fck harfle ba\u015flar" in described and "noktalamayla bitirir" in described
    assert "Ara s\u0131ra emoji" in described


def test_quiet_hours_wrap_midnight() -> None:
    quiet = QuietHours(dtime(0, 30), dtime(8, 0))
    assert quiet.contains(NIGHT) and not quiet.contains(NOON)
    assert quiet.contains(datetime(2026, 9, 21, 0, 30)) and not quiet.contains(datetime(2026, 9, 21, 8, 0))
    daytime = QuietHours(dtime(13, 0), dtime(14, 0))
    assert daytime.contains(datetime(2026, 9, 21, 13, 30)) and not daytime.contains(NIGHT)


def test_pacing_is_human_bounded_and_reproducible() -> None:
    pacing = Pacing(rng=random.Random(7))
    assert 1.2 <= pacing.read_delay(0) <= 2.0
    assert pacing.read_delay(100_000) == 12.0, "reading time is capped"
    rate = pacing.typing_rate(20)
    assert 0.04 <= rate <= 0.07
    assert pacing.typing_rate(10_000) <= 0.0025, "a long reply still lands within 25 s"
    assert 0.8 <= pacing.between_bubbles() <= 2.0
    again = Pacing(rng=random.Random(7))
    assert again.read_delay(0) == Pacing(rng=random.Random(7)).read_delay(0)
    instant = Pacing.instant()
    assert instant.read_delay(500) == 0.0 and instant.typing_rate(50) == 0.0 and instant.between_bubbles() == 0.0


def test_replies_split_into_the_bubbles_a_person_sends() -> None:
    assert split_bubbles("") == []
    assert split_bubbles("selam") == ["selam"]
    assert split_bubbles("selam\nnas\u0131ls\u0131n\n\nbug\u00fcn m\u00fcsait misin") == ["selam", "nas\u0131ls\u0131n", "bug\u00fcn m\u00fcsait misin"]
    assert split_bubbles("a\nb\nc\nd") == ["a", "b", "c d"], "never more than three; the rest folds into the last"
    long_paragraph = "Bug\u00fcn dersten sonra k\u00fct\u00fcphaneye ge\u00e7ece\u011fim ve ak\u015fama kadar oraday\u0131m. \u0130stersen sen de gel, birlikte \u00e7al\u0131\u015f\u0131r\u0131z. Sonra bir \u015feyler yeriz."
    assert len(split_bubbles(long_paragraph)) == 3
    assert split_bubbles("k\u0131sa. c\u00fcmle.") == ["k\u0131sa. c\u00fcmle."], "short paragraphs stay one bubble"


def test_the_decision_names_its_reason() -> None:
    assert decide_reply([], now=NOON).reason == "nothing_new"
    assert decide_reply(["naber?"], now=NIGHT, quiet=QuietHours()).reason == "quiet_hours"
    assert decide_reply(["tamam", "\U0001F44D"], now=NOON, quiet=QuietHours()).reason == "acknowledged"
    decision = decide_reply(["tamam", "saat ka\u00e7ta?"], now=NOON, quiet=QuietHours())
    assert decision.reply is True and decision.reason == "fresh_message"
    assert decide_reply(["naber?"], now=NIGHT, quiet=None).reply is True, "no quiet hours configured"
