# Testing

Run tests with the project virtual environment:

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

Tests are deterministic and must not require network access, real credentials,
or external services. Filesystem tests use pytest temporary directories. Every
bug fix or security-boundary change requires a focused regression test before
the complete suite is run.

The Phase 1 regression coverage includes:

- strict input and output tool contracts;
- execution success versus explicit verification state;
- sync and async timeout behavior;
- event-loop responsiveness;
- cancellation before and during execution and retry backoff;
- parameter-sensitive permission elevation and fail-closed rules;
- exact approval binding, invalidation, and expiry;
- cancellation state propagation through plans.

The Phase 2 regression coverage includes:

- one shared execution runtime for direct plans and tracked tasks;
- request, conversation, task, plan, and step identity propagation;
- hard plan-step, tool-call, model-iteration, token, and time budgets;
- provider and running-tool cancellation;
- exactly-once terminal cancellation events;
- verified completion reporting in direct agent mode;
- malformed and duplicate tool-call handling;
- failed, cancelled, and partial result propagation;
- prevention of orphan tasks after input validation failure.

The Phase 3 regression coverage includes:

- strict response, stream, capability, profile, and provider-error contracts;
- task-type and capability-aware model routing;
- dynamic capable-provider discovery;
- explicit provider and model overrides;
- fail-closed tool, vision, streaming, and structured-output requirements;
- transient-only retry and bounded exponential backoff;
- health accounting and disabled-fallback behavior;
- cancellation and timeout cleanup;
- streaming retry only before the first emitted chunk;
- OpenAI-compatible response, tool-call, usage, error, and stream
  normalization through the shared adapter base class;
- Gemini identity, model selection, compatible endpoint wiring, isolated
  credentials, and classified error normalization;
- model context and optional cost metadata;
- sanitization of unexpected provider exceptions.

The Phase 4 regression coverage includes:

- conversation, turn, role, and message contract validation;
- copy-isolated conversation storage;
- create, archive, activate, list, and delete lifecycle behavior;
- idempotent request and response recording;
- complete multi-turn user/assistant history;
- persisted assistant tool-call and verified tool-result turns;
- rejection of tool calls without stable identity;
- atomic request/tool grouping under context limits;
- bounded summaries with source turn count and update time;
- optional system prompt injection without persisted fake turns;
- conversation and turn trace metadata on Core responses.

The Phase 5 regression coverage includes:

- versioned tool metadata and unsafe retry-contract rejection;
- provider-neutral contract and OpenAI-compatible schema derivation;
- dynamic enable, disable, unregister, and registry revision behavior;
- exact-name, capability, and tag-based discovery;
- request-scoped tool exposure with malformed filters failing closed;
- per-tool concurrency admission for overlapping execution;
- tool-specific idempotent retry policy through the shared execution service;
- preservation of existing input, output, permission, timeout, cancellation,
  and verification boundaries.

The Phase 6 regression coverage includes:

- complete and non-overlapping permission policy validation;
- tool allowlists, denylists, and effective-risk ceilings;
- parameter-based elevation, confirmation, denial, and fail-closed matchers;
- bounded permission audit records and rule lifecycle revisions;
- approval binding to tool version, parameters, task, plan, step, and expiry;
- rejection of raw confirmation booleans and altered approval contexts;
- real tool-contract risk resolution in the agent approval gate;
- immutable approval requests and atomic concurrent state transitions;
- configurable approval TTL and permission audit capacity.

The Phase 7 regression coverage includes:

- strict Windows application definitions, aliases, and registry lifecycle;
- executable resolution without shell expressions, scripts, or network paths;
- bounded native process enumeration and current-process observation;
- launch PID observation and executable identity verification;
- unknown application, missing executable, timeout, and mismatch failures;
- verified Windows tool registration and Core provider/tool integration;
- feature-flagged bootstrap behavior;
- real local system/process observations;
- one real Notepad launch, PID verification, and test-process cleanup.

The Phase 8 regression coverage includes:

- SQLite schema initialization, integrity checks, and complete field roundtrip;
- restart persistence and application-level durable-memory wiring;
- deterministic relevance ranking and exclusion of inactive/expired records;
- concurrent writers and persisted recall timestamps;
- exact duplicate handling, declared-subject conflict visibility, and expiry purge;
- soft forget versus permanent deletion;
- source, confidence, freshness, sensitivity, and retention metadata;
- credential, private-key, and payment-card rejection;
- verified database backup/restore and fail-closed corruption handling;
- provider-visible memory controls with approval-bound mutations.

The Phase 9 regression coverage includes:

- complete SQLite task and task-step roundtrip across process restart;
- automatic interrupted-running to recoverable-paused conversion;
- preservation of waiting-for-input and waiting-for-approval states;
- safe-boundary pause followed by restart and remaining-step-only execution;
- durable cancellation and terminal-state recovery refusal;
- multi-task concurrency and same-task re-entry rejection;
- parent/subtask relationship persistence;
- atomic plan and execution snapshot coordination;
- strict metadata persistence and rollback after serialization failure;
- corrupt task database failure, integrity-checked backup, and restore;
- provider-visible progress plus approval-bound pause/resume/cancel controls.

The Phase 10 regression coverage includes:

- strict audio device, capture, transcription, speech, event, and result models;
- bounded PCM capture, WAV conversion, device enumeration, and stream closure;
- Gemini transcription and WAV synthesis request normalization;
- provider provenance plus text and audio response limits;
- exact, case-insensitive wake-word gating before Core execution;
- `RequestSource.VOICE` identity through the complete Core boundary;
- audio overwrite/release by default and explicit in-memory retention;
- interruption while listening, processing, and speaking;
- per-stage timeout and sanitized configuration/device/provider failures;
- single active-session admission and bounded continuous turns;
- optional bootstrap wiring without network, credentials, or audio hardware.

The Phase 11 regression coverage includes:

- validated RGB images, PNG encoding, bounds, and deterministic pixel hashes;
- irreversible region and automatic taskbar redaction;
- native screen-source dimension and allocation limits;
- explicit consent disclosure, expiry boundary, exact binding, and one use;
- rejection of altered grant identity, changed purpose, and changed regions;
- capture, redaction, stale-frame, analysis, timeout, and interruption states;
- source, capture time, dimensions, hashes, transformations, and consent provenance;
- raw and processed image overwrite/release plus explicit retention clearing;
- Base64 image normalization plus media/detail/size/count validation in the
  OpenAI-compatible adapter;
- prevention of image payload persistence in conversation history;
- capability-aware selection of the dedicated vision model;
- a deterministic image through the real Core and provider gateway.

The Phase 12 regression coverage includes:

- URL normalization plus scheme, credentials, hostname, and port rejection;
- IPv4, IPv6, mapped-address, mixed-DNS, localhost, and metadata SSRF blocking;
- IP-pinned retrieval and complete validation of every redirect target;
- HTTPS downgrade, redirect, status, byte, character, MIME, encoding, and
  attachment limits;
- active HTML element removal, publication-date parsing, and injection signals;
- strict SearXNG JSON normalization, safe-result filtering, and deduplication;
- bounded multi-source collection, freshness classification, source hashing,
  cross-checking, explicit uncertainties, and citation referential integrity;
- read-only untrusted-content tool contracts and offline deterministic reports;
- environment configuration and disabled-by-default bootstrap behavior.

The Phase 13 regression coverage includes:

- complete navigation parity with the approved eleven-screen prototype;
- strict black, white, and neutral-gray design tokens for both themes;
- validated UI state and conversation message models;
- live provider, model, memory, task, tool, and optional-service snapshots;
- real text requests through Core with preserved conversation context;
- fail-closed voice, vision, and research actions when unconfigured;
- controller bridges for enabled voice, one-use vision consent, and research;
- background event-loop execution, idempotent shutdown, and import safety;
- Python compilation of all desktop modules without constructing a window.

The packaged-runtime gate bundles Tcl/Tk and verifies its required files before
launching the frozen process. On the current sandbox, Tcl's native file API
cannot read an `init.tcl` that Python and the operating system can both read, so
the release evidence records `native_ui_rendered=false`. Controller and
presentation modules remain fully deterministic and headless-testable, but this
environment-limited result is not equivalent to a native render pass.
Since 22 August 2026 this limitation no longer applies on the development
host: Tcl/Tk 8.6.15 renders all eleven screens natively from source and from
the frozen build.

The Phase 14 regression coverage includes:

- recursive secret-key and credential-shaped message redaction;
- stable non-reversible trace correlation and bounded attributes;
- event hash chaining, filtering, tamper detection, and anchored eviction;
- validated fixed-capacity counters, gauges, and duration summaries;
- concurrent sync/async health checks with per-check timeout containment;
- sanitized exception handling and overall degraded/unhealthy aggregation;
- read-only health, event, and metric tool contracts and verification;
- bootstrap health checks for Core, provider, memory, tasks, and ledger;
- automatic Core start, completion, timing, and sanitized failure events;
- live event integrity and count visibility in the desktop diagnostics screen.

## Phase 15 acceptance automation

`python scripts/verify.py` is the single deterministic acceptance entry point.
It checks installed dependency consistency, compiles all application and test
modules, fixes `PYTHONHASHSEED` to a non-default value, and runs the complete
suite. The same command runs in a least-privilege Windows GitHub Actions job.

Phase 15 additionally verifies the complete offline application path, imports
every `app.*` module without external actions, serializes every tool contract,
enforces confirmation for medium-or-higher risk contracts, and scans runtime
source for forbidden dynamic execution and shell-enabled subprocess patterns.
The authoritative gate list is in `docs/ACCEPTANCE.md`.

The Phase 16 regression coverage includes:

- strict active and queued Core admission limits;
- immediate saturation rejection, queued timeout, and cancellation cleanup;
- lease accounting after success and exception paths;
- one hundred concurrent offline requests under a five-second outer budget;
- provider circuit threshold, open-state fast failure, half-open single probe,
  successful recovery, and failed-probe reopening;
- gateway call suppression and externally visible circuit health;
- environment parsing and validation for every reliability bound;
- admission rejection diagnostics and metrics without request-content leakage.

The Phase 17 regression coverage includes:

- path-contained cleanup of build, distribution, and release directories;
- strict frozen smoke-report validation and an explicit environment-limited
  classification that cannot pass without all Tcl/Tk/icon runtime files;
- deterministic portable archive structure;
- SHA-256 evidence for only the declared release artifacts;
- application, executable, window, installer, and shortcut icon integration;
- pinned PyInstaller dependencies and Authenticode-checked build-tool bootstrap.

## Nova shell regression coverage (5 September 2026)

`tests/test_ui_nova.py` exercises `NovaBridge` against the real
`DesktopController` and a recording window, without opening WebView2:

- import safety (importing `app.ui.nova` creates no window);
- `_jsonable` handling of dataclasses, enums, UUIDs, dates, paths, mapping
  proxies, sets, bytes, NaN/infinity, and unknown objects;
- `boot()` returning the live snapshot, restored history, and a settings
  snapshot that never contains the API key;
- empty-command rejection before the async runner exists, a second command
  rejected while the first runs, ordered stream flushes, and core failures
  reported as system messages without exception details;
- voice start/stop, double-start rejection, honest session failure, and the
  shared text/voice conversation;
- vision and research failures and unconfigured services reported to the page
  without internals, plus research source bounds;
- approval tokens: masked parameters, single use, non-boolean answers denied,
  timeout denied, denied without a ready page, and denied on shutdown;
- completion callbacks that fire synchronously (work finished before the
  callback was registered) neither deadlock nor leave the busy guard set;
- WebView2 runtime detection (loader first, registry fallback, broken loader
  tolerated) and the classic-shell fallback with its notice and diagnostics
  event;
- frozen and source asset resolution, explicit missing-asset errors, the
  per-user WebView2 profile location, exactly-once resource release on window
  close, and the `--classic` / import-fallback paths of `launch_desktop` and
  the packaged entry point;
- (cinematic interface, 5 September 2026) runtime facts in `boot()` through
  an allow-list with no secret field, tool executions pushed as activity,
  diagnostic events pushed live with redaction intact, observers following a
  runtime rebuild, the stored-conversation lifecycle (open, new, archive,
  refusal while busy), memory search/update/forget/delete with the
  confirmation flag, `system_status()` with measured health, metrics,
  provider, admission and process figures, bounded and filterable
  diagnostic events, the permission audit trail, in-page pause through the
  tray path, compact-mode geometry (restore, resize, move, on-top,
  maximize, failure reporting), throttled microphone levels, approval
  payloads carrying the request source and tool description, and the tray
  `Sesli mod` action reporting failures through the tray.

`tests/test_tools_executor.py`, `tests/test_diagnostics_service.py` and
`tests/test_voice_audio.py` cover the observation hooks the page relies on:
executor observers on both execution paths (a failing observer never changes
a result), diagnostics subscribers called after sealing, and the microphone
level callback that never affects capture.

`tests/test_nova_web.py` keeps the page honest without a browser harness: it
parses every script under `web/js/` with QuickJS (a syntax error fails the
gate), checks that the asset list matches the directory and the order in
`index.html`, that every element id the scripts reference exists, that every
`Bridge.*` and `call("…")` invocation exists on `NovaBridge` with a
compatible arity, that the demo bridge mirrors the Python API exactly, that
lifecycle hooks are not exposed, that every Python push kind has a page
handler, that demo mode is opt-in (`?demo=1`, never inside pywebview) and
never a fallback, that boot lines come from the real snapshot and unavailable
metrics are labelled rather than invented, that the failure, confirmation,
palette, voice-stage, compact and drawer UI exist, that deleting the key and
deleting a memory ask first, that the approval overlay offers no blanket
permission, that the palette and compact mode are wired, that design tokens
live only in `tokens.css` and the motion vocabulary is shared, that the
visible text is Turkish, and that the stylesheet ignores the OS reduced-motion
setting. For the Medical screen it runs the page's own helpers under QuickJS:
figure markup and the review order of the exam results, the z-up mesh frame
of a BodyParts3D asset, an anchor outside the mesh that is drawn nowhere, the
bell-ringer's lenient Latin matching, the free turn about the viewer's axes
(right is right, down is down, past the old clamp, orthonormal after two
thousand steps), screen-aligned pan, the black stage and the matte distinct
palette, the schematic map hidden by attribute (an SVG has no `hidden`
property), the shader without a highlight, a card's tables, and a scene with
a palette drawn and hidden structure by structure.

The packaging tests require the spec to collect the `web/` directory
recursively below `app/ui/nova/web` (recomputing the mapping for every asset
in `shell.WEB_ASSETS`) and the frozen smoke report to list the sorted asset
list as `nova_assets`.

Run the focused set with:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_ui_nova.py tests/test_nova_web.py tests/test_windows_packaging.py -q
```

### Manual live acceptance

WebView2 behaviour is verified by hand on a Windows 11 host; the 5 September
2026 run is recorded under "Verified live on this host" in
`docs/PROJECT_STATE.md`: boot, every screen, wheel and keyboard scrolling, a
real core reply, the scrolled-up "yeni mesaj" pill, the voice HUD and its
silence close, fail-closed vision and research, a denied approval with no side
effect, the connection test, the cancelled delete-key dialog, preference
persistence across a restart, and a clean Alt+F4 exit, from source and from
the frozen executable. A spoken voice turn still requires a person at the
microphone.

The cinematic interface was verified the same day from source: real boot
lines, every screen, the command palette, a Gemini command whose
`launch_windows_application` request produced the Turkish permission
overlay, its denial rendered as an inline pill and a drawer timeline, compact
mode in and out, and live diagnostics. Visual checks of the voice stage, task
timelines, memory cards and the approval overlay used the labelled `?demo=1`
page in a browser (`python -m http.server --directory app/ui/nova/web`).

## Plugin runtime regression coverage (5 September 2026)

- manifest validation: closed field set, id/version/entry-point formats,
  unsupported capabilities, tool and parameter limits, duplicate names,
  `critical` rejected, risk floor and forced confirmation, file size and
  link checks (`tests/test_plugin_manifest.py`);
- discovery: valid and rejected plugins side by side, missing root, no code
  import during discovery, junction and symlink refusal
  (`tests/test_plugin_discovery.py`);
- runtime: disabled by default, enable persists and registers namespaced
  tools with `source="plugin:<id>"`, executor argument validation, disable
  and stop unregister tools and unload modules, entry-point failures are
  isolated with class-name-only errors, consecutive failures quarantine and
  an explicit enable re-arms, honest timeouts, non-JSON and oversized output
  rejected, medium-risk tools need a bound approval, rediscovery keeps
  running plugins and drops removed ones, corrupt state fails closed
  (`tests/test_plugin_runtime.py`);
- security: no shadowing of registered tools with full rollback, link or
  missing entry modules rejected, undeclared or missing implementations
  rejected, risk floor and source on contracts, rejected plugins cannot be
  enabled, the plugin context carries no secrets or services
  (`tests/test_plugin_security.py`);
- wiring: off by default, discovery without trust registers nothing, a
  trusted plugin starts at bootstrap and stops on close, settings parsing
  and validation, and plugin tools satisfy the global contract invariants
  (`tests/test_plugin_bootstrap.py`).

## System tray regression coverage (5 September 2026)

`tests/test_ui_tray.py` covers the menu model (labels follow pause and
visibility state), the controller (dispatch, unknown items, post-exit
silence, failing actions reported without killing the tray thread), the
service with and without a backend, icon resolution from the frozen bundle,
the single-instance guard with real named kernel objects (second acquire
fails, activation reaches the first instance, release frees the name), the
bridge pause gate (commands, voice, vision, and research refused with the
Turkish notice; nothing submitted; resume restores service), Nova shell
wiring (close-to-tray cancels the close and hides, Aç shows, Duraklat gates
and pushes `paused`, Tanılama navigates, Çıkış destroys and lets the close
proceed, the icon lives exactly as long as the window, a failing tray is
recorded and the window keeps working), the desktop entry (second launch
notifies and exits; first launch watches and releases; classic also holds
the instance), and settings parsing. The real WinForms icon is exercised by
an opt-in test (`JARVIS_TRAY_LIVE_TESTS=1`).

## Notification persistence coverage (5 September 2026)

`tests/test_notifications.py` covers the store round trip (publish,
collapse, mark read, dismiss, clear, reload order), the bound on disk and
storage failures that never break the centre; `tests/test_ui_nova.py`
covers a bridge restart keeping an unread reminder and an empty path
keeping the centre in memory; `tests/test_settings.py` covers the setting.

## Clock and asset-cache coverage (5 September 2026)

`tests/test_core_engine.py` covers the Turkish clock line and its presence
in every model-bound system prompt, and the direct clock answer (phrases,
non-matches, no provider call); `tests/test_ui_nova.py` covers the WebView2 cache
refresh (cleared on first launch and on changed assets, kept when unchanged,
other profile data untouched, missing web root a no-op).

## Scheduled routines coverage (5 September 2026)

`tests/test_routines.py` covers clock parsing and schedule descriptions, the
next daily occurrence, create/list/delete with every validation, the count
bound, atomic due claims that move the next slot, deferral, run records and
the registered tools' risk levels. `tests/test_tool_schema_selection.py`
covers the routine intents; `tests/test_ui_nova.py` covers the boot payload
and watch, a due routine run through the core with the approval callback
and its own persisted conversation, failure reporting without internals,
deferral while paused, and confirmed deletion with ledger events;
`tests/test_nova_web.py` covers the Tasks-screen panel and its editor;
`tests/test_ui_nova.py` also covers creating routines from the desktop with
the tool's validation messages and the `routine.created` ledger line.

## Provisional transcription coverage (5 September 2026)

`tests/test_voice_low_latency.py` covers the microphone's provisional offer
(after the shorter silence, stale once speech resumes, a prefix of the final
capture, invalid durations refused) and the setting; `tests/test_voice_session.py`
covers the provisional transcript used when only silence followed, discarded
when speech continued, and inputs without the callback.

## Start-up coverage (5 September 2026)

`tests/test_provider_openai.py` covers the client built on first use (none at
construction, one afterwards, unconfigured stays unconfigured);
`tests/test_voice_low_latency.py` covers the shared lazily built speech client
(nothing built at construction, one client for both adapters, warm-up builds
it, no key means no client).

## Voice time-to-first-audio coverage (5 September 2026)

`tests/test_voice_session.py` covers the first sentence synthesized while the
reply streams, the discard when the final text differs, engines without a
stream callback, the streamed opening chunk played as a stream, the empty
cloud stream falling back to the local voice, and the closed-sentence
detector. `tests/test_voice_models.py` covers `SpeechStream` priming and
replay; `tests/test_voice_audio.py` covers sounddevice streaming, interruption
(device closed), device failures and the buffered fallback;
`tests/test_voice_low_latency.py` covers the Gemini streaming synthesizer, its
single-chunk fallback and the adapters' warm-up; the voice service's warm-up
and the bridge's combined warm-up ledger line are covered in
`tests/test_voice_session.py` and `tests/test_ui_nova.py`.

## Latency and Gemini 3 tool-turn coverage (5 September 2026)

`tests/test_provider_gemini.py` covers the signed replay (unsigned calls
get the skip marker, signed ones are untouched, the stored conversation is
not rewritten). `tests/test_provider_openai.py` covers the retry hint read
from a quota error body and the connection warm-up. `tests/test_core_engine.py`
covers streaming with tool calls (deltas folded, arguments appended,
signature kept, final answer streamed, `simple` task type on
finalization), the non-streaming provider fallback, and the action-model
quota path (single attempt, immediate fallback, cooldown, `request.model_call`
events and timers). `tests/test_provider_gateway.py` covers the single-attempt
flag and warm-up over configured providers; `tests/test_ui_nova.py` covers the
boot warm-up and its ledger line. `tests/test_ui_controller.py`,
`tests/test_conversation_engine.py` and `tests/test_nova_web.py` cover the
timing and tool-count reply chips (forwarded, persisted only when present,
rendered from real numbers).

## Notification centre coverage (5 September 2026)

`tests/test_notifications.py` covers the centre (bounded titles, bodies and
data; kind and severity validation; newest-first bounded listing; mark
read, dismiss, clear; dedupe within the window only; broken listeners;
concurrent publishing) and the reminder watch (leases acknowledged after
delivery and released with the error when delivery raised, a source without
leases polled as before, delivery and store errors swallowed, immediate
first poll, periodic polling, idempotent start and stop, restart).
`tests/test_reminder_delivery.py` covers the reminder store's lifecycle with a
controllable clock (see the reliability repair entry of 8 September 2026 in
`docs/PROJECT_STATE.md`). `tests/test_ui_nova.py`
covers the bridge: the boot payload and the watch lifecycle, due reminders
reaching the page and following a replaced runtime, the centre API,
unattended routing to the OS notifier with the setting and the in-flight
bound, replies, approvals (no parameters), vision and research results on a
hidden window, screen observations and collapsed ledger warnings, tray
actions and `launch_nova` wiring. `tests/test_nova_web.py` checks the page
declares the bell and popover, handles the `notification` push, reports
visibility, and never invents entries.

## Safe-filesystem regression coverage (5 September 2026)

`tests/test_filesystem_snapshots.py` covers the store: sealed bytes with a
verified manifest, oversized and invalid captures refused, tampered payloads
never handed back, unreadable manifests ignored, oldest-first pruning and
bound validation. `tests/test_filesystem_recovery.py` covers recoverable
delete and undo (including undoing an undo), refusal of non-empty directories
and links, fail-closed delete without a store, snapshots on every overwrite,
an oversized file blocking the mutation, name and glob search that never
follows links, index and result bounds, dry-run plans (nothing touched,
digest required, single use, drift refusal, conflict reporting, stop at the
first failure) and the critical-directory block. `tests/test_ui_nova.py`
covers the bridge: unavailable without Windows integrations, grant and revoke
with confirmation and ledger events, the native folder picker on the UI
thread, and snapshot listing and confirmed restore.

The Medical Academy regression coverage (`tests/test_medical_*.py`) includes:

- Turkish/Latin folding, heading-aware chunking with character offsets that
  map back into the page, page-range parsing and question similarity;
- the curriculum's structure, ranking and subject resolution, including the
  negative case where no subject is named;
- alias resolution across Latin, Turkish and English, Turkish suffix handling
  in free text, and the guard that stops an ordinary word from becoming a term;
- the SQLite store round-tripping every record type, in memory and on disk;
- PDF and text ingestion, deduplication by digest, the real status sequence,
  the failure path, and retrieval that never cites a page it does not hold;
- deterministic intent parsing over the specification's own command list, in
  Turkish and English, plus the non-medical requests that must stay untouched;
- question validation, letter shuffling, grading, similarity protection and
  exam analysis; mastery levels, review scheduling and adaptive difficulty
  against an injected clock;
- exam import from messy text, and the style profiler's evidence-based
  features, confidence thresholds and never-guess-the-answer-key rule;
- the Anatomy Lab's structure cards, movement axes and deterministic quizzes,
  and the asset registry that refuses an unlicensed or missing model, reads
  scenes from the manifest (a scene naming an unregistered structure loses it,
  an empty scene is reported), reports the up axis and both pin forms with
  their confidence, and passes a scene's card, palette and note with malformed
  colours dropped;
- the BodyParts3D importer: the OBJ merge with index re-basing, every scene's
  mapping checked against the curated data, derived pins on a synthetic bone
  (no pin without a rule or enough vertices; the skull rules put the foramen
  magnum low and midline and the crista galli high, and give a hole no pin),
  and the hand-placed pin that survives a re-import;
- the region cards and their tables: the neurocranium's fossae and foramen
  tables with the nerves and vessels they must name and the fixed table shape;
  the twelve cranial nerves (an overview table of the right shape, a card each
  with its skull exit and lesion, exits agreeing with the foramen table, the
  cranial-nerve fields staying off the limb nerve cards, the accessory nerve's
  lesion sign in the right direction); the viscerocranium (orbit-wall and
  sinus-drainage tables, a card per facial bone) and the fourteen-bone cranium
  scene; the vertebral column (region and curvature tables, atlas, axis and the
  typical vertebrae, the six-group spine scene); the craniovertebral and jaw
  joints; the thoracic cage (rib classification and rib parts, the rib-cage
  scene); the abdominal wall (rectus sheath and inguinal canal); and the
  head-and-neck and lower-limb vessels (external-carotid branches, the carotid
  sheath, the arterial line and the femoral triangle, both limbs holding four
  arteries and two veins);
- the table-recall quiz that turns a region card's tables into questions:
  short cells only, a dash placeholder never offered as an answer, candidates
  pooled and shuffled across columns, and the catalogue-wide guard proving no
  table distractor is a second true answer (`test_medical_anatomy.py`);
- the academy facade, its four tools, the tutor's decisions and question
  generation against a fake model client;
- the core engine's augmentation hook: prompt replacement, tool narrowing,
  direct responses, memory suppression, and the failure paths where a broken
  augmenter must not take the turn down;
- the pipeline layer (`test_medical_pipelines.py`): schema validation and
  string coercion, JSON extraction from a fenced or chatty reply, the single
  repair round and the timeout and transport failures around it, BM25 ranking
  with synonym expansion, and evidence blocks that mark their own truncation;
- exam generation and the sitting (`test_medical_exams.py`): every refusal the
  generator can raise, the quality filter and the similarity guard, lecture
  grounding and the `lecture_derived` origin, bank selection including the
  wrong-answers-only paper that is never padded, and the lifecycle where the
  answer key stays hidden until the sitting ends;
- the tutor and the study session (`test_medical_tutor.py`): which turns it
  declines, the grounding it reports, the chat quiz end to end (answering,
  skipping, stopping, the oral exam), and the honest refusals for a missing
  document, profile or structure;
- figure questions and the interactive paper (`test_medical_figures.py`): only
  pages the vision pass described may become figures, a figure question is
  anchored to the page it shows, a figure index the model invents yields no
  figure, a subject-only item still gets a topic, a typed "beni sına" opens the
  paper while the spoken one keeps the chat quiz, and the results name the
  blanks when nothing was wrong;
- the facade the Nova screen calls (`test_medical_facade.py`): the dashboard
  and subject tree over real data, document analysis and comparison including
  a model-stated page the material never had, note citations, question-bank
  filters, professor import and re-import, and the lab reporting a missing 3D
  model rather than drawing one;
- lecture sets and presentations (`test_medical_library.py`,
  `test_medical_convert.py`, `test_medical_documents.py`): folder names to
  subjects and tags, the set's counts computed from its documents, batch
  processing that notifies once and retries a failure only when asked, the
  folder job's honest report; the converter cached by the deck's bytes, loud
  about an empty export, refusing what it should and admitting PowerPoint's
  absence; a deck stored as the PDF made from it and deduplicated by the deck,
  a numbered file name replaced by the first heading, an empty text file
  refused;
- professors from the material (`test_medical_professor.py`,
  `test_medical_library.py`): every spelling of a rank, the lines that are not
  people, the same person across initials and a broken surname, a split name
  joined, the lecturer in a file name, a compiled paper cut at its headings
  while a lecture that quotes a doctor is not, lectures stamped with their
  lecturer and their review questions filed under them without a key, and the
  professor-style paper drawing on that lecturer's own lectures; a published
  book keeping its questions while its editors stay out of the lecturer list,
  and a lecturer read from a described cover slide but never from a portrait
  inside a lecture; an exam system's export parsed with its owner, department,
  committee, key suffix and the student's mark, every question filed under its
  own owner with the paper's key, and a scanned paper transcribed (an outage
  leaving the page untouched) and then mined like a text one;
- sesli anlatım (`test_medical_narration.py`): the script from the model
  validated and clamped to the pages it was given, the material read as it
  stands without a model, a failed batch falling back with a note; the player
  speaking every chunk in order, pause/resume/next/prev/stop landing between
  chunks, a question answered and the same chunk resumed, checkpoints that
  continue on silence and answer a question, a broken speaker stopping the
  narration with a named error; the speaker's local default and its fallback
  from a spent cloud quota; the service refusing a second narration and
  answering honestly without a model;
- the vision pass under a spent quota (`test_medical_library.py`): two
  refusals end the pass with the pages left pending and the reason recorded,
  nonsense from the model fails only that page, and resuming later describes
  the pending pages and reports an empty run honestly;
- the shell side of all three (`test_ui_nova.py`): the folder picker, the
  background folder import with its report and the quiet batch notification,
  the pause gate, mining reported to the page, narration state and commands,
  and the voice session and the narration refusing to open over each other.

## Medical Academy expansion coverage (8 September 2026)

- the store's second schema (`test_medical_store.py`): the migration applied
  once and recorded, records saved, listed by kind and subject key, updated
  and deleted, media round-tripped, `transaction()` rolling back together;
- understanding (`test_medical_understanding.py`): confidence validated and
  stored, a repeated submission id recorded once, reasoning sampled two per
  exam and always on an open finding, the rule classification, a
  contradictory reasoning opening a hypothesis while the mark stays right,
  two distinct pieces of evidence supporting a finding while two guesses do
  not, challenge/dismiss/reopen with history, the diagnostic question hiding
  its expected answer and its verdicts, the repair session's five steps and
  the transfer answer, resolution only after the delayed follow-up,
  invalidation withdrawing evidence, the assessment without a model;
- prerequisites (`test_medical_prerequisites.py`, `test_medical_study.py`):
  seeded edges with provenance, suggestions pending until confirmed, a cycle
  refused, a concept named from a Turkish fragment, the diagnosis asking at
  most three bank questions, skip, shorten, redirect, the located path with
  estimates and the objective, and the honest fallback without prerequisites;
- source support (`test_medical_review.py`): every status from the review
  verdicts, the gate keeping supported items and quarantining the rest with
  counts, the batch limit, stale and unavailable sources by hash, imported
  keys untouched, flags of every kind, invalidation correcting mastery and
  keeping the attempt, generation with the gate on and off;
- the planner (`test_medical_planner.py`): scope inferred and confirmed,
  reading logged as studied not demonstrated, the six coverage states, a day
  never over budget, missed days, overload with numbers and the uncovered
  list, estimates blended from actual durations and long items split,
  replans keeping completed and manual work, the reminder, "Bugün";
- histology (`test_medical_histology.py`): a specimen needing a basis for its
  name, the crop rendered and cached, the source change detected, sessions
  that hide the answer and show each specimen once, identification with
  folding, the explanation graded apart from the identification, the timed
  blank, repeated specimens reported;
- the connected journey and the bridge (`test_medical_study.py`,
  `test_ui_nova.py`): scope → today → confidence → finding → diagnosis →
  repair → coverage across a restart, a paper marked at the end recording its
  events once, invalidation through the workflow, malformed calls failing
  closed, sync and async study actions, confirmation for destructive ones;
- the page (`test_nova_web.py`, QuickJS): confidence chips and their locked
  state, the reasoning box, the support chip's "puansız", "Bugün" with a
  labelled estimate and the overload in words, coverage chips only for
  states that occur, a specimen hidden in a session and revealed after, the
  results' repeated-specimen caveat, reasoning prompts only for wrong answers
  with an event, a finding rendering its diagnostic question but never its
  expected answer; and the static gates: the three screens declared, the
  tabs, the answer's confidence and submission id, the flag in three places,
  destructive study actions confirmed, study jobs dispatched by action, and
  the prerequisite name box with confirm and reject.

## Live pass and the tests it added (9 September 2026)

Driving the five features in the running window produced six fixes, each
pinned by a test that fails on the unfixed code:

- a practice paper records one event per answer even when it is finished
  (`test_medical_study.py`);
- a plan with a confirmed scope is laid out the moment it is created, and a
  manual activity fits beside fixed work while automatic work makes room
  (`test_medical_planner.py`);
- a question without concepts is read for one from its stem, its key and its
  own explanation, is never filed under a distractor, and is otherwise
  anchored to itself; the plan still finds such findings by topic
  (`test_medical_understanding.py`);
- the repair explanation prompt carries the question, the chosen option and
  the key, and forbids restating the key as the mistake
  (`test_medical_understanding.py`);
- the page names an assessor, an explanation grade and a prerequisite's
  provenance in Turkish (`test_nova_web.py`, `test_medical_study.py`).

Two pre-existing flakes were repaired at the same time: the source-review
quarantine test no longer depends on a shuffled option letter, and the
desktop UI pump test waits on the pump instead of racing a one-second clock.

## Atlas pipeline coverage (9 September 2026)

`tests/test_medical_anatomy.py` covers the Z-Anatomy pipeline without Blender
and without the atlas, by building synthetic export packs:

- every structure and every landmark the allowlist would export is nameable by
  the curriculum data, the two scenes are disjoint and stay inside the
  allowlist, no source object is exported twice, and the five systems (bone,
  muscle, nerve, artery, vein) are all represented;
- an install keeps another dataset's entries, writes a new immutable directory,
  leaves an installed mesh byte-identical on the next install, keeps a manifest
  snapshot, and produces a manifest the asset registry loads with its licence,
  attribution, frame and approximate pins intact;
- twelve rejections: a mesh that does not match its checksum, a different
  revision, different source objects, a missing licence or attribution, the
  wrong frame or side, a triangle count that disagrees with the file, a path
  outside the pack, altered scenes, an incomplete allowlist, a pin outside its
  bone, and a pin the curriculum cannot name;
- a scene that would mix two coordinate frames stops the install;
- no module under `app/` imports `bpy` or `mathutils`, importing the exporter
  does not import Blender, and the exporter opens the blend as data
  (`use_scripts=False`, automatic script execution refused).

- the `hidden` attribute is honoured: no class on an element the page hides may
  set `display` without a matching `[hidden]` rule (`test_nova_web.py`). The
  narration panel failed this and was showing empty on every Medical screen.

## Bilateral atlas expansion (9 September 2026)

- `test_atlas_catalog.py`: bilateral carpals/tarsals, metacarpals/metatarsals
  and phalanges, representative face/neck/lower-limb muscles and nerve/vessel
  names, unique IDs and one bounded discovery scene per object. Negative
  classification covers annotations, teeth, cavities, tendons, ligaments and
  retinacula. An unpinned blend is refused before Blender is imported.
- `test_medical_anatomy.py`: a full-pack addition becomes visible only when
  installed, preserves curated lesson/landmark content, and rejects invalid
  normal references even when the file checksum is valid.
- `test_nova_web.py`: delayed old-scene replies cannot contaminate a new scene;
  partial/total model failures settle instead of leaving permanent loading.
- `test_windows_packaging.py`: smoke reports must include atlas_catalog.json,
  which the existing JSON data collection already bundles. No installer is
  distributed by this change.
- Real pinned source inventory was regenerated with portable Blender 4.5.9
  with embedded scripts disabled; the rebuilt catalogue was byte-identical.
  Geometry validation checks every active file, not just representative scenes.
  Live visual checks supplement this; they are not a clinical accuracy audit.
  Verified in a real isolated Nova window: eight long Turkish notifications
  including unbroken filenames, region picker contrast, left-hand muscles,
  right-foot bones, right head/face muscles, fullscreen and Escape exit.
  All 1,634 active meshes passed the pack validator (75 scenes, 7,563,067
  triangles); model-by-model clinical visual review is not claimed.
- atlas structures linked to curated lessons (`test_atlas_catalog.py`): the
  kind must agree (the tibial artery keeps the artery card, not the muscle
  one), a linked structure keeps its own id and name while gaining the
  lesson's sections and naming that lesson, an unlinked one keeps its plain
  source card, and the lesson itself is unchanged.

## Post-merge atlas review (9 September 2026)

- A real-catalogue rectus femoris regression reproduced the quadriceps
  group's origin/action being assigned to that individual model. It now
  retains only a related-lesson link and cannot generate group-fact quiz
  answers under the individual muscle's name. Primary-name matches still
  inherit lesson sections and preserve the source English name.
- Ambiguous same-kind aliases are rejected for both catalogue orders; a
  wrong-kind candidate cannot interfere with a unique valid match.
- Three deterministic async regressions reproduced late structure replies,
  late mesh replies and structure replies arriving after a scene change.
  A selection generation guard now rejects each stale reply. These are
  windowless tests with deferred test responses, not live provider calls.
- Review verification: `scripts/verify.py` completed with 2,472 passed and
  6 skipped in 278.95 seconds; dependency integrity and compilation passed.
  Targeted atlas/anatomy/Nova checks: 156 passed. Read-only installed-atlas
  acceptance loaded all 1,597 supplemental cards and verified the real rectus
  model, source identity, related group lesson and absence of misleading quiz.
## Tray and window-thread coverage (10 September 2026)

`tests/test_ui_tray.py` pins the deadlock that stopped the desktop shortcut
from reopening a hidden window:

- the `closing` callback returns `False` at once and touches the window not at
  all; the hide, the tray notice and the bridge's own push all happen on
  another thread, and the test fails if anything evaluates JavaScript on the
  thread that delivered the event;
- pressing close repeatedly queues one hide;
- an activation signal that arrives while the hide is still queued runs after
  it, so the window ends up visible rather than hidden;
- `WindowWorker` itself: one job per key while one is pending, a failure
  reported to its owner instead of swallowed, the thread surviving that
  failure, and a stopped worker refusing further work;
- the existing tray, pause, navigation and exit paths still hold, and a build
  without a tray still closes normally and releases the controller.

All three fail on the unfixed code (two by assertion, one because there is no
worker to exercise).

## The user test of 13 September 2026 and what pins its repairs

`docs/qa/2026-09-13-tip-akademisi/TEST_RAPORU.md` recorded eighteen findings
from a real WebView2 session; `DUZELTME_RAPORU.md` beside it records the
repair and the live re-test. The regressions live in
`tests/test_medical_scoring.py` and page-side in `tests/test_nova_web.py`:

- the scoring decision table (key from a person, cited source, no source,
  reviewed statuses, invalidation) and the reviewer gate following the
  setting without ever scoring an unsourced or invalid item;
- a practice paper with a study-only question: shown and explained, no
  mastery, no understanding event, listed under *Değerlendirme dışı* with
  the reason, finishing twice changes nothing; a paper of only study items
  has no percentage; a simulation applies the same decision once; a finished
  paper keeps the decision of its day after an invalidation;
- blanks are not wrong answers and a concept is weak only on answered
  questions; the analysis applies a scoring map and reports what it left out;
- bank selection honours the document and page range, the professor and the
  figure switch, excludes study items unless asked, and says why it is empty;
  a paper is named and listed for what it holds; the bank view pages through a
  stable order with the matched total;
- histology: exact concept or a stable own id; the side list and the detail
  hide a specimen under a timed test; a name printed inside the crop keeps
  the specimen out of the blind test until it is masked; a late answer is
  late;
- the understanding check reports a pending assessment rather than a
  verdict; a note asked from another subject's document follows the document
  and is refused when the model says the pages do not cover the topic; a
  reading activity names its sources in order;
- the lab quiz describes a landmark the model cannot point at, and atlas
  concept ids are named through the lab;
- the job ledger: duplicates, timeouts, interruptions, a paper as a job, a
  second click answered with the first;
- the repair: unscored evidence removed, histology relinked, results
  recomputed, and nothing applied twice.

Page tests (QuickJS): the timed specimen hidden in every markup, the exam
list and result counting what was built and what counted, bank paging,
professor folding and source buttons, Esc leaving fullscreen before the
home-screen shortcut and the folding lab notice, the library panel keeping
room for its list, and the job-state push handled.

Live, on a repaired copy of the tester's database with the real model: the
flows in `DUZELTME_RAPORU.md`. Not covered: a physical keyboard (access to
the desktop was declined, so Esc was injected through the browser input
pipeline), microphone and voice, and a clinical review of the 718 questions.

## Flashcards (15 September 2026)

`tests/test_medical_flashcards.py`: the scheduler walked through learning,
review, a lapse and the clamps (half-up rounding pinned); previews say what
each button schedules; a topic builds fact and terminology cards once and
never from atlas mirrors; wrong answers become cards with the bank's own
key and nothing unscored; histology cards render the masked crop; occlusion
masks only labels the page prints, refuses the rest, and explains a page
with no text layer; the queue orders due before new under the daily budget
and skips suspended cards; an "again" card returns the same day; an answer
is recorded once per submission id; reviews never move mastery, findings or
attempts; the forecast splits backlog from each day's own load; deletion is
a confirmed bridge action. Page side (`test_nova_web.py`): the answer stays
hidden until revealed, the four grades are Turkish with their previews, and
the keyboard grades only after the reveal.

## Committee rehearsal, weekly summary, backups (15 September 2026)

In `tests/test_medical_scoring.py`: the rehearsal keeps the requested
distribution and subject order, draws only scored imported questions,
reports shortfalls without padding, is seed-stable, honours `unseen_only`,
and its result breaks down per subject; the weekly report counts only what
the records hold (minutes without double counting read activities, accuracy
over scored answers only, unscored answers apart, card grades, adherence,
streak and countdowns) and is honestly empty; backups rotate to the newest
copies while sparing before-repair snapshots, produce an openable database,
postpone the weekly copy while one is fresh, refuse a full disk, and report
completion to the page.
- Exports (`test_medical_scoring.py`, `test_ui_nova.py`): the question
  sheet holds no key or explanation, the answer key labels unscored items
  and lists sources, the note export keeps its references, and the bridge
  refuses a missing folder and never overwrites an existing file.

## General shell: brief, export, focus, geometry (15 September 2026)

In `tests/test_ui_nova.py`: window geometry round-trips through
`window.json` while nonsense frames (too small, off any screen, corrupt
JSON, an odd window object) fall back to defaults without touching the
close path; conversation export writes the visible turns only (system
turns never leak), speaks as "Sen"/"JARVIS", refuses missing folders and
unknown ids, adds a collision suffix instead of overwriting, and never
activates or switches the open conversation; the daily brief answers each
section from its own service, reports availability honestly, invents no
countdown, and writes its date in Turkish words.

In `tests/test_nova_web.py` (QuickJS): the brief builder shows what
exists, escapes reminder text, marks a week-out committee as urgent,
renders the honest empty and quiet states, and wires each row to its
screen; the focus clock formats remaining time exactly (half-started
seconds round up, never negative). The demo bridge mirrors the three new
methods without pretending demo file writes; the hidden attribute wins
over every panel class, the topbar buttons included.

## Web research backends (15 September 2026)

In `tests/test_research_search.py`: the DuckDuckGo provider requests the
no-script endpoint with a browser agent, decodes organic redirects to
their real URLs, drops ad routers and duplicates, decodes entities,
honours limit and day/month/year ranges, reports a challenge page as
zero hits and a non-200 as a SearchError, and its redirect decoder
refuses foreign hosts and missing `uddg` values. The Gemini grounding
provider turns grounding chunks into hits with support-segment
snippets, keeps the key in a header and out of every URL and error,
reports an ungrounded answer as zero hits, and rejects bad payloads,
parameters and model names. `tests/test_settings.py` pins the provider
choices; `tests/test_bootstrap.py` pins default-on DuckDuckGo wiring,
Gemini wiring with a key and honest silence without one; the tool
schema selector exposes `research_web` for arastir/guncel/haber turns
(`tests/test_tool_schema_selection.py`); the extractor survives meta
tags with neither property nor name (`tests/test_research_extractor.py`);
and medical tutor turns include `research_web` in their allowed tools
(`tests/test_medical_tutor.py` keeps passing with the widened set).

## Conversation search (15 September 2026)

`tests/test_ui_nova.py`: the bridge refuses one-character queries,
matches across visible turns with Turkish folding ("BÖBREK" finds
"Böbrek", "idrar" finds "İDRAR"), never matches system turns, carries
the speaker and an exact excerpt, and reports zero hits honestly.
`tests/test_nova_web.py` (QuickJS): the drawer markup escapes excerpt
HTML, highlights the match with <mark> case-insensitively in Turkish,
and renders the empty and error states in words.

## Morning brief notification (15 September 2026)

`tests/test_ui_nova.py`: the clock fires only past the target, once per
local day, catches up after yesterday's stamp, and disables itself on
any malformed time; the notification line counts exactly what exists
and says in words when nothing is pending. `tests/test_settings.py`
pins the HH:MM validation; `tests/test_notifications.py` pins the new
"brief" notification kind. Live: with the target set a minute in the
past the running window published "Günün özeti · QA Akademi 13 Eylül:
5 gün kaldı · sırada Omuz kuşağı … · 14 kart tekrar bekliyor · 1 açık
bulgu" within one poll interval, and the stamp file prevented a second
send.

## Assistant settings card (15 September 2026)

`tests/test_ui_api_settings.py`: desktop preferences round-trip through
the profile (times normalized to two digits), a legacy profile without
the new keys loads with defaults, malformed times are refused, and the
built runtime follows the profile with environment variables keeping
precedence. `tests/test_ui_nova.py`: the bridge refuses a bad time in
words, and a save rebuilds the runtime live - the new application
carries the new values and web research off means the research tool is
not registered at all. Verified live in the window: saving 07:45 with
research off flipped the WEB indicator and the runtime immediately;
restoring the defaults brought both back.

## Web sources under answers (15 September 2026)

`tests/test_ui_nova.py`: only a successful research_web result pushes
sources (other tools, failures and sourceless reports push nothing),
and the payload carries exactly title and url. `tests/test_nova_web.py`
(QuickJS): the chip row shows the bare host with the full URL riding
the tooltip, escapes titles and queries, gives an unparseable URL an
honest generic chip, and renders nothing without sources. Live: a
fresh "Nobel 2026" research answered honestly that the prize is not
announced until 5 October and wore five source chips.

## General state backups (15 September 2026)

`tests/test_state_backup.py`: every first-level state database is
copied even while one is held open mid-write (the uncommitted row
never leaks into the copy, quick_check passes), rotation keeps the
newest three stamped folders, a half-written .tmp folder from an
interrupted run is neither listed nor rotated as a backup, the weekly
due-check respects a fresh copy, and an empty state directory refuses
in words. `tests/test_ui_nova.py`: the bridge summary and on-demand
backup round-trip against the isolated state directory. Live: the
Ayarlar card's chip went from "henüz kopya yok" to "1 kopya · son: az
önce" and a real stamped folder appeared.

## Reminders surface (15 September 2026)

`tests/test_ui_nova.py`: the bridge lists active reminders as the
service reports them, creates from "+25" and "23:59" forms, refuses
empty text and free-text times in the service's own words, cancels
only with explicit confirmation, and the list reflects each change
(order-independent near midnight). Live: created "+90" from the
Görevler form, saw "15.09 10:20" listed, cancelled through the real
confirm dialog, and the honest empty state returned.

## Unarchive and the weekly summary on paper (15 September 2026)

`tests/test_ui_nova.py`: archive then unarchive round-trips a
conversation's status through the bridge, and an unknown id refuses in
words. `tests/test_medical_scoring.py`: the weekly export prints the
empty week in words, seven day rows, the report's own honesty note,
and keeps marked answers and finished-paper scoring as separate lines
- the live QA store had 12 marked answers against 38 scored questions
on finished papers, which one shared sentence would have turned into
nonsense.

## Markdown in assistant bubbles (15 September 2026)

`tests/test_nova_web.py` (QuickJS): bold, italics, inline code and
heading lines render with the asterisks gone; bullet and numbered
lists open and close properly; hostile HTML and script tags from the
model or a fetched page arrive escaped and inert; `3*4` and `a*b`
never become emphasis; and the source pins that only assistant bubbles
take this path while user text stays literal.

## Source links that open the browser (15 September 2026)

`tests/test_ui_nova.py`: open_external refuses javascript:, file: and
empty input before anything reaches the launcher, opens validated
http(s), and completes a bare domain to https. Live: the bridge
refused file:/// in Turkish and the chip builder emits both the
open-in-browser chips and the "rapor" chip.

## Research history and palette entries (15 September 2026)

`tests/test_research_cache.py`: recent() lists the newest questions
first with their source counts, and an entry expired for reads is
still listed for reopening. Live: the chips carried both screen-typed
questions and the model's own chat-turn queries, one click reopened
the cached report instantly, and the four new palette commands surface
as the top fuzzy hits for odak/hatırlat/yedek/dışa.

## Exam chip (15 September 2026)

`tests/test_nova_web.py` (QuickJS): the chip text builder hides
without a plan, hides past exams, says "bugün" on the day, and turns
amber within a week. Live: the topbar showed "🎓 QA Akademi 13 Eylül ·
5 gün" in amber against the QA store.

## Drawer date groups (15 September 2026)

`tests/test_nova_web.py` (QuickJS): the group label follows local
calendar days between midnights - 23:59 yesterday is still "Dün", the
week and month boundaries hold, and a future timestamp from clock skew
falls back to "Bugün" instead of inventing a group. Live: the
hundred-conversation drawer rendered under "Bugün" and "Bu ay".

## Mobile companion (17 September 2026)

`tests/test_mobile.py` drives the loopback server over real HTTP against
a booted mock-provider core: every API path and the event stream refuse
an unauthenticated request; a pairing code is single-use, a wrong code
fails, codes and sessions expire on the store's clock, six attempts in a
minute are rate-limited; mutations without the page header, with a
foreign Origin or a cross-site Sec-Fetch-Site are refused while a
forwarded host matches; logout and desktop revocation end the session on
the next request and close its live channel; a phone message reaches the
shared conversation pipeline (user turn with source "api" plus reply in
the same store the desktop lists, desktop state untouched); a repeated
client id never runs the command twice and an unknown one reports 404;
the event stream carries turn_started/turn_done; empty, oversized or
malformed input and a paused core are refused; approvals are bound to
the pending token (an unauthenticated decision is 401, a repeat or an
unknown token is 409, a denial fails closed); tasks list and unsupported
actions refuse honestly; the desktop card mints codes that pair and
revokes everyone without pushing the code to the page; the static shell
is stamped, the manifest and icons served, and traversal refused.

Browser checks (Claude's in-app browser, 360×780 and 412×915): pairing
screen → paired chat with "BAĞLI", no horizontal overflow at either
width, hidden badge, a real turn answered in the chat, the Görevler empty
state, the Ayarlar details, "yeniden bağlanıyor" while the desktop was
stopped, a message sent meanwhile marked "sonuç bilinmiyor", automatic
reconnection after the restart with the message marked "bilgisayara
ulaşmamış" and an explicit resend that then got its reply; the desktop
window listed the phone's conversation and its Telefon card showed the
paired device.

Not verified here: installation on a physical Android device, Tailscale
Serve connectivity, mobile-data access. Those need the user's devices.

## The whole desktop page on the phone (19 September 2026)

`tests/test_mobile.py`: `/nova/` and its assets are served only to a
paired phone (strangers are sent to the pairing screen), the shim is
injected before every Nova script and the page's own CSP travels with
it, traversal and unknown assets are refused; `/api/bridge/<method>`
needs a session, answers real bridge calls (`list_conversations`,
`search_conversations`, `refresh`), refuses the deny list with 403,
rejects private, unknown, ill-typed and wrong-arity calls without
crashing, and a `submit_command` from the phone runs the desktop's own
submit path (same conversation, same busy state, reply pushed to the
window); a desktop `_push` is mirrored as a `push` event on the phone's
channel while the window still receives it. Browser (360 px): Nova
booted over HTTP with `body.phone`, twelve rail items in the bottom bar,
the Academy dashboard with no overflow, a four-question rehearsal built
and answered from the phone, the Anatomy Lab with a live WebGL context
and the skull loaded, a chat turn answered through the mirrored pushes.

## Voice on the phone (19 September 2026)

`tests/test_mobile.py`: transcription needs a session and the page
header, accepts only mono 16-bit WAV under 2 MB (JSON, stereo, garbage
and oversized uploads refuse before the recognizer sees them), reports
silence as an empty text, passes 16 kHz PCM in the configured language
to the PC's recognizer, and says a provider failure in words;
synthesis strips markup before the voice, applies the desktop's
character limit, returns the cloud voice as audio/wav, falls back to
the local voice with an honest header, and refuses in words when both
are gone; a spoken submit from the phone is recorded as a voice request
in the shared conversation and appears in the desktop chat. `tests/
test_nova_web.py` (QuickJS): the shim's WAV encoder downsamples 48 kHz
float audio into a valid mono 16-bit 16 kHz file with the right header
and amplitude, and the RMS helper is exact.

## Audit repairs (20 September 2026)

`tests/test_task_service.py`: a resume is reported as the outcome it
actually reached - COMPLETED verified success, PAUSED partial with
`side_effects_may_continue`, FAILED and CANCELLED failures carrying the
task's error; a queued or paused task cancels through the manager, a
second cancel refuses, and an unknown id raises rather than reporting a
cancellation. `tests/test_planning.py`: the approval grant never
reaches `plan.json` (no operation id, no binding digest, no key) while
the rest of the step metadata persists, and saving does not mutate the
caller's in-memory plan. `tests/test_nova_web.py`: engine failure
strings map to Turkish in both the desktop card and the step timeline,
an unmapped one is shown verbatim, and the phone's task card reads the
`goal` key the server actually sends. `tests/test_settings.py`: the
execution budget defaults to 300 s, reads
`JARVIS_EXECUTION_TIMEOUT_SECONDS`, refuses values outside 1-86400 and
reaches the engine.

Live pass (QA instance, isolated state directory, the user's own JARVIS
untouched): twelve Nova screens and thirteen Medical Academy views with
no console error, no unhandled rejection and no horizontal overflow; a
chat turn, conversation list and search, memory, tasks, routines, a
reminder created and cancelled, notifications, a four-question committee
rehearsal answered and finished, a card graded, progress and the weekly
report, a real web research, the permission audit and system status; the
bridge refusing unknown and empty approval tokens; the mobile surface
answering 401 unpaired, 200 paired, 403 without the page header, 403 for
denied bridge methods, 404 for unknown ones, and returning real cloud
speech. Thread and handle counts after that exercise matched an idle
instance.

## The academy room (20 September 2026)

`tests/test_nova_web.py` gained six checks for the Medical Academy's own
interface: the room is declared and wired (`showScreen` enters and leaves
it, `bindAcademy` runs at boot, the opening and the topbar chip read one
countdown field); the palette lives in `tokens.css` under `body.academy`,
is bright, and its inks clear WCAG AAA (body, secondary) and AA (tertiary)
against the ground; the type is set heavier; `academyIntroLine` and
`academyGreeting` say only what the core reported and what the clock says;
the opening is skipped without motion and opens no audio context when
muted; the sections are grouped in the order they are listed. Two more
pin the night theme: its block comes after the daylight one, its inks keep
the same contrast floors, the switch lives in the side column and leaves
with the room, and the preference defaults to daylight.

Manual live acceptance: open `nova-demo` (`?demo=1`), press `Alt+3`: the
veil shows the trace, the heart and the title within three seconds and
lifts on its own; a click or `Esc` during it skips; `Esc` afterwards
returns to Komuta Merkezi with the rail back; the side column groups the
thirteen sections; "Ses kapalı" silences the next opening; at 375 px with
`body.phone` the column becomes a strip and nothing overflows.

## Research sources and the research room (20 September 2026)

`tests/test_research_sources.py` drives every source over a canned
transport keyed by host: GitHub facts as evidence, YouTube candidates from
the web index confirmed by oEmbed (and an unconfirmed one that says so),
Wikipedia Turkish first, PubMed's two-step lookup, arXiv's Atom entries,
Stack Overflow's gzip answer, Hacker News' fallback to the discussion
link, a site search held to its host, non-200 and bad JSON as errors,
interleaving with one failure named, the build wiring exactly the enabled
sources, evidence-bearing hits cited without a fetch, a web-only backend
refusing other sources honestly, the cache key telling a YouTube question
from a web one, and the tool accepting a source list and a site.

`tests/test_nova_web.py` pins the room: assets and script order, the ids
and the eight constellation nodes, the `showScreen` hook and the single
binding, the two palettes and their contrast, Turkish uncertainties with
unknown strings verbatim, presets that only offer what the core enabled,
card markup that escapes untrusted text and names the facts, and room
switches that default to day and sound.

Manual live acceptance: `Alt+8` shows the constellation and lifts on its
own; a click skips; the side column lists the sources the core enabled;
a real query over Genel returns cards of more than one kind with an
"Aç" button that opens the browser; the night switch and the sound switch
remember themselves; `Esc` returns to Komuta Merkezi with the rail back.

## The instrument drawer (20 September 2026)

`tests/test_nova_web.py` gained three checks: the palette calculator
answers the pinned cases (Turkish commas, degrees for trig, unit tables)
and stays silent on nine command-like or undefined queries, with `eval`
asserted absent; every clinical calculator reproduces its textbook vector
(including Friedewald's refusal above 400 mg/dL and silence on missing
inputs); and the wiring test walks the new assets, the calc view, the
term card's read-only data path, and each key the F1 card names back to
a real binding in the shell. `tests/test_medical_academy.py` pins the
term of the day: same day same term, five consecutive days five terms,
every term a real catalogue entry, and the key riding `dashboard()`.

## The almanac (20 September 2026)

`tests/test_almanac.py` drives both halves over a canned transport: the
weather names the city, the sky and the range; the rates invert
Frankfurter's TRY base to street form; each half fails alone with a
Turkish reason; answers and failures are cached for half an hour with
the city geocoded once; missing fields refuse instead of showing zero;
an unknown weather code shows nothing. `tests/test_ui_nova.py` pins the
brief carrying the snapshot and surviving a broken or absent almanac,
and `tests/test_nova_web.py` pins the two brief rows, silence when no
city is set, and the settings card round-tripping the city.

## Two mobile races pinned (20 September 2026)

`tests/test_mobile.py` gained a raw-socket test for the oversized
recording: 413 with its Turkish message on the same connection that then
serves a normal request, proving the drain ceiling clears the voice cap.
The streaming lifecycle test that was flaky under load is deterministic
now that `turn_started` is emitted before the hand-over; the ordering is
by construction, not by scheduler luck.

## Voice, ink and accents (20 September 2026)

`tests/test_ui_nova.py` pins `speak_text` on a fake synthesizer - a
playable RIFF WAV from PCM, markup stripped before speech, honest
refusals for no service and empty text - and `save_markdown`'s
boundaries: sanitized basename, no overwrite, missing folder, empty and
oversized content. `tests/test_nova_web.py` pins the wiring (the
speaker button gated on `voice_available`, the export button and
`State.lastResearchReport`, accent blocks in `tokens.css` declared
before the rooms, the settings select) and the report Markdown builder
against a fixture, uncertainty translation included.

## Copy and the corner clock (21 September 2026)

`test_nova_web` pins the copy control on full-size bubbles only, the
single clipboard hand's three honest answers (copied, refused, nothing
to copy), the report's Kopyala building the same Markdown the export
writes, and the mini clock starting with compact mode and stopping
with it.

## Session, data, snow (21 September 2026)

`test_ui_nova` pins the size helper (top-level files only, a missing
directory is zero), the pulse's cached data figure (a livelier helper
is not consulted inside the minute) and a monotonic uptime.
`test_nova_web` walks `pulseUptime`'s words and the snow's three
honesty pins: the reduced-motion refusal, the single sky, the
self-removal.

## Quiet hours and the battery (21 September 2026)

`test_ui_api_settings` round-trips the quiet window (halves normalized,
equal ends refused, empty allowed). `test_ui_nova` walks the pure clock
check (midnight wrap, a daytime window's exclusive end, junk answering
False) and then the bridge itself with a frozen `_now`: inside the
window the stubbed OS notifier stays silent while the centre records,
outside it fires once; the settings error is Turkish and an omitted key
keeps the stored window. The pulse tests stub `read_power_status` three
ways - battery, no battery, a raising API - and the live Windows test
asserts the power answer's shape in the API's own terms.

## Pins and find (21 September 2026)

`test_nova_web` runs the pure drawer/find helpers in QuickJS: pin-set
parsing tolerates blanks, unknown pins pin nothing, the split keeps
order; the find filter lowercases Turkish, answers indexes, and an
empty query means "off", not "nothing matches". Wiring pins the row's
pin control, the localStorage key, the device-local note, the find box
and its Escape-to-clear, and the CSS that hides misses.

## Sun, lira, nudge (21 September 2026)

`test_almanac` pins the sunrise/sunset fields on the same forecast call
(and their absence staying None). `test_nova_web` walks the two new
palette parsers - currency vectors incl. symbol forms, lira needing a
target, the unit-converter and math guards staying out - and the
reminder forms (+minutes, HH:MM, hour normalisation, the "hatırlatma"
non-prefix), plus the sun row appearing only when the payload carries
times. `test_ui_nova` drives `convert_currency` against a stubbed
almanac: dated display in Turkish separators, TRY round-trip, EUR/USD
cross rate, and every refusal (amount, codes, dead service, no
almanac).

## The remote (21 September 2026)

`test_ui_nova` runs `run_remote_tool` against a tool registered on the
booted executor (once, result mirrored) and pins the two refusals: not
on the allow-list, and allowed but unregistered. `test_nova_web` draws
the remote from tool reports - playing, paused, Spotify closed, a
delegation running, a hostile artist name escaped - and checks the
wiring (card id, start/stop with the home screen, the bridge call).
`test_mobile` asserts the served `/nova/` page carries the new cards
and that the remote's bridge method answers from the phone.

## Spotify's hands (21 September 2026)

`tests/test_spotify_desktop.py` pins the pure helpers with the names the
window showed (library rows, shuffle states, seek arithmetic and clamps,
the play row a person would press, the transport bar), the ducker's
duck/restore sequence on a worker thread (no stacking, silence and a
missing window left alone) and the sleep timer's fade-then-pause with an
injected sleep. `tests/test_integrations.py` drives every new tool
through `FakeSpotifyUia` - sliders read back, shuffle pressed until the
name agrees and PARTIAL when it never does, repeat toggled to the
wanted state, like honest about an already-saved track, library rows
listed and double-clicked with the title as proof, queue verified in
the panel, now-playing from the bar when paused - and the shell's
voice-state chain keeps pushing phases when the ducker raises.

## WhatsApp manners and the vision that finally ran (21 September 2026)

`tests/test_whatsapp_human.py` pins the pure layer: nods and emoji are
acknowledgements (vocatives like "kanka" do not change that), a
question is not; the style profile is measured and says when it is
thin; quiet hours wrap midnight; pacing is bounded, capped and
reproducible with a seeded RNG; replies split into at most three
bubbles. `tests/test_whatsapp_agent.py` drives the agent with structured
snapshots: replies only to the contact's fresh bubbles, never to its own
or unattributed ones, leaves a "tamam 👍" alone, holds a night message
and sends it at 08:30, refuses to read a switched chat, types each
bubble at a pace, and one exchange is one turn however many bubbles.
`tests/test_integrations.py` covers the parsers with the shapes the
probes recorded (badge before or after the time, author inheritance
across unlabeled runs, media rows) and the paced send that types
instead of prefilling. The vision path is tested against the real
`VisionSessionResult` now: two test doubles had invented a `response`
field the controller then read, which is why the first live capture
failed with an AttributeError the suite had never seen.

## The dictionary and the honest month (20 September 2026)

`tests/test_dictionary.py` cans the live sozluk.gov.tr shape captured
by probe (list of entries, senses under `anlamlarListe`, compounds as
one comma-joined string) and pins: the reshape invents nothing, the
Turkish case fold (HEKİM→hekim, ISPARTA→ısparta) hits one transport
call for every casing, misses and dead services answer with named
reasons and are cached, guards run before any network, and the cache
cap evicts the oldest word. `test_nova_web.py` walks `dictionaryQuery`
prefixes and the card markup (hostile words stay escaped), checks the
shortcuts X is actually wired, and demands an answers-only day wear
`l1` while a truly empty day stays `l0`. `test_ui_nova.py` drives the
`define_word` bridge against a stub and reads the morning line with
and without the almanac.

## Rhythm, pulse and noise (20 September 2026)

`tests/test_medical_scoring.py` gained the frozen-clock backup test:
three backups inside one `datetime.now` tick yield three files, the
sibling names sort as the creation order, and rotation removes the true
oldest - pinning the collision that used to overwrite a kept copy.


`tests/test_nova_web.py` pins the heatmap builder against a fixture
(levels, tooltips, empty input) and the pulse's page half: beats only on
the diagnostics screen, a dash - never a zero - for the first CPU
reading, silence on failure, and the focus noise declaring itself
synthetic and yielding to Escape. `tests/test_ui_nova.py` drives
`system_pulse` over monkeypatched counters (first beat None, second the
exact busy share) and `cpu_percent_between` over its refusals; the
academy tests pin `weekly_report(days=28)` and the 31-day cap.
