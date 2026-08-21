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
owns routing, capability validation, timeout, retry, fallback, cancellation,
health accounting, and provider metadata.

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
