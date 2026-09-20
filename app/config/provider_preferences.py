from __future__ import annotations

import json
import os
import re
from dataclasses import asdict, dataclass
from pathlib import Path


SUPPORTED_PROVIDERS = frozenset({"gemini"})
DEFAULT_GEMINI_MODEL = "gemini-3.5-flash-lite"
_MODEL_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")


def validate_provider(provider: str) -> str:
    normalized = provider.strip().lower()
    if normalized not in SUPPORTED_PROVIDERS:
        raise ValueError(f"Unsupported provider: {provider!r}.")
    return normalized


def validate_model(model: str) -> str:
    normalized = model.strip()
    if not _MODEL_PATTERN.fullmatch(normalized):
        raise ValueError("Model name contains unsupported characters.")
    return normalized


def validate_brief_time(value: str) -> str:
    """HH:MM on a 24-hour clock, normalized to two digits each."""
    parts = str(value).strip().split(":")
    if len(parts) != 2 or not all(part.isdigit() for part in parts):
        raise ValueError("daily_brief_time must be HH:MM on a 24-hour clock.")
    hour, minute = int(parts[0]), int(parts[1])
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise ValueError("daily_brief_time must be HH:MM on a 24-hour clock.")
    return f"{hour:02d}:{minute:02d}"


@dataclass(frozen=True, slots=True)
class ProviderPreferences:
    provider: str = "gemini"
    model: str = DEFAULT_GEMINI_MODEL
    version: int = 1
    # Non-secret assistant preferences, editable from the Settings screen.
    daily_brief_notification: bool = True
    daily_brief_time: str = "08:30"
    research_enabled: bool = True
    # The one city the morning almanac reports the weather for; empty
    # means the brief simply carries no weather line.
    almanac_city: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "provider", validate_provider(self.provider))
        object.__setattr__(self, "model", validate_model(self.model))
        object.__setattr__(
            self, "daily_brief_notification", bool(self.daily_brief_notification)
        )
        object.__setattr__(
            self, "daily_brief_time", validate_brief_time(self.daily_brief_time)
        )
        object.__setattr__(self, "research_enabled", bool(self.research_enabled))
        object.__setattr__(self, "almanac_city", str(self.almanac_city or "").strip()[:80])
        if self.version != 1:
            raise ValueError("Unsupported provider preference version.")


class ProviderPreferencesStore:
    """Persist non-secret desktop provider preferences atomically."""

    def __init__(self, path: str | Path | None = None) -> None:
        self.path = Path(path) if path is not None else self.default_path()

    @staticmethod
    def default_path() -> Path:
        base = os.getenv("LOCALAPPDATA")
        if base:
            return Path(base) / "JARVIS" / "settings.json"
        return Path.home() / ".jarvis" / "settings.json"

    def load(self) -> ProviderPreferences:
        if not self.path.exists():
            return ProviderPreferences()
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(payload, dict):
                raise ValueError("Provider preferences must be a JSON object.")
            return ProviderPreferences(
                provider=str(payload.get("provider", "gemini")),
                model=str(payload.get("model", DEFAULT_GEMINI_MODEL)),
                version=int(payload.get("version", 1)),
                daily_brief_notification=bool(
                    payload.get("daily_brief_notification", True)
                ),
                daily_brief_time=str(payload.get("daily_brief_time", "08:30")),
                research_enabled=bool(payload.get("research_enabled", True)),
                almanac_city=str(payload.get("almanac_city", "")),
            )
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            return ProviderPreferences()

    def save(self, preferences: ProviderPreferences) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        payload = json.dumps(
            asdict(preferences),
            ensure_ascii=True,
            indent=2,
            sort_keys=True,
        )
        temporary.write_text(payload + "\n", encoding="utf-8")
        os.replace(temporary, self.path)
