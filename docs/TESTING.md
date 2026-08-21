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
