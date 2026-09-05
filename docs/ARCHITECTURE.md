# JARVIS Architecture

## Purpose

JARVIS is a modular, production-oriented personal AI assistant. Its runtime is
organized around explicit boundaries for providers, memory, planning, task
tracking, security, tool execution, verification, and persistence.

## Core runtime flow

The Phase 2 runtime uses this bounded flow:

```text
Request
  -> Context and memory
  -> Provider or validated plan
  -> Approval-bound tool execution
  -> Result verification
  -> Task and plan state propagation
  -> Response with outcome and usage metadata
```

`CoreEngine` is the composition root and public orchestration entry point. It
owns one shared `ExecutionService` instance. Direct requests, plans, and tracked
tasks therefore use the same tool executor, verification policy, execution
limits, state store, and event bus.

## Responsibilities

- `CoreEngine` composes dependencies, enriches request context, runs the bounded
  provider/tool loop, and exposes plan and task execution.
- `AgentLoop` selects direct or task mode and maps verified Core outcomes to
  agent terminal states.
- `TaskExecutionService` adapts plan lifecycle events to tracked task and task
  step states. It does not create a second execution runtime.
- `ExecutionService` executes plan steps, checks approval grants, verifies tool
  results, applies retries and limits, persists snapshots, and publishes
  lifecycle events.
- `ToolExecutor` enforces strict tool contracts, owns per-tool concurrency
  admission and provider schema generation, and returns structured terminal
  results.
- `VerificationEngine` is the only component that decides whether a tool result
  can complete a step.
- `TaskManager` owns legal task and task-step state transitions.

## Identity and traceability

`request_id`, `conversation_id`, `task_id`, `plan_id`, and the current step
identity travel in `ExecutionContext`. Plan lifecycle events are emitted from
the shared service, and persisted snapshots include terminal usage metadata.

## Bounded execution

`ExecutionLimits` applies hard per-execution limits for:

- plan steps;
- tool calls, including retry attempts;
- provider/model iterations;
- model tokens;
- elapsed execution time.

Budget exhaustion is a terminal, unverified outcome. It is never reported as a
successful completion.

## Terminal outcomes

The runtime distinguishes `completed`, `failed`, `cancelled`, `partial`, and
`budget_exhausted` outcomes. A plan step completes only after verification
passes. Partial tool data is retained without treating the operation as
complete, and unsafe-to-retry timeouts are not repeated.

Tracked tasks retain plan identity, progress, outcome, partial data when
present, and execution usage. Cancellation is propagated to every active or
queued task step and produces one plan terminal event.

## Dependency direction

Low-level execution code does not import the agent orchestration layer.
Approval binding primitives live in `app.security`, while the agent layer owns
approval request workflow and policy decisions. This keeps the Core import
graph acyclic and lets execution be tested in isolation.

## AI provider gateway

Phase 3 introduces `ProviderGateway` as the only model-provider entry point used
by `CoreEngine`. Provider adapters perform one vendor call and translate vendor
data into strict `ModelResponse` or `ModelStreamChunk` contracts. The gateway
owns routing, capability validation, timeout, retry, cancellation, health
accounting, and provider metadata. Cross-provider fallback is intentionally
disabled: Gemini is the only production provider, and falling back to the
offline mock provider would hide a real failure.

`ModelRouter` classifies work as simple, standard, complex, long-running,
vision, or agentic. It combines the task type with required capabilities such
as tool calling, streaming, structured output, and vision. Explicit user model
or provider overrides are honored but do not silently fall back.

`ModelCatalog` stores dynamic model profiles, including task support, priority,
context capacity, and optional pricing. Providers without catalog profiles can
still be discovered by declared capability, allowing integrations to be added
without changes to Core.

Only classified transient failures are retried. Streaming can retry or fall
back before the first emitted chunk; after output is visible, an error is
reported without restarting the stream and duplicating content. Unexpected
provider exceptions are sanitized at the gateway boundary.

**Gemini 3 function calls and streaming** (5 September 2026). Gemini 3
models require every replayed function call to carry a thought signature.
`OpenAIProvider` keeps the signature the API returns (`extra_content`) on
the tool-call dictionaries that flow through the engine and the
conversation store, and `GeminiProvider` signs any call that has none (a
core-injected deterministic call, or an older stored one) with Google's
skip marker on the wire copy only. The core's streaming collector folds
tool-call deltas into whole calls, so tool-bearing turns stream their
final answer; the finalization call after tool results asks the gateway
for the `simple` task type. Escalation to a separate action model is
opt-in; when configured and rate limited, the gateway honours a
single-attempt request flag, the default model answers immediately and
the action model enters a doubling cooldown. Each provider round trip is
recorded as `request.model_call` with latency and first-output timers.

## Conversation engine

Phase 4 introduces `ConversationEngine` as the owner of conversation lifecycle
and provider message history. Conversations contain validated, timestamped
turns with request, response, and tool-call identity. The Core records the user
turn before generation, every assistant/tool exchange during execution, and the
final assistant response.

The engine builds context from complete request groups so a tool result is
never separated from its assistant tool call. Older groups are represented by
a bounded local summary while the store retains the full turn history. Summary
metadata records how many turns were compacted and when the summary changed.

Conversation storage is behind `ConversationStore`; the current bootstrap uses
the copy-isolated, thread-safe in-memory implementation. Active conversations
can be archived, reactivated, listed, or deleted without coupling providers to
storage. Provider adapters receive normalized history and do not own lifecycle
state.

## Tool runtime

Phase 5 defines every tool through one provider-neutral `ToolContract`. The
contract includes version, input and output schemas, risk and approval rules,
timeout, retry policy, concurrency limit, capabilities, tags, source, and
extension metadata. Provider-specific function schemas are derived from this
contract instead of maintained separately.

`ToolRegistry` supports runtime registration, removal, enable/disable lifecycle
changes, and a monotonic revision for cache invalidation. Discovery can be
restricted by exact tool names, required capabilities, and tags. Disabled
tools are hidden from model providers and rejected by execution.

`CoreEngine` can scope the tools exposed for one request with
`allowed_tools`, `tool_capabilities`, and `tool_tags` metadata. Malformed
filters fail closed and expose no tools. Tool-specific retries can only be
declared for idempotent tools, and the shared execution service continues to
count every attempt against the execution budget.

`ToolExecutor.subscribe` registers read-only execution observers: each call
emits a `started` event before permission evaluation and a `finished` event
carrying the final `ToolResult`. Observers run synchronously, cannot change
the outcome, and a failing observer is ignored. The Nova shell uses this to
show live tool activity.

## Permission and approval engine

Phase 6 centralizes permission decisions in `PermissionEngine`. A validated
`PermissionPolicy` classifies every risk level exactly once. Parameter rules
may elevate effective risk or force confirmation/denial, while
`PermissionScope` adds per-execution least-privilege limits. Decisions are
immutable, carry evaluation identity and policy revision, and enter a bounded
audit buffer.

`ApprovalGate` resolves the real registered tool definition before deciding
whether a plan step can run, so plan metadata cannot downgrade tool risk.
Critical tools denied by policy never create misleading approval requests.
Approved operations produce grants bound to operation, tool version,
parameters, task, plan, step, and expiry.

Grant verification occurs again at the `ToolExecutor` boundary rather than
being reduced to a boolean in the execution service. Approval requests are
immutable and their in-memory store serializes state transitions, including
concurrent approval, denial, and expiry.

## Plugin runtime

`app/plugins/` adds manifest-based plugins that contribute tools and nothing
else. A plugin is a directory below the trusted plugins root
(`%LOCALAPPDATA%\JARVIS\plugins\<id>` by default) holding `plugin.json` and
the entry module it names. `manifest.py` validates the manifest strictly
before any plugin code is imported: versioned schema, closed field set, id
and version formats, an entry point confined to the plugin directory, at
most 32 tools with at most 16 typed parameters each, and a risk floor of
`LOW` that a plugin can raise but never lower (`critical` is rejected
because policy denies it anyway). `discovery.py` scans only immediate
subdirectories, refuses symlinks and junctions, and reports a broken
manifest per plugin without affecting the others.

`PluginRuntime` owns the lifecycle. Plugins are disabled by default and the
user's `enable`/`disable` decision is persisted in `state.json` next to the
plugins. Starting a plugin imports its entry module under the
`jarvis_plugins.<id>` namespace, calls `create_plugin(context)`, and registers
every declared tool on the shared `ToolExecutor` as `plugin_<id>_<tool>` with
`source="plugin:<id>"`; undeclared or missing implementations fail the start.
Because the registration is ordinary, the permission engine, approval
binding, execution slots, and result contracts apply unchanged. The adapter
runs each call on a bounded worker pool with its own deadline, requires
JSON-serializable output within a size limit, reports outcomes as
unverified, and counts consecutive failures: at the configured limit the
plugin is quarantined (its tools disabled, its trust flag cleared) until the
user enables it again. `PluginContext` exposes only the plugin id, version,
a private data directory, and a bounded logger that writes to the
diagnostics ledger.

Honest limit: plugin code runs in-process, so Python cannot sandbox it. The
runtime bounds what JARVIS grants, not what the interpreter allows; a plugin
is trusted code the user installed and enabled on purpose. Process
isolation is a later version.

## Windows integration layer

Phase 7 introduces `WindowsIntegrationService` as the composition boundary for
native Windows observations and actions. `WindowsApplicationRegistry` owns
trusted application definitions and aliases, so model output selects only a
registered ID and never supplies an executable path or shell expression.

`WindowsProcessInspector` uses Win32 process APIs for PID identity, executable
path observation, and bounded process snapshots. `WindowsApplicationLauncher`
starts an `.exe` with `shell=False`, rejects network and batch/script paths,
then independently observes the returned PID and checks its process identity.
An API return without that observation is a failed, unverified launch.

The bootstrap registers four provider-visible Windows tools when the feature
is enabled on Windows: registered application discovery, process observation,
system information, and application launch. Read-only observations return
verified results from native state. Application launch is classified `LOW` and
uses the same bounded tool, permission, and result-verification runtime as all
other actions.

### Safe-filesystem extensions

Bounded filesystem mutations are recoverable: `FilesystemSnapshotStore`
(`app/platform/windows/snapshots.py`) seals the exact bytes of a file before
it is replaced or removed and re-verifies digest and size before any restore.
`delete_path` and `undo_filesystem_change` are HIGH-risk confirmed tools;
`search_files` reads a bounded per-root name index that never follows links;
`plan_filesystem_changes` is a pure dry run whose result carries a plan id
and a SHA-256 digest, and `apply_filesystem_plan` consumes that plan once,
re-checks every target's fingerprint and stops at the first failure.
Critical Windows directories are refused at grant time. The Nova settings
screen manages root grants and restores through the bridge; the tools stay
model-callable only under the permission engine.

## Durable memory

Phase 8 separates transient working context, conversation history, and durable
long-term memory. `MemoryManager` owns lifecycle and safety policy through the
provider-independent `MemoryStore` boundary. Production bootstrap selects
`SQLiteMemoryStore`; tests and explicitly ephemeral runtimes may still use the
thread-safe in-memory implementation.

SQLite initialization is schema-versioned and integrity-checked. Writes are
transactional, concurrent access is serialized, and explicit backup/restore
operations verify database integrity. Records retain source, source reference,
confidence, timestamps, expiry, sensitivity, and supersession relationships.

Recall excludes inactive and expired records and applies deterministic lexical
relevance, importance, confidence, and recency scoring. Only a compact bounded
set enters provider context; its IDs and provenance remain available in Core
context metadata. Exact duplicates are collapsed, declared-subject conflicts
are visible, and supersession retains history.

`MemoryService` exposes read-only list/search tools and separately controlled
forget/delete actions. Forgetting deactivates a record; permanent deletion is a
high-risk operation. Both mutation tools pass through the same permission and
bound-approval runtime as every other side effect.

## Durable task and agent runtime

Phase 9 makes tracked task state independent from process lifetime.
`SQLiteTaskStore` transactionally persists tasks, steps, progress, results,
errors, parent-task relationships, execution identity, and timestamps.
`TaskManager` reloads this state at startup and converts interrupted `RUNNING`
tasks and steps to recoverable `PAUSED` state instead of guessing completion.

`DurableTaskRuntime` allocates an isolated plan and execution-state directory
per task. Plan writes and execution snapshots remain atomic. Recovery combines
the durable task projection, plan, and latest snapshot; verified completed
steps are preserved and an interrupted step is retried. One task cannot be
admitted twice concurrently.

Pause requests take effect only at a safe plan-step boundary. Cancellation is
cooperative and terminal. Resume creates a fresh bounded execution context, so
time, step, tool-call, model, retry, approval, and verification limits continue
to apply after restart. `TaskControlService` exposes task observation and
approval-bound pause, resume, and cancel tools.

## Voice pipeline

Phase 10 adds an optional voice boundary without moving device or vendor logic
into Core. `AudioInput`, `AudioOutput`, `SpeechRecognizer`, and
`SpeechSynthesizer` are explicit contracts. The production adapters provide
bounded PCM microphone capture, Windows WAV playback, and Gemini transcription
and synthesis. Imports for microphone hardware remain lazy so normal startup
does not require the optional audio dependency.

`VoiceSession` owns one stateful turn:

```text
LISTENING -> TRANSCRIBING -> wake gate -> PROCESSING
          -> SYNTHESIZING -> SPEAKING -> COMPLETED
```

Every blocking stage has a timeout and shares one interruption signal. A voice
request enters `CoreEngine` with `RequestSource.VOICE`, so its provider, tool,
permission, approval, budget, and verification behavior remains identical to a
text request. `VoiceService` admits only one active session and supports a
bounded multi-turn loop.

Session results contain transcript, response text, states, and provider/model
provenance, but never synthesized or captured audio. Raw capture uses mutable
memory so it can be overwritten and released immediately after transcription.

**Lazy clients** (5 September 2026). Neither the OpenAI-compatible provider
nor the Gemini speech adapters build their SDK client at construction: the
provider builds it on first use, the speech adapters share one google-genai
client per key built on first use, and the Nova boot warm-up builds and
connects all of them off the UI path. Start-up therefore no longer imports
either SDK before the window opens.

**Streamed speech** (5 September 2026). The Gemini synthesizer can return
a `SpeechStream` of PCM chunks primed on the first one; `AudioOutput.play_stream`
plays it, through sounddevice on Windows or buffered to WAV elsewhere. The
voice session races the cloud's first audio chunk against the local voice,
and starts synthesizing the reply's first sentence as soon as the streamed
reply has moved past it, discarding that speech if the final text differs.

## Vision and screen understanding

Phase 11 adds `VisionService` as a boundary around screen capture and visual
analysis. `ImageSource` keeps native capture replaceable; the Windows adapter
uses Win32 GDI directly, does not execute shell input, and checks width, height,
and pixel limits before allocation.

The state flow is explicit:

```text
AWAITING_CONSENT -> CAPTURING -> REDACTING -> ANALYZING -> COMPLETED
```

`VisionConsentGate` issues a short-lived, immutable grant bound to the exact
purpose, source, and user-selected redaction regions. A grant is consumed
atomically before capture and cannot be replayed. Disclosure text states that
the processed frame may reach the configured vision provider.

`ImageRedactor` irreversibly blackens user-selected regions and the configured
taskbar band before PNG encoding. Provenance records source, capture time,
dimensions, original pixel hash, processed PNG hash, transformations, and
consent identity. Frame age is checked before and after processing.

Vision enters Core as `RequestSource.VISION` with an ephemeral mutable image
payload. `ModelRouter` requires the `VISION` capability and selects the
dedicated vision model profile when `JARVIS_VISION_MODEL` names one, otherwise
the general model. The provider adapter converts validated bytes to a Base64
data URL only for the active vendor request. Conversation history stores
the user text and response, never the image payload. Mutable frame and PNG
buffers are overwritten after completion or failure.

## Web research

Phase 12 adds a separate research pipeline so ordinary conversation cannot
silently become network retrieval:

```text
QUESTION -> SEARCH -> COLLECT -> FILTER -> CROSS-CHECK
         -> SYNTHESIZE -> CITE -> COMPLETE
```

`URLPolicy` accepts only configured HTTP(S) schemes and ports, resolves every
hostname, and rejects any DNS answer that is not globally routable, including
IPv4-mapped IPv6 addresses. `PinnedHTTPTransport` connects directly to one of
the validated addresses while preserving the original hostname for HTTP Host
and TLS certificate verification. Proxies, cookies, credentials, automatic
redirects, authentication, and compressed responses are not used. Every
redirect target passes the complete policy again and HTTPS cannot downgrade.

`SafeWebFetcher` bounds time, redirect count, response bytes, extracted
characters, content types, and status codes. Attachments and binary downloads
fail closed. HTML extraction removes active and hidden elements. Web text is
always marked as untrusted data; prompt-injection indicators are hashed and
reported but never interpreted as instructions.

`SearXNGSearchProvider` uses the administrator-configured JSON endpoint. The
`ResearchService` deduplicates and bounds candidates, collects independent
sources concurrently, records observation/publication times, resolved IPs and
content hashes, assigns freshness, cross-checks excerpts, and validates every
claim citation against the returned source set. The structured report labels
every claim as observation or inference and lists unresolved limitations.

## Desktop interface

Two shells share one controller. Presentation remains outside Core:
`DesktopController` translates UI intent into typed `Request`, voice, vision,
research, task, memory, and tool service calls, and a persistent background
asyncio loop keeps provider and device operations off the UI thread.

**Nova** (`app/ui/nova/`, the default since 5 September 2026) is a pywebview
window hosting `web/index.html` in Microsoft Edge WebView2. `NovaBridge` is the
`js_api` object: pywebview calls its public methods on worker threads, each
returns a small `{ok, error?}` dictionary immediately, and long-running work is
submitted to the controller's runner. Results flow back through
`window.evaluate_js("window.NOVA.push({kind, payload})")` after `_jsonable`
serialization, so the page only ever renders data the core produced: snapshot,
reply, stream, busy, voice_message, voice_phase, voice_state, voice_level,
vision_result, research_result, approval, approval_closed, tool_activity,
diagnostic_event, notification, navigate, and paused. The bridge subscribes, read-only, to
the tool executor (every execution start and outcome), to the diagnostics
ledger (every sealed event) and to the microphone level, and re-subscribes
when a settings save replaces the runtime. Since the 5 September 2026
redesign the page is a small design system — `web/css/` tokens, base, shell,
components and screens; `web/js/` foundation (vocabulary, state, motion
primitives), bridge (API and push channel), presence (the state machine and
the `JarvisCore` visualization), shell (rail, drawer, palette, compact mode,
boot), conversation, activity (execution timeline and the approval overlay),
panels and main. All motion runs on the browser compositor at the monitor
refresh rate, throttles when calm and pauses when hidden; canvases scale
with `devicePixelRatio`. Window geometry changes for the compact mode are
marshalled onto the WinForms UI thread because pywebview applies them on
the calling thread.

Nova's honesty rules: the page waits for the real bridge and shows an explicit
failure screen if it never arrives; the demo bridge is reachable only with
`?demo=1` in a plain browser and is labelled everywhere; approvals are
single-use tokens that fail closed; secrets never cross the bridge; deleting
the credential needs an in-app confirmation and a `confirmed=True` argument.
Web assets are resolved by `resolve_web_root()` from `sys._MEIPASS` in a
frozen build or from the source tree otherwise, and the WebView2 profile lives
in `%LOCALAPPDATA%\JARVIS\webview` so theme and motion preferences persist.

**Scheduled routines** (`app/routines/`, 5 September 2026). `RoutineService`
is a bounded SQLite store of named prompts with a daily or interval
schedule and an atomic due claim. The Nova bridge runs a due routine
through the core like a typed command (same engine, permissions and
approval overlay; `RequestSource.SYSTEM`; the routine's own conversation),
defers it while the desktop is paused or busy, and publishes the outcome to
the notification centre.

**Notification centre** (`app/notifications/`, 5 September 2026).
`NotificationCenter` is a bounded, thread-safe, session-scoped attention
list with kinds, severities, target screens and dedupe-by-key collapsing;
`ReminderWatch` polls the reminder store on a daemon thread and hands each
due reminder out exactly once. The bridge owns one centre per window
session, publishes into it from the core's observers (reminders, ledger
warnings, screen observations) and from its own completions when the
window is unattended (replies, approval requests as tool name and risk
only, vision and research results), pushes every entry to the page with the
live unread count, and raises a native notification off-thread for
alert-worthy entries nobody is looking at. Whether the window is attended
comes from the page's visibility state, the tray's open/hide and
pywebview's minimized/maximized/restored events (pywebview reports
`restored` only for a return to the Normal state, so a maximized window
coming back from the taskbar arrives as `maximized`). Each flip and each
native attempt is a ledger event (`window.visibility`,
`notification.native`). The page renders the bell, the popover and the
toasts only from what the core pushed or returned.

**System tray** (`app/ui/tray/`, 5 September 2026). A toolkit-independent
menu model and controller (`Aç`/`Öne getir`, `Sesli mod`, `Duraklat`/`Devam`,
`Tanılama`, `Ayarlar`, `Çıkış`) drive a WinForms `NotifyIcon` that lives on its own STA
thread with its own message loop; pythonnet is already loaded by pywebview,
so no dependency was added. With the tray enabled, closing the Nova window
hides it to the notification area (a one-time balloon says so) and `Çıkış`
performs the same clean shutdown as before: the window is destroyed, the
tray thread exits, the bridge fails pending approvals closed, and the
controller releases its runner and stores exactly once. `Duraklat` is a
user-interface gate: the controller refuses new commands, voice, vision, and
research requests, an active voice session is stopped, and the page shows
`DURAKLATILDI`; it is not a security control and never touches the
permission engine. A named mutex plus a named event
(`Local\JARVIS.Desktop`) keep one desktop per user session: a second launch
signals the first, which brings its window forward, and exits. Tray failures
are recorded as `tray.error` and the window keeps working without an icon.

The **classic** Tk shell (`DesktopWindow`, Phase 13) remains available with
`--classic` and is used automatically when pywebview is not installed. Both
shells provide Home, Chat, Tasks, Memory, Voice, Vision, Research, Tools,
Integrations, Diagnostics, and Settings screens fed by live application
snapshots; disabled capabilities stay visibly disabled and fail closed at the
controller boundary, and neither shell can bypass tool permissions or
synthesize an approval grant.

## Diagnostics and observability

Phase 14 adds `DiagnosticsService` as the shared observability boundary. Core
emits request start, completion, duration, outcome, provider identity, verified
tool counts, and sanitized failure classification. Telemetry failure is isolated
from request execution so a full metrics registry cannot break Core.

`DiagnosticLedger` is thread-safe, bounded, and hash chained. When capacity
evicts an old record its hash becomes the retained anchor, preserving integrity
verification for the remaining window. Events use sanitized bounded attributes;
credential-shaped keys are redacted and correlation identifiers are represented
by stable non-reversible hashes.

`MetricRegistry` accepts validated low-cardinality names and fixed-capacity
counters, gauges, and duration summaries. `HealthRegistry` runs synchronous and
asynchronous checks concurrently under individual timeouts, converts exceptions
to stable failure messages, and reports one observed timestamp and latency per
component. Three read-only tools expose live health, sanitized events, and
metrics without granting mutation authority. `DiagnosticsService.subscribe`
delivers every sealed event to read-only listeners after it is in the
ledger, so the desktop can show the tail live without polling.

## Performance and reliability

Phase 16 adds admission control at the public Core boundary. A bounded semaphore
limits concurrent executions; a separate bounded queue admits only a configured
number of waiters and gives each a short deadline. Saturation returns a typed
`AdmissionRejectedError`, records a warning event and counter, and does not
create an unbounded task backlog. Leases release capacity on success, exception,
cancellation, or timeout.

Every provider has an independent circuit breaker. Retryable consecutive
failures open the circuit at a configured threshold. Calls fail fast while open;
after the recovery interval exactly one half-open probe is admitted. A successful
probe closes and resets the breaker, while a failed probe reopens it. Existing
provider retry, timeout, and cancellation rules remain in force.

Provider circuit and Core admission state are included in live health checks.
Load tests exercise one hundred parallel offline Core requests, saturation,
queue timeout, cancellation cleanup, circuit opening, half-open exclusivity,
recovery, and fixed resource accounting.

## Windows packaging boundary

Phase 17 uses an onedir PyInstaller build so native libraries and Tcl/Tk data
remain inspectable. A custom hook handles build hosts where PyInstaller cannot
initialize Tcl during analysis while still collecting `_tkinter`, Tcl/Tk DLLs,
and complete script libraries. The frozen entry point stores mutable state only
below `%LOCALAPPDATA%\JARVIS` and provides a non-interactive smoke mode.

`scripts/build_windows.py` runs the source acceptance gate, builds the frozen
application, requires native rendering of all eleven screens under normal
conditions, creates a portable archive, compiles the current-user Inno Setup
installer when ISCC is available, and records hashes and signing status. An
explicit environment-limited switch records a failed native-render result; it
cannot convert that evidence into a production qualification.

Since 5 September 2026 the spec also bundles every file below Nova's `web/`
directory (`index.html`, `css/`, `js/`) below `_internal/app/ui/nova/web`,
and the frozen smoke test imports `app.ui.nova.shell` and lists the files it
finds as `nova_assets`; the build fails unless every file in
`shell.WEB_ASSETS` is present. `JARVIS.exe --classic` opens the Tk
shell from the same package.
