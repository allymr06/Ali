"""Helpers for plugin runtime tests: build plugin directories from the fixture."""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

FIXTURE_ROOT = Path(__file__).resolve().parent / "fixtures" / "plugins"
ECHO_FIXTURE = FIXTURE_ROOT / "echo"


def fixture_manifest() -> dict[str, Any]:
    return json.loads((ECHO_FIXTURE / "plugin.json").read_text(encoding="utf-8"))


def install_plugin(
    root: Path,
    plugin_id: str = "echo",
    *,
    manifest: dict[str, Any] | None = None,
    code: str | None = None,
    manifest_text: str | None = None,
) -> Path:
    """Create ``root/<plugin_id>`` from the echo fixture with optional overrides.

    ``manifest`` replaces the whole manifest object (its ``plugin_id`` is set
    to ``plugin_id`` unless the caller set another value on purpose),
    ``manifest_text`` writes raw text (for corrupt manifests), and ``code``
    replaces ``plugin.py``.
    """
    directory = root / plugin_id
    directory.mkdir(parents=True, exist_ok=True)
    if manifest_text is not None:
        (directory / "plugin.json").write_text(manifest_text, encoding="utf-8")
    else:
        payload = manifest if manifest is not None else fixture_manifest()
        payload.setdefault("plugin_id", plugin_id)
        if manifest is None:
            payload["plugin_id"] = plugin_id
        (directory / "plugin.json").write_text(
            json.dumps(payload, indent=2), encoding="utf-8"
        )
    if code is not None:
        (directory / "plugin.py").write_text(code, encoding="utf-8")
    else:
        shutil.copyfile(ECHO_FIXTURE / "plugin.py", directory / "plugin.py")
    return directory


def echo_manifest(plugin_id: str = "echo", **tool_overrides: Any) -> dict[str, Any]:
    """The fixture manifest with the first tool's fields overridden."""
    payload = fixture_manifest()
    payload["plugin_id"] = plugin_id
    payload["tools"][0].update(tool_overrides)
    return payload


FAILING_PLUGIN = '''
def create_plugin(context):
    def echo(text, repeat=None):
        raise RuntimeError("plugin secret detail: " + text)
    return {"echo": echo}
'''

SLOW_PLUGIN = '''
import time

def create_plugin(context):
    def echo(text, repeat=None):
        time.sleep(5)
        return {"echo": text}
    return {"echo": echo}
'''

NON_JSON_PLUGIN = '''
def create_plugin(context):
    def echo(text, repeat=None):
        return object()
    return {"echo": echo}
'''

BIG_OUTPUT_PLUGIN = '''
def create_plugin(context):
    def echo(text, repeat=None):
        return {"echo": "x" * 100_000}
    return {"echo": echo}
'''

EXTRA_TOOL_PLUGIN = '''
def create_plugin(context):
    def echo(text, repeat=None):
        return {"echo": text}
    return {"echo": echo, "surprise": lambda: "not declared"}
'''

MISSING_TOOL_PLUGIN = '''
def create_plugin(context):
    return {}
'''

IMPORT_ERROR_PLUGIN = '''
raise RuntimeError("boom at import: secret path C:/private")
'''

MARKER_PLUGIN = '''
import pathlib
pathlib.Path(__file__).with_name("IMPORTED").write_text("yes")

def create_plugin(context):
    return {"echo": lambda text, repeat=None: {"echo": text}}
'''
