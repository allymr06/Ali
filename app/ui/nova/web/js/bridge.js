/* ════════════════════════════════════════════════════════════════════
   JARVIS · NOVA — bridge
   The pywebview bridge (window.pywebview.api), the explicit ?demo=1
   bridge, and the push channel Python drives (window.NOVA.push).
   ════════════════════════════════════════════════════════════════════ */
"use strict";

/* ── demo data (only with ?demo=1 outside pywebview; always labelled) ── */

const DEMO_SNAPSHOT = {
  provider: "gemini", model: "gemini-2.5-pro",
  memory_count: 3, task_count: 2, tool_count: 14, enabled_tools: 12,
  voice_available: true, vision_available: true,
  research_available: true, windows_available: true,
  diagnostic_event_count: 148, diagnostic_integrity_valid: true,
  tasks: [
    { task_id: "demo-1", goal: "Haftalık sistem raporunu derle", status: "running",
      progress: 0.62, current_step: "Tanılama olayları özetleniyor", error: null,
      created_at: "2026-09-05T10:00:00", updated_at: "2026-09-05T10:20:00",
      steps: [
        { step_id: "s1", name: "Kaynakları topla", status: "completed", error: null },
        { step_id: "s2", name: "Tanılama olaylarını özetle", status: "running", error: null },
        { step_id: "s3", name: "Raporu yaz", status: "queued", error: null },
        { step_id: "s4", name: "Sonucu doğrula", status: "queued", error: null },
      ] },
    { task_id: "demo-2", goal: "Ses profillerini yeniden eğit", status: "paused",
      progress: 0.25, current_step: null, error: null,
      created_at: "2026-09-04T09:00:00", updated_at: "2026-09-04T09:30:00", steps: [] },
  ],
  memories: [
    { memory_id: "m1", content: "Ali her zaman Türkçe iletişim tercih ediyor.", memory_type: "preference",
      source: "user", freshness: "current", confidence: 0.98, importance: 0.9, created_at: "2026-09-01T08:00:00", updated_at: "2026-09-01T08:00:00" },
    { memory_id: "m2", content: "Birincil çalışma dizini C:\\Users\\MeGaComputers\\JARVIS.", memory_type: "context",
      source: "inference", freshness: "current", confidence: 0.92, importance: 0.6, created_at: "2026-09-02T08:00:00", updated_at: "2026-09-02T08:00:00" },
    { memory_id: "m3", content: "Gemini tek üretim sağlayıcısı olarak yapılandırıldı.", memory_type: "fact",
      source: "system", freshness: "stale", confidence: 0.85, importance: 0.5, created_at: "2026-08-20T08:00:00", updated_at: "2026-08-20T08:00:00" },
  ],
  tools: [
    { name: "read_text_file", description: "Kök izinli dosya okuma", risk: "low", enabled: true, source: "platform:windows" },
    { name: "write_text_file", description: "Kök izinli dosya yazma", risk: "high", enabled: true, source: "platform:windows" },
    { name: "launch_windows_application", description: "Doğrulanmış uygulama başlatma", risk: "high", enabled: true, source: "platform:windows" },
    { name: "read_windows_clipboard", description: "Pano içeriğini okuma", risk: "medium", enabled: false, source: "platform:windows:clipboard" },
    { name: "watch_screen_start", description: "Onaylı ekran izleme", risk: "critical", enabled: true, source: "integration:vision" },
    { name: "research_web", description: "SSRF korumalı web araması", risk: "medium", enabled: true, source: "core:research" },
    { name: "search_memories", description: "Hafızada arama", risk: "read_only", enabled: true, source: "core:memory" },
  ],
};

const DEMO_NOTIFICATIONS = [
  { notification_id: "demo-n1", kind: "reminder", title: "Hatırlatıcı", body: "DEMO: Toplantı notlarını gözden geçir.",
    severity: "info", created_at: new Date().toISOString(), updated_at: new Date().toISOString(),
    target: null, reference: "demo-r1", data: {}, count: 1, read: false },
  { notification_id: "demo-n2", kind: "diagnostic", title: "Uyarı", body: "DEMO: providers · circuit.opened: örnek uyarı.",
    severity: "warning", created_at: new Date(Date.now() - 3600_000).toISOString(), updated_at: new Date(Date.now() - 3600_000).toISOString(),
    target: "diagnostics", reference: null, data: { component: "providers", name: "circuit.opened" }, count: 2, read: true },
];

const DEMO_ROUTINES = [
  { routine_id: "demo-rt1", name: "Sabah özeti", prompt: "DEMO: bugünkü hatırlatıcılarımı ve takvimi özetle", schedule: "her gün 09:00",
    schedule_kind: "daily", schedule_value: "09:00", conversation_id: null, next_run_at: new Date(Date.now() + 3600_000).toISOString(),
    next_run_local: "yarın 09:00", last_run_at: null, last_run_local: null, last_outcome: null, last_summary: null, run_count: 0 },
];

const DEMO_RUNTIME = {
  version: null, python: "3.12", platform: "Windows 11", webview2: null, user_name: "Ali",
  started_at: new Date().toISOString(), conversation_id: "demo-conv-1",
  configuration: { voice_enabled: true, voice_wake_word: "jarvis", voice_require_wake_word: false,
    voice_language: "tr", voice_gemini_tts_voice: "Charon", voice_trailing_silence_seconds: 1.5,
    voice_provisional_silence_seconds: 0.6, vision_enabled: true, research_enabled: true,
    windows_integrations_enabled: true, memory_auto_capture_enabled: true, tray_enabled: true,
    tray_close_to_tray: true, single_instance_enabled: true, plugins_enabled: false, approval_ttl_seconds: 300,
    notifications_os_enabled: true },
  applications: [{ id: "spotify", name: "Spotify" }, { id: "notepad", name: "Not Defteri" }],
  state_directory: "(demo)",
};

/* Mirrors NovaBridge's public API one-to-one (tests enforce this). Every
   response says it is a demo; nothing here performs a real action. */
const DemoBridge = {
  async boot() {
    State.demo = true;
    return {
      snapshot: DEMO_SNAPSHOT,
      settings: { provider: "gemini", model: "gemini-2.5-pro",
                  credential_configured: true, credential_required: true },
      messages: [
        { role: "user", text: "Jarvis, sistem durumu nedir?" },
        { role: "assistant", text: "DEMO: Bu örnek bir yanıttır; çekirdek bağlı değil. " +
          "Arayüzün her karesi gerçek, verileri değil." },
      ],
      voiceMessages: [],
      status: READY,
      paused: false,
      compact: false,
      runtime: DEMO_RUNTIME,
      conversations: [
        { conversation_id: "demo-conv-1", title: "Jarvis, sistem durumu nedir?", status: "active",
          turn_count: 2, created_at: new Date().toISOString(), updated_at: new Date().toISOString(), active: true },
      ],
      fileRoots: { available: true, roots: [
        { root_id: "belgeler-1a2b3c4d5e", name: "Belgeler", path: "C:\\Users\\Ali\\Documents" },
      ] },
      notifications: { items: DEMO_NOTIFICATIONS, unread: 1, total: DEMO_NOTIFICATIONS.length },
      routines: { available: true, routines: DEMO_ROUTINES },
    };
  },
  async submit_command(text) {
    setTimeout(() => NOVA.push({ kind: "tool_activity", payload: { phase: "started",
      execution_id: "demo-x", tool: "get_windows_system_info", operation: null, at: Date.now() } }), 400);
    setTimeout(() => NOVA.push({ kind: "tool_activity", payload: { phase: "finished",
      execution_id: "demo-x", tool: "get_windows_system_info", operation: null, at: Date.now(),
      status: "success", verified: true, message: "Demo", failed: false, duration_ms: 640 } }), 1100);
    setTimeout(() => {
      const reply = "Demo modundayım — çekirdek bağlı değil, hiçbir eylem " +
        "yapılmadı. Yine de bu arayüzün her karesi gerçek: 120 Hz'e kadar " +
        "akıcı animasyon, 4K keskinliğinde vektör çizim.\n\nKomutun şuydu: “" + text + "”";
      let i = 0;
      const timer = setInterval(() => {
        i += 6 + Math.floor(Math.random() * 10);
        NOVA.push({ kind: "stream", payload: { text: reply.slice(0, i) } });
        if (i >= reply.length) {
          clearInterval(timer);
          NOVA.push({ kind: "reply",
                      payload: { role: "assistant", text: reply, metadata: { assurance_level: "demo" } } });
          NOVA.push({ kind: "busy", payload: { busy: false, status: READY } });
        }
      }, 50);
    }, 1300);
    return { ok: true };
  },
  async list_conversations() {
    return { ok: true, active: "demo-conv-1", conversations: (await DemoBridge.boot()).conversations };
  },
  async open_conversation() { return { ok: false, error: "Demo modu: konuşma açılmadı." }; },
  async new_conversation() { return { ok: true, conversation_id: "demo-conv-2", messages: [] }; },
  async archive_conversation() { return { ok: false, error: "Demo modu: konuşma arşivlenmedi." }; },
  async start_voice() {
    const phase = (name, ms) => setTimeout(() =>
      NOVA.push({ kind: "voice_phase", payload: { phase: name } }), ms);
    phase("listening", 100);
    phase("transcribing", 2600);
    phase("processing", 3400);
    phase("synthesizing", 4800);
    phase("speaking", 5400);
    phase("listening", 8600);
    let tick = 0;
    const level = setInterval(() => {
      tick += 1;
      const value = tick < 25 ? 0.25 + 0.55 * Math.abs(Math.sin(tick / 2.3)) * Math.random() : 0;
      NOVA.push({ kind: "voice_level", payload: { level: value } });
      if (tick > 26) clearInterval(level);
    }, 90);
    setTimeout(() => NOVA.push({ kind: "voice_message",
      payload: { role: "user", text: "Jarvis, ışıkları kapat." } }), 2700);
    setTimeout(() => NOVA.push({ kind: "voice_message",
      payload: { role: "assistant", text: "Elbette. (Demo modu — gerçek bir eylem yapılmadı.)" } }), 5400);
    return { ok: true };
  },
  async stop_voice() {
    NOVA.push({ kind: "voice_state", payload: { active: false, error: null } });
    return { ok: true };
  },
  async run_vision() {
    setTimeout(() => NOVA.push({ kind: "vision_result",
      payload: { ok: true, text: "Demo: Ekranda NOVA kabuğunun kendisi görünüyor — bir öz-portre." } }), 1200);
    return { ok: true };
  },
  async run_research(query) {
    setTimeout(() => NOVA.push({ kind: "research_result",
      payload: { ok: true, report: {
        query, summary: "Demo raporu: gerçek araştırma çekirdek bağlıyken çalışır.",
        sources: [{ title: "Örnek kaynak", url: "https://example.com" }],
      } } }), 1400);
    return { ok: true };
  },
  async resolve_approval() { return { ok: true }; },
  async permission_audit() {
    return { ok: true, entries: [
      { decision: "confirm", reason: "Demo: yüksek riskli eylem onay ister.", operation: "launch_windows_application",
        risk: "high", tool: "launch_windows_application", evaluated_at: new Date().toISOString() },
    ] };
  },
  async refresh() { return { snapshot: DEMO_SNAPSHOT }; },
  async system_status() {
    return { ok: true, observed_at: new Date().toISOString(),
      health: { status: "healthy", observed_at: new Date().toISOString(), checks: [
        { name: "core", component: "core", status: "healthy", latency_ms: 0.4, message: "Demo çekirdeği.", details: {} },
        { name: "provider_gateway", component: "providers", status: "healthy", latency_ms: 0.7, message: "Demo sağlayıcı.", details: { circuit: "closed" } },
        { name: "event_ledger", component: "diagnostics", status: "healthy", latency_ms: 1.2, message: "Demo defteri.", details: {} },
      ] },
      health_error: null,
      metrics: { counters: { "core.requests": 12, "events.info": 148 }, gauges: {},
        timers: { "core.request.duration": { count: 12, total_seconds: 27.6, average_seconds: 2.3, minimum_seconds: 0.8, maximum_seconds: 5.1 } } },
      integrity: true, event_count: 148,
      provider: { name: "gemini", circuit: "closed", error: false },
      admission: { active: 0, waiting: 0, accepted: 12, rejected: 0, max_concurrent: 8, max_queue: 32 },
      process: { cpu_percent: 1.2, memory_bytes: 183 * 1024 * 1024, threads: 14, uptime_seconds: 1830 },
    };
  },
  async diagnostic_events() {
    return { ok: true, integrity_valid: true, events: [
      { sequence: 148, observed_at: new Date().toISOString(), component: "core", name: "request.completed",
        level: "info", message: "Demo: istek tamamlandı.", attributes: { elapsed_seconds: 2.3, tool_calls: 1 }, trace_id: null },
      { sequence: 147, observed_at: new Date().toISOString(), component: "bootstrap", name: "application.ready",
        level: "info", message: "Demo: uygulama hazır.", attributes: {}, trace_id: null },
    ] };
  },
  async list_memories() { return { ok: true, memories: DEMO_SNAPSHOT.memories }; },
  async search_memories(query) {
    return { ok: true, memories: DEMO_SNAPSHOT.memories.filter((m) => lower(m.content).includes(lower(query))) };
  },
  async forget_memory() { return { ok: false, error: "Demo modu: anı değiştirilmedi." }; },
  async delete_memory(memoryId, confirmed) {
    if (confirmed !== true) return { ok: false, error: "Silme işlemi onaylanmadı." };
    return { ok: false, error: "Demo modu: anı silinmedi." };
  },
  async update_memory() { return { ok: false, error: "Demo modu: anı güncellenmedi." }; },
  async set_paused(paused) {
    NOVA.push({ kind: "paused", payload: { paused: paused === true, status: paused === true ? "PAUSED" : READY } });
    return { ok: true, paused: paused === true };
  },
  async set_compact(enabled) { return { ok: true, compact: enabled === true }; },
  async list_file_roots() {
    return { ok: true, available: true, roots: [
      { root_id: "belgeler-1a2b3c4d5e", name: "Belgeler", path: "C:\\Users\\Ali\\Documents" },
    ] };
  },
  async pick_file_root() { return { ok: true, path: "C:\\Users\\Ali\\Projeler" }; },
  async grant_file_root(path, confirmed) {
    if (confirmed !== true) return { ok: false, error: "Klasör erişimi onaylanmadı." };
    return { ok: false, error: "Demo modu: klasör erişimi eklenmedi." };
  },
  async revoke_file_root() { return { ok: false, error: "Demo modu: klasör erişimi kaldırılmadı." }; },
  async list_snapshots() {
    return { ok: true, available: true, total: 1, usage: { entries: 1, bytes: 2048, max_entries: 200, max_total_bytes: 536870912 },
      snapshots: [{ snapshot_id: "0123456789abcdef0123456789abcdef", root_id: "belgeler-1a2b3c4d5e", path: "notlar/toplanti.txt",
        size_bytes: 2048, sha256: "demo", reason: "overwrite", created_at: new Date().toISOString(), tool_name: "write_text_file" }] };
  },
  async restore_snapshot(snapshotId, confirmed) {
    if (confirmed !== true) return { ok: false, error: "Geri yükleme onaylanmadı." };
    return { ok: false, error: "Demo modu: dosya geri yüklenmedi." };
  },
  async list_notifications() {
    return { ok: true, items: DEMO_NOTIFICATIONS, unread: 1, total: DEMO_NOTIFICATIONS.length };
  },
  async mark_notifications_read() { return { ok: true, changed: 0, unread: 0 }; },
  async dismiss_notification() { return { ok: false, error: "Demo modu: bildirim kaldırılmadı." }; },
  async clear_notifications() { return { ok: true, cleared: 0, unread: 0 }; },
  async set_visible(visible) { return { ok: true, visible: visible === true }; },
  async list_routines() { return { ok: true, available: true, routines: DEMO_ROUTINES }; },
  async create_routine() { return { ok: false, error: "Demo modu: rutin kurulmadı." }; },
  async delete_routine(routineId, confirmed) {
    if (confirmed !== true) return { ok: false, error: "Rutin silme onaylanmadı." };
    return { ok: false, error: "Demo modu: rutin silinmedi." };
  },
  async get_settings() { return { provider: "gemini", model: "gemini-2.5-pro",
    credential_configured: true, credential_required: true }; },
  async save_settings() {
    return { ok: true, message: "Demo modu: ayarlar kaydedilmedi." };
  },
  async test_connection() {
    return { ok: false, message: "Demo modu: bağlantı sınaması yapılmadı." };
  },
  async delete_api_key(confirmed) {
    if (confirmed !== true) return { ok: false, error: "Silme işlemi onaylanmadı." };
    return { ok: true, message: "Demo modu: silinecek anahtar yok." };
  },
};

let Bridge = null;

/* The demo bridge is opt-in only: an explicit ?demo=1 query parameter,
   and never while running inside pywebview. Nothing else — not a slow
   core, not a missing bridge — ever selects it. */
function demoRequested() {
  try {
    if (window.pywebview) return false;
    return new URLSearchParams(window.location.search).get("demo") === "1";
  } catch (err) {
    return false;
  }
}

const BRIDGE_TIMEOUT_MS = 10000;

/* Resolves true once window.pywebview.api exists, false after the
   timeout. It never substitutes another bridge. */
function resolveBridge(timeoutMs = BRIDGE_TIMEOUT_MS) {
  return new Promise((resolve) => {
    const adopt = () => {
      if (window.pywebview && window.pywebview.api) {
        Bridge = window.pywebview.api;
        return true;
      }
      return false;
    };
    if (adopt()) { resolve(true); return; }
    let settled = false;
    const ready = () => {
      if (settled) return;
      if (adopt()) { settled = true; resolve(true); }
    };
    window.addEventListener("pywebviewready", ready);
    setTimeout(() => {
      if (settled) return;
      settled = true;
      window.removeEventListener("pywebviewready", ready);
      resolve(adopt());
    }, timeoutMs);
  });
}

function bridgeReady() {
  if (Bridge && State.booted) return true;
  toast("Çekirdek köprüsü hazır değil.", true);
  return false;
}

/* One place that turns a bridge call into a {ok, error} shape, so a
   thrown error never leaves a screen half-updated. */
async function call(method, ...args) {
  if (!bridgeReady()) return { ok: false, error: "Çekirdek köprüsü hazır değil." };
  try {
    const result = await Bridge[method](...args);
    return result && typeof result === "object" ? result : { ok: false, error: "Geçersiz yanıt." };
  } catch (err) {
    return { ok: false, error: describeError(err) };
  }
}

/* ── push channel (Python → JS) ───────────────────────────────────── */

window.NOVA = {
  push(event) {
    const { kind, payload } = event || {};
    const handler = PUSH[kind];
    if (handler) handler(payload || {});
  },
};

const PUSH = {
  snapshot(payload) { State.snapshot = payload; renderSnapshot(); },

  busy({ busy, status }) { setBusy(!!busy, status); },

  stream({ text }) {
    if (!text) return;
    Activity.onStream();
    updateChat(() => {
      const node = ensurePendingBubble();
      node.querySelector(".msg-body").textContent = text;
    });
  },

  reply(message) {
    updateChat(() => finalizePendingBubble(message));
    State.messages.push(message);
    Activity.onReply(message);
    renderHomeSession();
  },

  voice_message(message) {
    State.voiceMessages.push(message);
    if (message.role !== "system") State.messages.push(message);
    appendMessage($("#voice-list"), message, true);
    VoiceStage.caption(message);
    updateChat(() => appendMessage($("#chat-list"), message, false));
    if (message.role === "assistant") { Presence.spoke(); Activity.settle(); }
    renderHomeSession();
  },

  voice_phase({ phase }) {
    State.voicePhase = lower(phase) || null;
    if (State.voicePhase !== "listening") State.voiceLevel = 0;
    Presence.apply();
    VoiceStage.phase(State.voicePhase);
  },

  voice_state({ active, error }) {
    const wasActive = State.voiceActive;
    State.voiceActive = !!active;
    if (!active) { State.voicePhase = null; State.voiceLevel = 0; VoiceStage.close(); }
    else if (!wasActive) VoiceStage.open();
    if (error) toast(error, true);
    updateVoiceUI();
  },

  voice_level({ level }) {
    State.voiceLevel = clamp(Number(level) || 0, 0, 1);
    VoiceStage.level(State.voiceLevel);
  },

  vision_result({ ok, text, error }) {
    renderVisionResult(ok, text, error);
    setBusy(false, READY);
  },

  research_result({ ok, report, error }) {
    renderResearch(ok, report, error);
    setBusy(false, READY);
  },

  approval(payload) { openApproval(payload); },
  approval_closed({ token }) { closeApproval(token, null); },

  /* Live, read-only observation of the tool executor. */
  tool_activity(payload) { Activity.onToolEvent(payload); },

  /* Every sealed ledger event; the diagnostics screen shows the tail. */
  diagnostic_event(payload) { Diagnostics.onEvent(payload); },

  /* One entry of the notification centre (new or updated), with the
     live unread count; the core decides what deserves attention. */
  notification(payload) { Notify.onPush(payload); },

  /* Tray menu: jump to a screen (only known ids are accepted). */
  navigate({ screen }) {
    if (NAV.some(([id]) => id === screen)) showScreen(screen);
  },

  paused({ paused, status }) {
    setPaused(!!paused);
    if (status) setStatus(status);
  },
};
