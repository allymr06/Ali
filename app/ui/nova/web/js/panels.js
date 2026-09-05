/* ════════════════════════════════════════════════════════════════════
   JARVIS · NOVA — panels
   Snapshot-driven screens: command centre, tasks, memory, automation,
   vision, research, diagnostics and settings. Every figure shown here
   is read from the core; anything unavailable is labelled as such.
   ════════════════════════════════════════════════════════════════════ */
"use strict";

function emptyState(title, text) {
  return `<div class="empty"><span class="empty-glyph"></span><span class="empty-title">${esc(title)}</span>${text ? `<span>${esc(text)}</span>` : ""}</div>`;
}

/* ── snapshot fan-out ─────────────────────────────────────────────── */

function renderSnapshot() {
  const s = State.snapshot;
  if (!s) return;
  renderSystemChips();
  renderGreeting();
  renderHomeSystem();
  renderHomeSession();
  renderHomeActivity();
  renderTasks(s.tasks || []);
  renderTools(s.tools || []);
  renderRiskBars(s.tools || []);
  renderDiagnosticsHead();
  refreshQuickActions();
  Context.render();
  Presence.apply();
}

/* ── command centre ───────────────────────────────────────────────── */

function renderGreeting() {
  const s = State.snapshot;
  $("#home-greeting").textContent = `${greetingForHour(new Date().getHours())}.`;
  if (!s) return;
  const running = (s.tasks || []).filter((t) => ["running", "waiting_for_input", "waiting_for_approval"].includes(String(t.status))).length;
  const parts = [`${s.memory_count ?? 0} anı`, `${s.enabled_tools ?? 0} araç etkin`];
  if (running) parts.push(`${running} görev çalışıyor`);
  $("#home-subline").textContent = State.demo
    ? "Demo modu: çekirdek bağlı değil, veriler örnek."
    : `Sistemler hazır · ${parts.join(" · ")}.`;
}

function renderHomeSystem() {
  const s = State.snapshot;
  const host = $("#home-system");
  if (!s || !host) return;
  const rows = [
    ["Sağlayıcı", `${s.provider} · ${s.model}`, "ok"],
    ["Ses", s.voice_available ? "hazır" : "kapalı", s.voice_available ? "ok" : ""],
    ["Görüş", s.vision_available ? "hazır" : "kapalı", s.vision_available ? "ok" : ""],
    ["Web araştırması", s.research_available ? "hazır" : "kapalı", s.research_available ? "ok" : ""],
    ["Windows", s.windows_available ? "hazır" : "kapalı", s.windows_available ? "ok" : ""],
    ["Olay defteri", `${s.diagnostic_event_count ?? 0} olay · ${s.diagnostic_integrity_valid ? "doğrulandı" : "HATA"}`, s.diagnostic_integrity_valid ? "ok" : "bad"],
  ];
  host.innerHTML = rows.map(([name, note, light]) =>
    `<div class="status-row"><span class="status-light ${light}"></span><span class="status-name">${esc(name)}</span><span class="status-note">${esc(note)}</span></div>`).join("");
}

function renderHomeSession() {
  const host = $("#home-session");
  if (!host) return;
  const active = State.conversations.find((item) => item.active);
  const count = State.messages.length;
  host.innerHTML =
    `<div class="status-row"><span class="status-light ${count ? "ok" : ""}"></span><span class="status-name">${esc(active ? active.title : "Yeni konuşma")}</span><span class="status-note">${count ? `${count} mesaj` : "boş"}</span></div>`;
}

function renderHomeActivity() {
  const host = $("#home-activity");
  if (!host) return;
  const turns = [Activity.current, ...Activity.recent].filter(Boolean).slice(0, 6);
  if (!turns.length) {
    host.innerHTML = '<div class="ctx-empty">Henüz etkinlik yok. İlk komutunla birlikte burada görünmeye başlar.</div>';
    return;
  }
  host.innerHTML = turns.map((turn) => {
    const light = turn.status === "thinking" ? "busy" : turn.status === "completed" ? "ok" : "bad";
    const tools = turn.tools.length ? ` · ${turn.tools.length} araç` : "";
    return `<div class="recent-item"><span class="status-light ${light}"></span><span class="recent-text" title="${esc(turn.goal)}">${esc(turn.goal)}${tools}</span><span class="recent-time">${esc(fmtClock(new Date(turn.startedAt)))}</span></div>`;
  }).join("");
}

function buildQuickActions() {
  const host = $("#quick-actions");
  const actions = [
    ["voice", "Sesli mod", () => toggleVoice(), () => !!State.snapshot?.voice_available],
    ["vision", "Ekranı incele", () => showScreen("vision"), () => !!State.snapshot?.vision_available],
    ["research", "Araştır", () => showScreen("research"), () => !!State.snapshot?.research_available],
    ["plus", "Yeni konuşma", () => newConversation(), () => true],
    ["palette", "Komut paleti", () => Palette.show(), () => true],
  ];
  host.innerHTML = "";
  actions.forEach(([iconName, label, run, enabled]) => {
    const btn = el("button", "quick-action");
    btn.type = "button";
    btn.innerHTML = `${icon(iconName)}<span>${esc(label)}</span>`;
    btn.addEventListener("click", run);
    btn._enabled = enabled;
    host.appendChild(btn);
  });
}

function refreshQuickActions() {
  $$("#quick-actions .quick-action").forEach((btn) => { btn.disabled = !(btn._enabled ? btn._enabled() : true); });
}

/* ── tasks ────────────────────────────────────────────────────────── */

function taskCardHTML(task, { compact = false } = {}) {
  const progress = clamp(Number(task.progress) || 0, 0, 1);
  const status = String(task.status ?? "queued");
  const chip = status === "completed" ? "ok" : ["failed", "cancelled"].includes(status) ? "bad"
    : ["running"].includes(status) ? "accent" : ["paused", "waiting_for_input", "waiting_for_approval"].includes(status) ? "warn" : "";
  const steps = Array.isArray(task.steps) ? task.steps : [];
  const timeline = steps.length
    ? `<div class="timeline">${steps.map((step) => `<div class="tl-node ${esc(step.status)}"><div class="tl-name">${esc(step.name)}</div>${step.error ? `<div class="tl-meta"><span class="bad">${esc(step.error)}</span></div>` : ""}</div>`).join("")}</div>`
    : (compact ? "" : `<div class="ctx-empty">Bu görevin adım planı yok.</div>`);
  return `<div class="panel task-card animated-border ${status === "running" ? "live" : ""}">
    <div class="task-goal">${esc(task.goal)}</div>
    <div class="progress-track"><div class="progress-fill ${status === "completed" ? "ok" : ""}" style="transform: scaleX(${progress})"></div></div>
    <div class="task-meta">
      <span class="chip ${chip}">${esc(tr(status))}</span>
      <span>%${Math.round(progress * 100)}</span>
      ${task.current_step ? `<span>${esc(task.current_step)}</span>` : ""}
      ${task.updated_at ? `<span class="faint">${esc(fmtRelative(task.updated_at))}</span>` : ""}
      ${task.recovery_required ? `<span class="chip warn">kurtarma gerekli</span>` : ""}
    </div>
    ${task.error ? `<div class="task-error">${esc(task.error)}</div>` : ""}
    ${timeline}
  </div>`;
}

function renderTasks(tasks) {
  const host = $("#tasks-list");
  if (!host) return;
  if (!tasks.length) {
    host.innerHTML = emptyState("Tüm sistemler hazır", "Kayıtlı görev yok. Sohbetten çok adımlı bir iş istediğinde adımları burada izlersin.");
    return;
  }
  host.innerHTML = tasks.map((task) => taskCardHTML(task)).join("");
}

/* ── memory ───────────────────────────────────────────────────────── */

const MEMORY_GROUP_ORDER = ["preference", "instruction", "goal", "project", "fact", "context"];

const Memory = {
  list: [],
  query: "",
  _debounce: 0,

  async load() {
    const result = await call("list_memories", 200);
    if (result.ok === false) { toast(result.error || "Hafıza okunamadı.", true); return; }
    this.list = result.memories || [];
    this.render(this.list);
    this.renderPrivacy();
  },

  async search(query) {
    this.query = query;
    const result = await call("search_memories", query, 50);
    if (result.ok === false) { toast(result.error || "Arama başarısız.", true); return; }
    this.render(result.memories || [], { searching: !!query.trim() });
  },

  card(memory) {
    const confidence = clamp(Number(memory.confidence) || 0, 0, 1);
    const fresh = String(memory.freshness);
    return `<div class="panel memory-card" data-id="${esc(memory.memory_id)}">
      <div class="mem-content">${esc(memory.content)}</div>
      <div class="confidence"><div class="progress-track"><div class="progress-fill violet" style="transform: scaleX(${confidence})"></div></div><span>güven ${confidence.toFixed(2)}</span></div>
      <div class="mem-meta">
        <span class="chip ${fresh === "current" ? "ok" : "warn"}">${esc(tr(fresh))}</span>
        <span class="chip">${esc(tr(memory.source))}</span>
        <span class="faint">${esc(fmtRelative(memory.updated_at))}</span>
        <span class="mem-actions">
          <button type="button" class="icon-btn small" data-act="edit" title="Düzenle">${icon("edit")}</button>
          <button type="button" class="icon-btn small" data-act="forget" title="Unut (devre dışı bırak)">${icon("forget")}</button>
          <button type="button" class="icon-btn small" data-act="delete" title="Kalıcı olarak sil">${icon("trash")}</button>
        </span>
      </div>
    </div>`;
  },

  render(memories, { searching = false } = {}) {
    const host = $("#memory-groups");
    $("#memory-count").textContent = searching ? `${memories.length} eşleşme` : `${memories.length} etkin anı`;
    if (!memories.length) {
      host.innerHTML = emptyState(searching ? "Eşleşen anı yok" : "Hafıza henüz boş",
        searching ? "Farklı bir ifade dene." : "Konuştukça önemli olan burada birikir; her anı kaynağı ve tazeliğiyle saklanır.");
      return;
    }
    const groups = new Map();
    for (const memory of memories) {
      const key = String(memory.memory_type || "fact");
      if (!groups.has(key)) groups.set(key, []);
      groups.get(key).push(memory);
    }
    const ordered = [...groups.entries()].sort((a, b) =>
      (MEMORY_GROUP_ORDER.indexOf(a[0]) + 100) % 100 - (MEMORY_GROUP_ORDER.indexOf(b[0]) + 100) % 100);
    host.innerHTML = ordered.map(([type, items]) =>
      `<section class="memory-group"><div class="ctx-title"><span>${esc(tr(type))}</span><span class="count">${items.length}</span></div>
       <div class="memory-grid">${items.map((m) => this.card(m)).join("")}</div></section>`).join("");
    $$(".memory-card", host).forEach((card) => {
      card.querySelectorAll("[data-act]").forEach((btn) =>
        btn.addEventListener("click", () => this.act(btn.dataset.act, card.dataset.id, card)));
    });
  },

  renderPrivacy() {
    const config = State.runtime?.configuration || {};
    const auto = config.memory_auto_capture_enabled;
    $("#memory-privacy-text").innerHTML =
      `Anılar yalnızca bu bilgisayarda saklanır (${esc(State.runtime?.state_directory || "yerel durum dizini")}). ` +
      `Konuşmalardan otomatik anı çıkarımı <b>${auto === true ? "açık" : auto === false ? "kapalı" : "bilinmiyor"}</b>` +
      (config.memory_extraction_model ? ` (${esc(config.memory_extraction_model)})` : "") +
      `. “Unut” bir anıyı devre dışı bırakır; “Sil” kalıcı olarak kaldırır.`;
  },

  async act(action, memoryId, card) {
    const memory = this.list.find((m) => m.memory_id === memoryId) || {};
    if (action === "edit") { this.edit(memory, card); return; }
    if (action === "forget") {
      const ok = await confirmDialog({ title: "Bu anı unutulsun mu?", body: `“${memory.content || ""}” artık yanıtlarda kullanılmayacak; kayıt devre dışı bırakılır, silinmez.`, confirmLabel: "UNUT" });
      if (!ok) return;
      const result = await call("forget_memory", memoryId);
      if (result.ok === false) { toast(result.error || "Anı unutulamadı.", true); return; }
      toast(result.message || "Anı unutuldu.", "ok");
      this.load();
      return;
    }
    if (action === "delete") {
      const ok = await confirmDialog({ title: "Anı kalıcı olarak silinsin mi?", body: `“${memory.content || ""}” diskten silinecek. Bu işlem geri alınamaz.`, confirmLabel: "KALICI OLARAK SİL", danger: true });
      if (!ok) return;
      const result = await call("delete_memory", memoryId, true);
      if (result.ok === false) { toast(result.error || "Anı silinemedi.", true); return; }
      toast(result.message || "Anı silindi.", "ok");
      this.load();
    }
  },

  edit(memory, card) {
    const body = card.querySelector(".mem-content");
    const original = memory.content || body.textContent;
    body.innerHTML = `<textarea class="mem-edit"></textarea><div class="btn-row" style="margin-top:.5rem"><button type="button" class="btn btn-ghost small" data-edit="cancel">VAZGEÇ</button><button type="button" class="btn btn-primary small" data-edit="save">KAYDET</button></div>`;
    const area = body.querySelector("textarea");
    area.value = original;
    area.focus();
    body.querySelector('[data-edit="cancel"]').addEventListener("click", () => { body.textContent = original; });
    body.querySelector('[data-edit="save"]').addEventListener("click", async () => {
      const result = await call("update_memory", memory.memory_id, area.value);
      if (result.ok === false) { toast(result.error || "Anı güncellenemedi.", true); return; }
      toast(result.message || "Anı güncellendi.", "ok");
      this.load();
    });
  },
};

/* ── automation (tools) ───────────────────────────────────────────── */

function renderTools(tools) {
  const host = $("#tools-groups");
  const summary = $("#tools-summary");
  if (!host) return;
  if (!tools.length) {
    summary.innerHTML = "";
    host.innerHTML = emptyState("Araç sözleşmesi yok", "Çekirdek hiçbir araç bildirmedi.");
    return;
  }
  const enabled = tools.filter((t) => t.enabled).length;
  const risky = tools.filter((t) => ["high", "critical"].includes(String(t.risk))).length;
  summary.innerHTML =
    `<span class="chip accent">${tools.length} araç</span><span class="chip ok">${enabled} etkin</span>` +
    `<span class="chip warn">${risky} onay gerektiren</span><span class="chip">${tools.length - enabled} devre dışı</span>`;
  const groups = new Map();
  for (const tool of tools) {
    const key = String(tool.source || "runtime");
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key).push(tool);
  }
  host.innerHTML = [...groups.entries()].sort((a, b) => sourceLabel(a[0]).localeCompare(sourceLabel(b[0]), "tr")).map(([source, items]) =>
    `<section><div class="ctx-title"><span>${esc(sourceLabel(source))}</span><span class="count">${items.length}</span></div>
     <div class="tool-grid">${items.map((t) => {
       const risk = String(t.risk ?? "low");
       const chip = risk === "critical" ? "bad" : risk === "high" ? "warn" : "";
       return `<div class="panel tool-card ${t.enabled ? "" : "off"}">
         <div class="tool-name">${esc(humanizeTool(t.name))}</div>
         <div class="tool-raw">${esc(t.name)}</div>
         <div class="tool-desc">${esc(t.description)}</div>
         <div class="tool-meta"><span class="chip ${chip}">risk · ${esc(tr(risk))}</span><span class="chip ${t.enabled ? "ok" : ""}">${t.enabled ? "etkin" : "devre dışı"}</span></div>
       </div>`; }).join("")}</div></section>`).join("");
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
  panel.innerHTML = '<span class="thinking"><span class="orbit"></span>ekran onayla yakalanıyor ve inceleniyor…</span>';
  $("#vision-submit").disabled = true;
  setBusy(true, "CAPTURING");
  const result = await call("run_vision", purpose);
  if (result.ok === false) renderVisionResult(false, null, result.error || "Görüş başlatılamadı.");
}

function renderVisionResult(ok, text, error) {
  const panel = $("#vision-result");
  panel.hidden = false;
  panel.classList.toggle("err", !ok);
  panel.textContent = ok ? text : (error || "Analiz başarısız.");
  $("#vision-submit").disabled = false;
  if (!ok) Presence.error("görüş başarısız");
  if (State.busy) setBusy(false, READY);
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
  panel.innerHTML = '<span class="thinking"><span class="orbit"></span>kaynaklar taranıyor ve doğrulanıyor…</span>';
  $("#research-submit").disabled = true;
  setBusy(true, "RESEARCHING");
  const result = await call("run_research", query, Number($("#research-sources").value));
  if (result.ok === false) renderResearch(false, null, result.error || "Araştırma başlatılamadı.");
}

function renderResearch(ok, report, error) {
  const panel = $("#research-result");
  panel.hidden = false;
  panel.classList.toggle("err", !ok);
  $("#research-submit").disabled = false;
  if (State.busy) setBusy(false, READY);
  if (!ok) { panel.textContent = error || "Araştırma başarısız."; Presence.error("araştırma başarısız"); return; }
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
    parts.push("<h3>BELİRSİZLİKLER</h3>" + uncertainties.map((u) => `<span class="src">▸ ${esc(u)}</span>`).join(""));
  }
  panel.innerHTML = parts.join("") || esc(JSON.stringify(report, null, 2));
}

/* ── diagnostics ──────────────────────────────────────────────────── */

const MAX_EVENT_ROWS = 300;

function renderDiagnosticsHead() {
  const s = State.snapshot;
  const node = $("#diag-head-note");
  if (!s || !node) return;
  node.innerHTML = `${s.diagnostic_event_count ?? 0} olay · bütünlük ` +
    (s.diagnostic_integrity_valid ? '<span style="color:var(--ok)">doğrulandı</span>' : '<span style="color:var(--bad)">HATALI</span>');
}

const Diagnostics = {
  loading: false,
  levelFilter: "",

  async refresh({ quiet = false } = {}) {
    if (this.loading) return;
    this.loading = true;
    $("#diag-refresh").disabled = true;
    const [status, events] = await Promise.all([call("system_status"), call("diagnostic_events", 120)]);
    this.loading = false;
    $("#diag-refresh").disabled = false;
    if (status.ok === false) { if (!quiet) toast(status.error || "Sistem durumu okunamadı.", true); }
    else { State.lastStatus = status; this.renderStatus(status); }
    if (events.ok !== false) {
      State.diagnosticEvents = (events.events || []).slice(0, MAX_EVENT_ROWS);
      State.requestDurations = State.diagnosticEvents
        .filter((e) => e.name === "request.completed" && Number.isFinite(Number(e.attributes?.elapsed_seconds)))
        .map((e) => Number(e.attributes.elapsed_seconds)).reverse().slice(-40);
      this.renderEvents();
      this.renderSparkline();
    }
  },

  onEvent(payload) {
    State.diagnosticEvents.unshift(payload);
    State.diagnosticEvents.length = Math.min(State.diagnosticEvents.length, MAX_EVENT_ROWS);
    if (payload.name === "request.completed") {
      const seconds = Number(payload.attributes?.elapsed_seconds);
      if (Number.isFinite(seconds)) { State.requestDurations.push(seconds); State.requestDurations = State.requestDurations.slice(-40); }
    }
    if (State.screen === "diagnostics") { this.renderEvents(payload); this.renderSparkline(); }
  },

  metricValue(value, unit, digits = 0) {
    if (value === null || value === undefined || !Number.isFinite(Number(value))) return `<div class="metric-value na">kullanılamıyor</div>`;
    return `<div class="metric-value">${Number(value).toFixed(digits)}${unit ? `<small>${esc(unit)}</small>` : ""}</div>`;
  },

  renderStatus(status) {
    const health = status.health;
    const healthHost = $("#diag-health");
    if (health) {
      const overall = String(health.status);
      const cls = overall === "healthy" ? "ok" : overall === "degraded" ? "warn" : "bad";
      healthHost.innerHTML =
        `<div class="panel-title"><span class="kicker">SAĞLIK DENETİMİ</span><span class="chip ${cls}">${esc(tr(overall))}</span></div>` +
        (health.checks || []).map((check) => {
          const light = check.status === "healthy" ? "ok" : check.status === "degraded" ? "warn" : "bad";
          const details = Object.entries(check.details || {}).map(([k, v]) => `${k}: ${v}`).join(" · ");
          return `<div class="status-row"><span class="status-light ${light}"></span><span class="status-name" title="${esc(check.message)}${details ? " · " + esc(details) : ""}">${esc(check.name)} <span class="faint">${esc(details)}</span></span><span class="status-note">${Number(check.latency_ms).toFixed(1)} ms</span></div>`;
        }).join("");
    } else {
      healthHost.innerHTML = `<div class="panel-title"><span class="kicker">SAĞLIK DENETİMİ</span></div><div class="ctx-empty">${esc(status.health_error || "Sağlık denetimi kullanılamıyor.")}</div>`;
    }

    const timers = status.metrics?.timers || {};
    const counters = status.metrics?.counters || {};
    const duration = timers["core.request.duration"] || null;
    $("#diag-core").innerHTML =
      `<div class="panel-title"><span class="kicker">ÇEKİRDEK VE SAĞLAYICI</span>` +
      (status.provider ? `<span class="chip ${status.provider.circuit === "closed" ? "ok" : "warn"}">devre · ${esc(tr(status.provider.circuit))}</span>` : "") + `</div>` +
      `<div class="diag-metrics">
        <div class="metric">${this.metricValue(counters["core.requests"] ?? 0, "")}<div class="metric-note">tamamlanan istek</div></div>
        <div class="metric">${this.metricValue(duration ? duration.average_seconds : null, "sn", 2)}<div class="metric-note">ortalama süre</div></div>
        <div class="metric">${this.metricValue(duration ? duration.minimum_seconds : null, "sn", 2)}<div class="metric-note">en hızlı</div></div>
        <div class="metric">${this.metricValue(duration ? duration.maximum_seconds : null, "sn", 2)}<div class="metric-note">en yavaş</div></div>
        <div class="metric">${this.metricValue(status.admission?.active, "")}<div class="metric-note">aktif istek</div></div>
        <div class="metric">${this.metricValue(status.admission?.rejected, "")}<div class="metric-note">reddedilen</div></div>
        <div class="metric">${this.metricValue(counters["core.requests.rejected"] ?? 0, "")}<div class="metric-note">kabul dışı</div></div>
        <div class="metric">${this.metricValue(status.event_count, "")}<div class="metric-note">olay · bütünlük ${status.integrity === true ? "✓" : status.integrity === false ? "✗" : "?"}</div></div>
      </div>
      <div class="ctx-title" style="margin-top:.4rem"><span>SON İSTEK SÜRELERİ</span><span class="count" id="diag-spark-note"></span></div>
      <canvas id="diag-spark" class="sparkline"></canvas>`;

    const p = status.process || {};
    $("#diag-process").innerHTML =
      `<div class="panel-title"><span class="kicker">SÜREÇ</span></div>
      <div class="diag-metrics">
        <div class="metric">${this.metricValue(p.cpu_percent, "%", 1)}<div class="metric-note">işlemci (JARVIS)</div></div>
        <div class="metric">${p.memory_bytes ? `<div class="metric-value">${esc(fmtBytes(p.memory_bytes))}</div>` : '<div class="metric-value na">kullanılamıyor</div>'}<div class="metric-note">bellek (çalışma kümesi)</div></div>
        <div class="metric">${this.metricValue(p.threads, "")}<div class="metric-note">iş parçacığı</div></div>
        <div class="metric"><div class="metric-value" style="font-size:var(--text-lg)">${esc(fmtUptime(Number(p.uptime_seconds)))}</div><div class="metric-note">çalışma süresi</div></div>
      </div>
      <div class="settings-note">İlk ölçümde işlemci yüzdesi bilinmez; yenilemede iki örnek arasındaki fark gösterilir.</div>`;

    const r = State.runtime || {};
    const runtimeRows = [
      ["Sürüm", r.version || "bilinmiyor"], ["Python", r.python || "—"], ["WebView2", r.webview2 || "algılanamadı"],
      ["Platform", r.platform || "—"], ["Başlangıç", r.started_at ? fmtTime(r.started_at) : "—"],
      ["Durum dizini", r.state_directory || "—"], ["Konuşma", r.conversation_id ? String(r.conversation_id).slice(0, 8) + "…" : "—"],
    ];
    $("#diag-runtime").innerHTML =
      `<div class="panel-title"><span class="kicker">ÇALIŞMA ZAMANI</span></div>` +
      runtimeRows.map(([name, value]) => `<div class="config-row"><span class="config-name">${esc(name)}</span><span class="config-value selectable">${esc(String(value))}</span></div>`).join("");
    $("#diag-observed").textContent = status.observed_at ? `ölçüm ${fmtTime(status.observed_at)}` : "";
    this.renderSparkline();
  },

  renderSparkline() {
    const canvas = $("#diag-spark");
    if (!canvas) return;
    const values = State.requestDurations;
    const note = $("#diag-spark-note");
    if (note) note.textContent = values.length ? `${values.length} istek` : "henüz istek yok";
    const rect = canvas.getBoundingClientRect();
    if (!rect.width) return;
    const dpr = Math.min(devicePixelRatio || 1, 2);
    canvas.width = rect.width * dpr; canvas.height = rect.height * dpr;
    const ctx = canvas.getContext("2d");
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, rect.width, rect.height);
    if (!values.length) return;
    const max = Math.max(...values, 0.001);
    const gap = 3, w = Math.max(3, (rect.width - gap * values.length) / values.length);
    const accent = getComputedStyle(document.body).getPropertyValue("--accent-rgb").trim() || "142,224,255";
    values.forEach((value, index) => {
      const h = Math.max(2, (value / max) * (rect.height - 4));
      ctx.fillStyle = `rgba(${accent},${0.35 + 0.55 * (index / values.length)})`;
      ctx.fillRect(index * (w + gap), rect.height - h, w, h);
    });
  },

  eventRow(event) {
    const level = String(event.level || "info");
    const attrs = Object.entries(event.attributes || {}).filter(([, v]) => v !== null && v !== "" && typeof v !== "object")
      .map(([k, v]) => `${k}=${typeof v === "number" ? Number(v.toFixed ? v.toFixed(3) : v) : v}`).join(" · ");
    return `<div class="event-item ${esc(level)}"><span class="ev-time">${esc(fmtTime(event.observed_at))}</span>` +
      `<span class="ev-name" title="${esc(event.component)}">${esc(event.name)}</span>` +
      `<span class="ev-msg">${esc(event.message)}${attrs ? `<small>${esc(attrs)}</small>` : ""}</span></div>`;
  },

  renderEvents(fresh) {
    const host = $("#diag-events");
    if (!host) return;
    const filter = this.levelFilter;
    const rows = State.diagnosticEvents.filter((e) => !filter || String(e.level) === filter);
    if (!rows.length) { host.innerHTML = '<div class="ctx-empty">Bu süzgeçle eşleşen olay yok.</div>'; return; }
    host.innerHTML = rows.slice(0, 120).map((e) => this.eventRow(e)).join("");
    if (fresh && (!filter || String(fresh.level) === filter)) host.firstElementChild?.classList.add("new");
    $("#diag-events-count").textContent = `${State.diagnosticEvents.length} olay bellekte`;
  },
};

/* ── settings ─────────────────────────────────────────────────────── */

function renderSettings() {
  const s = State.settings;
  const card = $("#settings-connection");
  card.hidden = !s;
  if (s) {
    $("#settings-model").value = s.model || "";
    $("#settings-key").placeholder = s.credential_configured
      ? "Anahtar kayıtlı — değiştirmek için yaz" : "Gemini API anahtarın";
    $("#settings-key-state").innerHTML = s.credential_configured
      ? '<span class="chip ok">anahtar kayıtlı</span>' : '<span class="chip warn">anahtar yok · deneme modu</span>';
  }
  $("#settings-motion").checked = State.reducedMotion;
  $("#settings-ambient").checked = State.ambient;
  $("#settings-theme").checked = document.body.classList.contains("light");
  renderConfig();
  Files.render();
}

function renderConfig() {
  const config = State.runtime?.configuration || {};
  for (const [group, fields] of Object.entries(SETTING_GROUPS)) {
    const host = $(`#config-${group}`);
    if (!host) continue;
    const rows = fields.filter((field) => field in config).map((field) => {
      const [label, env] = SETTING_LABELS[field] || [field, ""];
      const value = config[field];
      const off = value === false || value === null || value === "";
      const text = value === true ? "açık" : value === false ? "kapalı" : value === null ? "—" : String(value);
      return `<div class="config-row"><span class="config-name">${esc(label)}<span class="config-env">${esc(env)}</span></span><span class="config-value ${off ? "off" : ""}">${esc(text)}</span></div>`;
    });
    host.innerHTML = rows.length ? rows.join("") : '<div class="ctx-empty">Bu bölüm için yapılandırma bildirilmedi.</div>';
  }
}

/* ── file access: the roots the user grants, the snapshots they restore ── */

const Files = {
  async load() {
    const [roots, snapshots] = await Promise.all([call("list_file_roots"), call("list_snapshots", 50)]);
    if (roots.ok !== false) State.fileRoots = { available: !!roots.available, roots: roots.roots || [] };
    if (snapshots.ok !== false) { State.snapshots = snapshots.snapshots || []; State.snapshotUsage = snapshots.usage || null; State.snapshotsAvailable = !!snapshots.available; }
    this.render();
  },

  render() {
    const host = $("#file-roots");
    const roots = State.fileRoots?.roots || [];
    $("#file-roots-count").textContent = State.fileRoots?.available ? `${roots.length} klasör` : "kullanılamıyor";
    $("#file-root-add").disabled = !State.fileRoots?.available;
    if (!State.fileRoots?.available) {
      host.innerHTML = '<div class="ctx-empty">Dosya erişimi bu ortamda kullanılamıyor (Windows entegrasyonları kapalı).</div>';
    } else if (!roots.length) {
      host.innerHTML = '<div class="ctx-empty">Erişim verilen klasör yok. JARVIS dosyalara dokunamaz.</div>';
    } else {
      host.innerHTML = roots.map((root) => `
        <div class="file-root" data-id="${esc(root.root_id)}">
          <div><div class="root-name">${esc(root.name)}</div><div class="root-path">${esc(root.path)} · ${esc(root.root_id)}</div></div>
          <button type="button" class="btn btn-ghost small" data-act="revoke">Kaldır</button>
        </div>`).join("");
      $$("[data-act='revoke']", host).forEach((btn) =>
        btn.addEventListener("click", () => this.revoke(btn.closest(".file-root").dataset.id)));
    }

    const list = $("#snapshot-list");
    const usage = State.snapshotUsage;
    $("#snapshot-usage").textContent = usage ? `${usage.entries} kayıt · ${fmtBytes(usage.bytes)} / ${fmtBytes(usage.max_total_bytes)}` : "";
    const snapshots = State.snapshots || [];
    if (State.snapshotsAvailable === false) {
      list.innerHTML = '<div class="ctx-empty">Anlık görüntü deposu bu ortamda kullanılamıyor.</div>';
      return;
    }
    if (!snapshots.length) {
      list.innerHTML = '<div class="ctx-empty">Henüz anlık görüntü yok. Bir araç bir dosyanın üzerine yazdığında ya da sildiğinde burada belirir.</div>';
      return;
    }
    list.innerHTML = snapshots.map((item) => `
      <div class="snapshot-row" data-id="${esc(item.snapshot_id)}">
        <div>
          <div class="snap-path">${esc(item.path)}</div>
          <div class="snap-meta"><span class="chip ${item.reason === "delete" ? "warn" : ""}">${esc(tr(item.reason))}</span>${esc(item.root_id)} · ${esc(fmtBytes(Number(item.size_bytes)))} · ${esc(fmtRelative(item.created_at))} · ${esc(toolLabel(item.tool_name, true).toLocaleLowerCase("tr"))}</div>
        </div>
        <button type="button" class="btn btn-ghost small" data-act="restore">Geri yükle</button>
      </div>`).join("");
    $$("[data-act='restore']", list).forEach((btn) =>
      btn.addEventListener("click", () => this.restore(btn.closest(".snapshot-row").dataset.id)));
  },

  async add() {
    const picked = await call("pick_file_root");
    if (picked.ok === false) { toast(picked.error || "Klasör seçilemedi.", true); return; }
    if (!picked.path) return;
    const confirmed = await confirmDialog({
      title: "Klasör erişimi verilsin mi?",
      body: `JARVIS yalnız bu klasörün içinde çalışabilecek:\n\n${picked.path}\n\nAraçların yapacağı her değişiklik yine ayrıca onaylanacak; değiştirilen ve silinen dosyalar anlık görüntüden geri alınabilecek.`,
      confirmLabel: "ERİŞİM VER",
    });
    if (!confirmed) return;
    const result = await call("grant_file_root", picked.path, true);
    if (result.ok === false) { toast(result.error || "Klasör erişimi eklenemedi.", true); return; }
    State.fileRoots = { available: true, roots: result.roots || [] };
    this.render();
    toast("Klasör erişimi eklendi.", "ok");
  },

  async revoke(rootId) {
    const root = (State.fileRoots?.roots || []).find((item) => item.root_id === rootId);
    const confirmed = await confirmDialog({
      title: "Klasör erişimi kaldırılsın mı?",
      body: `JARVIS erişimi hemen kaybedecek:\n\n${root ? root.path : rootId}`,
      confirmLabel: "KALDIR",
      danger: true,
    });
    if (!confirmed) return;
    const result = await call("revoke_file_root", rootId);
    if (result.ok === false) { toast(result.error || "Klasör erişimi kaldırılamadı.", true); return; }
    State.fileRoots = { available: true, roots: result.roots || [] };
    this.render();
    toast("Klasör erişimi kaldırıldı.", "ok");
  },

  async restore(snapshotId) {
    const item = (State.snapshots || []).find((entry) => entry.snapshot_id === snapshotId);
    const confirmed = await confirmDialog({
      title: "Dosya geri yüklensin mi?",
      body: `“${item ? item.path : snapshotId}” bu anlık görüntüdeki hâline döndürülecek. Şu anki hâli de saklanır; bu işlem geri alınabilir.`,
      confirmLabel: "GERİ YÜKLE",
    });
    if (!confirmed) return;
    const result = await call("restore_snapshot", snapshotId, true);
    if (result.ok === false) { toast(result.error || "Geri yükleme başarısız.", true); return; }
    toast(result.message || "Dosya geri yüklendi.", "ok");
    this.load();
  },
};

function settingsStatus(text, ok) {
  const node = $("#settings-status");
  node.textContent = text || "";
  node.className = `settings-status ${ok === true ? "ok" : ok === false ? "err" : ""}`;
}

async function saveSettings(event) {
  event.preventDefault();
  if (!bridgeReady()) return;
  settingsStatus("Kaydediliyor…");
  const result = await call("save_settings", "gemini", $("#settings-model").value.trim(), $("#settings-key").value);
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
  const result = await call("test_connection", "gemini", $("#settings-model").value.trim(), $("#settings-key").value);
  settingsStatus(result.message || result.error, !!result.ok);
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
  const result = await call("delete_api_key", true);
  settingsStatus(result.message || result.error, !!result.ok);
  if (result.ok && result.settings) { State.settings = result.settings; renderSettings(); }
}

function renderShortcuts() {
  $("#shortcut-table").innerHTML = SHORTCUTS.map(([keys, label]) =>
    `<kbd>${esc(keys)}</kbd><span>${esc(label)}</span>`).join("");
}

function bindPanels() {
  buildQuickActions();
  $("#vision-form").addEventListener("submit", submitVision);
  $("#research-form").addEventListener("submit", submitResearch);
  $("#settings-form").addEventListener("submit", saveSettings);
  $("#settings-test").addEventListener("click", testConnection);
  $("#settings-delete").addEventListener("click", deleteKey);
  $("#settings-motion").addEventListener("change", (event) => applyMotionPreference(event.target.checked));
  $("#settings-ambient").addEventListener("change", (event) => applyAmbientPreference(event.target.checked));
  $("#settings-theme").addEventListener("change", (event) => {
    const light = document.body.classList.contains("light");
    if (light !== event.target.checked) toggleTheme();
  });
  $$("#settings-nav .tab").forEach((tab) => tab.addEventListener("click", () => {
    $$("#settings-nav .tab").forEach((t) => t.classList.toggle("active", t === tab));
    $(`#settings-${tab.dataset.target}`)?.scrollIntoView({ behavior: State.reducedMotion ? "auto" : "smooth", block: "start" });
  }));
  $("#memory-search").addEventListener("input", (event) => {
    clearTimeout(Memory._debounce);
    Memory._debounce = setTimeout(() => Memory.search(event.target.value), 220);
  });
  $("#memory-refresh").innerHTML = icon("refresh");
  $("#memory-refresh").addEventListener("click", () => { $("#memory-search").value = ""; Memory.load(); });
  $("#diag-refresh").innerHTML = `${icon("refresh")}<span>Yenile</span>`;
  $("#diag-refresh").addEventListener("click", () => Diagnostics.refresh());
  $("#diag-level").addEventListener("change", (event) => { Diagnostics.levelFilter = event.target.value; Diagnostics.renderEvents(); });
  $("#file-root-add").addEventListener("click", () => Files.add());
  $("#snapshot-refresh").innerHTML = icon("refresh");
  $("#snapshot-refresh").addEventListener("click", () => Files.load());
  $("#trust-refresh").innerHTML = `${icon("refresh")}<span>Yenile</span>`;
  $("#trust-refresh").addEventListener("click", () => Trust.refresh());
}
