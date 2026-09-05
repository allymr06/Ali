# JARVIS Ω

**Just A Rather Very Intelligent System**

JARVIS is a modular, Windows-first personal AI assistant under active development. The repository contains a tested Core, optional voice/vision/research capabilities, a native desktop shell, diagnostics, and a reproducible Windows release pipeline.

## Current status

The project is in the **core engineering stage**. It is not yet a complete desktop assistant, but its main orchestration components are implemented and covered by automated tests.

Implemented:

- provider abstraction with Gemini as the single production provider and a deterministic offline mock provider for tests
- provider registration, selection, timeout, and retry handling
- conversation orchestration and model tool-call processing
- versioned tool contracts, dynamic capability discovery, and bounded execution
- scoped, auditable permissions and action-bound immutable approvals
- native Windows system/process observation and verified application launching
- plans, dependencies, task states, progress, and cancellation
- durable tasks, safe-boundary pause/resume, restart recovery, and subtasks
- execution events, journaling, persistence, recovery, and replanning
- separated working/conversation context and SQLite-backed long-term memory
- provenance, freshness, relevance ranking, retention, and memory controls
- deterministic memory analysis with credential and payment-data rejection
- optional bounded voice input, exact wake-word gating, interruption, and WAV output
- Gemini speech recognition/synthesis adapters with explicit provenance
- privacy-first in-memory audio disposal and classified voice failures
- consent-bound native screen capture with bounded session state
- irreversible sensitive-region and taskbar redaction before model access
- image provenance, freshness checks, and capability-aware vision routing
- opt-in bounded web research with SearXNG-compatible search
- SSRF-resistant IP-pinned retrieval, redirect revalidation, and download limits
- source timestamps, content hashes, freshness, citation checks, and uncertainties
- Nova desktop shell (pywebview/WebView2): a cinematic command centre with a
  state-driven intelligence core, an execution timeline and tool activity
  observed live from the tool executor, a single-action permission overlay,
  stored conversations, editable memory, live diagnostics (health checks,
  bounded metrics, the event ledger), a command palette (Ctrl+K), a compact
  always-on-top window, and an immersive voice stage; honest bridge-failure
  handling and no simulated data; the classic Tk shell stays available
  behind `--classic`
- in-app Gemini model setup with connection testing and the API secret stored
  in Windows Credential Manager
- sanitized structured diagnostics, tamper-evident events, bounded metrics, and
  live component health checks
- bounded Core admission, explicit overload failure, and provider circuit
  breakers with single-probe recovery
- automated unit and integration tests

- root-allowlisted filesystem tools with traversal and reparse-point defenses
- Windows clipboard and window-listing controls under the permission engine
- Windows system tray for the Nova shell (open, voice mode, pause/resume,
  diagnostics, settings, exit; close-to-tray) and one instance per user
  session
- manifest-based plugin runtime (disabled by default; plugin tools go through
  the same executor, permission engine, and approvals; in-process, not
  sandboxed)

Not implemented yet:

- notifications and proactive behavior
- filesystem undo/rollback, bulk dry-run plans, and indexed search
- keyboard/mouse automation and general safe PowerShell execution
- a tray icon for the classic Tkinter shell
- plugin process isolation and a plugin settings screen
- production code signing and an installed-runtime qualification

## Verification status

The current test suite contains **1197 passing tests, 1 skipped** (`scripts/verify.py`), verified on 5 September 2026.

The project does not yet claim production readiness. The first real Windows
vertical slice is implemented and verified:

```text
User request
  -> configured AI provider
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
|-- platform/    Native Windows integrations and verification
|-- providers/   AI provider abstraction and implementations
|-- research/    Safe retrieval, search, provenance, synthesis, and citations
|-- security/    Permission and risk evaluation
|-- tasks/       Task and task-step lifecycle management
|-- tools/       Tool definitions, registration, and execution
|-- ui/          Desktop shells (Nova WebView2 + classic Tk), controller, state
|-- voice/       Audio devices, speech providers, wake gate, and sessions
`-- vision/      Consent, capture, redaction, provenance, and analysis
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

Microphone support is optional and can be installed with:

```powershell
python -m pip install -e .[voice]
```

Launch the desktop interface (Nova, hosted in the Microsoft Edge WebView2
Runtime that ships with Windows 11):

```powershell
python -m app.ui
```

The classic Tkinter shell remains available for a Python installation that
includes Tcl/Tk, and is used automatically when pywebview is not installed:

```powershell
python -m app.ui --classic
```

## Engineering principles

Development follows this loop:

```text
OBSERVE -> UNDERSTAND -> PLAN -> IMPLEMENT -> RUN -> TEST
        -> INSPECT -> VERIFY -> REVIEW -> FIX -> RETEST
```

An action must not be reported as completed merely because execution returned without an error. Execution and independent verification are separate concerns.

## Windows release

The reproducible PyInstaller application, portable archive, Inno Setup source,
icon assets, strict frozen-runtime smoke test, and SHA-256 manifest are
implemented. See `docs/PACKAGING.md`. On the development host the pinned
toolchain produces a `qualified` build whose frozen smoke test renders all
eleven screens against bundled Tcl/Tk and finds the Nova web assets (rebuilt
5 September 2026). The artifacts are unsigned, so they are not yet a
production release.

## Documentation

- `docs/ARCHITECTURE.md`
- `docs/PROJECT_STATE.md`
- `docs/DEVELOPMENT.md`
- `docs/TESTING.md`
- `docs/ACCEPTANCE.md`
- `docs/PACKAGING.md`
- `docs/FINAL_AUDIT.md`
- `docs/SECURITY.md`
- `docs/CONFIGURATION.md`
