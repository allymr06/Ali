# Security Model

JARVIS treats tool execution as a security boundary. Model output cannot
directly bypass the permission engine, argument validation, approval gate, or
execution timeout.

## Deterministic tool routing

Besides model-issued tool calls, `DeterministicToolRouter` can map an
unambiguous user request (for example "görevlerimi listele") directly to a tool
call without a model round trip. This path is latency-only, never a privilege
shortcut:

- The router refuses any candidate whose effective risk is not `READ_ONLY`, and
  any candidate whose permission decision requires confirmation.
- A routed call enters the same pipeline as a model tool call: the permission
  engine evaluates it, argument schemas validate it, and its structured result
  is verified.
- Requests can opt out per call with the `deterministic_tool_routing: false`
  metadata flag.

## Tool contracts

- Tool names, descriptions, versions, timeouts, retry policy, concurrency
  limits, capabilities, tags, and extension metadata are validated when
  definitions are created.
- Input arguments are bound to the handler signature and checked against type
  annotations before execution.
- Unknown arguments are rejected. Exported JSON schemas set
  `additionalProperties` to `false`.
- Annotated return values are validated before a successful result is exposed.
- Execution success does not automatically mean outcome verification. A raw
  handler value is successful but remains unverified. A structured successful
  `ToolResult` preserves the tool's explicit verification state.

## Permissions

The default risk policy is deterministic:

- `READ_ONLY` and `LOW` are allowed.
- `MEDIUM` and `HIGH` require confirmation.
- `CRITICAL` is denied by default, including when a confirmation flag is
  supplied.

Trusted code may register parameter-sensitive rules that elevate risk or force
confirmation/denial. Rules cannot force an allow decision or downgrade a
tool's declared risk. A matcher failure is denied closed without exposing its
exception contents. Rule lifecycle changes increment the policy revision.

`PermissionScope` can restrict an execution to an explicit tool allowlist,
denylist, and maximum risk level. Every decision has an ID, timestamp, declared
and effective risk, matching rules, and policy revision. A bounded in-memory
audit buffer retains this metadata without retaining tool parameters.

## Approval binding

Approval requests are bound to a SHA-256 fingerprint of the operation, tool,
tool-contract version, parameters, task, plan, and step. Parameters must be
JSON-serializable objects with string keys. A changed action, tool version, or
execution context invalidates the previous approval and creates a new pending
request. Approval requests expire after a bounded time.

The final grant validation happens inside `ToolExecutor`, immediately before
handler execution. A raw `confirmation_granted=True` flag cannot authorize an
operation. Approval request objects are immutable and store transitions are
serialized, preventing callers or concurrent requests from changing pending
state into approval.

## Timeouts and cancellation

Async handlers are cancelled cooperatively. Sync handlers run outside the
event loop so they cannot freeze async orchestration. Python cannot safely stop
an already-running thread; a timeout or cancellation therefore sets
`side_effects_may_continue` when the worker may still be active. Tool
implementations with side effects should support cooperative cancellation and
must be idempotent where possible.

Retries above one attempt require an explicit idempotency declaration in the
tool contract. Every retry remains subject to the global time and tool-call
budgets. A timeout that may still have side effects is not retried.

## Discovery and lifecycle

Tools may be exposed to a model only when they are enabled and match the
request's name, capability, and tag filters. Invalid request filters fail
closed. A provider tool call outside the exposed request scope is rejected at
execution time. Disabling a tool removes it from discovery and blocks new
execution without deleting its registration, allowing controlled runtime lifecycle
management and auditable registry revision changes.

## Windows actions

Model-generated executable paths and shell commands are not accepted by the
Windows launcher. The model supplies a registered application ID or alias;
trusted application definitions supply the executable and fixed arguments.
Only local `.exe` files are accepted. UNC/network paths, batch files, scripts,
unknown IDs, missing executables, and command expressions fail closed.

Successful process creation is not sufficient evidence of success. The
launcher observes the returned PID through Win32, confirms that it is still
active, and compares its executable identity with the application contract.
Failure, timeout, disappearance, or identity mismatch returns an unverified
failure. Process enumeration uses native snapshot APIs rather than executing
model-controlled terminal input.

## Memory safety and control

Durable memory rejects labeled credentials, private keys, and valid
payment-card numbers before a database write. Rejected content is not echoed
into an error record or persisted as metadata. Memory records retain their
source, source reference, confidence, freshness, and expiry so recalled facts
can be traced and evaluated.

Listing and searching memory are read-only tools. Soft forgetting is
`MEDIUM` risk and keeps history; permanent deletion is `HIGH` risk and removes
the row. Both require an exact, unexpired approval grant at the tool boundary.
Expired records do not enter model context and can be explicitly purged under
the retention API.

## Durable task control

An interrupted process never causes a running task or step to be assumed
complete. Startup converts it to `PAUSED`, and recovery trusts only completed
step IDs in the atomic execution snapshot. The interrupted step remains
unverified and is eligible for controlled retry.

Task pause, resume, and cancellation tools are `MEDIUM` risk and require an
exact bound approval. Pause becomes verified only after the runtime reaches a
safe step boundary; a timeout reports that side effects may continue. Resume
does not restore a stale approval grant for an underlying risky tool: that
action must still pass the current permission and approval policy.

## Voice privacy and interruption

Voice is disabled by default. A microphone session has a hard duration and
each capture, transcription, Core, synthesis, and playback stage is bounded by
a timeout. The same cooperative interruption event reaches the device and Core
layers; output is explicitly stopped when the user interrupts.

Captured PCM remains in memory and is overwritten and released immediately
after transcription by default, including failure paths. Retention requires an
explicit setting and does not write the capture to disk. Session results and
events never contain raw audio. Provider and device exceptions cross the voice
boundary only as stable classifications, preventing credentials or upstream
details from entering UI-visible results.

Wake-word matching is case-insensitive but requires a complete word, avoiding
activation by a substring. A required wake word gates the request before Core,
so ignored speech cannot trigger provider tools or side effects. Wake-word
gating is not treated as speaker authentication or authorization.

## Vision consent and image privacy

Vision and screen capture are disabled by default. Enabling the service does
not authorize capture. Every frame requires a user-visible consent request and
an immutable grant bound to purpose, source, redaction coordinates, expiration,
and random grant identity. Validation and one-use consumption happen atomically
before capture; altered, expired, denied, unknown, or replayed grants fail
closed.

Capture dimensions, pixel count, frame age, encoded size, image count, media
type, and model detail are bounded. User redactions must be fully in-frame, and
the configured taskbar band is blackened before encoding. Source and processed
hashes preserve traceability without retaining image content.

Raw RGB and processed PNG buffers remain mutable so they can be overwritten on
success, failure, timeout, stale-frame rejection, or interruption. They are not
written to disk or stored in conversation history. Optional processed-image
retention is explicit, remains in memory, clears the previous frame before a
new capture, and exposes a dedicated clearing operation.

## Untrusted web content and SSRF protection

Web research is disabled by default and requires an explicit SearXNG endpoint.
Source URLs allow no embedded credentials and resolve only to globally routable
addresses on configured ports. A hostname is rejected when any DNS answer is
private, loopback, link-local, reserved, multicast, unspecified, or otherwise
non-global. Connections are pinned to the validated address, preventing a
second DNS lookup from redirecting the connection to an internal service.

Redirects are manual and revalidated; HTTPS downgrade, attachment responses,
unknown media, compression, oversized content, malformed length declarations,
and non-success status codes fail closed. Retrieval bypasses environment proxy
settings and sends no cookies or credentials.

Search results, pages, snippets, and embedded instructions are untrusted data,
never policy. The research tool contract explicitly prohibits automatic
instruction execution. Reports include retrieval time, publication time when
available, final URL, IP evidence, content hash, freshness, prompt-injection
indicators, citations, and uncertainties. A successful fetch verifies the
retrieval and citation structure; it does not certify that a source is true.

## Desktop trust boundary

The desktop shell is an adapter, not an authority boundary. Text enters Core as
a normal typed request and all provider tool calls still pass the existing tool,
permission, approval, budget, and verification layers. Capability status is
read from application services; the UI never treats a displayed button as an
authorization grant.

Voice, vision, and research controls fail closed when their services are not
configured. Vision displays the provider-transfer and retention disclosure and
requires an immediate affirmative action before creating the one-use consent
grant. Window close cancels the UI-owned futures and closes the background
event loop; it does not claim that an already-running synchronous side effect
stopped.

The Nova shell adds a JavaScript boundary with the same posture:

- Only the public methods of `NovaBridge` are callable from the page;
  lifecycle hooks are private. Every method validates its input and returns a
  result dictionary instead of raising into the page.
- The page renders only what Python pushed. There is no simulated data path in
  production: a missing bridge produces a visible failure screen, and the demo
  bridge requires an explicit `?demo=1` outside pywebview.
- Tool approvals reach the page as single-use tokens with parameters already
  masked by `safe_approval_parameters`. A decision is accepted once, only as a
  real boolean; timeout, shutdown, an unknown token, or a page that has not
  booted all mean "denied".
- No secret crosses the bridge. Settings snapshots carry only whether a
  credential exists, connection-test messages are redacted upstream, and
  bridge error texts contain exception class names, never messages.
- Deleting the stored credential requires an in-app confirmation dialog and a
  `confirmed=True` argument; a bare call is a no-op.
- The page is self-contained (`default-src 'self'`, no remote scripts or
  styles) and loads from the application's own files.

## Observability privacy and integrity

Diagnostic fields are bounded before storage. Password, token, API-key,
authorization, cookie, credential, and private-key fields are replaced with a
redaction marker at every supported nesting level. Bearer and key-shaped values
are removed from messages. Request correlation remains possible through a
stable SHA-256-derived identifier that cannot reveal the original request ID.

The in-memory event window is hash chained and checked before an event query is
reported as verified. Bounded eviction advances a trusted anchor rather than
silently breaking the retained chain. Metrics prohibit arbitrary labels and
limit unique names, preventing user text or identifiers from creating unbounded
cardinality. Health check exceptions do not expose exception messages or stack
traces through tools or the UI.

## Overload and dependency failure containment

Core accepts only a configured number of active and queued requests. A saturated
process fails fast with a stable typed error before memory, provider, or tool
work begins. Queue cancellation and timeout remove their accounting entry, and
every admitted request releases its lease through an asynchronous context
boundary.

Provider circuit breakers react only to retryable availability failures; an
authentication or invalid-response failure cannot be misrepresented as a
temporary outage. Open circuits skip remote calls, preventing retry storms.
Recovery admits one probe and blocks concurrent probes until its result is
known. No request text, credentials, or provider response content enters circuit
state or reliability metrics.
