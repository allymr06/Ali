from __future__ import annotations

from threading import RLock

from app.providers.base import ProviderCapability
from app.providers.models import ModelProfile, TaskType


class ModelCatalog:
    """Thread-safe registry of routable model capability declarations."""

    def __init__(self) -> None:
        self._profiles: dict[tuple[str, str], ModelProfile] = {}
        self._lock = RLock()

    @staticmethod
    def _key(provider: str, model: str) -> tuple[str, str]:
        return provider.strip(), model.strip()

    def register(self, profile: ModelProfile) -> None:
        key = self._key(profile.provider, profile.model)
        with self._lock:
            if key in self._profiles:
                raise ValueError(
                    f"Model '{key[0]}/{key[1]}' is already registered."
                )
            self._profiles[key] = profile

    def unregister(self, provider: str, model: str) -> ModelProfile:
        key = self._key(provider, model)
        with self._lock:
            try:
                return self._profiles.pop(key)
            except KeyError as exc:
                raise KeyError(f"Unknown model: {key[0]}/{key[1]}") from exc

    def get(self, provider: str, model: str) -> ModelProfile:
        key = self._key(provider, model)
        with self._lock:
            try:
                return self._profiles[key]
            except KeyError as exc:
                raise KeyError(f"Unknown model: {key[0]}/{key[1]}") from exc

    def contains(self, provider: str, model: str) -> bool:
        key = self._key(provider, model)
        with self._lock:
            return key in self._profiles

    def list(self, provider: str | None = None) -> tuple[ModelProfile, ...]:
        normalized = provider.strip() if provider is not None else None
        with self._lock:
            profiles = tuple(self._profiles.values())
        if normalized is None:
            return profiles
        return tuple(item for item in profiles if item.provider == normalized)

    def candidates(
        self,
        *,
        task_type: TaskType,
        required: frozenset[ProviderCapability],
        provider: str | None = None,
    ) -> tuple[ModelProfile, ...]:
        candidates = (
            profile
            for profile in self.list(provider)
            if profile.supports(task_type, required)
        )
        return tuple(
            sorted(
                candidates,
                key=lambda item: (
                    item.priority,
                    item.input_cost_per_million is None,
                    item.input_cost_per_million or 0.0,
                    item.provider,
                    item.model,
                ),
            )
        )

    def __len__(self) -> int:
        with self._lock:
            return len(self._profiles)
