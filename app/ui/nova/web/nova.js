/* ════════════════════════════════════════════════════════════════════
   JARVIS · NOVA SHELL — runtime
   One rAF loop drives every canvas (delta-time based, so 60/120/144 Hz
   all animate at the same speed — higher refresh only adds smoothness).
   Canvases scale by devicePixelRatio for crisp 4K output.

   Honesty contract: everything shown comes from the Python core through
   window.pywebview.api. There is no silent demo. When the page is opened
   directly in a browser with ?demo=1 (and only then) a clearly labelled
   demo bridge serves sample data so the visuals can be inspected.
   ════════════════════════════════════════════════════════════════════ */
"use strict";

/* ── tiny helpers ─────────────────────────────────────────────────── */

const $  = (sel) => document.querySelector(sel);
const $$ = (sel) => Array.from(document.querySelectorAll(sel));

function esc(value) {
  return String(value ?? "")
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
}

function store(key, value) {
  try {
    if (value === undefined) return localStorage.getItem(key);
    localStorage.setItem(key, value);
  } catch (err) { return null; }
  return value;
}

function describeError(err) {
  if (err && typeof err === "object" && err.message) return String(err.message);
  return String(err ?? "bilinmeyen hata");
}

const TR = {
  pending: "BEKLİYOR", running: "ÇALIŞIYOR", active: "AKTİF",
  paused: "DURAKLATILDI", completed: "TAMAMLANDI", failed: "BAŞARISIZ",
  blocked: "ENGELLENDİ", cancelled: "İPTAL EDİLDİ", fresh: "GÜNCEL",
  stale: "ESKİ", low: "DÜŞÜK", medium: "ORTA", high: "YÜKSEK",
  critical: "KRİTİK",
};
const tr = (v) => TR[String(v ?? "").trim().toLowerCase()] ?? String(v ?? "");

const STATUS_TR = {
  "LOCAL CORE READY": "YEREL ÇEKİRDEK HAZIR",
  "PROCESSING": "İŞLENİYOR",
  "RESPONDING": "YANITLIYOR",
  "TESTING CONNECTION": "BAĞLANTI SINANIYOR",
  "LISTENING": "DİNLİYOR",
  "CAPTURING": "GÖRÜNTÜ ALINIYOR",
  "RESEARCHING": "ARAŞTIRIYOR",
  "PAUSED": "DURAKLATILDI",
};
const READY = "LOCAL CORE READY";
const PAUSED_NOTICE = "JARVIS duraklatıldı; önce tepsi menüsünden Devam'ı seç.";

/* ── state ────────────────────────────────────────────────────────── */

const State = {
  screen: "home",
  snapshot: null,
  settings: null,
  messages: [],
  voiceMessages: [],
  busy: false,
  voiceActive: false,
  coreMode: "idle",          // idle | busy | listening | speaking
  pendingEl: null,           // streaming assistant bubble
  approvals: [],             // session approval log
  /* Motion is Nova's soul, so it defaults ON regardless of the OS-wide
     animation toggle; the in-app "Hareketi azalt" switch persists an
     explicit opt-out. */
  reducedMotion: store("nova.motion") === "off",
  demo: false,
  booted: false,
  paused: false,           // tray "Duraklat": new work is refused
};

/* ── bridge (pywebview, or the explicit demo) ─────────────────────── */

const DEMO_SNAPSHOT = {
  provider: "gemini", model: "gemini-2.5-pro",
  memory_count: 3, task_count: 2, tool_count: 14, enabled_tools: 12,
  voice_available: true, vision_available: true,
  research_available: true, windows_available: true,
  diagnostic_event_count: 148, diagnostic_integrity_valid: true,
  tasks: [
    { goal: "Haftalık sistem raporunu derle", status: "running",
      progress: 0.62, current_step: "Tanılama olayları özetleniyor" },
    { goal: "Ses profillerini yeniden eğit", status: "paused",
      progress: 0.25, current_step: null },
  ],
  memories: [
    { content: "Ali her zaman Türkçe iletişim tercih ediyor.",
      source: "conversation", freshness: "fresh", confidence: 0.98 },
    { content: "Birincil çalışma dizini C:\\Users\\MeGaComputers\\JARVIS.",
      source: "observation", freshness: "fresh", confidence: 0.92 },
    { content: "Gemini tek üretim sağlayıcısı olarak yapılandırıldı.",
      source: "configuration", freshness: "stale", confidence: 0.85 },
  ],
  tools: [
    { name: "fs.read", description: "Kök izinli dosya okuma", risk: "low", enabled: true },
    { name: "fs.write", description: "Kök izinli dosya yazma", risk: "high", enabled: true },
    { name: "process.launch", description: "Doğrulanmış uygulama başlatma", risk: "high", enabled: true },
    { name: "clipboard.read", description: "Pano içeriğini okuma", risk: "medium", enabled: false },
    { name: "screen.capture", description: "Onaylı ekran yakalama", risk: "critical", enabled: true },
    { name: "web.research", description: "SSRF korumalı web araması", risk: "medium", enabled: true },
  ],
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
    };
  },
  async submit_command(text) {
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
                      payload: { role: "assistant", text: reply, metadata: {} } });
          NOVA.push({ kind: "busy", payload: { busy: false, status: READY } });
        }
      }, 50);
    }, 700);
    return { ok: true };
  },
  async start_voice() {
    const phase = (name, ms) => setTimeout(() =>
      NOVA.push({ kind: "voice_phase", payload: { phase: name } }), ms);
    phase("listening", 100);
    phase("transcribing", 2200);
    phase("processing", 3100);
    phase("synthesizing", 4600);
    phase("speaking", 5400);
    phase("listening", 8600);
    setTimeout(() => NOVA.push({ kind: "voice_message",
      payload: { role: "user", text: "Jarvis, ışıkları kapat." } }), 2400);
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
  async refresh() { return { snapshot: DEMO_SNAPSHOT }; },
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
    updateChat(() => {
      const el = ensurePendingBubble();
      el.querySelector(".msg-body").textContent = text;
    });
  },

  reply(message) {
    updateChat(() => finalizePendingBubble(message));
    State.messages.push(message);
    if (message.role === "assistant") speakPulse();
  },

  voice_message(message) {
    State.voiceMessages.push(message);
    if (message.role !== "system") State.messages.push(message);
    appendMessage($("#voice-list"), message, true);
    updateChat(() => appendMessage($("#chat-list"), message, false));
    if (message.role === "assistant") speakPulse();
  },

  voice_phase({ phase }) {
    /* The pipeline phase drives the core scene directly:
       ripples while listening, fast amber spin while thinking,
       green wobble while speaking. */
    const mode = {
      listening: "listening", transcribing: "busy", processing: "busy",
      synthesizing: "busy", speaking: "speaking",
    }[String(phase || "").trim().toLowerCase()];
    if (mode && State.voiceActive) {
      clearTimeout(speakTimer);
      State.coreMode = mode;
    }
  },

  voice_state({ active, error }) {
    State.voiceActive = !!active;
    if (!active) Engine.hud?.requestClose();
    if (error) toast(error, true);
    updateVoiceUI();
  },

  vision_result({ ok, text, error }) {
    const panel = $("#vision-result");
    panel.hidden = false;
    panel.classList.toggle("err", !ok);
    panel.textContent = ok ? text : (error || "Analiz başarısız.");
    $("#vision-form .action-btn").disabled = false;
    setBusy(false, READY);
  },

  research_result({ ok, report, error }) {
    renderResearch(ok, report, error);
    $("#research-form .action-btn").disabled = false;
    setBusy(false, READY);
  },

  approval(payload) { openApproval(payload); },
  approval_closed({ token }) { closeApproval(token, null); },

  /* Tray menu: jump to a screen (only known ids are accepted). */
  navigate({ screen }) {
    if (NAV.some(([id]) => id === screen)) showScreen(screen);
  },

  paused({ paused, status }) {
    setPaused(!!paused);
    if (status) setStatus(status);
  },
};

/* ── status & busy ────────────────────────────────────────────────── */

function setStatus(status) {
  $("#status-text").textContent = STATUS_TR[status] || status || "—";
}

function setBusy(busy, status) {
  State.busy = busy;
  if (status) setStatus(status);
  $("#status-orb").dataset.mode = State.paused ? "paused" : busy ? "busy"
    : State.voiceActive ? "listening" : "ready";
  $("#status-stream").classList.toggle("on", busy);
  $("#composer-send").disabled = busy || State.paused;
  if (!State.voiceActive) State.coreMode = busy ? "busy" : "idle";
  updateCoreCaption();
}

function setPaused(paused) {
  const changed = State.paused !== paused;
  State.paused = paused;
  document.body.classList.toggle("paused", paused);
  $("#composer-send").disabled = paused || State.busy;
  $("#chat-input").disabled = paused;
  $("#quick-input").disabled = paused;
  $("#status-orb").dataset.mode = paused ? "paused"
    : State.busy ? "busy" : State.voiceActive ? "listening" : "ready";
  updateCoreCaption();
  if (changed) {
    toast(paused
      ? "JARVIS duraklatıldı. Devam etmek için tepsi menüsünden Devam'ı seç."
      : "JARVIS devam ediyor.");
  }
}

function updateCoreCaption() {
  const stateEl = $("#core-state"), subEl = $("#core-sub");
  if (!State.snapshot) return;
  if (State.paused) {
    stateEl.textContent = "DURAKLATILDI";
    subEl.textContent = "tepsi menüsünden devam et";
    return;
  }
  if (State.busy) {
    stateEl.textContent = "İŞLENİYOR";
    subEl.textContent = "çekirdek yanıt üretiyor";
  } else if (State.voiceActive) {
    stateEl.textContent = "DİNLEMEDE";
    subEl.textContent = "uyandırma sözcüğü bekleniyor";
  } else {
    stateEl.textContent = "ÇEVRİMİÇİ";
    subEl.textContent = (State.snapshot.model || "çekirdek") + " hazır";
  }
}

/* ── navigation ───────────────────────────────────────────────────── */

const ICONS = {
  home: '<circle cx="12" cy="12" r="8.2"/><circle cx="12" cy="12" r="2.4"/><path d="M12 1.8v3.4M12 18.8v3.4M1.8 12h3.4M18.8 12h3.4"/>',
  chat: '<path d="M4 5.5h16v10.5h-9l-4.5 3.5v-3.5H4z"/><path d="M8 9.5h8M8 12.5h5"/>',
  tasks: '<rect x="4.5" y="3.5" width="15" height="17" rx="2"/><path d="M8.5 8.5l1.6 1.6 3-3.2M8.5 14.5l1.6 1.6 3-3.2"/>',
  memory: '<circle cx="6" cy="6" r="2.2"/><circle cx="18" cy="7.5" r="2.2"/><circle cx="8" cy="18" r="2.2"/><circle cx="17.5" cy="16.5" r="2.2"/><path d="M7.8 7.4l8.2-.6M7 8.1l.7 7.7M9.9 16.9l5.5-.2M16.6 9.6l.7 4.8"/>',
  voice: '<path d="M12 4a2.6 2.6 0 0 1 2.6 2.6v5a2.6 2.6 0 0 1-5.2 0v-5A2.6 2.6 0 0 1 12 4Z"/><path d="M6.5 11.5a5.5 5.5 0 0 0 11 0M12 17v3"/>',
  vision: '<path d="M2.5 12S6 5.8 12 5.8 21.5 12 21.5 12 18 18.2 12 18.2 2.5 12 2.5 12Z"/><circle cx="12" cy="12" r="2.8"/>',
  research: '<circle cx="10.5" cy="10.5" r="6"/><path d="M15 15l6 6"/><path d="M7.5 10.5h6M10.5 7.5v6"/>',
  tools: '<path d="M12 2.8l7.8 4.5v9.4L12 21.2l-7.8-4.5V7.3z"/><circle cx="12" cy="12" r="3"/>',
  integrations: '<path d="M12 3l7.5 3v5.5c0 4.6-3.2 7.6-7.5 9.5-4.3-1.9-7.5-4.9-7.5-9.5V6z"/><path d="M9 12l2.2 2.2L15.5 10"/>',
  diagnostics: '<path d="M3 12h4l2.2-5.5 3.6 11L15 12h6"/>',
  settings: '<path d="M5 7.5h14M5 12h14M5 16.5h14"/><circle cx="9" cy="7.5" r="1.7"/><circle cx="15" cy="12" r="1.7"/><circle cx="8" cy="16.5" r="1.7"/>',
};

const NAV = [
  ["home", "Komuta Merkezi"], ["chat", "Sohbet"], ["tasks", "Görevler"],
  ["memory", "Hafıza Ağı"], ["voice", "Ses"], ["vision", "Görüş"],
  ["research", "Araştırma"], ["tools", "Yetenekler"],
  ["integrations", "Güven ve Erişim"], ["diagnostics", "Tanılama"],
  ["settings", "Ayarlar"],
];

function buildRail() {
  const rail = $("#rail");
  NAV.forEach(([id, label], index) => {
    const btn = document.createElement("button");
    btn.className = "nav-btn"; btn.dataset.screen = id; btn.type = "button";
    btn.innerHTML =
      `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor"
            stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"
            aria-hidden="true">${ICONS[id]}</svg>` +
      `<span class="nav-label">${esc(label)}</span>` +
      `<span class="nav-num">${String(index + 1).padStart(2, "0")}</span>`;
    btn.addEventListener("click", () => showScreen(id));
    rail.appendChild(btn);
  });
}

function moveRailIndicator() {
  const active = $(`#rail .nav-btn[data-screen="${State.screen}"]`);
  const indicator = $("#rail-indicator");
  if (!active) { indicator.style.opacity = "0"; return; }
  indicator.style.opacity = "1";
  const offset = active.offsetTop + (active.offsetHeight - indicator.offsetHeight) / 2;
  indicator.style.transform = `translateY(${offset}px)`;
}

function showScreen(id) {
  if (State.screen === id) return;
  State.screen = id;
  $$("#rail .nav-btn").forEach((b) =>
    b.classList.toggle("active", b.dataset.screen === id));
  moveRailIndicator();

  const next = $(`.screen[data-screen="${id}"]`);
  $$(".screen").forEach((s) => s.classList.toggle("active", s === next));

  if (!State.reducedMotion) {
    next.animate(
      [{ opacity: 0, transform: "translateY(14px) scale(0.992)" },
       { opacity: 1, transform: "translateY(0) scale(1)" }],
      { duration: 420, easing: "cubic-bezier(0.16,1,0.3,1)" });
    next.querySelectorAll(".stagger > *, .card-grid > *").forEach((el, i) => {
      el.animate(
        [{ opacity: 0, transform: "translateY(16px)" },
         { opacity: 1, transform: "translateY(0)" }],
        { duration: 460, delay: Math.min(i, 14) * 45, fill: "backwards",
          easing: "cubic-bezier(0.16,1,0.3,1)" });
    });
  }

  if (id === "chat") { scrollChat({ force: true, instant: true }); $("#chat-input").focus(); }
  if (id === "home") requestAnimationFrame(() => Engine.resize());
  if (id === "voice") requestAnimationFrame(() => Engine.resize());
}

/* ── rendering: snapshot-driven screens ───────────────────────────── */

function renderSnapshot() {
  const s = State.snapshot;
  if (!s) return;

  /* topbar */
  $("#model-chip").textContent = s.model || s.provider || "—";
  const leds = [
    ["SES", s.voice_available], ["GÖRÜŞ", s.vision_available],
    ["WEB", s.research_available], ["SİSTEM", s.windows_available],
  ];
  $("#capability-leds").innerHTML = leds.map(([name, on]) =>
    `<span class="led ${on ? "on" : ""}"><i></i>${name}</span>`).join("");

  /* home stats */
  $("#stat-tasks").textContent = s.task_count ?? 0;
  const running = (s.tasks || []).filter((t) =>
    ["running", "active"].includes(String(t.status))).length;
  $("#stat-tasks-note").textContent =
    running ? `${running} görev şu an çalışıyor` : "şu an çalışan görev yok";
  $("#stat-memory").textContent = s.memory_count ?? 0;
  $("#stat-tools").firstChild.textContent = s.enabled_tools ?? 0;
  $("#stat-tools-total").textContent = ` / ${s.tool_count ?? 0}`;
  $("#stat-diag").textContent = s.diagnostic_event_count ?? 0;
  $("#stat-diag-note").innerHTML = s.diagnostic_integrity_valid
    ? 'olay kaydı · <span style="color:var(--green)">bütünlük doğrulandı</span>'
    : 'olay kaydı · <span style="color:var(--red)">bütünlük HATASI</span>';
  $("#cap-rows").innerHTML = leds.map(([name, on]) =>
    `<div class="cap-row"><b>${name}</b><span class="chip ${on ? "ok" : ""}">${on ? "ÇEVRİMİÇİ" : "KAPALI"}</span></div>`).join("");
  $("#stat-session").textContent =
    State.messages.length ? `${State.messages.length} mesaj` : "yeni oturum";
  updateCoreCaption();

  renderTasks(s.tasks || []);
  renderMemories(s.memories || []);
  renderTools(s.tools || []);
  renderTrust(s.tools || []);
  renderDiagnostics(s);
}

function renderTasks(tasks) {
  const host = $("#tasks-list");
  if (!tasks.length) {
    host.innerHTML = '<div class="empty-note">Kayıtlı görev yok. Sohbetten “bir görev planla” diyebilirsin.</div>';
    return;
  }
  host.innerHTML = tasks.map((t) => {
    const progress = Math.max(0, Math.min(1, Number(t.progress) || 0));
    const status = String(t.status ?? "pending");
    const chip = ["completed"].includes(status) ? "ok"
      : ["failed", "blocked", "cancelled"].includes(status) ? "bad"
      : ["running", "active"].includes(status) ? "accent" : "";
    return `<div class="panel data-card">
      <div class="dc-title">${esc(t.goal)}</div>
      <div class="progress-track"><div class="progress-fill"
           style="transform: scaleX(${progress})"></div></div>
      <div class="dc-meta">
        <span class="chip ${chip}">${esc(tr(status))}</span>
        <span>%${Math.round(progress * 100)}</span>
        <span>${esc(t.current_step || "aktif adım yok")}</span>
      </div></div>`;
  }).join("");
}

function renderMemories(memories) {
  const host = $("#memory-list");
  if (!memories.length) {
    host.innerHTML = '<div class="empty-note">Hafıza ağı henüz boş. Konuştukça önemli olan burada birikecek.</div>';
    return;
  }
  host.innerHTML = memories.map((m) => {
    const confidence = Number(m.confidence) || 0;
    return `<div class="panel data-card">
      <div class="dc-title">${esc(m.content)}</div>
      <div class="progress-track"><div class="progress-fill"
           style="transform: scaleX(${confidence})"></div></div>
      <div class="dc-meta">
        <span class="chip ${String(m.freshness) === "fresh" ? "ok" : "warn"}">${esc(tr(m.freshness))}</span>
        <span class="chip">${esc(m.source)}</span>
        <span>güven ${confidence.toFixed(2)}</span>
      </div></div>`;
  }).join("");
}

function renderTools(tools) {
  const host = $("#tools-list");
  if (!tools.length) {
    host.innerHTML = '<div class="empty-note">Araç sözleşmesi bulunamadı.</div>';
    return;
  }
  host.innerHTML = tools.map((t) => {
    const risk = String(t.risk ?? "low");
    const chip = risk === "critical" ? "bad" : risk === "high" ? "warn" : "";
    return `<div class="panel data-card" style="${t.enabled ? "" : "opacity:.5"}">
      <div class="dc-title" style="font-family:var(--font-mono);font-size:.8rem;letter-spacing:.06em">${esc(t.name)}</div>
      <div class="stat-note" style="margin:0">${esc(t.description)}</div>
      <div class="dc-meta">
        <span class="chip ${chip}">RİSK: ${esc(tr(risk))}</span>
        <span class="chip ${t.enabled ? "ok" : ""}">${t.enabled ? "ETKİN" : "DEVRE DIŞI"}</span>
      </div></div>`;
  }).join("");
}

function renderTrust(tools) {
  const counts = { low: 0, medium: 0, high: 0, critical: 0 };
  tools.forEach((t) => {
    const risk = String(t.risk ?? "low");
    if (risk in counts) counts[risk] += 1;
  });
  const total = Math.max(1, tools.length);
  $("#risk-bars").innerHTML = Object.entries(counts).map(([risk, count]) =>
    `<div class="risk-row">
       <div class="rr-head"><span>${esc(tr(risk))}</span><span>${count}</span></div>
       <div class="progress-track"><div class="progress-fill"
            style="transform: scaleX(${count / total})"></div></div>
     </div>`).join("");
}

function renderApprovalLog() {
  const host = $("#approval-log");
  if (!State.approvals.length) {
    host.innerHTML = '<div class="empty-note" style="padding:.6rem 0">Bu oturumda onay istenmedi.</div>';
    return;
  }
  host.innerHTML = State.approvals.slice(-8).reverse().map((a) =>
    `<div class="al-item"><span>${esc(a.tool)} · ${esc(a.operation)}</span>
     <span style="color:${a.approved ? "var(--green)" : "var(--red)"}">${a.approved ? "ONAY" : "RET"}</span></div>`).join("");
}

function renderDiagnostics(s) {
  const cards = [
    ["OLAY DEFTERİ", s.diagnostic_event_count,
     "yapılandırılmış tanılama olayı", true],
    ["BÜTÜNLÜK", s.diagnostic_integrity_valid ? "GEÇERLİ" : "İHLAL",
     "kurcalamaya karşı zincir doğrulaması", s.diagnostic_integrity_valid],
    ["SAĞLAYICI", (s.provider || "—").toUpperCase(),
     `${s.model || ""} modeli yapılandırıldı`, true],
    ["ARAÇLAR", `${s.enabled_tools}/${s.tool_count}`,
     "etkin araç sözleşmesi", true],
    ["SES HATTI", s.voice_available ? "HAZIR" : "KAPALI",
     "uyandırma sözcüğü + Gemini konuşma", s.voice_available],
    ["GÖRÜŞ HATTI", s.vision_available ? "HAZIR" : "KAPALI",
     "onaylı yakalama + bölge maskeleme", s.vision_available],
  ];
  $("#diag-grid").innerHTML = cards.map(([kicker, value, note, ok]) =>
    `<div class="panel data-card">
       <div class="stat-kicker">${kicker}</div>
       <div class="stat-value stat-small" style="font-size:1.5rem;color:${ok ? "var(--ink)" : "var(--amber)"}">${esc(String(value))}</div>
       <div class="stat-note">${esc(note)}</div>
     </div>`).join("");
}

/* ── chat ─────────────────────────────────────────────────────────── */

function appendMessage(host, message, slim) {
  if (!host || !message || !String(message.text ?? "").trim()) return null;
  const el = document.createElement("div");
  el.className = `msg ${esc(message.role)}`;
  const roleLabel = message.role === "user" ? "SEN"
    : message.role === "assistant" ? "JARVIS" : "";
  el.innerHTML =
    (roleLabel && !slim ? `<span class="msg-role">${roleLabel}</span>` : "") +
    `<span class="msg-body"></span>`;
  el.querySelector(".msg-body").textContent = message.text;
  host.appendChild(el);
  if (!State.reducedMotion) {
    el.animate(
      [{ opacity: 0, transform: "translateY(12px) scale(0.98)" },
       { opacity: 1, transform: "translateY(0) scale(1)" }],
      { duration: 380, easing: "cubic-bezier(0.16,1,0.3,1)" });
  }
  return el;
}

function ensurePendingBubble() {
  if (State.pendingEl) return State.pendingEl;
  const host = $("#chat-list");
  const el = document.createElement("div");
  el.className = "msg assistant pending";
  el.innerHTML = '<span class="msg-role">JARVIS</span>' +
    '<span class="msg-body"></span><span class="cursor"></span>';
  host.appendChild(el);
  State.pendingEl = el;
  return el;
}

function showThinking() {
  const el = ensurePendingBubble();
  el.querySelector(".msg-body").innerHTML =
    '<span class="thinking"><span class="orbit"></span>düşünüyor…</span>';
}

function finalizePendingBubble(message) {
  const el = State.pendingEl;
  State.pendingEl = null;
  if (el) {
    if (message.role === "assistant") {
      el.classList.remove("pending");
      el.querySelector(".cursor")?.remove();
      el.querySelector(".msg-body").textContent = message.text;
      return;
    }
    el.remove();
  }
  appendMessage($("#chat-list"), message, false);
}

/* Auto-scroll only when the reader is already at (or near) the bottom.
   Someone reading older messages is never yanked down; a small "yeni
   mesaj" pill appears instead. */
const SCROLL_STICK_PX = 96;

function chatAtBottom() {
  const s = $("#chat-scroll");
  return s.scrollHeight - s.scrollTop - s.clientHeight <= SCROLL_STICK_PX;
}

function scrollChat({ force = false, instant = false } = {}) {
  const scroller = $("#chat-scroll");
  if (!force && !chatAtBottom()) return;
  scroller.scrollTo({ top: scroller.scrollHeight,
                      behavior: instant || State.reducedMotion ? "auto" : "smooth" });
  $("#chat-jump").hidden = true;
}

function updateChat(mutate, { force = false } = {}) {
  const stick = force || chatAtBottom();
  mutate();
  if (stick) scrollChat({ force: true });
  else $("#chat-jump").hidden = false;
}

async function sendCommand(raw) {
  const text = String(raw ?? "").trim();
  if (State.paused) { toast(PAUSED_NOTICE, true); return; }
  if (!text || State.busy || !bridgeReady()) return;
  updateChat(() => {
    appendMessage($("#chat-list"), { role: "user", text }, false);
    showThinking();
  }, { force: true });
  State.messages.push({ role: "user", text });
  setBusy(true, "PROCESSING");
  let result;
  try {
    result = await Bridge.submit_command(text);
  } catch (err) {
    result = { ok: false, error: "Komut gönderilemedi: " + describeError(err) };
  }
  if (result && result.ok === false) {
    State.pendingEl?.remove(); State.pendingEl = null;
    setBusy(false, READY);
    toast(result.error || "Komut gönderilemedi.", true);
  }
}

/* ── voice ────────────────────────────────────────────────────────── */

async function toggleVoice() {
  if (!bridgeReady()) return;
  if (State.paused && !State.voiceActive) { toast(PAUSED_NOTICE, true); return; }
  if (State.voiceActive) {
    $("#voice-state").textContent = "DURDURULUYOR";
    let result;
    try { result = await Bridge.stop_voice(); }
    catch (err) { result = { ok: false, error: describeError(err) }; }
    if (result && result.ok === false) toast(result.error || "Sesli oturum durdurulamadı.", true);
    if (State.demo) {
      State.voiceActive = false;
      Engine.hud?.requestClose();
    }
    updateVoiceUI();
    return;
  }
  let result;
  try { result = await Bridge.start_voice(); }
  catch (err) { result = { ok: false, error: describeError(err) }; }
  if (result && result.ok === false) {
    toast(result.error || "Sesli oturum başlatılamadı.", true);
    return;
  }
  State.voiceActive = true;
  Engine.hud?.open();
  updateVoiceUI();
}

function updateVoiceUI() {
  const btn = $("#voice-toggle");
  btn.classList.toggle("live", State.voiceActive);
  btn.querySelector(".voice-btn-label").textContent =
    State.voiceActive ? "SESLİ OTURUMU DURDUR" : "SESLİ OTURUMU BAŞLAT";
  $("#voice-state").textContent = State.voiceActive ? "DİNLEMEDE" : "SESSİZ";
  State.coreMode = State.voiceActive ? "listening"
    : State.busy ? "busy" : "idle";
  $("#status-orb").dataset.mode = State.voiceActive ? "listening"
    : State.busy ? "busy" : "ready";
  setStatus(State.voiceActive ? "LISTENING" : State.busy ? "PROCESSING" : READY);
  updateCoreCaption();
}

let speakTimer = null;
function speakPulse() {
  State.coreMode = "speaking";
  clearTimeout(speakTimer);
  speakTimer = setTimeout(() => {
    State.coreMode = State.voiceActive ? "listening"
      : State.busy ? "busy" : "idle";
  }, 2600);
}

/* ── vision & research ────────────────────────────────────────────── */

async function submitVision(event) {
  event.preventDefault();
  if (State.paused) { toast(PAUSED_NOTICE, true); return; }
  if (State.busy || !bridgeReady()) return;
  const purpose = $("#vision-input").value;
  const panel = $("#vision-result");
  panel.hidden = false;
  panel.classList.remove("err");
  panel.innerHTML = '<span class="thinking"><span class="orbit"></span>ekran inceleniyor…</span>';
  $("#vision-form .action-btn").disabled = true;
  setBusy(true, "CAPTURING");
  let result;
  try { result = await Bridge.run_vision(purpose); }
  catch (err) { result = { ok: false, error: describeError(err) }; }
  if (result && result.ok === false) {
    panel.classList.add("err");
    panel.textContent = result.error || "Görüş başlatılamadı.";
    $("#vision-form .action-btn").disabled = false;
    setBusy(false, READY);
  }
}

async function submitResearch(event) {
  event.preventDefault();
  if (State.paused) { toast(PAUSED_NOTICE, true); return; }
  if (State.busy || !bridgeReady()) return;
  const query = $("#research-input").value.trim();
  if (!query) return;
  const panel = $("#research-result");
  panel.hidden = false;
  panel.classList.remove("err");
  panel.innerHTML = '<span class="thinking"><span class="orbit"></span>kaynaklar taranıyor…</span>';
  $("#research-form .action-btn").disabled = true;
  setBusy(true, "RESEARCHING");
  let result;
  try { result = await Bridge.run_research(query, Number($("#research-sources").value)); }
  catch (err) { result = { ok: false, error: describeError(err) }; }
  if (result && result.ok === false) {
    panel.classList.add("err");
    panel.textContent = result.error || "Araştırma başlatılamadı.";
    $("#research-form .action-btn").disabled = false;
    setBusy(false, READY);
  }
}

function renderResearch(ok, report, error) {
  const panel = $("#research-result");
  panel.hidden = false;
  panel.classList.toggle("err", !ok);
  if (!ok) { panel.textContent = error || "Araştırma başarısız."; return; }
  const parts = [];
  if (report.query) parts.push(`<h3>SORGU</h3>${esc(report.query)}`);
  const summary = report.summary || report.answer || report.text;
  if (summary) parts.push(`<h3>ÖZET</h3>${esc(summary)}`);
  const sources = report.sources || report.citations || [];
  if (Array.isArray(sources) && sources.length) {
    parts.push("<h3>KAYNAKLAR</h3>" + sources.map((src) => {
      const title = src.title || src.url || String(src);
      const url = src.url ? ` — ${esc(src.url)}` : "";
      return `<span class="src">▸ ${esc(title)}${url}</span>`;
    }).join(""));
  }
  const uncertainties = report.uncertainties || [];
  if (Array.isArray(uncertainties) && uncertainties.length) {
    parts.push("<h3>BELİRSİZLİKLER</h3>" +
      uncertainties.map((u) => `<span class="src">▸ ${esc(u)}</span>`).join(""));
  }
  panel.innerHTML = parts.join("") || esc(JSON.stringify(report, null, 2));
}

/* ── approval modal (permission engine: one exact action, one token) ── */

let activeApproval = null;

function openApproval(payload) {
  activeApproval = payload;
  $("#approval-risk").textContent = tr(payload.risk);
  $("#approval-risk").className = `risk-chip ${esc(payload.risk)}`;
  $("#approval-tool").textContent = payload.tool;
  $("#approval-operation").textContent = payload.operation;
  $("#approval-reason").textContent = payload.reason || "";
  $("#approval-params").innerHTML =
    Object.entries(payload.parameters || {}).map(([key, value]) =>
      `<div><b>${esc(key)}</b>: ${esc(String(value))}</div>`).join("") ||
    "<div>parametre yok</div>";
  const veil = $("#approval");
  veil.hidden = false;
  const bar = $("#approval-timer-bar");
  bar.style.transition = "none";
  bar.style.transform = "scaleX(1)";
  requestAnimationFrame(() => {
    bar.style.transition = `transform ${payload.seconds || 30}s linear`;
    bar.style.transform = "scaleX(0)";
  });
  if (!State.reducedMotion) {
    veil.querySelector(".modal").animate(
      [{ opacity: 0, transform: "translateY(18px) scale(0.96)" },
       { opacity: 1, transform: "translateY(0) scale(1)" }],
      { duration: 340, easing: "cubic-bezier(0.34,1.4,0.44,1)" });
  }
  $("#approval-deny").focus();
}

function closeApproval(token, approved) {
  if (!activeApproval) return;
  if (token && activeApproval.token !== token) return;
  if (approved !== null) {
    Bridge.resolve_approval(activeApproval.token, approved);
    State.approvals.push({
      tool: activeApproval.tool, operation: activeApproval.operation, approved,
    });
    renderApprovalLog();
  }
  activeApproval = null;
  $("#approval").hidden = true;
}

/* ── in-app confirmation (UI safety net, unrelated to tool approvals) ── */

let confirmOpen = false;

function confirmDialog({ title, body, confirmLabel = "ONAYLA",
                         cancelLabel = "VAZGEÇ", danger = false }) {
  return new Promise((resolve) => {
    const veil = $("#confirm");
    const ok = $("#confirm-ok"), cancel = $("#confirm-cancel");
    $("#confirm-title").textContent = title;
    $("#confirm-text").textContent = body;
    ok.textContent = confirmLabel;
    cancel.textContent = cancelLabel;
    ok.classList.toggle("danger", !!danger);
    const finish = (value) => {
      ok.onclick = null; cancel.onclick = null;
      window.removeEventListener("keydown", onKey, true);
      veil.hidden = true;
      confirmOpen = false;
      resolve(value);
    };
    const onKey = (event) => {
      if (event.key === "Escape") { event.preventDefault(); event.stopPropagation(); finish(false); }
    };
    ok.onclick = () => finish(true);
    cancel.onclick = () => finish(false);
    window.addEventListener("keydown", onKey, true);
    confirmOpen = true;
    veil.hidden = false;
    if (!State.reducedMotion) {
      veil.querySelector(".modal").animate(
        [{ opacity: 0, transform: "translateY(18px) scale(0.96)" },
         { opacity: 1, transform: "translateY(0) scale(1)" }],
        { duration: 300, easing: "cubic-bezier(0.34,1.4,0.44,1)" });
    }
    cancel.focus();   // the safe choice is the default focus
  });
}

/* ── settings ─────────────────────────────────────────────────────── */

function renderSettings() {
  const s = State.settings;
  if (!s) return;
  $("#settings-model").value = s.model || "";
  $("#settings-key").placeholder = s.credential_configured
    ? "Anahtar kayıtlı — değiştirmek için yaz" : "Gemini API anahtarın";
  $("#settings-motion").checked = State.reducedMotion;
}

function settingsStatus(text, ok) {
  const el = $("#settings-status");
  el.textContent = text || "";
  el.className = `settings-status ${ok === true ? "ok" : ok === false ? "err" : ""}`;
}

async function saveSettings(event) {
  event.preventDefault();
  if (!bridgeReady()) return;
  settingsStatus("Kaydediliyor…");
  let result;
  try {
    result = await Bridge.save_settings(
      "gemini", $("#settings-model").value.trim(), $("#settings-key").value);
  } catch (err) { result = { ok: false, error: describeError(err) }; }
  settingsStatus(result.message || result.error, !!result.ok);
  if (result.ok) {
    $("#settings-key").value = "";
    if (result.settings) { State.settings = result.settings; renderSettings(); }
  }
}

async function testConnection() {
  if (!bridgeReady()) return;
  settingsStatus("Bağlantı sınanıyor…");
  setStatus("TESTING CONNECTION");
  let result;
  try {
    result = await Bridge.test_connection(
      "gemini", $("#settings-model").value.trim(), $("#settings-key").value);
  } catch (err) { result = { ok: false, message: describeError(err) }; }
  settingsStatus(result.message, !!result.ok);
  setStatus(State.busy ? "PROCESSING" : READY);
}

async function deleteKey() {
  if (!bridgeReady()) return;
  const confirmed = await confirmDialog({
    title: "API anahtarı silinsin mi?",
    body: "Bu işlem Gemini API anahtarını Windows Kimlik Bilgisi " +
      "Yöneticisi'nden siler ve JARVIS'i deneme moduna döndürür. Yeni bir " +
      "anahtar girene kadar bulut yanıtları ve Charon sesi kullanılamaz.",
    confirmLabel: "ANAHTARI SİL",
    danger: true,
  });
  if (!confirmed) { settingsStatus("Silme işlemi iptal edildi."); return; }
  let result;
  try { result = await Bridge.delete_api_key(true); }
  catch (err) { result = { ok: false, error: describeError(err) }; }
  settingsStatus(result.message || result.error, !!result.ok);
  if (result.ok && result.settings) {
    State.settings = result.settings; renderSettings();
  }
}

function applyMotionPreference(reduced) {
  State.reducedMotion = reduced;
  document.body.classList.toggle("reduced-motion", reduced);
  store("nova.motion", reduced ? "off" : "on");
  Engine.staticFrame = reduced;
}

const SHORTCUTS = [
  ["Enter", "Komutu gönder"], ["Shift + Enter", "Yeni satır"],
  ["Ctrl + L", "Komut alanına odaklan"], ["Ctrl + M", "Sesli bağlantıyı aç/kapat"],
  ["Ctrl + ,", "Ayarları aç"],
  ["Ctrl + Shift + T", "Açık/koyu tema"], ["Alt + 1…9", "Ekranlar"],
  ["Alt + 0", "Tanılama"], ["↑ / ↓", "Ekranı satır satır kaydır"],
  ["Page Up / Page Down", "Ekranı sayfa sayfa kaydır"],
  ["Home / End", "Başa / sona git"], ["Escape", "Komuta Merkezi'ne dön"],
];

function renderShortcuts() {
  $("#shortcut-table").innerHTML = SHORTCUTS.map(([keys, label]) =>
    `<kbd>${esc(keys)}</kbd><span>${esc(label)}</span>`).join("");
}

/* ── keyboard scrolling (mirrors the classic shell's shortcuts) ────── */

function activeScroller() {
  return State.screen === "chat" ? $("#chat-scroll") : $(".screen.active");
}

function handleScrollKeys(event) {
  if (event.ctrlKey || event.altKey || event.metaKey) return false;
  const target = event.target;
  const tag = (target && target.tagName || "").toLowerCase();
  if (["input", "textarea", "select"].includes(tag) || (target && target.isContentEditable)) return false;
  if (activeApproval || confirmOpen || Engine.hud?.active) return false;
  const scroller = activeScroller();
  if (!scroller) return false;
  const behavior = State.reducedMotion ? "auto" : "smooth";
  const line = 64, page = Math.max(line, scroller.clientHeight * 0.85);
  const delta = { ArrowDown: line, ArrowUp: -line, PageDown: page, PageUp: -page }[event.key];
  if (delta !== undefined) { scroller.scrollBy({ top: delta, behavior }); return true; }
  if (event.key === "Home") { scroller.scrollTo({ top: 0, behavior }); return true; }
  if (event.key === "End") { scroller.scrollTo({ top: scroller.scrollHeight, behavior }); return true; }
  return false;
}

/* ── toasts ───────────────────────────────────────────────────────── */

function toast(text, isError) {
  const host = $("#toasts");
  const el = document.createElement("div");
  el.className = `toast ${isError ? "err" : ""}`;
  el.textContent = text;
  host.appendChild(el);
  const fade = () => {
    const anim = el.animate([{ opacity: 1 }, { opacity: 0, transform: "translateY(8px)" }],
      { duration: 400, easing: "ease" });
    anim.onfinish = () => el.remove();
  };
  if (!State.reducedMotion) {
    el.animate([{ opacity: 0, transform: "translateY(10px)" },
                { opacity: 1, transform: "translateY(0)" }],
      { duration: 320, easing: "cubic-bezier(0.16,1,0.3,1)" });
  }
  setTimeout(fade, 5200);
}

/* ════════════════════════════════════════════════════════════════════
   CANVAS ENGINE — starfield backdrop + arc reactor cores
   ════════════════════════════════════════════════════════════════════ */

const Engine = {
  cores: [], starfield: null, staticFrame: false,
  _last: 0, _raf: 0,

  init() {
    this.starfield = new Starfield($("#bg-canvas"));
    this.cores = [
      new ArcCore($("#core-canvas"), () => State.screen === "home"),
      new ArcCore($("#voice-canvas"), () => State.screen === "voice"),
    ];
    this.hud = new VoiceHUD($("#voice-hud"));
    this.resize();
    addEventListener("resize", () => this.resize());
    document.addEventListener("visibilitychange", () => {
      if (!document.hidden) { this._last = performance.now(); }
    });
    this._raf = requestAnimationFrame((t) => this.tick(t));
  },

  resize() {
    this.starfield.resize();
    this.cores.forEach((core) => core.resize());
    this.hud?.resize();
    this._staticDrawn = false;   // a resize wipes canvases: repaint even when static
  },

  tick(now) {
    this._raf = requestAnimationFrame((t) => this.tick(t));
    if (document.hidden) return;
    const dt = Math.min((now - (this._last || now)) / 1000, 0.05);
    this._last = now;
    const hudLive = this.hud && this.hud.active;
    if (this.staticFrame && !hudLive) {
      // Reduced motion: draw one calm frame, then idle.
      if (!this._staticDrawn) {
        this.starfield.draw(0, 0);
        this.cores.forEach((c) => { if (c.visible()) c.draw(0, 0); });
        this._staticDrawn = true;
      }
      return;
    }
    this._staticDrawn = false;
    if (hudLive) {
      this.hud.draw(this.staticFrame ? 0.016 : dt, now / 1000);
      return;      // the HUD covers everything: skip the layers beneath
    }
    this.starfield.draw(dt, now / 1000);
    this.cores.forEach((core) => { if (core.visible()) core.draw(dt, now / 1000); });
  },
};

/* Full-viewport backdrop: parallax stars + drifting aurora veils. */
class Starfield {
  constructor(canvas) {
    this.canvas = canvas;
    this.ctx = canvas.getContext("2d");
    this.stars = [];
  }
  resize() {
    const rect = this.canvas.getBoundingClientRect();
    const dpr = Math.min(devicePixelRatio || 1, 2.5);
    this.w = rect.width || innerWidth;
    this.h = rect.height || innerHeight;
    this.canvas.width = this.w * dpr; this.canvas.height = this.h * dpr;
    this.ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    const count = Math.floor((this.w * this.h) / 6200);
    this.stars = Array.from({ length: count }, () => ({
      x: Math.random() * this.w, y: Math.random() * this.h,
      z: 0.3 + Math.random() * 0.7,           // depth → size/speed/brightness
      p: Math.random() * Math.PI * 2,          // twinkle phase
    }));
  }
  draw(dt, t) {
    const { ctx, w, h } = this;
    const dark = !document.body.classList.contains("light");
    ctx.clearRect(0, 0, w, h);

    /* aurora veils */
    const veils = [
      [w * (0.28 + 0.06 * Math.sin(t * 0.05)), h * (0.2 + 0.05 * Math.cos(t * 0.04)),
       w * 0.5, "126,232,255", dark ? 0.045 : 0.05],
      [w * (0.75 + 0.05 * Math.cos(t * 0.037)), h * (0.7 + 0.06 * Math.sin(t * 0.045)),
       w * 0.55, "56,140,232", dark ? 0.04 : 0.04],
    ];
    for (const [x, y, r, rgb, alpha] of veils) {
      const gradient = ctx.createRadialGradient(x, y, 0, x, y, r);
      gradient.addColorStop(0, `rgba(${rgb},${alpha})`);
      gradient.addColorStop(1, "rgba(0,0,0,0)");
      ctx.fillStyle = gradient;
      ctx.fillRect(0, 0, w, h);
    }

    /* stars */
    if (dark) {
      for (const star of this.stars) {
        star.x -= star.z * dt * 2.2;                 // glacial drift
        if (star.x < -2) star.x = w + 2;
        const twinkle = 0.55 + 0.45 * Math.sin(t * (0.6 + star.z) + star.p);
        ctx.globalAlpha = 0.16 + 0.5 * star.z * twinkle;
        ctx.fillStyle = "#bfeAf7";
        const size = star.z * 1.5;
        ctx.fillRect(star.x, star.y, size, size);
      }
      ctx.globalAlpha = 1;
    }
  }
}

/* The living core: ticked outer ring, counter-rotating arcs, orbiting
   particles, breathing nucleus. Mode-reactive (idle/busy/listening/speaking). */
class ArcCore {
  constructor(canvas, visible) {
    this.canvas = canvas;
    this.visible = visible;
    this.ctx = canvas.getContext("2d");
    this.rotation = [0, 0, 0, 0];
    this.ripples = [];
    this.rippleClock = 0;
    this.energy = 0;                 // eases toward mode target
    this.particles = Array.from({ length: 42 }, (_, i) => ({
      angle: (i / 42) * Math.PI * 2,
      band: i % 3,
      speed: 0.25 + (i % 5) * 0.09,
      size: 0.8 + (i % 4) * 0.45,
    }));
  }
  resize() {
    const rect = this.canvas.getBoundingClientRect();
    if (!rect.width) return;
    const dpr = Math.min(devicePixelRatio || 1, 3);
    this.size = rect.width;
    this.canvas.width = rect.width * dpr;
    this.canvas.height = rect.height * dpr;
    this.ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  }
  palette() {
    const mode = State.coreMode;
    if (mode === "busy")      return { hue: "240,198,132", core: "255,224,178" };
    if (mode === "speaking")  return { hue: "127,224,178", core: "205,255,228" };
    return { hue: "126,232,255", core: "196,244,255" };  // idle & listening
  }
  draw(dt, t) {
    if (!this.size) {
      this.resize();
      if (!this.size) return;   // still zero-sized (hidden screen)
    }
    const { ctx } = this;
    const size = this.size, c = size / 2, R = size / 2;
    const mode = State.coreMode;
    const target = mode === "busy" ? 1 : mode === "speaking" ? 0.8
      : mode === "listening" ? 0.55 : 0.22;
    this.energy += (target - this.energy) * Math.min(dt * 3, 1);
    const e = this.energy;
    const { hue, core } = this.palette();

    ctx.clearRect(0, 0, size, size);

    /* rotation states (direction alternates per layer) */
    const speeds = [0.08 + e * 0.5, -(0.14 + e * 0.9), 0.24 + e * 1.4, -(0.05 + e * 0.2)];
    this.rotation = this.rotation.map((r, i) => r + speeds[i] * dt);

    /* ── outer tick ring ── */
    ctx.save();
    ctx.translate(c, c);
    ctx.rotate(this.rotation[3]);
    ctx.strokeStyle = `rgba(${hue},0.5)`;
    for (let i = 0; i < 72; i++) {
      const angle = (i / 72) * Math.PI * 2;
      const long = i % 6 === 0;
      const r1 = R * 0.985, r2 = R * (long ? 0.945 : 0.965);
      ctx.globalAlpha = long ? 0.55 : 0.28;
      ctx.lineWidth = long ? 1.4 : 1;
      ctx.beginPath();
      ctx.moveTo(Math.cos(angle) * r1, Math.sin(angle) * r1);
      ctx.lineTo(Math.cos(angle) * r2, Math.sin(angle) * r2);
      ctx.stroke();
    }
    ctx.restore();

    /* ── dashed mid ring ── */
    ctx.save();
    ctx.translate(c, c);
    ctx.rotate(-this.rotation[0] * 0.6);
    ctx.globalAlpha = 0.4;
    ctx.strokeStyle = `rgba(${hue},0.45)`;
    ctx.lineWidth = 1;
    ctx.setLineDash([2, 9]);
    ctx.beginPath();
    ctx.arc(0, 0, R * 0.8, 0, Math.PI * 2);
    ctx.stroke();
    ctx.setLineDash([]);
    ctx.restore();

    /* ── rotating arc segments (cheap glow: wide faint + thin bright) ── */
    const arcs = [
      [0.88, this.rotation[0], 1.9, 0.7],
      [0.72, this.rotation[1], 1.25, 0.85],
      [0.64, this.rotation[2], 0.8, 1.0],
    ];
    for (const [radiusFactor, rotation, span, brightness] of arcs) {
      const radius = R * radiusFactor;
      for (const [width, alpha] of [[5.5, 0.08], [1.6, 0.75]]) {
        ctx.beginPath();
        ctx.arc(c, c, radius, rotation, rotation + span);
        ctx.strokeStyle = `rgba(${hue},${alpha * brightness * (0.45 + e)})`;
        ctx.lineWidth = width;
        ctx.lineCap = "round";
        ctx.stroke();
      }
    }

    /* ── orbiting particles on three inclined bands ── */
    for (const particle of this.particles) {
      particle.angle += particle.speed * (0.4 + e * 1.6) * dt;
      const bandRadius = R * (0.5 + particle.band * 0.13);
      const squash = 0.42 + particle.band * 0.16;
      const tilt = particle.band * (Math.PI / 5) + this.rotation[3] * 0.4;
      const px = Math.cos(particle.angle) * bandRadius;
      const py = Math.sin(particle.angle) * bandRadius * squash;
      const x = c + px * Math.cos(tilt) - py * Math.sin(tilt);
      const y = c + px * Math.sin(tilt) + py * Math.cos(tilt);
      const depth = 0.5 + 0.5 * Math.sin(particle.angle);   // fake z-light
      ctx.globalAlpha = 0.15 + depth * 0.6 * (0.4 + e);
      ctx.fillStyle = `rgba(${hue},1)`;
      ctx.beginPath();
      ctx.arc(x, y, particle.size * (0.7 + depth * 0.5), 0, Math.PI * 2);
      ctx.fill();
    }
    ctx.globalAlpha = 1;

    /* ── listening ripples ── */
    if (mode === "listening") {
      this.rippleClock -= dt;
      if (this.rippleClock <= 0) {
        this.ripples.push({ r: R * 0.3, alpha: 0.5 });
        this.rippleClock = 1.15;
      }
    }
    this.ripples = this.ripples.filter((ripple) => ripple.alpha > 0.01);
    for (const ripple of this.ripples) {
      ripple.r += dt * R * 0.5;
      ripple.alpha *= 1 - dt * 1.6;
      ctx.beginPath();
      ctx.arc(c, c, ripple.r, 0, Math.PI * 2);
      ctx.strokeStyle = `rgba(${hue},${ripple.alpha})`;
      ctx.lineWidth = 1.2;
      ctx.stroke();
    }

    /* ── breathing nucleus ── */
    const breath = 1 + 0.05 * Math.sin(t * (1.4 + e * 4));
    const wobble = mode === "speaking" ? 0.06 * Math.sin(t * 16) : 0;
    const nucleusR = R * 0.24 * (breath + wobble);
    const glow = ctx.createRadialGradient(c, c, 0, c, c, nucleusR * 2.4);
    glow.addColorStop(0, `rgba(${core},${0.5 + e * 0.4})`);
    glow.addColorStop(0.35, `rgba(${hue},${0.2 + e * 0.25})`);
    glow.addColorStop(1, "rgba(0,0,0,0)");
    ctx.fillStyle = glow;
    ctx.beginPath();
    ctx.arc(c, c, nucleusR * 2.4, 0, Math.PI * 2);
    ctx.fill();
    ctx.fillStyle = `rgba(${core},0.95)`;
    ctx.beginPath();
    ctx.arc(c, c, nucleusR * 0.42, 0, Math.PI * 2);
    ctx.fill();
  }
}

/* Full-screen voice link HUD: the command-centre core, floor to ceiling.
   No chrome, no text — just the starfield and the living ArcCore, which
   already reacts to the session phase through State.coreMode. Click or
   Escape ends the link. */

class VoiceHUD {
  constructor(host) {
    this.host = host;
    this.stars = new Starfield(host.querySelector("#voice-hud-stars"));
    this.core = new ArcCore(
      host.querySelector("#voice-hud-core"),
      () => this.visible,
    );
    this.visible = false;
    host.addEventListener("click", () => toggleVoice());
  }

  get active() { return this.visible; }

  open() {
    this.visible = true;
    this.host.hidden = false;
    this.resize();
    if (!State.reducedMotion) {
      this.host.animate([{ opacity: 0 }, { opacity: 1 }],
        { duration: 420, easing: "cubic-bezier(0.16,1,0.3,1)" });
      this.core.canvas.parentElement.animate(
        [{ transform: "scale(0.9)", opacity: 0 },
         { transform: "scale(1)", opacity: 1 }],
        { duration: 620, easing: "cubic-bezier(0.16,1,0.3,1)" });
    }
  }

  requestClose() {
    if (!this.visible) return;
    this.visible = false;
    const finish = () => {
      if (!this.visible) this.host.hidden = true;   // unless reopened meanwhile
    };
    if (State.reducedMotion) { finish(); return; }
    const fade = this.host.animate([{ opacity: 1 }, { opacity: 0 }],
      { duration: 360, easing: "ease" });
    fade.onfinish = finish;
    setTimeout(finish, 480);   // safety net if the animation never finishes
  }

  resize() {
    if (this.host.hidden) return;
    this.stars.resize();
    this.core.resize();
  }

  draw(dt, t) {
    this.stars.draw(dt, t);
    this.core.draw(dt, t);
  }
}

/* ════════════════════════════════════════════════════════════════════
   BOOT SEQUENCE
   ════════════════════════════════════════════════════════════════════ */

const BOOT_LINES = [
  "çekirdek köprüsü kuruldu",
  "araç sözleşmeleri yüklendi",
  "hafıza ağı bağlandı",
  "ses ve görüş hatları denetlendi",
  "izin motoru mühürlendi",
];

async function runBootSequence() {
  const boot = $("#boot"), log = $("#boot-log");
  if (State.reducedMotion) {
    boot.classList.add("gone");
    $("#app").classList.remove("pre-boot");
    return;
  }
  for (const line of BOOT_LINES) {
    const row = document.createElement("div");
    row.innerHTML = `${esc(line)} <span class="ok">· TAMAM</span>`;
    log.appendChild(row);
    row.animate([{ opacity: 0 }, { opacity: 1 }], { duration: 200, fill: "both" });
    await new Promise((resolve) => setTimeout(resolve, 170));
  }
  const online = document.createElement("div");
  online.innerHTML = State.demo
    ? '<span class="warn">DEMO MODU · ÇEKİRDEK BAĞLI DEĞİL · VERİLER ÖRNEK</span>'
    : '<span class="ok">JARVIS ÇEVRİMİÇİ</span>';
  log.appendChild(online);
  await new Promise((resolve) => setTimeout(resolve, 420));
  boot.classList.add("gone");
  $("#app").classList.remove("pre-boot");
}

/* The shell stays hidden; the reader sees exactly what went wrong. */
function showBootFailure(message) {
  const boot = $("#boot");
  boot.classList.add("failed");
  $("#boot-error-text").textContent = message;
  $("#boot-error").hidden = false;
  $("#boot-retry").onclick = () => window.location.reload();
  $("#boot-retry").focus();
}

function applyBoot(bootData) {
  State.booted = true;
  State.snapshot = bootData.snapshot;
  State.settings = bootData.settings;
  State.messages = bootData.messages || [];
  State.voiceMessages = bootData.voiceMessages || [];
  $("#demo-badge").hidden = !State.demo;
  renderSnapshot();
  renderSettings();
  renderApprovalLog();
  setStatus(bootData.status || READY);
  State.messages.forEach((message) =>
    appendMessage($("#chat-list"), message, false));
  State.voiceMessages.forEach((message) =>
    appendMessage($("#voice-list"), message, true));
  scrollChat({ force: true, instant: true });
}

/* ════════════════════════════════════════════════════════════════════
   WIRE-UP
   ════════════════════════════════════════════════════════════════════ */

function bindEvents() {
  /* quick command → chat */
  $("#quick-form").addEventListener("submit", (event) => {
    event.preventDefault();
    const text = $("#quick-input").value;
    if (!text.trim()) return;
    if (State.busy) {
      toast("JARVIS hâlâ yanıtlıyor; komutun bekliyor, yanıt bitince gönder.", true);
      return;
    }
    $("#quick-input").value = "";
    showScreen("chat");
    sendCommand(text);
  });

  /* chat composer */
  $("#chat-form").addEventListener("submit", (event) => {
    event.preventDefault();
    const input = $("#chat-input");
    const text = input.value;
    if (!text.trim()) return;
    if (State.busy) {   // keep the draft instead of silently dropping it
      toast("JARVIS hâlâ yanıtlıyor; mesajın bekliyor, yanıt bitince gönder.", true);
      return;
    }
    input.value = ""; input.style.height = "auto";
    sendCommand(text);
  });
  const chatInput = $("#chat-input");
  chatInput.addEventListener("keydown", (event) => {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      $("#chat-form").requestSubmit();
    }
  });
  chatInput.addEventListener("input", () => {
    chatInput.style.height = "auto";
    chatInput.style.height = Math.min(chatInput.scrollHeight, 176) + "px";
  });
  $("#chat-scroll").addEventListener("scroll", () => {
    if (chatAtBottom()) $("#chat-jump").hidden = true;
  }, { passive: true });
  $("#chat-jump").addEventListener("click", () => scrollChat({ force: true }));

  $("#composer-voice").addEventListener("click", () => {
    showScreen("voice");
    if (!State.voiceActive) toggleVoice();
  });
  $("#voice-toggle").addEventListener("click", toggleVoice);
  /* The orb itself is the voice switch: click the core, start talking. */
  $$("#stage .core-frame").forEach((frame) =>
    frame.addEventListener("click", () => toggleVoice()));

  $("#vision-form").addEventListener("submit", submitVision);
  $("#research-form").addEventListener("submit", submitResearch);

  $("#settings-form").addEventListener("submit", saveSettings);
  $("#settings-test").addEventListener("click", testConnection);
  $("#settings-delete").addEventListener("click", deleteKey);
  $("#settings-motion").addEventListener("change", (event) =>
    applyMotionPreference(event.target.checked));

  $("#approval-allow").addEventListener("click", () => closeApproval(null, true));
  $("#approval-deny").addEventListener("click", () => closeApproval(null, false));

  /* keyboard shortcuts */
  addEventListener("keydown", (event) => {
    if (!State.booted) return;
    if (event.altKey && !event.ctrlKey && !event.shiftKey) {
      const digit = event.key === "0" ? 9 : parseInt(event.key, 10) - 1;
      if (digit >= 0 && digit < NAV.length) {
        event.preventDefault();
        showScreen(NAV[digit][0]);
        return;
      }
      if (event.key.toLowerCase() === "s") {
        event.preventDefault(); showScreen("settings"); return;
      }
    }
    if (event.ctrlKey && event.key.toLowerCase() === "l") {
      event.preventDefault();
      if (State.screen === "home") $("#quick-input").focus();
      else { showScreen("chat"); $("#chat-input").focus(); }
    }
    if (event.ctrlKey && event.key === ",") {
      event.preventDefault(); showScreen("settings");
    }
    if (event.ctrlKey && event.key.toLowerCase() === "m") {
      event.preventDefault(); toggleVoice();
    }
    if (event.ctrlKey && event.shiftKey && event.key.toLowerCase() === "t") {
      event.preventDefault();
      const light = document.body.classList.toggle("light");
      store("nova.theme", light ? "light" : "dark");
    }
    if (event.key === "Escape" && !activeApproval && !confirmOpen) {
      if (State.voiceActive || Engine.hud?.active) toggleVoice();
      else showScreen("home");
    }
    if (handleScrollKeys(event)) event.preventDefault();
  });

  /* clock */
  const tickClock = () => {
    const now = new Date();
    $("#clock-time").textContent = now.toLocaleTimeString("tr-TR",
      { hour: "2-digit", minute: "2-digit" });
    $("#clock-date").textContent = now.toLocaleDateString("tr-TR",
      { day: "2-digit", month: "long", weekday: "long" });
  };
  tickClock();
  setInterval(tickClock, 10_000);
}

async function main() {
  if (store("nova.theme") === "light") document.body.classList.add("light");
  document.body.classList.toggle("reduced-motion", State.reducedMotion);

  buildRail();
  renderShortcuts();
  bindEvents();
  Engine.staticFrame = State.reducedMotion;
  Engine.init();

  $$("#rail .nav-btn")[0].classList.add("active");
  $(".screen[data-screen='home']").classList.add("active");
  requestAnimationFrame(() => { moveRailIndicator(); Engine.resize(); });

  let bootData = null;
  if (demoRequested()) {
    Bridge = DemoBridge;
    bootData = await Bridge.boot();
  } else {
    const connected = await resolveBridge();
    if (!connected) {
      showBootFailure(
        "Python çekirdeğiyle bağlantı kurulamadı (pywebview köprüsü " +
        "zamanında gelmedi). Hiçbir veri gösterilmiyor ve hiçbir eylem " +
        "simüle edilmiyor. Uygulamayı yeniden başlatmayı dene; sorun " +
        "sürerse JARVIS'i --classic bayrağıyla açabilirsin.");
      return;
    }
    try {
      bootData = await Bridge.boot();
    } catch (err) {
      showBootFailure("Çekirdek başlatılamadı: " + describeError(err));
      return;
    }
    if (!bootData || !bootData.snapshot) {
      showBootFailure("Çekirdek geçerli bir başlangıç durumu döndürmedi.");
      return;
    }
  }
  applyBoot(bootData);
  requestAnimationFrame(() => { moveRailIndicator(); Engine.resize(); });
  await runBootSequence();
  if (State.demo) toast("Demo modu: çekirdek bağlı değil, tüm veriler örnektir.", true);
}

document.addEventListener("DOMContentLoaded", main);
