"""Static and syntax checks for the Nova page (index.html, css/, js/).

Deliberately lightweight: no browser automation. Every script is parsed
by QuickJS (a 400 KB engine) so a syntax slip cannot reach a release, and
the bridge contract between the page and NovaBridge is verified by
reading both sides. Behaviour in a real WebView2 window is covered by
the manual live acceptance run documented in docs/TESTING.md.
"""

from __future__ import annotations

import inspect
import json
import re

import pytest

from app.ui.nova import shell

WEB = shell.SOURCE_WEB_ROOT
HTML = (WEB / "index.html").read_text(encoding="utf-8")
JS_FILES = tuple(name for name in shell.WEB_ASSETS if name.endswith(".js"))
CSS_FILES = tuple(name for name in shell.WEB_ASSETS if name.endswith(".css"))
JS_SOURCES = {name: (WEB / name).read_text(encoding="utf-8") for name in JS_FILES}
JS = "\n".join(JS_SOURCES[name] for name in JS_FILES)
CSS = "\n".join((WEB / name).read_text(encoding="utf-8") for name in CSS_FILES)


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


def _argument_count(source: str, index: int) -> int:
    """Count the top-level arguments of the call whose ``(`` was just passed."""
    depth, commas, seen_argument, quote = 1, 0, False, None
    while depth:
        char = source[index]
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
    return commas + 1 if seen_argument else 0


def javascript_bridge_calls() -> dict[str, set[int]]:
    """Map every bridge call in the page to its argument counts.

    Two forms exist: the direct ``Bridge.name(...)`` and the guarded
    ``call("name", ...)`` helper, whose first argument is the method name.
    """
    calls: dict[str, set[int]] = {}
    for match in re.finditer(r"\bBridge\.(\w+)\(", JS):
        calls.setdefault(match.group(1), set()).add(_argument_count(JS, match.end()))
    for match in re.finditer(r"\bcall\(\s*\"(\w+)\"", JS):
        opening = JS.index("(", match.start())
        calls.setdefault(match.group(1), set()).add(
            _argument_count(JS, opening + 1) - 1
        )
    return calls


# ---------------------------------------------------------------------------
# assets & syntax
# ---------------------------------------------------------------------------


def test_web_assets_exist_and_decode_as_utf8() -> None:
    for name in shell.WEB_ASSETS:
        assert (WEB / name).is_file(), name
        (WEB / name).read_text(encoding="utf-8")


def test_asset_list_matches_the_web_directory_exactly() -> None:
    on_disk = {
        path.relative_to(WEB).as_posix()
        for path in WEB.rglob("*")
        if path.is_file()
    }
    assert on_disk == set(shell.WEB_ASSETS)


def test_index_links_every_stylesheet_and_script_in_order() -> None:
    assert re.findall(r'<link rel="stylesheet" href="([^"]+)">', HTML) == list(CSS_FILES)
    assert re.findall(r'<script src="([^"]+)"></script>', HTML) == list(JS_FILES)
    assert JS_FILES[0] == "js/foundation.js" and JS_FILES[-1] == "js/main.js"


def test_every_script_parses_as_modern_javascript() -> None:
    quickjs = pytest.importorskip("quickjs")
    for name, source in JS_SOURCES.items():
        # ``new Function`` compiles without executing: a SyntaxError raises.
        quickjs.Context().eval("new Function(" + json.dumps(source) + ")")


def test_syntax_gate_detects_broken_javascript() -> None:
    quickjs = pytest.importorskip("quickjs")
    with pytest.raises(quickjs.JSException):
        quickjs.Context().eval("new Function('const broken = ;')")


def test_scripts_run_in_strict_mode_and_boot_on_dom_ready() -> None:
    for name, source in JS_SOURCES.items():
        assert source.lstrip().startswith("/*"), name
        assert '"use strict";' in source, name
    assert 'document.addEventListener("DOMContentLoaded", main);' in JS_SOURCES["js/main.js"]


def test_referenced_element_ids_exist() -> None:
    referenced = set(re.findall(r"""\$\(\s*["']#([\w-]+)[ "']""", JS))
    declared = set(re.findall(r'id="([\w-]+)"', HTML + JS))
    missing = referenced - declared
    assert not missing, missing


# ---------------------------------------------------------------------------
# bridge contract
# ---------------------------------------------------------------------------


def test_javascript_calls_match_the_python_bridge_api() -> None:
    calls = javascript_bridge_calls()
    python_api = public_bridge_methods()

    assert calls, "the page must call the bridge"
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
    assert {"_attach", "_shutdown", "_push", "_request_approval", "_observe_application"} <= {
        name for name, _ in inspect.getmembers(shell.NovaBridge, inspect.isfunction)
    }
    assert not {"attach", "shutdown", "observe_application"} & public_bridge_methods()


def test_python_push_kinds_are_all_handled_by_the_page() -> None:
    shell_source = inspect.getsource(shell)
    pushed = set(re.findall(r'\._push\(\s*"(\w+)"', shell_source))
    handlers = set(
        re.findall(r"^\s{2}(\w+)\(", section(JS, "const PUSH = {", "\n};"), re.MULTILINE)
    )
    assert pushed <= handlers, pushed - handlers
    # Live observation channels exist and are consumed.
    assert {"tool_activity", "diagnostic_event", "voice_level"} <= pushed


# ---------------------------------------------------------------------------
# honesty: no silent demo, explicit failure, real data only
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

    main = JS_SOURCES["js/main.js"]
    assert main.count("Bridge = DemoBridge") == 1
    assert "if (demoRequested())" in main
    assert "showBootFailure(" in main
    assert "await resolveBridge()" in main


def test_demo_data_is_always_labelled() -> None:
    demo = section(JS, "const DemoBridge = {", "\n};")
    assert "DEMO" in demo and "Demo modu" in demo
    assert 'id="demo-badge"' in HTML
    assert "$(\"#demo-badge\").hidden = !State.demo" in JS


def test_boot_lines_come_from_the_real_snapshot() -> None:
    boot = section(JS, "function bootLines(", "async function runBootSequence(")
    for field in ("memory_count", "enabled_tools", "voice_available", "vision_available",
                  "windows_available", "diagnostic_integrity_valid"):
        assert field in boot, field


def test_unavailable_metrics_are_labelled_not_invented() -> None:
    diagnostics = section(JS, "const Diagnostics = {", "\n};")
    assert "kullanılamıyor" in diagnostics
    assert "Math.random" not in diagnostics


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
        "palette",
        "voice-stage",
        "mini",
        "context",
        "file-roots",
        "file-root-add",
        "snapshot-list",
    ):
        assert f'id="{element_id}"' in HTML, element_id
    assert 'http-equiv="Content-Security-Policy"' in HTML
    assert not re.search(r'(src|href)="https?://', HTML), "page must be self-contained"


def test_delete_key_asks_for_confirmation_before_calling_python() -> None:
    body = section(JS, "async function deleteKey(", "function renderShortcuts(")
    assert body.index("await confirmDialog(") < body.index('call("delete_api_key", true)')
    assert "danger: true" in body
    assert "Bridge.delete_api_key()" not in JS
    assert 'call("delete_api_key")' not in JS


def test_memory_deletion_asks_for_confirmation_and_forgetting_is_explicit() -> None:
    body = section(JS, "  async act(action, memoryId, card) {", "  edit(memory, card) {")
    assert body.index("confirmDialog(") < body.index('call("delete_memory", memoryId, true)')
    assert "danger: true" in body
    assert 'call("forget_memory", memoryId)' in body
    assert 'call("delete_memory", memoryId)' not in JS


def test_file_access_changes_ask_first_and_pass_the_confirmation_flag() -> None:
    files = section(JS, "const Files = {", "\n};")
    assert files.index("confirmDialog(") < files.index('call("grant_file_root", picked.path, true)')
    assert files.index('call("revoke_file_root"') > files.index("KALDIR")
    assert 'call("restore_snapshot", snapshotId, true)' in files
    assert 'call("grant_file_root", picked.path)' not in JS
    assert 'call("restore_snapshot", snapshotId)' not in JS


def test_approval_overlay_offers_no_blanket_permission() -> None:
    approval = section(HTML, 'id="approval"', 'id="confirm"')
    assert 'id="approval-allow"' in approval and 'id="approval-deny"' in approval
    assert "Bir kez izin ver" in approval
    assert "genellenmez" in approval
    assert "her zaman" not in approval.lower()


def test_chat_autoscroll_is_conditional_and_keyboard_scrolling_exists() -> None:
    assert "function chatAtBottom(" in JS
    assert "function updateChat(" in JS
    push_block = section(JS, "const PUSH = {", "\n};")
    assert push_block.count("updateChat(") >= 3  # stream, reply, voice_message
    keys = section(JS, "function handleScrollKeys(", "function bindKeyboard(")
    for key in ("ArrowDown", "ArrowUp", "PageDown", "PageUp", "Home", "End"):
        assert key in keys


def test_page_handles_tray_navigation_and_pause() -> None:
    push_block = section(JS, "const PUSH = {", "\n};")
    assert "  navigate(" in push_block and "  paused(" in push_block
    assert "NAV.some(([id]) => id === screen)" in push_block
    assert '"PAUSED": "DURAKLATILDI"' in JS
    assert "function setPaused(" in JS
    for name in (
        "async function sendCommand(",
        "async function toggleVoice(",
        "async function submitVision(",
        "async function submitResearch(",
    ):
        body = JS[JS.index(name) : JS.index(name) + 400]
        assert "State.paused" in body, name
    assert '.presence-orb[data-state="paused"]' in CSS


def test_command_palette_and_compact_mode_are_wired() -> None:
    assert 'if (event.ctrlKey && key === "k")' in JS
    assert "const Palette = {" in JS
    assert 'call("set_compact", enabled)' in JS
    assert "function applyCompact(" in JS


# ---------------------------------------------------------------------------
# medical academy: anatomy labels and the import report
# ---------------------------------------------------------------------------


# The lab only touches the overlay and the canvas rectangle, so QuickJS can
# run drawLabels with these two stubs in place of the real page.
LAB_DOM_STUBS = """
const OVERLAY = {innerHTML: ""};
const CANVAS = {getBoundingClientRect() { return {width: 900, height: 600}; }};
function $(selector) {
  if (selector === "#lab-overlay") return OVERLAY;
  if (selector === "#lab-canvas") return CANVAS;
  return null;
}
function $$(selector, host) { return []; }
function esc(value) { return String(value); }
function clamp(value) { return value; }
"""

LAB_SCENARIO = """
(() => {
  const bounds = __BOUNDS__;
  const anchor = __ANCHOR__;
  Lab.mesh = {bounds, landmarks: {acromion: anchor}};
  Lab.structure = {structure_id: "scapula", landmarks: [{landmark_id: "acromion", latin: "Acromion"}]};
  Lab.highlight = [];
  Lab.showLabels = true;
  Lab.camera = {yaw: 0.6, pitch: 0.25, distance: 2.6, panX: 0, panY: 0};
  Lab.drawLabels();
  // Where the geometry for that same point is drawn: buildBuffers writes
  // every vertex as (p - centre) / extent and the shader runs with
  // uModel = identity. Spelled out here so the check does not lean on the
  // helper it is checking.
  const centre = [0, 1, 2].map((axis) => (bounds.min[axis] + bounds.max[axis]) / 2);
  const extent = Math.max(...[0, 1, 2].map((axis) => bounds.max[axis] - bounds.min[axis])) || 1;
  const vertex = project([0, 1, 2].map((axis) => (anchor[axis] - centre[axis]) / extent),
    lookAtView(Lab.camera), perspective(0.9, 900 / 600, 0.05, 40), 900, 600);
  return JSON.stringify({html: OVERLAY.innerHTML, vertex});
})()
"""


def lab_label_and_vertex(bounds: dict, anchor: list[float]):
    """Run Lab.drawLabels for one landmark and report, in canvas pixels,
    where the label went and where the mesh puts that same point."""
    quickjs = pytest.importorskip("quickjs")
    context = quickjs.Context()
    context.eval(LAB_DOM_STUBS)
    context.eval(JS_SOURCES["js/medical.js"])
    scenario = LAB_SCENARIO.replace("__BOUNDS__", json.dumps(bounds)).replace(
        "__ANCHOR__", json.dumps(anchor)
    )
    measured = json.loads(context.eval(scenario))
    drawn = re.search(r"left:([-\d.]+)px; top:([-\d.]+)px", measured["html"])
    label = (float(drawn.group(1)), float(drawn.group(2))) if drawn else None
    vertex = (measured["vertex"]["x"], measured["vertex"]["y"]) if measured["vertex"] else None
    return label, vertex


def test_landmark_labels_are_projected_in_the_mesh_own_space() -> None:
    # A manifest anchor is written in the asset's coordinates, exactly like
    # the vertices. Projected raw it lands on a different part of the bone
    # (112 px away for the documented example), or vanishes entirely for an
    # asset in millimetres.
    cases = (
        ({"min": [0, 0, 0], "max": [1, 1, 1]}, [0.31, 0.62, 0.04]),        # docs example
        ({"min": [-100, -300, -60], "max": [345, 150, 65]}, [120.0, 140.0, 10.0]),  # mm asset
    )
    for bounds, anchor in cases:
        label, vertex = lab_label_and_vertex(bounds, anchor)
        assert label is not None and vertex is not None, (bounds, anchor)
        assert abs(label[0] - vertex[0]) < 1.0 and abs(label[1] - vertex[1]) < 1.0, (bounds, anchor)


def test_an_anchor_the_mesh_cannot_contain_draws_no_label() -> None:
    # An anchor outside the mesh's own bounds names no point on this bone, so
    # nothing is drawn rather than a Latin name over the wrong structure. The
    # anchor has to be one the camera would otherwise happily draw: a point far
    # behind the viewer projects to nothing whatever the guard does, and would
    # make this test pass with the guard deleted.
    bounds = {"min": [0, 0, 0], "max": [1, 1, 1]}
    inside, vertex = lab_label_and_vertex(bounds, [0.5, 0.5, 0.5])
    assert inside is not None and vertex is not None, "the control anchor must draw"

    outside, _vertex = lab_label_and_vertex(bounds, [1.5, 0.5, 0.5])
    assert outside is None


def test_the_lab_has_one_model_transform_for_geometry_and_labels() -> None:
    lab = section(JS, "const Lab = {", "\n};")
    build = section(lab, "  buildBuffers() {", "  drawMesh() {")
    labels = section(lab, "  drawLabels() {", "  /* ── quiz")
    assert "meshSpace(" in build and "space.place(positions" in build
    assert "meshSpace(" in labels and "space.place(anchor)" in labels
    assert "project(anchor" not in JS  # never the raw manifest coordinates


def test_the_import_report_and_a_paused_import_reach_the_student() -> None:
    medical = JS_SOURCES["js/medical.js"]
    assert '"kind": "job_report"' in inspect.getsource(shell)
    handler = section(medical, "  onJobReport(payload) {", "\n};")
    for field in ("added", "skipped", "without_key", "notes"):
        assert field in handler, field
    professor = section(medical, "  renderProfessor() {", "  async professorAction(")
    assert "Son içe aktarma" in professor and "report.notes" in professor
    # A document filed while the core is paused was not processed, so the
    # page must not toast it as a completed import.
    importer = section(medical, "  async importDocument() {", "  async openDocument(")
    assert "result.started" in importer


# ---------------------------------------------------------------------------
# design system
# ---------------------------------------------------------------------------


def test_css_ignores_the_os_reduced_motion_setting() -> None:
    # Documented invariant: motion stays on unless the in-app switch is used,
    # so no media query may key off the OS setting (a comment may mention it).
    assert "@media (prefers-reduced-motion" not in CSS
    assert "@media(prefers-reduced-motion" not in CSS
    assert "body.reduced-motion" in CSS


def test_design_tokens_live_in_one_place() -> None:
    tokens = (WEB / "css/tokens.css").read_text(encoding="utf-8")
    others = "\n".join(
        (WEB / name).read_text(encoding="utf-8") for name in CSS_FILES if name != "css/tokens.css"
    )
    for token in ("--accent:", "--bg:", "--motion-fast:", "--motion-cinematic:",
                  "--ease-standard:", "--ease-enter:", "--radius-2:", "--z-veil:"):
        assert token in tokens, token
        assert token not in others, token


def test_motion_vocabulary_is_shared_by_scripts_and_styles() -> None:
    motion = section(JS, "const Motion = {", "\n};")
    assert "fast: 120" in motion and "panel: 300" in motion and "cinematic: 620" in motion
    assert re.search(r"--motion-panel:\s+300ms;", CSS) and re.search(r"--motion-cinematic:\s+620ms;", CSS)


def test_user_facing_text_is_turkish() -> None:
    visible = re.sub(r"<[^>]+>", " ", section(HTML, "<body>", "<script"))
    for english in ("Loading", "Settings", "Diagnostics", "Allow", "Deny", "Retry", "Send"):
        assert re.search(rf"\b{english}\b", visible) is None, english


def test_notification_centre_is_declared_and_fed_only_by_pushes() -> None:
    for element_id in ("notify-btn", "notify-badge", "notify-panel", "notify-list",
                       "notify-read-all", "notify-clear", "notify-close"):
        assert f'id="{element_id}"' in HTML, element_id
    push = section(JS, "const PUSH = {", "\n};")
    assert "notification(payload) { Notify.onPush(payload); }" in push
    notify = section(JS, "const Notify = {", "\n};")
    # The page never invents entries: it only merges what the core pushed
    # or returned, and the unread count always comes from the core.
    assert "Math.random" not in notify
    assert 'call("mark_notifications_read"' in notify
    assert 'call("dismiss_notification"' in notify
    assert 'call("clear_notifications"' in notify
    assert "Bildirim yok." in notify
    assert "reportVisibility" in JS_SOURCES["js/shell.js"]
    assert 'document.addEventListener("visibilitychange", reportVisibility)' in JS
    assert "Bridge.set_visible(!document.hidden)" in JS
    assert "notifications_os_enabled" in section(JS, "const SETTING_LABELS = {", "\n};")
    assert '["Ctrl + Shift + N", "Bildirimler"]' in JS


def test_reply_chips_show_real_timing_only() -> None:
    chips = section(JS, "function assuranceChips(", "function fmtSecondsTr(")
    assert "metadata.elapsed_seconds" in chips and "metadata.tool_calls" in chips
    assert "Math.random" not in chips
    assert 'toLocaleString("tr-TR"' in JS_SOURCES["js/conversation.js"]


def test_routines_panel_is_declared_and_driven_by_the_core() -> None:
    for element_id in ("routines-panel", "routines-list", "routines-count", "routines-refresh"):
        assert f'id="{element_id}"' in HTML, element_id
    routines = section(JS, "const Routines = {", "\n};")
    assert 'call("list_routines")' in routines
    assert 'call("delete_routine", routineId, true)' in routines
    assert "confirmDialog(" in routines
    assert "Math.random" not in routines
    assert "Tanımlı rutin yok." in routines
    assert "Routines.load()" in JS_SOURCES["js/shell.js"]


def test_routine_editor_is_declared_and_uses_the_bridge() -> None:
    for element_id in ("routine-form", "routine-name", "routine-kind", "routine-at", "routine-minutes", "routine-prompt", "routine-add"):
        assert f'id="{element_id}"' in HTML, element_id
    routines = section(JS, "const Routines = {", "\n};")
    assert 'call("create_routine", name, prompt' in routines
    assert "Rutin adı ve komutu gerekli." in routines


def test_voice_silence_settings_are_labelled_for_the_settings_screen() -> None:
    from app.ui.nova import shell

    labels = section(JS, "const SETTING_LABELS = {", "\n};")
    groups = section(JS, "const SETTING_GROUPS = {", "\n};")
    for field in ("voice_trailing_silence_seconds", "voice_provisional_silence_seconds"):
        assert field in shell.RUNTIME_SETTING_FIELDS
        assert field in labels and field in groups
    assert "Konuşma sonu sessizliği" in labels


# ---------------------------------------------------------------------------
# the paper: figures and the review order, executed rather than grepped
# ---------------------------------------------------------------------------


PAPER_SCENARIO = """
(() => {
  const figure = Medical.figureMarkup({figure: {document_id: "d1", page_number: 7, title: "Slaytlar", caption: "Slaytlar · s. 7"}});
  const none = Medical.figureMarkup({figure: null}) + Medical.figureMarkup({});
  const questions = [
    {question_id: "a", stem: "Dogru cevaplanan", options: [{key: "A", text: "x"}, {key: "B", text: "y"}], correct_key: "A", answer: "A", correct: true, difficulty: 3},
    {question_id: "b", stem: "Bos birakilan", options: [{key: "A", text: "x"}, {key: "B", text: "y"}], correct_key: "A", answer: null, correct: null, difficulty: 3},
    {question_id: "c", stem: "Yanlis cevaplanan", options: [{key: "A", text: "x", explanation: "neden dogru"}, {key: "B", text: "y", explanation: "neden yanlis"}], correct_key: "A", answer: "B", correct: false, difficulty: 3, explanation: "Aciklama satiri"},
  ];
  const review = Medical.reviewSection(questions);
  return JSON.stringify({figure, none, review});
})()
"""


def test_a_figure_question_renders_its_lecture_page_and_nothing_without_one() -> None:
    quickjs = pytest.importorskip("quickjs")
    context = quickjs.Context()
    context.eval(LAB_DOM_STUBS)
    context.eval(JS_SOURCES["js/medical.js"])

    result = json.loads(context.eval(PAPER_SCENARIO))

    # The page key the loader fetches, and the caption naming the source.
    assert 'data-figure="d1|7"' in result["figure"]
    assert "Slaytlar · s. 7" in result["figure"]
    assert "Şekil yükleniyor" in result["figure"]
    assert result["none"] == "", "a question without a figure draws no figure block"


def test_the_review_puts_the_wrong_answers_first_with_their_explanation() -> None:
    quickjs = pytest.importorskip("quickjs")
    context = quickjs.Context()
    context.eval(LAB_DOM_STUBS)
    context.eval(JS_SOURCES["js/medical.js"])

    review = json.loads(context.eval(PAPER_SCENARIO))["review"]

    wrong = review.index("Yanlışların")
    blank = review.index("Boş bıraktıkların")
    right = review.index("Doğruların")
    assert wrong < blank < right
    # The wrong item sits in the first block, the correct one in the last.
    assert wrong < review.index("Yanlis cevaplanan") < blank
    assert right < review.index("Dogru cevaplanan")
    # The wrong answer is explained: the correct option is ticked, the chosen one crossed.
    assert "✓ A) x" in review and "✗ B) y" in review
    assert "neden yanlis" in review and "Aciklama satiri" in review
