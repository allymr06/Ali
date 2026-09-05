# JARVIS Project State

Last verified: 5 September 2026

## Current status

- Completed implementation milestone: Phase 17 — Windows packaging and installer
- Completed validation milestone: Phase 18 — final audit and delivery evidence
- Completed maintenance milestone: single-provider (Gemini) consolidation
- Completed stabilization milestone: Nova desktop shell (pywebview/WebView2),
  5 September 2026
- Completed feature milestone: manifest-based plugin runtime v1 (in-process,
  disabled by default), 5 September 2026
- Completed feature milestone: Windows system tray with single-instance
  behaviour, 5 September 2026
- Completed interface milestone: Nova cinematic interface redesign (design
  system, presence-driven core, live execution timeline, command palette,
  compact window, voice stage), 5 September 2026
- Completed feature milestone: safe-filesystem extensions (verified
  snapshots with recoverable delete and undo, dry-run plans applied by
  digest, bounded name search, critical-directory block, Nova file-access
  settings), 5 September 2026
- Completed feature milestone: Nova notification centre and attention
  routing (reminders delivered in the Nova shell, unattended replies,
  approvals and results collected, ledger warnings and screen observations,
  native notification when the window is hidden), 5 September 2026
- Next action: scheduled routines (a prompt that runs on a timer under the
  same approval gates) or plugin process isolation; code signing and a
  user-attended voice qualification remain release blockers
  (`docs/FINAL_AUDIT.md`)
- State: development release; production acceptance is not yet achieved
- Platform target: Windows 11, Python 3.12
- Automated verification: 1368 tests passing, 5 skipped (`scripts/verify.py`)
- Production readiness: not yet claimed

## Nova notification centre (5 September 2026)

Before this milestone the Nova shell never delivered reminders: the
delivery loop lived in the classic Tk window only, so a reminder created
from Nova was stored and never shown. `app/notifications/` adds two small,
UI-independent pieces. `NotificationCenter` is a bounded (200 entries),
thread-safe, session-scoped list of things that deserve attention; every
entry has a kind (`reminder`, `approval`, `reply`, `task`, `diagnostic`,
`observation`, `system`), a Turkish title, a body bounded to 600
characters, a severity, an optional target screen and a small data map,
and repeats with the same dedupe key within 60 seconds collapse into one
entry with a count. `ReminderWatch` polls `ReminderService.claim_due()` on
a daemon thread (first poll immediately, then every 10 s), so each due
reminder fires exactly once and reminders that came due while the desktop
was closed fire right after boot.

`NovaBridge` owns one centre per window session and feeds it from the
running core: due reminders; ledger events at warning level or above
(collapsed per component and name, target `Tanılama`); screen-watcher
observations (target `Görüş`); and, only while the window is unattended,
assistant replies (target `Sohbet`), approval requests (tool name and risk
only, never parameters) and finished vision or research work. The page
reports `document.visibilityState` through `set_visible`, the tray's
open/hide and pywebview's minimized/restored events set the same flag, and
for alert-worthy entries on an unattended window the bridge also raises a
native notification on its own thread (tray balloon when the icon exists,
otherwise the WinRT toast, at most four in flight); the ledger's warnings
never reach the OS. `JARVIS_NOTIFICATIONS_OS_ENABLED=false` keeps native
notifications off; the in-window centre is always on. Both decisions are
observable in the ledger: `window.visibility` (attended true/false) and
`notification.native` (delivered, channel `tray` or `toast`, title only;
the body is user content and stays out).

The page gains a bell in the top bar with the live unread count, a
popover (`Ctrl+Shift+N`, palette entry, outside click and Escape close it)
listing entries newest first with kind icon, relative time and repeat
count, per-entry open (marks read, jumps to the target screen) and dismiss,
`Tümünü okundu say` and `Temizle`, an in-page toast for each new entry, and
a system line in the conversation for reminders and screen observations as
the classic shell shows. Pushes that arrive before boot finishes are merged
after it, so a reminder due at start-up is not lost. New bridge methods:
`list_notifications`, `mark_notifications_read`, `dismiss_notification`,
`clear_notifications`, `set_visible`; new push kind `notification`.

Tests: `tests/test_notifications.py` (centre and watch) and new cases in
`tests/test_ui_nova.py`, `tests/test_nova_web.py`, `tests/test_settings.py`.
Live on this host from the Nova shell: a Gemini command created a
one-minute reminder; when it came due the bell showed `1`, the popover
listed it (`Ctrl+Shift+N`) and the conversation gained the `⏰ Hatırlatıcı`
line. A second reminder was set, the maximized window was minimized, and
the ledger recorded `window.visibility attended=false`, then
`notification.native delivered=true via=tray`, then `attended=true` when
the window came back with the badge at `1`; the same run exposed and fixed
the `maximized` event gap. Whether Windows painted the balloon could not be
observed because a full-screen video was on the desktop at the time
(Windows suppresses notifications then); the bridge-side path is covered
by the unit tests with a fake notifier. The frozen build was not rebuilt
for this milestone.

## Safe-filesystem extensions (5 September 2026)

`app/platform/windows/snapshots.py` adds `FilesystemSnapshotStore`: before a
bounded tool replaces or removes a file, its exact bytes are sealed below the
state directory (`filesystem_snapshots/<id>.bin` + `<id>.json` manifest with
root, relative path, size, SHA-256, reason, time and tool). Payloads are only
handed back after the digest and size are re-verified, the store is bounded
by count and total bytes (`JARVIS_FILESYSTEM_SNAPSHOT_MAX_ENTRIES`,
`JARVIS_FILESYSTEM_SNAPSHOT_MAX_BYTES`; oldest pruned first), and a file the
store cannot hold blocks the mutation instead of proceeding unprotected.

`BoundedFilesystemService` gains, all under the same root-and-relative-path
policy and the same confirmation gates:

- `delete_path` (HIGH, confirmation) — a real, recoverable delete: files are
  sealed then unlinked, only empty directories are removed, links are
  refused; without a snapshot store a file delete stays blocked.
- `undo_filesystem_change` (HIGH, confirmation) — writes a snapshot back to
  its original path; the file currently there is sealed first, so an undo
  can itself be undone. `list_filesystem_snapshots` is read-only.
- `write_text_file`, `copy_file` and `move_file` with `overwrite=True` seal
  the file they replace and report the snapshot.
- `search_files` (read-only) — a bounded name index per root (20 000
  entries, refreshed after 60 s or on demand), case-insensitive substring or
  glob, optional sub-path scope; links and reparse points are listed but
  never followed.
- `plan_filesystem_changes` (read-only dry run of up to 50
  write/create_directory/copy/move/delete operations under one root:
  reports per-operation readiness and conflicts, touches nothing) and
  `apply_filesystem_plan(plan_id, digest)` (HIGH, confirmation): the plan is
  single-use, expires after ten minutes, its digest must match, every target
  is re-checked against the fingerprint taken at planning time
  (`PLAN_TARGETS_CHANGED` otherwise), and execution stops at the first
  failure reporting what was applied and its snapshots.
- Critical directories can never be granted: Windows, Program Files,
  ProgramData, the JARVIS state directory, the user profile root itself (its
  subfolders remain grantable), `$Recycle.Bin` and `System Volume
  Information`; the snapshot store cannot sit inside a granted root.

The Nova settings screen gains "Dosyalar": granted roots with add (native
folder picker on the UI thread, then an in-page confirmation) and remove, and
the snapshot list with restore (confirmed in the page, `confirmed=True` on
the bridge). Grants, revocations and restores are recorded in the ledger.

Verification: `tests/test_filesystem_snapshots.py`,
`tests/test_filesystem_recovery.py`, the updated
`tests/test_bounded_filesystem.py` and the bridge tests in
`tests/test_ui_nova.py`. Live on this host from the Nova shell: a test
folder granted through the native picker and the in-page confirmation, a
Gemini command that created `deneme.txt` after the `write_text_file`
approval (content verified on disk), a second command that removed it
after the `delete_path` approval (HIGH risk shown, file gone, one sealed
snapshot in the store), the snapshot listed under Ayarlar › Dosyalar and
restored from there with its confirmation (original bytes back on disk),
and the grant removed again. The frozen build was not rebuilt for this
milestone.

## Nova cinematic interface (5 September 2026)

The Nova page was rebuilt as a design system rather than a single file:
`web/css/tokens.css` (colour, surface, radius, glow, type, motion and z-index
tokens), `base.css`, `shell.css`, `components.css`, `screens.css`, and eight
scripts under `web/js/` (`foundation`, `bridge`, `presence`, `shell`,
`conversation`, `activity`, `panels`, `main`). `shell.WEB_ASSETS` names every
file, the PyInstaller spec collects the `web/` directory recursively, and the
build script imports the list from the shell so the smoke report cannot drift.

- **Presence.** One state machine (`presence.js`) decides what JARVIS is
  doing — offline, idle, listening, understanding, thinking, tool, waiting for
  permission, speaking, interrupted, paused, error — from the bridge status,
  voice phases, live tool activity and open approvals. The topbar readout, the
  captions, the tray-paused state and the core visualization all read it.
- **JarvisCore.** An original canvas visualization: index ring, segmented arcs
  with travelling highlights, inclined orbits with satellites, radial data
  spokes that carry light while computing (biased toward the context panel
  while a tool runs), an interference field driven by the real microphone
  level while listening, and a breathing nucleus with a speech halo. It runs
  at the monitor refresh rate, drops to 30 fps when calm, pauses when hidden,
  and draws one frame under "Hareketi azalt".
- **Real data only.** Tool activity is observed through
  `ToolExecutor.subscribe` (started/finished with status, verification and
  duration), live events through `DiagnosticsService.subscribe`, the
  microphone level through `VoiceService.level_callback`. The diagnostics
  screen shows real health checks, the metric registry, admission and provider
  circuit figures, process memory/threads/uptime (CPU after the second
  sample), runtime versions and the ledger tail; anything unmeasured is shown
  as "kullanılamıyor". Boot lines report the actual subsystem state.
- **Screens.** Command centre (greeting, core, quick command, quick actions,
  recent activity, system rows), conversation (integrated JARVIS messages,
  streaming, inline activity strip, assurance chips, stored-conversation list
  with open/new/archive), tasks (step timelines), memory (grouped by type,
  search, edit, forget, permanent delete with confirmation, privacy note),
  voice (inline core, level bar, transcript; full-screen stage with phase
  readout and captions), vision, research, automation (tools grouped by
  source), trust (risk distribution, session approvals, the permission
  engine's own audit trail), diagnostics, settings (connection, appearance,
  read-only runtime configuration per subsystem, shortcuts).
- **Interaction.** Command palette (Ctrl+K: screens, actions, real approved
  applications, "JARVIS'e sor"), contextual drawer that opens itself during
  execution and settles afterwards, collapsible rail, compact always-on-top
  window (`set_compact`, geometry applied on the WinForms UI thread), in-page
  pause/resume through the same path as the tray, tray "Sesli mod" entry.
- **Honesty and safety.** The approval overlay offers only "Bir kez izin
  ver" and "Reddet" — no blanket permission exists; it shows the tool in
  Turkish, the raw tool name, the operation, the permission engine's reason,
  the request source, the expected effect and the masked parameters. Secrets
  never cross the bridge; runtime configuration is exported through an
  explicit allow-list. Memory deletion needs `confirmed=True`. Observers are
  read-only and cannot change a tool's outcome.

Verified live on this host (source, Windows 11, 1920×1080 at 125 %): boot
with real subsystem lines, every screen, palette navigation and application
launch entries, a real Gemini command that requested permission for
`launch_windows_application` (overlay shown with Turkish labels), the denial
producing the reply plus the inline "Reddedildi" pill and the drawer timeline
(İstek alındı → Anlaşılıyor → İzin istendi → Yanıt yazılıyor → Tamamlandı),
compact mode entering at the bottom-right and expanding back, the
diagnostics, memory and settings screens with live data, the tray menu
showing `Sesli mod`, and `Çıkış` ending the process cleanly. Ctrl+M opened
the voice stage with the real pipeline: the stage moved through
`DİNLİYOR` and `KONUŞUYOR` as the microphone picked up ambient speech and
JARVIS answered aloud, and Escape ended the session; a deliberate spoken
exchange still needs a person at the microphone. The demo page (`?demo=1`)
was used for task timelines, memory cards and the approval overlay in a
browser. The frozen build was rebuilt after the redesign and its smoke
report lists every page file as `nova_assets`.

Known limitations: tool descriptions and a few permission reasons that the
core registers in English are shown as-is when no Turkish mapping exists; the
compact window assumes the primary monitor; "always allow" is intentionally
absent.

## System tray and single instance (5 September 2026)

`app/ui/tray/` adds the notification-area icon for the Nova shell: `Aç`
(`Öne getir` while visible), `Duraklat`/`Devam`, `Tanılama`, `Ayarlar`, and
`Çıkış`, plus double-click to open. The icon is a WinForms `NotifyIcon` on
its own STA thread (pythonnet is already present through pywebview; no new
dependency). Closing the window hides it to the tray with a one-time
balloon; `Çıkış` runs the ordinary clean shutdown. `Duraklat` is a UI gate:
the controller refuses new commands, voice, vision, and research, stops an
active voice session, and the page shows `DURAKLATILDI`; reminders and other
background services keep running. A named mutex and event keep one desktop
per user session: a second launch activates the first and exits. A tray that
cannot start is recorded as `tray.error` and the window runs without it.
Settings: `JARVIS_TRAY_ENABLED`, `JARVIS_TRAY_CLOSE_TO_TRAY`,
`JARVIS_SINGLE_INSTANCE` (all default true). The classic Tk shell has no
tray icon (it still honours the single-instance guard).

Verified live on this host: icon in the notification area with the JARVIS
logo, balloon on close-to-tray, `Aç` restoring the window, `Duraklat`
refusing a command with the Turkish notice and `Devam` restoring it,
`Tanılama` opening that screen, `Çıkış` ending the process cleanly, and a
second launch bringing the first window forward.

## Plugin runtime v1 (5 September 2026)

`app/plugins/` adds manifest-based plugins that contribute tools through the
existing `ToolExecutor` and `PermissionEngine`; nothing is bypassed and no
core contract changed. Core touches are limited to four settings
(`plugins_enabled`, `plugins_directory`, `plugin_tool_timeout_seconds`,
`plugin_max_consecutive_failures`) and the bootstrap wiring
(`JARVISApplication.plugins`, stopped on `close()`).

- Discovery is confined to immediate subdirectories of the trusted plugins
  root, never follows links or junctions, bounds manifest size, and imports
  no code. Manifests are versioned, closed-schema, and fail closed.
- Plugins are disabled by default; enabling is an explicit, persisted user
  decision. Plugin tools are namespaced (`plugin_<id>_<tool>`), carry
  `source="plugin:<id>"`, sit at or above the `LOW` risk floor, and require
  approval when medium or high. Results are unverified by contract.
- Isolation: per-call deadline on a bounded worker pool, JSON-only bounded
  output, class-name-only error reporting, and quarantine after consecutive
  failures with explicit re-enable. Plugin code receives only its id,
  version, a private data directory, and a bounded ledger logger.
- Honest limit: in-process Python cannot be sandboxed; a plugin is trusted
  code the user installed and enabled. Process isolation, a settings UI,
  and signing are later versions.
- Tests: `tests/test_plugin_manifest.py`, `test_plugin_discovery.py`,
  `test_plugin_runtime.py`, `test_plugin_security.py`,
  `test_plugin_bootstrap.py` with the safe `tests/fixtures/plugins/echo`
  sample.


Nova (`app/ui/nova/`) is now the default desktop shell: a pywebview window
hosting `web/index.html` in Microsoft Edge WebView2, with every animation on
the browser compositor and every fact coming from the Python core through the
`NovaBridge` JS API and the `window.NOVA.push(...)` channel. The Tkinter shell
remains available with `python -m app.ui --classic` (or `JARVIS.exe --classic`)
and is used automatically when pywebview is not installed.

Stabilization changes, all covered by tests:

- **No silent demo.** The page previously fell back to sample data when the
  Python bridge did not answer within 1.6 s. It now waits up to 10 s for
  `pywebviewready` and, on failure, shows an explicit "çekirdek köprüsü
  kurulamadı" screen with a retry button; nothing is simulated. The demo bridge
  runs only when the page is opened directly in a browser with `?demo=1`, never
  inside pywebview, and is labelled DEMO in the top bar, the boot log, every
  reply, and a persistent toast.
- **Frozen asset resolution.** `resolve_web_root()` looks below
  `sys._MEIPASS/app/ui/nova/web` first, then the source tree, and reports a
  missing file set instead of opening an empty window. `installer/JARVIS.spec`
  bundles the three page files at that path, the frozen smoke test records them
  as `nova_assets`, and `scripts/build_windows.py` refuses a report without all
  three.
- **Race-free bridge.** The busy check and the submission share one lock, so a
  double send cannot slip past the guard; voice start/stop and shutdown are
  guarded the same way. Approval requests are single-use tokens that fail
  closed on timeout, on shutdown, on a non-boolean answer, and when the page is
  not ready. Window close releases the bridge, the async runner, and the
  application exactly once, whether the `closed` event fires or `webview.start`
  simply returns. The bridge lock is re-entrant: when a background task
  finishes before its completion callback is registered, concurrent.futures
  runs that callback synchronously on the submitting thread, which used to
  deadlock the pywebview worker (found as an intermittent test hang, fixed
  with a deterministic regression test); shutdown acquires the lock with a
  deadline so it can never hang on a stuck worker.
- **Credential deletion needs two explicit steps.** The Settings screen opens
  an in-app confirmation dialog (separate from the tool-approval modal) and the
  bridge ignores `delete_api_key()` unless it is called with `confirmed=True`.
- **Persistent WebView2 profile.** The window runs with `private_mode=False`
  and `storage_path=%LOCALAPPDATA%\JARVIS\webview`, so the theme and the
  in-app "Hareketi azalt" switch survive restarts (verified live). The OS-wide
  `prefers-reduced-motion` setting is deliberately not inherited.
- **Reading is never interrupted.** Chat auto-scrolls only when the reader is
  already at the bottom; otherwise a "yeni mesaj" pill appears. Arrow, Page,
  Home, and End keys scroll the active screen when no input has focus, and a
  send attempted while JARVIS is busy keeps the draft and says so.
- **Runtime detection.** `detect_webview2_runtime()` asks Microsoft's
  WebView2Loader (bundled with pywebview) and then the Evergreen registry
  entries before any window exists; without a runtime `launch_desktop`
  records a warning event, posts a Turkish system message, and opens the
  classic shell instead of crashing out of pywebview.
- **Packaged entry point.** `JARVIS.exe --classic` is accepted, and the
  `.venv` is now expected to be re-synchronized from `requirements-dev.txt`
  whenever `pyproject.toml` changes (`pip check` cannot detect a dependency
  that was declared but never installed).
- **Tests.** `tests/test_ui_nova.py` (34 tests) drives `NovaBridge` against the
  real controller with a recording window; `tests/test_nova_web.py` (18 tests)
  parses `nova.js` with QuickJS, checks that every JavaScript bridge call
  matches a Python method and arity, that the demo bridge mirrors the Python
  API, that demo mode is opt-in only, and that the page declares the failure
  and confirmation UI. The packaging tests require the Nova assets in the spec
  and in the smoke report. `scripts/verify.py`: 1197 passed, 1 skipped.

### Verified live on this host (5 September 2026)

Source launch (`python -m app.ui`, real Gemini credential from Credential
Manager, WebView2 Runtime 152): boot into Nova; every screen reachable by rail
click and Alt+digit; mouse-wheel, PageUp/PageDown, Home/End scrolling; a text
command answered by the core (about 10 s round trip, tool-verified time
query); a reply arriving while scrolled up left the view in place and showed
the pill; Ctrl+M opened the text-free full-screen HUD and, with no speech, the
session closed itself with the honest "Ses algılanmadığı için sesli modu
kapattım" notice; Vision and Research (both disabled in this configuration)
failed closed with visible messages; a clipboard write raised the ORTA
approval modal with masked parameters, was denied, produced "İşlem iptal
edildi; bilgisayarında değişiklik yapılmadı", and left the clipboard
untouched; the Settings connection test succeeded against the live model; the
delete-key dialog was cancelled with Escape; the motion switch persisted
across a restart; Alt+F4 left no process behind and an empty stderr. The
frozen `JARVIS.exe` from the rebuilt package also opened Nova and closed
cleanly.

Not re-qualified in this pass: a spoken voice turn (cloud Charon speech and
the local Windows fallback). The host has no microphone input JARVIS could be
driven with unattended, so that path keeps its 23 August qualification from
the classic shell and still needs a run with the user's own microphone.

## Provider consolidation (22 August 2026)

JARVIS now ships exactly one production AI provider: **Gemini**, reached through
Google's OpenAI-compatible API surface.

- Removed: the Ollama provider, its warm keeper, its hybrid chat/tool routing
  policy, and the OpenAI speech adapters. `app/providers/openai.py` remains only
  as the shared adapter base class that `GeminiProvider` extends.
- `MockProvider` remains the deterministic offline provider for the automated
  suite. `create_application()` registers it, and makes it the default, only
  when `settings.default_provider == "mock"`. The desktop cannot select it.
- Provider fallback is disabled by construction. With one production provider
  there is nothing to fall back to, and falling back to the mock provider would
  replace a real failure with a convincing fiction.
- `DeterministicToolRouter` is now provider-neutral. It previously refused to
  route unless the active provider was Ollama, which silently disabled the
  latency optimization after the migration. Every candidate it routes to is a
  `READ_ONLY` observation tool and the permission engine still authorizes the
  call, so removing the provider gate removes a model round trip, never a
  security boundary.
- `vision_model` is now an optional dedicated Gemini vision model instead of a
  dead `gpt-4o` default. When set and different from the general model, `VISION`
  requests route to it exclusively.
- Dead configuration knobs were removed rather than left to mislead:
  `voice_stt_model`, `voice_tts_model`, `voice_tts_voice`, and
  `provider_fallback_enabled` had no remaining consumer, and the retired
  `JARVIS_API_KEY`, `JARVIS_API_BASE_URL`, and `JARVIS_OPENAI_MODEL`
  environment variables are no longer read.
- The approved-application fast-action router, previously gated behind the
  Ollama hybrid mode, now activates whenever Windows integrations are enabled.
  It short-circuits only registered application launches, and the launched
  process is still verified by PID and identity.
- Stale environment from older builds cannot break startup: unknown
  `JARVIS_DEFAULT_PROVIDER` values fall back to Gemini, the desktop ignores
  stale default-model and voice-provider variables, the retired `gpt-4o`
  vision default is dropped, and voice automatic selection falls back to
  Gemini when the text provider has no speech adapters.

### Verified on this host

- Native Tcl/Tk 8.6.15 initializes, and all eleven desktop screens render from
  source on a normal Windows 11 account. The sandbox limitation recorded in the
  Phase 13 and Phase 17 notes below does not apply to this machine.
- `scripts/verify.py` passes dependency integrity, bytecode compilation, and the
  complete deterministic suite.
- Voice was qualified live on 23 August with the real desktop, real
  Gemini APIs, and a scripted microphone: capture, 1.3s transcription,
  short-reasoned reply, sentence-pipelined speech, HUD states, earcons,
  and a graceful, explained silence-close. When cloud synthesis is
  rate-limited the turn retries once and then answers through the local
  Windows SAPI voice with an honest notice — a voice turn can no longer
  end silently.
- The full release pipeline ran end to end on 22 August: PyInstaller build,
  frozen smoke test (`ok=true`, `screens=11`, `tcl=8.6.15`), portable ZIP,
  Inno Setup installer compile, silent clean install, installed-location smoke
  test, in-place reinstall, and silent uninstall with user data preserved.

## Application integrations and live awareness (23 August 2026)

JARVIS now drives real applications and watches the screen. Every
capability is a permission-checked tool; nothing bypasses the approval
gate or the verification contract.

- **Spotify**: transport control through media keys verified against the
  window title, search deep links, and an optional Web API tier behind
  one-time PKCE OAuth for exact playback, private playlist creation, and
  listening statistics.
- **WhatsApp**: contact book, deep links that prefill without sending,
  chat and conversation reading through the native UIA3 accessibility
  tree, HIGH-risk approval-gated sending that reports PARTIAL when the
  send button cannot be verified, and a bounded delegation agent that
  answers a named contact on the user's behalf with draft screening for
  credentials and commitments.
- **System**: http/https-only browser navigation, web search, volume.
- **Reminders**: persistent SQLite reminders with exactly-once delivery
  and native toast notifications.
- **Screen watching**: continuous observation with local 12x12 luminance
  change detection (0.11ms per frame) that calls the vision model only
  on real change; frames are discarded immediately after signature.

Hardening from live use on 23 August 2026: Spotify plays a named
track with no account setup by driving the desktop app's own search
UI (verified against the window title); WhatsApp launches itself when
closed and opens chats by their visible list name with an empty
contact book; every chat render lands at the newest message instead of
the top; and PowerShell output is forced to UTF-8 so Turkish titles
survive.

Intent handling was hardened alongside: unresolved phrasings expose the
full tool inventory instead of failing closed, tool-bearing turns
escalate to the stronger action model with a graceful rate-limit
fallback, and an action-integrity directive forbids claiming an action
without calling its tool.

Local Turkish speech now goes through WinRT ("Microsoft Tolga"), which
SAPI does not expose, and races cloud synthesis so the reply starts with
whichever source answers first.

## Voice quality and integration robustness (23 August 2026, session 2)

- The first spoken sentence now gives the high-quality cloud voice a
  bounded head start (`voice_cloud_grace_seconds`, default 3.0s):
  within the window the cloud voice wins even when the instant local
  voice finished first, so the robotic Windows voice is heard only
  during real outages or past-deadline slowness. 0 restores the pure
  latency race.
- Every synthesis request carries a JARVIS persona style directive
  (`voice_tts_instructions` now defaults on), and the local fallback
  is bilingual: replies without Turkish letters or everyday Turkish
  words are spoken by the English Windows voice instead of Tolga
  spelling English out phonetically.
- End-of-turn silence is 1.5s (was 0.9s), per user tuning.
- Spotify `play_track` works with zero account setup: when no Web API
  token exists (or no active device is registered) it drives the
  desktop app itself — search deep link, then the top result's play
  button through UI Automation — and verifies via the window title.
  Verified live: both an artist query and a specific-song query.
- WhatsApp launches itself when closed, and chats are reachable by
  their visible chat-list name with an empty contact book (real-click
  row activation, composer verification). Typed drafts are
  whitespace-collapsed so a newline can never act as Enter from the
  non-sending open-chat tool, and typing re-fronts the window and
  strips control characters. comtypes NULL window pointers no longer
  raise.
- PowerShell output is forced to UTF-8 so Turkish titles survive.
- Chat auto-scroll goes through the scroller's own offset bookkeeping;
  replies no longer bounce the conversation to the top.
- Host voice-preference environment variables are scrubbed in the
  test suite so the suite stays hermetic. (An ElevenLabs adapter was
  built this session and fully removed the next: the user settled on
  a single built-in voice instead of a purchased one.)

## Voice identity, Turkish recognition, and real memory (23 August 2026, session 3)

- **Charon is the single voice of JARVIS.** After hearing samples
  the user settled on Charon; it is the default everywhere, the only
  registered cloud voice path, and the same multilingual voice speaks
  both Turkish and English. The stray `JARVIS_VOICE_GEMINI_TTS_VOICE`
  user-environment override was deleted so the setting's default is
  authoritative.
- **Recognition is pinned to Turkish** (`voice_language` defaults to
  `tr` with a firm transcription directive), ending wrong-language
  transcripts, and one transient transcription failure is retried
  before the turn can fail.
- **Voice and text now share one conversation.** Voice turns appear in
  the chat history, persist with the conversation store, and are
  restored on restart; asking in text about something said aloud
  works.
- **Long-term memory actually captures now.** Three layers: the
  explicit-prefix analyzer understands more Turkish ("unutma", "not
  al", "aklında tut") plus identity statements; the policy's
  preference path writes (it used to be dead code because the engine
  demanded an analyzer candidate); and an automatic post-turn model
  pass (`memory_auto_capture_enabled`, lite model) distills durable
  personal facts into third-person memories with paraphrase-aware
  deduplication, off the latency path, never able to fail a turn.
  Verified live against real Gemini: a casual sentence about the
  user's project became a stored memory.

## Implemented architecture

- `CoreEngine` provides bounded request, provider, conversation, memory, and
  tool orchestration.
- `ProviderGateway` provides capability-aware routing, explicit overrides,
  timeout, retry, health accounting, and normalized streaming; cross-provider
  fallback is intentionally disabled.
- `ConversationEngine` owns validated conversation lifecycle, complete tool-call
  groups, bounded context, and summaries.
- `ToolExecutor` owns strict input/output contracts, dynamic discovery,
  lifecycle, permission enforcement, timeout, cancellation, concurrency, and
  provider schema generation.
- `ExecutionService` owns plan-step execution, budgets, retries, verification,
  events, snapshots, recovery, and result propagation.
- `PermissionEngine` owns deterministic policy, parameter rules,
  least-privilege scopes, and bounded decision auditing.
- `ApprovalGate` and `ApprovalStore` own immutable, expiring, action-bound
  approval lifecycle. Final grant validation occurs at the tool boundary.
- `WindowsIntegrationService` owns trusted application registration, native
  system/process observation, safe process creation, and launch verification.
- `SQLiteMemoryStore` owns schema-versioned durable long-term memory,
  transactional persistence, integrity checks, and verified backup/restore.
- `MemoryManager` and `MemoryService` own safety screening, provenance,
  freshness, relevance, lifecycle, retention, and user-visible controls.
- `SQLiteTaskStore`, `TaskManager`, and `DurableTaskRuntime` own durable task
  identity, progress, subtasks, safe pause/resume, cancellation, and restart
  recovery through isolated plan and execution snapshots.
- `VoiceService` and `VoiceSession` own bounded microphone turns, explicit
  state, wake-word gating, interruption, speech provenance, and audio disposal.
- `VisionService`, `VisionConsentGate`, and `WindowsScreenSource` own one-use
  capture consent, native bounded capture, redaction, freshness, provenance,
  interruption, and vision-capability routing.
- `ResearchService`, `URLPolicy`, and `SafeWebFetcher` own opt-in search,
  IP-pinned safe retrieval, untrusted-content isolation, provenance, freshness,
  bounded multi-source synthesis, uncertainties, and citation integrity.
- `DesktopController` owns responsive background dispatch, live service state,
  and explicit user input while preserving all Core security boundaries; the
  Nova shell (`app/ui/nova`, pywebview/WebView2) is the default presentation
  and the classic Tk `DesktopWindow` remains behind `--classic`.
- `DiagnosticsService`, `DiagnosticLedger`, `MetricRegistry`, and
  `HealthRegistry` own sanitized structured events, tamper evidence, bounded
  low-cardinality metrics, and timeout-contained live health checks.
- `AdmissionController` and per-provider `CircuitBreaker` instances own bounded
  Core concurrency, queue deadlines, overload failure, dependency isolation,
  and single-probe recovery.
- The Windows release pipeline owns pinned PyInstaller analysis, bundled Tcl/Tk,
  logo resources, frozen smoke evidence, portable packaging, Inno Setup source,
  and artifact hashes.

## Completed phases

1. Phase 0 — workspace inspection and architecture foundation
2. Phase 1 — project bootstrap, configuration, and core contracts
3. Phase 2 — bounded JARVIS Core and execution runtime
4. Phase 3 — AI provider gateway and model routing
5. Phase 4 — conversation engine and context lifecycle
6. Phase 5 — versioned, dynamically discoverable tool system
7. Phase 6 — scoped permission policy and bound approval security
8. Phase 7 — native Windows observation and verified application launch
9. Phase 8 — durable, searchable, provenance-aware long-term memory
10. Phase 9 — durable, resumable, bounded task and agent execution
11. Phase 10 — bounded voice input, speech output, and interruption
12. Phase 11 — consent-bound vision and screen understanding
13. Phase 12 — source-grounded, SSRF-resistant web research
14. Phase 13 — native desktop UI from the approved visual prototype
15. Phase 14 — diagnostics, observability, health, and tamper-evident events
16. Phase 15 — complete-system acceptance and security regression gates
17. Phase 16 — bounded load, overload control, and provider circuit recovery
18. Phase 17 — reproducible Windows packaging, branding, and release evidence
19. Phase 18 — final acceptance audit and production-gap classification

## Current security decisions

- `READ_ONLY` and `LOW` operations are allowed by default.
- `MEDIUM` and `HIGH` operations require a valid bound approval grant.
- `CRITICAL` operations are denied by the default policy.
- Tool or plan metadata cannot lower effective risk.
- Parameter rules may elevate risk or force confirmation/denial, never allow.
- A raw confirmation boolean is not authorization.
- Approvals bind operation, tool version, parameters, task, plan, step, and
  expiry and are validated immediately before handler execution.
- Invalid filters, rules, scopes, grants, and approval parameters fail closed.
- Permission audit records do not retain tool parameter values.
- Windows launch accepts only registered local `.exe` definitions and verifies
  the returned PID and process identity before reporting success.
- Durable memory rejects credential, private-key, and payment-card material.
- Soft forgetting requires `MEDIUM`-risk approval; permanent deletion requires
  `HIGH`-risk approval and both are revalidated at the tool boundary.
- Task pause, resume, and cancellation require bound approval; interrupted work
  is paused on startup and never assumed complete.
- Voice is disabled by default, microphone capture is duration-bounded, and raw
  audio is overwritten and released after transcription unless retention is
  explicitly enabled.
- Vision is disabled by default. Every capture requires a short-lived, exact,
  one-use consent grant and configured privacy masks run before model access.
- Research is disabled by default. Every URL and redirect is DNS-validated,
  pinned to a public IP, content-bounded, and treated as untrusted evidence.
- Diagnostics redact secret-bearing fields and values, hash trace identity,
  bound event/metric growth, and never expose health-check exception details.
- Core work and queued callers are bounded; retryable provider outages open a
  circuit and later admit exactly one recovery probe.

## Known limitations and deferred work

- Window management, clipboard, notifications, and broader application-specific
  controls remain future Windows extensions.
- Gemini requires a configured API key. Without one the desktop reports a
  classified configuration error instead of answering.
- Filesystem search covers names only (no content index); plans work within
  one root; the compact window assumes the primary monitor.
- The notification centre is session-scoped (nothing is persisted across
  restarts; the reminder store itself keeps its delivered flag). Native
  notifications are best effort (tray balloon or a PowerShell WinRT toast)
  and cannot deep-link back into the window; the tray's `Aç` does.
- Python cannot forcibly stop an already-running synchronous worker thread.
  Timeout results explicitly report when side effects may continue.
- The tray menu offers `Sesli mod`, which opens the voice screen and starts
  the same session as the page's microphone button.
- The tray icon exists for the Nova shell only; the classic Tk shell keeps
  the single-instance guard but has no icon. `Duraklat` does not stop
  reminders or the screen watcher.
- The plugin runtime is in-process (no operating-system sandbox) and has no
  settings-screen UI yet; plugins are enabled through `PluginRuntime.enable()`.
- Publisher code signing is not configured.
- Nova needs the Microsoft Edge WebView2 Runtime (shipped with Windows 11).
  When pywebview is missing or no runtime is detected, the classic shell opens
  with a Turkish notice in the chat and a `nova.unavailable` warning in the
  diagnostics ledger; nothing crashes silently.
- Voice from the Nova shell was exercised without speech input only; a spoken
  cloud (Charon) and local-fallback turn still needs the user's microphone.

## Verified Phase 7 vertical slice

Implement the first real Windows vertical slice through the existing security
and verification boundaries:

```text
User request
  -> provider tool call
  -> registered Windows tool
  -> permission/approval decision
  -> native Windows action
  -> independent state verification
  -> structured result
  -> natural response
```

The real Notepad launch path was executed locally, verified by its new PID and
process identity, then the test-created process was closed. Native system and
process observations were also executed successfully.

## Backup policy

After every completed phase, the verified project is mirrored to:

`C:\Users\MeGaComputers\Documents\Codex\JARVIS_BACKUPS\JARVIS`

Generated virtual environments, caches, transient logs, and temporary runtime
data are excluded because they are reproducible or non-authoritative.

## Verified Phase 8 vertical slice

The application persisted a memory to SQLite, closed the store, rebuilt the
application against the same database, recalled the memory with its source and
freshness metadata, created an integrity-checked backup, and restored that
backup into a separate database. Corrupt input failed closed. Concurrent
writes, expiry filtering, sensitive-data blocking, soft forgetting, and
approval-gated permanent deletion are covered by regression tests.

## Verified Phase 9 vertical slice

Completed. A real two-process validation persisted a running two-step task,
restarted the task manager, detected the interruption as paused, resumed from
the second step without repeating the verified first step, completed the task,
and restored its terminal state from an integrity-checked database backup.

## Verified Phase 10 vertical slice

Completed. A deterministic end-to-end voice turn captured bounded PCM audio,
converted it to WAV for transcription, applied an exact optional wake-word
gate, entered Core as a `VOICE` request, synthesized the response as WAV, and
sent it to the audio output. Tests verify interruption while listening,
processing, and speaking; timeout and failure classification; device closure;
audio disposal; provider limits; and single-session admission without network,
credentials, or physical audio hardware.

## Verified Phase 11 vertical slice

Completed. A deterministic screen frame passed through explicit one-use
consent, bounded capture, user-selected and automatic taskbar redaction, PNG
encoding, freshness verification, SHA-256 provenance, the real `CoreEngine`,
and capability-aware routing to a vision provider. The image entered Core as a
`VISION` request and was cleared after analysis. Tests cover consent tampering,
single use, stale images, invalid regions, timeouts, interruption, retention,
native-source boundaries, OpenAI image normalization, and conversation-history
privacy without network access or capture of the user's real desktop.

## Verified Phase 12 vertical slice

Completed. Deterministic SearXNG results pass through safe-result filtering,
bounded concurrent collection, source extraction, freshness classification,
cross-checking, synthesis, citation validation, and explicit uncertainties.
Tests verify public-IP pinning, redirect revalidation, IPv4/IPv6 SSRF defenses,
download and content limits, injection indicators, source hashes and timestamps,
strict tool contracts, and offline bootstrap behavior without network access.

## Verified Phase 13 vertical slice

The approved HTML prototype was translated into a native monochrome desktop
shell with eleven screens, collapsible navigation, live context, command
composer, two themes, and explicit voice/vision/research controls. Prototype
mock values were replaced by live application snapshots. Text commands pass
through the real Core; optional capabilities fail closed; one-capture vision
requires a user-visible confirmation immediately before consent creation.

The development harness has no Tcl data files, so this phase verifies the UI
through controller integration, complete state/render-module compilation, and
import-safe native presentation code. The packaged runtime smoke test remains a
mandatory Phase 17 acceptance check.

## Verified Phase 14 vertical slice

Core request start and completion events enter a sanitized, bounded,
hash-chained ledger with stable non-reversible correlation. Successful requests
update counters and duration summaries; provider failures record only a stable
error class. Concurrent live checks observe Core, provider registration, memory,
durable tasks, and event-ledger integrity under individual timeouts. Read-only
tools expose health, events, and metrics, and the desktop diagnostics view shows
the live ledger count and integrity result.

## Verified Phase 15 acceptance gate

The single-command verifier passed dependency integrity, complete bytecode
compilation, and all 795 tests under a fixed non-default hash seed. The offline
system acceptance path covered durable SQLite memory/tasks, Core, conversation,
UI state, diagnostics, metrics, health, and event integrity. All application
modules imported without external actions; every tool contract was unique,
closed-schema, versioned, JSON serializable, and risk-consistent. Static security
regressions confirmed no shell-enabled subprocess or dynamic `eval`/`exec` path.

## Verified Phase 16 load and recovery slice

Core admission limits active and queued work before any memory, provider, or
tool processing. Saturation, queue timeout, and cancellation release accounting
correctly and record content-free diagnostics. One hundred parallel mock Core
requests complete under an outer five-second budget without leaked leases.
Retryable provider failures open an independent circuit, suppress further remote
calls, admit only one half-open probe, and close or reopen from its verified
result. Provider circuit and admission state are included in live health checks.

## Phase 17 packaging evidence

The pinned Windows build produces a versioned, branded onedir executable and a
portable ZIP. The executable contains the approved monochrome JARVIS icon,
Windows version metadata, manifest, `_tkinter`, Tcl/Tk DLLs, complete Tcl/Tk
script data, voice binaries, and application documentation. The build pipeline
validates controlled cleanup, archive topology, static runtime completeness,
artifact hashes, signing status, and strict native smoke evidence.

All 817 source tests pass. The frozen process starts and reaches Tk creation,
but this sandbox's native Tcl file API reports `init.tcl` unavailable even while
Python and PowerShell read the same bundled file. The release manifest records
this as `environment_limited`, `ok=false`, and `native_ui_rendered=false`.
Inno Setup 7.0.2 was downloaded from its immutable official release and its
Pyrsys B.V. Authenticode signature verified, but its compiler installation is
blocked by the same sandbox profile-folder limitation. No production-readiness
claim is made until both gates pass on a normal Windows account.

## Phase 18 final validation

Completed. The single-command source verifier passes dependency integrity,
bytecode compilation, and all 817 deterministic tests. A dated runtime
dependency audit reports no known vulnerabilities. Static review found no
forbidden dynamic/shell execution path; abstract-provider `NotImplementedError`
methods and best-effort cleanup handlers are intentional boundaries rather than
unfinished product stubs.

The authoritative 28-item acceptance matrix is in `docs/FINAL_AUDIT.md`: 18
items pass, 9 are conditional on configured hardware/external services or a
normal Windows packaging host, and 1 is missing (the complete safe filesystem
tool family). JARVIS remains a development release until the recorded blockers
are resolved.

## Post-Phase 13 desktop and API configuration refresh

The native desktop was comprehensively redesigned around a restrained
black/white/grayscale system while preserving all eleven live runtime views.
The new shell includes scroll-safe content, clearer visual hierarchy, a
collapsible animated navigation rail, a reduced-motion control, a live status
pulse, improved conversation presentation, and keyboard-first operation.
`Enter` submits the composer, `Shift+Enter` inserts a line break, and additional
navigation, focus, theme, and help shortcuts are available through `F1`.

The Settings screen accepts the Gemini API key through a masked field, selects
the model, tests model access, saves non-secret preferences atomically, and
activates a rebuilt runtime without restarting the desktop. The secret is stored
only in Windows Credential Manager under `JARVIS/Gemini API`; it is never
written to the repository or preferences JSON. Mock echo responses are blocked
in the user interface and replaced by a clear configuration path. A live
provider failure can no longer fall back to the development-only mock provider,
so authentication, model, quota, and network errors remain visible.

## Gemini provider integration

Gemini is the sole production provider, reached through Google's
OpenAI-compatible API endpoint. Its credential lives in its own Windows
Credential Manager record. The default model is `gemini-3.5-flash-lite`, and an
optional `JARVIS_VISION_MODEL` routes `VISION` requests to a separate Gemini
model when the two differ.
