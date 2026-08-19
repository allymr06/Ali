from __future__ import annotations

from app.core.models import Request
from app.memory.models import MemoryType
from app.memory.policy import MemoryPolicy


def test_policy_remembers_explicit_turkish_request() -> None:
    policy = MemoryPolicy()

    decision = policy.evaluate(
        Request("Bunu hatırla: Python öğreniyorum")
    )

    assert decision.should_remember is True
    assert decision.memory_type is MemoryType.FACT
    assert decision.importance == 0.9


def test_policy_remembers_explicit_english_request() -> None:
    policy = MemoryPolicy()

    decision = policy.evaluate(
        Request("Remember that I use Python")
    )

    assert decision.should_remember is True
    assert decision.importance == 0.9


def test_policy_detects_preference() -> None:
    policy = MemoryPolicy()

    decision = policy.evaluate(
        Request("Kısa cevapları tercih ediyorum")
    )

    assert decision.should_remember is True
    assert decision.memory_type is MemoryType.PREFERENCE
    assert decision.importance == 0.8


def test_policy_detects_negative_preference() -> None:
    policy = MemoryPolicy()

    decision = policy.evaluate(
        Request("Uzun cevapları sevmiyorum")
    )

    assert decision.should_remember is True
    assert decision.memory_type is MemoryType.PREFERENCE


def test_policy_ignores_normal_request() -> None:
    policy = MemoryPolicy()

    decision = policy.evaluate(
        Request("Bugün hava nasıl?")
    )

    assert decision.should_remember is False


def test_policy_is_case_insensitive() -> None:
    policy = MemoryPolicy()

    decision = policy.evaluate(
        Request("HATIRLA: Python öğreniyorum")
    )

    assert decision.should_remember is True


def test_policy_is_conservative() -> None:
    policy = MemoryPolicy()

    decision = policy.evaluate(
        Request("Merhaba JARVIS")
    )

    assert decision.should_remember is False
    assert decision.reason == "No memory-worthy signal detected."