from __future__ import annotations

import re
from dataclasses import dataclass


class SensitiveMemoryError(ValueError):
    """Raised when durable storage would retain an unsafe secret."""


@dataclass(frozen=True, slots=True)
class SensitiveDataFinding:
    category: str
    reason: str


class SensitiveDataGuard:
    """Conservative deterministic guard for common credential material."""

    _LABELED_SECRET = re.compile(
        r"(?i)\b(password|passwd|parola|şifre|sifre|api[_ -]?key|"
        r"access[_ -]?token|secret[_ -]?key)\b\s*[:=]\s*\S+"
    )
    _PRIVATE_KEY = re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")
    _CARD_NUMBER = re.compile(r"(?<!\d)(?:\d[ -]?){13,19}(?!\d)")

    def inspect(self, content: str) -> SensitiveDataFinding | None:
        if self._LABELED_SECRET.search(content):
            return SensitiveDataFinding(
                "credential",
                "Credential-like values cannot be stored in durable memory.",
            )
        if self._PRIVATE_KEY.search(content):
            return SensitiveDataFinding(
                "private_key",
                "Private keys cannot be stored in durable memory.",
            )
        if self._contains_payment_card(content):
            return SensitiveDataFinding(
                "payment_card",
                "Payment-card numbers cannot be stored in durable memory.",
            )
        return None

    def ensure_safe(self, content: str) -> None:
        finding = self.inspect(content)
        if finding is not None:
            raise SensitiveMemoryError(finding.reason)

    def _contains_payment_card(self, content: str) -> bool:
        for match in self._CARD_NUMBER.finditer(content):
            digits = "".join(character for character in match.group() if character.isdigit())
            if 13 <= len(digits) <= 19 and self._passes_luhn(digits):
                return True
        return False

    @staticmethod
    def _passes_luhn(digits: str) -> bool:
        total = 0
        parity = len(digits) % 2
        for index, character in enumerate(digits):
            value = int(character)
            if index % 2 == parity:
                value *= 2
                if value > 9:
                    value -= 9
            total += value
        return total % 10 == 0
