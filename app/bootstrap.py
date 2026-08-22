from __future__ import annotations

import os
from dataclasses import dataclass

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
from app.providers.mock import MockProvider
from app.providers.openai import OpenAIProvider
from app.providers.gemini import GeminiProvider
from app.providers.catalog import ModelCatalog
from app.providers.gateway import ProviderGateway
from app.providers.models import ModelProfile, TaskType
from app.providers.registry import ProviderRegistry
from app.providers.router import ModelRouter
from app.research import (
    ResearchService,
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
    voice: VoiceService | None = None
    vision: VisionService | None = None
    research: ResearchService | None = None

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
    provider_registry.register(mock_provider)
    provider_registry.register(openai_provider)
    provider_registry.register(gemini_provider)

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
    gemini_model = active_settings.gemini_model or "gemini-3.7-flash"
    model_catalog.register(
        ModelProfile(
            provider="gemini",
            model=gemini_model,
            capabilities=gemini_provider.capabilities,
            priority=75,
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
        circuit_failure_threshold=(
            active_settings.provider_circuit_failure_threshold
        ),
        circuit_recovery_seconds=(
            active_settings.provider_circuit_recovery_seconds
        ),
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

    engine = CoreEngine(
        provider_registry=provider_registry,
        memory_manager=memory_manager,
        tool_executor=tool_executor,
        task_manager=task_manager,
        provider_gateway=provider_gateway,
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
        from app.voice.gemini import (
            GeminiSpeechRecognizer,
            GeminiSpeechSynthesizer,
        )
        from app.voice.providers import (
            OpenAISpeechRecognizer,
            OpenAISpeechSynthesizer,
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

        voice_provider = (
            active_settings.default_provider
            .strip()
            .casefold()
        )

        if voice_provider == "gemini":
            recognizer = GeminiSpeechRecognizer(
                active_settings
            )
            synthesizer = GeminiSpeechSynthesizer(
                active_settings
            )

        elif voice_provider == "openai":
            recognizer = OpenAISpeechRecognizer(
                active_settings
            )
            synthesizer = OpenAISpeechSynthesizer(
                active_settings
            )

        else:
            # Backward-compatible explicit voice wiring.
            #
            # Historically JARVIS allowed the voice service
            # to be constructed while Core was in mock mode.
            # Provider adapters fail closed later if actual
            # speech is attempted without credentials.
            recognizer = OpenAISpeechRecognizer(
                active_settings
            )
            synthesizer = OpenAISpeechSynthesizer(
                active_settings
            )

        voice = VoiceService.create(
            engine=engine,
            audio_input=audio_input,
            audio_output=audio_output,
            recognizer=recognizer,
            synthesizer=synthesizer,
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
        )
        research.register_tools(tool_executor)

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
        voice=voice,
        vision=vision,
        research=research,
    )
