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
- frozen and source asset resolution, explicit missing-asset errors, the
  per-user WebView2 profile location, exactly-once resource release on window
  close, and the `--classic` / import-fallback paths of `launch_desktop` and
  the packaged entry point.

`tests/test_nova_web.py` keeps the page honest without a browser harness: it
parses `nova.js` with QuickJS (a syntax error fails the gate), checks that
every `Bridge.*` call in JavaScript exists on `NovaBridge` with a compatible
arity, that the demo bridge mirrors the Python API exactly, that lifecycle
hooks are not exposed, that every Python push kind has a page handler, that
demo mode is opt-in (`?demo=1`, never inside pywebview) and never a fallback,
that the failure and confirmation UI exist, that deleting the key asks first,
and that the stylesheet ignores the OS reduced-motion setting.

The packaging tests require the spec to bundle the three page files at
`app/ui/nova/web` and the frozen smoke report to list them as `nova_assets`.

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
