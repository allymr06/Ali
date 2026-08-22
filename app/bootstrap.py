from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import timedelta

from app.config.provider_preferences import DEFAULT_GEMINI_MODEL
from app.config.paths import migrate_default_directory, migrate_default_sqlite
from app.config.settings import Settings
from app.conversation.engine import ConversationEngine
from app.conversation.sqlite import SQLiteConversationStore
from app.conversation.store import InMemoryConversationStore
from app.core.engine import CoreEngine
from app.diagnostics.health import HealthCheck
from app.diagnostics.ledger import DiagnosticLedger
from app.diagnostics.metrics import MetricRegistry
from app.diagnostics.models import HealthStatus
from app.diagnostics.service import DiagnosticsService
from app.memory.in_memory import InMemoryStore
from app.memory.manager import MemoryManager
from app.memory.service import MemoryService
from app.memory.sqlite import SQLiteMemoryStore
from app.platform.windows import WindowsIntegrationService
from app.providers.base import ModelCapabilities
from app.providers.mock import MockProvider
from app.providers.openai import OpenAIProvider
from app.providers.gemini import GeminiProvider
from app.providers.ollama import OllamaProvider
from app.providers.ollama_hybrid import OllamaHybridPolicy
from app.providers.ollama_warm import OllamaWarmKeeper
from app.providers.catalog import ModelCatalog
from app.providers.gateway import ProviderGateway
from app.providers.models import ModelProfile, TaskType
from app.providers.registry import ProviderRegistry
from app.providers.router import ModelRouter
from app.research import (
    ResearchService,
    SQLiteResearchCache,
    SafeWebFetcher,
    SearXNGSearchProvider,
    URLPolicy,
)
from app.reliability.circuit import CircuitState
from app.security.permissions import PermissionEngine
from app.tasks.manager import TaskManager
from app.tasks.sqlite import SQLiteTaskStore
from app.tasks.service import TaskControlService
from app.tools.executor import ToolExecutor
from app.tools.fast_actions import ApprovedApplicationFastRouter
from app.tools.selection import ToolSchemaSelector
from app.voice.registry import (
    VoiceProviderRegistry,
    create_default_voice_provider_registry,
)
from app.voice.service import VoiceService
from app.vision.service import VisionService


@dataclass(slots=True)
class JARVISApplication:
    """Fully initialized JARVIS application."""

    settings: Settings
    provider_registry: ProviderRegistry
    model_catalog: ModelCatalog
    provider_gateway: ProviderGateway
    conversation_engine: ConversationEngine
    memory_manager: MemoryManager
    memory_service: MemoryService
    tool_executor: ToolExecutor
    task_manager: TaskManager
    task_service: TaskControlService
    diagnostics: DiagnosticsService
    windows: WindowsIntegrationService | None
    engine: CoreEngine
    voice_provider_registry: VoiceProviderRegistry | None = None
    voice: VoiceService | None = None
    vision: VisionService | None = None
    research: ResearchService | None = None
    ollama_warm_keeper: OllamaWarmKeeper | None = None
    ollama_chat_warm_keeper: OllamaWarmKeeper | None = None

    @property
    def agent_loop(self):
        """Create an agent loop bound to this application's engine."""
        from app.agent.loop import AgentLoop

        return AgentLoop(
            engine=self.engine,
            approval_ttl_seconds=self.settings.approval_ttl_seconds,
        )

    def close(self) -> None:
        """Release durable stores owned by the application runtime."""
        if self.ollama_chat_warm_keeper is not None:
            self.ollama_chat_warm_keeper.close()

        if self.ollama_warm_keeper is not None:
            self.ollama_warm_keeper.close()

        conversation_store = (
            self.conversation_engine.store
        )
        close_conversations = getattr(
            conversation_store,
            "close",
            None,
        )
        if callable(close_conversations):
            close_conversations()

        self.task_manager.close()
        self.memory_manager.close()


def create_application(
    settings: Settings | None = None,
) -> JARVISApplication:
    """Create and wire the complete JARVIS application."""

    active_settings = settings or Settings.from_environment()

    diagnostics = DiagnosticsService(
        ledger=DiagnosticLedger(active_settings.diagnostics_event_capacity),
        metrics=MetricRegistry(active_settings.diagnostics_metric_capacity),
    )

    provider_registry = ProviderRegistry(
        default_provider=active_settings.default_provider,
    )

    mock_provider = MockProvider()
    openai_provider = OpenAIProvider(active_settings)
    gemini_provider = GeminiProvider(active_settings)
    ollama_provider = OllamaProvider(active_settings)

    provider_registry.register(mock_provider)
    provider_registry.register(openai_provider)
    provider_registry.register(gemini_provider)
    provider_registry.register(ollama_provider)

    voice_provider_registry = (
        create_default_voice_provider_registry()
    )

    model_catalog = ModelCatalog()
    model_catalog.register(
        ModelProfile(
            provider="mock",
            model="mock-model",
            capabilities=mock_provider.capabilities,
            priority=1000,
        )
    )
    openai_general_model = active_settings.openai_model or active_settings.default_model
    if openai_general_model == active_settings.vision_model:
        model_catalog.register(
            ModelProfile(
                provider="openai",
                model=openai_general_model,
                capabilities=openai_provider.capabilities,
                priority=100,
            )
        )
    else:
        model_catalog.register(
            ModelProfile(
                provider="openai",
                model=openai_general_model,
                capabilities=openai_provider.capabilities,
                task_types=frozenset(TaskType) - {TaskType.VISION},
                priority=100,
            )
        )
        model_catalog.register(
            ModelProfile(
                provider="openai",
                model=active_settings.vision_model,
                capabilities=openai_provider.capabilities,
                task_types=frozenset({TaskType.VISION}),
                priority=50,
            )
        )
    gemini_model = active_settings.gemini_model or DEFAULT_GEMINI_MODEL
    model_catalog.register(
        ModelProfile(
            provider="gemini",
            model=gemini_model,
            capabilities=gemini_provider.capabilities,
            priority=75,
        )
    )

    ollama_model = (
        active_settings.ollama_model
        or "llama3.2:latest"
    )

    ollama_chat_model = (
        active_settings.ollama_chat_model.strip()
    )
    model_catalog.register(
        ModelProfile(
            provider="ollama",
            model=ollama_model,
            capabilities=ollama_provider.capabilities,
            priority=90,
        )
    )

    if (
        active_settings.ollama_hybrid_enabled
        and ollama_chat_model != ollama_model
    ):
        model_catalog.register(
            ModelProfile(
                provider="ollama",
                model=ollama_chat_model,
                capabilities=ModelCapabilities(
                    text=True,
                    streaming=True,
                    structured_output=False,
                    tool_calling=False,
                    vision=False,
                ),
                task_types=frozenset(
                    {
                        TaskType.SIMPLE,
                        TaskType.STANDARD,
                        TaskType.COMPLEX,
                        TaskType.LONG_RUNNING,
                    }
                ),
                priority=80,
            )
        )

    ollama_hybrid_policy = OllamaHybridPolicy(
        enabled=active_settings.ollama_hybrid_enabled,
        chat_model=ollama_chat_model,
        tool_model=ollama_model,
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
        circuit_failure_threshold=(
            active_settings.provider_circuit_failure_threshold
        ),
        circuit_recovery_seconds=(
            active_settings.provider_circuit_recovery_seconds
        ),
    )
    migrate_default_sqlite(
        active_settings.conversation_database_path,
        "jarvis_conversations.sqlite3",
    )
    migrate_default_sqlite(
        active_settings.memory_database_path,
        "jarvis_memory.sqlite3",
    )
    migrate_default_sqlite(
        active_settings.task_database_path,
        "jarvis_tasks.sqlite3",
    )
    migrate_default_directory(
        active_settings.task_runtime_directory,
        "tasks",
    )

    conversation_store = (
        SQLiteConversationStore(
            active_settings.conversation_database_path
        )
        if active_settings.conversation_database_path
        is not None
        else InMemoryConversationStore()
    )

    conversation_engine = ConversationEngine(
        conversation_store,
        max_context_messages=active_settings.conversation_max_messages,
        max_context_characters=active_settings.conversation_max_characters,
        summary_max_characters=(
            active_settings.conversation_summary_max_characters
        ),
        system_prompt=active_settings.conversation_system_prompt,
    )

    memory_store = (
        SQLiteMemoryStore(active_settings.memory_database_path)
        if active_settings.memory_database_path is not None
        else InMemoryStore()
    )
    memory_manager = MemoryManager(memory_store)

    tool_executor = ToolExecutor(
        PermissionEngine(
            audit_capacity=active_settings.permission_audit_capacity,
        )
    )
    memory_service = MemoryService(memory_manager)
    memory_service.register_tools(tool_executor)
    windows = None
    if active_settings.windows_integrations_enabled and os.name == "nt":
        windows = WindowsIntegrationService.create_default(
            verification_timeout_seconds=(
                active_settings.windows_launch_verification_timeout_seconds
            )
        )
        windows.register_tools(tool_executor)
    task_store = (
        SQLiteTaskStore(active_settings.task_database_path)
        if active_settings.task_database_path is not None
        else None
    )
    task_manager = TaskManager(task_store)

    fast_action_router = None

    if (
        windows is not None
        and active_settings.ollama_hybrid_enabled
        and (
            active_settings
            .default_provider
            .strip()
            .casefold()
            == "ollama"
        )
    ):
        launch_aliases = {}

        for application in (
            windows.applications.list()
        ):
            target = (
                application.application_id,
                application.display_name,
            )

            launch_aliases[
                application.application_id
            ] = target

            launch_aliases[
                application.display_name
            ] = target

            for alias in (
                application.aliases
            ):
                launch_aliases[
                    alias
                ] = target

        fast_action_router = (
            ApprovedApplicationFastRouter(
                launch_aliases
            )
        )

    tool_schema_selector = ToolSchemaSelector()

    engine = CoreEngine(
        provider_registry=provider_registry,
        memory_manager=memory_manager,
        tool_executor=tool_executor,
        task_manager=task_manager,
        provider_gateway=provider_gateway,
        ollama_hybrid_policy=ollama_hybrid_policy,
        fast_action_router=fast_action_router,
        tool_schema_selector=tool_schema_selector,
        conversation_engine=conversation_engine,
        task_runtime_directory=active_settings.task_runtime_directory,
        diagnostics=diagnostics,
        max_concurrent_requests=active_settings.core_max_concurrent_requests,
        max_queued_requests=active_settings.core_max_queued_requests,
        admission_timeout_seconds=active_settings.core_admission_timeout_seconds,
    )
    task_service = TaskControlService(task_manager, engine.task_runtime)
    task_service.register_tools(tool_executor)

    voice = None
    if active_settings.voice_enabled:
        if os.name != "nt":
            raise OSError("The configured voice output currently requires Windows.")
        from app.voice.audio import (
            SoundDeviceAudioInput,
            WindowsWaveAudioOutput,
        )

        audio_input = SoundDeviceAudioInput(
            sample_rate=active_settings.voice_sample_rate,
            channels=active_settings.voice_channels,
            device_id=active_settings.voice_input_device_id,
            vad_enabled=active_settings.voice_vad_enabled,
            silence_threshold_rms=(
                active_settings.voice_silence_threshold_rms
            ),
            min_speech_seconds=(
                active_settings.voice_min_speech_seconds
            ),
            trailing_silence_seconds=(
                active_settings.voice_trailing_silence_seconds
            ),
            start_timeout_seconds=(
                active_settings.voice_start_timeout_seconds
            ),
        )

        audio_output = WindowsWaveAudioOutput()

        default_voice_fallback = (
            "openai"
            if (
                active_settings.default_provider
                .strip()
                .casefold()
                == "mock"
            )
            else None
        )

        stt_provider = (
            voice_provider_registry
            .resolve_recognizer_provider(
                active_settings.voice_stt_provider,
                default_provider=(
                    active_settings.default_provider
                ),
                fallback_provider=(
                    default_voice_fallback
                ),
            )
        )

        tts_provider = (
            voice_provider_registry
            .resolve_synthesizer_provider(
                active_settings.voice_tts_provider,
                default_provider=(
                    active_settings.default_provider
                ),
                fallback_provider=(
                    default_voice_fallback
                ),
            )
        )

        recognizer = (
            voice_provider_registry
            .create_recognizer(
                stt_provider,
                active_settings,
            )
        )

        synthesizer = (
            voice_provider_registry
            .create_synthesizer(
                tts_provider,
                active_settings,
            )
        )

        voice = VoiceService.create(
            engine=engine,
            audio_input=audio_input,
            audio_output=audio_output,
            recognizer=recognizer,
            synthesizer=synthesizer,
            stt_provider=stt_provider,
            tts_provider=tts_provider,
            wake_word=active_settings.voice_wake_word,
            max_recording_seconds=active_settings.voice_max_recording_seconds,
            operation_timeout_seconds=active_settings.voice_operation_timeout_seconds,
            language=active_settings.voice_language,
            require_wake_word=active_settings.voice_require_wake_word,
            retain_audio=active_settings.voice_retain_last_audio,
        )

    vision = None
    if active_settings.vision_enabled:
        if os.name != "nt":
            raise OSError("The configured vision source currently requires Windows.")
        from app.vision.capture import WindowsScreenSource
        from app.vision.consent import VisionConsentGate
        from app.vision.models import VisionDetail

        vision = VisionService(
            engine=engine,
            source=WindowsScreenSource(
                max_width=active_settings.vision_max_width,
                max_height=active_settings.vision_max_height,
                max_pixels=active_settings.vision_max_pixels,
            ),
            consent_gate=VisionConsentGate(
                ttl_seconds=active_settings.vision_consent_ttl_seconds
            ),
            detail=VisionDetail(active_settings.vision_detail),
            operation_timeout_seconds=active_settings.vision_operation_timeout_seconds,
            max_frame_age_seconds=active_settings.vision_max_frame_age_seconds,
            max_encoded_bytes=active_settings.vision_max_encoded_bytes,
            redact_taskbar=active_settings.vision_redact_taskbar,
            taskbar_height=active_settings.vision_taskbar_height,
            retain_last_image=active_settings.vision_retain_last_image,
        )

    research = None
    if active_settings.research_enabled:
        policy = URLPolicy(allow_http=active_settings.research_allow_http)
        fetcher = SafeWebFetcher(
            policy,
            timeout_seconds=active_settings.research_timeout_seconds,
            max_bytes=active_settings.research_max_response_bytes,
            max_characters=active_settings.research_max_content_characters,
            max_redirects=active_settings.research_max_redirects,
            user_agent=active_settings.research_user_agent,
        )
        search_provider = SearXNGSearchProvider(
            active_settings.research_searxng_url or "",
            fetcher,
            policy,
        )
        research = ResearchService(
            search_provider=search_provider,
            fetcher=fetcher,
            max_sources=active_settings.research_max_sources,
            max_concurrency=active_settings.research_max_concurrency,
            operation_timeout_seconds=(
                active_settings.research_operation_timeout_seconds
            ),
            cache=(
                SQLiteResearchCache(
                    active_settings.research_cache_database_path,
                    ttl=timedelta(
                        seconds=active_settings.research_cache_ttl_seconds
                    ),
                )
                if active_settings.research_cache_database_path is not None
                else None
            ),
        )
        research.register_tools(tool_executor)

    ollama_warm_keeper = None
    ollama_chat_warm_keeper = None

    if (
        active_settings.ollama_warm_enabled
        and ollama_provider.is_configured
    ):
        ollama_warm_keeper = OllamaWarmKeeper(
            base_url=active_settings.ollama_base_url,
            model=ollama_model,
            keep_alive_seconds=(
                active_settings.ollama_keep_alive_seconds
            ),
            refresh_seconds=(
                active_settings.ollama_warm_refresh_seconds
            ),
            retry_seconds=(
                active_settings.ollama_warm_retry_seconds
            ),
            timeout_seconds=(
                active_settings.ollama_warmup_timeout_seconds
            ),
        )

        # start() never performs the HTTP request itself.
        # The potentially slow model load runs in a daemon
        # background thread.
        ollama_warm_keeper.start()

        if (
            active_settings.ollama_hybrid_enabled
            and ollama_chat_model != ollama_model
        ):
            ollama_chat_warm_keeper = OllamaWarmKeeper(
                base_url=active_settings.ollama_base_url,
                model=ollama_chat_model,
                keep_alive_seconds=(
                    active_settings.ollama_keep_alive_seconds
                ),
                refresh_seconds=(
                    active_settings.ollama_warm_refresh_seconds
                ),
                retry_seconds=(
                    active_settings.ollama_warm_retry_seconds
                ),
                timeout_seconds=(
                    active_settings.ollama_warmup_timeout_seconds
                ),
            )

            # Model loading remains off the synchronous
            # JARVIS bootstrap path.
            ollama_chat_warm_keeper.start()

    health_timeout = active_settings.diagnostics_health_timeout_seconds
    diagnostics.health.register(
        HealthCheck(
            "core",
            "core",
            lambda: (HealthStatus.HEALTHY, "Core is initialized."),
            health_timeout,
        )
    )
    diagnostics.health.register(
        HealthCheck(
            "provider_registry",
            "providers",
            lambda: (
                HealthStatus.HEALTHY,
                "Default provider is registered.",
                {"default": provider_registry.get_default().name},
            ),
            health_timeout,
        )
    )
    diagnostics.health.register(
        HealthCheck(
            "provider_gateway",
            "providers",
            lambda: (
                (
                    HealthStatus.DEGRADED
                    if provider_gateway.health(
                        active_settings.default_provider
                    ).circuit_state is CircuitState.OPEN
                    else HealthStatus.HEALTHY
                ),
                "Provider gateway circuit observed.",
                {
                    "provider": active_settings.default_provider,
                    "circuit": provider_gateway.health(
                        active_settings.default_provider
                    ).circuit_state.value,
                },
            ),
            health_timeout,
        )
    )
    diagnostics.health.register(
        HealthCheck(
            "memory_store",
            "memory",
            lambda: (
                HealthStatus.HEALTHY,
                "Memory store is readable.",
                {"active_records": len(memory_manager.active())},
            ),
            health_timeout,
        )
    )
    diagnostics.health.register(
        HealthCheck(
            "task_store",
            "tasks",
            lambda: (
                HealthStatus.HEALTHY,
                "Task store is readable.",
                {"records": len(task_manager.list())},
            ),
            health_timeout,
        )
    )
    diagnostics.health.register(
        HealthCheck(
            "event_ledger",
            "diagnostics",
            lambda: (
                (
                    HealthStatus.HEALTHY
                    if diagnostics.ledger.verify_integrity()
                    else HealthStatus.UNHEALTHY
                ),
                "Diagnostic event chain verified.",
            ),
            health_timeout,
        )
    )
    diagnostics.health.register(
        HealthCheck(
            "core_admission",
            "core",
            lambda: (
                HealthStatus.HEALTHY,
                "Core admission controller is within configured bounds.",
                {
                    "active": engine.admission.snapshot().active,
                    "waiting": engine.admission.snapshot().waiting,
                    "rejected": engine.admission.snapshot().rejected,
                },
            ),
            health_timeout,
        )
    )
    diagnostics.register_tools(tool_executor)
    diagnostics.record(
        "bootstrap",
        "application.ready",
        "JARVIS application initialized.",
        attributes={
            "provider": active_settings.default_provider,
            "voice_enabled": voice is not None,
            "vision_enabled": vision is not None,
            "research_enabled": research is not None,
            "windows_enabled": windows is not None,
            "ollama_warm_enabled": (
                ollama_warm_keeper is not None
            ),
            "ollama_hybrid_enabled": (
                active_settings.ollama_hybrid_enabled
            ),
            "ollama_chat_warm_enabled": (
                ollama_chat_warm_keeper is not None
            ),
        },
    )

    return JARVISApplication(
        settings=active_settings,
        provider_registry=provider_registry,
        model_catalog=model_catalog,
        provider_gateway=provider_gateway,
        conversation_engine=conversation_engine,
        memory_manager=memory_manager,
        memory_service=memory_service,
        tool_executor=tool_executor,
        task_manager=task_manager,
        task_service=task_service,
        diagnostics=diagnostics,
        windows=windows,
        engine=engine,
        voice_provider_registry=voice_provider_registry,
        voice=voice,
        vision=vision,
        research=research,
        ollama_warm_keeper=ollama_warm_keeper,
        ollama_chat_warm_keeper=ollama_chat_warm_keeper,
    )
