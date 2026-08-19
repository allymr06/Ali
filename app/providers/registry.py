from __future__ import annotations

from threading import RLock

from app.providers.base import AIProvider


class ProviderRegistry:
    """
    Central registry for JARVIS AI providers.

    The registry owns provider registration and lookup, while remaining
    independent from any specific provider implementation.
    """

    def __init__(self) -> None:
        self._providers: dict[str, AIProvider] = {}
        self._default_provider: str | None = None
        self._lock = RLock()

    def register(
        self,
        provider: AIProvider,
        *,
        make_default: bool = False,
    ) -> None:
        """Register a provider by its unique name."""
        name = provider.name.strip()

        if not name:
            raise ValueError("Provider name cannot be empty.")

        with self._lock:
            if name in self._providers:
                raise ValueError(
                    f"Provider '{name}' is already registered."
                )

            self._providers[name] = provider

            if make_default or self._default_provider is None:
                self._default_provider = name

    def unregister(self, name: str) -> AIProvider:
        """Remove and return a registered provider."""
        normalized_name = name.strip()

        with self._lock:
            if normalized_name not in self._providers:
                raise KeyError(
                    f"Provider '{normalized_name}' is not registered."
                )

            provider = self._providers.pop(normalized_name)

            if self._default_provider == normalized_name:
                self._default_provider = next(
                    iter(self._providers),
                    None,
                )

            return provider

    def get(self, name: str) -> AIProvider:
        """Return a provider by name."""
        normalized_name = name.strip()

        with self._lock:
            try:
                return self._providers[normalized_name]
            except KeyError as exc:
                raise KeyError(
                    f"Provider '{normalized_name}' is not registered."
                ) from exc

    def get_default(self) -> AIProvider:
        """Return the currently configured default provider."""
        with self._lock:
            if self._default_provider is None:
                raise RuntimeError("No default provider is configured.")

            return self._providers[self._default_provider]

    def set_default(self, name: str) -> None:
        """Set an existing provider as the default provider."""
        normalized_name = name.strip()

        with self._lock:
            if normalized_name not in self._providers:
                raise KeyError(
                    f"Provider '{normalized_name}' is not registered."
                )

            self._default_provider = normalized_name

    def contains(self, name: str) -> bool:
        """Return whether a provider is registered."""
        return name.strip() in self._providers

    def list_names(self) -> tuple[str, ...]:
        """Return registered provider names."""
        with self._lock:
            return tuple(self._providers.keys())

    def __len__(self) -> int:
        with self._lock:
            return len(self._providers)