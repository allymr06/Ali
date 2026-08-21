from __future__ import annotations

from dataclasses import dataclass

from app.config.settings import Settings
from app.conversation.engine import ConversationEngine
from app.conversation.store import InMemoryConversationStore
from app.core.engine import CoreEngine
from app.memory.in_memory import InMemoryStore
from app.memory.manager import MemoryManager
from app.providers.mock import MockProvider
from app.providers.openai import OpenAIProvider
from app.providers.catalog import ModelCatalog
from app.providers.gateway import ProviderGateway
from app.providers.models import ModelProfile
from app.providers.registry import ProviderRegistry
from app.providers.router import ModelRouter
from app.tasks.manager import TaskManager
from app.tools.executor import ToolExecutor


@dataclass(slots=True)
class JARVISApplication:
    """Fully initialized JARVIS application."""

    settings: Settings
    provider_registry: ProviderRegistry
    model_catalog: ModelCatalog
    provider_gateway: ProviderGateway
    conversation_engine: ConversationEngine
    memory_manager: MemoryManager
    tool_executor: ToolExecutor
    task_manager: TaskManager
    engine: CoreEngine

    @property
    def agent_loop(self):
        """Create an agent loop bound to this application's engine."""
        from app.agent.loop import AgentLoop

        return AgentLoop(engine=self.engine)


def create_application(
    settings: Settings | None = None,
) -> JARVISApplication:
    """Create and wire the complete JARVIS application."""

    active_settings = settings or Settings.from_environment()

    provider_registry = ProviderRegistry(
        default_provider=active_settings.default_provider,
    )

    mock_provider = MockProvider()
    openai_provider = OpenAIProvider(active_settings)
    provider_registry.register(mock_provider)
    provider_registry.register(openai_provider)

    model_catalog = ModelCatalog()
    model_catalog.register(
        ModelProfile(
            provider="mock",
            model="mock-model",
            capabilities=mock_provider.capabilities,
            priority=1000,
        )
    )
    model_catalog.register(
        ModelProfile(
            provider="openai",
            model=(
                active_settings.openai_model
                or active_settings.default_model
            ),
            capabilities=openai_provider.capabilities,
            priority=100,
        )
    )
    provider_gateway = ProviderGateway(
        provider_registry,
        router=ModelRouter(provider_registry, model_catalog),
        timeout_seconds=active_settings.provider_timeout_seconds,
        max_retries=active_settings.provider_max_retries,
        retry_backoff_seconds=(
            active_settings.provider_retry_backoff_seconds
        ),
        fallback_enabled=active_settings.provider_fallback_enabled,
    )
    conversation_engine = ConversationEngine(
        InMemoryConversationStore(),
        max_context_messages=active_settings.conversation_max_messages,
        max_context_characters=active_settings.conversation_max_characters,
        summary_max_characters=(
            active_settings.conversation_summary_max_characters
        ),
        system_prompt=active_settings.conversation_system_prompt,
    )

    memory_manager = MemoryManager(
        InMemoryStore(),
    )

    tool_executor = ToolExecutor()
    task_manager = TaskManager()

    engine = CoreEngine(
        provider_registry=provider_registry,
        memory_manager=memory_manager,
        tool_executor=tool_executor,
        task_manager=task_manager,
        provider_gateway=provider_gateway,
        conversation_engine=conversation_engine,
    )

    return JARVISApplication(
        settings=active_settings,
        provider_registry=provider_registry,
        model_catalog=model_catalog,
        provider_gateway=provider_gateway,
        conversation_engine=conversation_engine,
        memory_manager=memory_manager,
        tool_executor=tool_executor,
        task_manager=task_manager,
        engine=engine,
    )
