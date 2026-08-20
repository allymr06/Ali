# Security Model

JARVIS treats tool execution as a security boundary. Model output cannot
directly bypass the permission engine, argument validation, approval gate, or
execution timeout.

## Tool contracts

- Tool names, descriptions, and timeouts are validated when definitions are
  created.
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

Trusted code may register parameter-sensitive rules that only elevate risk.
Rules cannot downgrade a tool's declared risk. A rule failure is denied closed.

## Approval binding

Approval requests are bound to a SHA-256 fingerprint of the operation, tool,
parameters, task, plan, and step. Parameters must be JSON-serializable. A
changed action or execution context invalidates the previous approval and
creates a new pending request. Approval requests expire after a bounded time.

## Timeouts and cancellation

Async handlers are cancelled cooperatively. Sync handlers run outside the
event loop so they cannot freeze async orchestration. Python cannot safely stop
an already-running thread; a timeout or cancellation therefore sets
`side_effects_may_continue` when the worker may still be active. Tool
implementations with side effects should support cooperative cancellation and
must be idempotent where possible.
