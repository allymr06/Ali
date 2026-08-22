from __future__ import annotations

import pytest

from app.memory.safety import SensitiveDataGuard, SensitiveMemoryError


@pytest.mark.parametrize(
    "content",
    [
        "password: hunter2",
        "API_KEY=sk-example",
        "parola = gizli",
        "-----BEGIN PRIVATE KEY-----",
        "Card 4111 1111 1111 1111",
    ],
)
def test_guard_rejects_common_secret_material(content: str) -> None:
    with pytest.raises(SensitiveMemoryError):
        SensitiveDataGuard().ensure_safe(content)


@pytest.mark.parametrize(
    "content",
    [
        "The user prefers short answers",
        "The project uses token-based pagination",
        "Discuss password managers without storing a password",
    ],
)
def test_guard_allows_non_secret_context(content: str) -> None:
    SensitiveDataGuard().ensure_safe(content)
