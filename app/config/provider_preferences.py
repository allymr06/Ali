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


@dataclass(frozen=True, slots=True)
class ProviderPreferences:
    provider: str = "gemini"
    model: str = DEFAULT_GEMINI_MODEL
    version: int = 1

    def __post_init__(self) -> None:
        object.__setattr__(self, "provider", validate_provider(self.provider))
        object.__setattr__(self, "model", validate_model(self.model))
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
