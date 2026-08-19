import pytest

from app.core.models import Context, Request
from app.providers.base import AIProvider, ModelCapabilities, ModelResponse
from app.providers.registry import ProviderRegistry


class FakeProvider(AIProvider):
    def __init__(self, provider_name: str) -> None:
        self._name = provider_name

    @property
    def name(self) -> str:
        return self._name

    @property
    def capabilities(self) -> ModelCapabilities:
        return ModelCapabilities(
            text=True,
            tool_calling=True,
        )

    async def generate(
        self,
        request: Request,
        context: Context,
        *,
        model: str | None = None,
        system_prompt: str | None = None,
        tools: list[dict] | None = None,
    ) -> ModelResponse:
        return ModelResponse(
            text=request.text,
            model=model or "fake-model",
            provider=self.name,
        )


def test_registry_starts_empty():
    registry = ProviderRegistry()

    assert len(registry) == 0
    assert registry.list_names() == ()


def test_provider_can_be_registered():
    registry = ProviderRegistry()
    provider = FakeProvider("test")

    registry.register(provider)

    assert len(registry) == 1
    assert registry.contains("test")
    assert registry.get("test") is provider


def test_first_provider_becomes_default():
    registry = ProviderRegistry()
    provider = FakeProvider("test")

    registry.register(provider)

    assert registry.get_default() is provider


def test_explicit_default_provider():
    registry = ProviderRegistry()

    first = FakeProvider("first")
    second = FakeProvider("second")

    registry.register(first)
    registry.register(second, make_default=True)

    assert registry.get_default() is second


def test_default_provider_can_be_changed():
    registry = ProviderRegistry()

    first = FakeProvider("first")
    second = FakeProvider("second")

    registry.register(first)
    registry.register(second)

    registry.set_default("second")

    assert registry.get_default() is second


def test_duplicate_provider_registration_is_rejected():
    registry = ProviderRegistry()

    registry.register(FakeProvider("test"))

    with pytest.raises(ValueError):
        registry.register(FakeProvider("test"))


def test_unknown_provider_lookup_is_rejected():
    registry = ProviderRegistry()

    with pytest.raises(KeyError):
        registry.get("unknown")


def test_unknown_default_provider_is_rejected():
    registry = ProviderRegistry()

    with pytest.raises(KeyError):
        registry.set_default("unknown")


def test_provider_can_be_unregistered():
    registry = ProviderRegistry()
    provider = FakeProvider("test")

    registry.register(provider)

    removed = registry.unregister("test")

    assert removed is provider
    assert len(registry) == 0
    assert registry.contains("test") is False


def test_unregistering_default_selects_another_provider():
    registry = ProviderRegistry()

    first = FakeProvider("first")
    second = FakeProvider("second")

    registry.register(first)
    registry.register(second)

    registry.unregister("first")

    assert registry.get_default() is second


def test_unregistering_unknown_provider_is_rejected():
    registry = ProviderRegistry()

    with pytest.raises(KeyError):
        registry.unregister("unknown")


def test_provider_names_are_listed():
    registry = ProviderRegistry()

    registry.register(FakeProvider("alpha"))
    registry.register(FakeProvider("beta"))

    assert registry.list_names() == ("alpha", "beta")


def test_provider_name_is_normalized():
    registry = ProviderRegistry()
    provider = FakeProvider("  test  ")

    registry.register(provider)

    assert registry.contains("test")
    assert registry.get("test") is provider