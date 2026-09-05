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

Gemini is the only production provider. `mock` is a deterministic offline
provider used by the automated tests; it is registered only when
`JARVIS_DEFAULT_PROVIDER` is explicitly set to `mock`, and it is never
selectable from the desktop Settings screen.

| Variable | Default | Purpose |
| --- | --- | --- |
| `JARVIS_DEFAULT_PROVIDER` | `gemini` | Default registered provider. Only `gemini` is supported for normal use; `mock` additionally registers the offline test provider. |
| `JARVIS_DEFAULT_MODEL` | `gemini-3.5-flash-lite` | Default model for the selected provider. |
| `JARVIS_GEMINI_MODEL` | unset | Gemini model override. |
| `JARVIS_PROVIDER_TIMEOUT` | `15` | Per-attempt timeout in seconds. |
| `JARVIS_PROVIDER_MAX_RETRIES` | `1` | Maximum retries after the first attempt. |
| `JARVIS_PROVIDER_RETRY_BACKOFF` | `0.25` | Initial exponential backoff in seconds. |

Provider fallback is disabled. With a single production provider there is
nothing to fall back to, and falling back to `mock` would hide a real failure.
Authentication, model-access, quota, and availability failures stay visible to
the user instead of being replaced by a misleading mock echo.

## Gemini connection

Gemini is reached through Google's OpenAI-compatible API surface, which lets
JARVIS keep one provider-neutral conversation and tool contract while
preserving Gemini's own identity and classified errors. `app/providers/openai.py`
therefore remains in the tree as the shared adapter base class that
`GeminiProvider` extends; there is no separately registered OpenAI provider.

| Variable | Default | Purpose |
| --- | --- | --- |
| `JARVIS_GEMINI_API_KEY` | unset | Gemini API credential. |
| `JARVIS_GEMINI_MODEL` | unset | Gemini model override. |
| `JARVIS_GEMINI_BASE_URL` | `https://generativelanguage.googleapis.com/v1beta/openai/` | Compatible Gemini endpoint. |

The desktop Settings screen stores the Gemini credential in the current user's
Windows Credential Manager under `JARVIS/Gemini API`. The secret is never
written to the project, the non-secret settings JSON, diagnostics, or logs.
Model preferences are stored separately in
`%LOCALAPPDATA%\JARVIS\settings.json`. Explicit environment variables retain
precedence over desktop preferences.

Enter the model name, test the connection, then save and activate it.

Deleting the stored key from the Settings screen requires confirming an in-app
dialog; the shell's bridge also refuses a deletion that was not explicitly
confirmed.

## Desktop shell

`python -m app.ui` (and the packaged `JARVIS.exe`) opens the Nova shell, a
pywebview window rendered by the Microsoft Edge WebView2 Runtime that ships
with Windows 11. `--classic` opens the Tkinter shell instead, which is also
used automatically when pywebview is not installed or no WebView2 Runtime is
detected; the chat then shows a Turkish notice and the diagnostics ledger
records a `nova.unavailable` warning.

| Location | Purpose |
| --- | --- |
| `%LOCALAPPDATA%\JARVIS\webview` | WebView2 profile holding the page's own preferences (theme, "Hareketi azalt"). Follows `JARVIS_STATE_DIRECTORY` when that override is set. |

The page never falls back to sample data. Opening `app/ui/nova/web/index.html`
in a plain browser with `?demo=1` shows a clearly labelled demo for visual
work only; without the parameter the page reports that the core bridge is
missing.

### System tray and single instance

| Variable | Default | Purpose |
| --- | --- | --- |
| `JARVIS_TRAY_ENABLED` | `true` | Show the notification-area icon with Aç, Duraklat/Devam, Tanılama, Ayarlar, Çıkış (Nova shell, Windows). |
| `JARVIS_TRAY_CLOSE_TO_TRAY` | `true` | Closing the window hides it to the tray; `Çıkış` in the tray menu exits. Set to `false` to make the close button exit. |
| `JARVIS_SINGLE_INSTANCE` | `true` | One desktop per user session; a second launch brings the running window forward and exits. |

`Duraklat` refuses new commands, voice, vision, and research requests and
stops an active voice session until `Devam`; reminders and other background
services keep running. The classic Tkinter shell has no tray icon.

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

## Durable state storage

All durable state defaults to the per-user application data directory
`%LOCALAPPDATA%\JARVIS` (or `~/.jarvis` when `LOCALAPPDATA` is unavailable).
`JARVIS_STATE_DIRECTORY` relocates that root; the per-store variables override
individual files.

| Variable | Default | Purpose |
| --- | --- | --- |
| `JARVIS_STATE_DIRECTORY` | `%LOCALAPPDATA%\JARVIS` | Root directory for all durable runtime state. |
| `JARVIS_CONVERSATION_DATABASE_PATH` | `<state>/jarvis_conversations.sqlite3` | SQLite database for durable conversation history. |
| `JARVIS_MEMORY_DATABASE_PATH` | `<state>/jarvis_memory.sqlite3` | SQLite database used for durable long-term memory. |
| `JARVIS_RESEARCH_CACHE_DATABASE_PATH` | `<state>/jarvis_research.sqlite3` | SQLite cache for web-research retrievals. |

The configured parent directory is created when needed. Runtime SQLite files
are excluded from Git. Backups should be stored separately from the live
database and restored only after their integrity check succeeds.

## Durable tasks

| Variable | Default | Purpose |
| --- | --- | --- |
| `JARVIS_TASK_DATABASE_PATH` | `<state>/jarvis_tasks.sqlite3` | SQLite database for task and task-step state. |
| `JARVIS_TASK_RUNTIME_DIRECTORY` | `<state>/tasks` | Per-task atomic plan and execution snapshot directory. |

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
capture. Windows WAV output uses the standard library. Gemini provides both
speech recognition and speech synthesis and uses the same API connection
settings documented above.

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
| `JARVIS_VOICE_LANGUAGE` | `tr` | ISO language hint for recognition; keeps Turkish speech from being transcribed in other languages. |
| `JARVIS_VOICE_GEMINI_STT_MODEL` | `gemini-3.5-flash-lite` | Speech recognition model. |
| `JARVIS_VOICE_GEMINI_TTS_MODEL` | `gemini-3.1-flash-tts-preview` | Speech synthesis model. |
| `JARVIS_VOICE_GEMINI_TTS_VOICE` | `Charon` | Speech synthesis voice (user-chosen; the single voice of JARVIS). |
| `JARVIS_VOICE_TTS_INSTRUCTIONS` | JARVIS persona | Speaking-style instruction prepended to every synthesis. |
| `JARVIS_VOICE_CLOUD_GRACE_SECONDS` | `3.0` | Head start the cloud voice gets before the local voice may speak; `0` restores the pure latency race. |
| `JARVIS_VOICE_TRAILING_SILENCE_SECONDS` | `1.5` | Silence that ends the user's turn. |
| `JARVIS_MEMORY_AUTO_CAPTURE` | `true` | Post-turn model pass that stores durable personal facts automatically. |
| `JARVIS_MEMORY_EXTRACTION_MODEL` | `gemini-3.5-flash-lite` | Model used by automatic memory capture. |
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
| `JARVIS_VISION_MODEL` | unset | Optional dedicated Gemini vision model. When set and different from the general model, `VISION` requests route to it exclusively; when unset, vision uses the general model. |
| `JARVIS_VISION_DETAIL` | `high` | Image detail: `low`, `high`, `original`, or `auto`. |
| `JARVIS_VISION_OPERATION_TIMEOUT_SECONDS` | `60` | Timeout for each capture or analysis operation. |
| `JARVIS_VISION_MAX_FRAME_AGE_SECONDS` | `5` | Maximum age before a captured frame is rejected as stale. |
| `JARVIS_VISION_CONSENT_TTL_SECONDS` | `60` | Lifetime of a one-use capture consent request. |
| `JARVIS_VISION_MAX_WIDTH` | `7680` | Maximum virtual-screen width. |
| `JARVIS_VISION_MAX_HEIGHT` | `4320` | Maximum virtual-screen height. |
| `JARVIS_VISION_MAX_PIXELS` | `20000000` | Maximum pixels allocated for one capture. |
| `JARVIS_VISION_MAX_ENCODED_BYTES` | `20000000` | Maximum processed image payload. |
| `JARVIS_VISION_MAX_IMAGES` | `4` | Maximum image inputs accepted by the provider adapter. |
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

## Plugins

Plugins are off unless `JARVIS_PLUGINS_ENABLED` is true, and each discovered
plugin additionally stays disabled until it is enabled through
`PluginRuntime.enable()` (the decision is stored in `state.json` inside the
plugins directory).

| Variable | Default | Purpose |
| --- | --- | --- |
| `JARVIS_PLUGINS_ENABLED` | `false` | Discover and run plugins from the plugins directory. |
| `JARVIS_PLUGINS_DIRECTORY` | `%LOCALAPPDATA%\JARVIS\plugins` | Trusted root; only its immediate subdirectories are considered. |
| `JARVIS_PLUGIN_TOOL_TIMEOUT_SECONDS` | `10` | Per-call deadline for a plugin tool (0 < value <= 120). |
| `JARVIS_PLUGIN_MAX_CONSECUTIVE_FAILURES` | `3` | Consecutive failures after which a plugin is quarantined (1-10). |

A plugin directory contains `plugin.json` and the entry module it names; see
`docs/DEVELOPMENT.md` for the manifest format and `tests/fixtures/plugins/echo`
for a complete safe example.

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
