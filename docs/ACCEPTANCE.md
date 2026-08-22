# Acceptance Gates

JARVIS is accepted only when every gate below passes on Python 3.12:

1. Installed dependency metadata is consistent (`python -m pip check`).
2. Every application and test module compiles to bytecode.
3. The complete deterministic test suite passes with a fixed non-default
   `PYTHONHASHSEED`.
4. All application modules import without launching a process, opening a
   network connection, capturing audio/video, or creating a desktop window.
5. The offline end-to-end path verifies Core, memory, durable tasks, UI state,
   diagnostics, metrics, health checks, and event-ledger integrity.
6. Every tool contract is unique, JSON serializable, closed to unknown input,
   versioned, and confirmation-bound whenever its risk is medium or higher.
7. Runtime source contains no `shell=True`, `os.system`, `eval`, or `exec` path.
8. Phase-specific security and recovery tests remain green.

Run all automated gates with:

```powershell
python scripts/verify.py
```

Phase 17 additionally requires a native Windows build whose frozen executable
renders every desktop screen against bundled Tcl/Tk, reports healthy services,
and produces a portable archive, current-user installer, release manifest, and
SHA-256 checksums. Signing status must be explicit; an unsigned artifact may be
validated locally but must not be represented as publisher-authenticated.
