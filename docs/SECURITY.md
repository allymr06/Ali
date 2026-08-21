# Security Model

JARVIS treats tool execution as a security boundary. Model output cannot
directly bypass the permission engine, argument validation, approval gate, or
execution timeout.

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
