# JARVIS Project State

Last verified: 22 August 2026

## Current status

- Completed implementation milestone: Phase 17 — Windows packaging and installer
- Completed validation milestone: Phase 18 — final audit and delivery evidence
- Completed maintenance milestone: single-provider (Gemini) consolidation
- Next action: code signing and live-service qualification (`docs/FINAL_AUDIT.md`)
- State: development release; production acceptance is not yet achieved
- Platform target: Windows 11, Python 3.12
- Automated verification: 1143 tests passing, 1 skipped (`scripts/verify.py`)
- Production readiness: not yet claimed

## Provider consolidation (22 August 2026)

JARVIS now ships exactly one production AI provider: **Gemini**, reached through
Google's OpenAI-compatible API surface.

- Removed: the Ollama provider, its warm keeper, its hybrid chat/tool routing
  policy, and the OpenAI speech adapters. `app/providers/openai.py` remains only
  as the shared adapter base class that `GeminiProvider` extends.
- `MockProvider` remains the deterministic offline provider for the automated
  suite. `create_application()` registers it, and makes it the default, only
  when `settings.default_provider == "mock"`. The desktop cannot select it.
- Provider fallback is disabled by construction. With one production provider
  there is nothing to fall back to, and falling back to the mock provider would
  replace a real failure with a convincing fiction.
- `DeterministicToolRouter` is now provider-neutral. It previously refused to
  route unless the active provider was Ollama, which silently disabled the
  latency optimization after the migration. Every candidate it routes to is a
  `READ_ONLY` observation tool and the permission engine still authorizes the
  call, so removing the provider gate removes a model round trip, never a
  security boundary.
- `vision_model` is now an optional dedicated Gemini vision model instead of a
  dead `gpt-4o` default. When set and different from the general model, `VISION`
  requests route to it exclusively.
- Dead configuration knobs were removed rather than left to mislead:
  `voice_stt_model`, `voice_tts_model`, `voice_tts_voice`, and
  `provider_fallback_enabled` had no remaining consumer, and the retired
  `JARVIS_API_KEY`, `JARVIS_API_BASE_URL`, and `JARVIS_OPENAI_MODEL`
  environment variables are no longer read.
- The approved-application fast-action router, previously gated behind the
  Ollama hybrid mode, now activates whenever Windows integrations are enabled.
  It short-circuits only registered application launches, and the launched
  process is still verified by PID and identity.
- Stale environment from older builds cannot break startup: unknown
  `JARVIS_DEFAULT_PROVIDER` values fall back to Gemini, the desktop ignores
  stale default-model and voice-provider variables, the retired `gpt-4o`
  vision default is dropped, and voice automatic selection falls back to
  Gemini when the text provider has no speech adapters.

### Verified on this host

- Native Tcl/Tk 8.6.15 initializes, and all eleven desktop screens render from
  source on a normal Windows 11 account. The sandbox limitation recorded in the
  Phase 13 and Phase 17 notes below does not apply to this machine.
- `scripts/verify.py` passes dependency integrity, bytecode compilation, and the
  complete deterministic suite.
- Voice was qualified live on 23 August with the real desktop, real
  Gemini APIs, and a scripted microphone: capture, 1.3s transcription,
  short-reasoned reply, sentence-pipelined speech, HUD states, earcons,
  and a graceful, explained silence-close. When cloud synthesis is
  rate-limited the turn retries once and then answers through the local
  Windows SAPI voice with an honest notice — a voice turn can no longer
  end silently.
- The full release pipeline ran end to end on 22 August: PyInstaller build,
  frozen smoke test (`ok=true`, `screens=11`, `tcl=8.6.15`), portable ZIP,
  Inno Setup installer compile, silent clean install, installed-location smoke
  test, in-place reinstall, and silent uninstall with user data preserved.

## Application integrations and live awareness (23 August 2026)

JARVIS now drives real applications and watches the screen. Every
capability is a permission-checked tool; nothing bypasses the approval
gate or the verification contract.

- **Spotify**: transport control through media keys verified against the
  window title, search deep links, and an optional Web API tier behind
  one-time PKCE OAuth for exact playback, private playlist creation, and
  listening statistics.
- **WhatsApp**: contact book, deep links that prefill without sending,
  chat and conversation reading through the native UIA3 accessibility
  tree, HIGH-risk approval-gated sending that reports PARTIAL when the
  send button cannot be verified, and a bounded delegation agent that
  answers a named contact on the user's behalf with draft screening for
  credentials and commitments.
- **System**: http/https-only browser navigation, web search, volume.
- **Reminders**: persistent SQLite reminders with exactly-once delivery
  and native toast notifications.
- **Screen watching**: continuous observation with local 12x12 luminance
  change detection (0.11ms per frame) that calls the vision model only
  on real change; frames are discarded immediately after signature.

Hardening from live use on 23 August 2026: Spotify plays a named
track with no account setup by driving the desktop app's own search
UI (verified against the window title); WhatsApp launches itself when
closed and opens chats by their visible list name with an empty
contact book; every chat render lands at the newest message instead of
the top; and PowerShell output is forced to UTF-8 so Turkish titles
survive.

Intent handling was hardened alongside: unresolved phrasings expose the
full tool inventory instead of failing closed, tool-bearing turns
escalate to the stronger action model with a graceful rate-limit
fallback, and an action-integrity directive forbids claiming an action
without calling its tool.

Local Turkish speech now goes through WinRT ("Microsoft Tolga"), which
SAPI does not expose, and races cloud synthesis so the reply starts with
whichever source answers first.

## Voice quality and integration robustness (23 August 2026, session 2)

- The first spoken sentence now gives the high-quality cloud voice a
  bounded head start (`voice_cloud_grace_seconds`, default 3.0s):
  within the window the cloud voice wins even when the instant local
  voice finished first, so the robotic Windows voice is heard only
  during real outages or past-deadline slowness. 0 restores the pure
  latency race.
- Every synthesis request carries a JARVIS persona style directive
  (`voice_tts_instructions` now defaults on), and the local fallback
  is bilingual: replies without Turkish letters or everyday Turkish
  words are spoken by the English Windows voice instead of Tolga
  spelling English out phonetically.
- End-of-turn silence is 1.5s (was 0.9s), per user tuning.
- Spotify `play_track` works with zero account setup: when no Web API
  token exists (or no active device is registered) it drives the
  desktop app itself — search deep link, then the top result's play
  button through UI Automation — and verifies via the window title.
  Verified live: both an artist query and a specific-song query.
- WhatsApp launches itself when closed, and chats are reachable by
  their visible chat-list name with an empty contact book (real-click
  row activation, composer verification). Typed drafts are
  whitespace-collapsed so a newline can never act as Enter from the
  non-sending open-chat tool, and typing re-fronts the window and
  strips control characters. comtypes NULL window pointers no longer
  raise.
- PowerShell output is forced to UTF-8 so Turkish titles survive.
- Chat auto-scroll goes through the scroller's own offset bookkeeping;
  replies no longer bounce the conversation to the top.
- Host voice-preference environment variables are scrubbed in the
  test suite so the suite stays hermetic. (An ElevenLabs adapter was
  built this session and fully removed the next: the user settled on
  a single built-in voice instead of a purchased one.)

## Voice identity, Turkish recognition, and real memory (23 August 2026, session 3)

- **Charon is the single voice of JARVIS.** After hearing samples
  the user settled on Charon; it is the default everywhere, the only
  registered cloud voice path, and the same multilingual voice speaks
  both Turkish and English. The stray `JARVIS_VOICE_GEMINI_TTS_VOICE`
  user-environment override was deleted so the setting's default is
  authoritative.
- **Recognition is pinned to Turkish** (`voice_language` defaults to
  `tr` with a firm transcription directive), ending wrong-language
  transcripts, and one transient transcription failure is retried
  before the turn can fail.
- **Voice and text now share one conversation.** Voice turns appear in
  the chat history, persist with the conversation store, and are
  restored on restart; asking in text about something said aloud
  works.
- **Long-term memory actually captures now.** Three layers: the
  explicit-prefix analyzer understands more Turkish ("unutma", "not
  al", "aklında tut") plus identity statements; the policy's
  preference path writes (it used to be dead code because the engine
  demanded an analyzer candidate); and an automatic post-turn model
  pass (`memory_auto_capture_enabled`, lite model) distills durable
  personal facts into third-person memories with paraphrase-aware
  deduplication, off the latency path, never able to fail a turn.
  Verified live against real Gemini: a casual sentence about the
  user's project became a stored memory.

## Implemented architecture

- `CoreEngine` provides bounded request, provider, conversation, memory, and
  tool orchestration.
- `ProviderGateway` provides capability-aware routing, explicit overrides,
  timeout, retry, health accounting, and normalized streaming; cross-provider
  fallback is intentionally disabled.
- `ConversationEngine` owns validated conversation lifecycle, complete tool-call
  groups, bounded context, and summaries.
- `ToolExecutor` owns strict input/output contracts, dynamic discovery,
  lifecycle, permission enforcement, timeout, cancellation, concurrency, and
  provider schema generation.
- `ExecutionService` owns plan-step execution, budgets, retries, verification,
  events, snapshots, recovery, and result propagation.
- `PermissionEngine` owns deterministic policy, parameter rules,
  least-privilege scopes, and bounded decision auditing.
- `ApprovalGate` and `ApprovalStore` own immutable, expiring, action-bound
  approval lifecycle. Final grant validation occurs at the tool boundary.
- `WindowsIntegrationService` owns trusted application registration, native
  system/process observation, safe process creation, and launch verification.
- `SQLiteMemoryStore` owns schema-versioned durable long-term memory,
  transactional persistence, integrity checks, and verified backup/restore.
- `MemoryManager` and `MemoryService` own safety screening, provenance,
  freshness, relevance, lifecycle, retention, and user-visible controls.
- `SQLiteTaskStore`, `TaskManager`, and `DurableTaskRuntime` own durable task
  identity, progress, subtasks, safe pause/resume, cancellation, and restart
  recovery through isolated plan and execution snapshots.
- `VoiceService` and `VoiceSession` own bounded microphone turns, explicit
  state, wake-word gating, interruption, speech provenance, and audio disposal.
- `VisionService`, `VisionConsentGate`, and `WindowsScreenSource` own one-use
  capture consent, native bounded capture, redaction, freshness, provenance,
  interruption, and vision-capability routing.
- `ResearchService`, `URLPolicy`, and `SafeWebFetcher` own opt-in search,
  IP-pinned safe retrieval, untrusted-content isolation, provenance, freshness,
  bounded multi-source synthesis, uncertainties, and citation integrity.
- `DesktopController` and `DesktopWindow` own native grayscale presentation,
  responsive background dispatch, live service state, and explicit user input
  while preserving all Core security boundaries.
- `DiagnosticsService`, `DiagnosticLedger`, `MetricRegistry`, and
  `HealthRegistry` own sanitized structured events, tamper evidence, bounded
  low-cardinality metrics, and timeout-contained live health checks.
- `AdmissionController` and per-provider `CircuitBreaker` instances own bounded
  Core concurrency, queue deadlines, overload failure, dependency isolation,
  and single-probe recovery.
- The Windows release pipeline owns pinned PyInstaller analysis, bundled Tcl/Tk,
  logo resources, frozen smoke evidence, portable packaging, Inno Setup source,
  and artifact hashes.

## Completed phases

1. Phase 0 — workspace inspection and architecture foundation
2. Phase 1 — project bootstrap, configuration, and core contracts
3. Phase 2 — bounded JARVIS Core and execution runtime
4. Phase 3 — AI provider gateway and model routing
5. Phase 4 — conversation engine and context lifecycle
6. Phase 5 — versioned, dynamically discoverable tool system
7. Phase 6 — scoped permission policy and bound approval security
8. Phase 7 — native Windows observation and verified application launch
9. Phase 8 — durable, searchable, provenance-aware long-term memory
10. Phase 9 — durable, resumable, bounded task and agent execution
11. Phase 10 — bounded voice input, speech output, and interruption
12. Phase 11 — consent-bound vision and screen understanding
13. Phase 12 — source-grounded, SSRF-resistant web research
14. Phase 13 — native desktop UI from the approved visual prototype
15. Phase 14 — diagnostics, observability, health, and tamper-evident events
16. Phase 15 — complete-system acceptance and security regression gates
17. Phase 16 — bounded load, overload control, and provider circuit recovery
18. Phase 17 — reproducible Windows packaging, branding, and release evidence
19. Phase 18 — final acceptance audit and production-gap classification

## Current security decisions

- `READ_ONLY` and `LOW` operations are allowed by default.
- `MEDIUM` and `HIGH` operations require a valid bound approval grant.
- `CRITICAL` operations are denied by the default policy.
- Tool or plan metadata cannot lower effective risk.
- Parameter rules may elevate risk or force confirmation/denial, never allow.
- A raw confirmation boolean is not authorization.
- Approvals bind operation, tool version, parameters, task, plan, step, and
  expiry and are validated immediately before handler execution.
- Invalid filters, rules, scopes, grants, and approval parameters fail closed.
- Permission audit records do not retain tool parameter values.
- Windows launch accepts only registered local `.exe` definitions and verifies
  the returned PID and process identity before reporting success.
- Durable memory rejects credential, private-key, and payment-card material.
- Soft forgetting requires `MEDIUM`-risk approval; permanent deletion requires
  `HIGH`-risk approval and both are revalidated at the tool boundary.
- Task pause, resume, and cancellation require bound approval; interrupted work
  is paused on startup and never assumed complete.
- Voice is disabled by default, microphone capture is duration-bounded, and raw
  audio is overwritten and released after transcription unless retention is
  explicitly enabled.
- Vision is disabled by default. Every capture requires a short-lived, exact,
  one-use consent grant and configured privacy masks run before model access.
- Research is disabled by default. Every URL and redirect is DNS-validated,
  pinned to a public IP, content-bounded, and treated as untrusted evidence.
- Diagnostics redact secret-bearing fields and values, hash trace identity,
  bound event/metric growth, and never expose health-check exception details.
- Core work and queued callers are bounded; retryable provider outages open a
  circuit and later admit exactly one recovery probe.

## Known limitations and deferred work

- Window management, clipboard, notifications, and broader application-specific
  controls remain future Windows extensions.
- Gemini requires a configured API key. Without one the desktop reports a
  classified configuration error instead of answering.
- The filesystem tool family has no undo/rollback snapshot, no dry-run plan for
  bulk operations, and no indexed search tool.
- Python cannot forcibly stop an already-running synchronous worker thread.
  Timeout results explicitly report when side effects may continue.
- System tray and plugin runtime remain future extensions.
- Publisher code signing is not configured.
- The current sandbox cannot complete native Tcl rendering or install the Inno
  compiler; the portable artifact therefore remains environment-limited and
  the installer EXE has not been produced in this environment.

## Verified Phase 7 vertical slice

Implement the first real Windows vertical slice through the existing security
and verification boundaries:

```text
User request
  -> provider tool call
  -> registered Windows tool
  -> permission/approval decision
  -> native Windows action
  -> independent state verification
  -> structured result
  -> natural response
```

The real Notepad launch path was executed locally, verified by its new PID and
process identity, then the test-created process was closed. Native system and
process observations were also executed successfully.

## Backup policy

After every completed phase, the verified project is mirrored to:

`C:\Users\MeGaComputers\Documents\Codex\JARVIS_BACKUPS\JARVIS`

Generated virtual environments, caches, transient logs, and temporary runtime
data are excluded because they are reproducible or non-authoritative.

## Verified Phase 8 vertical slice

The application persisted a memory to SQLite, closed the store, rebuilt the
application against the same database, recalled the memory with its source and
freshness metadata, created an integrity-checked backup, and restored that
backup into a separate database. Corrupt input failed closed. Concurrent
writes, expiry filtering, sensitive-data blocking, soft forgetting, and
approval-gated permanent deletion are covered by regression tests.

## Verified Phase 9 vertical slice

Completed. A real two-process validation persisted a running two-step task,
restarted the task manager, detected the interruption as paused, resumed from
the second step without repeating the verified first step, completed the task,
and restored its terminal state from an integrity-checked database backup.

## Verified Phase 10 vertical slice

Completed. A deterministic end-to-end voice turn captured bounded PCM audio,
converted it to WAV for transcription, applied an exact optional wake-word
gate, entered Core as a `VOICE` request, synthesized the response as WAV, and
sent it to the audio output. Tests verify interruption while listening,
processing, and speaking; timeout and failure classification; device closure;
audio disposal; provider limits; and single-session admission without network,
credentials, or physical audio hardware.

## Verified Phase 11 vertical slice

Completed. A deterministic screen frame passed through explicit one-use
consent, bounded capture, user-selected and automatic taskbar redaction, PNG
encoding, freshness verification, SHA-256 provenance, the real `CoreEngine`,
and capability-aware routing to a vision provider. The image entered Core as a
`VISION` request and was cleared after analysis. Tests cover consent tampering,
single use, stale images, invalid regions, timeouts, interruption, retention,
native-source boundaries, OpenAI image normalization, and conversation-history
privacy without network access or capture of the user's real desktop.

## Verified Phase 12 vertical slice

Completed. Deterministic SearXNG results pass through safe-result filtering,
bounded concurrent collection, source extraction, freshness classification,
cross-checking, synthesis, citation validation, and explicit uncertainties.
Tests verify public-IP pinning, redirect revalidation, IPv4/IPv6 SSRF defenses,
download and content limits, injection indicators, source hashes and timestamps,
strict tool contracts, and offline bootstrap behavior without network access.

## Verified Phase 13 vertical slice

The approved HTML prototype was translated into a native monochrome desktop
shell with eleven screens, collapsible navigation, live context, command
composer, two themes, and explicit voice/vision/research controls. Prototype
mock values were replaced by live application snapshots. Text commands pass
through the real Core; optional capabilities fail closed; one-capture vision
requires a user-visible confirmation immediately before consent creation.

The development harness has no Tcl data files, so this phase verifies the UI
through controller integration, complete state/render-module compilation, and
import-safe native presentation code. The packaged runtime smoke test remains a
mandatory Phase 17 acceptance check.

## Verified Phase 14 vertical slice

Core request start and completion events enter a sanitized, bounded,
hash-chained ledger with stable non-reversible correlation. Successful requests
update counters and duration summaries; provider failures record only a stable
error class. Concurrent live checks observe Core, provider registration, memory,
durable tasks, and event-ledger integrity under individual timeouts. Read-only
tools expose health, events, and metrics, and the desktop diagnostics view shows
the live ledger count and integrity result.

## Verified Phase 15 acceptance gate

The single-command verifier passed dependency integrity, complete bytecode
compilation, and all 795 tests under a fixed non-default hash seed. The offline
system acceptance path covered durable SQLite memory/tasks, Core, conversation,
UI state, diagnostics, metrics, health, and event integrity. All application
modules imported without external actions; every tool contract was unique,
closed-schema, versioned, JSON serializable, and risk-consistent. Static security
regressions confirmed no shell-enabled subprocess or dynamic `eval`/`exec` path.

## Verified Phase 16 load and recovery slice

Core admission limits active and queued work before any memory, provider, or
tool processing. Saturation, queue timeout, and cancellation release accounting
correctly and record content-free diagnostics. One hundred parallel mock Core
requests complete under an outer five-second budget without leaked leases.
Retryable provider failures open an independent circuit, suppress further remote
calls, admit only one half-open probe, and close or reopen from its verified
result. Provider circuit and admission state are included in live health checks.

## Phase 17 packaging evidence

The pinned Windows build produces a versioned, branded onedir executable and a
portable ZIP. The executable contains the approved monochrome JARVIS icon,
Windows version metadata, manifest, `_tkinter`, Tcl/Tk DLLs, complete Tcl/Tk
script data, voice binaries, and application documentation. The build pipeline
validates controlled cleanup, archive topology, static runtime completeness,
artifact hashes, signing status, and strict native smoke evidence.

All 817 source tests pass. The frozen process starts and reaches Tk creation,
but this sandbox's native Tcl file API reports `init.tcl` unavailable even while
Python and PowerShell read the same bundled file. The release manifest records
this as `environment_limited`, `ok=false`, and `native_ui_rendered=false`.
Inno Setup 7.0.2 was downloaded from its immutable official release and its
Pyrsys B.V. Authenticode signature verified, but its compiler installation is
blocked by the same sandbox profile-folder limitation. No production-readiness
claim is made until both gates pass on a normal Windows account.

## Phase 18 final validation

Completed. The single-command source verifier passes dependency integrity,
bytecode compilation, and all 817 deterministic tests. A dated runtime
dependency audit reports no known vulnerabilities. Static review found no
forbidden dynamic/shell execution path; abstract-provider `NotImplementedError`
methods and best-effort cleanup handlers are intentional boundaries rather than
unfinished product stubs.

The authoritative 28-item acceptance matrix is in `docs/FINAL_AUDIT.md`: 18
items pass, 9 are conditional on configured hardware/external services or a
normal Windows packaging host, and 1 is missing (the complete safe filesystem
tool family). JARVIS remains a development release until the recorded blockers
are resolved.

## Post-Phase 13 desktop and API configuration refresh

The native desktop was comprehensively redesigned around a restrained
black/white/grayscale system while preserving all eleven live runtime views.
The new shell includes scroll-safe content, clearer visual hierarchy, a
collapsible animated navigation rail, a reduced-motion control, a live status
pulse, improved conversation presentation, and keyboard-first operation.
`Enter` submits the composer, `Shift+Enter` inserts a line break, and additional
navigation, focus, theme, and help shortcuts are available through `F1`.

The Settings screen accepts the Gemini API key through a masked field, selects
the model, tests model access, saves non-secret preferences atomically, and
activates a rebuilt runtime without restarting the desktop. The secret is stored
only in Windows Credential Manager under `JARVIS/Gemini API`; it is never
written to the repository or preferences JSON. Mock echo responses are blocked
in the user interface and replaced by a clear configuration path. A live
provider failure can no longer fall back to the development-only mock provider,
so authentication, model, quota, and network errors remain visible.

## Gemini provider integration

Gemini is the sole production provider, reached through Google's
OpenAI-compatible API endpoint. Its credential lives in its own Windows
Credential Manager record. The default model is `gemini-3.5-flash-lite`, and an
optional `JARVIS_VISION_MODEL` routes `VISION` requests to a separate Gemini
model when the two differ.
