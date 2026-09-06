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

/* The academy's demo shape mirrors the Python payloads one for one; every
   string says DEMO so a screenshot can never be mistaken for real study
   data. Nothing here writes anything. */
const DEMO_MEDICAL_SESSION = {
  session_id: "default", subject: "anatomy", topic_id: "anatomy.musculoskeletal.upper_limb.shoulder_girdle",
  mode: "teach", depth: "standard", knowledge_source: "course_and_jarvis", knowledge_priority: "balanced",
  document_ids: [], page_from: 0, page_to: 0, difficulty: 3, question_count: 10, option_count: 5,
  professor_id: null, language: "tr", recent_topics: [], adaptive_difficulty: true,
  labels: { subject: "Anatomi", topic: "Anatomi \u203a Hareket sistemi \u203a \u00dcst ekstremite \u203a Omuz ku\u015fa\u011f\u0131", mode: "\u00d6\u011fret",
            depth: "Standart", knowledge_source: "Ders materyali + JARVIS bilgisi", knowledge_priority: "Dengeli" },
  options: {
    modes: [{ value: "teach", label: "\u00d6\u011fret" }, { value: "quiz", label: "Quiz" }],
    depths: [{ value: "simple", label: "Basit" }, { value: "standard", label: "Standart" }, { value: "exam", label: "S\u0131nav modu" }],
    knowledge_sources: [{ value: "course_and_jarvis", label: "Ders materyali + JARVIS bilgisi" }],
    knowledge_priorities: [{ value: "balanced", label: "Dengeli" }],
    subjects: [{ value: "anatomy", label: "Anatomi" }, { value: "histology", label: "Histoloji" }],
  },
};

const DEMO_MEDICAL_DOCUMENT = {
  document_id: "demo-doc", title: "DEMO \u00b7 \u00dcst Ekstremite Ders 4", file_name: "demo.pdf", kind: "pdf",
  page_count: 24, subject: "anatomy", topic_ids: [], tags: [], status: "ready", status_label: "Haz\u0131r",
  status_detail: "Haz\u0131r \u00b7 24 sayfa \u00b7 96 par\u00e7a", error: null, visual_pages_analyzed: 3,
  visual_pages_pending: 0, chunk_count: 96, summary: "DEMO \u00f6zet.", key_terms: ["scapula", "clavicula"],
  imported_at: new Date().toISOString(), indexed_at: new Date().toISOString(), ready: true,
};

const DEMO_MEDICAL_EXAM = {
  exam_id: "demo-exam", title: "DEMO \u00b7 Omuz ku\u015fa\u011f\u0131 \u00b7 10 soru", status: "completed", mode: "study",
  question_count: 10, created_at: new Date().toISOString(), finished_at: null, score: 0.7, percent: 70,
  config: { subjects: ["anatomy"], topic_ids: [], difficulty: 3, option_count: 5, professor_id: null,
            answers_at_end: true, immediate_feedback: false, timed_seconds: 0, wrong_only: false, document_ids: [] },
  notes: [],
};

const DEMO_MEDICAL_STRUCTURE = {
  structure_id: "scapula", canonical: "Scapula", kind: "bone", kind_label: "Kemik", region: "upper_limb",
  region_label: "\u00dcst ekstremite", turkish: "K\u00fcrek kemi\u011fi", english: "Shoulder blade",
  landmark_count: 2, has_model: false, topic_id: "anatomy",
};

const DEMO_MEDICAL = {
  available: true,
  session: DEMO_MEDICAL_SESSION,
  counts: { documents: 1, pages: 24, chunks: 96, notes: 1, questions: 2, exams: 1, attempts: 1, mastery: 2, professors: 0, persistent: false },
  learning: { concepts: 2, attempts: 6, correct: 4, accuracy: 0.667, levels: { unknown: 0, weak: 1, moderate: 1, strong: 0 }, due_reviews: 1 },
  review_queue: [{ concept_id: "anatomy.scapula", name: "Scapula", subject: "anatomy", subject_label: "Anatomi",
                   level: "weak", level_label: "Zay\u0131f", reason: "DEMO: 3 denemede 1 do\u011fru.", attempts: 3, correct: 1, next_review_at: null }],
  weak_concepts: [{ concept_id: "anatomy.scapula", name: "Scapula", subject: "anatomy", subject_label: "Anatomi", attempts: 3, correct: 1,
                    accuracy: 0.333, recent: [false, true, false], streak: 0, level: "weak", level_label: "Zay\u0131f",
                    reason: "DEMO: fossa'lar kar\u0131\u015f\u0131yor.", confusions: {} }],
  insights: ["DEMO: Scapula sorular\u0131nda fossa supraspinata ile infraspinata'y\u0131 kar\u0131\u015ft\u0131r\u0131yorsun."],
  recent_documents: [DEMO_MEDICAL_DOCUMENT],
  recent_exams: [DEMO_MEDICAL_EXAM],
  recent_attempts: [], recent_topics: [], professors: [], jobs: [],
};

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
      medical: DEMO_MEDICAL,
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
  async medical_pick_file() { return { ok: true, path: null }; },
  async medical_call(action, params) {
    const query = String((params && params.query) || "");
    const views = {
      state: () => Object.assign({ ok: true }, DEMO_MEDICAL),
      session: () => ({ ok: true, session: DEMO_MEDICAL_SESSION, problems: ["Demo modu: oturum de\u011fi\u015fmedi."] }),
      subjects: () => ({ ok: true, subjects: [
        { topic_id: "anatomy", subject: "anatomy", title: "Anatomi", title_en: "Anatomy", keywords: [],
          mastery: { weak: 1, moderate: 0, strong: 0 }, documents: 1, concepts: 12,
          children: [{ topic_id: "anatomy.musculoskeletal", subject: "anatomy", title: "Hareket sistemi",
                       title_en: "Musculoskeletal", keywords: [], mastery: {}, documents: 1, concepts: 8, children: [] }] },
        { topic_id: "histology", subject: "histology", title: "Histoloji", title_en: "Histology", keywords: [],
          mastery: {}, documents: 0, concepts: 9, children: [] },
      ] }),
      topic: () => ({ ok: true, topic: {
        topic_id: "anatomy.musculoskeletal", subject: "anatomy", subject_label: "Anatomi",
        title: "Hareket sistemi", title_en: "Musculoskeletal system",
        path: [{ topic_id: "anatomy", title: "Anatomi" }], children: [], keywords: [],
        concepts: [{ concept_id: "anatomy.scapula", name: "Scapula", relations: [] }],
        structures: [DEMO_MEDICAL_STRUCTURE], documents: [], question_count: 2, mastery: [], notes: [] } }),
      search: () => ({ ok: true, query, terms: [], topics: [], structures: [], hits: [] }),
      term: () => ({ ok: true, query, entries: [] }),
      documents: () => ({ ok: true, documents: [DEMO_MEDICAL_DOCUMENT] }),
      document: () => ({ ok: true, document: Object.assign({}, DEMO_MEDICAL_DOCUMENT, {
        pages: [{ page_number: 1, headings: ["DEMO"], char_count: 900, image_count: 1, visual_status: "done", has_visual_summary: true }],
        topics: [], comparison: null, questions: 2, job: null }) }),
      page: () => ({ ok: true, page: { document_id: "demo-doc", page_number: 1,
        text: "DEMO: bu bir \u00f6rnek sayfa metnidir.", headings: ["DEMO"],
        visual_summary: "DEMO \u015fekil a\u00e7\u0131klamas\u0131.", visual_labels: ["acromion"],
        visual_status: "done", image_count: 1, image_area_ratio: 0.4, image: null } }),
      comparison: () => ({ ok: true, comparison: null }),
      analysis: () => ({ ok: true, analysis: null }),
      notes: () => ({ ok: true, notes: [{ note_id: "demo-note", title: "DEMO \u00b7 Scapula k\u0131sa notu",
        content: "## Scapula\n- DEMO madde", subject: "anatomy", subject_label: "Anatomi", topic_id: null,
        topic_label: "", mode: "medical.short_notes", references: [], created_at: new Date().toISOString() }] }),
      exams: () => ({ ok: true, exams: [DEMO_MEDICAL_EXAM] }),
      exam: () => ({ ok: true, exam: Object.assign({}, DEMO_MEDICAL_EXAM, { questions: [],
        attempt: { attempt_id: null, started_at: null, finished_at: null, answered: 0, current_index: 0 }, analysis: null }) }),
      bank: () => ({ ok: true, questions: [], counts: { total: 0 }, total: 0 }),
      professors: () => ({ ok: true, professors: [] }),
      progress: () => ({ ok: true, summary: DEMO_MEDICAL.learning, review_queue: DEMO_MEDICAL.review_queue,
        weak: DEMO_MEDICAL.weak_concepts, strong: [], all: DEMO_MEDICAL.weak_concepts,
        insights: DEMO_MEDICAL.insights, exams: [DEMO_MEDICAL_EXAM] }),
      anatomy: () => ({ ok: true, hierarchy: [{ region: "upper_limb", label: "\u00dcst ekstremite",
        kinds: [{ kind: "bone", label: "Kemik", structures: [DEMO_MEDICAL_STRUCTURE] }] }],
        assets: { directory: null, available: [], problems: [] }, source: "DEMO" }),
      structure: () => ({ ok: true, structure: Object.assign({}, DEMO_MEDICAL_STRUCTURE, {
        abbreviations: [], synonyms: [], topic_path: "Anatomi",
        landmarks: [{ landmark_id: "acromion", latin: "Acromion", turkish: "DEMO a\u00e7\u0131klama", note: "" },
                    { landmark_id: "spina_scapulae", latin: "Spina scapulae", turkish: "DEMO a\u00e7\u0131klama", note: "" }],
        sections: [{ key: "location", label: "Konum", items: ["DEMO: g\u00f6\u011f\u00fcs arka duvar\u0131nda."] }],
        relations: [], movements: [],
        model: { available: false, reason: "Demo modu: kay\u0131tl\u0131 3B model yok." },
        landmark_map: { schematic: true,
          nodes: [{ id: "scapula", label: "Scapula", kind: "bone", central: true },
                  { id: "acromion", label: "Acromion", kind: "landmark" }],
          edges: [{ from: "scapula", to: "acromion", relation: "landmark" }] },
        source: "DEMO" }) }),
      mesh: () => ({ ok: true, mesh: { available: false, reason: "Demo modu: model yok." } }),
      anatomy_quiz: () => ({ ok: true, questions: [] }),
      anatomy_answer: () => ({ ok: true, mastery: [] }),
    };
    const view = views[action];
    if (view) return view();
    return { ok: false, error: "Demo modu: bu i\u015flem yap\u0131lmad\u0131 (" + String(action) + ")." };
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

  /* Tıp Akademisi: belge işleme, sınav, not ve profil olayları. */
  medical(payload) { Medical.onPush(payload); },

  /* Tray menu: jump to a screen (only known ids are accepted). */
  navigate({ screen }) {
    if (NAV.some(([id]) => id === screen)) showScreen(screen);
  },

  paused({ paused, status }) {
    setPaused(!!paused);
    if (status) setStatus(status);
  },
};
