/* ════════════════════════════════════════════════════════════════════
   JARVIS · NOVA — shell
   Navigation rail, screens, topbar readouts, context drawer, command
   palette, compact window, pause gate, boot sequence, keyboard.
   ════════════════════════════════════════════════════════════════════ */
"use strict";

/* ── one icon family: 1.5px line icons on a 24-unit grid ─────────── */

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
  palette: '<rect x="3.5" y="5" width="17" height="14" rx="2"/><path d="M7 12h3M12 9l3 3-3 3"/>',
  context: '<rect x="3.5" y="4.5" width="17" height="15" rx="2"/><path d="M14.5 4.5v15"/>',
  compact: '<path d="M9 4H4v5M15 20h5v-5M4 15v5h5M20 9V4h-5"/>',
  expand: '<path d="M4 9V4h5M20 15v5h-5M15 4h5v5M9 20H4v-5"/>',
  send: '<path d="M4 12 20 4l-4.5 8L20 20 4 12Zm0 0h9"/>',
  search: '<circle cx="10.5" cy="10.5" r="6"/><path d="M15 15l6 6"/>',
  close: '<path d="M6 6l12 12M18 6L6 18"/>',
  chevron: '<path d="M14.5 5.5 8 12l6.5 6.5"/>',
  check: '<path d="M5 12.5l4.5 4.5L19 7.5"/>',
  spark: '<path d="M12 3v4M12 17v4M3 12h4M17 12h4M6 6l2.5 2.5M15.5 15.5 18 18M18 6l-2.5 2.5M8.5 15.5 6 18"/>',
  plus: '<path d="M12 5v14M5 12h14"/>',
  archive: '<rect x="3.5" y="4.5" width="17" height="4"/><path d="M5 8.5v11h14v-11M10 12.5h4"/>',
  refresh: '<path d="M20 12a8 8 0 1 1-2.3-5.6M20 4v4.5h-4.5"/>',
  pause: '<path d="M8 5v14M16 5v14"/>',
  play: '<path d="M7 4.5v15l12-7.5z"/>',
  edit: '<path d="M4 20h4l11-11-4-4L4 16z"/>',
  trash: '<path d="M4 7h16M9 7V4h6v3M6 7l1 13h10l1-13"/>',
  forget: '<path d="M4 12a8 8 0 1 0 2.3-5.6M4 4v4.5h4.5M12 8v4l3 2"/>',
  history: '<circle cx="12" cy="12" r="8"/><path d="M12 7.5V12l3 2"/>',
  app: '<rect x="4" y="4" width="16" height="16" rx="3"/><path d="M8 12h8M12 8v8"/>',
  stop: '<rect x="6" y="6" width="12" height="12" rx="1.5"/>',
  moon: '<path d="M20 14.5A8 8 0 0 1 9.5 4a8 8 0 1 0 10.5 10.5z"/>',
  motion: '<path d="M3 12c3-5 6-5 9 0s6 5 9 0"/>',
  bell: '<path d="M6.5 16.5V11a5.5 5.5 0 0 1 11 0v5.5l1.5 1.5H5z"/><path d="M10 20.5a2 2 0 0 0 4 0"/>',
  alarm: '<circle cx="12" cy="13" r="7"/><path d="M12 9.5V13l2.5 1.5M4.5 6.5 7 4M19.5 6.5 17 4"/>',
};
function icon(name) {
  return `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${ICONS[name] || ""}</svg>`;
}

/* ── navigation ───────────────────────────────────────────────────── */

const NAV = [
  ["home", "Komuta Merkezi", "Alt+1"], ["chat", "Sohbet", "Alt+2"], ["tasks", "Görevler", "Alt+3"],
  ["memory", "Hafıza", "Alt+4"], ["voice", "Ses", "Alt+5"], ["vision", "Görüş", "Alt+6"],
  ["research", "Araştırma", "Alt+7"], ["tools", "Otomasyon", "Alt+8"],
  ["integrations", "Güven", "Alt+9"], ["diagnostics", "Tanılama", "Alt+0"],
  ["settings", "Ayarlar", "Ctrl+,"],
];

function buildRail() {
  const host = $("#rail-items");
  NAV.forEach(([id, label, key]) => {
    const btn = el("button", "nav-btn");
    btn.dataset.screen = id; btn.type = "button"; btn.title = `${label} (${key})`;
    btn.innerHTML = `${icon(id)}<span class="nav-label">${esc(label)}</span><span class="nav-key">${esc(key)}</span><span class="nav-badge"></span>`;
    btn.addEventListener("click", () => showScreen(id));
    host.appendChild(btn);
  });
  $("#rail-toggle").innerHTML = `${icon("chevron")}<span>Daralt</span>`;
  $("#rail-toggle").addEventListener("click", () => setRailCollapsed(!State.railCollapsed));
  setRailCollapsed(State.railCollapsed, true);
}

function setRailCollapsed(collapsed, silent) {
  State.railCollapsed = collapsed;
  $("#app").classList.toggle("rail-collapsed", collapsed);
  store("nova.rail", collapsed ? "collapsed" : "open");
  $("#rail-toggle").title = collapsed ? "Gezinmeyi genişlet" : "Gezinmeyi daralt";
  if (!silent) setTimeout(() => { moveRailIndicator(); Engine.resize(); }, Motion.panel + 20);
}

function moveRailIndicator() {
  const active = $(`#rail .nav-btn[data-screen="${State.screen}"]`);
  const indicator = $("#rail-indicator");
  if (!active) { indicator.style.opacity = "0"; return; }
  indicator.style.opacity = "1";
  const railRect = $("#rail").getBoundingClientRect();
  const rect = active.getBoundingClientRect();
  const offset = rect.top - railRect.top + (rect.height - indicator.offsetHeight) / 2;
  indicator.style.transform = `translateY(${offset}px)`;
}

function showScreen(id, { focus = true } = {}) {
  if (!NAV.some(([screen]) => screen === id)) return;
  if (State.screen === id) return;
  State.screen = id;
  $$("#rail .nav-btn").forEach((b) => b.classList.toggle("active", b.dataset.screen === id));
  moveRailIndicator();

  const next = $(`.screen[data-screen="${id}"]`);
  $$(".screen").forEach((s) => s.classList.toggle("active", s === next));
  if (Motion.allowed()) {
    next.animate(
      [{ opacity: 0, transform: "translateY(10px) scale(0.995)" },
       { opacity: 1, transform: "translateY(0) scale(1)" }],
      { duration: 380, easing: Motion.enter });
    Motion.stagger($$(".stagger > *, .card-grid > *, .task-list > *, .memory-groups > *, .tool-groups > *, .diag-grid > *, .trust-grid > *", next));
  }
  if (id === "chat") { scrollChat({ force: true, instant: true }); if (focus) $("#chat-input").focus(); }
  if (id === "home" && focus) $("#quick-input").focus();
  if (id === "diagnostics") Diagnostics.refresh({ quiet: true });
  if (id === "memory") Memory.load();
  if (id === "integrations") Trust.refresh();
  if (id === "tasks") renderTasks(State.snapshot?.tasks || []);
  if (id === "settings") Files.load();
  requestAnimationFrame(() => Engine.resize());
  Engine.wake();
}

/* ── status, busy, pause ──────────────────────────────────────────── */

function setStatus(status) {
  State.status = status;
  Presence.apply();
}

function setBusy(busy, status) {
  State.busy = busy;
  Presence.busy = busy;
  if (!busy) Presence.streaming = false;
  if (status) State.status = status;
  $("#composer-send").disabled = busy || State.paused;
  $("#quick-send").disabled = busy || State.paused;
  $(`#rail .nav-btn[data-screen="chat"]`).classList.toggle("live", busy);
  Presence.apply();
}

function setPaused(paused) {
  const changed = State.paused !== paused;
  State.paused = paused;
  Presence.paused = paused;
  document.body.classList.toggle("paused", paused);
  $("#composer-send").disabled = paused || State.busy;
  $("#quick-send").disabled = paused || State.busy;
  $("#chat-input").disabled = paused;
  $("#quick-input").disabled = paused;
  $("#mini-input").disabled = paused;
  $("#pause-btn").innerHTML = paused ? icon("play") : icon("pause");
  $("#pause-btn").title = paused ? "Devam et" : "JARVIS'i duraklat";
  $("#pause-btn").classList.toggle("active", paused);
  Presence.apply();
  if (changed) {
    toast(paused ? "JARVIS duraklatıldı. Devam etmek için tepsiden ya da üst çubuktan Devam'ı seç."
                 : "JARVIS devam ediyor.");
  }
}

async function togglePause() {
  const result = await call("set_paused", !State.paused);
  if (result.ok === false) toast(result.error || "Duraklatma değiştirilemedi.", true);
  else if (State.demo) setPaused(!!result.paused);
}

/* ── topbar: model chip, capabilities, clock ──────────────────────── */

function renderSystemChips() {
  const s = State.snapshot;
  if (!s) return;
  const caps = [["SES", s.voice_available], ["GÖRÜŞ", s.vision_available],
                ["WEB", s.research_available], ["SİSTEM", s.windows_available]];
  $("#system-chips").innerHTML =
    `<span class="model-chip" title="Etkin model">${esc(s.model || s.provider || "—")}</span>` +
    `<span class="cap-dots" title="Yetenek durumu">${caps.map(([name, on]) =>
      `<span class="cap-dot ${on ? "on" : ""}"><i></i>${name}</span>`).join("")}</span>`;
}

function startClock() {
  const tick = () => {
    const now = new Date();
    $("#clock-time").textContent = fmtClock(now);
    $("#clock-date").textContent = now.toLocaleDateString("tr-TR", { day: "2-digit", month: "long", weekday: "long" });
  };
  tick();
  setInterval(tick, 10_000);
}

/* ── notification centre: what happened while you were not looking ── */

const Notify = {
  open: false,
  pending: [],              // pushes that arrived before boot finished

  apply(state) {
    State.notifications = Array.isArray(state?.items) ? state.items.slice() : [];
    State.unread = Number(state?.unread) || 0;
    const pending = this.pending.splice(0);
    pending.forEach((payload) => this.merge(payload));
    this.renderBadge();
    if (this.open) this.render();
  },

  merge({ notification, unread }) {
    if (!notification || !notification.notification_id) return;
    const index = State.notifications.findIndex((n) => n.notification_id === notification.notification_id);
    if (index >= 0) State.notifications.splice(index, 1);
    State.notifications.unshift(notification);
    State.notifications.length = Math.min(State.notifications.length, 100);
    if (Number.isFinite(Number(unread))) State.unread = Number(unread);
  },

  onPush(payload) {
    if (!State.booted) { this.pending.push(payload); return; }
    const wasKnown = State.notifications.some((n) => n.notification_id === payload.notification?.notification_id);
    this.merge(payload);
    this.renderBadge();
    if (this.open) this.render();
    const item = payload.notification;
    if (!item || wasKnown) return;
    /* The approval overlay is already on screen for its own kind. */
    if (item.kind !== "approval") toast(`${item.title} · ${item.body}`.slice(0, 220), item.severity === "error" ? "err" : "");
    if (item.kind === "reminder") this.chatLine(`⏰ Hatırlatıcı: ${item.body}`);
    else if (item.kind === "observation") this.chatLine(`👁 Ekran: ${item.body}`);
    Engine.wake();
  },

  /* Reminders and screen observations also land in the conversation,
     as the classic shell does; the centre keeps the durable record. */
  chatLine(text) {
    updateChat(() => appendMessage($("#chat-list"), { role: "system", text, at: Date.now() }, false));
  },

  set(open) {
    if (open === this.open) return;
    this.open = open;
    $("#notify-panel").hidden = !open;
    $("#notify-btn").classList.toggle("active", open);
    $("#notify-btn").setAttribute("aria-expanded", open ? "true" : "false");
    if (open) { this.render(); $("#notify-close").focus(); }
  },
  toggle() { this.set(!this.open); },

  renderBadge() {
    const badge = $("#notify-badge");
    const unread = State.unread;
    badge.hidden = unread <= 0;
    badge.textContent = unread > 99 ? "99+" : String(unread);
    $("#notify-btn").classList.toggle("has-unread", unread > 0);
    $("#notify-btn").title = unread > 0 ? `Bildirimler · ${unread} okunmamış (Ctrl+Shift+N)` : "Bildirimler (Ctrl+Shift+N)";
  },

  render() {
    const host = $("#notify-list");
    const items = State.notifications;
    $("#notify-summary").textContent = items.length ? `${items.length} kayıt · ${State.unread} okunmamış` : "";
    $("#notify-read-all").disabled = State.unread === 0;
    $("#notify-clear").disabled = items.length === 0;
    if (!items.length) {
      host.innerHTML = '<div class="notify-empty">Bildirim yok. Hatırlatıcılar, sen bakmazken gelen yanıtlar ve onay istekleri, tanılama uyarıları burada birikir.</div>';
      return;
    }
    host.innerHTML = items.map((item) => `
      <div class="notify-item ${item.read ? "" : "unread"} ${esc(item.severity)}" data-id="${esc(item.notification_id)}" role="button" tabindex="0">
        <span class="notify-icon" title="${esc(NOTIFICATION_KIND_TR[item.kind] || item.kind)}">${icon(NOTIFICATION_KIND_ICON[item.kind] || "spark")}</span>
        <span><div class="notify-title">${esc(item.title)}</div><div class="notify-body">${esc(item.body)}</div></span>
        <span class="notify-meta"><span class="notify-time" title="${esc(fmtTime(item.updated_at))}">${esc(fmtRelative(item.updated_at))}</span>${item.count > 1 ? `<span class="notify-count">×${item.count}</span>` : ""}<button type="button" class="icon-btn small notify-dismiss" data-act="dismiss" title="Kaldır">${icon("close")}</button></span>
      </div>`).join("");
    $$(".notify-item", host).forEach((row) => {
      const id = row.dataset.id;
      row.addEventListener("click", (event) => {
        if (event.target.closest("[data-act='dismiss']")) { event.stopPropagation(); this.dismiss(id); return; }
        this.activate(id);
      });
      row.addEventListener("keydown", (event) => {
        if (event.key === "Enter" || event.key === " ") { event.preventDefault(); this.activate(id); }
        else if (event.key === "Delete") { event.preventDefault(); this.dismiss(id); }
      });
    });
  },

  async activate(id) {
    const item = State.notifications.find((n) => n.notification_id === id);
    if (!item) return;
    if (!item.read) {
      const result = await call("mark_notifications_read", [id]);
      if (result.ok === false) { toast(result.error || "Bildirim güncellenemedi.", true); return; }
      item.read = true;
      State.unread = Number(result.unread) || 0;
      this.renderBadge();
    }
    if (item.target && NAV.some(([screen]) => screen === item.target)) { this.set(false); showScreen(item.target); }
    else this.render();
  },

  async readAll() {
    const result = await call("mark_notifications_read", null);
    if (result.ok === false) { toast(result.error || "Bildirimler güncellenemedi.", true); return; }
    State.notifications.forEach((n) => { n.read = true; });
    State.unread = Number(result.unread) || 0;
    this.renderBadge();
    this.render();
  },

  async dismiss(id) {
    const result = await call("dismiss_notification", id);
    if (result.ok === false) { toast(result.error || "Bildirim kaldırılamadı.", true); return; }
    State.notifications = State.notifications.filter((n) => n.notification_id !== id);
    State.unread = Number(result.unread) || 0;
    this.renderBadge();
    this.render();
  },

  async clear() {
    const result = await call("clear_notifications");
    if (result.ok === false) { toast(result.error || "Bildirimler temizlenemedi.", true); return; }
    State.notifications = [];
    State.unread = 0;
    this.renderBadge();
    this.render();
  },
};

/* The bridge routes attention (native notifications) by whether the
   page is visible; the browser knows that better than pywebview does. */
function reportVisibility() {
  if (!Bridge || !State.booted || State.demo) return;
  try {
    const result = Bridge.set_visible(!document.hidden);
    if (result && typeof result.catch === "function") result.catch(() => {});
  } catch (err) { /* the window is closing; nothing to report */ }
}

/* ── context drawer: only what matters right now ──────────────────── */

const Context = {
  autoOpened: false,
  _closeTimer: 0,

  set(open, { auto = false } = {}) {
    State.contextOpen = open;
    $("#app").classList.toggle("context-open", open);
    $("#context-btn").classList.toggle("active", open);
    if (!auto) { store("nova.context", open ? "open" : "closed"); this.autoOpened = false; }
    else this.autoOpened = open;
    if (open) this.render();
    setTimeout(() => Engine.resize(), Motion.panel + 20);
  },
  toggle() { this.set(!State.contextOpen); },

  /* Activity opens the drawer; it closes itself a while after the
     activity settles, unless the reader opened it deliberately. */
  autoOpen() {
    clearTimeout(this._closeTimer);
    if (!State.contextOpen && !State.compact && innerWidth >= 1180) this.set(true, { auto: true });
    else this.render();
  },
  autoSettle() {
    clearTimeout(this._closeTimer);
    if (!this.autoOpened) return;
    this._closeTimer = setTimeout(() => {
      if (this.autoOpened && !Activity.active()) this.set(false, { auto: true });
    }, 25_000);
  },

  render() {
    if (!State.contextOpen) return;
    Activity.renderTimeline($("#ctx-timeline"));
    const s = State.snapshot;
    const task = (s?.tasks || []).find((item) => ["running", "waiting_for_input", "waiting_for_approval"].includes(String(item.status)));
    const taskSection = $("#ctx-task");
    taskSection.hidden = !task;
    if (task) $("#ctx-task-body").innerHTML = taskCardHTML(task, { compact: true });
    if (s) {
      const rows = [
        ["Sağlayıcı", `${s.provider} · ${s.model}`, "ok"],
        ["Ses", s.voice_available ? "hazır" : "kapalı", s.voice_available ? "ok" : ""],
        ["Görüş", s.vision_available ? "hazır" : "kapalı", s.vision_available ? "ok" : ""],
        ["Web", s.research_available ? "hazır" : "kapalı", s.research_available ? "ok" : ""],
        ["Windows", s.windows_available ? "hazır" : "kapalı", s.windows_available ? "ok" : ""],
        ["Olay defteri", s.diagnostic_integrity_valid ? "bütünlük doğrulandı" : "bütünlük HATASI", s.diagnostic_integrity_valid ? "ok" : "bad"],
      ];
      $("#ctx-system-body").innerHTML = rows.map(([name, note, light]) =>
        `<div class="status-row"><span class="status-light ${light}"></span><span class="status-name">${esc(name)}</span><span class="status-note">${esc(note)}</span></div>`).join("");
      const memories = (s.memories || []).slice(0, 5);
      $("#ctx-memory-body").innerHTML = memories.length
        ? memories.map((m) => `<div class="status-row"><span class="status-light ${m.freshness === "current" ? "ok" : "warn"}"></span><span class="status-name" title="${esc(m.content)}">${esc(m.content)}</span></div>`).join("")
        : '<div class="ctx-empty">Henüz kayıtlı bir anı yok.</div>';
    }
  },
};

/* ── command palette ──────────────────────────────────────────────── */

const Palette = {
  open: false, selected: 0, items: [],

  commands(query) {
    const q = query.trim();
    const list = [];
    NAV.forEach(([id, label]) => list.push({ group: "ekran", icon: id, label: `${label}`, keywords: "git ekran " + label, run: () => showScreen(id) }));
    list.push({ group: "eylem", icon: "voice", label: State.voiceActive ? "Sesli modu durdur" : "Sesli modu başlat", keywords: "ses mikrofon konuş", run: () => toggleVoice() });
    list.push({ group: "eylem", icon: "plus", label: "Yeni konuşma", keywords: "sohbet temiz", run: () => newConversation() });
    list.push({ group: "eylem", icon: State.paused ? "play" : "pause", label: State.paused ? "JARVIS'i sürdür" : "JARVIS'i duraklat", keywords: "dur bekle devam", run: () => togglePause() });
    list.push({ group: "eylem", icon: State.compact ? "expand" : "compact", label: State.compact ? "Tam görünüme dön" : "Kompakt moda geç", keywords: "mini küçük pencere", run: () => setCompact(!State.compact) });
    list.push({ group: "eylem", icon: "context", label: State.contextOpen ? "Bağlam panelini kapat" : "Bağlam panelini aç", keywords: "panel yürütme", run: () => Context.toggle() });
    list.push({ group: "eylem", icon: "bell", label: State.unread > 0 ? `Bildirimler (${State.unread} okunmamış)` : "Bildirimler", keywords: "bildirim hatırlatıcı uyarı", run: () => Notify.set(true) });
    list.push({ group: "eylem", icon: "chevron", label: State.railCollapsed ? "Gezinmeyi genişlet" : "Gezinmeyi daralt", keywords: "menü rail", run: () => setRailCollapsed(!State.railCollapsed) });
    list.push({ group: "eylem", icon: "refresh", label: "Sistem sağlığını denetle", keywords: "tanılama health", run: () => { showScreen("diagnostics"); Diagnostics.refresh(); } });
    list.push({ group: "görünüm", icon: "motion", label: State.reducedMotion ? "Hareketi geri aç" : "Hareketi azalt", keywords: "animasyon", run: () => applyMotionPreference(!State.reducedMotion) });
    list.push({ group: "görünüm", icon: "moon", label: document.body.classList.contains("light") ? "Koyu tema" : "Açık tema", keywords: "tema light dark", run: () => toggleTheme() });
    (State.runtime?.applications || []).forEach((app) =>
      list.push({ group: "uygulama", icon: "app", label: `Aç: ${app.name}`, keywords: "uygulama başlat " + app.name + " " + app.id, run: () => { showScreen("chat"); sendCommand(`${app.name} uygulamasını aç`); } }));
    const scored = list.map((item) => {
      const score = q ? Math.max(fuzzyScore(q, item.label) ?? -1, (fuzzyScore(q, item.keywords) ?? -1) * 0.6) : 0;
      return { item, score };
    }).filter(({ score }) => !q || score >= 0).sort((a, b) => b.score - a.score).slice(0, 9).map(({ item }) => item);
    if (q) {
      scored.push({ group: "sor", icon: "spark", label: `JARVIS'e sor: “${q}”`, run: () => { showScreen("chat"); sendCommand(q); } });
      if (State.snapshot?.research_available) scored.push({ group: "araştır", icon: "research", label: `Araştır: “${q}”`, run: () => { showScreen("research"); $("#research-input").value = q; $("#research-form").requestSubmit(); } });
      if (State.snapshot?.vision_available) scored.push({ group: "görüş", icon: "vision", label: `Ekranı incele: “${q}”`, run: () => { showScreen("vision"); $("#vision-input").value = q; $("#vision-form").requestSubmit(); } });
    }
    return scored;
  },

  show() {
    if (this.open || !State.booted) return;
    this.open = true;
    $("#palette").hidden = false;
    const input = $("#palette-input");
    input.value = "";
    this.selected = 0;
    this.render();
    input.focus();
  },
  hide() {
    if (!this.open) return;
    this.open = false;
    $("#palette").hidden = true;
  },
  render() {
    const query = $("#palette-input").value;
    this.items = this.commands(query);
    this.selected = clamp(this.selected, 0, Math.max(0, this.items.length - 1));
    const host = $("#palette-list");
    if (!this.items.length) { host.innerHTML = '<div class="palette-empty">Eşleşen komut yok.</div>'; return; }
    host.innerHTML = this.items.map((item, index) =>
      `<button type="button" class="palette-item ${index === this.selected ? "selected" : ""}" data-index="${index}" role="option" aria-selected="${index === this.selected}">
         ${icon(item.icon)}<span class="pi-label">${highlight(item.label, query)}</span><span class="pi-group">${esc(item.group)}</span></button>`).join("");
    $$(".palette-item", host).forEach((node) => {
      node.addEventListener("mouseenter", () => { this.selected = Number(node.dataset.index); this.render(); });
      node.addEventListener("click", () => this.run(Number(node.dataset.index)));
    });
    host.querySelector(".selected")?.scrollIntoView({ block: "nearest" });
  },
  move(delta) {
    if (!this.items.length) return;
    this.selected = (this.selected + delta + this.items.length) % this.items.length;
    this.render();
  },
  run(index) {
    const item = this.items[index ?? this.selected];
    if (!item) return;
    this.hide();
    item.run();
  },
};

function highlight(label, query) {
  const q = lower(query);
  if (!q) return esc(label);
  const index = lower(label).indexOf(q);
  if (index < 0) return esc(label);
  return esc(label.slice(0, index)) + "<b>" + esc(label.slice(index, index + q.length)) + "</b>" + esc(label.slice(index + q.length));
}

/* ── compact window (mini JARVIS) ─────────────────────────────────── */

async function setCompact(enabled) {
  if (!bridgeReady()) return;
  const result = await call("set_compact", enabled);
  if (result.ok === false) { toast(result.error || "Kompakt mod değiştirilemedi.", true); return; }
  applyCompact(!!result.compact);
}

function applyCompact(compact) {
  State.compact = compact;
  document.body.classList.toggle("compact", compact);
  $("#mini").hidden = !compact;
  $("#compact-btn").classList.toggle("active", compact);
  if (compact) { Palette.hide(); renderMiniLine(); $("#mini-input").focus(); }
  requestAnimationFrame(() => Engine.resize());
  Engine.wake();
}

function renderMiniLine(text) {
  const line = $("#mini-line");
  if (text) { line.textContent = text; return; }
  const last = [...State.messages].reverse().find((m) => m.role === "assistant");
  line.textContent = last ? last.text : "Sistemler hazır. Bir komut ver ya da mikrofona dokun.";
}

/* ── theme & motion ───────────────────────────────────────────────── */

function toggleTheme() {
  const light = document.body.classList.toggle("light");
  store("nova.theme", light ? "light" : "dark");
  Engine.wake();
}

function applyMotionPreference(reduced) {
  State.reducedMotion = reduced;
  document.body.classList.toggle("reduced-motion", reduced);
  store("nova.motion", reduced ? "off" : "on");
  Engine.staticFrame = reduced;
  const toggle = $("#settings-motion");
  if (toggle) toggle.checked = reduced;
  Engine.wake();
}

function applyAmbientPreference(enabled) {
  State.ambient = enabled;
  store("nova.ambient", enabled ? "on" : "off");
  const toggle = $("#settings-ambient");
  if (toggle) toggle.checked = enabled;
  Engine.wake();
}

/* ── keyboard ─────────────────────────────────────────────────────── */

function activeScroller() {
  return State.screen === "chat" ? $("#chat-scroll") : $(".screen.active");
}

function handleScrollKeys(event) {
  if (event.ctrlKey || event.altKey || event.metaKey) return false;
  const target = event.target;
  const tag = (target && target.tagName || "").toLowerCase();
  if (["input", "textarea", "select"].includes(tag) || (target && target.isContentEditable)) return false;
  if (activeApproval || confirmOpen || Palette.open || VoiceStage.active) return false;
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

function bindKeyboard() {
  addEventListener("keydown", (event) => {
    if (!State.booted) return;
    const key = event.key.toLowerCase();
    if (Palette.open) {
      if (event.key === "Escape") { event.preventDefault(); Palette.hide(); }
      else if (event.key === "ArrowDown") { event.preventDefault(); Palette.move(1); }
      else if (event.key === "ArrowUp") { event.preventDefault(); Palette.move(-1); }
      else if (event.key === "Enter") { event.preventDefault(); Palette.run(); }
      else if (event.ctrlKey && key === "k") { event.preventDefault(); Palette.hide(); }
      return;
    }
    if (event.ctrlKey && key === "k") { event.preventDefault(); Palette.show(); return; }
    if (event.altKey && !event.ctrlKey && !event.shiftKey) {
      const digit = event.key === "0" ? 9 : parseInt(event.key, 10) - 1;
      if (digit >= 0 && digit < NAV.length) { event.preventDefault(); showScreen(NAV[digit][0]); return; }
    }
    if (event.ctrlKey && key === "l") {
      event.preventDefault();
      if (State.compact) $("#mini-input").focus();
      else if (State.screen === "home") $("#quick-input").focus();
      else { showScreen("chat"); $("#chat-input").focus(); }
    }
    if (event.ctrlKey && event.key === ",") { event.preventDefault(); showScreen("settings"); }
    if (event.ctrlKey && key === "m") { event.preventDefault(); toggleVoice(); }
    if (event.ctrlKey && key === "n" && !event.shiftKey) { event.preventDefault(); newConversation(); }
    if (event.ctrlKey && event.shiftKey && key === "t") { event.preventDefault(); toggleTheme(); }
    if (event.ctrlKey && event.shiftKey && key === "c") { event.preventDefault(); Context.toggle(); }
    if (event.ctrlKey && event.shiftKey && key === "n") { event.preventDefault(); Notify.toggle(); }
    if (event.key === "Escape" && Notify.open) { event.preventDefault(); Notify.set(false); return; }
    if (event.ctrlKey && event.shiftKey && key === "b") { event.preventDefault(); setRailCollapsed(!State.railCollapsed); }
    if (event.key === "Escape" && !activeApproval && !confirmOpen) {
      if (VoiceStage.active) toggleVoice();
      else if (State.compact) setCompact(false);
      else showScreen("home");
    }
    if (handleScrollKeys(event)) event.preventDefault();
  });
}

const SHORTCUTS = [
  ["Ctrl + K", "Komut paleti"], ["Enter", "Komutu gönder"], ["Shift + Enter", "Yeni satır"],
  ["Ctrl + L", "Komut alanına odaklan"], ["Ctrl + M", "Sesli modu aç/kapat"],
  ["Ctrl + N", "Yeni konuşma"], ["Ctrl + ,", "Ayarlar"],
  ["Ctrl + Shift + C", "Bağlam paneli"], ["Ctrl + Shift + N", "Bildirimler"],
  ["Ctrl + Shift + B", "Gezinmeyi daralt/genişlet"],
  ["Ctrl + Shift + T", "Koyu/açık tema"], ["Alt + 1…9", "Ekranlar"], ["Alt + 0", "Tanılama"],
  ["↑ / ↓ · Page Up / Down", "Ekranı kaydır"], ["Home / End", "Başa / sona git"],
  ["Escape", "Kapat · Komuta Merkezi'ne dön"],
];

/* ── boot: real subsystem state, briefly staged ───────────────────── */

function bootLines(bootData) {
  const s = bootData.snapshot || {};
  const on = (flag) => (flag ? "ok" : "off");
  return [
    ["çekirdek", State.demo ? "demo · bağlı değil" : "çevrimiçi", State.demo ? "warn" : "ok"],
    ["sağlayıcı", `${s.provider || "—"} · ${s.model || "—"}`, "ok"],
    ["hafıza", `${s.memory_count ?? 0} anı`, "ok"],
    ["araçlar", `${s.enabled_tools ?? 0} / ${s.tool_count ?? 0} etkin`, "ok"],
    ["ses", s.voice_available ? "hazır" : "kapalı", on(s.voice_available)],
    ["görüş", s.vision_available ? "hazır" : "kapalı", on(s.vision_available)],
    ["otomasyon", s.windows_available ? "hazır" : "kapalı", on(s.windows_available)],
    ["olay defteri", s.diagnostic_integrity_valid ? "bütünlük doğrulandı" : "bütünlük hatası", s.diagnostic_integrity_valid ? "ok" : "warn"],
  ];
}

async function runBootSequence(bootData) {
  const boot = $("#boot"), log = $("#boot-log");
  const finish = () => {
    boot.classList.add("gone");
    $("#app").classList.remove("pre-boot");
    Engine.wake();
    setTimeout(() => { moveRailIndicator(); Engine.resize(); }, 50);
  };
  if (State.reducedMotion) { finish(); return; }
  for (const [name, value, kind] of bootLines(bootData)) {
    const row = el("div", "row");
    row.innerHTML = `<span>${esc(name)}</span><span class="${kind}">${esc(value)}</span>`;
    log.appendChild(row);
    row.animate([{ opacity: 0, transform: "translateY(4px)" }, { opacity: 1, transform: "translateY(0)" }], { duration: 220, fill: "both", easing: Motion.enter });
    await new Promise((resolve) => setTimeout(resolve, 95));
  }
  const final = el("div", "final");
  final.innerHTML = State.demo
    ? '<span class="warn">DEMO MODU · ÇEKİRDEK BAĞLI DEĞİL · VERİLER ÖRNEK</span>'
    : "JARVIS HAZIR";
  log.appendChild(final);
  final.animate([{ opacity: 0 }, { opacity: 1 }], { duration: 260, fill: "both" });
  await new Promise((resolve) => setTimeout(resolve, 420));
  finish();
}

/* The shell stays hidden; the reader sees exactly what went wrong. */
function showBootFailure(message) {
  const boot = $("#boot");
  boot.classList.add("failed");
  $("#boot-error-text").textContent = message;
  $("#boot-error").hidden = false;
  $("#boot-retry").onclick = () => window.location.reload();
  $("#boot-retry").focus();
  Presence.connected = false;
  Presence.apply();
}

function applyBoot(bootData) {
  State.booted = true;
  State.snapshot = bootData.snapshot;
  State.settings = bootData.settings;
  State.runtime = bootData.runtime || null;
  State.messages = bootData.messages || [];
  State.voiceMessages = bootData.voiceMessages || [];
  State.conversations = bootData.conversations || [];
  State.fileRoots = bootData.fileRoots || { available: false, roots: [] };
  Notify.apply(bootData.notifications);
  $("#demo-badge").hidden = !State.demo;
  Presence.connected = true;
  State.status = bootData.status || READY;
  renderSnapshot();
  renderSettings();
  renderShortcuts();
  renderApprovalLog();
  renderConversations();
  renderChatHistory();
  State.voiceMessages.forEach((message) => appendMessage($("#voice-list"), message, true));
  renderGreeting();
  setPaused(!!bootData.paused);
  if (bootData.compact) applyCompact(true);
  Context.set(State.contextOpen);
  scrollChat({ force: true, instant: true });
  Presence.apply();
  reportVisibility();
}

function bindShell() {
  $("#palette-btn").innerHTML = `${icon("palette")}<kbd>Ctrl K</kbd>`;
  $("#palette-btn").addEventListener("click", () => Palette.show());
  $("#context-btn").innerHTML = icon("context");
  $("#context-btn").addEventListener("click", () => Context.toggle());
  $("#context-close").innerHTML = icon("close");
  $("#context-close").addEventListener("click", () => Context.set(false));
  $("#notify-btn").insertAdjacentHTML("afterbegin", icon("bell"));
  $("#notify-btn").addEventListener("click", () => Notify.toggle());
  $("#notify-close").innerHTML = icon("close");
  $("#notify-close").addEventListener("click", () => Notify.set(false));
  $("#notify-read-all").addEventListener("click", () => Notify.readAll());
  $("#notify-clear").addEventListener("click", () => Notify.clear());
  document.addEventListener("click", (event) => {
    if (!Notify.open) return;
    if (event.target.closest("#notify-panel") || event.target.closest("#notify-btn")) return;
    Notify.set(false);
  });
  document.addEventListener("visibilitychange", reportVisibility);
  $("#compact-btn").innerHTML = icon("compact");
  $("#compact-btn").addEventListener("click", () => setCompact(true));
  $("#pause-btn").innerHTML = icon("pause");
  $("#pause-btn").addEventListener("click", togglePause);
  $("#palette").addEventListener("click", (event) => { if (event.target === $("#palette")) Palette.hide(); });
  $("#palette-input").addEventListener("input", () => { Palette.selected = 0; Palette.render(); });
  $("#mini-expand").innerHTML = icon("expand");
  $("#mini-expand").addEventListener("click", () => setCompact(false));
  $("#mini-mic").innerHTML = icon("voice");
  $("#mini-mic").addEventListener("click", () => toggleVoice());
  $("#mini-form").addEventListener("submit", (event) => {
    event.preventDefault();
    const text = $("#mini-input").value;
    if (!text.trim()) return;
    if (State.busy) { toast("JARVIS hâlâ yanıtlıyor; komutun bekliyor.", true); return; }
    $("#mini-input").value = "";
    renderMiniLine("…");
    sendCommand(text);
  });
  startClock();
  bindKeyboard();
}
