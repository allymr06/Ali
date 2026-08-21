# JARVIS Project State

Last verified: 21 August 2026

## Current status

- Completed milestone: Phase 6 — Permission and security engine
- Next milestone: Phase 7 — Windows integrations
- State: awaiting user approval to begin Phase 7
- Platform target: Windows 11, Python 3.12
- Automated verification: 579 tests passing
- Production readiness: not yet claimed

## Implemented architecture

- `CoreEngine` provides bounded request, provider, conversation, memory, and
  tool orchestration.
- `ProviderGateway` provides capability-aware routing, explicit overrides,
  timeout, retry, fallback, health accounting, and normalized streaming.
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
- Memory, planning, task state, recovery, and agent-loop foundations are
  implemented behind explicit domain boundaries.

## Completed phases

1. Phase 0 — workspace inspection and architecture foundation
2. Phase 1 — project bootstrap, configuration, and core contracts
3. Phase 2 — bounded JARVIS Core and execution runtime
4. Phase 3 — AI provider gateway and model routing
5. Phase 4 — conversation engine and context lifecycle
6. Phase 5 — versioned, dynamically discoverable tool system
7. Phase 6 — scoped permission policy and bound approval security

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

## Known limitations and deferred work

- No production Windows tool or independently verified Windows action exists
  yet; this is the Phase 7 objective.
- Long-term memory and conversation stores are currently in-memory.
- The mock provider remains the offline default; OpenAI requires configuration.
- Python cannot forcibly stop an already-running synchronous worker thread.
  Timeout results explicitly report when side effects may continue.
- Desktop UI, voice, vision, web research, plugin runtime, diagnostics,
  packaging, and installer phases remain pending.

## Phase 7 acceptance target

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

The action must have a real implementation, deterministic local tests, safe
failure behavior, cancellation where practical, and documentation. Phase 7
must not begin until explicitly approved by the user.
