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

When running several development instances side by side, disable the
single-instance guard and the tray: `JARVIS_SINGLE_INSTANCE=false` and
`JARVIS_TRAY_ENABLED=false`. With the tray on, closing the window only hides
it; exit through the tray menu.

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

## Writing a plugin

A plugin is a directory `<plugins root>/<plugin-id>/` (default root
`%LOCALAPPDATA%\JARVIS\plugins`, id pattern `^[a-z][a-z0-9-]{2,40}$`) with
`plugin.json` and the module named by `entry_point`:

```json
{
  "schema_version": 1,
  "plugin_id": "echo",
  "name": "Echo",
  "version": "1.0.0",
  "description": "Returns the text it is given.",
  "entry_point": "plugin:create_plugin",
  "capabilities": ["tools"],
  "tools": [
    {
      "name": "echo",
      "description": "Return the given text unchanged.",
      "risk_level": "low",
      "parameters": [
        {"name": "text", "type": "string", "description": "Text to echo."},
        {"name": "repeat", "type": "integer", "required": false}
      ]
    }
  ]
}
```

```python
def create_plugin(context):
    context.log("ready")            # bounded line in the diagnostics ledger
    def echo(text, repeat=None):    # arguments arrive as keywords
        return {"echo": text}       # return JSON-serializable data
    return {"echo": echo}           # one callable per declared tool
```

Rules: parameter types are `string`, `integer`, `number`, or `boolean`;
`risk_level` is `read_only`, `low`, `medium`, or `high` (effective risk is at
least `low`, medium and high always require approval); the tool is exposed
as `plugin_<id>_<tool>`; output must stay under 64 KB; a call that exceeds
`JARVIS_PLUGIN_TOOL_TIMEOUT_SECONDS` is reported as a timeout; three
consecutive failures quarantine the plugin. Enable a discovered plugin with
`application.plugins.enable("echo")`; the choice persists in `state.json`.
The complete example lives in `tests/fixtures/plugins/echo`.

## Conventions

- User-facing text is Turkish; code, comments, and documentation are English.
- Before a risky change, keep an unpushed backup branch of the uncommitted
  work (for example `backup-before-nova-stabilization`) so nothing is lost.
- `.claude/` holds per-machine tooling configuration and is git-ignored.
