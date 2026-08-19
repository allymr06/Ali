from __future__ import annotations

from dataclasses import dataclass

from app.config.settings import Settings
from app.core.engine import CoreEngine
from app.memory.in_memory import InMemoryStore
from app.memory.manager import MemoryManager
from app.providers.mock import MockProvider
from app.providers.openai import OpenAIProvider
from app.providers.registry import ProviderRegistry
from app.tools.executor import ToolExecutor


@dataclass(slots=True)
class JARVISApplication:
    """Fully initialized JARVIS application."""

    settings: Settings
    provider_registry: ProviderRegistry
    memory_manager: MemoryManager
    tool_executor: ToolExecutor
    engine: CoreEngine


def create_application(
    settings: Settings | None = None,
) -> JARVISApplication:
    """Create and wire the complete JARVIS application."""

    active_settings = settings or Settings.from_environment()

    provider_registry = ProviderRegistry(
        default_provider=active_settings.default_provider,
    )

    provider_registry.register(
        MockProvider(),
    )

    provider_registry.register(
        OpenAIProvider(active_settings),
    )

    memory_manager = MemoryManager(
        InMemoryStore(),
    )

    tool_executor = ToolExecutor()

    engine = CoreEngine(
        provider_registry=provider_registry,
        memory_manager=memory_manager,
        tool_executor=tool_executor,
    )

    return JARVISApplication(
        settings=active_settings,
        provider_registry=provider_registry,
        memory_manager=memory_manager,
        tool_executor=tool_executor,
        engine=engine,
    )
