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
- `ToolExecutor` enforces strict tool contracts and returns structured terminal
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
