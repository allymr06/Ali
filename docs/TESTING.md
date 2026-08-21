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
- provider fallback and health accounting;
- cancellation and timeout cleanup;
- streaming retry only before the first emitted chunk;
- OpenAI response, tool-call, usage, error, and stream normalization;
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
- provider-neutral contract and OpenAI schema derivation;
- dynamic enable, disable, unregister, and registry revision behavior;
- exact-name, capability, and tag-based discovery;
- request-scoped tool exposure with malformed filters failing closed;
- per-tool concurrency admission for overlapping execution;
- tool-specific idempotent retry policy through the shared execution service;
- preservation of existing input, output, permission, timeout, cancellation,
  and verification boundaries.
