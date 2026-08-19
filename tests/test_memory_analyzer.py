from __future__ import annotations

from app.core.models import Request
from app.memory.analyzer import MemoryAnalyzer
from app.memory.models import MemoryType


def test_analyzer_detects_explicit_memory_request() -> None:
    analyzer = MemoryAnalyzer()

    candidate = analyzer.analyze(
        Request("HATIRLA: Python öğreniyorum")
    )

    assert candidate is not None
    assert candidate.content == "Python öğreniyorum"
    assert candidate.memory_type is MemoryType.FACT
    assert candidate.confidence == 1.0


def test_analyzer_is_case_insensitive() -> None:
    analyzer = MemoryAnalyzer()

    candidate = analyzer.analyze(
        Request("HaTıRlA: Rust öğreniyorum")
    )

    assert candidate is not None
    assert candidate.content == "Rust öğreniyorum"


def test_analyzer_supports_english_command() -> None:
    analyzer = MemoryAnalyzer()

    candidate = analyzer.analyze(
        Request("REMEMBER: I am learning Python")
    )

    assert candidate is not None
    assert candidate.content == "I am learning Python"


def test_analyzer_ignores_normal_request() -> None:
    analyzer = MemoryAnalyzer()

    candidate = analyzer.analyze(
        Request("Merhaba JARVIS")
    )

    assert candidate is None


def test_analyzer_ignores_empty_memory_request() -> None:
    analyzer = MemoryAnalyzer()

    candidate = analyzer.analyze(
        Request("HATIRLA:")
    )

    assert candidate is None