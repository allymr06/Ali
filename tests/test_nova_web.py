"""Static and syntax checks for the Nova page (index.html / nova.css / nova.js).

Deliberately lightweight: no browser automation. The JavaScript is parsed
by QuickJS (a 400 KB engine) so a syntax slip cannot reach a release, and
the bridge contract between nova.js and NovaBridge is verified by reading
both sides. Behaviour in a real WebView2 window is covered by the manual
live acceptance run documented in docs/TESTING.md.
"""

from __future__ import annotations

import inspect
import json
import re

import pytest

from app.ui.nova import shell

WEB = shell.SOURCE_WEB_ROOT
JS = (WEB / "nova.js").read_text(encoding="utf-8")
HTML = (WEB / "index.html").read_text(encoding="utf-8")
CSS = (WEB / "nova.css").read_text(encoding="utf-8")


def section(text: str, start: str, end: str) -> str:
    begin = text.index(start)
    return text[begin : text.index(end, begin)]


def public_bridge_methods() -> set[str]:
    return {
        name
        for name, _member in inspect.getmembers(shell.NovaBridge, inspect.isfunction)
        if not name.startswith("_")
    }


def demo_bridge_methods() -> set[str]:
    block = section(JS, "const DemoBridge = {", "\n};")
    return set(re.findall(r"^\s{2}async (\w+)\(", block, flags=re.MULTILINE))


def javascript_bridge_calls() -> dict[str, set[int]]:
    """Map every ``Bridge.name(...)`` call in nova.js to its argument counts."""
    calls: dict[str, set[int]] = {}
    for match in re.finditer(r"\bBridge\.(\w+)\(", JS):
        depth, index, commas, seen_argument, quote = 1, match.end(), 0, False, None
        while depth:
            char = JS[index]
            if quote:
                if char == "\\":
                    index += 2
                    continue
                if char == quote:
                    quote = None
            elif char in "\"'`":
                quote = char
            elif char in "([{":
                depth += 1
            elif char in ")]}":
                depth -= 1
            elif char == "," and depth == 1:
                commas += 1
            if depth and not char.isspace():
                seen_argument = True
            index += 1
        calls.setdefault(match.group(1), set()).add(
            commas + 1 if seen_argument else 0
        )
    return calls


# ---------------------------------------------------------------------------
# syntax
# ---------------------------------------------------------------------------


def test_web_assets_exist_and_decode_as_utf8() -> None:
    for name in shell.WEB_ASSETS:
        assert (WEB / name).is_file()
        (WEB / name).read_text(encoding="utf-8")


def test_nova_js_parses_as_modern_javascript() -> None:
    quickjs = pytest.importorskip("quickjs")
    # ``new Function`` compiles without executing: a SyntaxError raises.
    quickjs.Context().eval("new Function(" + json.dumps(JS) + ")")


def test_syntax_gate_detects_broken_javascript() -> None:
    quickjs = pytest.importorskip("quickjs")
    with pytest.raises(quickjs.JSException):
        quickjs.Context().eval("new Function('const broken = ;')")


def test_nova_js_runs_in_strict_mode_and_boots_on_dom_ready() -> None:
    assert JS.lstrip().startswith("/*") and '"use strict";' in JS
    assert 'document.addEventListener("DOMContentLoaded", main);' in JS


# ---------------------------------------------------------------------------
# bridge contract
# ---------------------------------------------------------------------------


def test_javascript_calls_match_the_python_bridge_api() -> None:
    calls = javascript_bridge_calls()
    python_api = public_bridge_methods()

    assert calls, "nova.js must call the bridge"
    assert set(calls) <= python_api, set(calls) - python_api
    for name, counts in calls.items():
        signature = inspect.signature(getattr(shell.NovaBridge, name))
        parameters = [p for p in signature.parameters.values() if p.name != "self"]
        required = sum(p.default is inspect.Parameter.empty for p in parameters)
        for count in counts:
            assert required <= count <= len(parameters), (name, count)


def test_demo_bridge_mirrors_the_python_api_exactly() -> None:
    assert demo_bridge_methods() == public_bridge_methods()


def test_lifecycle_hooks_are_not_exposed_to_the_page() -> None:
    assert {"_attach", "_shutdown", "_push", "_request_approval"} <= {
        name for name, _ in inspect.getmembers(shell.NovaBridge, inspect.isfunction)
    }
    assert not {"attach", "shutdown"} & public_bridge_methods()


def test_python_push_kinds_are_all_handled_by_the_page() -> None:
    shell_source = inspect.getsource(shell)
    pushed = set(re.findall(r'self\._push\(\s*"(\w+)"', shell_source))
    handlers = set(
        re.findall(r"^\s{2}(\w+)\(", section(JS, "const PUSH = {", "\n};"), re.MULTILINE)
    )
    assert pushed <= handlers, pushed - handlers


# ---------------------------------------------------------------------------
# honesty: no silent demo, explicit failure
# ---------------------------------------------------------------------------


def test_demo_mode_is_opt_in_and_never_a_fallback() -> None:
    assert "let Bridge = null;" in JS
    assert "let Bridge = DemoBridge" not in JS

    resolver = section(JS, "function resolveBridge(", "function bridgeReady(")
    assert "DemoBridge" not in resolver
    assert "pywebviewready" in resolver

    gate = section(JS, "function demoRequested(", "const BRIDGE_TIMEOUT_MS")
    assert 'get("demo") === "1"' in gate
    assert "if (window.pywebview) return false;" in gate

    main = JS[JS.index("async function main("):]
    assert main.count("Bridge = DemoBridge") == 1
    assert "if (demoRequested())" in main
    assert "showBootFailure(" in main
    assert "await resolveBridge()" in main


def test_demo_data_is_always_labelled() -> None:
    demo = section(JS, "const DemoBridge = {", "\n};")
    assert "DEMO" in demo and "Demo modu" in demo
    assert 'id="demo-badge"' in HTML
    assert "$(\"#demo-badge\").hidden = !State.demo" in JS


def test_page_declares_the_failure_and_confirmation_ui() -> None:
    for element_id in (
        "boot-error",
        "boot-error-text",
        "boot-retry",
        "confirm",
        "confirm-title",
        "confirm-text",
        "confirm-ok",
        "confirm-cancel",
        "chat-jump",
        "approval",
    ):
        assert f'id="{element_id}"' in HTML, element_id
    assert 'http-equiv="Content-Security-Policy"' in HTML
    assert not re.search(r'(src|href)="https?://', HTML), "page must be self-contained"


def test_delete_key_asks_for_confirmation_before_calling_python() -> None:
    body = section(JS, "async function deleteKey(", "function applyMotionPreference(")
    assert body.index("await confirmDialog(") < body.index("Bridge.delete_api_key(true)")
    assert "danger: true" in body
    assert "Bridge.delete_api_key()" not in JS


def test_chat_autoscroll_is_conditional_and_keyboard_scrolling_exists() -> None:
    assert "function chatAtBottom(" in JS
    assert "function updateChat(" in JS
    push_block = section(JS, "const PUSH = {", "\n};")
    assert push_block.count("updateChat(") >= 3  # stream, reply, voice_message
    keys = section(JS, "function handleScrollKeys(", "/* ── toasts")
    for key in ("ArrowDown", "ArrowUp", "PageDown", "PageUp", "Home", "End"):
        assert key in keys


def test_css_ignores_the_os_reduced_motion_setting() -> None:
    # Documented invariant: motion stays on unless the in-app switch is used,
    # so no media query may key off the OS setting (a comment may mention it).
    assert "@media (prefers-reduced-motion" not in CSS
    assert "@media(prefers-reduced-motion" not in CSS
    assert "body.reduced-motion" in CSS
