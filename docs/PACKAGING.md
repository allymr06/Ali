# Windows Packaging

JARVIS is packaged as a 64-bit Windows 11 desktop application. The build is
performed on Windows with Python 3.12 and a Python installation that includes
Tcl/Tk. Build tools and generated artifacts are intentionally excluded from
source control.

## Reproducible build

From an ordinary PowerShell prompt in the repository root:

```powershell
powershell -ExecutionPolicy Bypass -File scripts/bootstrap_windows_build.ps1
build-tools/venv/Scripts/python.exe scripts/build_windows.py
```

The bootstrap script downloads only the official Python and Inno Setup
installers, verifies their Authenticode publisher signatures before execution,
creates a repository-local Python environment, and installs the pinned package
requirements. It does not modify the system PATH.

The build command runs the complete project verifier, creates an onedir
PyInstaller application, launches the frozen executable in smoke-test mode,
renders all eleven native UI screens against bundled Tcl/Tk, checks live health,
creates a portable ZIP and a current-user Inno Setup installer, and writes
SHA-256 release evidence.

The executable, installer, Start menu shortcut, desktop shortcut, and native
window use the monochrome JARVIS logo derived from
`assets/branding/jarvis-shortcut-icon.pdf`. The generated ICO contains Windows
icon sizes from 16 through 256 pixels.

## Release outputs

`release/` contains:

- `JARVIS-0.1.0-windows-x64.zip`
- `JARVIS-Setup-0.1.0-x64.exe`
- `release-manifest.json`
- `SHA256SUMS.txt`

The release manifest explicitly records that the artifacts are unsigned until
a publisher code-signing certificate is supplied. Hashes prove integrity, not
publisher identity. A public release must be code-signed before distribution.

`--allow-environment-limited-smoke` exists only for sandboxed build hosts whose
native Tcl loader cannot read files that Python and the operating system can
both see. It requires the frozen process to reach a classified `TclError` and
verifies every bundled Tcl/Tk/icon file, but records `native_ui_rendered=false`.
This evidence is not equivalent to the normal native render gate and must not
be used to claim production readiness.

## Local application data

The installed application stores mutable databases and task state below
`%LOCALAPPDATA%\JARVIS`. Uninstalling the application does not silently delete
user data. No credential is embedded in either artifact.
