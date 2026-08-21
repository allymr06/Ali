# JARVIS Ω

**Just A Rather Very Intelligent System**

JARVIS is a modular, Windows-first personal AI assistant under active development. The repository currently contains the tested backend foundation for conversation orchestration, model providers, memory, planning, tools, permissions, approvals, and recoverable task execution.

## Current status

The project is in the **core engineering stage**. It is not yet a complete desktop assistant, but its main orchestration components are implemented and covered by automated tests.

Implemented:

- provider abstraction with mock and OpenAI-compatible providers
- provider registration, selection, timeout, and retry handling
- conversation orchestration and model tool-call processing
- versioned tool contracts, dynamic capability discovery, and bounded execution
- risk-based permissions and approval flow
- plans, dependencies, task states, progress, and cancellation
- execution events, journaling, persistence, recovery, and replanning
- working, conversation, and in-memory long-term memory foundations
- deterministic memory analysis and memory lifecycle operations
- automated unit and integration tests

Not implemented yet:

- production Windows tools and verified Windows actions
- persistent long-term memory backend
- desktop UI and system tray
- voice input, wake word, and speech output
- vision and screen control
- web research engine
- plugin runtime
- Windows packaging and installer

## Verification status

The current test suite contains **565 passing tests** on Python 3.12. This result was verified on 21 August 2026.

The project does not yet claim production readiness. In particular, the first real end-to-end Windows vertical slice still needs to be completed:

```text
User request
  -> real AI provider
  -> structured tool call
  -> permission check
  -> Windows action
  -> independent verification
  -> natural response
```

## Architecture

The current source tree is divided into the following domains:

```text
app/
|-- agent/       Multi-step agent loop and approvals
|-- config/      Environment-based runtime settings
|-- core/        Central request orchestration and shared models
|-- execution/   Execution state, events, retry, recovery, and verification
|-- memory/      Memory models, policy, analysis, and stores
|-- planning/    Plans, dependencies, persistence, and execution
|-- platform/    Reserved for Windows platform integrations
|-- providers/   AI provider abstraction and implementations
|-- security/    Permission and risk evaluation
|-- tasks/       Task and task-step lifecycle management
`-- tools/       Tool definitions, registration, and execution
```

## Development setup

JARVIS currently targets Python 3.12.

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements-dev.txt
python -m pytest -q
```

Runtime and development dependencies are pinned in `pyproject.toml`. The
requirements files install the corresponding project dependency set.

## Engineering principles

Development follows this loop:

```text
OBSERVE -> UNDERSTAND -> PLAN -> IMPLEMENT -> RUN -> TEST
        -> INSPECT -> VERIFY -> REVIEW -> FIX -> RETEST
```

An action must not be reported as completed merely because execution returned without an error. Execution and independent verification are separate concerns.

## Next milestone

The next milestone is the first real Windows vertical slice: one production
Windows action executed through the completed permission, approval, bounded
tool-runtime, and independent-verification path.

## Documentation

- `docs/ARCHITECTURE.md`
- `docs/PROJECT_STATE.md`
- `docs/DEVELOPMENT.md`
- `docs/TESTING.md`
- `docs/SECURITY.md`
- `docs/CONFIGURATION.md`
