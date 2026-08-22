# Configuration

JARVIS reads runtime configuration from environment variables. Secrets must not
be committed to source control or written to logs.

## General settings

| Variable | Default | Purpose |
| --- | --- | --- |
| `JARVIS_APP_NAME` | `JARVIS` | Application display name. |
| `JARVIS_ENVIRONMENT` | `development` | Runtime environment label. |
| `JARVIS_DEBUG` | `false` | Enables development diagnostics. |

## Provider gateway

| Variable | Default | Purpose |
| --- | --- | --- |
| `JARVIS_DEFAULT_PROVIDER` | `mock` | Default registered provider. |
| `JARVIS_DEFAULT_MODEL` | `mock-model` | Default model for the selected provider. |
| `JARVIS_OPENAI_MODEL` | unset | OpenAI-specific model override. |
| `JARVIS_GEMINI_MODEL` | unset | Gemini-specific model override. |
| `JARVIS_PROVIDER_TIMEOUT` | `30` | Per-attempt timeout in seconds. |
| `JARVIS_PROVIDER_MAX_RETRIES` | `2` | Maximum retries after the first attempt. |
| `JARVIS_PROVIDER_RETRY_BACKOFF` | `0.25` | Initial exponential backoff in seconds. |
| `JARVIS_PROVIDER_FALLBACK` | `true` | Allows fallback when no user override is active. |

`JARVIS_OPENAI_MODEL` should be configured when OpenAI is enabled while another
provider uses a different default model name.

The development-only `mock` provider is never used as a fallback for a live
provider. Authentication, model-access, quota, and availability failures remain
visible to the user instead of being replaced by a misleading mock echo.

## OpenAI connection

| Variable | Default | Purpose |
| --- | --- | --- |
| `JARVIS_API_KEY` | unset | OpenAI-compatible API credential. |
| `JARVIS_API_BASE_URL` | unset | Optional compatible API base URL. |

With no API key, the OpenAI adapter remains registered but reports a classified
configuration error if selected. The default mock provider continues to work
offline.

The desktop Settings screen can store the API credential in the current user's
Windows Credential Manager under `JARVIS/OpenAI API`. The secret is never
written to the project, the non-secret settings JSON, diagnostics, or logs.
Provider and model preferences are stored separately in
`%LOCALAPPDATA%\JARVIS\settings.json`. Explicit environment variables retain
precedence over desktop preferences.

## Gemini connection

Gemini uses Google's OpenAI-compatible API surface, allowing JARVIS to keep
one provider-neutral conversation and tool contract while preserving Gemini's
own identity and classified errors.

| Variable | Default | Purpose |
| --- | --- | --- |
| `JARVIS_GEMINI_API_KEY` | unset | Gemini API credential. |
| `JARVIS_GEMINI_MODEL` | unset | Gemini model override. |
| `JARVIS_GEMINI_BASE_URL` | `https://generativelanguage.googleapis.com/v1beta/openai/` | Compatible Gemini endpoint. |

The desktop stores Gemini credentials separately under `JARVIS/Gemini API`.
Select `gemini`, use `gemini-3.7-flash`, test the connection, then save and
activate it. Removing a key affects only the currently selected provider.

## Conversation context

| Variable | Default | Purpose |
| --- | --- | --- |
| `JARVIS_CONVERSATION_MAX_MESSAGES` | `50` | Maximum recent conversational turns before grouping and summarization. |
| `JARVIS_CONVERSATION_MAX_CHARACTERS` | `50000` | Character budget for recent turns and summary. |
| `JARVIS_CONVERSATION_SUMMARY_MAX_CHARACTERS` | `4000` | Maximum generated summary size. |
| `JARVIS_CONVERSATION_SYSTEM_PROMPT` | unset | Optional system instruction prepended to provider context. |

Conversation limits preserve the complete most recent request/tool group even
when that single group is larger than the configured normal window. This avoids
creating an invalid partial tool-call chain.

## Durable memory

| Variable | Default | Purpose |
| --- | --- | --- |
| `JARVIS_MEMORY_DATABASE_PATH` | `data/jarvis_memory.sqlite3` | SQLite database used for durable long-term memory. |

The configured parent directory is created when needed. Runtime SQLite files
are excluded from Git. Backups should be stored separately from the live
database and restored only after their integrity check succeeds.

## Durable tasks

| Variable | Default | Purpose |
| --- | --- | --- |
| `JARVIS_TASK_DATABASE_PATH` | `data/jarvis_tasks.sqlite3` | SQLite database for task and task-step state. |
| `JARVIS_TASK_RUNTIME_DIRECTORY` | `data/tasks` | Per-task atomic plan and execution snapshot directory. |

Task database files and runtime directories are excluded from Git. A restarted
application marks interrupted running work as paused and requires an explicit
resume operation. Completed steps are not repeated during recovery.

## Permission and approval security

| Variable | Default | Purpose |
| --- | --- | --- |
| `JARVIS_APPROVAL_TTL_SECONDS` | `300` | Lifetime of an action-bound approval request. |
| `JARVIS_PERMISSION_AUDIT_CAPACITY` | `1000` | Maximum in-memory permission decisions retained for diagnostics. |

Approval TTL values must be positive and finite. The audit buffer is bounded
and contains decision metadata, not tool parameter values.

## Windows integrations

| Variable | Default | Purpose |
| --- | --- | --- |
| `JARVIS_WINDOWS_INTEGRATIONS` | `true` | Registers native Windows tools when running on Windows. |
| `JARVIS_WINDOWS_LAUNCH_VERIFICATION_TIMEOUT` | `3` | Maximum seconds to observe and verify a newly launched process. |

Disabling Windows integrations leaves provider, conversation, memory, and
other local runtime features available without registering Windows tools.

## Voice pipeline

Voice is opt-in and requires the `voice` dependency extra for microphone
capture. Windows WAV output uses the standard library. OpenAI speech adapters
use the same API connection settings documented above.

| Variable | Default | Purpose |
| --- | --- | --- |
| `JARVIS_VOICE_ENABLED` | `false` | Enables voice construction at application startup. |
| `JARVIS_VOICE_MAX_RECORDING_SECONDS` | `30` | Hard limit for one microphone capture; maximum `300`. |
| `JARVIS_VOICE_OPERATION_TIMEOUT_SECONDS` | `60` | Timeout for each capture, provider, Core, or playback stage. |
| `JARVIS_VOICE_SAMPLE_RATE` | `16000` | PCM sample rate from `8000` through `192000`. |
| `JARVIS_VOICE_CHANNELS` | `1` | Microphone channels: `1` or `2`. |
| `JARVIS_VOICE_INPUT_DEVICE_ID` | unset | Optional numeric `sounddevice` input ID. |
| `JARVIS_VOICE_REQUIRE_WAKE_WORD` | `false` | Ignores transcripts without the exact wake word. |
| `JARVIS_VOICE_WAKE_WORD` | `jarvis` | Case-insensitive, whole-word activation text. |
| `JARVIS_VOICE_LANGUAGE` | unset | Optional ISO language hint for recognition. |
| `JARVIS_VOICE_STT_MODEL` | `gpt-4o-mini-transcribe` | Speech recognition model. |
| `JARVIS_VOICE_TTS_MODEL` | `gpt-4o-mini-tts` | Speech synthesis model. |
| `JARVIS_VOICE_TTS_VOICE` | `alloy` | Speech synthesis voice. |
| `JARVIS_VOICE_TTS_INSTRUCTIONS` | unset | Optional speaking-style instruction. |
| `JARVIS_VOICE_MAX_TTS_CHARACTERS` | `4000` | Maximum text length sent for synthesis. |
| `JARVIS_VOICE_MAX_AUDIO_BYTES` | `20000000` | Maximum accepted synthesized audio response. |
| `JARVIS_VOICE_RETAIN_LAST_AUDIO` | `false` | Retains the last raw capture in memory until explicitly cleared. |

Audio is never written to disk by the voice layer. Leave retention disabled
unless an embedding application has a specific, disclosed need for the raw
capture and clears it after use.

## Vision pipeline

Vision is opt-in and currently uses native Windows virtual-screen capture.
Every analysis still requires a separately approved, short-lived consent grant.

| Variable | Default | Purpose |
| --- | --- | --- |
| `JARVIS_VISION_ENABLED` | `false` | Constructs the vision service at application startup. |
| `JARVIS_VISION_MODEL` | `gpt-4o` | Dedicated vision-capable OpenAI model profile. |
| `JARVIS_VISION_DETAIL` | `high` | Image detail: `low`, `high`, `original`, or `auto`. |
| `JARVIS_VISION_OPERATION_TIMEOUT_SECONDS` | `60` | Timeout for each capture or analysis operation. |
| `JARVIS_VISION_MAX_FRAME_AGE_SECONDS` | `5` | Maximum age before a captured frame is rejected as stale. |
| `JARVIS_VISION_CONSENT_TTL_SECONDS` | `60` | Lifetime of a one-use capture consent request. |
| `JARVIS_VISION_MAX_WIDTH` | `7680` | Maximum virtual-screen width. |
| `JARVIS_VISION_MAX_HEIGHT` | `4320` | Maximum virtual-screen height. |
| `JARVIS_VISION_MAX_PIXELS` | `20000000` | Maximum pixels allocated for one capture. |
| `JARVIS_VISION_MAX_ENCODED_BYTES` | `20000000` | Maximum processed image payload. |
| `JARVIS_VISION_MAX_IMAGES` | `4` | Maximum image inputs accepted by the OpenAI adapter. |
| `JARVIS_VISION_REDACT_TASKBAR` | `true` | Blacks out the configured bottom taskbar band. |
| `JARVIS_VISION_TASKBAR_HEIGHT` | `64` | Height in pixels of the automatic bottom privacy mask. |
| `JARVIS_VISION_RETAIN_LAST_IMAGE` | `false` | Retains the processed PNG in memory until explicitly cleared. |

The capture layer does not write images to disk. User-selected redaction
coordinates are relative to the captured frame and must be fully inside it;
invalid regions fail closed rather than being silently clipped.

## Web research

Web research is opt-in. Configure a SearXNG instance whose JSON output format is
enabled. HTTPS is required by default; enabling HTTP does not weaken private or
local address rejection.

| Variable | Default | Purpose |
| --- | --- | --- |
| `JARVIS_RESEARCH_ENABLED` | `false` | Registers the read-only research tool at startup. |
| `JARVIS_RESEARCH_SEARXNG_URL` | unset | Base URL of the configured SearXNG service; required when enabled. |
| `JARVIS_RESEARCH_ALLOW_HTTP` | `false` | Explicitly permits plain HTTP while retaining all address checks. |
| `JARVIS_RESEARCH_TIMEOUT_SECONDS` | `10` | Timeout for one HTTP request. |
| `JARVIS_RESEARCH_OPERATION_TIMEOUT_SECONDS` | `45` | Tool-level research deadline. |
| `JARVIS_RESEARCH_MAX_RESPONSE_BYTES` | `2000000` | Maximum bytes accepted from one response. |
| `JARVIS_RESEARCH_MAX_CONTENT_CHARACTERS` | `50000` | Maximum extracted characters per source. |
| `JARVIS_RESEARCH_MAX_REDIRECTS` | `3` | Maximum manually validated redirects; range `0` through `10`. |
| `JARVIS_RESEARCH_MAX_SOURCES` | `5` | Maximum source pages in one report; range `1` through `10`. |
| `JARVIS_RESEARCH_MAX_CONCURRENCY` | `3` | Maximum simultaneous source fetches; range `1` through `8`. |
| `JARVIS_RESEARCH_USER_AGENT` | `JARVIS/0.1` | Non-secret HTTP user-agent identifier. |

## Diagnostics

Diagnostics are always available in memory and expose read-only health, event,
and metric tools. Capacities are intentionally bounded; increasing them raises
the maximum process memory retained for observability.

| Variable | Default | Purpose |
| --- | --- | --- |
| `JARVIS_DIAGNOSTICS_EVENT_CAPACITY` | `2000` | Maximum sanitized events retained in the hash-chained window. |
| `JARVIS_DIAGNOSTICS_METRIC_CAPACITY` | `200` | Maximum unique counter, gauge, and timer names. |
| `JARVIS_DIAGNOSTICS_HEALTH_TIMEOUT_SECONDS` | `2` | Per-component live health-check timeout. |

## Reliability and overload control

| Variable | Default | Purpose |
| --- | --- | --- |
| `JARVIS_CORE_MAX_CONCURRENT_REQUESTS` | `8` | Maximum Core requests executing simultaneously. |
| `JARVIS_CORE_MAX_QUEUED_REQUESTS` | `32` | Maximum additional requests waiting for Core admission. |
| `JARVIS_CORE_ADMISSION_TIMEOUT_SECONDS` | `2` | Maximum wait for a Core execution lease. |
| `JARVIS_PROVIDER_CIRCUIT_FAILURE_THRESHOLD` | `5` | Consecutive retryable failures before opening a provider circuit. |
| `JARVIS_PROVIDER_CIRCUIT_RECOVERY_SECONDS` | `30` | Open interval before one half-open provider probe. |
