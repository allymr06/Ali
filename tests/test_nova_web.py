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


# ---------------------------------------------------------------------------
# the lab's scene frame and the bell-ringer's grading, executed in QuickJS
# ---------------------------------------------------------------------------


LAB_HELPERS_SCENARIO = """
(() => {
  const yUp = meshSpace({bounds: {min: [0, 0, 0], max: [2, 4, 8]}});
  const zUp = meshSpace({bounds: {min: [0, 0, 0], max: [2, 4, 8]}, up_axis: "z"});
  const point = [2, 4, 8];
  return JSON.stringify({
    yUp: yUp.place(point), zUp: zUp.place(point),
    matches: {
      exact: latinMatches("Tuberculum majus", "Tuberculum majus"),
      folded: latinMatches("tuberculum majus", "Tuberculum majus"),
      turkish_keys: latinMatches("epıcondylus medıalıs", "Epicondylus medialis"),
      abbreviation: latinMatches("m. biceps brachii", "Musculus biceps brachii"),
      wrong_pair: latinMatches("tuberculum minus", "Tuberculum majus"),
      partial: latinMatches("humeri", "Caput humeri"),
      empty: latinMatches("", "Acromion"),
      stem: latinMatches("acromion", "Acromion"),
    },
  });
})()
"""


def test_a_z_up_asset_is_turned_into_the_viewer_frame_once() -> None:
    quickjs = pytest.importorskip("quickjs")
    context = quickjs.Context()
    context.eval(LAB_DOM_STUBS)
    context.eval(JS_SOURCES["js/medical.js"])

    result = json.loads(context.eval(LAB_HELPERS_SCENARIO))

    # Normalised on the longest axis (8): the top corner of the box.
    assert result["yUp"] == pytest.approx([0.125, 0.25, 0.5])
    # z becomes up, the old y goes to -z, so vertices, bounds and pins agree.
    assert result["zUp"] == pytest.approx([0.125, 0.5, -0.25])


def test_bell_ringer_grading_forgives_spelling_but_not_the_wrong_structure() -> None:
    quickjs = pytest.importorskip("quickjs")
    context = quickjs.Context()
    context.eval(LAB_DOM_STUBS)
    context.eval(JS_SOURCES["js/medical.js"])

    matches = json.loads(context.eval(LAB_HELPERS_SCENARIO))["matches"]

    assert matches["exact"] and matches["folded"] and matches["turkish_keys"] and matches["abbreviation"] and matches["stem"]
    assert not matches["wrong_pair"], "majus and minus are different structures"
    assert not matches["partial"], "naming the bone is not naming the landmark"
    assert not matches["empty"]


def test_the_lab_turns_the_model_about_the_viewer_axes() -> None:
    """A pull to the right spins the near face to the right, a pull down tips
    it down, from any orientation, with no clamp and no shear."""
    quickjs = pytest.importorskip("quickjs")
    context = quickjs.Context()
    context.eval(LAB_DOM_STUBS)
    context.eval(JS_SOURCES["js/medical.js"])
    report = json.loads(context.eval("""
      (() => {
        const apply = (view, p) => [0, 1, 2].map((row) => view[row] * p[0] + view[4 + row] * p[1] + view[8 + row] * p[2]);
        Lab.scene = null;
        Lab.camera = { rotation: null, distance: 2.6, panX: 0, panY: 0 };
        const near = [0, 0, 1];
        const before = apply(lookAtView(Lab.camera), near);
        Lab.turn(Math.PI / 2, 0);
        const right = apply(lookAtView(Lab.camera), near);
        Lab.camera = { rotation: null, distance: 2.6, panX: 0, panY: 0 };
        Lab.turn(0, Math.PI / 2);
        const down = apply(lookAtView(Lab.camera), near);
        // Well past the old ±83° clamp: the top of the model ends up at the bottom.
        Lab.camera = { rotation: null, distance: 2.6, panX: 0, panY: 0 };
        for (let i = 0; i < 200; i += 1) Lab.turn(0, Math.PI / 200);
        const flipped = apply(lookAtView(Lab.camera), [0, 1, 0]);
        for (let i = 0; i < 2000; i += 1) Lab.turn(0.013, -0.007);
        const r = Lab.camera.rotation;
        const lengths = [0, 3, 6].map((c) => Math.hypot(r[c], r[c + 1], r[c + 2]));
        const dots = [r[0] * r[3] + r[1] * r[4] + r[2] * r[5], r[0] * r[6] + r[1] * r[7] + r[2] * r[8], r[3] * r[6] + r[4] * r[7] + r[5] * r[8]];
        return JSON.stringify({ before, right, down, flipped, lengths, dots });
      })()
    """))
    assert [round(v, 6) for v in report["before"]] == [0, 0, 1]
    assert [round(v, 6) for v in report["right"]] == [1, 0, 0]
    assert [round(v, 6) for v in report["down"]] == [0, -1, 0]
    assert [round(v, 6) for v in report["flipped"]] == [0, -1, 0]
    assert all(abs(length - 1) < 1e-9 for length in report["lengths"])
    assert all(abs(value) < 1e-9 for value in report["dots"])


def test_the_lab_pans_along_the_screen_and_the_old_orbit_still_reads() -> None:
    """Pan moves the picture on the screen's own axes whichever way the model
    faces, and a camera written with yaw and pitch draws the same view as the
    rotation it stands for."""
    quickjs = pytest.importorskip("quickjs")
    context = quickjs.Context()
    context.eval(LAB_DOM_STUBS)
    context.eval(JS_SOURCES["js/medical.js"])
    report = json.loads(context.eval("""
      (() => {
        const projection = perspective(0.9, 900 / 600, 0.05, 40);
        const at = (camera) => project([0, 0, 0], lookAtView(camera), projection, 900, 600);
        const turned = { rotation: rotationFromAngles(1.1, -0.7), distance: 2.6, panX: 0, panY: 0 };
        const centre = at(turned);
        const right = at({ ...turned, panX: 0.4 });
        const up = at({ ...turned, panY: 0.4 });
        const angles = lookAtView({ yaw: 0.6, pitch: 0.25, distance: 2.6, panX: 0, panY: 0 });
        const rotation = lookAtView({ rotation: rotationFromAngles(0.6, 0.25), distance: 2.6, panX: 0, panY: 0 });
        return JSON.stringify({ centre, right, up, same: Array.from(angles).every((v, i) => Math.abs(v - rotation[i]) < 1e-9) });
      })()
    """))
    assert report["right"]["x"] > report["centre"]["x"] and abs(report["right"]["y"] - report["centre"]["y"]) < 1e-6
    assert report["up"]["y"] < report["centre"]["y"] and abs(report["up"]["x"] - report["centre"]["x"]) < 1e-6
    assert report["same"]


def test_the_lab_stage_is_black_and_its_tissues_matte_and_distinct() -> None:
    """Black behind the model, no rim or specular term in the shader, and
    every pair of tissue colours far enough apart to tell at a glance."""
    quickjs = pytest.importorskip("quickjs")
    source = JS_SOURCES["js/medical.js"]
    assert "background: #000" in re.search(r"#lab-canvas \{[^}]*\}", CSS).group(0)
    assert source.count("gl.clearColor(0, 0, 0, 1)") == 2
    fragment = source[source.index("gl.FRAGMENT_SHADER"):source.index("if (!vertex || !fragment)")]
    assert "rim" not in fragment.lower() and "specular" not in fragment.lower()
    assert "uRim" not in source
    context = quickjs.Context()
    context.eval(LAB_DOM_STUBS)
    context.eval(source)
    colours = json.loads(context.eval("JSON.stringify(LAB_KIND_COLOURS)"))
    kinds = ["bone", "joint", "muscle", "artery", "vein", "nerve", "ligament", "region"]
    assert set(kinds) <= set(colours)
    for index, first in enumerate(kinds):
        for second in kinds[index + 1:]:
            distance = sum((a - b) ** 2 for a, b in zip(colours[first], colours[second])) ** 0.5
            assert distance > 0.3, f"{first} and {second} are too alike: {distance:.2f}"
    assert all(0 <= channel <= 1 for kind in kinds for channel in colours[kind])


def test_lab_isolation_preserves_layer_choices_and_only_draws_selected_structure() -> None:
    context = pytest.importorskip("quickjs").Context()
    context.eval(LAB_DOM_STUBS)
    context.eval(JS_SOURCES["js/medical.js"])
    context.eval('Lab.scheduleDraw = () => {}; function toast() {}')
    report = json.loads(context.eval('''
      (() => {
        const bone = {structure_id: "bone", kind: "bone"};
        const vein = {structure_id: "vein", kind: "vein"};
        Lab.scene = {items: [bone, vein], visible: new Set(["bone"])};
        Lab.structure = {structure_id: "vein"};
        Lab.toggleIsolate();
        const during = [Lab.itemVisible(bone), Lab.itemVisible(vein)];
        Lab.toggleIsolate();
        const after = [Lab.itemVisible(bone), Lab.itemVisible(vein)];
        Lab.structure = {structure_id: "not-in-scene"};
        Lab.toggleIsolate();
        return JSON.stringify({during, after, invalid: Lab.isolated});
      })()
    '''))
    assert report == {"during": [False, True], "after": [True, False], "invalid": False}


def test_scene_loading_discards_old_replies_and_finishes_partial_failures():
    context = pytest.importorskip("quickjs").Context()
    context.eval(LAB_DOM_STUBS)
    context.eval(JS_SOURCES["js/medical.js"])
    context.eval('''
      const pendingMeshes = [];
      Medical.request = (action, params) => new Promise(resolve => pendingMeshes.push(resolve));
      Lab.renderLayers = Lab.draw = Lab.resetCamera = () => {};
      Lab.select = async () => {};
      Lab.scenes = [{scene_id:"old", available:["old-bone"]}, {scene_id:"new", available:["new-bone","missing"]}];
      const meshReply = {ok:true,mesh:{positions:[0,0,0],bounds:{min:[0,0,0],max:[1,1,1]}}};
      void Lab.openScene("old"); void Lab.openScene("new");
      pendingMeshes[0](meshReply);
    ''')
    while context.execute_pending_job():
        pass
    assert context.eval("Lab.scene.items.length") == 0
    context.eval("pendingMeshes[1](meshReply); pendingMeshes[2]({ok:false});")
    while context.execute_pending_job():
        pass
    report = json.loads(context.eval('JSON.stringify({ids:Lab.scene.items.map(i=>i.structure_id), total:Lab.scene.total, failed:Lab.scene.failed, bounds:!!Lab.scene.bounds})'))
    assert report == {"ids": ["new-bone"], "total": 1, "failed": 1, "bounds": True}


def test_scene_with_no_readable_models_stops_loading():
    context = pytest.importorskip("quickjs").Context()
    context.eval(LAB_DOM_STUBS)
    context.eval(JS_SOURCES["js/medical.js"])
    context.eval('''
      Medical.request = async () => ({ok:false});
      Lab.renderLayers = Lab.draw = () => {};
      Lab.scenes = [{scene_id:"missing",available:["no-file"]}];
      void Lab.openScene("missing");
    ''')
    while context.execute_pending_job():
        pass
    assert context.eval("Lab.scene === null")
    assert "yüklenemedi" in context.eval("Lab.meshNotice")


@pytest.mark.parametrize("interruption", ["selection", "scene", "mesh"])
def test_late_structure_or_mesh_reply_cannot_replace_newer_selection(interruption):
    context = pytest.importorskip("quickjs").Context()
    context.eval(LAB_DOM_STUBS)
    context.eval(JS_SOURCES["js/medical.js"])
    context.eval('''
      const requests = [];
      Medical.request = (action, params) => new Promise(resolve => requests.push({action, params, resolve}));
      Lab.renderList = Lab.renderInfo = Lab.renderLayers = Lab.draw = Lab.resetCamera = () => {};
      Lab.scenes = [{scene_id:"new-scene",available:["new"]}];
      const reply = id => ({ok:true,structure:{structure_id:id,model:{available:true}}});
      void Lab.select("old");
    ''')
    if interruption == "mesh":
        context.eval('requests[0].resolve(reply("old"));')
        while context.execute_pending_job():
            pass
        context.eval('void Lab.select("new"); requests[2].resolve({ok:true,structure:{structure_id:"new"}});')
    elif interruption == "selection":
        context.eval('void Lab.select("new"); requests[1].resolve({ok:true,structure:{structure_id:"new"}});')
    else:
        context.eval('void Lab.openScene("new-scene");')
    while context.execute_pending_job():
        pass
    if interruption == "mesh":
        context.eval('requests[1].resolve({ok:true,mesh:{positions:[99,99,99]}});')
    else:
        context.eval('requests[0].resolve(reply("old"));')
    while context.execute_pending_job():
        pass
    if interruption == "scene":
        assert context.eval('Lab.scene.scene_id') == "new-scene"
        assert context.eval('!Lab.structure || Lab.structure.structure_id !== "old"')
    else:
        assert context.eval('Lab.structure.structure_id') == "new"
        assert context.eval('Lab.mesh === null')


def test_lab_drawing_buffer_is_sharp_bounded_and_not_reallocated_on_every_draw() -> None:
    context = pytest.importorskip("quickjs").Context()
    context.eval(LAB_DOM_STUBS)
    context.eval(JS_SOURCES["js/medical.js"])
    report = json.loads(context.eval('''
      (() => {
        let devicePixelRatio = 1;
        globalThis.devicePixelRatio = devicePixelRatio;
        let width = 900, height = 600, writes = 0, w = 0, h = 0;
        const canvas = {getBoundingClientRect: () => ({width, height}),
          get width() { return w; }, set width(v) { w = v; writes++; },
          get height() { return h; }, set height(v) { h = v; writes++; }};
        Lab.sizeCanvas(canvas); const normal = [w, h];
        Lab.sizeCanvas(canvas); const unchanged = writes;
        width = 7680; height = 4320; globalThis.devicePixelRatio = 3;
        Lab.sizeCanvas(canvas); const large = [w, h];
        return JSON.stringify({normal, unchanged, large});
      })()
    '''))
    assert report["normal"] == [1350, 900]
    assert report["unchanged"] == 2
    assert report["large"][0] * report["large"][1] <= 8_000_000
    assert max(report["large"]) <= 4096


def test_lab_camera_presets_fit_portrait_views_and_ignore_unknown_names() -> None:
    context = pytest.importorskip("quickjs").Context()
    context.eval(LAB_DOM_STUBS)
    context.eval(JS_SOURCES["js/medical.js"])
    report = json.loads(context.eval('''
      (() => {
        Lab.scheduleDraw = () => {};
        Lab.mesh = {bounds: {min: [-1,-1,-1], max: [1,1,1]}};
        Lab.setView("front"); const front = Lab.camera.distance;
        CANVAS.getBoundingClientRect = () => ({width: 300, height: 900});
        Lab.setView("side"); const narrow = Lab.camera.distance;
        const before = JSON.stringify(Lab.camera);
        Lab.setView("untrusted-name");
        return JSON.stringify({front, narrow, unchanged: before === JSON.stringify(Lab.camera)});
      })()
    '''))
    assert report["narrow"] > report["front"] > 0.8
    assert report["unchanged"]


@pytest.mark.parametrize("native", [True, False])
def test_lab_fullscreen_roundtrip_handles_native_and_rejected_api(native: bool) -> None:
    context = pytest.importorskip("quickjs").Context()
    context.eval('''
      const classes = new Set(); let moved = false; let returned = false;
      const button = {setAttribute(k,v) {this[k]=v;}, focus() {}};
      const stage = {classList: {contains: k => classes.has(k), add: k => classes.add(k), remove: k => classes.delete(k)}, before() {}};
      const document = {fullscreenElement: null, body: {appendChild() {moved=true;}},
        createComment: () => ({replaceWith() {returned=true;}}),
        async exitFullscreen() {this.fullscreenElement=null;}};
      function $(s) {return s === "#lab-stage" ? stage : s === "#lab-fullscreen" ? button : null;}
      function $$(s) {return [];}
      function toast() {}
    ''')
    context.eval(JS_SOURCES["js/medical.js"])
    context.eval("Lab.scheduleDraw = () => {};")
    context.eval("stage.requestFullscreen = async () => {" + (
        "document.fullscreenElement=stage;" if native else 'throw new Error("denied");'
    ) + "}; void Lab.toggleFullscreen();")
    while context.execute_pending_job():
        pass
    assert context.eval('button["aria-pressed"]') == "true"
    assert context.eval("moved") is (not native)
    context.eval("void Lab.toggleFullscreen();")
    while context.execute_pending_job():
        pass
    assert context.eval('button["aria-pressed"]') == "false"
    assert context.eval("Lab.fullscreenBusy") is False
    assert context.eval("returned") is (not native)


def test_lab_webgl_failure_does_not_recurse_or_discard_registered_mesh() -> None:
    context = pytest.importorskip("quickjs").Context()
    context.eval(LAB_DOM_STUBS)
    context.eval(JS_SOURCES["js/medical.js"])
    context.eval('''
      Lab.mesh = {positions: [1,2,3]}; Lab.scene = {items: []};
      Lab.ensureGL = () => null; Lab.drawSchematic = () => {};
      Lab.draw = () => {throw new Error("recursive draw");};
      Lab.drawMesh();
    ''')
    assert context.eval("Lab.mesh.positions.length") == 3
    assert context.eval("CANVAS.hidden") is True


def test_lab_labels_are_separated_without_moving_the_anatomical_anchor() -> None:
    context = pytest.importorskip("quickjs").Context()
    context.eval(LAB_DOM_STUBS)
    context.eval(JS_SOURCES["js/medical.js"])
    report = json.loads(context.eval('''
      (() => {
        const occupied = [], point = {x: 200, y: 100}, rect = {width: 800, height: 600};
        const labels = Array.from({length: 10}, () => placeLabLabel(point, 180, rect, occupied));
        return JSON.stringify({labels, point, outside: placeLabLabel({x:-20,y:100}, 180, rect, occupied)});
      })()
    '''))
    assert report["point"] == {"x": 200, "y": 100}
    assert report["outside"] is None
    ys = sorted(label["y"] for label in report["labels"])
    assert all(b - a >= 28 for a, b in zip(ys, ys[1:]))


def test_lab_isolated_mesh_and_labels_use_selected_bounds_not_whole_scene() -> None:
    context = pytest.importorskip("quickjs").Context()
    context.eval(LAB_DOM_STUBS)
    context.eval(JS_SOURCES["js/medical.js"])
    context.eval('''
      const MESH = {bounds: {min:[10,20,30], max:[11,21,31]}, up_axis:"z"};
      Lab.scene = {bounds:{min:[0,0,0],max:[100,100,100]}, items:[{structure_id:"bone",mesh:MESH}]};
      Lab.structure = {structure_id:"bone"}; Lab.isolated = true;
    ''')
    assert context.eval("Lab.viewMesh() === MESH") is True
    context.eval("Lab.isolated = false;")
    assert context.eval("Lab.viewMesh().bounds.max[0]") == 100


def test_lab_redraws_are_coalesced_per_animation_frame() -> None:
    context = pytest.importorskip("quickjs").Context()
    context.eval(LAB_DOM_STUBS)
    context.eval(JS_SOURCES["js/medical.js"])
    context.eval('''
      let calls = 0, draws = 0, callback;
      function requestAnimationFrame(fn) { calls++; callback=fn; return calls; }
      Lab.draw = () => {draws++;};
      for (let i=0;i<100;i++) Lab.scheduleDraw();
    ''')
    assert context.eval("calls") == 1
    context.eval("callback();")
    assert context.eval("draws") == 1
    assert context.eval("Lab.frame") == 0


@pytest.mark.parametrize("axis,expected", [("y", [0, -1, 0]), ("z", [0, 0, 1])])
def test_lab_vertex_normals_rotate_with_geometry(axis: str, expected: list[int]) -> None:
    context = pytest.importorskip("quickjs").Context()
    context.eval(LAB_DOM_STUBS)
    context.eval(JS_SOURCES["js/medical.js"])
    context.eval('''
      const uploads = [];
      Lab.gl = {createBuffer: () => ({}), bindBuffer() {}, bufferData(t, data) {uploads.push(Array.from(data));}};
      const mesh = {positions:[0,0,0, 1,0,0, 0,0,1], indices:[0,1,2],
        normals:[0,-1,0], normal_indices:[0,0,0], bounds:{min:[0,0,0], max:[1,1,1]}};
    ''')
    context.eval("mesh.up_axis = " + json.dumps(axis) + "; Lab.buildBuffersFor(mesh, meshSpace(mesh));")
    actual = json.loads(context.eval("JSON.stringify(uploads[1])"))
    assert actual == expected * 3


def test_the_lab_hides_its_schematic_map_by_attribute_so_the_canvas_gets_the_mouse() -> None:
    """An SVG has no `hidden` property. Assigning one hid nothing: the
    transparent map stayed over the canvas and took every drag and click.
    The lab must set the attribute, which is what the [hidden] rule reads."""
    quickjs = pytest.importorskip("quickjs")
    source = JS_SOURCES["js/medical.js"]
    assert "schematic.hidden =" not in source, "the map must be hidden through setHidden, not a property an SVG lacks"
    assert "#lab-schematic[hidden] { display: none; }" in CSS
    context = quickjs.Context()
    context.eval(LAB_DOM_STUBS)
    context.eval(source)
    report = json.loads(context.eval("""
      (() => {
        const svg = { attrs: {}, toggleAttribute(name, force) { if (force) this.attrs[name] = ""; else delete this.attrs[name]; } };
        setHidden(svg, true);
        const hiddenAttr = "hidden" in svg.attrs;
        setHidden(svg, false);
        const shownAttr = "hidden" in svg.attrs;
        const plain = {};
        setHidden(plain, true);
        return JSON.stringify({ hiddenAttr, shownAttr, plainHidden: plain.hidden === true });
      })()
    """))
    assert report == {"hiddenAttr": True, "shownAttr": False, "plainHidden": True}


def test_the_lab_shader_keeps_contrast_without_a_highlight() -> None:
    """Detail without shine: the shade factor has a low floor and a grazing
    darkening, and still no specular power term or rim colour."""
    source = JS_SOURCES["js/medical.js"]
    fragment = source[source.index("gl.FRAGMENT_SHADER"):source.index("if (!vertex || !fragment)")]
    assert "grazing" in fragment and "0.20 + 0.62 * key" in fragment
    assert "uRim" not in fragment and "specular" not in fragment.lower()
    # The only pow() is the grazing term, which darkens; nothing is added to the colour.
    assert fragment.count("pow(") == 1 and "pow(1.0 - facing" in fragment
    assert "+ uRim" not in fragment and "gl_FragColor = vec4(uColor * shade, 1.0)" in fragment


def test_a_card_renders_its_tables_with_the_first_cell_as_a_row_heading() -> None:
    quickjs = pytest.importorskip("quickjs")
    context = quickjs.Context()
    context.eval(LAB_DOM_STUBS)
    context.eval(JS_SOURCES["js/medical.js"])
    html = context.eval("""
      (() => {
        const INFO = { innerHTML: "" };
        const previous = $;
        $ = (selector) => selector === "#lab-info" ? INFO : previous(selector);
        Lab.bell = null;
        Lab.highlight = [];
        Lab.structure = {
          structure_id: "neurocranium", canonical: "Neurocranium", turkish: "Beyin kutusu", english: "Braincase",
          kind_label: "Bölge", region_label: "Baş ve boyun", landmarks: [], sections: [{ label: "Tanım", items: ["Sekiz kemik"] }],
          tables: [{ title: "Delikler", columns: ["Delik", "Kemik", "Geçen"], rows: [["Foramen rotundum", "Os sphenoidale", "V2"], ["Foramen ovale", "Os sphenoidale", "V3"]] }],
          relations: [], source: "",
        };
        Lab.renderInfo();
        $ = previous;
        return INFO.innerHTML;
      })()
    """)
    assert "<table class=\"lab-table\">" in html and html.count("<tr>") == 3
    assert "<th>Delik</th><th>Kemik</th><th>Geçen</th>" in html
    assert "<th scope=\"row\">Foramen rotundum</th><td>Os sphenoidale</td><td>V2</td>" in html
    assert html.index("Sekiz kemik") < html.index("<table"), "the prose sections come before the tables"


def test_a_scene_with_a_palette_is_drawn_and_hidden_structure_by_structure() -> None:
    """A skull is all bone: the chips are one per structure in the scene's own
    colours, visibility follows the structure, and a scene item wears the
    palette colour where it has one and its kind's colour where it does not."""
    quickjs = pytest.importorskip("quickjs")
    context = quickjs.Context()
    context.eval(LAB_DOM_STUBS)
    context.eval(JS_SOURCES["js/medical.js"])
    report = json.loads(context.eval("""
      (() => {
        const LAYERS = { innerHTML: "" };
        const previous = $;
        $ = (selector) => selector === "#lab-layers" ? LAYERS : previous(selector);
        Lab.scenes = [{ scene_id: "skull", title: "Kafa" }];
        const items = [
          { structure_id: "os_frontale", kind: "bone", canonical: "Os frontale" },
          { structure_id: "os_occipitale", kind: "bone", canonical: "Os occipitale" },
        ];
        Lab.scene = { scene_id: "skull", title: "Kafa", items, palette: { os_frontale: [1, 0.5, 0] }, visible: new Set(["os_frontale", "os_occipitale"]), note: "iki taraf" };
        Lab.renderLayers();
        const chips = LAYERS.innerHTML;
        Lab.toggleLayer("os_frontale");
        const afterToggle = { frontale: Lab.itemVisible(items[0]), occipitale: Lab.itemVisible(items[1]) };
        const colours = { frontale: Lab.itemColour(items[0]), occipitale: Lab.itemColour(items[1]) };
        Lab.scene = { scene_id: "arm", title: "Kol", items: [{ structure_id: "humerus", kind: "bone", canonical: "Humerus" }], palette: null, visible: new Set(["bone"]), note: "" };
        Lab.renderLayers();
        const kindChips = LAYERS.innerHTML;
        $ = previous;
        return JSON.stringify({ chips, afterToggle, colours, kindChips, bone: LAB_KIND_COLOURS.bone });
      })()
    """))
    assert 'data-layer="os_frontale"' in report["chips"] and 'data-layer="os_occipitale"' in report["chips"]
    assert "rgb(255,128,0)" in report["chips"] and "iki taraf" in report["chips"]
    assert report["afterToggle"] == {"frontale": False, "occipitale": True}
    assert report["colours"]["frontale"] == [1, 0.5, 0] and report["colours"]["occipitale"] == report["bone"]
    assert 'data-layer="bone"' in report["kindChips"] and "sağ taraf" in report["kindChips"]


# ---------------------------------------------------------------------------
# the bell-ringer's submissions, executed in QuickJS with deferred requests
# ---------------------------------------------------------------------------

# Timers are recorded, not run, so a test rings the bell itself; every
# academy request is a promise the test resolves or rejects by hand.
BELL_STUBS = """
let TIMERS = [];
function setInterval(fn, ms) { const id = TIMERS.length + 1; TIMERS.push({ id, fn }); return id; }
function clearInterval(id) { TIMERS = TIMERS.filter((timer) => timer.id !== id); }
function TICK(count) { for (let i = 0; i < (count || 1); i += 1) TIMERS.slice().forEach((timer) => timer.fn()); }
const TOASTS = [];
function toast(text, kind) { TOASTS.push({ text, kind }); }
"""

BELL_SETUP = """
const STRUCTURES = {
  scapula: { structure_id: "scapula", canonical: "Scapula", turkish: "Kürek kemiği", landmarks: [{ landmark_id: "acromion", latin: "Acromion", turkish: "Akromion" }] },
  humerus: { structure_id: "humerus", canonical: "Humerus", turkish: "Kol kemiği", landmarks: [{ landmark_id: "caput_humeri", latin: "Caput humeri", turkish: "Humerus başı" }] },
};
// Math.random() === 0 reverses a two-item pool, so the stations are scapula then humerus.
const POOL = [{ structure_id: "humerus", landmark_id: "caput_humeri" }, { structure_id: "scapula", landmark_id: "acromion" }];
Math.random = () => 0;
Lab.draw = () => {};
Lab.select = async (id) => { Lab.structure = STRUCTURES[id]; };
Lab.structure = STRUCTURES.scapula;
const REQUESTS = [];
Medical.request = (action, params) => new Promise((resolve, reject) => { REQUESTS.push({ action, params: JSON.parse(JSON.stringify(params)), resolve, reject }); });
function snapshot() {
  const bell = Lab.bell;
  return JSON.stringify({
    run: bell ? bell.run : null,
    index: bell ? bell.index : null,
    current: bell ? bell.current : null,
    loading: bell ? bell.loading : null,
    pending: bell ? !!bell.pending : null,
    failed: bell ? bell.failed : null,
    answers: bell ? bell.answers.map((a) => ({ given: a.given, correct: a.correct, saved: a.saved, timedOut: a.timedOut, skipped: a.skipped, latin: a.latin, structure: a.structure })) : null,
    requests: REQUESTS.map((r) => r.params),
    toasts: TOASTS.map((t) => t.text),
    timers: TIMERS.length,
  });
}
"""


def bell_context():
    quickjs = pytest.importorskip("quickjs")
    context = quickjs.Context()
    context.eval(LAB_DOM_STUBS)
    context.eval(BELL_STUBS)
    context.eval(JS_SOURCES["js/medical.js"])
    context.eval(BELL_SETUP)
    return context


def drain(context) -> None:
    """Run every settled promise continuation."""
    for _ in range(500):
        if not context.execute_pending_job():
            return


def bell(context) -> dict:
    return json.loads(context.eval("snapshot()"))


def test_a_bell_ringer_answer_is_saved_once_and_advances_once() -> None:
    context = bell_context()
    context.eval("Lab.beginBellRinger(POOL, 60)")
    drain(context)
    state = bell(context)
    assert state["index"] == 0 and state["current"] == {"structure_id": "scapula", "landmark_id": "acromion"}
    assert state["timers"] == 1 and state["loading"] is None

    context.eval('void Lab.answerStation("acromion")')
    state = bell(context)
    assert state["pending"] is True and state["current"] is None and state["timers"] == 0
    assert state["answers"] == [{"given": "acromion", "correct": True, "saved": False, "timedOut": False, "skipped": False, "latin": "Acromion", "structure": "Scapula"}]
    assert len(state["requests"]) == 1
    request = state["requests"][0]
    assert request["structure_id"] == "scapula" and request["landmark_id"] == "acromion" and request["correct"] is True
    assert request["submission_id"].startswith("1-0-")

    context.eval("REQUESTS[0].resolve({ ok: true, mastery: [] })")
    drain(context)
    state = bell(context)
    assert state["index"] == 1 and state["current"] == {"structure_id": "humerus", "landmark_id": "caput_humeri"}
    assert state["pending"] is False and state["answers"][0]["saved"] is True
    assert len(state["requests"]) == 1 and state["toasts"] == []
    assert context.eval("Lab.structure.structure_id") == "humerus"


def test_repeated_clicks_and_enter_on_one_station_submit_it_once() -> None:
    context = bell_context()
    context.eval("Lab.beginBellRinger(POOL, 60)")
    drain(context)

    # Click, click, Enter — the second and third find the station claimed.
    context.eval('void Lab.answerStation("acromion"); void Lab.answerStation("acromion"); void Lab.answerStation("acromion")')
    state = bell(context)
    assert len(state["answers"]) == 1 and len(state["requests"]) == 1

    context.eval("REQUESTS[0].resolve({ ok: true, mastery: [] })")
    drain(context)
    state = bell(context)
    assert state["index"] == 1, "advanced exactly once"
    assert len(state["answers"]) == 1 and len(state["requests"]) == 1


def test_the_bell_and_a_manual_answer_close_together_count_one_answer() -> None:
    context = bell_context()
    context.eval("Lab.beginBellRinger(POOL, 60)")
    drain(context)

    # The clock runs out in the same turn as the student presses Cevapla.
    context.eval('TICK(60); void Lab.answerStation("acromion")')
    state = bell(context)
    assert len(state["answers"]) == 1 and state["answers"][0]["timedOut"] is True
    assert len(state["requests"]) == 1 and state["requests"][0]["correct"] is False
    assert state["timers"] == 0, "the station's timer stopped with the claim"

    context.eval("REQUESTS[0].resolve({ ok: true })")
    drain(context)
    state = bell(context)
    assert state["index"] == 1 and len(state["answers"]) == 1
    # A late tick from the old station cannot touch the new one.
    context.eval("TIMERS.length = 0; void Lab.answerStation(\"\", { timedOut: true })")
    assert len(bell(context)["answers"]) == 2 and bell(context)["answers"][1]["timedOut"] is True


def test_a_failed_save_is_reported_and_retried_without_a_second_record() -> None:
    context = bell_context()
    context.eval("Lab.beginBellRinger(POOL, 60)")
    drain(context)
    context.eval('void Lab.answerStation("acromion")')

    context.eval('REQUESTS[0].resolve({ ok: false, error: "Depo kilitli." })')
    drain(context)
    state = bell(context)
    assert state["index"] == 0 and state["pending"] is True, "nothing advanced"
    assert state["failed"] == {"error": "Depo kilitli."}
    assert state["toasts"] == ["Cevap kaydedilemedi: Depo kilitli."]
    assert state["answers"][0]["saved"] is False and len(state["answers"]) == 1

    context.eval("Lab.retryStation()")
    state = bell(context)
    assert len(state["requests"]) == 2 and len(state["answers"]) == 1
    assert state["requests"][1]["submission_id"] == state["requests"][0]["submission_id"], "the retry names the same submission"
    assert state["failed"] is None

    context.eval("REQUESTS[1].resolve({ ok: true, mastery: [] })")
    drain(context)
    state = bell(context)
    assert state["index"] == 1 and state["answers"][0]["saved"] is True and state["pending"] is False

    # A rejected request (the bridge threw) reads the same way, and the
    # student may go on with the station left unrecorded.
    context.eval('void Lab.answerStation("caput humeri")')
    context.eval('REQUESTS[2].reject(new Error("Köprü koptu"))')
    drain(context)
    state = bell(context)
    assert state["failed"]["error"] == "Köprü koptu" and state["index"] == 1
    context.eval("const LAST = Lab.bell; Lab.skipSaving()")
    drain(context)
    assert bell(context)["run"] is None, "the last station settled ends the exam"
    assert json.loads(context.eval("JSON.stringify(LAST.answers.map((a) => a.saved))")) == [True, False]
    assert len(json.loads(context.eval("JSON.stringify(REQUESTS.map((r) => r.params))"))) == 3


def test_finishing_the_exam_while_a_save_is_pending_leaves_the_late_reply_inert() -> None:
    context = bell_context()
    context.eval("Lab.beginBellRinger(POOL, 60)")
    drain(context)
    context.eval('void Lab.answerStation("acromion")')

    context.eval("const OLD = Lab.bell; Lab.finishBellRinger()")
    assert bell(context)["run"] is None
    assert context.eval("OLD.answers[0].saved") == "pending" and context.eval("OLD.pending") is None

    context.eval("REQUESTS[0].resolve({ ok: true, mastery: [] })")
    drain(context)
    state = bell(context)
    assert state["run"] is None and len(state["requests"]) == 1 and state["toasts"] == []


def test_a_new_exam_started_before_the_old_reply_is_not_touched_by_it() -> None:
    context = bell_context()
    context.eval("Lab.beginBellRinger(POOL, 60)")
    drain(context)
    context.eval('void Lab.answerStation("acromion")')

    context.eval("Lab.beginBellRinger(POOL, 60)")
    drain(context)
    fresh = bell(context)
    assert fresh["run"] == 2 and fresh["index"] == 0 and fresh["answers"] == []

    context.eval("REQUESTS[0].resolve({ ok: true, mastery: [] })")
    drain(context)
    state = bell(context)
    assert state["run"] == 2 and state["index"] == 0 and state["answers"] == []
    assert state["current"] == {"structure_id": "scapula", "landmark_id": "acromion"} and state["pending"] is False
    assert len(state["requests"]) == 1
    # The second exam answers and advances on its own account.
    context.eval('void Lab.answerStation("acromion")')
    assert bell(context)["requests"][1]["submission_id"].startswith("2-0-")


def test_a_station_is_graded_against_its_own_pin_whatever_is_selected() -> None:
    context = bell_context()
    context.eval("Lab.beginBellRinger(POOL, 60)")
    drain(context)
    # The student clicks another bone in the list while the station is open.
    context.eval("Lab.structure = STRUCTURES.humerus")

    context.eval('void Lab.answerStation("caput humeri")')
    state = bell(context)
    assert state["answers"][0] == {"given": "caput humeri", "correct": False, "saved": False, "timedOut": False, "skipped": False, "latin": "Acromion", "structure": "Scapula"}
    assert state["requests"][0]["structure_id"] == "scapula" and state["requests"][0]["landmark_id"] == "acromion"
    assert state["requests"][0]["correct"] is False


def test_a_station_still_loading_its_specimen_cannot_be_answered() -> None:
    context = bell_context()
    context.eval("Lab.structure = STRUCTURES.humerus; Lab.beginBellRinger(POOL, 60)")
    state = bell(context)
    assert state["loading"] == {"structure_id": "scapula", "landmark_id": "acromion"} and state["current"] == state["loading"]

    assert context.eval('Lab.answerStation("acromion")') is not None  # a promise of false
    drain(context)
    state = bell(context)
    assert state["answers"] == [] and state["requests"] == [] and state["loading"] is None

    context.eval('void Lab.answerStation("acromion")')
    assert len(bell(context)["answers"]) == 1 and bell(context)["answers"][0]["correct"] is True


# ---------------------------------------------------------------------------
# the connected study workflow: plan, understanding, histology, confidence
# ---------------------------------------------------------------------------


STUDY_STUBS = """
function icon() { return ""; }
function toast() {}
function el(tag, className) { return {className, style: {}, appendChild() {}, parentNode: null}; }
const State = {screen: "medical"};
"""

STUDY_MARKUP = """
(() => JSON.stringify({
  chips: Study.confidenceChips("unsure"),
  locked: Study.confidenceChips("sure", {locked: true}),
  explain: Study.explainBox({event_id: "ev1", reason: "open_finding"}),
  sent: Study.explainBox({event_id: "ev1", reason: "sample", sent: true, text: "Cunku K girer", note: "Dogru cevap, gerekce celiskili"}),
  support: Study.supportChip({status: "needs_review", label: "Inceleme gerekli", scored: false, reason: "Kaynak destegi henuz incelenmedi."}),
  scored: Study.supportChip({status: "source_supported", label: "Kaynak destekli", scored: true}),
  none: Study.supportChip(null),
  today_empty: Study.todayMarkup({plan: null, message: "Sinav plani yok."}),
  today: Study.todayMarkup({plan: {plan_id: "p1", name: "Komite 2", exam_date: "2026-09-15", days_left: 7}, date: "2026-09-08", budget: 45, planned_minutes: 40, done_minutes: 0,
    activities: [{activity_id: "a1", kind: "read", kind_label: "Materyali oku", title: "Aksiyon potansiyeli", reason: "Kapsamda, calisilmadi.", estimate_minutes: 20, estimate_label: "varsayilan tahmin", status: "planned", status_label: "Planlandi"}],
    next: {activity_id: "a1", kind: "read", kind_label: "Materyali oku", title: "Aksiyon potansiyeli", reason: "Kapsamda, calisilmadi.", estimate_minutes: 20, estimate_label: "varsayilan tahmin", status: "planned", status_label: "Planlandi"},
    message: "", fit: false, overload: {message: "Kapsam kalan sureye sigmiyor: 300 dk is var."}}, {compact: true}),
  coverage: Study.coverageMarkup({topics: [{topic_id: "t1", title: "Uyarilabilir dokular", state: "misconception", state_label: "Onarim bekleyen yanlis anlama", attempts: 4, correct: 1, findings: 1}], counts: {misconception: 1, unstudied: 0}, labels: {misconception: "Onarim bekleyen yanlis anlama", unstudied: "Kapsamda, calisilmadi"}}),
  specimen_hidden: Study.specimenMarkup({specimen_id: "hs1", label: "Cok katli yassi epitel", latin: "Epithelium stratificatum", basis: "user_confirmed", basis_label: "Ogrenci onayladi", status: "eligible", status_label: "Puanli sinava uygun", document_title: "Histoloji", page_number: 3, features: ["Bazal tabaka"], model_description: "Model boyle gordu"}, {reveal: false}),
  specimen_shown: Study.specimenMarkup({specimen_id: "hs1", label: "Cok katli yassi epitel", latin: "Epithelium stratificatum", basis: "user_confirmed", basis_label: "Ogrenci onayladi", status: "eligible", status_label: "Puanli sinava uygun", document_title: "Histoloji", page_number: 3, features: ["Bazal tabaka"], model_description: "Model boyle gordu"}, {reveal: true}),
  session_note: Study.sessionResultsMarkup({mode: "timed", items: [], results: {shown: 3, scored: 2, identified: 1, identification_accuracy: 0.5, explanation_quality: {specific: 1, generic: 1}, study_only: 1, repeated_specimens: 1, note: "Bazi ornekler daha once gorulmustu."}}),
  reasons: Study.reasoningPrompts({questions: [{question_id: "q1", stem: "Yanlis soru", answer: "A", correct: false}, {question_id: "q2", stem: "Dogru soru", answer: "B", correct: true}], analysis: {events: {q1: "ev1", q2: "ev2"}}}),
  finding: Study.findingMarkup({finding_id: "mc1", concept_name: "Aksiyon potansiyeli", statement: "Yukselen fazi K+ girisi saniyor.", status: "supported", status_label: "Desteklenen bulgu", subject_label: "Fizyoloji", priority: 3, evidence_count: 2,
    evidence: [{kind: "answer_confident", excerpt: "Cunku K girer", outcome: "against", valid: true}], sources: [{document_id: "d1", page_number: 3, title: "Fizyoloji"}],
    pending_diagnostic: {question: "Repolarizasyonda hangi iyon hareket eder?", expected_answer: "GIZLI CEVAP", rubric: "GIZLI RUBRIK"}, history: []}),
}))()
"""


def study_context():
    quickjs = pytest.importorskip("quickjs")
    context = quickjs.Context()
    context.eval(LAB_DOM_STUBS)
    context.eval(STUDY_STUBS)
    context.eval(JS_SOURCES["js/medical.js"])
    context.eval(JS_SOURCES["js/study.js"])
    return context


def test_study_markup_labels_estimates_hides_answers_and_speaks_turkish() -> None:
    result = json.loads(study_context().eval(STUDY_MARKUP))

    # Confidence: three Turkish levels, the chosen one lit, locked after the reveal.
    for label in ("Eminim", "Kararsızım", "Tahmin ettim"):
        assert label in result["chips"]
    assert 'class="chip active accent" data-confidence="unsure"' in result["chips"]
    assert result["locked"].count("disabled") == 3
    # The reasoning box says why it asks and never hides that it is optional.
    assert "açık bir bulgu" in result["explain"] and "data-explain-send" in result["explain"]
    assert "Cunku K girer" in result["sent"] and "gerekce celiskili" in result["sent"]
    # Source support: an unreviewed question is marked unscored; a supported one is not.
    assert "Inceleme gerekli" in result["support"] and "puansız" in result["support"]
    assert "puansız" not in result["scored"] and result["none"] == ""
    # Today: no plan is an honest empty state; a plan shows the estimate as an estimate and the overload as words.
    assert "Sinav plani yok." in result["today_empty"] and 'data-study-go="plan"' in result["today_empty"]
    for text in ("Komite 2", "7 gün kaldı", "≈ 20 dk", "varsayilan tahmin", "sigmiyor", 'data-activity-run="a1"'):
        assert text in result["today"], text
    # Coverage: states with a count are chips; empty states are not invented.
    assert "Onarim bekleyen yanlis anlama · 1" in result["coverage"] and "1/4" in result["coverage"]
    assert "Kapsamda, calisilmadi" not in result["coverage"]
    # A specimen in a session hides its answer, its Latin name, its features and the model's description.
    for secret in ("Cok katli yassi epitel", "Epithelium", "Bazal tabaka", "Model boyle gordu"):
        assert secret not in result["specimen_hidden"], secret
    assert 'data-crop="hs1"' in result["specimen_hidden"]
    assert "Cok katli yassi epitel" in result["specimen_shown"] and "Model betimlemesi (cevap değil)" in result["specimen_shown"]
    # Session results keep the repeated-specimen caveat.
    assert "%50" in result["session_note"] and "1/2" in result["session_note"] and "daha once gorulmustu" in result["session_note"]
    # The explanation grades are named in Turkish, never as the raw keys.
    assert "açıklama özgül · 1" in result["session_note"] and "açıklama genel · 1" in result["session_note"]
    assert "specific" not in result["session_note"]
    # Reasoning is asked only for the wrong answers that have an event.
    assert "Yanlis soru" in result["reasons"] and 'data-reason-send="ev1"' in result["reasons"]
    assert "Dogru soru" not in result["reasons"]
    # A finding's diagnostic question is shown; its expected answer and rubric never are.
    assert "Repolarizasyonda hangi iyon" in result["finding"]
    assert "GIZLI" not in result["finding"]
    assert "Onarımı başlat" in result["finding"] and "İtiraz et" in result["finding"]


def test_the_study_screens_are_declared_wired_and_confirmed() -> None:
    assert "js/study.js" in shell.WEB_ASSETS and "css/study.css" in shell.WEB_ASSETS
    for view in ("plan", "understanding", "histology"):
        assert f'data-view="{view}"' in HTML, view
    tabs = section(JS, "const MED_TABS = [", "];")
    for view in ("plan", "understanding", "histology"):
        assert f'["{view}",' in tabs, view
    medical_js = JS_SOURCES["js/medical.js"]
    study_js = JS_SOURCES["js/study.js"]
    # The answer carries the confidence and a submission id; the runner, the review and the bank can flag a question.
    assert "params.confidence" in medical_js and "params.submission_id" in medical_js
    assert 'data-run="report"' in medical_js
    assert medical_js.count("Soruda hata olabilir") >= 3
    # Destructive study actions send the confirmation flag only from their own dialog.
    for action in ("invalidate_question", "plan_delete", "histology_delete"):
        call = study_js[study_js.index(f'"{action}"'):]
        assert "confirmed: true" in call[:320], action
    assert study_js.count("confirmed: true") == 3
    # Study jobs return through the report push and are dispatched by action.
    assert 'String(payload.job) === "study"' in medical_js
    for action in ("understanding_assess", "diagnostic_ask", "repair_start", "question_review", "histology_answer"):
        assert f'action === "{action}"' in study_js, action
    # Dashboard cards and the library's region selector are declared.
    for element_id in ("med-today", "med-understanding-card", "med-plan-detail", "med-und-detail", "med-histo-detail", "med-histo-form"):
        assert f'id="{element_id}"' in HTML + JS, element_id
    # Who judged a thing is said in Turkish, and an unnamed model is not called "unknown".
    assert 'return "Kural"' in study_js and '"model:"' in study_js and '"unknown"' in study_js
    # A prerequisite the model or the student proposes is confirmed or rejected by the student, by name.
    for marker in ("data-edge-confirm", "data-edge-reject", '"concept_search"', '"prerequisite_suggest"', 'provenance: "student"'):
        assert marker in study_js, marker


def test_hidden_really_hides_every_panel_the_page_toggles() -> None:
    """A class that sets `display` outranks the `hidden` attribute.

    The narration panel was declared `hidden` and shown anyway, because
    `.med-narration { display: flex }` won: it took 142 px from every Medical
    screen, including the Anatomy Lab's stage. Any class on an element the page
    hides must either leave `display` alone or carry its own `[hidden]` rule.
    """
    classes: set[str] = set()
    # The attribute itself, never aria-hidden or a name that merely ends in it.
    for tag in re.findall(r"<[a-zA-Z][^>]*(?<![-\w])hidden(?=[\s>=])[^>]*>", HTML):
        for group in re.findall(r'class="([^"]+)"', tag):
            classes.update(group.split())
    assert "med-narration" in classes, "the sample this test was written for must still be in the page"

    unguarded = []
    for name in sorted(classes):
        sets_display = re.search(rf"^\.{re.escape(name)}\s*\{{[^}}]*\bdisplay\s*:", CSS, re.MULTILINE)
        guarded = re.search(rf"^\.{re.escape(name)}\[hidden\]\s*\{{[^}}]*display\s*:\s*none", CSS, re.MULTILINE)
        if sets_display and not guarded:
            unguarded.append(name)
    assert unguarded == [], f"these classes override the hidden attribute: {unguarded}"


def test_notification_cards_wrap_without_flex_shrinking_and_scene_picker_is_readable():
    head = section(CSS, ".notify-head {", "}")
    item = section(CSS, ".notify-item {", "}")
    listing = section(CSS, ".notify-list {", "}")
    assert "flex-wrap: wrap" in head and "flex: none" in head
    assert "flex: none" in item and "overflow-wrap: anywhere" in item
    assert "white-space: normal" in item and "min-width: 0" in item
    assert "overflow-y: auto" in listing and "min-height: 0" in listing
    picker = section(CSS, ".lab-scene-picker select {", "}")
    assert "background: var(--surface-2)" in picker
    assert "color: var(--ink-1)" in picker


# ---------------------------------------------------------------------------
# 13 September 2026: what the user test found on the page
# ---------------------------------------------------------------------------

MEDICAL_DOM_STUBS = """
const HOSTS = {};
function fakeNode(selector) {
  return HOSTS[selector] || (HOSTS[selector] = {innerHTML: "", textContent: "", hidden: false, value: "", disabled: false, dataset: {}, title: "",
    classList: {toggle() {}, contains() { return false; }, add() {}, remove() {}}, options: [], checked: false,
    addEventListener() {}, setAttribute() {}, focus() {}, querySelector() { return null; }, appendChild() {}});
}
$ = (selector) => fakeNode(selector);
function fmtRelative() { return "az önce"; }
function fmtDuration() { return "1 dk"; }
function tr(value) { return String(value || "").toUpperCase(); }
function medPercent(value) { return "%" + Math.round(value * 100); }
function bridgeReady() { return true; }
function showScreen() {}
const Motion = {allowed() { return false; }, rise() {}};
"""


def medical_context():
    quickjs = pytest.importorskip("quickjs")
    context = quickjs.Context()
    context.eval(LAB_DOM_STUBS)
    context.eval(STUDY_STUBS)
    context.eval(MEDICAL_DOM_STUBS)
    context.eval(JS_SOURCES["js/medical.js"])
    context.eval(JS_SOURCES["js/study.js"])
    context.eval("Medical.state = {session: {options: {subjects: [{value: 'histology', label: 'Histoloji'}]}}};")
    return context


def test_the_page_keeps_a_timed_specimen_hidden_everywhere() -> None:
    result = json.loads(study_context().eval("""
      (() => JSON.stringify({
        row: Study.specimenRow({specimen_id: "hs1", masked: true, status: "eligible", status_label: "Puanlı sınava uygun"}),
        shown: Study.specimenRow({specimen_id: "hs1", label: "Tek katlı kübik epitel", status: "study_only", status_label: "Yalnız çalışma", status_reason: "Cevap görselin üzerinde yazıyor", document_title: "Histoloji 3 - Epitel Doku", page_number: 34}),
        hidden: Study.specimenMarkup({specimen_id: "hs1", masked: true, status: "eligible", status_label: "Puanlı sınava uygun", document_title: "Histoloji 3 - Epitel Doku", page_number: 34}, {reveal: false}),
        revealed: Study.specimenMarkup({specimen_id: "hs1", label: "Tek katlı kübik epitel", basis: "page_caption", basis_label: "Sayfadaki başlık", status: "study_only", status_label: "Yalnız çalışma", status_reason: "Cevap görselin üzerinde yazıyor: kör sınava girmez.", answer_visible: true, masks: [], document_title: "Histoloji 3 - Epitel Doku", page_number: 34}, {reveal: true}),
        pending: Study.assessmentStateMarkup({assessment_status: "pending", classification: "correct_unsupported", event_id: "ev1"}),
        unavailable: Study.assessmentStateMarkup({assessment_status: "unavailable", assessment_note: "Model yanıt vermedi.", event_id: "ev1"}),
        done: Study.assessmentStateMarkup({assessment_status: "done", classification: "correct_supported", classification_label: "Doğru cevap, gerekçe destekliyor"}),
      }))()
    """))
    # The side list names neither the tissue nor the lecture while the session asks about it.
    assert "adı gizli" in result["row"] and "kübik" not in result["row"] and "Epitel" not in result["row"]
    assert "Cevap görselin üzerinde" in result["shown"], "a study-only specimen says why"
    # Hidden markup carries no title, no page number, no name; the caption is a placeholder.
    assert "Epitel Doku" not in result["hidden"] and "34" not in result["hidden"] and "kaynak gizli" in result["hidden"]
    assert 'data-crop-masked="1"' in result["hidden"] and 'data-crop-masked="0"' in result["revealed"]
    assert "Görseldeki adı gizle" in result["revealed"] and "kör sınava girmez" in result["revealed"]
    # A pending assessment is pending; a verdict is a verdict; a failure can be retried.
    assert "değerlendiriliyor" in result["pending"] and "tahmin" not in result["pending"]
    assert "data-check-retry" in result["unavailable"] and "Model yanıt vermedi" in result["unavailable"]
    assert "gerekçe destekliyor" in result["done"]


def test_the_exam_list_and_result_count_what_was_built_and_what_counted() -> None:
    context = medical_context()
    context.eval("""
      Medical.exams = [
        {exam_id: "e1", title: "Karbonhidrat metabolizması · 8 soru", status: "completed", status_label: "Tamamlandı", percent: 0, question_count: 8, requested_count: 10, scored_count: 8, created_at: "2026-09-13T10:00:00Z", config: {difficulty: 3}},
        {exam_id: "e2", title: "Deneme", status: "ready", status_label: "Hazır", percent: null, question_count: 3, requested_count: 3, scored_count: 2, created_at: "2026-09-13T10:00:00Z", config: {difficulty: 3}},
        {exam_id: "e3", title: "Çalışma", status: "completed", status_label: "Tamamlandı", percent: null, question_count: 2, requested_count: 2, scored_count: 0, created_at: "2026-09-13T10:00:00Z", config: {difficulty: 3}},
      ];
      Medical.renderExamList();
      Medical.exam = {exam_id: "e3", title: "Çalışma", attempt: {finished_at: "2026-09-13T10:05:00Z", attempt_id: "a1"}, config: {}, notes: [],
        questions: [{question_id: "g1", stem: "S1", answer: "A", correct: true, options: [], scoring: {scored: false, status: "not_applicable", label: "Kaynaksız (yalnız çalışma)", reason: "Kaynak pasaj yok."}}],
        analysis: {percent: null, score: null, total: 0, correct: 0, incorrect: 0, unanswered: 0, unscored: [{question_id: "g1", label: "Kaynaksız (yalnız çalışma)", reason: "Kaynak pasaj yok.", answered: true, correct: true}], unscored_answered: 1, unassessed_concepts: [{concept_id: "c", label: "Glikoliz", unanswered: 1}], weak_concepts: [], by_topic: [], by_subject: [], by_difficulty: []}};
      Medical.loadFigures = () => {};
      Medical.renderResult(fakeNode("#result"));
    """)
    listing = context.eval('HOSTS["#med-exam-list"].innerHTML')
    assert "8 soru · 10 istendi" in listing and "3 soru · 2 puanlı" in listing
    assert ">puansız<" in listing and "READY" not in listing and "Hazır" in listing, "no raw status, a completed paper with no scored question is not %0"
    result = context.eval('HOSTS["#result"].innerHTML')
    assert "Değerlendirme dışı" in result and "puanlı soru yok" in result and "%0" not in result
    assert "Kaynaksız (yalnız çalışma)" in result and "doğru cevapladın" in result
    assert "Cevaplanmadı" in result and "Glikoliz · 1 boş" in result and "med-unscored" in result


def test_the_bank_pages_the_professor_folds_and_sources_are_buttons() -> None:
    context = medical_context()
    context.eval("""
      Medical.bank = {matched: 716, counts: {total: 717}, questions: [
        {question_id: "q1", subject_label: "Biyokimya", origin: "generated", difficulty: 3, has_answer_key: true, options: [{key: "A", text: "x"}], correct_key: "A", stem: "Soru", references: [{document_id: "d1", page_number: 34, title: "Biyokimya 7"}], support: {status: "not_applicable", label: "Kaynaksız (yalnız çalışma)", scored: false}, scoring: {scored: false, status: "not_applicable", label: "Kaynaksız (yalnız çalışma)", reason: "Kaynak pasaj yok."}, problems: [], flags: 0, invalidated: false},
      ]};
      Medical.renderBank();
      Medical.notes = [{note_id: "n1", title: "Not", content: "x", references: [{document_id: "d1", page_number: 34, title: "Histoloji 3 - Epitel Doku"}], created_at: "2026-09-13T10:00:00Z"}];
      Medical.renderNotes();
      Medical.professor = {profile_id: "p1", name: "Burcu Baba", sample_size: 59, features: [], answer_distribution: {}, documents: [], average_options: 5, average_stem_words: 20,
        questions: Array.from({length: 59}, (_, index) => ({question_id: "pq" + index, origin: "imported_exam", has_answer_key: true, correct_key: "A", stem: "Soru " + index, options: []}))};
      Medical.renderProfessor();
    """)
    bank = context.eval('HOSTS["#med-bank-list"].innerHTML')
    assert "data-bank-more" in bank and "715 soru daha" in bank and 'data-source="d1|34"' in bank and "med-unscored" in bank
    assert "1 / 716 gösteriliyor · toplam 717" in context.eval('HOSTS["#med-bank-count"].textContent')
    notes = context.eval('HOSTS["#med-note-list"].innerHTML')
    assert '<button type="button" class="chip" data-source="d1|34"' in notes and "s. 34" in notes
    professor = context.eval('HOSTS["#med-prof-detail"].innerHTML')
    assert professor.count("mb-stem") == 30 and "30 soru daha göster" in professor and "29 soru daha" in professor
    context.eval("Medical.profShown = 60; Medical.renderProfessor();")
    assert context.eval('HOSTS["#med-prof-detail"].innerHTML').count("mb-stem") == 59


def test_escape_leaves_real_fullscreen_before_anything_else_and_the_lab_notice_folds() -> None:
    shell = JS_SOURCES["js/shell.js"]
    home = shell.index('if (event.key === "Escape" && !activeApproval && !confirmOpen)')
    fullscreen = shell.index('if (event.key === "Escape" && document.fullscreenElement)')
    assert fullscreen < home, "the fullscreen exit is checked before the home-screen escape"
    assert "exitFullscreen" in shell[fullscreen:home]
    medical = JS_SOURCES["js/medical.js"]
    assert 'stage.classList.contains("expanded") || document.fullscreenElement === stage' in medical
    context = medical_context()
    context.eval("""
      Lab.setNotice("3B model · CC BY-SA 4.0", "Z-Anatomy — CC BY-SA 4.0; https://example.org/atlas");
    """)
    notice = context.eval('HOSTS["#lab-notice"].innerHTML')
    assert notice.startswith("3B model · CC BY-SA 4.0") and "ln-detail" in notice and "example.org" in notice
    assert "white-space: nowrap" in re.search(r"\.lab-notice \{[^}]*\}", CSS).group(0), "one line until opened"
    assert ".lab-notice.open" in CSS
    quiz = json.loads(context.eval("""
      (() => {
        Lab.structure = {structure_id: "scapula", landmarks: []};
        Lab.draw = () => {};
        Lab.quiz = {questions: [{stem: "Scapula üzerindeki şu yapının Latince adı nedir: Spina scapulae?", landmark_id: "spina", pinned: false, highlight: null, options: [{key: "A", text: "Spina scapulae"}], correct_key: "A"}], index: 0, correct: 0};
        Lab.renderQuiz();
        return JSON.stringify({info: HOSTS["#lab-info"].innerHTML, highlight: Lab.highlight});
      })()
    """))
    assert "işaret yok" in quiz["info"] and quiz["highlight"] == []


def test_the_library_panel_keeps_room_for_the_document_list() -> None:
    assert ".med-doc-panel .med-set-list { flex: 0 1 auto; max-height: 32vh; overflow-y: auto; min-height: 0; }" in CSS
    assert ".med-doc-panel .med-list { flex: 1 1 9rem; min-height: 9rem; }" in CSS
    assert 'id="med-set-toggle"' in HTML and ".med-doc-panel .med-set-list.collapsed { display: none; }" in CSS
    # The date and time inputs and the multi-select follow the theme.
    assert 'input[type="date"], .med-form input[type="time"]' in CSS and "color-scheme: dark;" in CSS and "body.light .med-form select[multiple] { color-scheme: light; }" in CSS
    assert "Ctrl ile birden çok seç" in HTML
    assert 'id="med-exam-unscored"' in HTML and 'id="med-exam-jobs"' in HTML and 'id="med-note-jobs"' in HTML and 'id="med-exam-context"' in HTML


def test_the_page_handles_job_state_pushes_and_duplicate_starts() -> None:
    context = medical_context()
    context.eval("""
      const toasts = [];
      toast = (text, tone) => toasts.push([String(text), tone]);
      Medical.refreshCounts = async () => {};
      Medical.onPush({kind: "job_state", job: {job_id: "j1", kind: "create_exam", kind_label: "Sınav hazırlama", title: "Histoloji", status: "running", status_label: "Hazırlanıyor", started_at: "2026-09-13T10:00:00Z", request: {topic_ids: ["histology"]}}});
      const running = HOSTS["#med-exam-jobs"].innerHTML;
      Medical.onPush({kind: "job_state", job: {job_id: "j1", kind: "create_exam", kind_label: "Sınav hazırlama", title: "Histoloji", status: "timeout", status_label: "Zaman aşımı", error: "300 saniye içinde tamamlanmadı; sağlayıcı yanıt vermedi.", started_at: "2026-09-13T10:00:00Z", request: {topic_ids: ["histology"]}}});
      globalThis.REPORT = JSON.stringify({running, failed: HOSTS["#med-exam-jobs"].innerHTML, toasts});
    """)
    report = json.loads(context.eval("REPORT"))
    assert "Hazırlanıyor" in report["running"] and "data-job-retry" not in report["running"]
    assert "Zaman aşımı" in report["failed"] and "data-job-retry" in report["failed"] and "sağlayıcı yanıt vermedi" in report["failed"]
    # The failure is toasted once, by the bridge's job_failed push; the state push only keeps the row on screen.
    assert report["toasts"] == []
    assert "job_state" in JS_SOURCES["js/medical.js"], "the push kind the ledger emits is handled"


def test_the_cards_tab_reveals_before_grading_and_speaks_turkish() -> None:
    assert 'data-view="cards"' in HTML and 'id="med-cards-review"' in HTML and "data-occlusion-scan" in HTML.replace("&quot;", '"') or "data-occlusion-scan" in JS_SOURCES["js/medical.js"]
    context = medical_context()
    context.eval("""
      Cards.overview = {total: 3, due: 1, new_available: 2, new_budget: 2, reviewed_today: 0, suspended: 0, by_source: [{source: "anatomy_fact", label: "Ders kartı (anatomi)", count: 3}], forecast: [{date: "2026-09-15", due: 1}], settings: {new_per_day: 15}};
      Cards.queue = [{card_id: "fc1", state: "new", state_label: "Yeni", source: "anatomy_fact", source_label: "Ders kartı (anatomi)", front: "Musculus biceps brachii — innervasyonu?", back: "Nervus musculocutaneus (C5–C6)", provenance: "Ders kartı: Musculus biceps brachii · innervation", topic_label: "Anatomi › Kol", reps: 0, has_image: false, previews: {again: "bugün", hard: "bugün", good: "1 gün", easy: "3 gün"}, preview_labels: {again: "Tekrar", hard: "Zor", good: "İyi", easy: "Kolay"}}];
      Cards.index = 0; Cards.revealed = false;
      Cards.renderPanel(); Cards.renderReview();
      const hidden = HOSTS["#med-cards-review"].innerHTML;
      Cards.revealed = true; Cards.renderReview();
      globalThis.CARDS_REPORT = JSON.stringify({hidden, shown: HOSTS["#med-cards-review"].innerHTML, panel: HOSTS["#med-cards-summary"].innerHTML, forecast: HOSTS["#med-cards-forecast"].innerHTML});
    """)
    report = json.loads(context.eval("CARDS_REPORT"))
    # Before the reveal: the question, no answer, no grade buttons.
    assert "innervasyonu" in report["hidden"] and "musculocutaneus" not in report["hidden"].lower()
    assert "Cevabı göster" in report["hidden"] and "data-grade" not in report["hidden"]
    # After: the answer, four Turkish grades with their schedule previews, the source.
    assert "Nervus musculocutaneus" in report["shown"]
    for label in ("Tekrar", "Zor", "İyi", "Kolay"):
        assert label in report["shown"]
    assert "1 gün" in report["shown"] and "3 gün" in report["shown"]
    assert "Ders kartı: Musculus biceps brachii" in report["shown"] and "ölçmeye girmez" in report["shown"]
    assert "1 tekrar" in report["panel"] and "2 yeni" in report["panel"]
    assert "Bugün" in report["forecast"]
    # The keyboard grades only after the reveal.
    context.eval('Medical.view = "cards"; Cards.revealed = false; globalThis.GRADED = null; Cards.answer = (g) => { GRADED = g; };')
    assert context.eval('Cards.keydown({key: "3", target: {tagName: "DIV"}})') is False
    context.eval("Cards.revealed = true;")
    assert context.eval('Cards.keydown({key: "3", target: {tagName: "DIV"}})') is True
    assert context.eval("GRADED") == "good"


# ---------------------------------------------------------------------------
# general JARVIS on the page: the brief builder and the focus timer
# ---------------------------------------------------------------------------


BRIEF_STUBS = """
function esc(value) { return String(value == null ? "" : value).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;"); }
function emptyState(title, hint) { return `<div class="empty-state">${title}|${hint}</div>`; }
"""


def brief_context():
    quickjs = pytest.importorskip("quickjs")
    context = quickjs.Context()
    context.eval(BRIEF_STUBS)
    context.eval(section(JS_SOURCES["js/panels.js"], "function homeBriefMarkup", "\nlet briefFetchedAt"))
    context.eval(section(JS_SOURCES["js/shell.js"], "const Focus = {", "\nfunction bindKeyboard"))
    return context


def test_home_brief_markup_shows_what_exists_and_invents_nothing() -> None:
    context = brief_context()
    run = lambda payload: context.eval("homeBriefMarkup(" + json.dumps(payload) + ")")

    assert "Özet okunamadı" in run({"ok": False})

    full = run({
        "ok": True, "date": "15 Eylül 2026, Pazartesi",
        "reminders": [{"text": "Anatomi <b>tekrarı</b>", "due_local": "09.00"}], "reminders_available": True,
        "routines": [{"name": "Sabah özeti", "schedule": "her gün", "next_run_local": "yarın 08:30"}], "routines_available": True,
        "tasks_open": 2, "notifications_unread": 1,
        "medical": {"available": True, "next_activity": {"title": "Düzlemler", "kind_label": "Materyali oku"}, "plan_message": "",
                     "cards_waiting": 5, "findings_open": 2, "countdown": {"name": "Komite 2", "days_left": 3}},
    })
    for expected in ("15 Eylül 2026", "Anatomi &lt;b&gt;tekrarı&lt;/b&gt;", "09.00", "Sabah özeti", "yarın 08:30",
                     "2 açık görev", "1 okunmamış bildirim", "Komite 2", "3 gün", "Sırada: Düzlemler",
                     "5 kart tekrar bekliyor", "2 açık bulgu"):
        assert expected in full, expected
    assert "<b>" not in full, "reminder text is escaped, never injected"
    assert 'class="hb-row warn"' in full, "a committee 3 days away is marked urgent"
    assert 'data-brief-go="tasks"' in full and 'data-brief-medical="cards"' in full

    # Nothing anywhere: an honest empty state, no invented rows.
    empty = run({"ok": True, "date": "", "reminders": [], "reminders_available": False, "routines": [],
                 "routines_available": False, "tasks_open": 0, "notifications_unread": 0, "medical": {"available": False}})
    assert "Bugün için bekleyen bir şey yok" in empty

    # Reminders exist as a service but none are due: said in words, not hidden.
    quiet = run({"ok": True, "date": "x", "reminders": [], "reminders_available": True, "routines": [],
                 "routines_available": True, "tasks_open": 0, "notifications_unread": 0, "medical": {"available": False}})
    assert "Bugün için hatırlatıcı yok" in quiet


def test_the_focus_clock_formats_time_exactly() -> None:
    context = brief_context()
    values = json.loads(context.eval(
        "JSON.stringify([Focus.format(0), Focus.format(-500), Focus.format(1500000), Focus.format(61000), Focus.format(59400), Focus.format(3600000)])"
    ))
    assert values == ["0:00", "0:00", "25:00", "1:01", "1:00", "60:00"]


def test_conversation_search_markup_marks_matches_and_stays_honest() -> None:
    quickjs = pytest.importorskip("quickjs")
    context = quickjs.Context()
    context.eval(
        "function esc(v) { return String(v == null ? \"\" : v).replace(/&/g, \"&amp;\").replace(/</g, \"&lt;\").replace(/>/g, \"&gt;\"); }"
        + "function fmtRelative(v) { return \"az önce\"; }"
    )
    context.eval(section(JS_SOURCES["js/conversation.js"], "function convSearchMarkup", "\nlet convSearchTimer"))

    run = lambda payload: context.eval("convSearchMarkup(" + json.dumps(payload) + ")")

    empty = run({"ok": True, "query": "pankreas", "results": []})
    assert "hiçbir konuşmada geçmiyor" in empty and "pankreas" in empty

    error = run({"ok": False, "error": "Arama için en az 2 karakter yaz."})
    assert "en az 2 karakter" in error

    rows = run({"ok": True, "query": "böbrek", "results": [{
        "conversation_id": "c1", "title": "Böbrek anatomisi", "status": "active", "matches": 2,
        "excerpt": "Böbrek retroperitoneal <b>organdır</b>", "excerpt_role": "user",
        "turn_count": 4, "updated_at": "2026-09-15T07:00:00+03:00", "active": False,
    }]})
    assert "<mark>Böbrek</mark>" in rows, "the match is highlighted case-insensitively in Turkish"
    assert "Sen: " in rows and "2 eşleşme" in rows
    assert "<b>" not in rows, "excerpt HTML is escaped, never injected"


def test_answer_source_chips_show_hosts_and_escape_everything() -> None:
    quickjs = pytest.importorskip("quickjs")
    context = quickjs.Context()
    context.eval("function esc(v) { return String(v == null ? SQ : v).replace(/&/g, \"&amp;\").replace(/</g, \"&lt;\").replace(/>/g, \"&gt;\").replace(/\"/g, \"&quot;\"); }".replace("SQ", "\"\""))
    context.eval(section(JS_SOURCES["js/conversation.js"], "function researchSourcesMarkup", "\nfunction bindResearchChips"))

    run = lambda payload: context.eval("researchSourcesMarkup(" + json.dumps(payload) + ")")

    assert run(None) == "" and run({"sources": []}) == "", "no sources, no block"

    html = run({"query": "tus 2026 <script>", "sources": [
        {"title": "ÖSYM \"Takvimi\"", "url": "https://www.osym.gov.tr/takvim?x=1"},
        {"title": "", "url": "not a url"},
    ]})
    assert ">osym.gov.tr</button>" in html, "the chip text is the bare host; the full URL rides the tooltip"
    assert ">kaynak<" in html, "an unparseable url still gets an honest generic chip"
    assert "<script>" not in html and "&quot;Takvimi&quot;" in html
    assert "Web kaynakları" in html


def test_assistant_markdown_renders_the_safe_subset_and_nothing_else() -> None:
    quickjs = pytest.importorskip("quickjs")
    context = quickjs.Context()
    context.eval("function esc(v) { return String(v == null ? \"\" : v).replace(/&/g, \"&amp;\").replace(/</g, \"&lt;\").replace(/>/g, \"&gt;\").replace(/\"/g, \"&quot;\"); }")
    context.eval(section(JS_SOURCES["js/conversation.js"], "function renderMarkdownLite", "\nfunction appendMessage"))

    run = lambda text: context.eval("renderMarkdownLite(" + json.dumps(text) + ")")

    NL = chr(10)
    # Bold, italics, inline code and headings render; the asterisks disappear.
    rich = run("### Sonuc" + NL + "Sinav **15 Mart 2026** tarihinde, yani *bahar donemi* icinde. Kod: `verify.py`")
    assert '<div class="md-h">Sonuc</div>' in rich
    assert "<strong>15 Mart 2026</strong>" in rich and "**" not in rich
    assert "<em>bahar donemi</em>" in rich
    assert "<code>verify.py</code>" in rich

    # Lists: bullets and numbers, closed properly, mixed with paragraphs.
    listed = run("Plan:" + NL + "- birinci" + NL + "- ikinci" + NL + NL + "1. adim" + NL + "2. adim")
    assert listed.count("<li>") == 4 and "<ul>" in listed and "<ol>" in listed
    assert listed.index("</ul>") < listed.index("<ol>"), "the bullet list closes before the numbered one opens"

    # Tables: header + rule + rows become a real table; cells keep inline
    # markdown; a stray pipe line without a rule stays plain text.
    table = run("| İlaç | Doz |" + NL + "|---|:---:|" + NL + "| **Aspirin** | 100 mg |" + NL + "| Parol | 500 mg |" + NL + "Bitti")
    assert '<table class="md-table">' in table and table.count("<tr>") == 3
    assert "<th>İlaç</th>" in table and "<td><strong>Aspirin</strong></td>" in table
    assert "---" not in table, "the rule row is consumed, not printed"
    assert "<div>Bitti</div>" in table
    stray = run("a | b | c" + NL + "| tek satır |")
    assert "<table" not in stray, "no rule, no table"
    ragged = run("| A | B |" + NL + "|---|---|" + NL + "| yalnız |")
    assert ragged.count("<td>") == 2 and "<td>yalnız</td>" in ragged and "<td></td>" in ragged, (
        "a short row pads to the header, never crashes"
    )
    hostile_cell = run("| A |" + NL + "|---|" + NL + "| <script>x</script> |")
    assert "<script" not in hostile_cell and "&lt;script&gt;" in hostile_cell

    # Fenced code: literal, inline markdown left alone, copy in the corner.
    fenced = run("Açıklama:" + NL + "```python" + NL + "x = a * b  # **not bold**" + NL + "print(x)" + NL + "```" + NL + "Bitti **tamam**")
    assert '<pre class="md-code">' in fenced and "data-code-copy" in fenced
    assert "x = a * b  # **not bold**" in fenced, "inline markdown never runs inside a fence"
    assert fenced.count("<pre") == 1 and "<strong>tamam</strong>" in fenced
    cut = run("```" + NL + "yarım kalan satır")
    assert '<pre class="md-code">' in cut and "yarım kalan satır" in cut, "a stream cut mid-block still renders"
    hostile_fence = run("```" + NL + "<script>alert(1)</script>" + NL + "```")
    assert "<script" not in hostile_fence and "&lt;script&gt;" in hostile_fence

    # Injection: model or web text can never smuggle HTML through.
    hostile = run('<img src=x onerror=alert(1)> ve **<script>alert(2)</script>**')
    assert "<img" not in hostile and "<script" not in hostile
    assert "&lt;img" in hostile and "<strong>&lt;script&gt;alert(2)&lt;/script&gt;</strong>" in hostile

    # Multiplication stays multiplication: no stray emphasis from 3*4 or a*b.
    math = run("3*4 = 12 ve a*b carpimi")
    assert "<em>" not in math

    # User bubbles never go through this path at all.
    conversation = JS_SOURCES["js/conversation.js"]
    assert 'if (message.role === "assistant") node.querySelector(".msg-body").innerHTML = renderMarkdownLite(message.text);' in conversation
    assert 'else node.querySelector(".msg-body").textContent = message.text;' in conversation


def test_the_exam_chip_speaks_turkish_and_hides_without_a_plan() -> None:
    quickjs = pytest.importorskip("quickjs")
    context = quickjs.Context()
    context.eval(section(JS_SOURCES["js/panels.js"], "function examChipText", "\nfunction renderExamChip"))

    run = lambda payload: context.eval("JSON.stringify(examChipText(" + json.dumps(payload) + "))")

    assert run(None) == "null" and run({}) == "null"
    assert run({"name": "Komite 2", "days_left": -1}) == "null", "a past exam shows nothing"
    assert json.loads(run({"name": "Komite 2", "days_left": 9})) == {"text": "🎓 Komite 2 · 9 gün", "warn": False}
    assert json.loads(run({"name": "Komite 2", "days_left": 5})) == {"text": "🎓 Komite 2 · 5 gün", "warn": True}
    assert json.loads(run({"name": "Komite 2", "days_left": 0})) == {"text": "🎓 Komite 2 · bugün", "warn": True}


def test_drawer_groups_follow_local_calendar_days() -> None:
    quickjs = pytest.importorskip("quickjs")
    context = quickjs.Context()
    context.eval(section(JS_SOURCES["js/conversation.js"], "function convGroupLabel", "\n\nfunction renderConversations"))

    run = lambda iso, now: context.eval(
        "convGroupLabel(" + json.dumps(iso) + ", new Date(" + json.dumps(now) + ").getTime())"
    )

    now = "2026-09-15T10:00:00"
    assert run("2026-09-15T00:05:00", now) == "Bugün"
    assert run("2026-09-14T23:59:00", now) == "Dün", "just before midnight is still yesterday"
    assert run("2026-09-12T09:00:00", now) == "Bu hafta"
    assert run("2026-09-01T09:00:00", now) == "Bu ay"
    assert run("2026-07-01T09:00:00", now) == "Daha eski"
    assert run("2026-09-16T09:00:00", now) == "Bugün", "a clock skew never invents a group"


# ---------------------------------------------------------------------------
# phone voice helpers in the shim
# ---------------------------------------------------------------------------


def test_the_phone_wav_encoder_downsamples_into_a_valid_mono_pcm16_file() -> None:
    quickjs = pytest.importorskip("quickjs")
    from app.mobile.server import NOVA_SHIM

    source = NOVA_SHIM.read_text(encoding="utf-8")
    context = quickjs.Context()
    context.eval(section(source, "function phoneVoiceRms", "\n/* ---- the shim"))

    assert context.eval("phoneVoiceRms(new Float32Array([0.5, -0.5, 0.5, -0.5]))") == pytest.approx(0.5)
    assert context.eval("phoneVoiceRms(new Float32Array(0))") == 0

    report = json.loads(context.eval("""
      (() => {
        const rate = 48000;
        const chunk = new Float32Array(rate);
        for (let i = 0; i < rate; i += 1) chunk[i] = Math.sin(i / 20) * 0.5;
        const buffer = phoneVoiceEncodeWav([chunk], rate, 16000);
        const view = new DataView(buffer);
        const text = (start, length) => Array.from(new Uint8Array(buffer, start, length)).map((b) => String.fromCharCode(b)).join("");
        const samples = new Int16Array(buffer, 44);
        let peak = 0;
        for (let i = 0; i < samples.length; i += 1) peak = Math.max(peak, Math.abs(samples[i]));
        return JSON.stringify({
          riff: text(0, 4), wave: text(8, 4), fmt: text(12, 4), data: text(36, 4),
          format: view.getUint16(20, true), channels: view.getUint16(22, true), rate: view.getUint32(24, true),
          byteRate: view.getUint32(28, true), bits: view.getUint16(34, true), dataSize: view.getUint32(40, true),
          total: buffer.byteLength, peak,
        });
      })()
    """))
    assert (report["riff"], report["wave"], report["fmt"], report["data"]) == ("RIFF", "WAVE", "fmt ", "data")
    assert report["format"] == 1 and report["channels"] == 1 and report["bits"] == 16
    assert report["rate"] == 16000 and report["byteRate"] == 32000
    assert report["dataSize"] == 16000 * 2 and report["total"] == 44 + 16000 * 2, "one second at 48 kHz becomes one second at 16 kHz"
    assert 0x3000 < report["peak"] <= 0x4000, "a half-scale sine stays half scale after the box filter"


def test_engine_failures_are_shown_in_turkish_and_unknown_ones_verbatim() -> None:
    quickjs = pytest.importorskip("quickjs")
    context = quickjs.Context()
    context.eval(section(JS_SOURCES["js/panels.js"], "const TASK_ERROR_TR", "\nfunction renderTasks"))

    assert context.eval('taskErrorTr("Execution time budget exhausted.")') == "Süre bütçesi doldu; adım yarıda kesildi."
    assert context.eval('taskErrorTr("Invalid tool_name.")') == "Adımın aracı tanımsız."
    assert context.eval('taskErrorTr("User confirmation required.")') == "Bu adım için onayın gerekiyor."
    assert context.eval('taskErrorTr("")') == "" and context.eval("taskErrorTr(null)") == ""
    unknown = "Some future engine string."
    assert context.eval('taskErrorTr("' + unknown + '")') == unknown, "an unmapped failure is shown as it came, never invented"
    panels = JS_SOURCES["js/panels.js"]
    assert "esc(taskErrorTr(task.error))" in panels and "esc(taskErrorTr(step.error))" in panels


def test_the_phone_task_card_shows_the_goal_not_the_uuid() -> None:
    from app.mobile.server import WEB_ROOT
    from app.tasks.manager import TaskManager
    from app.tasks.service import TaskControlService

    source = (WEB_ROOT / "app.js").read_text(encoding="utf-8")
    assert "esc(task.goal || task.title" in source, "the client reads the key the server actually sends"

    row = TaskControlService(TaskManager(), None)._serialize(TaskManager().create("PDF'leri dönüştür"))
    assert row["goal"] and "title" not in row, "the server contract is goal, and the client follows it"


# ---------------------------------------------------------------------------
# the busy bracket on a surface that did not start the turn
# ---------------------------------------------------------------------------


def lift_push_handler(name: str) -> str:
    """One PUSH handler, exactly as bridge.js writes it."""
    source = JS_SOURCES["js/bridge.js"]
    start = source.index(f"  {name}({{")
    return source[start : source.index("\n  },", start) + len("\n  },")]


def lift_function(file: str, name: str) -> str:
    source = JS_SOURCES[file]
    start = source.index(f"function {name}(")
    return source[start : source.index("\n}", start) + 2]


# The smallest stubs that keep the handler's control flow honest: setBusy
# writes State.busy as shell.js does, ensurePendingBubble hands back the one
# open bubble as conversation.js does, and appendMessage/updateChat record
# what the reader would have seen.
WATCHED_TURN_STUBS = """
var State = { busy: false, messages: [], pendingSources: null, pendingEl: null,
              watchedTurn: null, status: "" };
var chat = [];
var forced = [];
var removed = [];
function hideChatEmpty() {}
function $(_selector) { return {}; }
function appendMessage(_host, message) { chat.push(message.text); return {}; }
function updateChat(mutate, options) { forced.push(!!(options && options.force)); mutate(); }
var Activity = {
  current: null,
  beginTurn(goal) { this.current = { goal: goal, status: "thinking", error: null }; return this.current; },
  abortTurn(error) {
    if (!this.current) return;
    this.current.status = "failed";
    this.current.error = error;
    this.current = null;
  },
};
function ensurePendingBubble() {
  if (State.pendingEl) return State.pendingEl;
  State.pendingEl = { id: chat.length, remove() { removed.push(this.id); } };
  return State.pendingEl;
}
function showThinking() { ensurePendingBubble(); }
function setBusy(busy, status) { State.busy = busy; if (status) State.status = status; }
function renderHomeSession() {}
"""


def watching_surface():
    quickjs = pytest.importorskip("quickjs")
    context = quickjs.Context()
    context.eval(WATCHED_TURN_STUBS)
    context.eval(lift_function("js/conversation.js", "closeWatchedTurn"))
    context.eval("var PUSH = {\n" + lift_push_handler("busy") + "\n};")
    return context


def test_the_watching_page_closes_the_turn_a_busy_push_opened() -> None:
    """The phone typed and this page drew the question and the thinking mark.
    Nothing else here ever closes that turn - sendCommand's cleanup belongs
    to the page that sent the message - so a submission with no answer (the
    runner refused it, the turn was cancelled) has to be closed by the
    busy:false that ends the bracket, or the orphan bubble is handed to the
    next question and the answer lands above it."""
    context = watching_surface()
    context.eval(
        'PUSH.busy({ busy: true, status: "PROCESSING", text: "telefondan selam", spoken: false });'
    )
    assert json.loads(context.eval("JSON.stringify(chat)")) == ["telefondan selam"]
    assert context.eval("State.pendingEl !== null")
    assert context.eval("Activity.current !== null")

    context.eval('PUSH.busy({ busy: false, status: "LOCAL CORE READY" });')
    assert context.eval("State.pendingEl === null"), "the thinking bubble outlived the turn"
    assert context.eval("Activity.current === null"), "the activity turn was left open"
    assert context.eval("State.watchedTurn === null")
    assert json.loads(context.eval("JSON.stringify(removed)")) == [1]
    assert context.eval("State.busy") is False

    # The next question gets a bubble of its own, not the orphan.
    context.eval(
        'PUSH.busy({ busy: true, status: "PROCESSING", text: "ikinci soru", spoken: false });'
    )
    assert json.loads(context.eval("JSON.stringify(chat)")) == ["telefondan selam", "ikinci soru"]
    assert context.eval("State.pendingEl.id") == 2


def test_the_closing_push_undoes_only_what_it_opened() -> None:
    """busy:false ends every turn, answered or not. An answered bubble was
    already finalized by PUSH.reply, and a background turn belongs to the
    tool activity that raised it; neither is this push's to take down."""
    context = watching_surface()
    context.eval(
        'PUSH.busy({ busy: true, status: "PROCESSING", text: "telefondan selam", spoken: false });'
    )
    # PUSH.reply -> finalizePendingBubble keeps the node and clears the slot;
    # Activity.onReply finishes the turn. Then a tool event opens its own.
    context.eval("State.pendingEl = null; Activity.current = null;")
    context.eval("Activity.current = { goal: 'arka plan', status: 'thinking', error: null };")

    context.eval('PUSH.busy({ busy: false, status: "LOCAL CORE READY" });')
    assert json.loads(context.eval("JSON.stringify(removed)")) == [], "an answered bubble was removed"
    assert context.eval("Activity.current !== null"), "a background turn was aborted"
    assert context.eval("State.watchedTurn === null")


def test_a_turn_from_another_device_does_not_yank_the_reader_down() -> None:
    """conversation.js carries the rule: only a reader already at the bottom
    is scrolled, and anyone reading older messages gets the pill instead.
    sendCommand forces the scroll because the reader just typed; on the
    watching surface nobody did."""
    context = watching_surface()
    context.eval(
        'PUSH.busy({ busy: true, status: "PROCESSING", text: "telefondan selam", spoken: false });'
    )
    assert json.loads(context.eval("JSON.stringify(forced)")) == [False]
    assert "force" not in lift_push_handler("busy")
    sent = section(JS_SOURCES["js/conversation.js"], "async function sendCommand(", "\n}")
    assert "{ force: true }" in sent, "the page that typed still scrolls itself down"


def test_the_demo_bridge_raises_the_same_busy_bracket_as_python() -> None:
    """?demo=1 exists to exercise this page, so a demo turn has to open the
    bracket the real bridge opens and not only close it."""
    demo = section(JS, "  async submit_command(text) {", "\n  },")
    assert 'busy: true, status: "PROCESSING", text, spoken: false' in demo
    assert demo.index("busy: true") < demo.index("busy: false")
    python = inspect.getsource(shell.NovaBridge.submit_command)
    assert '"status": WORKING_STATUS' in python and '"spoken": spoken is True' in python


# ---------------------------------------------------------------------------
# Tıp Akademisi: a bright room of its own, with an opening
# ---------------------------------------------------------------------------


def test_the_academy_room_is_declared_and_wired() -> None:
    assert "css/academy.css" in shell.WEB_ASSETS and "js/academy.js" in shell.WEB_ASSETS
    for element_id in ("academy-intro", "academy-ecg-path", "academy-heart", "academy-intro-line",
                       "med-back", "med-sound", "med-greeting", "med-tabs"):
        assert f'id="{element_id}"' in HTML, element_id
    assert '<aside class="med-side"' in HTML and '<div class="med-main">' in HTML
    assert "Geçmek için tıkla" in HTML
    # Entering the screen is what opens the room; leaving any other way closes it.
    show = section(JS_SOURCES["js/shell.js"], "function showScreen(", "function setStatus(")
    assert 'if (id === "medical") Academy.enter(); else Academy.leave();' in show
    assert JS_SOURCES["js/main.js"].count("bindAcademy();") == 1, "bound once: a second binding makes every toggle undo itself"
    # The opening never reports a figure the topbar chip would not: both read one field.
    assert "State.examCountdown = countdown || null;" in JS_SOURCES["js/panels.js"]
    assert "academyIntroLine(State.examCountdown)" in JS_SOURCES["js/academy.js"]


def test_the_academy_palette_is_daylight_and_lives_in_tokens() -> None:
    tokens = (WEB / "css/tokens.css").read_text(encoding="utf-8")
    block = section(tokens, "body.academy, body.academy.light {", "\n}")
    academy_css = (WEB / "css/academy.css").read_text(encoding="utf-8")

    def luminance(hex_colour: str) -> float:
        """WCAG relative luminance, so the pin below is the contrast ratio."""
        value = hex_colour.lstrip("#")
        channels = []
        for i in (0, 2, 4):
            c = int(value[i:i + 2], 16) / 255
            channels.append(c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4)
        r, g, b = channels
        return 0.2126 * r + 0.7152 * g + 0.0722 * b

    def contrast(a: str, b: str) -> float:
        light, dark = sorted((luminance(a), luminance(b)), reverse=True)
        return (light + 0.05) / (dark + 0.05)

    colour = lambda name: re.search(name + r":\s+(#[0-9a-f]{6});", block).group(1)
    assert luminance(colour("--bg")) > 0.85, "the academy ground is bright"
    # Body ink and the secondary ink both clear WCAG AAA on the ground; the
    # tertiary ink, used for asides, still clears AA.
    assert contrast(colour("--bg"), colour("--ink-1")) >= 7
    assert contrast(colour("--bg"), colour("--ink-2")) >= 7
    assert contrast(colour("--bg"), colour("--ink-3")) >= 4.5
    assert contrast(colour("--surface-solid"), colour("--accent-2")) >= 4.5, "accent text reads on a card"
    assert "--font-display:" in block and "serif" in block
    # The academy re-binds the shell's tokens only in tokens.css; its own file adds layout.
    for token in ("--accent:", "--bg:", "--ink-1:", "--font-display:"):
        assert token not in academy_css, token
    assert "--acad-pulse:" in tokens and "--acad-sun-rgb:" in tokens


def test_the_academy_sets_its_type_heavier() -> None:
    academy_css = (WEB / "css/academy.css").read_text(encoding="utf-8")
    assert re.search(r"\.med-tab \{[^}]*font-weight: 600", academy_css)
    assert re.search(r"\.med-head-main h1 \{[^}]*font-weight: 700", academy_css)
    assert re.search(r'\.screen\[data-screen="medical"\] \{ font-weight: 500; \}', academy_css)
    assert "color-scheme: light" in academy_css, "native fields follow the academy's daylight"


def test_the_opening_speaks_only_of_what_the_core_reported() -> None:
    quickjs = pytest.importorskip("quickjs")
    context = quickjs.Context()
    context.eval(section(JS_SOURCES["js/academy.js"], "function academyIntroLine(", "\n/* Where along the trace"))
    line = lambda payload: context.eval("academyIntroLine(" + json.dumps(payload) + ")")
    assert line({"name": "Komite 2", "days_left": 9}) == "Komite 2 · 9 gün kaldı"
    assert line({"name": "Komite 2", "days_left": 0}) == "Komite 2 · bugün"
    assert line({"name": "Komite 2", "days_left": -1}) == ""
    assert line({"name": "", "days_left": 3}) == ""
    assert line(None) == "" and line({"days_left": "yakında"}) == ""
    greeting = lambda hour: context.eval("academyGreeting({ getHours() { return " + str(hour) + "; } })")
    assert greeting(4) == "İyi geceler" and greeting(9) == "Günaydın"
    assert greeting(14) == "İyi günler" and greeting(21) == "İyi akşamlar"


def test_the_opening_is_skipped_without_motion_and_silent_when_muted() -> None:
    quickjs = pytest.importorskip("quickjs")
    context = quickjs.Context()
    context.eval("var preferences = {}; function store(key, value) { if (value === undefined) return preferences[key] === undefined ? null : preferences[key]; preferences[key] = value; return value; }")
    context.eval("var State = { compact: false }; var Motion = { allowed() { return !State.reducedMotion; } };")
    context.eval("var window = {}; var touched = 0;")
    context.eval(section(JS_SOURCES["js/academy.js"], "const AcademySound = {", "\n/* ── the room"))
    context.eval(section(JS_SOURCES["js/academy.js"], "const Academy = {", "\n  enter() {") + "\n};")
    # Sound is on by default and off when the student said so; a muted academy never opens an audio context.
    assert context.eval("AcademySound.enabled()") is True
    context.eval('store("nova.academy.sound", "off")')
    assert context.eval("AcademySound.enabled()") is False
    assert context.eval("AcademySound.ensure()") is None
    # Motion off means no opening at all.
    assert context.eval("Academy.shouldPlayIntro()") is True
    context.eval("State.reducedMotion = true")
    assert context.eval("Academy.shouldPlayIntro()") is False
    context.eval("State.reducedMotion = false; State.compact = true")
    assert context.eval("Academy.shouldPlayIntro()") is False


def test_the_academy_sections_are_grouped_in_the_order_listed() -> None:
    quickjs = pytest.importorskip("quickjs")
    context = quickjs.Context()
    context.eval(section(JS_SOURCES["js/medical.js"], "const MED_TABS = [", "\nconst MED_ORIGIN_TR"))
    ids = json.loads(context.eval("JSON.stringify(MED_TABS.map(([id]) => id))"))
    groups = json.loads(context.eval("JSON.stringify(MED_TAB_GROUPS)"))
    assert set(groups) <= set(ids), set(groups) - set(ids)
    assert ids[0] == "dashboard" and groups["dashboard"] == "Çalış"
    assert [ids.index(key) for key in ("dashboard", "exam", "understanding", "histology")] == sorted(
        ids.index(key) for key in ("dashboard", "exam", "understanding", "histology")
    ), "each heading opens the group that follows it"
    assert 'host.appendChild(el("span", "med-tab-group", MED_TAB_GROUPS[id]))' in JS_SOURCES["js/medical.js"]


def _wcag_luminance(hex_colour: str) -> float:
    value = hex_colour.lstrip("#")
    channels = []
    for i in (0, 2, 4):
        c = int(value[i:i + 2], 16) / 255
        channels.append(c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4)
    r, g, b = channels
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def _wcag_contrast(a: str, b: str) -> float:
    light, dark = sorted((_wcag_luminance(a), _wcag_luminance(b)), reverse=True)
    return (light + 0.05) / (dark + 0.05)


def test_the_academy_turns_dark_on_request_and_keeps_its_contrast() -> None:
    tokens = (WEB / "css/tokens.css").read_text(encoding="utf-8")
    # The academy's own switch outranks the shell's theme: its block comes last.
    assert tokens.index("body.academy.academy-dark {") > tokens.index("body.academy, body.academy.light {")
    night = section(tokens, "body.academy.academy-dark {", "\n}")
    colour = lambda name: re.search(name + r":\s+(#[0-9a-f]{6});", night).group(1)
    assert _wcag_luminance(colour("--bg")) < 0.05, "night is dark"
    assert _wcag_contrast(colour("--bg"), colour("--ink-1")) >= 7
    assert _wcag_contrast(colour("--bg"), colour("--ink-2")) >= 7
    assert _wcag_contrast(colour("--bg"), colour("--ink-3")) >= 4.5
    assert _wcag_contrast(colour("--surface-solid"), colour("--accent-2")) >= 4.5
    # The switch lives in the side column, remembers itself, and leaves with the room.
    assert 'id="med-theme"' in HTML
    academy_js = JS_SOURCES["js/academy.js"]
    assert 'store("nova.academy.theme")' in academy_js
    assert 'classList.toggle("academy-dark", this.dark())' in academy_js
    assert 'document.body.classList.remove("academy", "academy-dark")' in academy_js
    academy_css = (WEB / "css/academy.css").read_text(encoding="utf-8")
    assert re.search(r"body\.academy\.academy-dark [^{]*\{ color-scheme: dark; \}", academy_css)
    assert "sun:" in JS_SOURCES["js/shell.js"] and "moon:" in JS_SOURCES["js/shell.js"]


def test_the_academy_theme_defaults_to_daylight() -> None:
    quickjs = pytest.importorskip("quickjs")
    context = quickjs.Context()
    context.eval("var preferences = {}; function store(key, value) { if (value === undefined) return preferences[key] === undefined ? null : preferences[key]; preferences[key] = value; return value; }")
    context.eval("var document = { body: { classes: {}, classList: { toggle(name, on) { this.classes[name] = !!on; } } } }; document.body.classList.classes = document.body.classes;")
    context.eval(section(JS_SOURCES["js/academy.js"], "const AcademyTheme = {", "\n/* ── the room"))
    assert context.eval("AcademyTheme.dark()") is False
    context.eval("AcademyTheme.set(true); AcademyTheme.apply()")
    assert context.eval("AcademyTheme.dark()") is True
    assert context.eval("document.body.classes['academy-dark']") is True
    context.eval("AcademyTheme.set(false); AcademyTheme.apply()")
    assert context.eval("document.body.classes['academy-dark']") is False


# ---------------------------------------------------------------------------
# Araştırma: many doors, and a room of its own
# ---------------------------------------------------------------------------


def test_the_research_room_is_declared_and_wired() -> None:
    for name in ("css/research.css", "js/rooms.js", "js/research.js"):
        assert name in shell.WEB_ASSETS, name
    assert JS_FILES.index("js/rooms.js") < JS_FILES.index("js/research.js") < JS_FILES.index("js/main.js")
    for element_id in ("research-intro", "res-back", "res-theme", "res-sound", "res-presets", "res-sources",
                       "research-site", "research-count", "research-form", "research-input", "research-submit",
                       "research-history", "research-result"):
        assert f'id="{element_id}"' in HTML, element_id
    for source in ("web", "wikipedia", "youtube", "github", "pubmed", "arxiv", "stackoverflow", "hackernews"):
        assert f'class="ri-node" data-source="{source}"' in HTML, source
        assert f'class="ri-line" data-source="{source}"' in HTML, source
    show = section(JS_SOURCES["js/shell.js"], "function showScreen(", "function setStatus(")
    assert 'if (id === "research") ResearchRoom.enter(); else ResearchRoom.leave();' in show
    assert JS_SOURCES["js/main.js"].count("bindResearch();") == 1
    research_js = JS_SOURCES["js/research.js"]
    assert "async function submitResearch(" in research_js and "submitResearch(" not in JS_SOURCES["js/panels.js"].replace("addEventListener(\"submit\", submitResearch)", "")
    # The page asks the core where it may look and sends the selection back with the question.
    assert 'call("research_sources")' in research_js
    assert 'call("run_research", query, Number($("#research-count").value), sources, site || null)' in research_js
    assert "Math.random" not in research_js and "Math.random" not in JS_SOURCES["js/rooms.js"]


def test_the_research_palette_is_daylight_with_a_night_and_keeps_contrast() -> None:
    tokens = (WEB / "css/tokens.css").read_text(encoding="utf-8")
    assert tokens.index("body.research.research-dark {") > tokens.index("body.research, body.research.light {")
    for header in ("body.research, body.research.light {", "body.research.research-dark {"):
        block = section(tokens, header, "\n}")
        colour = lambda name: re.search(name + r":\s+(#[0-9a-f]{6});", block).group(1)
        assert _wcag_contrast(colour("--bg"), colour("--ink-1")) >= 7, header
        assert _wcag_contrast(colour("--bg"), colour("--ink-2")) >= 7, header
        assert _wcag_contrast(colour("--bg"), colour("--ink-3")) >= 4.5, header
        assert _wcag_contrast(colour("--surface-solid"), colour("--accent-2")) >= 4.5, header
    day = section(tokens, "body.research, body.research.light {", "\n}")
    night = section(tokens, "body.research.research-dark {", "\n}")
    assert _wcag_luminance(re.search(r"--bg:\s+(#[0-9a-f]{6});", day).group(1)) > 0.85
    assert _wcag_luminance(re.search(r"--bg:\s+(#[0-9a-f]{6});", night).group(1)) < 0.05
    research_css = (WEB / "css/research.css").read_text(encoding="utf-8")
    for token in ("--accent:", "--bg:", "--ink-1:", "--font-display:", "--res-lamp:"):
        assert token not in research_css, token


def test_research_uncertainties_read_in_turkish_and_unknown_ones_verbatim() -> None:
    quickjs = pytest.importorskip("quickjs")
    context = quickjs.Context()
    context.eval(section(JS_SOURCES["js/research.js"], "const RESEARCH_UNCERTAINTY_TR = [", "\nconst RESEARCH_FRESHNESS_TR"))
    tr = lambda text: context.eval("researchUncertaintyTr(" + json.dumps(text) + ")")
    assert tr("Source Hacker News was unavailable (SearchError).") == "Hacker News kaynağına ulaşılamadı (SearchError)."
    assert tr("2 candidate source(s) could not be safely collected.") == "2 aday kaynak güvenle toplanamadı."
    assert tr("The evidence was not corroborated across independent domains.") == "Kanıt bağımsız alan adlarında doğrulanmadı."
    assert tr("Live research was unavailable; cached evidence was returned and may be outdated (FetchError).").startswith("Canlı araştırma yapılamadı")
    assert tr("Some future service string.") == "Some future service string."
    assert tr("") == "" and tr(None) == ""


def test_research_presets_offer_only_sources_the_core_enabled() -> None:
    quickjs = pytest.importorskip("quickjs")
    context = quickjs.Context()
    context.eval(section(JS_SOURCES["js/research.js"], "const RESEARCH_PRESETS = [", "\n/* The facts a card shows"))
    catalogue = json.dumps([{"id": "web"}, {"id": "github"}, {"id": "youtube"}, {"id": "site"}])
    preset = lambda name: json.loads(context.eval("JSON.stringify(researchPreset(" + json.dumps(name) + ", " + catalogue + "))"))
    assert preset("general") == ["web", "youtube", "github"], "in the preset's order, only what exists, never the site"
    assert preset("science") == ["web"]
    assert preset("all") == ["web", "github", "youtube"]
    assert preset("video") == ["youtube"]
    assert preset("nonsense") == preset("general")


def test_research_cards_escape_untrusted_source_text_and_name_their_facts() -> None:
    quickjs = pytest.importorskip("quickjs")
    context = quickjs.Context()
    context.eval(section(JS_SOURCES["js/foundation.js"], "function esc(", "\nfunction store("))
    context.eval("function icon(name) { return '<svg data-icon=\"' + name + '\"></svg>'; }")
    context.eval(section(JS_SOURCES["js/research.js"], "const RESEARCH_KINDS = {", "\n/* A preset is"))
    context.eval(section(JS_SOURCES["js/research.js"], "const RESEARCH_FRESHNESS_TR = {", "\n/* ── the workspace"))
    catalogue = json.dumps([{"id": "github", "label": "GitHub"}, {"id": "youtube", "label": "YouTube"}])
    repo = {"id": "S1", "title": "octo/atlas <script>alert(1)</script>", "url": "https://github.com/octo/atlas", "kind": "repo",
            "source": "github", "freshness": "current", "excerpt": "3D atlas \"quoted\"", "meta": {"stars": "1240", "language": "Python", "updated": "2026-09-01"},
            "prompt_injection_findings": ["ignore_previous"]}
    html = context.eval("researchSourceMarkup(" + json.dumps(repo) + ", " + catalogue + ")")
    assert "<script" not in html and "&lt;script&gt;" in html and "&quot;quoted&quot;" in html
    assert "★ 1240" in html and "Python" in html and "güncelleme 2026-09-01" in html
    assert "yönlendirme kalıbı" in html and "güncel" in html and 'data-icon="repo"' in html and "GitHub" in html
    video = {"id": "S2", "title": "Ders", "url": "https://www.youtube.com/watch?v=x", "kind": "video", "source": "youtube",
             "freshness": "unknown", "excerpt": "", "meta": {"channel": "Anatomi", "confirmed": "no"}}
    html = context.eval("researchSourceMarkup(" + json.dumps(video) + ", " + catalogue + ")")
    assert "Anatomi" in html and "kanal doğrulanamadı" in html and "tarih bilinmiyor" in html
    claims = context.eval("researchClaimsMarkup(" + json.dumps([{"text": "a <b>claim</b>", "citations": ["S1", "S2"], "confidence": 0.8}]) + ")")
    assert "&lt;b&gt;" in claims and "S1" in claims and "birden çok kaynak" in claims


def test_a_room_remembers_its_switches_and_defaults_to_day_and_sound() -> None:
    quickjs = pytest.importorskip("quickjs")
    context = quickjs.Context()
    context.eval("var preferences = {}; function store(key, value) { if (value === undefined) return preferences[key] === undefined ? null : preferences[key]; preferences[key] = value; return value; }")
    context.eval("var document = { body: { classes: {}, classList: { toggle(name, on) { document.body.classes[name] = !!on; } } } };")
    context.eval(section(JS_SOURCES["js/rooms.js"], "function roomSwitch(", "\n/* ── the opening"))
    context.eval('var sound = roomSwitch("k.sound"); var night = roomNight("k.theme", "x-dark");')
    assert context.eval("sound.on()") is True and context.eval("night.dark()") is False
    context.eval("sound.set(false); night.set(true); night.apply()")
    assert context.eval("sound.on()") is False and context.eval("document.body.classes['x-dark']") is True
    assert context.eval('preferences["k.sound"]') == "off" and context.eval('preferences["k.theme"]') == "dark"


# ---------------------------------------------------------------------------
# toolbox: the palette answers arithmetic; the academy computes at the bedside
# ---------------------------------------------------------------------------


def test_the_palette_calculator_answers_and_stays_out_of_the_way() -> None:
    quickjs = pytest.importorskip("quickjs")
    context = quickjs.Context()
    context.eval(JS_SOURCES["js/toolbox.js"])
    answer = lambda q: context.eval("JSON.stringify(paletteMath(" + json.dumps(q) + "))")
    assert json.loads(answer("12*(3+2)"))["display"] == "12*(3+2) = 60"
    assert json.loads(answer("3,5+1,5"))["display"] == "3,5+1,5 = 5"
    assert json.loads(answer("2^10"))["value"] == 1024
    assert json.loads(answer("sqrt(144)+1"))["value"] == 13
    assert json.loads(answer("sin(30)"))["display"] == "sin(30) = 0,5", "trig speaks degrees on this surface"
    assert json.loads(answer("-3+5"))["value"] == 2
    assert json.loads(answer("70 kg lb"))["display"] == "70 kg = 154,324 lb"
    assert json.loads(answer("37 c f"))["display"] == "37 c = 98,6 f"
    assert json.loads(answer("120 mmhg kpa"))["value"] == pytest.approx(15.9987, abs=0.001)
    assert json.loads(answer("90 dk sa"))["display"] == "90 dk = 1,5 sa"
    assert json.loads(answer("5 mi km"))["value"] == pytest.approx(8.04672, abs=0.001)
    # The guard: ordinary queries, junk and undefined arithmetic stay out.
    for query in ("notlar", "3 elma", "hatırlatıcı kur 5", "1/0", "", "kg lb", "12*", "5 kg kg", "alert(1)"):
        assert json.loads(answer(query)) is None, query
    assert "eval(" not in JS_SOURCES["js/toolbox.js"]


def test_flashcards_speak_only_what_is_on_screen() -> None:
    study = JS_SOURCES["js/study.js"]
    assert "data-card-speak" in study
    assert "State.snapshot?.voice_available" in study, "no voice, no button"
    assert "this.revealed ? `${card.front}. Cevap: ${card.back}` : card.front" in study, (
        "the hidden back is never spoken"
    )
    assert "Speech.unlock(); // inside the gesture" in study
    assert "Speech.play(result.audio);" in study
    assert "new Audio(" not in study


def test_the_term_of_the_day_speaks_when_the_voice_can() -> None:
    medical = JS_SOURCES["js/medical.js"]
    assert "data-term-speak" in medical
    assert "State.snapshot?.voice_available" in medical, "no voice service, no button"
    assert "Speech.unlock(); // inside the gesture" in medical
    assert "Speech.play(result.audio);" in medical
    assert "Türkçesi: ${term.turkish}" in medical
    assert "new Audio(" not in medical


def test_read_aloud_survives_the_slow_synthesis() -> None:
    """The 4-second synthesis outlives Chromium's activation window, so
    playback goes through a context the click itself unlocked - and the
    window asks WebView2 for autoplay outright."""
    conversation = JS_SOURCES["js/conversation.js"]
    assert "const Speech = {" in conversation
    assert "Speech.unlock();" in conversation, "the unlock happens inside the gesture"
    assert "decodeAudioData" in conversation and "createBufferSource" in conversation
    assert "new Audio(" not in conversation, "no <audio> path is left to be blocked"
    assert 'toast("Ses çalınamadı.", true)' in conversation
    assert "--autoplay-policy=no-user-gesture-required" in __import__("io").open(
        "app/ui/nova/shell.py", encoding="utf-8").read()


def test_the_dictionary_card_speaks_and_copies() -> None:
    shell_js = JS_SOURCES["js/shell.js"]
    assert "attachActions(entry) {" in shell_js
    # The voice button exists only when the voice service does.
    assert "if (State.snapshot?.voice_available) {" in shell_js and "data-dict-speak" in shell_js
    assert "data-dict-copy" in shell_js
    # It speaks the word and at most two senses - not the whole card.
    assert "senses.slice(0, 2).join" in shell_js
    # The copy strips the action bar so buttons never enter the clipboard.
    assert 'clone.querySelector(".dict-actions")?.remove();' in shell_js
    assert "Speech.unlock(); // inside the gesture" in shell_js
    assert "Speech.play(result.audio);" in shell_js
    assert ".dict-actions" in CSS


def test_a_held_notification_wears_its_moon_in_the_centre() -> None:
    shell_js = JS_SOURCES["js/shell.js"]
    assert "item.data && item.data.quiet_held" in shell_js
    assert "Sessiz saatlerde geldi; Windows bildirimi gösterilmedi" in shell_js
    assert ".notify-quiet" in CSS


def test_archived_threads_hide_on_request_and_the_ledger_copies() -> None:
    conversation = JS_SOURCES["js/conversation.js"]
    assert '"nova.conv.hidearchive"' in conversation
    assert "arşivli konuşma gizli · göster" in conversation
    assert "Arşivlileri gizle" in conversation
    assert 'item.status !== "archived" || item.active' in conversation

    panels = JS_SOURCES["js/panels.js"]
    assert "copyEvents() {" in panels
    assert 'toast("Kopyalanacak olay yok.", true);' in panels
    assert "copyTextToClipboard(lines.join(" in panels
    assert "fmtTime(e.observed_at)" in panels and "e.component" in panels, "the copy speaks the ledger's own fields"
    assert 'id="diag-copy"' in HTML
    assert 'Diagnostics.copyEvents());' in panels


def test_the_palette_remembers_five_commands_and_forgets_on_request() -> None:
    quickjs = pytest.importorskip("quickjs")
    context = quickjs.Context()
    context.eval(JS_SOURCES["js/toolbox.js"])
    parse = lambda raw: json.loads(context.eval("JSON.stringify(paletteRecentParse(" + json.dumps(raw) + "))"))
    add = lambda raw, cmd: context.eval("paletteRecentAdd(" + json.dumps(raw) + ", " + json.dumps(cmd) + ")")

    state = "[]"
    for command in ("bir", "iki", "üç", "dört", "beş", "altı"):
        state = add(state, command)
    assert parse(state) == ["altı", "beş", "dört", "üç", "iki"], "five entries, newest first"
    state = add(state, "dört")
    assert parse(state)[0] == "dört" and parse(state).count("dört") == 1, "a repeat moves up, never duplicates"
    assert parse(add("[]", "   ")) == [] and add("[]", "") == "[]"
    assert parse("bozuk json") == [] and parse(json.dumps({"a": 1})) == []
    long_command = "x" * 200
    assert len(parse(add("[]", long_command))[0]) == 80, "entries are clipped, not refused"

    shell_js = JS_SOURCES["js/shell.js"]
    assert 'paletteRecentParse(store("nova.palette.recent"))' in shell_js
    assert "Tekrar gönder: “" in shell_js
    assert "Son komutları unut (bu cihazda)" in shell_js
    assert 'paletteRecentAdd(store("nova.palette.recent"), text)' in JS_SOURCES["js/conversation.js"]


def test_a_fired_reminder_notification_offers_to_rearm_itself() -> None:
    shell = JS_SOURCES["js/shell.js"]
    assert 'item.kind === "reminder" ? `<button type="button" class="icon-btn small notify-rearm"' in shell, (
        "only reminder entries carry the ⏰"
    )
    assert "10 dakika sonraya yeni hatırlatıcı kur" in shell, "the title admits it arms a NEW reminder"
    assert 'call("create_reminder", item.body, "+10")' in shell
    assert 'item.kind !== "reminder") return;' in shell, "rearm refuses other kinds"
    rearm_branch = shell.index("data-act='rearm'")
    dismiss_branch = shell.index("data-act='dismiss'")
    assert rearm_branch < dismiss_branch, "rearm is checked before the row activates"


def test_the_composer_walks_its_sent_history_with_ctrl_arrows() -> None:
    quickjs = pytest.importorskip("quickjs")
    context = quickjs.Context()
    context.eval(JS_SOURCES["js/toolbox.js"])
    step = lambda entries, index, direction, draft="": json.loads(context.eval(
        "JSON.stringify(historyStep(" + json.dumps(entries) + ", " + json.dumps(index)
        + ", " + json.dumps(direction) + ", " + json.dumps(draft) + ") || null)"))

    assert step([], -1, "back") is None, "no history, no walk"
    assert step(["son", "eski"], -1, "back") == {"index": 0, "text": "son"}
    assert step(["son", "eski"], 0, "back") == {"index": 1, "text": "eski"}
    assert step(["son", "eski"], 1, "back") is None, "the oldest is the edge"
    assert step(["son", "eski"], 0, "forward", "taslak") == {"index": -1, "text": "taslak"}
    assert step(["son", "eski"], -1, "forward", "taslak") is None
    assert step(["son"], 5, "back") is None, "an index beyond a shrunken history clamps"
    assert step(["son"], 5, "forward") == {"index": -1, "text": ""}
    assert step([1, " ", "gerçek"], -1, "back") == {"index": 0, "text": "gerçek"}

    conversation = JS_SOURCES["js/conversation.js"]
    assert 'event.key === "ArrowUp" ? "back" : "forward"' in conversation
    assert "if (historyIndex === -1) historyDraft = chatInput.value;" in conversation
    assert "historyIndex = -1; // typing by hand leaves the walk" in conversation
    assert 'historyIndex = -1; historyDraft = "";' in conversation, "a send resets the walk"


def test_a_finished_focus_offers_itself_to_the_academy_log() -> None:
    shell_js = JS_SOURCES["js/shell.js"]
    # Asked, never assumed - and only when it could be study at all.
    assert "if (finished) this.offerToLog(this.minutes);" in shell_js
    assert "if (!minutes || minutes < 5 || !State.medical?.available" in shell_js
    assert "Akademi günlüğüne yazılsın mı?" in shell_js
    assert 'Medical.request("plan_log_study", { activity: "focus", minutes })' in shell_js
    assert "çalışma günlüğüne işlendi." in shell_js
    # The stopped-early path never offers: only stop(true) does.
    assert shell_js.count("this.offerToLog(") == 1


def test_copy_hands_and_the_mini_clock_are_wired() -> None:
    conversation = JS_SOURCES["js/conversation.js"]
    # Every full-size bubble carries the copy control; slim ones do not.
    assert "function copyButton(slim)" in conversation and "${copyButton(slim)}" in conversation
    assert 'data-copy title="Metni kopyala"' in conversation
    assert 'copy.closest(".msg")?.querySelector(".msg-body")' in conversation
    # One clipboard hand, honest in both directions.
    assert "async function copyTextToClipboard(" in conversation
    assert 'toast("Panoya erişilemedi.", true);' in conversation
    assert 'toast("Panoya kopyalandı.", "ok");' in conversation
    assert 'toast("Kopyalanacak metin yok.", true);' in conversation

    research = JS_SOURCES["js/research.js"]
    assert 'id="res-copy"' in research
    assert "copyResearchReport" in research
    assert 'toast("Kopyalanacak rapor yok.", true);' in research
    assert "researchReportMarkdown(report, Research.catalogue)" in research

    shell_js = JS_SOURCES["js/shell.js"]
    assert 'id="mini-clock"' in HTML
    assert "const MiniClock = {" in shell_js
    assert "MiniClock.start();" in shell_js and "else MiniClock.stop();" in shell_js
    assert "setInterval(() => this.tick(), 30000)" in shell_js
    assert ".mini-clock" in CSS and ".msg-copy" in CSS


def test_uptime_speaks_and_snow_respects_reduced_motion() -> None:
    quickjs = pytest.importorskip("quickjs")
    context = quickjs.Context()
    context.eval(section(JS_SOURCES["js/panels.js"], "function pulseUptime", "\nconst Pulse = {"))
    uptime = lambda seconds: context.eval("pulseUptime(" + json.dumps(seconds) + ")")
    assert uptime(0) == "0 dk"
    assert uptime(48 * 60) == "48 dk"
    assert uptime(2 * 3600 + 14 * 60) == "2 sa 14 dk"
    assert uptime(3 * 86400 + 4 * 3600) == "3 g 4 sa"
    assert uptime(-5) == "0 dk" and uptime(None) == "0 dk"

    panels = JS_SOURCES["js/panels.js"]
    assert "Oturum ${esc(pulseUptime(pulse.uptime_seconds))}" in panels
    assert "Veri ${esc(fmtBytes(pulse.state_data_bytes))}" in panels

    shell_js = JS_SOURCES["js/shell.js"]
    assert '"Kar yağdır"' in shell_js and "Snow.fall()" in shell_js
    assert 'if (State.reducedMotion) { toast("Hareket azaltılmışken kar yağmaz.", true); return; }' in shell_js
    assert 'setTimeout(() => layer.remove(), 15000);' in shell_js
    assert ".snowfall { position: fixed" in CSS and "pointer-events: none" in CSS
    assert "@keyframes snow-drop" in CSS


def test_the_settings_card_and_pulse_carry_quiet_hours_and_battery() -> None:
    assert 'id="settings-quiet"' in HTML and "Sessiz saatler" in HTML
    assert "uygulama içi bildirim merkezi almaya devam eder" in HTML
    panels = JS_SOURCES["js/panels.js"]
    assert '$("#settings-quiet").value = s.quiet_hours || "";' in panels
    assert 'quiet_hours: $("#settings-quiet").value.trim(),' in panels
    assert "battery_percent" in panels and "şarjda" in panels
    assert "battery_percent: 84" in JS_SOURCES["js/bridge.js"]


def test_the_drawer_pins_and_the_chat_find_are_pure_and_honest() -> None:
    quickjs = pytest.importorskip("quickjs")
    context = quickjs.Context()
    conversation = JS_SOURCES["js/conversation.js"]
    context.eval(section(conversation, "/* ── drawer pins & in-chat find (pure)", "\nfunction appendMessage("))

    pins = lambda raw: json.loads(context.eval("JSON.stringify([...convPinsParse(" + json.dumps(raw) + ")])"))
    assert pins("a,b") == ["a", "b"]
    assert pins(" a , ,b, ") == ["a", "b"]
    assert pins("") == [] and pins(None) == []

    order = lambda items, raw: json.loads(context.eval(
        "JSON.stringify(convOrder(" + json.dumps(items) + ", convPinsParse(" + json.dumps(raw) + ")))"))
    items = [{"conversation_id": "one"}, {"conversation_id": "two"}, {"conversation_id": "three"}]
    split = order(items, "three,ghost")
    assert [item["conversation_id"] for item in split["pinned"]] == ["three"], "unknown pins pin nothing"
    assert [item["conversation_id"] for item in split["rest"]] == ["one", "two"]
    assert order([], "x") == {"pinned": [], "rest": [], "hiddenCount": 0}

    hide = lambda payload: json.loads(context.eval(
        "JSON.stringify(convOrder(" + json.dumps(payload) + ", convPinsParse(''), { hideArchived: true }))"))
    mixed = [
        {"conversation_id": "a", "status": "active"},
        {"conversation_id": "b", "status": "archived"},
        {"conversation_id": "c", "status": "archived", "active": True},
    ]
    hidden = hide(mixed)
    assert [item["conversation_id"] for item in hidden["rest"]] == ["a", "c"], "the open thread never hides"
    assert hidden["hiddenCount"] == 1

    find = lambda texts, query: json.loads(context.eval(
        "JSON.stringify(chatFindFilter(" + json.dumps(texts) + ", " + json.dumps(query) + "))"))
    texts = ["Merhaba dünya", "Kalp anatomisi", "kalp krizi belirtileri"]
    assert find(texts, "kalp") == [1, 2], "Turkish-lowercased, case-insensitive"
    assert find(texts, "KALP") == [1, 2]
    assert find(texts, "yürek") == []
    assert find(texts, "") is None and find(texts, "   ") is None, "empty query means the filter is off"

    # Wiring: the pin on every row, the find box, the escape, the clear.
    assert 'data-pin="${esc(item.conversation_id)}"' in conversation
    assert '"nova.conv.pins"' in conversation and "Sabitlenmiş" in conversation
    assert "bindChatFind();" in JS_SOURCES["js/main.js"]
    assert 'id="chat-find"' in HTML and 'id="chat-find-count"' in HTML
    assert "bu cihazda sabitler" in HTML, "the note says pins are device-local"
    assert ".msg.find-miss { display: none; }" in CSS
    assert "clearChatFind(); input.blur();" in conversation


def test_the_ledger_sieve_matches_what_a_row_shows() -> None:
    quickjs = pytest.importorskip("quickjs")
    context = quickjs.Context()
    context.eval(JS_SOURCES["js/toolbox.js"])
    match = lambda event, query: context.eval(
        "eventMatches(" + json.dumps(event) + ", " + json.dumps(query) + ")")

    event = {"level": "warning", "component": "ui", "name": "reminder.snoozed",
             "message": "Hatırlatıcı ertelendi", "attributes": {"routine_id": "abc123"}}
    assert match(event, "") is True and match(event, "   ") is True, "empty query keeps everything"
    assert match(event, "SNOOZED") is True, "case folds"
    assert match(event, "REMINDER.SNOOZED") is True, "an uppercase I still finds its dotted i"
    assert match(event, "HATIRLATICI") is True, "and the Turkish fold still finds dotted friends"
    assert match(event, "hatırlatıcı") is True
    assert match(event, "abc123") is True, "attributes match too"
    assert match(event, "warning") is True
    assert match(event, "yok-boyle") is False
    assert match({"message": None}, "x") is False, "a bare row never crashes"

    panels = JS_SOURCES["js/panels.js"]
    assert 'textFilter: "",' in panels
    assert panels.count("eventMatches(") == 3, "render, fresh-row mark and copy all sieve"
    assert "`${rows.length} / ${State.diagnosticEvents.length} olay`" in panels
    assert '$("#diag-find").addEventListener("input"' in panels
    assert 'id="diag-find"' in HTML


def test_fenced_code_wears_its_language_but_only_clean_tokens() -> None:
    quickjs = pytest.importorskip("quickjs")
    context = quickjs.Context()
    context.eval("function esc(v) { return String(v == null ? \"\" : v).replace(/&/g, \"&amp;\").replace(/</g, \"&lt;\").replace(/>/g, \"&gt;\").replace(/\"/g, \"&quot;\"); }")
    context.eval(section(JS_SOURCES["js/conversation.js"], "function renderMarkdownLite", "\nfunction appendMessage"))
    run = lambda text: context.eval("renderMarkdownLite(" + json.dumps(text) + ")")
    NL = chr(10)

    tagged = run("```python" + NL + "x = 1" + NL + "```")
    assert '<span class="code-lang">python</span>' in tagged and "data-code-copy" in tagged
    plain = run("```" + NL + "x" + NL + "```")
    assert "code-lang" not in plain, "no info word, no badge"
    plus = run("```c++" + NL + "int x;" + NL + "```")
    assert '<span class="code-lang">c++</span>' in plus
    hostile = run("```<script>alert(1)</script>" + NL + "kod" + NL + "```")
    assert "code-lang" not in hostile, "a strange info word earns no badge at all"
    assert "<script" not in hostile
    cut = run("```sql" + NL + "SELECT 1")
    assert '<span class="code-lang">sql</span>' in cut, "a mid-stream cut keeps its badge"
    assert ".code-lang" in CSS


def test_the_bell_counts_its_kinds_without_inventing_any() -> None:
    quickjs = pytest.importorskip("quickjs")
    context = quickjs.Context()
    context.eval(JS_SOURCES["js/toolbox.js"])
    kinds = lambda items: json.loads(context.eval(
        "JSON.stringify(notifKinds(" + json.dumps(items) + "))"))

    assert kinds([]) == []
    mixed = kinds([
        {"kind": "reminder"}, {"kind": "task"}, {"kind": "reminder"},
        {"kind": "approval"}, {"kind": "task"}, {"kind": "reminder"},
        {"kind": ""}, {"notitle": True},
    ])
    assert mixed == [
        {"kind": "reminder", "count": 3},
        {"kind": "task", "count": 2},
        {"kind": "approval", "count": 1},
    ], "most numerous first, the kindless skipped"
    tied = kinds([{"kind": "b"}, {"kind": "a"}])
    assert [k["kind"] for k in tied] == ["a", "b"], "ties break by name"

    shell = JS_SOURCES["js/shell.js"]
    assert "const visible = this.filter ? items.filter((item) => item.kind === this.filter) : items;" in shell
    assert "if (this.filter && !kinds.some((k) => k.kind === this.filter)) this.filter = null;" in shell, (
        "a vanished kind resets the view instead of showing nothing"
    )
    assert "if (kinds.length < 2)" in shell, "one kind alone earns no chip row"
    assert "Kind chips are a view, never a mutation" in shell
    assert 'id="notify-filter"' in HTML
    assert ".notify-filter .chip.on" in CSS


def test_the_routine_rows_offer_run_now() -> None:
    panels = JS_SOURCES["js/panels.js"]
    assert 'data-act="run"' in panels and "Çalıştır</button>" in panels
    assert 'call("run_routine_now", btn.closest(".routine-row").dataset.id)' in panels
    run_handler = panels.split("[data-act='run']\", host")[1].split("}));")[0]
    assert "btn.disabled = true;" in run_handler, "no double starts while one runs"
    assert "confirmDialog" not in run_handler, "running is not destructive: no dialog"


def test_the_about_card_draws_dashes_for_what_is_unknown() -> None:
    quickjs = pytest.importorskip("quickjs")
    context = quickjs.Context()
    context.eval(section(JS_SOURCES["js/foundation.js"], "function esc(", "\nfunction store("))
    context.eval(section(JS_SOURCES["js/panels.js"], "/* ── about: the build's own facts (pure markup)", "/* ── about: runtime"))
    full = context.eval("aboutRows(" + json.dumps({
        "app_version": "0.1.0", "python_version": "3.12.1",
        "webview2_version": "129.0.1", "state_directory": "C:/veri"}) + ")")
    assert "0.1.0" in full and "129.0.1" in full and "C:/veri" in full and "—" not in full
    bare = context.eval("aboutRows(" + json.dumps({
        "app_version": None, "python_version": None,
        "webview2_version": None, "state_directory": None}) + ")")
    assert bare.count("—") == 4 and 'class="config-value off"' in bare, "unknown is a dash, not a guess"
    hostile = context.eval("aboutRows(" + json.dumps({
        "app_version": "<b>x</b>", "python_version": "3",
        "webview2_version": "1", "state_directory": "d"}) + ")")
    assert "<b>" not in hostile and "&lt;b&gt;" in hostile

    panels = JS_SOURCES["js/panels.js"]
    assert 'const info = await call("about_info");' in panels
    assert 'call("open_state_folder")' in panels
    assert "About.load();" in panels and "About.bind();" in panels
    for element_id in ("settings-about", "about-rows", "about-open-state"):
        assert f'id="{element_id}"' in HTML, element_id


def test_the_reminder_rows_offer_a_ten_minute_snooze() -> None:
    panels = JS_SOURCES["js/panels.js"]
    assert 'data-reminder-snooze="${esc(row.reminder_id)}"' in panels
    assert "+10 dk" in panels
    assert 'call("snooze_reminder", button.dataset.reminderSnooze, 10)' in panels
    handler = panels.split('data-reminder-snooze]", host')[1].split("}));")[0]
    assert "confirmDialog" not in handler, "a snooze is reversible: no dialog"
    assert "Reminders.load()" in handler and "renderHomeBrief(true)" in handler


def test_the_palette_converts_money_and_sets_reminders() -> None:
    quickjs = pytest.importorskip("quickjs")
    context = quickjs.Context()
    context.eval(JS_SOURCES["js/toolbox.js"])
    money = lambda q: json.loads(context.eval("JSON.stringify(paletteCurrency(" + json.dumps(q) + "))"))
    assert money("100 usd") == {"amount": 100, "from": "USD", "to": "TRY"}
    assert money("100 usd eur") == {"amount": 100, "from": "USD", "to": "EUR"}
    assert money("$50") == {"amount": 50, "from": "USD", "to": "TRY"}
    assert money("€10 tl") == {"amount": 10, "from": "EUR", "to": "TRY"}
    assert money("3,5 euro") == {"amount": 3.5, "from": "EUR", "to": "TRY"}
    assert money("250 tl usd") == {"amount": 250, "from": "TRY", "to": "USD"}
    assert money("48,78 lira eur") == {"amount": 48.78, "from": "TRY", "to": "EUR"}
    for query in ("100 tl", "100 usd usd", "70 kg lb", "12*3", "usd", "0 usd", "1000000001 usd", "yüz dolar", ""):
        assert money(query) is None, query

    remind = lambda q: json.loads(context.eval("JSON.stringify(paletteReminder(" + json.dumps(q) + "))"))
    assert remind("hatırlat 10 dk su iç") == {"when": "+10", "label": "10 dk sonra", "text": "su iç"}
    assert remind("hatirlat 2 sa ilaç al") == {"when": "+120", "label": "120 dk sonra", "text": "ilaç al"}
    assert remind("HATIRLAT 45 dakika mola") == {"when": "+45", "label": "45 dk sonra", "text": "mola"}
    assert remind("hatırlat 09:30 komiteye çalış") == {"when": "09:30", "label": "09:30", "text": "komiteye çalış"}
    assert remind("hatırlat 9:30 toplantı") == {"when": "09:30", "label": "09:30", "text": "toplantı"}
    for query in ("hatırlat su iç", "hatırlatma ayarı", "hatırlat 25:00 x", "hatırlat 9:5 x", "hatırlat 0 dk x", "hatırlat 2000 dk x", "su iç"):
        assert remind(query) is None, query

    # Wiring: the rows, the bridge call, the focus shortcut.
    shell_js = JS_SOURCES["js/shell.js"]
    assert "const money = paletteCurrency(q);" in shell_js
    assert 'call("convert_currency", money.amount, money.from, money.to)' in shell_js
    assert "const remind = paletteReminder(q);" in shell_js
    assert 'call("create_reminder", remind.text, remind.when)' in shell_js
    assert "Focus.start(Number(focusMinutes[1]))" in shell_js


def test_the_palette_hands_a_word_to_the_dictionary_and_the_card_escapes() -> None:
    quickjs = pytest.importorskip("quickjs")
    context = quickjs.Context()
    context.eval(JS_SOURCES["js/toolbox.js"])
    ask = lambda q: context.eval("dictionaryQuery(" + json.dumps(q) + ")")
    assert ask("sözlük kalp") == "kalp"
    assert ask("sozluk yürek") == "yürek"
    assert ask("TDK göz") == "göz"
    assert ask("SÖZLÜK Kalp") == "Kalp", "the word keeps its casing; the service folds it"
    assert ask("sözlük iki kelime") == "iki kelime"
    for query in ("sözlük", "tdk", "kalp", "hesap 12*3", "sözlük " + "a" * 65, ""):
        assert ask(query) is None, query

    entry = {"ok": True, "word": "kalp", "origin": "Arapça ḳalb",
             "meanings": [{"features": "isim, anatomi", "sense": "Organ.", "example": "Kalbim çarpıyor."}],
             "compounds": ["kalp ağrısı"]}
    markup = context.eval("dictionaryMarkup(" + json.dumps(entry) + ")")
    assert '<div class="dict-word">kalp</div>' in markup
    assert '<i class="dict-feat">isim, anatomi</i>' in markup and "Organ." in markup
    assert "“Kalbim çarpıyor.”" in markup and "kalp ağrısı" in markup
    assert "TDK Güncel Türkçe Sözlük" in markup and "canlı sorgu" in markup
    hostile = dict(entry, word="<script>alert(1)</script>", compounds=["<img src=x>"])
    poisoned = context.eval("dictionaryMarkup(" + json.dumps(hostile) + ")")
    assert "<script>" not in poisoned and "&lt;script&gt;" in poisoned and "<img" not in poisoned
    assert context.eval("dictionaryMarkup(null)") == ""
    assert context.eval("dictionaryMarkup({ok: false})") == ""

    # Wiring: the palette row, the card, its Escape, and close buttons
    # that actually close (the shortcuts X used to be dead).
    shell_js = JS_SOURCES["js/shell.js"]
    assert "const wordQuery = dictionaryQuery(q);" in shell_js
    assert "Dict.lookup(wordQuery)" in shell_js
    assert 'if (event.key === "Escape" && !$("#dictcard").hidden)' in shell_js
    assert '$("#dict-close").addEventListener("click", () => Dict.close());' in shell_js
    assert '$("#shortcuts-close").addEventListener("click", () => setShortcutsOpen(false));' in shell_js
    for element_id in ("dictcard", "dict-close", "dict-body"):
        assert f'id="{element_id}"' in HTML, element_id
    assert "sözlük kalp" in HTML, "the F1 card teaches the prefix"
    assert ".modal.dict" in CSS


def test_the_home_remote_draws_what_the_tools_reported() -> None:
    quickjs = pytest.importorskip("quickjs")
    context = quickjs.Context()
    context.eval(section(JS_SOURCES["js/foundation.js"], "function esc(", "\nfunction store("))
    context.eval(section(JS_SOURCES["js/panels.js"], "/* ── remote: the home card's markup (pure)", "/* ── remote: runtime"))
    draw = lambda now, wa=None: context.eval("remoteMarkup(" + json.dumps(now) + ", " + json.dumps(wa) + ")")

    playing = draw({"ok": True, "data": {"running": True, "playing": True, "artist": "Duman", "track": "Bal\u0131k", "position": "0:12", "duration": "2:48", "liked": True, "volume_percent": 80}})
    assert "Duman — Balık" in playing and "0:12 / 2:48" in playing
    assert "⏸" in playing and 'title="Duraklat"' in playing
    assert 'class="remote-btn liked"' in playing and 'value="80"' in playing
    assert 'data-tool="spotify_sleep_timer"' in playing and '{&quot;minutes&quot;:30}' not in playing, "the args stay valid JSON in the attribute"
    assert "remote-wa" not in playing, "no delegation, no delegation line"

    paused = draw({"ok": True, "data": {"running": True, "playing": False, "track": "Bal\u0131k", "artists": ["Duman"]}})
    assert "Duman — Balık" in paused and "▶" in paused and 'title="Çal"' in paused
    assert "remote-volume" not in paused, "no volume read, no slider invented"

    closed = draw({"ok": False, "status": "blocked", "message": "Spotify çalışmıyor. Önce uygulamayı aç.", "data": {"running": False}})
    assert 'class="remote-off"' in closed and "Spotify çalışmıyor" in closed and "remote-btn" not in closed

    busy = draw({"ok": True, "data": {"running": True, "playing": False}}, {"ok": True, "data": {"active": True, "contact": "Ahmet", "turns_taken": 2, "max_turns": 8}})
    assert "Bir şey çalmıyor" in busy
    assert "<b>Ahmet</b>" in busy and "(2/8)" in busy and 'data-tool="whatsapp_stop_delegation"' in busy
    hostile = draw({"ok": True, "data": {"running": True, "playing": True, "artist": "<img src=x>", "track": "x"}})
    assert "<img" not in hostile and "&lt;img" in hostile

    glanced = context.eval("remoteMarkup(" + json.dumps({"ok": True, "data": {"running": True, "playing": False}})
                            + ", null, " + json.dumps({"ok": True, "data": {"unread_chats": 3}}) + ")")
    assert "3 sohbette okunmamış mesaj" in glanced
    closed_wa = context.eval("remoteMarkup(" + json.dumps({"ok": True, "data": {"running": True, "playing": False}})
                              + ", null, " + json.dumps({"ok": False, "status": "blocked", "message": "WhatsApp kapalı."}) + ")")
    assert "okunmamış" not in closed_wa, "a closed WhatsApp draws no line"
    panels_glance = JS_SOURCES["js/panels.js"]
    assert '"whatsapp_read_chats", { limit: 20, launch: false }' in panels_glance

    sleeping = draw({"ok": True, "data": {"running": True, "playing": True, "artist": "Duman", "track": "Bal\u0131k", "sleep_minutes_left": 23}})
    assert "⏰ 23 dk · iptal" in sleeping and 'data-tool="spotify_cancel_sleep_timer"' in sleeping
    assert 'data-tool="spotify_sleep_timer"' not in sleeping, "one timer button at a time"

    queue_input = draw({"ok": True, "data": {"running": True, "playing": False}})
    assert 'class="remote-queue"' in queue_input, "the request line ships with a live card"
    assert "remote-queue" not in closed, "no Spotify, no request line"

    # Wiring: the card, its start/stop with the screen, the bridge call.
    assert 'id="home-remote"' in HTML
    panels_js = JS_SOURCES["js/panels.js"]
    assert '"spotify_queue_track", { query: wanted }' in panels_js
    assert "queue.disabled = true;" in panels_js, "one request at a time"
    assert 'if (id === "home") Remote.start(); else Remote.stop();' in JS_SOURCES["js/shell.js"]
    assert 'call("run_remote_tool", "spotify_now_playing", {})' in JS_SOURCES["js/panels.js"]
    assert ".remote-btn" in CSS


def test_the_settings_screen_carries_the_vision_switch() -> None:
    assert 'id="settings-vision-toggle"' in HTML
    panels = JS_SOURCES["js/panels.js"]
    assert '$("#settings-vision-toggle").checked = s.vision_enabled !== false;' in panels
    assert 'vision_enabled: $("#settings-vision-toggle").checked,' in panels


def test_the_clinical_calculators_apply_the_formulas_they_name() -> None:
    quickjs = pytest.importorskip("quickjs")
    context = quickjs.Context()
    context.eval(section(JS_SOURCES["js/medcalc.js"], "const MEDCALC = [", "\n/* ── render"))
    run = lambda calc, values: json.loads(context.eval(
        "JSON.stringify(medcalcResult(MEDCALC.find((item) => item.id === " + json.dumps(calc) + "), " + json.dumps(values) + "))"))
    assert run("bmi", {"weight": 70, "height": 175})["value"] == pytest.approx(22.9, abs=0.01)
    assert run("bsa", {"height": 175, "weight": 70})["value"] == pytest.approx(1.84, abs=0.01)
    assert run("ibw", {"height": 175, "sex": "male"})["value"] == pytest.approx(70.5, abs=0.1)
    assert run("ibw", {"height": 175, "sex": "female"})["value"] == pytest.approx(66.0, abs=0.1)
    assert run("crcl", {"age": 40, "weight": 70, "creatinine": 1.0, "sex": "male"})["value"] == pytest.approx(97.2, abs=0.1)
    assert run("crcl", {"age": 40, "weight": 70, "creatinine": 1.0, "sex": "female"})["value"] == pytest.approx(82.6, abs=0.1)
    assert run("aniongap", {"sodium": 140, "chloride": 104, "bicarbonate": 24})["value"] == 12
    assert run("corrca", {"calcium": 8.0, "albumin": 2.0})["value"] == pytest.approx(9.6)
    assert run("corrna", {"sodium": 130, "glucose": 600})["value"] == pytest.approx(138.0)
    assert run("ldl", {"total": 200, "hdl": 50, "tg": 150})["value"] == 120
    assert "geçerli değildir" in run("ldl", {"total": 200, "hdl": 50, "tg": 450})["warn"]
    assert run("map", {"systolic": 120, "diastolic": 80})["value"] == 93
    assert run("osm", {"sodium": 140, "glucose": 90, "bun": 14})["value"] == 290
    assert run("maxhr", {"age": 20})["value"] == 200
    assert run("units", {"value": 90, "what": "glucose"})["value"] == pytest.approx(5.0, abs=0.01)
    assert run("units", {"value": 5, "what": "glucose_r"})["value"] == pytest.approx(90.08, abs=0.01)
    assert run("units", {"value": 1.0, "what": "cr"})["value"] == pytest.approx(88.4)
    # Missing inputs answer with silence, never zero.
    assert run("bmi", {"weight": 70}) is None
    assert run("crcl", {"age": 40, "weight": 70, "creatinine": 1.0}) is None


def test_batch_one_surfaces_are_declared_and_wired() -> None:
    for name in ("js/toolbox.js", "js/medcalc.js"):
        assert name in shell.WEB_ASSETS, name
    assert JS_FILES.index("js/toolbox.js") < JS_FILES.index("js/shell.js")
    assert JS_FILES.index("js/medcalc.js") < JS_FILES.index("js/main.js")
    for element_id in ("med-calc-grid", "med-term", "shortcuts", "shortcuts-close"):
        assert f'id="{element_id}"' in HTML, element_id
    assert 'data-view="calc"' in HTML and "Eğitim amaçlıdır" in HTML
    tabs = section(JS_SOURCES["js/medical.js"], "const MED_TABS = [", "];")
    assert '["calc", "Hesaplar",' in tabs
    assert 'if (view === "calc") { MedCalc.render(); return; }' in JS_SOURCES["js/medical.js"]
    # The palette's answer row and the F1 card exist, and every key the card
    # names is a binding the shell actually has.
    shell_js = JS_SOURCES["js/shell.js"]
    assert "const math = paletteMath(q);" in shell_js
    assert 'if (event.key === "F1")' in shell_js and "function setShortcutsOpen(" in shell_js
    card = section(HTML, 'id="shortcuts"', "ARAŞTIRMA AÇILIŞI")
    for key_markup, binding in (
        ("<kbd>Ctrl</kbd>+<kbd>K</kbd>", 'key === "k"'),
        ("<kbd>Ctrl</kbd>+<kbd>D</kbd>", 'key === "d"'),
        ("<kbd>Ctrl</kbd>+<kbd>,</kbd>", 'event.key === ","'),
        ("<kbd>Ctrl</kbd>+<kbd>Shift</kbd>+<kbd>B</kbd>", 'key === "b"'),
        ("<kbd>F1</kbd>", 'event.key === "F1"'),
    ):
        assert key_markup in card, key_markup
        assert binding in shell_js, binding
    # The day's term renders only what the core sent and opens the lab.
    medical_js = JS_SOURCES["js/medical.js"]
    assert "this.state.term_of_day" in medical_js
    assert "Lab.pendingSelect = termButton.dataset.term" in medical_js
    assert "const pending = this.pendingSelect;" in medical_js


def test_the_brief_carries_the_almanac_and_stays_silent_without_a_city() -> None:
    quickjs = pytest.importorskip("quickjs")
    context = quickjs.Context()
    context.eval(section(JS_SOURCES["js/foundation.js"], "function esc(", "\nfunction store("))
    context.eval("function emptyState() { return '<empty>'; }")
    context.eval(section(JS_SOURCES["js/panels.js"], "function homeBriefMarkup", "\nlet briefFetchedAt"))
    markup = lambda brief: context.eval("homeBriefMarkup(" + json.dumps(brief) + ")")
    full = markup({"ok": True, "date": "20 Eylül", "almanac": {
        "weather": {"available": True, "city": "İstanbul", "temperature": 21, "label": "parçalı bulutlu", "high": 24, "low": 18},
        "rates": {"available": True, "usd_try": 41.2, "eur_try": 44.8, "date": "2026-09-19"},
    }})
    assert "İstanbul 21° · parçalı bulutlu" in full and "↑24° ↓18°" in full
    assert "1 $ = 41,2 ₺ · 1 € = 44,8 ₺" in full
    assert "Gün doğumu" not in full, "no sun times in the payload, no sun row invented"
    sunny = markup({"ok": True, "date": "21 Eylül", "almanac": {
        "weather": {"available": True, "city": "İstanbul", "temperature": 21, "label": "açık",
                     "high": 24, "low": 18, "sunrise": "06:52", "sunset": "19:24"},
        "rates": {"available": False, "reason": "x"},
    }})
    assert "Gün doğumu 06:52 · batımı 19:24" in sunny
    assert "Yarın" not in sunny, "no tomorrow in the payload, no tomorrow row"
    tomorrow = markup({"ok": True, "date": "21 Eylül", "almanac": {
        "weather": {"available": True, "city": "İstanbul", "temperature": 21, "label": "açık",
                     "high": 24, "low": 18, "tomorrow_high": 18, "tomorrow_low": 12},
        "rates": {"available": False, "reason": "x"},
    }})
    assert "Yarın" in tomorrow and "↑18° ↓12°" in tomorrow
    # No city configured: no weather row and no nagging.
    silent = markup({"ok": True, "almanac": {"weather": {"available": False, "reason": "Şehir ayarlanmadı."},
                                             "rates": {"available": False, "reason": "Kur servisi yanıt vermedi (HTTP 503)."}}})
    assert "Şehir ayarlanmadı" not in silent and "Kur servisi" not in silent
    # A real failure is shown as the reason it is.
    failed = markup({"ok": True, "almanac": {"weather": {"available": False, "reason": "Hava servisi yanıt vermedi (HTTP 503)."},
                                             "rates": {"available": False, "reason": "x"}}})
    assert "Hava servisi yanıt vermedi (HTTP 503)." in failed
    # The settings card round-trips the city.
    assert 'id="settings-city"' in HTML
    panels = JS_SOURCES["js/panels.js"]
    assert 'almanac_city: $("#settings-city").value.trim(),' in panels
    assert '$("#settings-city").value = s.almanac_city || "";' in panels


def test_batch_three_surfaces_are_wired_and_accents_stay_out_of_the_rooms() -> None:
    # Read-aloud: only when the voice service exists, stripped by the bridge.
    conversation = JS_SOURCES["js/conversation.js"]
    assert "State.snapshot?.voice_available" in conversation and 'call("speak_text", text)' in conversation
    assert "const Readaloud = {" in conversation and "bindReadaloud();" in JS_SOURCES["js/main.js"]
    # Research export: the page composes, the bridge bounds and writes.
    research = JS_SOURCES["js/research.js"]
    assert "function researchReportMarkdown(" in research
    assert 'call("save_markdown", picked.path' in research and 'id="res-export"' in research
    assert "State.lastResearchReport = report;" in research
    # Accents: tokens only, declared before the rooms so they never leak in.
    tokens = (WEB / "css/tokens.css").read_text(encoding="utf-8")
    for name in ("zumrut", "kehribar", "gul", "leylak"):
        assert f'body[data-accent="{name}"]' in tokens, name
        assert f'body.light[data-accent="{name}"]' in tokens, name
    assert tokens.index('body[data-accent="zumrut"]') < tokens.index("body.academy, body.academy.light {")
    assert 'id="settings-accent"' in HTML
    shell_js = JS_SOURCES["js/shell.js"]
    assert "function applyAccent(" in shell_js and 'store("nova.accent", accent)' in shell_js
    assert 'applyAccent(store("nova.accent") || "")' in JS_SOURCES["js/main.js"]


def test_the_research_report_markdown_says_only_what_the_report_says() -> None:
    quickjs = pytest.importorskip("quickjs")
    context = quickjs.Context()
    context.eval(section(JS_SOURCES["js/research.js"], "const RESEARCH_UNCERTAINTY_TR = [", "\nconst RESEARCH_FRESHNESS_TR"))
    context.eval(section(JS_SOURCES["js/research.js"], "const RESEARCH_FRESHNESS_TR = {", "\nfunction researchPreset"))
    context.eval(section(JS_SOURCES["js/research.js"], "function researchReportMarkdown(", "\nasync function exportResearchReport"))
    report = {
        "question": "omuz anatomisi", "cache_hit": False, "created_at": "2026-09-20T18:00:00+00:00",
        "claims": [{"text": "Bir bulgu.", "citations": ["S1"]}],
        "sources": [{"id": "S1", "title": "Kaynak", "url": "https://example.org/a", "source": "web",
                     "freshness": "current", "excerpt": "Alıntı.", "published_at": "2026-09-01T00:00:00+00:00"}],
        "uncertainties": ["Source Hacker News was unavailable (HTTP 503)."],
    }
    markdown = context.eval("researchReportMarkdown(" + json.dumps(report) + ", " + json.dumps([{"id": "web", "label": "Web"}]) + ")")
    assert markdown.splitlines()[0] == "# Araştırma raporu: omuz anatomisi"
    assert "- Bir bulgu. _[S1]_" in markdown
    assert "### S1 · Kaynak" in markdown and "- https://example.org/a" in markdown
    assert "Web · güncel · 2026-09-01" in markdown and "> Alıntı." in markdown
    assert "- Hacker News kaynağına ulaşılamadı (HTTP 503)." in markdown
    assert "1 kaynak · canlı · 2026-09-20T18:00:00+00:00" in markdown


def test_the_months_rhythm_draws_exactly_what_the_records_say() -> None:
    quickjs = pytest.importorskip("quickjs")
    context = quickjs.Context()
    context.eval(section(JS_SOURCES["js/foundation.js"], "function esc(", "\nfunction store("))
    rhythm = JS_SOURCES["js/medical.js"]
    context.eval(rhythm[rhythm.index("function monthRhythmLevel("):])
    assert [context.eval(f"monthRhythmLevel({m})") for m in (0, 1, 14, 15, 29, 30, 59, 60, 240)] == [0, 1, 1, 2, 2, 3, 3, 4, 4]
    days = [{"date": f"2026-09-{index:02d}", "minutes": index % 3 and index or 0, "answers": index, "cards": 0} for index in range(1, 29)]
    markup = context.eval("monthRhythmMarkup(" + json.dumps(days) + ")")
    assert markup.count("mr-cell") == 28 and 'data-day="2026-09-28"' in markup
    # Day 3 logged no minutes but 3 answers: the legend counts it active,
    # so the cell wears the first shade instead of sitting empty.
    assert 'class="mr-cell l1" title="2026-09-03' in markup
    quiet = context.eval("monthRhythmMarkup(" + json.dumps(
        [{"date": "2026-09-01", "minutes": 0, "answers": 0, "cards": 0}]) + ")")
    assert 'class="mr-cell l0"' in quiet, "a truly empty day stays empty"
    assert "2026-09-05 · 5 dk · 5 soru · 0 kart" in markup
    assert "aktif gün" in markup and "dk</span>" in markup
    assert context.eval("monthRhythmMarkup([])") == ""
    # The page asks the same weekly_report action, just for 28 days.
    assert 'this.request("weekly_report", { days: 28 })' in JS_SOURCES["js/medical.js"]
    assert 'id="med-month"' in HTML


def test_the_pulse_and_the_focus_noise_are_wired_honestly() -> None:
    panels = JS_SOURCES["js/panels.js"]
    assert "const Pulse = {" in panels and 'call("system_pulse")' in panels
    assert 'State.screen !== "diagnostics"' in panels, "the pulse beats only on the diagnostics screen"
    shell_js = JS_SOURCES["js/shell.js"]
    assert "Pulse.start(); } else Pulse.stop();" in shell_js
    assert 'id="diag-pulse"' in HTML
    # The first beat shows a dash for CPU: null is a dash, never a zero.
    quickjs = pytest.importorskip("quickjs")
    context = quickjs.Context()
    context.eval(section(JS_SOURCES["js/foundation.js"], "function esc(", "\nfunction store("))
    context.eval(section(panels, "const Pulse = {", "\n/* ── diagnostics"))
    markup = context.eval('Pulse.markup({ ok: true, cpu_percent: null, memory_percent: 41.2, memory_used_gib: 6.6, memory_total_gib: 16, disk_free_gib: 208.4 })')
    assert "CPU —" in markup and "%41,2" in markup and "208,4 GB boş" in markup
    assert context.eval("Pulse.markup({ ok: false })") == ""
    # The focus noise says it is synthetic and stops before the voice stage.
    assert "const FocusNoise = {" in shell_js and "sentezlenmiş kahverengi gürültü" in shell_js
    assert "FocusNoise.playing) { FocusNoise.stop(); return; }" in shell_js
