from __future__ import annotations

import math
import os
from dataclasses import dataclass


def _get_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)

    if value is None:
        return default

    normalized = value.strip().lower()
    if normalized in {
        "1",
        "true",
        "yes",
        "on",
    }:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError(
        f"Environment variable '{name}' must be a boolean."
    )


def _get_float(name: str, default: float = 30.0) -> float:
    value = os.getenv(name)

    if value is None:
        return default

    try:
        parsed = float(value)
    except ValueError as exc:
        raise ValueError(
            f"Environment variable '{name}' must be a number."
        ) from exc

    if not math.isfinite(parsed) or parsed < 0:
        raise ValueError(
            f"Environment variable '{name}' must be a finite non-negative number."
        )

    return parsed


def _get_non_negative_int(name: str, default: int = 0) -> int:
    value = os.getenv(name)

    if value is None:
        return default

    try:
        parsed = int(value)
    except ValueError as exc:
        raise ValueError(
            f"Environment variable '{name}' must be an integer."
        ) from exc

    if parsed < 0:
        raise ValueError(
            f"Environment variable '{name}' cannot be negative."
        )

    return parsed


def _get_positive_int(name: str, default: int) -> int:
    parsed = _get_non_negative_int(name, default)
    if parsed < 1:
        raise ValueError(
            f"Environment variable '{name}' must be greater than 0."
        )
    return parsed


@dataclass(frozen=True, slots=True)
class Settings:
    """Runtime configuration loaded from environment variables."""

    app_name: str = "JARVIS"
    environment: str = "development"
    debug: bool = False

    default_provider: str = "mock"
    default_model: str = "mock-model"
    openai_model: str | None = None
    gemini_model: str | None = None
    gemini_reasoning_effort: str = "low"

    provider_timeout_seconds: float = 30.0
    provider_max_retries: int = 2
    provider_retry_backoff_seconds: float = 0.25
    provider_fallback_enabled: bool = True

    conversation_max_messages: int = 50
    conversation_max_characters: int = 50_000
    conversation_summary_max_characters: int = 4_000
    conversation_system_prompt: str | None = None
    conversation_database_path: str | None = None

    memory_database_path: str | None = None
    task_database_path: str | None = None
    task_runtime_directory: str | None = None

    approval_ttl_seconds: float = 300.0
    permission_audit_capacity: int = 1000

    windows_integrations_enabled: bool = True
    windows_launch_verification_timeout_seconds: float = 3.0

    voice_enabled: bool = False
    voice_max_recording_seconds: float = 30.0
    voice_operation_timeout_seconds: float = 60.0
    voice_sample_rate: int = 16_000
    voice_channels: int = 1
    voice_input_device_id: str | None = None
    voice_require_wake_word: bool = False
    voice_wake_word: str = "jarvis"
    voice_language: str | None = None
    voice_stt_model: str = "gpt-4o-mini-transcribe"
    voice_tts_model: str = "gpt-4o-mini-tts"
    voice_tts_voice: str = "alloy"
    voice_tts_instructions: str | None = None
    voice_max_tts_characters: int = 4_000
    voice_max_audio_bytes: int = 20_000_000
    voice_retain_last_audio: bool = False

    vision_enabled: bool = False
    vision_model: str = "gpt-4o"
    vision_detail: str = "high"
    vision_operation_timeout_seconds: float = 60.0
    vision_max_frame_age_seconds: float = 5.0
    vision_consent_ttl_seconds: float = 60.0
    vision_max_width: int = 7680
    vision_max_height: int = 4320
    vision_max_pixels: int = 20_000_000
    vision_max_encoded_bytes: int = 20_000_000
    vision_max_images: int = 4
    vision_redact_taskbar: bool = True
    vision_taskbar_height: int = 64
    vision_retain_last_image: bool = False

    research_enabled: bool = False
    research_searxng_url: str | None = None
    research_allow_http: bool = False
    research_timeout_seconds: float = 10.0
    research_operation_timeout_seconds: float = 45.0
    research_max_response_bytes: int = 2_000_000
    research_max_content_characters: int = 50_000
    research_max_redirects: int = 3
    research_max_sources: int = 5
    research_max_concurrency: int = 3
    research_user_agent: str = "JARVIS/0.1"

    diagnostics_event_capacity: int = 2_000
    diagnostics_metric_capacity: int = 200
    diagnostics_health_timeout_seconds: float = 2.0

    core_max_concurrent_requests: int = 8
    core_max_queued_requests: int = 32
    core_admission_timeout_seconds: float = 2.0
    provider_circuit_failure_threshold: int = 5
    provider_circuit_recovery_seconds: float = 30.0

    api_key: str | None = None
    api_base_url: str | None = None
    gemini_api_key: str | None = None
    gemini_base_url: str = (
        "https://generativelanguage.googleapis.com/v1beta/openai/"
    )

    def __post_init__(self) -> None:
        if not self.default_provider.strip():
            raise ValueError("default_provider cannot be empty.")
        if not self.default_model.strip():
            raise ValueError("default_model cannot be empty.")
        if self.openai_model is not None and not self.openai_model.strip():
            raise ValueError("openai_model cannot be empty when set.")
        if self.gemini_model is not None and not self.gemini_model.strip():
            raise ValueError("gemini_model cannot be empty when set.")
        if self.gemini_reasoning_effort not in {"low", "medium", "high"}:
            raise ValueError(
                "gemini_reasoning_effort must be low, medium, or high."
            )
        if not self.gemini_base_url.strip():
            raise ValueError("gemini_base_url cannot be empty.")
        if self.provider_timeout_seconds <= 0:
            raise ValueError("provider_timeout_seconds must be greater than 0.")
        if self.provider_max_retries < 0:
            raise ValueError("provider_max_retries cannot be negative.")
        if self.provider_retry_backoff_seconds < 0:
            raise ValueError(
                "provider_retry_backoff_seconds cannot be negative."
            )
        if self.conversation_max_messages < 2:
            raise ValueError("conversation_max_messages must be at least 2.")
        if self.conversation_max_characters < 100:
            raise ValueError("conversation_max_characters must be at least 100.")
        if self.conversation_summary_max_characters < 100:
            raise ValueError(
                "conversation_summary_max_characters must be at least 100."
            )
        if self.memory_database_path is not None and not self.memory_database_path.strip():
            raise ValueError("memory_database_path cannot be empty when set.")
        if self.task_database_path is not None and not self.task_database_path.strip():
            raise ValueError("task_database_path cannot be empty when set.")
        if (
            self.task_runtime_directory is not None
            and not self.task_runtime_directory.strip()
        ):
            raise ValueError("task_runtime_directory cannot be empty when set.")
        if self.approval_ttl_seconds <= 0:
            raise ValueError("approval_ttl_seconds must be greater than 0.")
        if self.permission_audit_capacity < 1:
            raise ValueError("permission_audit_capacity must be at least 1.")
        if self.windows_launch_verification_timeout_seconds <= 0:
            raise ValueError(
                "windows_launch_verification_timeout_seconds must be greater than 0."
            )
        if not 0 < self.voice_max_recording_seconds <= 300:
            raise ValueError(
                "voice_max_recording_seconds must be between 0 and 300."
            )
        if self.voice_operation_timeout_seconds <= 0:
            raise ValueError(
                "voice_operation_timeout_seconds must be greater than 0."
            )
        if not 8_000 <= self.voice_sample_rate <= 192_000:
            raise ValueError("voice_sample_rate must be between 8000 and 192000.")
        if self.voice_channels not in {1, 2}:
            raise ValueError("voice_channels must be 1 or 2.")
        if self.voice_input_device_id is not None and not self.voice_input_device_id.strip():
            raise ValueError("voice_input_device_id cannot be empty when set.")
        if not self.voice_wake_word.strip():
            raise ValueError("voice_wake_word cannot be empty.")
        if self.voice_language is not None and not self.voice_language.strip():
            raise ValueError("voice_language cannot be empty when set.")
        for field_name in ("voice_stt_model", "voice_tts_model", "voice_tts_voice"):
            if not getattr(self, field_name).strip():
                raise ValueError(f"{field_name} cannot be empty.")
        if (
            self.voice_tts_instructions is not None
            and not self.voice_tts_instructions.strip()
        ):
            raise ValueError("voice_tts_instructions cannot be empty when set.")
        if self.voice_max_tts_characters < 1:
            raise ValueError("voice_max_tts_characters must be positive.")
        if self.voice_max_audio_bytes < 1:
            raise ValueError("voice_max_audio_bytes must be positive.")
        if not self.vision_model.strip():
            raise ValueError("vision_model cannot be empty.")
        if self.vision_detail not in {"low", "high", "original", "auto"}:
            raise ValueError("vision_detail must be low, high, original, or auto.")
        if min(
            self.vision_operation_timeout_seconds,
            self.vision_max_frame_age_seconds,
            self.vision_consent_ttl_seconds,
        ) <= 0:
            raise ValueError("Vision time limits must be positive.")
        if min(
            self.vision_max_width,
            self.vision_max_height,
            self.vision_max_pixels,
            self.vision_max_encoded_bytes,
            self.vision_max_images,
        ) < 1:
            raise ValueError("Vision image limits must be positive.")
        if self.vision_taskbar_height < 0:
            raise ValueError("vision_taskbar_height cannot be negative.")
        if self.research_enabled and (
            self.research_searxng_url is None
            or not self.research_searxng_url.strip()
        ):
            raise ValueError(
                "research_searxng_url is required when research is enabled."
            )
        if self.research_searxng_url is not None and not self.research_searxng_url.strip():
            raise ValueError("research_searxng_url cannot be empty when set.")
        if min(
            self.research_timeout_seconds,
            self.research_operation_timeout_seconds,
            self.research_max_response_bytes,
            self.research_max_content_characters,
        ) <= 0:
            raise ValueError("Research time and content limits must be positive.")
        if not 0 <= self.research_max_redirects <= 10:
            raise ValueError("research_max_redirects must be between 0 and 10.")
        if not 1 <= self.research_max_sources <= 10:
            raise ValueError("research_max_sources must be between 1 and 10.")
        if not 1 <= self.research_max_concurrency <= 8:
            raise ValueError("research_max_concurrency must be between 1 and 8.")
        if not self.research_user_agent.strip():
            raise ValueError("research_user_agent cannot be empty.")
        if min(
            self.diagnostics_event_capacity,
            self.diagnostics_metric_capacity,
        ) < 1:
            raise ValueError("Diagnostics capacities must be positive.")
        if self.diagnostics_health_timeout_seconds <= 0:
            raise ValueError("diagnostics_health_timeout_seconds must be positive.")
        if self.core_max_concurrent_requests < 1:
            raise ValueError("core_max_concurrent_requests must be positive.")
        if self.core_max_queued_requests < 0:
            raise ValueError("core_max_queued_requests cannot be negative.")
        if self.core_admission_timeout_seconds <= 0:
            raise ValueError("core_admission_timeout_seconds must be positive.")
        if self.provider_circuit_failure_threshold < 1:
            raise ValueError("provider_circuit_failure_threshold must be positive.")
        if self.provider_circuit_recovery_seconds <= 0:
            raise ValueError("provider_circuit_recovery_seconds must be positive.")

    @property
    def openai_api_key(self) -> str | None:
        return self.api_key

    @property
    def openai_base_url(self) -> str | None:
        return self.api_base_url

    @classmethod
    def from_environment(cls) -> Settings:
        return cls(
            app_name=os.getenv("JARVIS_APP_NAME", "JARVIS"),
            environment=os.getenv(
                "JARVIS_ENVIRONMENT",
                "development",
            ),
            debug=_get_bool("JARVIS_DEBUG"),
            default_provider=os.getenv(
                "JARVIS_DEFAULT_PROVIDER",
                "mock",
            ),
            default_model=os.getenv(
                "JARVIS_DEFAULT_MODEL",
                "mock-model",
            ),
            openai_model=os.getenv("JARVIS_OPENAI_MODEL"),
            gemini_model=os.getenv("JARVIS_GEMINI_MODEL"),
            gemini_reasoning_effort=os.getenv(
                "JARVIS_GEMINI_REASONING_EFFORT",
                "low",
            ).strip().lower(),
            provider_timeout_seconds=_get_float(
                "JARVIS_PROVIDER_TIMEOUT",
                30.0,
            ),
            provider_max_retries=_get_non_negative_int(
                "JARVIS_PROVIDER_MAX_RETRIES",
                2,
            ),
            provider_retry_backoff_seconds=_get_float(
                "JARVIS_PROVIDER_RETRY_BACKOFF",
                0.25,
            ),
            provider_fallback_enabled=_get_bool(
                "JARVIS_PROVIDER_FALLBACK",
                True,
            ),
            conversation_max_messages=_get_positive_int(
                "JARVIS_CONVERSATION_MAX_MESSAGES",
                50,
            ),
            conversation_max_characters=_get_positive_int(
                "JARVIS_CONVERSATION_MAX_CHARACTERS",
                50_000,
            ),
            conversation_summary_max_characters=_get_positive_int(
                "JARVIS_CONVERSATION_SUMMARY_MAX_CHARACTERS",
                4_000,
            ),
            conversation_system_prompt=os.getenv(
                "JARVIS_CONVERSATION_SYSTEM_PROMPT"
            ),
            conversation_database_path=os.getenv(
                "JARVIS_CONVERSATION_DATABASE_PATH",
                os.path.join(
                    "data",
                    "jarvis_conversations.sqlite3",
                ),
            ),
            memory_database_path=os.getenv(
                "JARVIS_MEMORY_DATABASE_PATH",
                os.path.join("data", "jarvis_memory.sqlite3"),
            ),
            task_database_path=os.getenv(
                "JARVIS_TASK_DATABASE_PATH",
                os.path.join("data", "jarvis_tasks.sqlite3"),
            ),
            task_runtime_directory=os.getenv(
                "JARVIS_TASK_RUNTIME_DIRECTORY",
                os.path.join("data", "tasks"),
            ),
            approval_ttl_seconds=_get_float(
                "JARVIS_APPROVAL_TTL_SECONDS",
                300.0,
            ),
            permission_audit_capacity=_get_positive_int(
                "JARVIS_PERMISSION_AUDIT_CAPACITY",
                1000,
            ),
            windows_integrations_enabled=_get_bool(
                "JARVIS_WINDOWS_INTEGRATIONS",
                True,
            ),
            windows_launch_verification_timeout_seconds=_get_float(
                "JARVIS_WINDOWS_LAUNCH_VERIFICATION_TIMEOUT",
                3.0,
            ),
            voice_enabled=_get_bool("JARVIS_VOICE_ENABLED"),
            voice_max_recording_seconds=_get_float(
                "JARVIS_VOICE_MAX_RECORDING_SECONDS",
                30.0,
            ),
            voice_operation_timeout_seconds=_get_float(
                "JARVIS_VOICE_OPERATION_TIMEOUT_SECONDS",
                60.0,
            ),
            voice_sample_rate=_get_positive_int(
                "JARVIS_VOICE_SAMPLE_RATE",
                16_000,
            ),
            voice_channels=_get_positive_int("JARVIS_VOICE_CHANNELS", 1),
            voice_input_device_id=os.getenv("JARVIS_VOICE_INPUT_DEVICE_ID"),
            voice_require_wake_word=_get_bool(
                "JARVIS_VOICE_REQUIRE_WAKE_WORD"
            ),
            voice_wake_word=os.getenv("JARVIS_VOICE_WAKE_WORD", "jarvis"),
            voice_language=os.getenv("JARVIS_VOICE_LANGUAGE"),
            voice_stt_model=os.getenv(
                "JARVIS_VOICE_STT_MODEL",
                "gpt-4o-mini-transcribe",
            ),
            voice_tts_model=os.getenv(
                "JARVIS_VOICE_TTS_MODEL",
                "gpt-4o-mini-tts",
            ),
            voice_tts_voice=os.getenv("JARVIS_VOICE_TTS_VOICE", "alloy"),
            voice_tts_instructions=os.getenv("JARVIS_VOICE_TTS_INSTRUCTIONS"),
            voice_max_tts_characters=_get_positive_int(
                "JARVIS_VOICE_MAX_TTS_CHARACTERS",
                4_000,
            ),
            voice_max_audio_bytes=_get_positive_int(
                "JARVIS_VOICE_MAX_AUDIO_BYTES",
                20_000_000,
            ),
            voice_retain_last_audio=_get_bool("JARVIS_VOICE_RETAIN_LAST_AUDIO"),
            vision_enabled=_get_bool("JARVIS_VISION_ENABLED"),
            vision_model=os.getenv("JARVIS_VISION_MODEL", "gpt-4o"),
            vision_detail=os.getenv("JARVIS_VISION_DETAIL", "high"),
            vision_operation_timeout_seconds=_get_float(
                "JARVIS_VISION_OPERATION_TIMEOUT_SECONDS", 60.0
            ),
            vision_max_frame_age_seconds=_get_float(
                "JARVIS_VISION_MAX_FRAME_AGE_SECONDS", 5.0
            ),
            vision_consent_ttl_seconds=_get_float(
                "JARVIS_VISION_CONSENT_TTL_SECONDS", 60.0
            ),
            vision_max_width=_get_positive_int("JARVIS_VISION_MAX_WIDTH", 7680),
            vision_max_height=_get_positive_int("JARVIS_VISION_MAX_HEIGHT", 4320),
            vision_max_pixels=_get_positive_int(
                "JARVIS_VISION_MAX_PIXELS", 20_000_000
            ),
            vision_max_encoded_bytes=_get_positive_int(
                "JARVIS_VISION_MAX_ENCODED_BYTES", 20_000_000
            ),
            vision_max_images=_get_positive_int("JARVIS_VISION_MAX_IMAGES", 4),
            vision_redact_taskbar=_get_bool("JARVIS_VISION_REDACT_TASKBAR", True),
            vision_taskbar_height=_get_non_negative_int(
                "JARVIS_VISION_TASKBAR_HEIGHT", 64
            ),
            vision_retain_last_image=_get_bool("JARVIS_VISION_RETAIN_LAST_IMAGE"),
            research_enabled=_get_bool("JARVIS_RESEARCH_ENABLED"),
            research_searxng_url=os.getenv("JARVIS_RESEARCH_SEARXNG_URL"),
            research_allow_http=_get_bool("JARVIS_RESEARCH_ALLOW_HTTP"),
            research_timeout_seconds=_get_float(
                "JARVIS_RESEARCH_TIMEOUT_SECONDS", 10.0
            ),
            research_operation_timeout_seconds=_get_float(
                "JARVIS_RESEARCH_OPERATION_TIMEOUT_SECONDS", 45.0
            ),
            research_max_response_bytes=_get_positive_int(
                "JARVIS_RESEARCH_MAX_RESPONSE_BYTES", 2_000_000
            ),
            research_max_content_characters=_get_positive_int(
                "JARVIS_RESEARCH_MAX_CONTENT_CHARACTERS", 50_000
            ),
            research_max_redirects=_get_non_negative_int(
                "JARVIS_RESEARCH_MAX_REDIRECTS", 3
            ),
            research_max_sources=_get_positive_int(
                "JARVIS_RESEARCH_MAX_SOURCES", 5
            ),
            research_max_concurrency=_get_positive_int(
                "JARVIS_RESEARCH_MAX_CONCURRENCY", 3
            ),
            research_user_agent=os.getenv(
                "JARVIS_RESEARCH_USER_AGENT", "JARVIS/0.1"
            ),
            diagnostics_event_capacity=_get_positive_int(
                "JARVIS_DIAGNOSTICS_EVENT_CAPACITY", 2_000
            ),
            diagnostics_metric_capacity=_get_positive_int(
                "JARVIS_DIAGNOSTICS_METRIC_CAPACITY", 200
            ),
            diagnostics_health_timeout_seconds=_get_float(
                "JARVIS_DIAGNOSTICS_HEALTH_TIMEOUT_SECONDS", 2.0
            ),
            core_max_concurrent_requests=_get_positive_int(
                "JARVIS_CORE_MAX_CONCURRENT_REQUESTS", 8
            ),
            core_max_queued_requests=_get_non_negative_int(
                "JARVIS_CORE_MAX_QUEUED_REQUESTS", 32
            ),
            core_admission_timeout_seconds=_get_float(
                "JARVIS_CORE_ADMISSION_TIMEOUT_SECONDS", 2.0
            ),
            provider_circuit_failure_threshold=_get_positive_int(
                "JARVIS_PROVIDER_CIRCUIT_FAILURE_THRESHOLD", 5
            ),
            provider_circuit_recovery_seconds=_get_float(
                "JARVIS_PROVIDER_CIRCUIT_RECOVERY_SECONDS", 30.0
            ),
            api_key=os.getenv("JARVIS_API_KEY"),
            api_base_url=os.getenv(
                "JARVIS_API_BASE_URL",
                None,
            ),
            gemini_api_key=os.getenv("JARVIS_GEMINI_API_KEY"),
            gemini_base_url=os.getenv(
                "JARVIS_GEMINI_BASE_URL",
                "https://generativelanguage.googleapis.com/v1beta/openai/",
            ),
        )

