# Development

## Environment

JARVIS targets Python 3.12 on Windows 11.

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
```

`requirements-dev.txt` installs the project in editable mode with the `dev`
and `voice` extras (pytest, pytest-asyncio, QuickJS for the JavaScript syntax
gate, sounddevice, google-genai). Re-run it whenever `pyproject.toml` changes:
`pip check` cannot notice a dependency that was added to the project metadata
but never installed.

## Running the desktop

```powershell
.\.venv\Scripts\python.exe -m app.ui            # Nova (pywebview / WebView2)
.\.venv\Scripts\python.exe -m app.ui --classic  # classic Tkinter shell
```

Nova lives in `app/ui/nova/`: `shell.py` hosts the window and exposes
`NovaBridge` to the page; `web/index.html`, `web/nova.css`, and `web/nova.js`
are the page. Python is the source of truth. Everything the page shows arrives
through `boot()` or a `window.NOVA.push({kind, payload})` event, and every user
action is a bridge call that returns `{ok, error?}`. Keep both sides in step:
`tests/test_nova_web.py` fails when a JavaScript call has no matching Python
method, when the demo bridge drifts from the real API, or when a push kind has
no handler.

Rules that the tests enforce and reviews should keep:

- Never simulate. If the bridge is unavailable the page shows the failure
  screen; the demo bridge exists only for `?demo=1` in a plain browser.
- Never send a secret over the bridge. Settings snapshots say whether a
  credential exists, nothing more.
- Destructive UI actions ask first in the page and are guarded again in Python
  (`delete_api_key(confirmed=True)`).
- Approvals are single-use tokens and fail closed.
- Motion follows only the in-app "Hareketi azalt" switch, never the OS-wide
  reduced-motion setting.

To iterate on the page without the Python core, serve the folder and open the
demo explicitly; the DEMO badge must stay visible the whole time:

```powershell
python -m http.server 8321 --directory app/ui/nova/web
```

Then browse to `http://localhost:8321/?demo=1`. Without the query parameter the
page shows the bridge-failure screen after ten seconds, which is the intended
production behaviour.

Preferences that the page stores in `localStorage` (theme, motion) live in the
WebView2 profile below `%LOCALAPPDATA%\JARVIS\webview` (or below
`JARVIS_STATE_DIRECTORY` when that override is set). Delete that folder to
reset them.

## Verification

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_ui_nova.py tests/test_nova_web.py -q
.\.venv\Scripts\python.exe scripts\verify.py
```

`scripts/verify.py` is the acceptance gate CI runs: dependency integrity,
bytecode compilation of `app` and `tests`, and the complete suite under a fixed
`PYTHONHASHSEED`. Nova-specific coverage is described in `docs/TESTING.md`.

## Packaging

```powershell
.\.venv\Scripts\python.exe scripts\build_windows.py
```

The frozen smoke test must report `ok=true`, `screens=11`, a Tcl version, and
`nova_assets` listing all three page files. See `docs/PACKAGING.md`.

## Conventions

- User-facing text is Turkish; code, comments, and documentation are English.
- Before a risky change, keep an unpushed backup branch of the uncommitted
  work (for example `backup-before-nova-stabilization`) so nothing is lost.
- `.claude/` holds per-machine tooling configuration and is git-ignored.
