/* ════════════════════════════════════════════════════════════════════
   JARVIS · NOVA — Tıp Akademisi
   The study workspace: dashboard and session, curriculum, library and
   page reader, notes, exams, question bank, professor style, progress
   and the Anatomy Lab.

   Every figure here comes from the Python academy through
   Bridge.medical_call(action, params). Nothing is simulated: when a
   licensed 3D asset is missing the lab says so and shows the schematic
   relationship map instead of inventing a bone.
   ════════════════════════════════════════════════════════════════════ */
"use strict";

const MED_TABS = [
  ["dashboard", "Panel", "home"],
  ["subjects", "Konular", "memory"],
  ["library", "Kütüphane", "research"],
  ["notes", "Notlar", "chat"],
  ["exam", "Sınav", "tasks"],
  ["bank", "Soru bankası", "tools"],
  ["professor", "Hoca tarzı", "integrations"],
  ["progress", "İlerleme", "diagnostics"],
  ["anatomy", "Anatomi Lab", "vision"],
];

const MED_ORIGIN_TR = {
  generated: "Üretilmiş",
  imported_exam: "Hocadan alınmış",
  manual: "Elle girilmiş",
  lecture_derived: "Ders notundan",
};
const MED_STATUS_TONE = { ready: "ok", failed: "bad", pending: "", reading: "accent", extracting: "accent", analyzing_visuals: "accent", indexing: "accent" };
const MED_LEVEL_TONE = { weak: "bad", moderate: "warn", strong: "ok", unknown: "" };
const MED_DIFFICULTY = [
  [1, "1 · Kolay"], [2, "2 · Kolay-orta"], [3, "3 · Orta"], [4, "4 · Orta-zor"], [5, "5 · Zor"],
];

function medPercent(value) {
  return Number.isFinite(Number(value)) ? `%${Math.round(Number(value) * 100)}` : "—";
}

function medBar(ratio, { label = "", value = "" } = {}) {
  const width = clamp(Number(ratio) || 0, 0, 1);
  const tone = width < 0.5 ? "low" : width < 0.8 ? "mid" : "";
  return `<div class="med-bar-row"><span class="mb-label">${esc(label)}</span><span class="mb-value">${esc(value)}</span>
    <span class="med-bar"><i class="${tone}" style="transform: scaleX(${width.toFixed(3)})"></i></span></div>`;
}

function medEmpty(title, text) {
  return `<div class="ctx-empty">${esc(title)}${text ? ` — ${esc(text)}` : ""}</div>`;
}

/* ════════════════════════════════════════════════════════════════════
   Medical: the workspace controller
   ════════════════════════════════════════════════════════════════════ */

const Medical = {
  view: "dashboard",
  state: null,          // dashboard payload from the core
  subjects: [],
  topic: null,
  documents: [],
  document: null,       // open document detail
  page: null,           // open page of that document
  notes: [],
  exams: [],
  exam: null,           // open exam (with questions)
  runner: null,         // {index, revealed, deadline}
  bank: null,
  professors: [],
  professor: null,
  importReport: null,   // the last question import's own account of itself
  progressData: null,
  loaded: {},           // which views have been fetched at least once
  expanded: {},         // curriculum tree open state
  timer: 0,

  get available() { return !!(State.medical && State.medical.available); },

  /* ── plumbing ──────────────────────────────────────────────────── */

  async request(action, params) {
    if (!bridgeReady()) return { ok: false, error: "Çekirdek köprüsü hazır değil." };
    const result = await call("medical_call", action, params || {});
    if (result && result.available === false) {
      State.medical = { available: false, reason: result.error };
      this.renderAvailability();
    }
    return result;
  },

  apply(payload) {
    State.medical = payload && typeof payload === "object" ? payload : { available: false };
    this.state = this.available ? State.medical : null;
    this.renderAvailability();
    if (this.available) { this.renderChips(); this.renderDashboard(); }
  },

  renderAvailability() {
    const notice = $("#med-unavailable");
    const body = $("#med-body");
    const tabs = $("#med-tabs");
    if (!notice || !body) return;
    if (this.available) {
      notice.hidden = true;
      body.hidden = false;
      if (tabs) tabs.hidden = false;
      return;
    }
    notice.hidden = false;
    body.hidden = true;
    if (tabs) tabs.hidden = true;
    const reason = (State.medical && State.medical.reason) || "Tıp Akademisi bu ortamda kullanılamıyor.";
    notice.innerHTML = `<div class="panel-title"><span class="kicker">Tıp Akademisi</span></div>
      <p>${esc(reason)}</p>
      <p class="settings-note">JARVIS_MEDICAL_ENABLED değişkenini açıp yeniden başlatabilirsin.</p>`;
  },

  /* ── tabs ──────────────────────────────────────────────────────── */

  buildTabs() {
    const host = $("#med-tabs");
    if (!host) return;
    host.innerHTML = "";
    MED_TABS.forEach(([id, label, iconName]) => {
      const btn = el("button", "med-tab");
      btn.type = "button";
      btn.dataset.view = id;
      btn.innerHTML = `${icon(iconName)}<span>${esc(label)}</span><span class="med-tab-count" data-count="${id}"></span>`;
      btn.addEventListener("click", () => this.show(id));
      host.appendChild(btn);
    });
    this.markTabs();
  },

  markTabs() {
    $$("#med-tabs .med-tab").forEach((tab) => tab.classList.toggle("active", tab.dataset.view === this.view));
    const counts = this.state && this.state.counts ? this.state.counts : {};
    const map = { library: counts.documents, notes: counts.notes, exam: counts.exams, bank: counts.questions };
    $$("#med-tabs .med-tab-count").forEach((node) => {
      const value = map[node.dataset.count];
      node.textContent = Number.isFinite(Number(value)) && Number(value) > 0 ? String(value) : "";
    });
  },

  show(view) {
    if (!MED_TABS.some(([id]) => id === view)) return;
    this.view = view;
    $$(".med-view").forEach((node) => { node.hidden = node.dataset.view !== view; });
    this.markTabs();
    const active = $(`.med-view[data-view="${view}"]`);
    if (active && Motion.allowed()) Motion.rise(active, { y: 8, duration: Motion.panel });
    this.loadView(view);
    requestAnimationFrame(() => Engine.resize());
  },

  async open() {
    if (!State.medical) await this.refresh();
    this.renderAvailability();
    if (!this.available) return;
    // The dashboard's topic picker needs the curriculum, so it is
    // fetched once when the workspace opens rather than only when the
    // Konular tab is first visited.
    if (!this.subjects.length) await this.loadSubjects();
    this.renderChips();
    this.renderDashboard();
    this.show(this.view);
  },

  async refresh() {
    const result = await this.request("state");
    if (result.ok === false && result.available === false) { this.apply({ available: false, reason: result.error }); return; }
    if (result.ok === false) { toast(result.error || "Tıp Akademisi okunamadı.", true); return; }
    this.apply(result);
  },

  async loadView(view) {
    if (!this.available) return;
    const first = !this.loaded[view];
    this.loaded[view] = true;
    if (view === "dashboard") { if (!first) await this.refresh(); this.renderDashboard(); return; }
    if (view === "subjects") { if (first) await this.loadSubjects(); else this.renderTree(); return; }
    if (view === "library") { await this.loadDocuments(); return; }
    if (view === "notes") { await this.loadNotes(); return; }
    if (view === "exam") { await this.loadExams(); return; }
    if (view === "bank") { await this.loadBank(); return; }
    if (view === "professor") { await this.loadProfessors(); return; }
    if (view === "progress") { await this.loadProgress(); return; }
    if (view === "anatomy") { await Lab.open(); return; }
  },

  /* ── dashboard ─────────────────────────────────────────────────── */

  renderChips() {
    const host = $("#med-session-chips");
    if (!host || !this.state) return;
    const labels = (this.state.session && this.state.session.labels) || {};
    const available = this.state.available || {};
    const chips = [
      ["accent", labels.subject],
      ["", labels.topic],
      ["", labels.mode],
      ["violet", labels.depth],
      ["", labels.knowledge_source],
    ].filter(([, text]) => text && text !== "Ders seçilmedi" && text !== "Konu seçilmedi");
    if (!available.model) chips.push(["warn", "Model bağlı değil"]);
    if (available.persistent === false) chips.push(["warn", "Geçici depo"]);
    host.innerHTML = chips.map(([tone, text]) => `<span class="chip ${tone}">${esc(text)}</span>`).join("");
  },

  renderDashboard() {
    if (!this.state) return;
    const session = this.state.session || {};
    const options = session.options || {};
    const note = $("#med-session-note");
    if (note) note.textContent = this.state.available && this.state.available.model ? "" : "Model bağlı değil: üretim gerektiren işlemler beklemede.";

    const select = (id, list, value, label) =>
      `<label><span>${esc(label)}</span><select data-session="${id}">${(list || [])
        .map((item) => `<option value="${esc(item.value)}" ${String(item.value) === String(value) ? "selected" : ""}>${esc(item.label)}</option>`)
        .join("")}</select></label>`;

    const subjectOptions = [{ value: "", label: "— ders seçilmedi —" }].concat(options.subjects || []);
    const topicOptions = [{ value: "", label: "— konu seçilmedi —" }].concat(this.topicOptions(session.subject));
    const host = $("#med-session-form");
    if (host) {
      host.innerHTML =
        select("subject", subjectOptions, session.subject || "", "Ders") +
        select("topic_id", topicOptions, session.topic_id || "", "Konu") +
        select("mode", options.modes, session.mode, "Çalışma modu") +
        select("depth", options.depths, session.depth, "Derinlik") +
        select("knowledge_source", options.knowledge_sources, session.knowledge_source, "Bilgi kaynağı") +
        select("knowledge_priority", options.knowledge_priorities, session.knowledge_priority, "Sınav önceliği") +
        `<label><span>Zorluk</span><select data-session="difficulty">${MED_DIFFICULTY.map(([value, label]) =>
          `<option value="${value}" ${Number(session.difficulty) === value ? "selected" : ""}>${esc(label)}</option>`).join("")}</select></label>` +
        `<label><span>Soru sayısı</span><input data-session="question_count" type="number" min="1" max="60" value="${Number(session.question_count) || 10}"></label>` +
        `<label><span>Şık sayısı</span><input data-session="option_count" type="number" min="2" max="6" value="${Number(session.option_count) || 5}"></label>`;
      $$("[data-session]", host).forEach((node) => {
        node.addEventListener("change", () => this.updateSession(node.dataset.session, node.value));
      });
    }

    const queue = this.state.review_queue || [];
    const count = $("#med-review-count");
    if (count) count.textContent = queue.length ? `${queue.length} kavram` : "";
    const queueHost = $("#med-review-queue");
    if (queueHost) {
      queueHost.innerHTML = queue.length
        ? queue.map((item) => `<div class="med-row"><span class="med-row-title">${esc(item.name)}</span>
            <span class="med-row-side">${esc(item.subject_label || "")}</span>
            <span class="med-row-meta"><span class="chip ${MED_LEVEL_TONE[item.level] || ""}">${esc(item.level_label)}</span>${esc(item.reason)}</span></div>`).join("")
        : medEmpty("Bugün tekrar bekleyen kavram yok", "Quiz çözdükçe zayıf kavramlar burada birikir.");
    }

    const weakHost = $("#med-weak");
    if (weakHost) {
      const weak = this.state.weak_concepts || [];
      weakHost.innerHTML = weak.length
        ? weak.map((item) => `<div class="med-row"><span class="med-row-title">${esc(item.name)}</span>
            <span class="med-row-side">${item.correct}/${item.attempts}</span>
            <span class="med-row-meta">${esc(item.reason)}</span></div>`).join("")
        : medEmpty("Zayıf kavram işaretlenmedi");
    }

    const insightHost = $("#med-insights");
    if (insightHost) {
      const insights = this.state.insights || [];
      insightHost.innerHTML = insights.length
        ? insights.map((line) => `<div class="med-row"><span class="med-row-sub">${esc(line)}</span></div>`).join("")
        : medEmpty("Henüz çıkarılacak bir örüntü yok", "Birkaç sınav sonrası burada gerçek gözlemler görünür.");
    }

    const docHost = $("#med-recent-documents");
    if (docHost) {
      const documents = this.state.recent_documents || [];
      docHost.innerHTML = documents.length
        ? documents.map((item) => `<button type="button" class="med-row" data-document="${esc(item.document_id)}">
            <span class="med-row-title">${esc(item.title)}</span><span class="med-row-side">${item.page_count || 0} s.</span>
            <span class="med-row-meta"><span class="chip ${MED_STATUS_TONE[item.status] || ""}">${esc(item.status_label)}</span>${esc(item.status_detail || "")}</span></button>`).join("")
        : medEmpty("Kütüphane boş", "Ders PDF'lerini Kütüphane sekmesinden ekle.");
      $$("[data-document]", docHost).forEach((node) =>
        node.addEventListener("click", () => { this.show("library"); this.openDocument(node.dataset.document); }));
    }

    const examHost = $("#med-recent-exams");
    if (examHost) {
      const exams = this.state.recent_exams || [];
      examHost.innerHTML = exams.length
        ? exams.map((item) => `<button type="button" class="med-row" data-exam="${esc(item.exam_id)}">
            <span class="med-row-title">${esc(item.title)}</span><span class="med-row-side">${item.percent === null || item.percent === undefined ? "—" : "%" + item.percent}</span>
            <span class="med-row-meta">${item.question_count} soru · ${esc(fmtRelative(item.created_at))}</span></button>`).join("")
        : medEmpty("Kayıtlı sınav yok");
      $$("[data-exam]", examHost).forEach((node) =>
        node.addEventListener("click", () => { this.show("exam"); this.openExam(node.dataset.exam); }));
    }

    const quick = $("#med-quick");
    if (quick) {
      const actions = [
        ["Bu konudan sına", () => this.quickAsk("bu konudan beni sına")],
        ["Kısa not çıkar", () => this.quickAsk("bu konudan kısa sınav notu çıkar")],
        ["Yüksek verimli noktalar", () => this.quickAsk("bu konunun yüksek verimli noktalarını ver")],
        ["Zayıf alanlarımı tekrar et", () => this.quickAsk("zayıf olduğum konuları tekrar et")],
        ["Belge ekle", () => { this.show("library"); this.importDocument(); }],
        ["Anatomi Lab", () => this.show("anatomy")],
      ];
      quick.innerHTML = "";
      actions.forEach(([label, run]) => {
        const btn = el("button", "quick-action");
        btn.type = "button";
        btn.innerHTML = `${icon("spark")}<span>${esc(label)}</span>`;
        btn.addEventListener("click", run);
        quick.appendChild(btn);
      });
    }
    this.markTabs();
  },

  topicOptions(subject) {
    const options = [];
    const walk = (node, depth) => {
      options.push({ value: node.topic_id, label: `${"— ".repeat(depth)}${node.title}` });
      (node.children || []).forEach((child) => walk(child, depth + 1));
    };
    (this.subjects || []).filter((node) => !subject || node.subject === subject).forEach((node) => walk(node, 0));
    return options;
  },

  quickAsk(text) {
    showScreen("chat");
    sendCommand(text);
  },

  async updateSession(field, value) {
    const fields = {};
    fields[field] = value === "" ? null : value;
    const result = await this.request("session", { fields });
    if (result.ok === false) { toast(result.error || "Oturum güncellenemedi.", true); return; }
    (result.problems || []).forEach((problem) => toast(problem, true));
    if (this.state) this.state.session = result.session;
    this.renderChips();
    if (field === "subject") this.renderDashboard();
  },

  /* ── subjects ──────────────────────────────────────────────────── */

  async loadSubjects() {
    const result = await this.request("subjects");
    if (result.ok === false) { toast(result.error || "Müfredat okunamadı.", true); return; }
    this.subjects = result.subjects || [];
    this.renderTree();
    const session = this.state && this.state.session;
    if (session && session.topic_id && this.view === "subjects" && !this.topic) this.openTopic(session.topic_id);
  },

  renderTree(filter) {
    const host = $("#med-tree");
    if (!host) return;
    const query = lower(filter || "");
    const matches = (node) => {
      if (!query) return true;
      if (lower(node.title).includes(query) || lower(node.title_en || "").includes(query)) return true;
      if ((node.keywords || []).some((word) => lower(word).includes(query))) return true;
      return (node.children || []).some(matches);
    };
    const render = (node, depth) => {
      const children = (node.children || []).filter(matches);
      const open = query ? true : this.expanded[node.topic_id] || depth === 0;
      const mastery = node.mastery || {};
      const dot = mastery.weak ? "weak" : mastery.moderate ? "moderate" : mastery.strong ? "strong" : "";
      const badge = node.documents ? `<span class="med-row-side">${node.documents}</span>` : "";
      return `<div class="med-node-wrap">
        <div class="med-node">
          <button type="button" class="med-node-toggle ${children.length ? (open ? "open" : "") : "leaf"}" data-toggle="${esc(node.topic_id)}">${icon("chevron")}</button>
          <button type="button" class="med-node-label ${this.topic && this.topic.topic_id === node.topic_id ? "active" : ""}" data-topic="${esc(node.topic_id)}" title="${esc(node.title)}">
            ${dot ? `<span class="med-node-dot ${dot}"></span>` : ""}${esc(node.title)}</button>${badge}
        </div>
        ${children.length ? `<div class="med-children" data-children="${esc(node.topic_id)}" ${open ? "" : "hidden"}>${children.map((child) => render(child, depth + 1)).join("")}</div>` : ""}
      </div>`;
    };
    const roots = (this.subjects || []).filter(matches);
    host.innerHTML = roots.length ? roots.map((node) => render(node, 0)).join("") : medEmpty("Eşleşen konu yok");
    $$("[data-toggle]", host).forEach((node) => node.addEventListener("click", () => {
      const id = node.dataset.toggle;
      this.expanded[id] = !(this.expanded[id] === undefined ? node.classList.contains("open") : this.expanded[id]);
      const children = host.querySelector(`[data-children="${CSS.escape(id)}"]`);
      if (children) children.hidden = !this.expanded[id];
      node.classList.toggle("open", !!this.expanded[id]);
    }));
    $$("[data-topic]", host).forEach((node) => node.addEventListener("click", () => this.openTopic(node.dataset.topic)));
  },

  async openTopic(topicId) {
    const result = await this.request("topic", { topic_id: topicId });
    if (result.ok === false) { toast(result.error || "Konu açılamadı.", true); return; }
    this.topic = result.topic;
    this.renderTopic();
    this.renderTree($("#med-topic-search") ? $("#med-topic-search").value : "");
  },

  renderTopic() {
    const host = $("#med-topic");
    if (!host) return;
    const topic = this.topic;
    if (!topic) {
      host.innerHTML = `<div class="panel med-card">${medEmpty("Konu seç", "Soldaki müfredattan bir konu seçince kavramları, yapıları ve materyalleri burada görürsün.")}</div>`;
      return;
    }
    const crumbs = (topic.path || []).map((item) => item.title).join(" › ");
    const structures = topic.structures || [];
    const concepts = topic.concepts || [];
    const mastery = topic.mastery || [];
    host.innerHTML = `
      <div class="panel med-card">
        <div class="med-crumb">${esc(crumbs)}</div>
        <h2>${esc(topic.title)}</h2>
        <div class="med-chips">
          <span class="chip accent">${esc(topic.subject_label)}</span>
          ${topic.question_count ? `<span class="chip">${topic.question_count} soru</span>` : ""}
          ${(topic.documents || []).length ? `<span class="chip">${topic.documents.length} belge</span>` : ""}
          ${concepts.length ? `<span class="chip">${concepts.length} kavram</span>` : ""}
        </div>
        <div class="btn-row" style="justify-content:flex-start">
          <button type="button" class="btn btn-primary small" data-act="study">JARVIS anlatsın</button>
          <button type="button" class="btn btn-ghost small" data-act="notes">Kısa not</button>
          <button type="button" class="btn btn-ghost small" data-act="quiz">Beni sına</button>
          <button type="button" class="btn btn-ghost small" data-act="exam">Sınav kur</button>
        </div>
      </div>
      ${(topic.children || []).length ? `<div class="panel med-card"><div class="panel-title"><span class="kicker">Alt konular</span></div>
        <div class="med-chips">${topic.children.map((child) => `<button type="button" class="chip" data-child="${esc(child.topic_id)}">${esc(child.title)}</button>`).join("")}</div></div>` : ""}
      ${structures.length ? `<div class="panel med-card"><div class="panel-title"><span class="kicker">Anatomik yapılar</span></div>
        <div class="med-structures">${structures.map((item) => `<button type="button" class="med-structure" data-structure="${esc(item.structure_id)}">
          <span class="ms-latin">${esc(item.canonical)}</span><span class="ms-tr">${esc(item.turkish)} · ${esc(item.kind_label)}</span></button>`).join("")}</div></div>` : ""}
      ${concepts.length ? `<div class="panel med-card"><div class="panel-title"><span class="kicker">Kavramlar</span></div>
        <div class="med-chips">${concepts.slice(0, 40).map((concept) => `<span class="chip" title="${esc((concept.relations || []).map((r) => r.label + ": " + r.name).join(" · "))}">${esc(concept.name)}</span>`).join("")}</div></div>` : ""}
      ${mastery.length ? `<div class="panel med-card"><div class="panel-title"><span class="kicker">Bu konudaki durumun</span></div>
        <div class="med-mastery">${mastery.map((item) => this.masteryCard(item)).join("")}</div></div>` : ""}
      ${(topic.documents || []).length ? `<div class="panel med-card"><div class="panel-title"><span class="kicker">İlgili materyal</span></div>
        <div class="med-list">${topic.documents.map((item) => `<button type="button" class="med-row" data-document="${esc(item.document_id)}">
          <span class="med-row-title">${esc(item.title)}</span><span class="med-row-side">${item.page_count} s.</span></button>`).join("")}</div></div>` : ""}
      ${(topic.notes || []).length ? `<div class="panel med-card"><div class="panel-title"><span class="kicker">Notların</span></div>
        <div class="med-list">${topic.notes.map((item) => `<div class="med-row"><span class="med-row-title">${esc(item.title)}</span>
          <span class="med-row-side">${esc(fmtRelative(item.created_at))}</span></div>`).join("")}</div></div>` : ""}`;

    const ask = (text) => { this.setTopicThen(topic.topic_id, () => this.quickAsk(text)); };
    const actions = {
      study: () => ask(`${topic.title} konusunu anlat`),
      notes: () => ask(`${topic.title} konusundan kısa sınav notu çıkar`),
      quiz: () => ask(`${topic.title} konusundan beni sına`),
      exam: () => { this.setTopicThen(topic.topic_id, () => this.show("exam")); },
    };
    $$("[data-act]", host).forEach((node) => node.addEventListener("click", () => (actions[node.dataset.act] || (() => {}))()));
    $$("[data-child]", host).forEach((node) => node.addEventListener("click", () => this.openTopic(node.dataset.child)));
    $$("[data-structure]", host).forEach((node) => node.addEventListener("click", () => { this.show("anatomy"); Lab.select(node.dataset.structure); }));
    $$("[data-document]", host).forEach((node) => node.addEventListener("click", () => { this.show("library"); this.openDocument(node.dataset.document); }));
  },

  async setTopicThen(topicId, run) {
    const result = await this.request("session", { fields: { topic_id: topicId } });
    if (result.ok !== false && this.state) { this.state.session = result.session; this.renderChips(); }
    run();
  },

  masteryCard(item) {
    const recent = (item.recent || []).map((ok) => `<i class="${ok ? "ok" : "bad"}"></i>`).join("");
    return `<div class="med-mastery-card">
      <span class="mm-name">${esc(item.name)}</span>
      <div class="med-chips"><span class="chip ${MED_LEVEL_TONE[item.level] || ""}">${esc(item.level_label)}</span><span class="chip">${item.correct}/${item.attempts}</span></div>
      <span class="med-recent">${recent}</span>
      <span class="mm-reason">${esc(item.reason)}</span>
    </div>`;
  },

  /* ── library ───────────────────────────────────────────────────── */

  async loadDocuments() {
    const result = await this.request("documents");
    if (result.ok === false) { toast(result.error || "Belgeler okunamadı.", true); return; }
    this.documents = result.documents || [];
    this.renderDocuments();
    if (this.document) {
      const still = this.documents.find((item) => item.document_id === this.document.document_id);
      if (still) this.openDocument(this.document.document_id, { quiet: true });
      else { this.document = null; this.renderDocument(); }
    } else this.renderDocument();
  },

  renderDocuments() {
    const host = $("#med-doc-list");
    const count = $("#med-doc-count");
    if (count) count.textContent = this.documents.length ? `${this.documents.length} belge` : "";
    if (!host) return;
    if (!this.documents.length) {
      host.innerHTML = medEmpty("Belge yok", "Ders PDF'lerini ya da not dosyalarını ekle: sayfa numaralarıyla birlikte dizinlenir.");
      return;
    }
    host.innerHTML = this.documents.map((item) => `<button type="button" class="med-row ${this.document && this.document.document_id === item.document_id ? "active" : ""}" data-doc="${esc(item.document_id)}">
      <span class="med-row-title">${esc(item.title)}</span><span class="med-row-side">${item.page_count || 0} s.</span>
      <span class="med-row-meta"><span class="chip ${MED_STATUS_TONE[item.status] || ""}">${esc(item.status_label)}</span>${item.subject ? esc(item.subject) : ""}</span></button>`).join("");
    $$("[data-doc]", host).forEach((node) => node.addEventListener("click", () => this.openDocument(node.dataset.doc)));
  },

  async importDocument() {
    const picked = await call("medical_pick_file", "document");
    if (picked.ok === false) { toast(picked.error || "Dosya seçilemedi.", true); return; }
    if (!picked.path) return;
    const result = await this.request("import_document", { path: picked.path });
    if (result.ok === false) { toast(result.error || "Belge eklenemedi.", true); return; }
    this.documents = result.documents || this.documents;
    this.renderDocuments();
    // A document filed but not processed (the core is paused) is not a
    // success toast: the message says what did not happen, so it must not
    // look like everything went through.
    toast(result.message || "Belge eklendi.", result.created && !result.started ? true : "ok");
    if (result.document) this.openDocument(result.document.document_id);
  },

  async openDocument(documentId, { quiet = false } = {}) {
    const result = await this.request("document", { document_id: documentId });
    if (result.ok === false) { if (!quiet) toast(result.error || "Belge açılamadı.", true); return; }
    this.document = result.document;
    this.page = null;
    this.renderDocuments();
    this.renderDocument();
  },

  renderDocument() {
    const host = $("#med-doc-detail");
    if (!host) return;
    const doc = this.document;
    if (!doc) {
      host.innerHTML = `<div class="panel med-card">${medEmpty("Belge seç", "Soldan bir belge seçince sayfaları, çıkarılan konuları ve karşılaştırma bulgularını görürsün.")}</div>`;
      return;
    }
    const job = doc.job;
    const comparison = doc.comparison;
    const pages = doc.pages || [];
    host.innerHTML = `
      <div class="panel med-card">
        <div class="panel-title"><span class="kicker">${esc(doc.file_name)}</span><span class="chip ${MED_STATUS_TONE[doc.status] || ""}">${esc(doc.status_label)}</span></div>
        <h2>${esc(doc.title)}</h2>
        <div class="med-doc-status">
          <span>${esc(job ? job.detail : doc.status_detail || "")}</span>
          ${job ? '<span class="med-progress-track"><i></i></span>' : ""}
        </div>
        ${doc.error ? `<div class="med-explain" style="border-color: rgba(var(--bad-rgb),.5)">${esc(doc.error)}</div>` : ""}
        <div class="med-chips">
          ${doc.subject ? `<span class="chip accent">${esc(doc.subject)}</span>` : ""}
          <span class="chip">${doc.page_count} sayfa</span>
          <span class="chip">${doc.chunk_count} parça</span>
          ${doc.visual_pages_analyzed ? `<span class="chip violet">${doc.visual_pages_analyzed} şekil incelendi</span>` : ""}
          ${doc.visual_pages_pending ? `<span class="chip warn">${doc.visual_pages_pending} şekil bekliyor</span>` : ""}
          ${doc.questions ? `<span class="chip">${doc.questions} soru</span>` : ""}
        </div>
        ${doc.summary ? `<p class="med-row-sub">${esc(doc.summary)}</p>` : ""}
        ${(doc.topics || []).length ? `<div class="med-chips">${doc.topics.map((topic) => `<button type="button" class="chip" data-topic="${esc(topic.topic_id)}">${esc(topic.label)}</button>`).join("")}</div>` : ""}
        ${(doc.key_terms || []).length ? `<div class="med-chips">${doc.key_terms.slice(0, 24).map((term) => `<span class="chip">${esc(term)}</span>`).join("")}</div>` : ""}
        <div class="btn-row" style="justify-content:flex-start">
          <button type="button" class="btn btn-ghost small" data-act="analyze">Analiz et</button>
          <button type="button" class="btn btn-ghost small" data-act="compare">Bilgiyle karşılaştır</button>
          <button type="button" class="btn btn-ghost small" data-act="notes">Not çıkar</button>
          <button type="button" class="btn btn-ghost small" data-act="exam">Soru üret</button>
          <button type="button" class="btn btn-ghost small" data-act="process">Yeniden işle</button>
          <button type="button" class="btn btn-ghost small" data-act="delete">Sil</button>
        </div>
      </div>
      ${pages.length ? `<div class="panel med-card"><div class="panel-title"><span class="kicker">Sayfalar</span><span class="faint">şekil içeren sayfalar mor</span></div>
        <div class="med-pages">${pages.map((page) => `<button type="button" class="med-page-chip ${page.has_visual_summary ? "figure" : ""} ${this.page && this.page.page_number === page.page_number ? "active" : ""}" data-page="${page.page_number}" title="${esc((page.headings || [])[0] || "")}">${page.page_number}</button>`).join("")}</div>
        <div id="med-page-view" class="med-page-view"></div></div>` : ""}
      ${comparison ? this.comparisonHTML(comparison) : ""}`;

    $$("[data-act]", host).forEach((node) => node.addEventListener("click", () => this.documentAction(node.dataset.act)));
    $$("[data-page]", host).forEach((node) => node.addEventListener("click", () => this.openPage(Number(node.dataset.page))));
    $$("[data-topic]", host).forEach((node) => node.addEventListener("click", () => { this.show("subjects"); this.openTopic(node.dataset.topic); }));
    if (this.page) this.renderPage();
  },

  comparisonHTML(comparison) {
    const findings = comparison.findings || [];
    const counts = comparison.counts || {};
    return `<div class="panel med-card">
      <div class="panel-title"><span class="kicker">Ders notu ↔ standart bilgi</span><span class="faint">${esc(fmtRelative(comparison.compared_at))}</span></div>
      <div class="med-chips">${Object.entries(counts).map(([key, value]) => `<span class="chip">${esc(MED_COMPARISON_TR[key] || key)} · ${value}</span>`).join("")}</div>
      ${comparison.overall ? `<p class="med-row-sub">${esc(comparison.overall)}</p>` : ""}
      <div class="med-findings">${findings.map((item) => `<div class="med-finding ${esc(item.category)}">
        <div class="mf-statement">“${esc(item.statement)}”</div>
        <div class="mf-body">${esc(item.explanation)}${item.standard_view ? `<br><b>Standart kaynaklar:</b> ${esc(item.standard_view)}` : ""}</div>
        <div class="mf-meta"><span class="chip">${esc(item.category_label)}</span>
          ${item.page ? `<button type="button" class="chip" data-jump="${item.page}">s. ${item.page}</button>` : '<span class="chip warn">sayfa doğrulanamadı</span>'}
          <span class="chip">${esc(item.support_label || "")}</span></div></div>`).join("")}</div>
      <p class="settings-note">${esc(comparison.note || "")}</p>
    </div>`;
  },

  async documentAction(action) {
    const doc = this.document;
    if (!doc) return;
    if (action === "delete") {
      const ok = await confirmDialog({
        title: "Belge silinsin mi?",
        body: `“${doc.title}” ve ondan çıkarılan sayfalar, parçalar ve şekil özetleri kaldırılacak. Bu belgeden üretilmiş sorular kalır.`,
        confirmLabel: "SİL", danger: true,
      });
      if (!ok) return;
      const result = await this.request("delete_document", { document_id: doc.document_id, confirmed: true });
      if (result.ok === false) { toast(result.error || "Belge silinemedi.", true); return; }
      this.document = null;
      this.documents = result.documents || [];
      this.renderDocuments();
      this.renderDocument();
      toast("Belge silindi.", "ok");
      return;
    }
    if (action === "notes") { this.show("notes"); const select = $("#med-note-document"); if (select) select.value = doc.document_id; return; }
    if (action === "exam") {
      this.show("exam");
      const select = $("#med-exam-document");
      if (select) select.value = doc.document_id;
      return;
    }
    const map = { analyze: "analyze_document", compare: "compare_document", process: "process_document" };
    const name = map[action];
    if (!name) return;
    const result = await this.request(name, { document_id: doc.document_id });
    if (result.ok === false) { toast(result.error || "İşlem başlatılamadı.", true); return; }
    toast(result.message || "Başlatıldı.", "ok");
    this.openDocument(doc.document_id, { quiet: true });
  },

  async openPage(pageNumber) {
    const doc = this.document;
    if (!doc) return;
    const result = await this.request("page", { document_id: doc.document_id, page_number: pageNumber });
    if (result.ok === false) { toast(result.error || "Sayfa açılamadı.", true); return; }
    this.page = result.page;
    $$("#med-doc-detail [data-page]").forEach((node) => node.classList.toggle("active", Number(node.dataset.page) === pageNumber));
    this.renderPage();
  },

  renderPage() {
    const host = $("#med-page-view");
    if (!host || !this.page) return;
    const page = this.page;
    host.innerHTML = `
      <div>${page.image ? `<div class="med-page-image"><img src="${page.image}" alt="Sayfa ${page.page_number}"></div>`
        : `<div class="med-explain">${esc(page.image_error || "Bu belge için sayfa görüntüsü yok.")}</div>`}</div>
      <div>
        ${(page.headings || []).length ? `<div class="med-chips">${page.headings.map((heading) => `<span class="chip">${esc(heading)}</span>`).join("")}</div>` : ""}
        ${page.visual_summary ? `<div class="med-explain"><h4>Şekil</h4>${esc(page.visual_summary)}
          ${(page.visual_labels || []).length ? `<div class="med-chips" style="margin-top:.4rem">${page.visual_labels.slice(0, 20).map((label) => `<span class="chip violet">${esc(label)}</span>`).join("")}</div>` : ""}</div>` : ""}
        <div class="med-page-text">${esc(page.text || "(bu sayfadan metin çıkarılamadı)")}</div>
      </div>`;
  },

  /* ── notes ─────────────────────────────────────────────────────── */

  async loadNotes() {
    const [notes, documents] = await Promise.all([this.request("notes"), this.request("documents")]);
    if (notes.ok !== false) this.notes = notes.notes || [];
    if (documents.ok !== false) this.documents = documents.documents || [];
    const select = $("#med-note-document");
    if (select) {
      select.innerHTML = `<option value="">— belgesiz (konudan) —</option>` +
        this.documents.filter((item) => item.ready).map((item) => `<option value="${esc(item.document_id)}">${esc(item.title)}</option>`).join("");
    }
    this.renderNotes();
  },

  renderNotes() {
    const host = $("#med-note-list");
    if (!host) return;
    if (!this.notes.length) {
      host.innerHTML = `<div class="panel med-card">${medEmpty("Not yok", "Yukarıdan bir not türü seç; JARVIS seçili konudan ve belgelerden sayfa atıflı not çıkarır.")}</div>`;
      return;
    }
    host.innerHTML = this.notes.map((note) => `<div class="panel med-note" data-note="${esc(note.note_id)}">
      <h3>${esc(note.title)}</h3>
      <div class="med-chips">${note.subject_label ? `<span class="chip accent">${esc(note.subject_label)}</span>` : ""}
        ${note.topic_label ? `<span class="chip">${esc(note.topic_label)}</span>` : ""}
        <span class="chip">${esc(fmtRelative(note.created_at))}</span></div>
      <div class="med-note-body">${renderMarkdown(note.content)}</div>
      ${(note.references || []).length ? `<div class="med-chips">${note.references.map((ref) => `<span class="chip" title="${esc(ref.title)}">s. ${ref.page_number}</span>`).join("")}</div>` : ""}
      <div class="btn-row"><button type="button" class="btn btn-ghost small" data-delete="${esc(note.note_id)}">Sil</button></div>
    </div>`).join("");
    $$("[data-delete]", host).forEach((node) => node.addEventListener("click", () => this.deleteNote(node.dataset.delete)));
  },

  async createNote(event) {
    event.preventDefault();
    const session = (this.state && this.state.session) || {};
    const documentId = $("#med-note-document") ? $("#med-note-document").value : "";
    const result = await this.request("create_note", {
      mode: $("#med-note-mode").value,
      subject: session.subject || "",
      topic_id: session.topic_id || "",
      document_ids: documentId ? [documentId] : [],
      page_from: Number($("#med-note-from").value) || 0,
      page_to: Number($("#med-note-to").value) || 0,
      depth: session.depth || "standard",
    });
    if (result.ok === false) { toast(result.error || "Not hazırlanamadı.", true); return; }
    toast(result.message || "Not hazırlanıyor.", "ok");
  },

  async deleteNote(noteId) {
    const note = this.notes.find((item) => item.note_id === noteId);
    const ok = await confirmDialog({ title: "Not silinsin mi?", body: `“${note ? note.title : noteId}” kalıcı olarak silinecek.`, confirmLabel: "SİL", danger: true });
    if (!ok) return;
    const result = await this.request("delete_note", { note_id: noteId, confirmed: true });
    if (result.ok === false) { toast(result.error || "Not silinemedi.", true); return; }
    this.notes = result.notes || [];
    this.renderNotes();
    toast("Not silindi.", "ok");
  },

  /* ── exams ─────────────────────────────────────────────────────── */

  async loadExams() {
    const [exams, documents] = await Promise.all([this.request("exams"), this.request("documents")]);
    if (exams.ok !== false) this.exams = exams.exams || [];
    if (documents.ok !== false) this.documents = documents.documents || [];
    this.renderExamForm();
    this.renderExamList();
  },

  renderExamForm() {
    const session = (this.state && this.state.session) || {};
    const options = session.options || {};
    const difficulty = $("#med-exam-difficulty");
    if (difficulty && !difficulty.options.length) {
      difficulty.innerHTML = MED_DIFFICULTY.map(([value, label]) => `<option value="${value}">${esc(label)}</option>`).join("");
    }
    if (difficulty) difficulty.value = String(session.difficulty || 3);
    const priority = $("#med-exam-priority");
    if (priority) {
      priority.innerHTML = (options.knowledge_priorities || []).map((item) => `<option value="${esc(item.value)}">${esc(item.label)}</option>`).join("");
      priority.value = session.knowledge_priority || "balanced";
    }
    const professor = $("#med-exam-professor");
    if (professor) {
      const list = (this.state && this.state.professors) || this.professors || [];
      professor.innerHTML = `<option value="">— hoca tarzı yok —</option>` +
        list.map((item) => `<option value="${esc(item.profile_id)}">${esc(item.name)} (${item.sample_size} soru)</option>`).join("");
    }
    const document = $("#med-exam-document");
    if (document) {
      document.innerHTML = `<option value="">— belgesiz —</option>` +
        this.documents.filter((item) => item.ready).map((item) => `<option value="${esc(item.document_id)}">${esc(item.title)}</option>`).join("");
    }
    const count = $("#med-exam-count");
    if (count) count.value = String(session.question_count || 10);
    const optionCount = $("#med-exam-options");
    if (optionCount) optionCount.value = String(session.option_count || 5);
    const note = $("#med-exam-note");
    if (note) {
      const session2 = (this.state && this.state.session && this.state.session.labels) || {};
      note.textContent = `${session2.subject || "Ders seçilmedi"} · ${session2.topic || "Konu seçilmedi"}`;
    }
  },

  examConfig() {
    const session = (this.state && this.state.session) || {};
    const documentId = $("#med-exam-document") ? $("#med-exam-document").value : "";
    return {
      subjects: session.subject ? [session.subject] : [],
      topic_ids: session.topic_id ? [session.topic_id] : [],
      document_ids: documentId ? [documentId] : [],
      page_from: Number($("#med-exam-from").value) || 0,
      page_to: Number($("#med-exam-to").value) || 0,
      question_count: Number($("#med-exam-count").value) || 10,
      option_count: Number($("#med-exam-options").value) || 5,
      difficulty: Number($("#med-exam-difficulty").value) || 3,
      professor_id: $("#med-exam-professor").value || null,
      knowledge_priority: $("#med-exam-priority").value,
      timed_seconds: (Number($("#med-exam-timed").value) || 0) * 60,
      immediate_feedback: $("#med-exam-immediate").checked,
      answers_at_end: $("#med-exam-end").checked,
      weak_emphasis: $("#med-exam-weak").checked,
      wrong_only: $("#med-exam-wrong").checked,
      include_images: $("#med-exam-images") ? $("#med-exam-images").checked : false,
    };
  },

  async createExam(event, fromBank) {
    if (event) event.preventDefault();
    const config = this.examConfig();
    if (fromBank) config.from_bank = true;
    const button = $("#med-exam-create");
    if (button) button.disabled = true;
    const result = await this.request("create_exam", { config });
    if (button) button.disabled = false;
    if (result.ok === false) { toast(result.error || "Sınav hazırlanamadı.", true); return; }
    toast(result.message || "Sınav hazırlanıyor.", "ok");
  },

  renderExamList() {
    const host = $("#med-exam-list");
    if (!host) return;
    if (!this.exams.length) {
      host.innerHTML = medEmpty("Kayıtlı sınav yok", "Yukarıdaki formdan bir sınav kur ya da sohbette “bu konudan 20 soru hazırla” de.");
      return;
    }
    host.innerHTML = this.exams.map((item) => `<button type="button" class="med-row ${this.exam && this.exam.exam_id === item.exam_id ? "active" : ""}" data-exam="${esc(item.exam_id)}">
      <span class="med-row-title">${esc(item.title)}</span>
      <span class="med-row-side">${item.percent === null || item.percent === undefined ? tr(item.status) : "%" + item.percent}</span>
      <span class="med-row-meta">${item.question_count} soru · zorluk ${item.config.difficulty}/5 · ${esc(fmtRelative(item.created_at))}${item.config.professor_id ? " · hoca tarzı" : ""}</span></button>`).join("");
    $$("[data-exam]", host).forEach((node) => node.addEventListener("click", () => this.openExam(node.dataset.exam)));
  },

  async openExam(examId) {
    const result = await this.request("exam", { exam_id: examId });
    if (result.ok === false) { toast(result.error || "Sınav açılamadı.", true); return; }
    this.exam = result.exam;
    const finished = !!(this.exam.attempt && this.exam.attempt.finished_at);
    this.runner = { index: Math.max(0, Number(this.exam.attempt && this.exam.attempt.current_index) || 0), finished };
    this.renderExamList();
    this.renderRunner();
    // The builder form and the list sit above the paper; an opened exam is
    // what the student came for, so bring it to the top of the view.
    const runner = $("#med-exam-runner");
    if (runner && typeof runner.scrollIntoView === "function") runner.scrollIntoView({ behavior: "smooth", block: "start" });
  },

  async startExam() {
    if (!this.exam) return;
    const result = await this.request("start_exam", { exam_id: this.exam.exam_id });
    if (result.ok === false) { toast(result.error || "Sınav başlatılamadı.", true); return; }
    this.exam = result.exam;
    this.runner = { index: 0, finished: false, startedAt: Date.now() };
    this.renderRunner();
  },

  renderRunner() {
    const host = $("#med-exam-runner");
    if (!host) return;
    if (!this.exam) { host.hidden = true; return; }
    host.hidden = false;
    const exam = this.exam;
    const attempt = exam.attempt || {};
    const finished = !!attempt.finished_at;
    if (finished) { this.renderResult(host); return; }
    const questions = exam.questions || [];
    if (!questions.length) { host.innerHTML = medEmpty("Bu sınavda soru yok"); return; }
    const index = clamp(this.runner ? this.runner.index : 0, 0, questions.length - 1);
    const question = questions[index];
    const answered = questions.filter((item) => item.answer).length;
    const immediate = exam.config.immediate_feedback;
    const revealed = immediate && !!question.answer;
    // What the quality filter rejected is part of the paper's honesty, so it is
    // shown with the paper rather than left in a payload nothing renders.
    const notes = (exam.notes || []).filter(Boolean);
    host.innerHTML = `
      ${notes.length ? `<div class="med-note-strip">${notes.map((note) => `<span class="med-row-sub">${esc(note)}</span>`).join("")}</div>` : ""}
      <div class="med-runner-head">
        <span class="med-runner-title">${esc(exam.title)}</span>
        <span class="chip">${index + 1} / ${questions.length}</span>
        <span class="chip">${answered} yanıtlandı</span>
        ${exam.config.timed_seconds ? `<span id="med-timer" class="med-timer"></span>` : ""}
        <button type="button" class="btn btn-ghost small" data-run="finish">Sınavı bitir</button>
      </div>
      <div class="med-dots">${questions.map((item, position) => {
        const state = [
          item.answer ? "answered" : "",
          item.flagged ? "flagged" : "",
          position === index ? "current" : "",
          item.correct === true ? "correct" : item.correct === false ? "wrong" : "",
        ].filter(Boolean).join(" ");
        return `<button type="button" class="med-dot ${state}" data-goto="${position}">${position + 1}</button>`;
      }).join("")}</div>
      <div class="panel med-question">
        <div class="mq-meta">
          <span class="chip accent">${esc(question.subject_label)}</span>
          <span class="chip">zorluk ${question.difficulty}/5</span>
          <span class="chip">${esc(MED_ORIGIN_TR[question.origin] || question.origin)}</span>
          ${question.topic_label ? `<span class="chip">${esc(question.topic_label)}</span>` : ""}
          <button type="button" class="chip ${question.flagged ? "warn" : ""}" data-run="flag">${question.flagged ? "İşaret kaldır" : "İşaretle"}</button>
        </div>
        ${this.figureMarkup(question)}
        <div class="mq-stem">${esc(question.stem)}</div>
        <div class="med-options">${(question.options || []).map((option) => {
          const chosen = question.answer === option.key;
          const isCorrect = revealed && question.correct_key === option.key;
          const isWrong = revealed && chosen && question.correct_key !== option.key;
          return `<button type="button" class="med-option ${chosen ? "chosen" : ""} ${isCorrect ? "correct" : ""} ${isWrong ? "wrong" : ""}" data-option="${esc(option.key)}">
            <span class="mo-key">${esc(option.key)}</span><span>${esc(option.text)}
            ${revealed && option.explanation ? `<span class="mo-why">${esc(option.explanation)}</span>` : ""}</span></button>`;
        }).join("")}</div>
        ${revealed && question.explanation ? `<div class="med-explain"><h4>Açıklama</h4>${esc(question.explanation)}
          ${question.trap ? `<br><b>Tuzak:</b> ${esc(question.trap)}` : ""}
          ${(question.references || []).map((ref) => `<br><span class="med-source">${esc(ref.title)} · s. ${ref.page_number}</span>`).join("")}</div>` : ""}
      </div>
      <div class="med-runner-nav">
        <button type="button" class="btn btn-ghost small" data-run="prev" ${index === 0 ? "disabled" : ""}>← Önceki</button>
        <button type="button" class="btn btn-ghost small" data-run="next" ${index >= questions.length - 1 ? "disabled" : ""}>Sonraki →</button>
        <span class="spacer"></span>
        <button type="button" class="btn btn-ghost small" data-run="ask">JARVIS'e sor</button>
      </div>`;
    this.loadFigures(host);
    $$("[data-option]", host).forEach((node) => node.addEventListener("click", () => this.answer(question.question_id, node.dataset.option)));
    $$("[data-goto]", host).forEach((node) => node.addEventListener("click", () => { this.runner.index = Number(node.dataset.goto); this.renderRunner(); }));
    $$("[data-run]", host).forEach((node) => node.addEventListener("click", () => this.runnerAction(node.dataset.run, question)));
    this.startTimer();
  },

  figureMarkup(question) {
    const figure = question && question.figure;
    if (!figure || !figure.document_id) return "";
    // One of the student's own lecture pages, never something drawn by JARVIS:
    // the caption names the document and page so the source is always visible.
    return `<figure class="mq-figure" data-figure="${esc(figure.document_id + "|" + figure.page_number)}">
      <div class="mq-figure-frame"><span class="mq-figure-wait">Şekil yükleniyor…</span></div>
      <figcaption>${esc(figure.caption || `${figure.title} · s. ${figure.page_number}`)}</figcaption>
    </figure>`;
  },

  async loadFigures(host) {
    this.figureCache = this.figureCache || new Map();
    for (const node of $$("[data-figure]", host)) {
      const key = node.dataset.figure;
      const frame = $(".mq-figure-frame", node);
      if (!frame) continue;
      let image = this.figureCache.get(key);
      if (image === undefined) {
        const [documentId, page] = key.split("|");
        const result = await this.request("page", { document_id: documentId, page_number: Number(page), image: true });
        image = result.ok !== false && result.page ? result.page.image || null : null;
        this.figureCache.set(key, image);
      }
      if (!node.isConnected) continue;
      frame.innerHTML = image
        ? `<img src="${esc(image)}" alt="Ders notu sayfası">`
        : `<span class="mq-figure-wait">Sayfa görseli alınamadı; kaynağı Kütüphane'den açabilirsin.</span>`;
    }
  },

  startTimer() {
    clearInterval(this.timer);
    const node = $("#med-timer");
    if (!node || !this.exam || !this.exam.config.timed_seconds) return;
    const started = this.exam.attempt && this.exam.attempt.started_at ? new Date(this.exam.attempt.started_at).getTime() : Date.now();
    const total = this.exam.config.timed_seconds * 1000;
    const tick = () => {
      const remaining = Math.max(0, started + total - Date.now());
      const minutes = Math.floor(remaining / 60000);
      const seconds = Math.floor((remaining % 60000) / 1000);
      node.textContent = `${minutes}:${String(seconds).padStart(2, "0")}`;
      node.classList.toggle("low", remaining < 60000);
      if (remaining <= 0) { clearInterval(this.timer); this.finishExam({ auto: true }); }
    };
    tick();
    this.timer = setInterval(tick, 1000);
  },

  async runnerAction(action, question) {
    if (action === "prev") { this.runner.index = Math.max(0, this.runner.index - 1); this.renderRunner(); return; }
    if (action === "next") { this.runner.index = Math.min((this.exam.questions || []).length - 1, this.runner.index + 1); this.renderRunner(); return; }
    if (action === "finish") { await this.finishExam(); return; }
    if (action === "flag") { await this.answer(question.question_id, question.answer, { flagged: !question.flagged }); return; }
    if (action === "ask") { this.quickAsk(`Bu soruyu açıkla: ${question.stem}`); return; }
  },

  async answer(questionId, answerKey, { flagged } = {}) {
    if (!this.exam) return;
    const params = { exam_id: this.exam.exam_id, question_id: questionId, answer_key: answerKey || "", current_index: this.runner.index };
    if (flagged !== undefined) params.flagged = flagged;
    const result = await this.request("answer", params);
    if (result.ok === false) { toast(result.error || "Cevap kaydedilemedi.", true); return; }
    const question = (this.exam.questions || []).find((item) => item.question_id === questionId);
    if (question) {
      question.answer = result.answer;
      question.flagged = result.flagged;
      if (result.feedback) {
        question.correct = result.feedback.correct;
        question.correct_key = result.feedback.correct_key;
        question.explanation = result.feedback.why_correct;
        question.trap = result.feedback.trap;
        (question.options || []).forEach((option) => {
          const match = (result.feedback.other_options || []).find((item) => item.key === option.key);
          if (match) option.explanation = match.why_wrong;
        });
      }
    }
    if (!this.exam.config.immediate_feedback && this.runner.index < (this.exam.questions || []).length - 1) {
      this.runner.index += 1;
    }
    this.renderRunner();
  },

  async finishExam({ auto = false } = {}) {
    if (!this.exam) return;
    if (!auto) {
      const unanswered = (this.exam.questions || []).filter((item) => !item.answer).length;
      const ok = await confirmDialog({
        title: "Sınav bitirilsin mi?",
        body: unanswered ? `${unanswered} soru boş kaldı. Bitirince cevaplar ve analiz gösterilir.` : "Cevapların değerlendirilecek ve analiz gösterilecek.",
        confirmLabel: "BİTİR",
      });
      if (!ok) return;
    }
    clearInterval(this.timer);
    const result = await this.request("finish_exam", { exam_id: this.exam.exam_id });
    if (result.ok === false) { toast(result.error || "Sınav bitirilemedi.", true); return; }
    this.exam = result.exam;
    this.runner = { index: 0, finished: true };
    this.renderRunner();
    this.loadExams();
  },

  renderResult(host) {
    const exam = this.exam;
    const analysis = exam.analysis || {};
    const questions = exam.questions || [];
    const rows = (list) => (list || []).map((row) => medBar(row.accuracy === null ? 0 : row.accuracy,
      { label: row.label, value: `${row.correct}/${row.total}` })).join("");
    host.innerHTML = `
      <div class="med-result">
        <div class="panel med-card">
          <div class="panel-title"><span class="kicker">Sonuç</span><span class="faint">${esc(exam.title)}</span></div>
          <div class="med-score">
            <span class="ms-value">${analysis.percent === null || analysis.percent === undefined ? "—" : "%" + analysis.percent}</span>
            <span class="ms-note">${analysis.correct || 0} doğru · ${analysis.incorrect || 0} yanlış · ${analysis.unanswered || 0} boş${analysis.ungradable ? ` · ${analysis.ungradable} anahtarsız` : ""}${analysis.elapsed_seconds ? ` · ${fmtDuration(analysis.elapsed_seconds * 1000)}` : ""}</span>
          </div>
          ${analysis.suggestion ? `<div class="med-explain"><h4>Sıradaki adım</h4>${esc(analysis.suggestion.text)}</div>` : ""}
          ${analysis.adaptive ? `<div class="med-explain"><h4>Uyarlanabilir zorluk</h4>${esc(analysis.adaptive.reason)}</div>` : ""}
        </div>
        <div class="med-breakdown">
          <div class="panel med-card"><div class="panel-title"><span class="kicker">Konu</span></div>${rows(analysis.by_topic) || medEmpty("Veri yok")}</div>
          <div class="panel med-card"><div class="panel-title"><span class="kicker">Zorluk</span></div>${rows(analysis.by_difficulty) || medEmpty("Veri yok")}</div>
          <div class="panel med-card"><div class="panel-title"><span class="kicker">Ders</span></div>${rows(analysis.by_subject) || medEmpty("Veri yok")}</div>
        </div>
        ${(analysis.weak_concepts || []).length ? `<div class="panel med-card"><div class="panel-title"><span class="kicker">Zayıf kavramlar</span></div>
          <div class="med-chips">${analysis.weak_concepts.map((item) => `<span class="chip bad">${esc(item.label)} · ${item.correct}/${item.total}</span>`).join("")}</div>
          <div class="btn-row" style="justify-content:flex-start"><button type="button" class="btn btn-ghost small" data-result="review">Zayıf alanları tekrar et</button>
          <button type="button" class="btn btn-ghost small" data-result="retry">Yanlışlarımı tekrar sor</button></div></div>` : ""}
        ${this.reviewSection(questions)}
      </div>`;
    this.loadFigures(host);
    $$("[data-result]", host).forEach((node) => node.addEventListener("click", () => {
      if (node.dataset.result === "review") this.quickAsk("zayıf olduğum konuları tekrar et");
      if (node.dataset.result === "retry") { const wrong = $("#med-exam-wrong"); if (wrong) wrong.checked = true; this.createExam(null, false); }
    }));
    $$("[data-ask]", host).forEach((node) => node.addEventListener("click", () => this.quickAsk(node.dataset.ask)));
    $$("[data-source]", host).forEach((node) => node.addEventListener("click", () => {
      const [documentId, page] = node.dataset.source.split("|");
      this.show("library");
      this.openDocument(documentId).then(() => this.openPage(Number(page)));
    }));
  },

  reviewSection(questions) {
    // The student opens the results to see what went wrong: the wrong answers
    // come first with their explanation and the correct option, then the blanks,
    // then what was right. Positions keep the paper's numbering.
    const indexed = questions.map((question, position) => ({ question, position }));
    const wrong = indexed.filter(({ question }) => question.answer && question.correct === false);
    const blank = indexed.filter(({ question }) => !question.answer);
    const right = indexed.filter(({ question }) => question.correct === true);
    const block = (title, tone, items, note) => items.length
      ? `<div class="panel med-card"><div class="panel-title"><span class="kicker">${esc(title)}</span><span class="chip ${tone}">${items.length}</span></div>
          ${note ? `<p class="med-review-note">${esc(note)}</p>` : ""}
          <div class="med-bank-list">${items.map(({ question, position }) => this.reviewQuestion(question, position)).join("")}</div></div>`
      : "";
    return block("Yanlışların", "bad", wrong, "Her soruda doğru şık ✓ ile işaretli; altındaki açıklama neden doğru olduğunu, seçtiğin şıkkın yanındaki not neden yanlış olduğunu anlatır.")
      + block("Boş bıraktıkların", "warn", blank, "")
      + block("Doğruların", "ok", right, "");
  },

  reviewQuestion(question, position) {
    const correct = question.correct === true;
    const answered = !!question.answer;
    return `<div class="panel med-bank-item">
      <div class="mb-meta"><span class="chip ${correct ? "ok" : answered ? "bad" : "warn"}">${position + 1} · ${correct ? "doğru" : answered ? "yanlış" : "boş"}</span>
        <span class="chip">zorluk ${question.difficulty}/5</span>${question.topic_label ? `<span class="chip">${esc(question.topic_label)}</span>` : ""}</div>
      ${this.figureMarkup(question)}
      <div class="mb-stem">${esc(question.stem)}</div>
      <div class="mb-options">${(question.options || []).map((option) => {
        const isCorrect = option.key === question.correct_key;
        const chosen = option.key === question.answer;
        return `<span class="${isCorrect ? "ok" : ""}">${isCorrect ? "✓" : chosen ? "✗" : "·"} ${esc(option.key)}) ${esc(option.text)}${option.explanation ? ` — ${esc(option.explanation)}` : ""}</span>`;
      }).join("")}</div>
      ${question.explanation ? `<div class="med-explain">${esc(question.explanation)}${question.trap ? `<br><b>Tuzak:</b> ${esc(question.trap)}` : ""}</div>` : ""}
      <div class="mb-meta">
        <button type="button" class="chip" data-ask="${esc("Bu soruyu ayrıntılı açıkla: " + question.stem)}">JARVIS'e sor</button>
        ${(question.references || []).map((ref) => `<button type="button" class="chip" data-source="${esc(ref.document_id + "|" + ref.page_number)}">${esc(ref.title)} · s. ${ref.page_number}</button>`).join("")}
      </div>
    </div>`;
  },

  /* ── question bank ─────────────────────────────────────────────── */

  async loadBank() {
    const subject = $("#med-bank-subject");
    if (subject && !subject.options.length) {
      const options = ((this.state && this.state.session && this.state.session.options) || {}).subjects || [];
      subject.innerHTML = `<option value="">tüm dersler</option>` + options.map((item) => `<option value="${esc(item.value)}">${esc(item.label)}</option>`).join("");
    }
    const origin = $("#med-bank-origin");
    if (origin && !origin.options.length) {
      origin.innerHTML = `<option value="">her kaynak</option>` + Object.entries(MED_ORIGIN_TR).map(([value, label]) => `<option value="${esc(value)}">${esc(label)}</option>`).join("");
    }
    const answered = $("#med-bank-answered");
    if (answered && !answered.options.length) {
      answered.innerHTML = `<option value="">hepsi</option><option value="unanswered">çözülmemiş</option><option value="incorrect">yanlış yaptıklarım</option><option value="correct">doğru yaptıklarım</option>`;
    }
    const filters = {
      subject: subject ? subject.value : "",
      origin: origin ? origin.value : "",
      answered: answered ? answered.value : "",
      text: $("#med-bank-search") ? $("#med-bank-search").value : "",
      limit: 120,
    };
    const result = await this.request("bank", { filters });
    if (result.ok === false) { toast(result.error || "Soru bankası okunamadı.", true); return; }
    this.bank = result;
    this.renderBank();
  },

  renderBank() {
    const host = $("#med-bank-list");
    const count = $("#med-bank-count");
    if (!host || !this.bank) return;
    const counts = this.bank.counts || {};
    if (count) count.textContent = `${this.bank.total} gösteriliyor · toplam ${counts.total || 0}`;
    const questions = this.bank.questions || [];
    if (!questions.length) { host.innerHTML = medEmpty("Bu süzgeçle soru yok"); return; }
    host.innerHTML = questions.map((question) => `<div class="panel med-bank-item" data-question="${esc(question.question_id)}">
      <div class="mb-meta">
        <span class="chip accent">${esc(question.subject_label)}</span>
        <span class="chip">${esc(MED_ORIGIN_TR[question.origin] || question.origin)}</span>
        <span class="chip">zorluk ${question.difficulty}/5</span>
        ${question.has_answer_key ? "" : '<span class="chip warn">cevap anahtarı yok</span>'}
        ${question.last_result === true ? '<span class="chip ok">doğru yapmıştın</span>' : question.last_result === false ? '<span class="chip bad">yanlış yapmıştın</span>' : ""}
        ${(question.problems || []).length ? `<span class="chip warn" title="${esc(question.problems.join(", "))}">kalite uyarısı</span>` : ""}
        <span class="mb-actions">
          ${question.has_answer_key ? "" : `<select data-key="${esc(question.question_id)}" aria-label="Cevap anahtarı"><option value="">anahtar seç</option>${(question.options || []).map((option) => `<option value="${esc(option.key)}">${esc(option.key)}</option>`).join("")}</select>`}
          <button type="button" class="icon-btn small" data-remove="${esc(question.question_id)}" title="Sil">${icon("trash")}</button>
        </span>
      </div>
      <div class="mb-stem">${esc(question.stem)}</div>
      <div class="mb-options">${(question.options || []).map((option) =>
        `<span class="${option.key === question.correct_key ? "ok" : ""}">${esc(option.key)}) ${esc(option.text)}</span>`).join("")}</div>
      ${question.explanation ? `<div class="med-explain">${esc(question.explanation)}</div>` : ""}
      ${(question.references || []).length ? `<div class="mb-meta">${question.references.map((ref) => `<span class="chip">${esc(ref.title)} · s. ${ref.page_number}</span>`).join("")}</div>` : ""}
    </div>`).join("");
    $$("[data-remove]", host).forEach((node) => node.addEventListener("click", () => this.deleteQuestion(node.dataset.remove)));
    $$("[data-key]", host).forEach((node) => node.addEventListener("change", () => this.setAnswerKey(node.dataset.key, node.value)));
  },

  async deleteQuestion(questionId) {
    const ok = await confirmDialog({ title: "Soru silinsin mi?", body: "Soru bankadan kalıcı olarak kaldırılacak.", confirmLabel: "SİL", danger: true });
    if (!ok) return;
    const result = await this.request("delete_question", { question_id: questionId, confirmed: true });
    if (result.ok === false) { toast(result.error || "Soru silinemedi.", true); return; }
    this.loadBank();
  },

  async setAnswerKey(questionId, key) {
    if (!key) return;
    const result = await this.request("set_answer_key", { question_id: questionId, answer_key: key });
    if (result.ok === false) { toast(result.error || "Anahtar işaretlenemedi.", true); return; }
    toast("Cevap anahtarı işaretlendi.", "ok");
    this.loadBank();
  },

  /* ── professor style ───────────────────────────────────────────── */

  async loadProfessors() {
    const subject = $("#med-prof-subject");
    if (subject && !subject.options.length) {
      const options = ((this.state && this.state.session && this.state.session.options) || {}).subjects || [];
      subject.innerHTML = `<option value="">— ders —</option>` + options.map((item) => `<option value="${esc(item.value)}">${esc(item.label)}</option>`).join("");
    }
    const result = await this.request("professors");
    if (result.ok === false) { toast(result.error || "Hoca profilleri okunamadı.", true); return; }
    this.professors = result.professors || [];
    this.renderProfessors();
    if (this.professor) this.openProfessor(this.professor.profile_id);
    else this.renderProfessor();
  },

  renderProfessors() {
    const host = $("#med-prof-list");
    if (!host) return;
    if (!this.professors.length) {
      host.innerHTML = medEmpty("Hoca profili yok", "Bir hoca ekle, sonra eski sınav sorularını yükle.");
      return;
    }
    host.innerHTML = this.professors.map((item) => `<button type="button" class="med-row ${this.professor && this.professor.profile_id === item.profile_id ? "active" : ""}" data-prof="${esc(item.profile_id)}">
      <span class="med-row-title">${esc(item.name)}</span><span class="med-row-side">${item.sample_size} soru</span>
      <span class="med-row-meta"><span class="chip">${esc(item.subject_label)}</span><span class="chip ${item.confidence === "high" ? "ok" : item.confidence === "none" ? "" : "warn"}">${esc(item.confidence_label)}</span></span></button>`).join("");
    $$("[data-prof]", host).forEach((node) => node.addEventListener("click", () => this.openProfessor(node.dataset.prof)));
  },

  async addProfessor(event) {
    event.preventDefault();
    const name = $("#med-prof-name").value.trim();
    if (!name) { toast("Hoca adı gerekli.", true); return; }
    const result = await this.request("create_professor", { name, subject: $("#med-prof-subject").value });
    if (result.ok === false) { toast(result.error || "Hoca eklenemedi.", true); return; }
    $("#med-prof-name").value = "";
    this.professors = result.professors || [];
    this.renderProfessors();
    if (result.professor) this.openProfessor(result.professor.profile_id);
  },

  async openProfessor(profileId) {
    const result = await this.request("professor", { profile_id: profileId });
    if (result.ok === false) { toast(result.error || "Profil açılamadı.", true); return; }
    this.professor = result.professor;
    this.renderProfessors();
    this.renderProfessor();
  },

  renderProfessor() {
    const host = $("#med-prof-detail");
    if (!host) return;
    const profile = this.professor;
    if (!profile) {
      host.innerHTML = `<div class="panel med-card">${medEmpty("Hoca seç", "Bir hoca seçip eski sınavlarını yükle: soru yapısını, çeldirici tarzını ve terminoloji yoğunluğunu kanıta dayalı çıkarırım.")}</div>`;
      return;
    }
    const features = (profile.features || []).filter((feature) => feature.observed > 0);
    const distribution = profile.answer_distribution || {};
    const report = this.importReport && this.importReport.profile_id === profile.profile_id ? this.importReport : null;
    host.innerHTML = `
      <div class="panel med-card">
        <div class="panel-title"><span class="kicker">${esc(profile.subject_label)}</span><span class="chip ${profile.confidence === "high" ? "ok" : profile.confidence === "none" ? "" : "warn"}">${esc(profile.confidence_label)}</span></div>
        <h2>${esc(profile.name)}</h2>
        <div class="med-evidence">${esc(profile.basis)}</div>
        <div class="med-chips">
          <span class="chip">${profile.sample_size} soru</span>
          <span class="chip">ortalama ${profile.average_options} şık</span>
          <span class="chip">ortalama ${profile.average_stem_words} kelime</span>
          ${Object.keys(distribution).length ? `<span class="chip" title="${esc(Object.entries(distribution).map(([key, value]) => key + ": " + value).join(", "))}">cevap dağılımı</span>` : ""}
        </div>
        <div class="btn-row" style="justify-content:flex-start">
          <button type="button" class="btn btn-primary small" data-prof-act="import-file">Sınav dosyası yükle</button>
          <button type="button" class="btn btn-ghost small" data-prof-act="import-text">Metin yapıştır</button>
          <button type="button" class="btn btn-ghost small" data-prof-act="exam">Bu tarzda sınav</button>
          <button type="button" class="btn btn-ghost small" data-prof-act="reset">Profili sıfırla</button>
          <button type="button" class="btn btn-ghost small" data-prof-act="delete">Sil</button>
        </div>
      </div>
      ${report ? `<div class="panel med-card">
        <div class="panel-title"><span class="kicker">Son içe aktarma</span><span class="faint">bu oturumda</span></div>
        <div class="med-chips">
          <span class="chip ok">${report.added} soru eklendi</span>
          ${report.skipped ? `<span class="chip warn">${report.skipped} yinelenen atlandı</span>` : ""}
          ${report.without_key ? `<span class="chip warn">${report.without_key} soru anahtarsız</span>` : ""}
        </div>
        ${report.notes.length
          ? report.notes.map((note) => `<div class="med-row"><span class="med-row-sub">${esc(note)}</span></div>`).join("")
          : '<p class="settings-note">Ayrıştırma not bırakmadı.</p>'}
      </div>` : ""}
      <div class="panel med-card">
        <div class="panel-title"><span class="kicker">Gözlemlenen özellikler</span><span class="faint">oran = gözlem / örneklem</span></div>
        ${features.length ? features.map((feature) => `<div class="med-feature">
            <span class="mf-name">${esc(feature.label)}</span><span class="mf-count">${feature.observed}/${feature.total} · ${esc(feature.level_label)}</span>
            <span class="med-bar"><i class="${feature.ratio < 0.3 ? "mid" : ""}" style="transform: scaleX(${Number(feature.ratio).toFixed(3)})"></i></span>
          </div>`).join("")
        : medEmpty("Henüz özellik gözlemlenmedi", "Sınav yükleyince oranlar burada belirir.")}
        ${profile.sample_size && profile.sample_size < 10 ? '<p class="settings-note">Örneklem küçük: bu oranlar bir eğilim değil, yalnızca gözlemdir.</p>' : ""}
      </div>
      ${(profile.questions || []).length ? `<div class="panel med-card"><div class="panel-title"><span class="kicker">Yüklenen sorular</span><span class="faint">${profile.questions.length}</span></div>
        <div class="med-bank-list">${profile.questions.slice(0, 30).map((question) => `<div class="panel med-bank-item">
          <div class="mb-meta"><span class="chip">${esc(MED_ORIGIN_TR[question.origin] || question.origin)}</span>${question.has_answer_key ? `<span class="chip ok">anahtar: ${esc(question.correct_key)}</span>` : '<span class="chip warn">anahtar yok</span>'}</div>
          <div class="mb-stem">${esc(question.stem)}</div>
          <div class="mb-options">${(question.options || []).map((option) => `<span class="${option.key === question.correct_key ? "ok" : ""}">${esc(option.key)}) ${esc(option.text)}</span>`).join("")}</div>
        </div>`).join("")}</div></div>` : ""}`;
    $$("[data-prof-act]", host).forEach((node) => node.addEventListener("click", () => this.professorAction(node.dataset.profAct)));
  },

  async professorAction(action) {
    const profile = this.professor;
    if (!profile) return;
    if (action === "import-file") {
      const picked = await call("medical_pick_file", "questions");
      if (picked.ok === false) { toast(picked.error || "Dosya seçilemedi.", true); return; }
      if (!picked.path) return;
      const image = /\.(png|jpe?g|webp)$/i.test(picked.path);
      const params = { profile_id: profile.profile_id, subject: profile.subject || "" };
      if (image) params.image_path = picked.path; else params.path = picked.path;
      const result = await this.request("import_questions", params);
      if (result.ok === false) { toast(result.error || "Sorular içe aktarılamadı.", true); return; }
      toast(result.message || "Sorular içe aktarılıyor.", "ok");
      return;
    }
    if (action === "import-text") {
      const text = await promptDialog({
        title: "Sınav sorularını yapıştır",
        body: "Numaralı sorular ve A) B) C) biçimli şıklar bekleniyor. Cevap anahtarı metinde varsa okunur; yoksa asla tahmin edilmez.",
        placeholder: "1. Scapula'nın spina scapulae'si…\nA) …\nB) …",
      });
      if (!text) return;
      const result = await this.request("import_questions", { profile_id: profile.profile_id, subject: profile.subject || "", text });
      if (result.ok === false) { toast(result.error || "Sorular içe aktarılamadı.", true); return; }
      toast(result.message || "Sorular içe aktarılıyor.", "ok");
      return;
    }
    if (action === "exam") {
      this.show("exam");
      const select = $("#med-exam-professor");
      if (select) select.value = profile.profile_id;
      return;
    }
    if (action === "reset") {
      const ok = await confirmDialog({
        title: "Profil sıfırlansın mı?",
        body: `“${profile.name}” profilinin çıkarılan tarzı sıfırlanacak; yüklenen sorular soru bankasında kalır.`,
        confirmLabel: "SIFIRLA", danger: true,
      });
      if (!ok) return;
      const result = await this.request("reset_professor", { profile_id: profile.profile_id, confirmed: true });
      if (result.ok === false) { toast(result.error || "Profil sıfırlanamadı.", true); return; }
      this.professor = result.professor;
      this.professors = result.professors || [];
      this.renderProfessors();
      this.renderProfessor();
      toast("Profil sıfırlandı.", "ok");
      return;
    }
    if (action === "delete") {
      const ok = await confirmDialog({
        title: "Hoca profili silinsin mi?",
        body: `“${profile.name}” profili kaldırılacak. Yüklenen sorular soru bankasında kalır.`,
        confirmLabel: "SİL", danger: true,
      });
      if (!ok) return;
      const result = await this.request("delete_professor", { profile_id: profile.profile_id, confirmed: true });
      if (result.ok === false) { toast(result.error || "Profil silinemedi.", true); return; }
      this.professor = null;
      this.professors = result.professors || [];
      this.renderProfessors();
      this.renderProfessor();
      toast("Profil silindi.", "ok");
    }
  },

  /* ── progress ──────────────────────────────────────────────────── */

  async loadProgress() {
    const result = await this.request("progress");
    if (result.ok === false) { toast(result.error || "İlerleme okunamadı.", true); return; }
    this.progressData = result;
    this.renderProgress();
  },

  renderProgress() {
    const data = this.progressData;
    if (!data) return;
    const summary = data.summary || {};
    const metrics = $("#med-progress-metrics");
    if (metrics) {
      const levels = summary.levels || {};
      metrics.innerHTML = [
        [summary.concepts || 0, "izlenen kavram"],
        [summary.attempts || 0, "soru denemesi"],
        [summary.accuracy === null || summary.accuracy === undefined ? "—" : medPercent(summary.accuracy), "genel doğruluk"],
        [levels.weak || 0, "zayıf"],
        [levels.moderate || 0, "orta"],
        [levels.strong || 0, "güçlü"],
        [summary.due_reviews || 0, "tekrar bekliyor"],
      ].map(([value, label]) => `<div class="metric"><div class="metric-value">${esc(String(value))}</div><div class="metric-note">${esc(label)}</div></div>`).join("");
    }
    const queue = $("#med-progress-queue");
    if (queue) {
      const items = data.review_queue || [];
      queue.innerHTML = items.length
        ? items.map((item) => `<div class="med-row"><span class="med-row-title">${esc(item.name)}</span>
            <span class="med-row-side">${esc(item.level_label)}</span><span class="med-row-meta">${esc(item.reason)}</span></div>`).join("")
        : medEmpty("Tekrar bekleyen kavram yok");
    }
    const insights = $("#med-progress-insights");
    if (insights) {
      const items = data.insights || [];
      insights.innerHTML = items.length
        ? items.map((line) => `<div class="med-row"><span class="med-row-sub">${esc(line)}</span></div>`).join("")
        : medEmpty("Yeterli veri yok", "Birkaç sınav sonrası burada gerçek örüntüler görünür.");
    }
    const list = $("#med-mastery-list");
    const count = $("#med-mastery-count");
    const all = data.all || [];
    if (count) count.textContent = all.length ? `${all.length} kavram` : "";
    if (list) list.innerHTML = all.length ? all.map((item) => this.masteryCard(item)).join("") : medEmpty("Henüz kavram izlenmiyor");
  },

  /* ── push events ───────────────────────────────────────────────── */

  onPush(payload) {
    const kind = String(payload && payload.kind);
    if (kind === "job_failed") { toast(`Tıp Akademisi işi başarısız: ${payload.message || payload.error}`, true); return; }
    if (kind === "session_updated") { this.refresh(); return; }
    if (kind === "document_status" || kind === "document_ready" || kind === "document_analyzed" || kind === "comparison_ready" || kind === "document_deleted") {
      if (State.screen === "medical" && this.view === "library") this.loadDocuments();
      return;
    }
    if (kind === "exam_ready" || kind === "exam_finished") {
      if (kind === "exam_ready" && payload.exam_id && payload.open) {
        // The student asked for a paper, from chat or from the form: put it in
        // front of them rather than a toast that points at a list.
        showScreen("medical");
        this.show("exam");
        this.loadExams().then(() => this.openExam(payload.exam_id));
        return;
      }
      if (State.screen === "medical" && this.view === "exam") this.loadExams();
      if (kind === "exam_ready" && payload.exam_id) toast(`Sınav hazır: ${payload.title}`, "ok");
      return;
    }
    if (kind === "note_ready") {
      if (State.screen === "medical" && this.view === "notes") this.loadNotes();
      else toast(`Not hazır: ${payload.title}`, "ok");
      return;
    }
    if (kind === "professor_updated") {
      if (State.screen === "medical" && this.view === "professor") this.loadProfessors();
      return;
    }
    if (kind === "job_report") { this.onJobReport(payload); return; }
    if (kind === "anatomy_open") {
      showScreen("medical");
      this.show("anatomy");
      Lab.select(payload.structure_id, { highlight: payload.highlight || [], quiz: !!payload.quiz });
      return;
    }
    if (kind === "quiz_ready") { toast(`Quiz hazır: ${payload.title}`, "ok"); return; }
    if (kind === "quiz_started" || kind === "quiz_progress" || kind === "quiz_finished") return;
    if (kind === "refresh") { if (State.screen === "medical") this.loadView(this.view); return; }
  },

  /* An import's own account of itself — questions whose options could not
     be parsed, duplicates it skipped, keys the paper never stated, and
     whether the model had to read the structure — is computed by the core
     and has nowhere else to appear. A five-second toast is not a record,
     so it is kept with the profile it belongs to and rendered there. */
  onJobReport(payload) {
    if (String(payload.job) !== "import") return;
    const notes = Array.isArray(payload.notes) ? payload.notes.map((note) => String(note)) : [];
    const report = {
      profile_id: String(payload.profile_id || ""),
      added: Number(payload.added) || 0,
      skipped: Number(payload.skipped) || 0,
      without_key: Number(payload.without_key) || 0,
      notes,
    };
    this.importReport = report;
    const parts = [`${report.added} soru eklendi`];
    if (report.skipped) parts.push(`${report.skipped} yinelenen atlandı`);
    if (report.without_key) parts.push(`${report.without_key} soru anahtarsız`);
    toast(`İçe aktarma bitti: ${parts.join(", ")}.${notes.length ? " Notlar “Hoca tarzı” ekranında." : ""}`,
      notes.length ? "" : "ok");
    if (this.professor && this.professor.profile_id === report.profile_id) this.renderProfessor();
  },
};

const MED_COMPARISON_TR = {
  consistent: "Tutarlı",
  simplified: "Basitleştirilmiş",
  incomplete: "Eksik",
  potentially_misleading: "Yanıltıcı olabilir",
  possibly_incorrect: "Muhtemelen hatalı",
  terminology_difference: "Terminoloji farkı",
};

/* Minimal, safe Markdown: the notes the core produces use headings,
   bullets, bold and tables. Everything is escaped first, so no model
   output can inject markup. */
function renderMarkdown(text) {
  const lines = String(text || "").split(/\r?\n/);
  const out = [];
  let list = false;
  let table = false;
  const closeList = () => { if (list) { out.push("</ul>"); list = false; } };
  const closeTable = () => { if (table) { out.push("</table>"); table = false; } };
  const inline = (value) => esc(value)
    .replace(/\*\*([^*]+)\*\*/g, "<b>$1</b>")
    .replace(/`([^`]+)`/g, "<code>$1</code>")
    .replace(/(^|\s)\*([^*]+)\*(?=\s|$)/g, "$1<i>$2</i>");
  lines.forEach((raw) => {
    const line = raw.trimEnd();
    if (!line.trim()) { closeList(); closeTable(); return; }
    const heading = line.match(/^(#{1,4})\s+(.*)$/);
    if (heading) { closeList(); closeTable(); out.push(`<h4>${inline(heading[2])}</h4>`); return; }
    if (/^\s*[-*•]\s+/.test(line)) {
      closeTable();
      if (!list) { out.push("<ul>"); list = true; }
      out.push(`<li>${inline(line.replace(/^\s*[-*•]\s+/, ""))}</li>`);
      return;
    }
    if (/^\s*\|.*\|\s*$/.test(line)) {
      closeList();
      if (/^\s*\|[\s:|-]+\|\s*$/.test(line)) return;
      if (!table) { out.push('<table class="med-table">'); table = true; }
      const cells = line.trim().replace(/^\||\|$/g, "").split("|");
      out.push("<tr>" + cells.map((cell) => `<td>${inline(cell.trim())}</td>`).join("") + "</tr>");
      return;
    }
    closeList(); closeTable();
    out.push(`<p>${inline(line)}</p>`);
  });
  closeList(); closeTable();
  return out.join("");
}

/* A small text prompt built on the existing confirmation dialog styling. */
function promptDialog({ title, body, placeholder = "" }) {
  return new Promise((resolve) => {
    const veil = $("#confirm");
    const ok = $("#confirm-ok"), cancel = $("#confirm-cancel");
    const textNode = $("#confirm-text");
    $("#confirm-title").textContent = title;
    textNode.innerHTML = `${esc(body)}<textarea id="confirm-input" class="mem-edit" rows="8" placeholder="${esc(placeholder)}"></textarea>`;
    ok.textContent = "İÇE AKTAR";
    cancel.textContent = "VAZGEÇ";
    ok.className = "btn btn-primary";
    const finish = (value) => {
      ok.onclick = null; cancel.onclick = null;
      window.removeEventListener("keydown", onKey, true);
      veil.hidden = true;
      textNode.textContent = "";
      resolve(value);
    };
    const onKey = (event) => { if (event.key === "Escape") { event.preventDefault(); event.stopPropagation(); finish(null); } };
    ok.onclick = () => finish(($("#confirm-input").value || "").trim() || null);
    cancel.onclick = () => finish(null);
    window.addEventListener("keydown", onKey, true);
    veil.hidden = false;
    Motion.rise(veil.querySelector(".modal"), { y: 14, scale: 0.97, duration: Motion.panel });
    $("#confirm-input").focus();
  });
}

/* ════════════════════════════════════════════════════════════════════
   Lab: the Anatomy Lab
   A real WebGL viewer when a licensed mesh is registered, and an honest
   schematic relationship map when one is not. Geometry is never
   invented: no asset means no bone on screen.
   ════════════════════════════════════════════════════════════════════ */

/* Colour per system, muted and stable in both themes: the point of the
   scene is telling a vein from an artery from a nerve at a glance. */
const LAB_KIND_COLOURS = {
  bone: [0.86, 0.83, 0.76],
  joint: [0.70, 0.75, 0.80],
  muscle: [0.70, 0.33, 0.30],
  artery: [0.85, 0.24, 0.21],
  vein: [0.29, 0.43, 0.78],
  nerve: [0.92, 0.80, 0.34],
  ligament: [0.78, 0.74, 0.60],
  region: [0.60, 0.60, 0.60],
};
const LAB_KIND_LABELS = { bone: "Kemik", joint: "Eklem", muscle: "Kas", artery: "Arter", vein: "Ven", nerve: "Sinir", ligament: "Bağ" };
const LAB_LAYER_ORDER = ["bone", "joint", "muscle", "artery", "vein", "nerve", "ligament"];

const Lab = {
  hierarchy: [],
  scenes: [],
  scene: null,
  structure: null,
  mesh: null,
  highlight: [],
  showLabels: true,
  detailed: false,
  pinCard: null,
  bell: null,
  quiz: null,
  gl: null,
  program: null,
  buffers: null,
  camera: { yaw: 0.6, pitch: 0.25, distance: 2.6, panX: 0, panY: 0 },
  dragging: null,
  frame: 0,

  async open() {
    if (!this.hierarchy.length) {
      const result = await Medical.request("anatomy");
      if (result.ok === false) { toast(result.error || "Anatomi Lab okunamadı.", true); return; }
      this.hierarchy = result.hierarchy || [];
      this.assets = result.assets || {};
      this.scenes = (result.scenes || []).filter((scene) => (scene.available || []).length);
      this.source = result.source || "";
      this.renderList();
      this.renderLayers();
    }
    if (!this.structure) {
      // Licensed meshes for a whole region are the richer first sight; a
      // single card is what remains when the manifest names no scene.
      if (this.scenes.length) { await this.openScene(this.scenes[0].scene_id); return; }
      const first = this.firstStructureId();
      if (first) this.select(first);
    } else this.draw();
  },

  /* ── scenes: a region's licensed meshes as one view ─────────────── */

  kindOf(structureId) {
    for (const region of this.hierarchy) {
      for (const kind of region.kinds || []) {
        const hit = (kind.structures || []).find((item) => item.structure_id === structureId);
        if (hit) return { kind: kind.kind, canonical: hit.canonical, turkish: hit.turkish };
      }
    }
    return { kind: "", canonical: structureId, turkish: "" };
  },

  async openScene(sceneId) {
    const scene = this.scenes.find((item) => item.scene_id === sceneId);
    if (!scene) return;
    const ids = scene.available || [];
    this.scene = { scene_id: scene.scene_id, title: scene.title, items: [], total: ids.length, visible: new Set(LAB_LAYER_ORDER), bounds: null, attribution: "" };
    this.mesh = null;
    this.meshNotice = "";
    this.quiz = null;
    this.renderLayers();
    this.draw();
    for (const structureId of ids) {
      if (!this.scene || this.scene.scene_id !== scene.scene_id) return;   // the student moved on
      const result = await Medical.request("mesh", { structure_id: structureId });
      if (result.ok === false || !result.mesh || !result.mesh.positions) continue;
      const meta = this.kindOf(structureId);
      this.scene.items.push({ structure_id: structureId, kind: meta.kind, canonical: meta.canonical, mesh: result.mesh, buffers: null });
      if (!this.scene.attribution) this.scene.attribution = result.mesh.attribution || result.mesh.license || "";
      const notice = $("#lab-notice");
      if (notice) { notice.hidden = false; notice.textContent = `Sahne yükleniyor · ${this.scene.items.length}/${this.scene.total}`; }
    }
    if (!this.scene || this.scene.scene_id !== scene.scene_id) return;
    // Every BodyParts3D mesh shares one body frame, so the scene is placed
    // once, on the union of its bounds, and the meshes keep their relations.
    const bounds = { min: [Infinity, Infinity, Infinity], max: [-Infinity, -Infinity, -Infinity] };
    this.scene.items.forEach((item) => [0, 1, 2].forEach((axis) => {
      bounds.min[axis] = Math.min(bounds.min[axis], item.mesh.bounds.min[axis]);
      bounds.max[axis] = Math.max(bounds.max[axis], item.mesh.bounds.max[axis]);
    }));
    this.scene.bounds = bounds;
    this.scene.upAxis = this.scene.items.some((item) => String(item.mesh.up_axis || "").toLowerCase() === "z") ? "z" : "y";
    this.scene.items.forEach((item) => { item.buffers = null; });
    // The camera is framed for the limb before anything is drawn, whichever
    // card ends up selected below.
    this.resetCamera();
    if (!this.structure || !this.scene.items.some((item) => item.structure_id === this.structure.structure_id)) {
      const first = this.scene.items.find((item) => item.kind === "bone") || this.scene.items[0];
      if (first) { await this.select(first.structure_id); return; }
    }
    this.renderLayers();
    this.draw();
  },

  leaveScene() {
    const current = this.structure ? this.structure.structure_id : null;
    this.scene = null;
    this.renderLayers();
    if (current) this.select(current);
    else this.draw();
  },

  toggleLayer(kind) {
    if (!this.scene) return;
    if (this.scene.visible.has(kind)) this.scene.visible.delete(kind);
    else this.scene.visible.add(kind);
    this.renderLayers();
    this.draw();
  },

  renderLayers() {
    const host = $("#lab-layers");
    if (!host) return;
    if (!this.scenes.length) { host.innerHTML = ""; return; }
    const sceneChips = [`<button type="button" class="lab-layer ${this.scene ? "" : "active"}" data-scene="">Tek yapı</button>`]
      .concat(this.scenes.map((scene) => `<button type="button" class="lab-layer ${this.scene && this.scene.scene_id === scene.scene_id ? "active" : ""}" data-scene="${esc(scene.scene_id)}">${esc(scene.title)}</button>`));
    const parts = [`<span class="lab-layer-group">${sceneChips.join("")}</span>`];
    if (this.scene) {
      const present = new Set(this.scene.items.map((item) => item.kind));
      const layers = LAB_LAYER_ORDER.filter((kind) => present.has(kind)).map((kind) => {
        const colour = LAB_KIND_COLOURS[kind] || [0.6, 0.6, 0.6];
        const css = `rgb(${colour.map((value) => Math.round(value * 255)).join(",")})`;
        return `<button type="button" class="lab-layer ${this.scene.visible.has(kind) ? "active" : "off"}" data-layer="${kind}"><span class="lab-swatch" style="background:${css}"></span>${esc(LAB_KIND_LABELS[kind] || kind)}</button>`;
      });
      parts.push(`<span class="lab-layer-group">${layers.join("")}</span>`);
      parts.push(`<span class="lab-layer-note">${this.scene.items.length} yapı · sağ taraf · tıkla: kart</span>`);
    }
    host.innerHTML = parts.join("");
    $$("[data-scene]", host).forEach((node) => node.addEventListener("click", () => {
      if (node.dataset.scene) this.openScene(node.dataset.scene);
      else this.leaveScene();
    }));
    $$("[data-layer]", host).forEach((node) => node.addEventListener("click", () => this.toggleLayer(node.dataset.layer)));
  },

  firstStructureId() {
    for (const region of this.hierarchy) {
      for (const kind of region.kinds || []) {
        if ((kind.structures || []).length) return kind.structures[0].structure_id;
      }
    }
    return null;
  },

  renderList(filter) {
    const host = $("#lab-list");
    if (!host) return;
    const query = lower(filter || "");
    const parts = [];
    this.hierarchy.forEach((region) => {
      (region.kinds || []).forEach((kind) => {
        const items = (kind.structures || []).filter((item) =>
          !query || lower(item.canonical).includes(query) || lower(item.turkish).includes(query) || lower(item.english).includes(query));
        if (!items.length) return;
        parts.push(`<div class="lab-group-title">${esc(region.label)} · ${esc(kind.label)}</div>`);
        parts.push(items.map((item) => `<button type="button" class="lab-item ${this.structure && this.structure.structure_id === item.structure_id ? "active" : ""}" data-structure="${esc(item.structure_id)}">
          <span class="li-latin">${esc(item.canonical)}${item.has_model ? " ·" : ""}</span><span class="li-tr">${esc(item.turkish)}</span></button>`).join(""));
      });
    });
    host.innerHTML = parts.length ? parts.join("") : medEmpty("Eşleşen yapı yok");
    $$("[data-structure]", host).forEach((node) => node.addEventListener("click", () => this.select(node.dataset.structure)));
  },

  async select(structureId, { highlight = [], quiz = false } = {}) {
    const result = await Medical.request("structure", { structure_id: structureId });
    if (result.ok === false) { toast(result.error || "Yapı açılamadı.", true); return; }
    this.structure = result.structure;
    this.highlight = highlight;
    this.quiz = null;
    this.meshNotice = "";
    this.renderList($("#lab-search") ? $("#lab-search").value : "");
    this.renderInfo();
    if (this.scene && this.scene.items.some((item) => item.structure_id === structureId)) {
      // The region stays on screen; the chosen structure is lit within it.
      this.renderLayers();
      this.draw();
      if (quiz) this.startQuiz();
      return;
    }
    if (this.scene) { this.scene = null; this.renderLayers(); }
    this.mesh = null;
    if (this.structure.model && this.structure.model.available) {
      const mesh = await Medical.request("mesh", { structure_id: structureId });
      if (mesh.ok !== false && mesh.mesh && mesh.mesh.positions) this.mesh = mesh.mesh;
      // A registered model that cannot be read is a different fact from no model
      // at all, and only this reply knows which one happened.
      else this.meshNotice = (mesh && (mesh.reason || mesh.error)) || "";
    }
    this.resetCamera();
    this.draw();
    if (quiz) this.startQuiz();
  },

  renderInfo() {
    const host = $("#lab-info");
    if (!host) return;
    const structure = this.structure;
    if (!structure) { host.innerHTML = medEmpty("Yapı seç"); return; }
    if (this.bell && this.bell.current) {
      // The card lists every landmark with its description, which is the
      // answer sheet; during a bell-ringer only the specimen's name shows.
      host.innerHTML = `<h2>Zilli sınav</h2><div class="lab-tr">İstasyon ${this.bell.index + 1} / ${this.bell.stations.length}</div>
        <div class="lab-section"><span class="ls-title">Örnek</span><ul><li>${esc(structure.canonical)} · ${esc(structure.turkish)}</li></ul></div>
        <p class="settings-note">Numaralı pinin gösterdiği yapının Latince adını üstteki kutuya yaz; süre dolunca zil çalar.</p>`;
      return;
    }
    host.innerHTML = `
      <h2>${esc(structure.canonical)}</h2>
      <div class="lab-tr">${esc(structure.turkish)} · ${esc(structure.english)}</div>
      <div class="med-chips"><span class="chip accent">${esc(structure.kind_label)}</span><span class="chip">${esc(structure.region_label)}</span>
        ${structure.topic_path ? `<button type="button" class="chip" data-lab-topic="${esc(structure.topic_id)}">${esc(structure.topic_path)}</button>` : ""}</div>
      ${(structure.landmarks || []).length ? `<div class="lab-section"><span class="ls-title">İşaret noktaları</span>
        <div class="lab-landmarks">${structure.landmarks.map((landmark) => `<button type="button" class="lab-landmark ${this.highlight.includes(landmark.landmark_id) ? "active" : ""}" data-landmark="${esc(landmark.landmark_id)}">
          <span class="ll-latin">${esc(landmark.latin)}</span><span class="ll-tr">${esc(landmark.turkish)}${landmark.note ? " · " + esc(landmark.note) : ""}</span></button>`).join("")}</div></div>` : ""}
      ${(structure.sections || []).map((section) => `<div class="lab-section"><span class="ls-title">${esc(section.label)}</span>
        <ul>${section.items.map((item) => `<li>${esc(item)}</li>`).join("")}</ul></div>`).join("")}
      ${(structure.relations || []).length ? `<div class="lab-section"><span class="ls-title">İlişkiler</span>
        <div class="med-chips">${structure.relations.map((relation) => `<button type="button" class="chip" data-lab-go="${esc(relation.structure_id)}">${esc(relation.canonical)}</button>`).join("")}</div></div>` : ""}
      ${structure.source ? `<p class="settings-note">${esc(structure.source)}</p>` : ""}`;
    $$("[data-landmark]", host).forEach((node) => node.addEventListener("click", () => this.toggleHighlight(node.dataset.landmark)));
    $$("[data-lab-go]", host).forEach((node) => node.addEventListener("click", () => this.select(node.dataset.labGo)));
    $$("[data-lab-topic]", host).forEach((node) => node.addEventListener("click", () => { Medical.show("subjects"); Medical.openTopic(node.dataset.labTopic); }));
  },

  toggleHighlight(landmarkId) {
    this.highlight = this.highlight.includes(landmarkId)
      ? this.highlight.filter((item) => item !== landmarkId)
      : this.highlight.concat([landmarkId]);
    this.renderInfo();
    this.draw();
  },

  /* One step of the view: from the pad, the keys or a script. Pan scales
     with distance so a step moves the picture the same amount whatever the
     zoom; zoom is multiplicative so in and out are symmetric. */
  nudge(action) {
    const camera = this.camera;
    const pan = 0.08 * camera.distance;
    if (action === "left") camera.panX -= pan;
    else if (action === "right") camera.panX += pan;
    else if (action === "up") camera.panY += pan;
    else if (action === "down") camera.panY -= pan;
    else if (action === "in") camera.distance = clamp(camera.distance / 1.15, 0.8, 12);
    else if (action === "out") camera.distance = clamp(camera.distance * 1.15, 0.8, 12);
    else if (action === "rotl") camera.yaw -= 0.15;
    else if (action === "rotr") camera.yaw += 0.15;
    else if (action === "rotu") camera.pitch = clamp(camera.pitch - 0.12, -1.45, 1.45);
    else if (action === "rotd") camera.pitch = clamp(camera.pitch + 0.12, -1.45, 1.45);
    else if (action === "reset") this.resetCamera();
    else return;
    if (this.mesh || this.scene) { this.drawMesh(); this.drawLabels(); }
  },

  resetCamera() {
    // A whole limb is long and thin: it sits closer than a single bone would,
    // and it opens facing the student the way an atlas plate does.
    this.camera = this.scene
      ? { yaw: 0.35, pitch: 0.10, distance: 1.2, panX: 0, panY: 0 }
      : { yaw: 0.6, pitch: 0.25, distance: 2.6, panX: 0, panY: 0 };
  },

  /* ── drawing ───────────────────────────────────────────────────── */

  draw() {
    const canvas = $("#lab-canvas");
    const schematic = $("#lab-schematic");
    const notice = $("#lab-notice");
    if (!canvas || !schematic || !notice) return;
    const model = this.structure && this.structure.model;
    if (this.scene) {
      canvas.hidden = false;
      schematic.hidden = true;
      notice.hidden = false;
      const loading = this.scene.items.length < this.scene.total || !this.scene.bounds;
      notice.textContent = loading
        ? `Sahne yükleniyor · ${this.scene.items.length}/${this.scene.total}`
        : `3B sahne: ${this.scene.title} · ${this.scene.attribution || "lisanslı model"}`;
      if (!loading) { this.drawMesh(); this.drawLabels(); }
      return;
    }
    if (this.mesh) {
      canvas.hidden = false;
      schematic.hidden = true;
      notice.hidden = false;
      notice.textContent = `3B model: ${model.license || "lisans belirtilmemiş"} · ${model.source || ""}${model.attribution ? " · " + model.attribution : ""}`;
      this.drawMesh();
      this.drawLabels();
      return;
    }
    canvas.hidden = true;
    schematic.hidden = false;
    this.drawSchematic();
    notice.hidden = false;
    notice.textContent = this.meshNotice
      || (model && model.reason)
      || "Bu yapı için lisanslı 3B model kayıtlı değil; ilişki haritası gösteriliyor. Anatomik doğruluk gösterişten önce gelir.";
    const overlay = $("#lab-overlay");
    if (overlay) overlay.innerHTML = "";
  },

  drawSchematic() {
    const svg = $("#lab-schematic");
    const map = this.structure && this.structure.landmark_map;
    if (!svg || !map) return;
    const width = 900, height = 600, cx = width / 2, cy = height / 2;
    const nodes = map.nodes || [];
    const central = nodes.find((node) => node.central) || nodes[0];
    const landmarks = nodes.filter((node) => node.kind === "landmark");
    const others = nodes.filter((node) => !node.central && node.kind !== "landmark");
    const place = (list, radius, offset) => list.map((node, index) => {
      const angle = offset + (index / Math.max(1, list.length)) * Math.PI * 2;
      return { node, x: cx + Math.cos(angle) * radius, y: cy + Math.sin(angle) * radius * 0.72 };
    });
    const placed = [{ node: central, x: cx, y: cy }].concat(place(landmarks, 195, -Math.PI / 2)).concat(place(others, 300, -Math.PI / 2 + 0.25));
    const byId = {};
    placed.forEach((item) => { if (item.node) byId[item.node.id] = item; });
    const edges = (map.edges || []).map((edge) => {
      const from = byId[edge.from], to = byId[edge.to];
      if (!from || !to) return "";
      return `<path class="lab-edge" d="M${from.x.toFixed(1)} ${from.y.toFixed(1)} Q ${((from.x + to.x) / 2).toFixed(1)} ${((from.y + to.y) / 2 - 18).toFixed(1)} ${to.x.toFixed(1)} ${to.y.toFixed(1)}"/>`;
    }).join("");
    const shapes = placed.map((item) => {
      if (!item.node) return "";
      const node = item.node;
      const label = node.label.length > 26 ? node.label.slice(0, 25) + "…" : node.label;
      const w = Math.max(90, label.length * 6.6 + 18);
      const isHighlighted = this.highlight.includes(node.id);
      const cls = [node.central ? "central" : "", node.kind, isHighlighted ? "highlight" : ""].filter(Boolean).join(" ");
      return `<g data-node="${esc(node.id)}">
        <rect class="lab-node-shape ${cls}" x="${(item.x - w / 2).toFixed(1)}" y="${(item.y - 15).toFixed(1)}" width="${w.toFixed(1)}" height="30" rx="15"></rect>
        <text class="lab-node-text ${node.central ? "central" : ""}" x="${item.x.toFixed(1)}" y="${(item.y + 4).toFixed(1)}" text-anchor="middle">${esc(label)}</text>
      </g>`;
    }).join("");
    svg.setAttribute("viewBox", `0 0 ${width} ${height}`);
    svg.innerHTML = edges + shapes;
    $$("[data-node]", svg).forEach((node) => node.addEventListener("click", () => {
      const id = node.dataset.node;
      if ((this.structure.landmarks || []).some((landmark) => landmark.landmark_id === id)) this.toggleHighlight(id);
      else if (id !== this.structure.structure_id) this.select(id);
    }));
  },

  /* ── WebGL ─────────────────────────────────────────────────────── */

  ensureGL() {
    const canvas = $("#lab-canvas");
    if (!canvas) return null;
    if (this.gl && this.gl.canvas === canvas && !this.gl.isContextLost()) return this.gl;
    const gl = canvas.getContext("webgl", { antialias: true, alpha: true });
    if (!gl) return null;
    const compile = (type, source) => {
      const shader = gl.createShader(type);
      gl.shaderSource(shader, source);
      gl.compileShader(shader);
      return gl.getShaderParameter(shader, gl.COMPILE_STATUS) ? shader : null;
    };
    const vertex = compile(gl.VERTEX_SHADER, `
      attribute vec3 aPosition; attribute vec3 aNormal;
      uniform mat4 uProjection; uniform mat4 uView; uniform mat4 uModel;
      varying vec3 vNormal; varying vec3 vView;
      void main() {
        vec4 world = uModel * vec4(aPosition, 1.0);
        vec4 eye = uView * world;
        vNormal = mat3(uModel) * aNormal;
        vView = -eye.xyz;
        gl_Position = uProjection * eye;
      }`);
    const fragment = compile(gl.FRAGMENT_SHADER, `
      precision mediump float;
      varying vec3 vNormal; varying vec3 vView;
      uniform vec3 uColor; uniform vec3 uRim; uniform float uFlat;
      void main() {
        if (uFlat > 0.5) { gl_FragColor = vec4(uColor, 1.0); return; }
        vec3 n = normalize(vNormal);
        vec3 v = normalize(vView);
        vec3 key = normalize(vec3(0.4, 0.8, 0.6));
        float lambert = max(dot(n, key), 0.0);
        float fill = max(dot(n, normalize(vec3(-0.5, -0.2, 0.4))), 0.0) * 0.25;
        float rim = pow(1.0 - max(dot(n, v), 0.0), 2.5);
        vec3 colour = uColor * (0.22 + 0.78 * lambert + fill) + uRim * rim * 0.9;
        gl_FragColor = vec4(colour, 1.0);
      }`);
    if (!vertex || !fragment) return null;
    const program = gl.createProgram();
    gl.attachShader(program, vertex);
    gl.attachShader(program, fragment);
    gl.linkProgram(program);
    if (!gl.getProgramParameter(program, gl.LINK_STATUS)) return null;
    gl.enable(gl.DEPTH_TEST);
    this.gl = gl;
    this.program = program;
    this.locations = {
      position: gl.getAttribLocation(program, "aPosition"),
      normal: gl.getAttribLocation(program, "aNormal"),
      projection: gl.getUniformLocation(program, "uProjection"),
      view: gl.getUniformLocation(program, "uView"),
      model: gl.getUniformLocation(program, "uModel"),
      colour: gl.getUniformLocation(program, "uColor"),
      rim: gl.getUniformLocation(program, "uRim"),
      flat: gl.getUniformLocation(program, "uFlat"),
    };
    return gl;
  },

  buildBuffers() {
    return this.buildBuffersFor(this.mesh, this.mesh ? meshSpace(this.mesh) : null);
  },

  buildBuffersFor(mesh, space) {
    const gl = this.gl;
    if (!gl || !mesh || !space) return null;
    const positions = mesh.positions || [];
    const indices = mesh.indices || [];
    const normals = mesh.normals || [];
    const normalIndices = mesh.normal_indices || [];
    const outPositions = new Float32Array(indices.length * 3);
    const outNormals = new Float32Array(indices.length * 3);
    for (let triangle = 0; triangle < indices.length; triangle += 3) {
      const corners = [indices[triangle], indices[triangle + 1], indices[triangle + 2]];
      const points = corners.map((index) => space.place(positions, index));
      const edge1 = [0, 1, 2].map((axis) => points[1][axis] - points[0][axis]);
      const edge2 = [0, 1, 2].map((axis) => points[2][axis] - points[0][axis]);
      const face = [
        edge1[1] * edge2[2] - edge1[2] * edge2[1],
        edge1[2] * edge2[0] - edge1[0] * edge2[2],
        edge1[0] * edge2[1] - edge1[1] * edge2[0],
      ];
      const length = Math.hypot(face[0], face[1], face[2]) || 1;
      corners.forEach((index, corner) => {
        const base = (triangle + corner) * 3;
        outPositions[base] = points[corner][0];
        outPositions[base + 1] = points[corner][1];
        outPositions[base + 2] = points[corner][2];
        const normalIndex = normalIndices[triangle + corner];
        if (normals.length && normalIndex !== undefined && normalIndex >= 0) {
          outNormals[base] = normals[normalIndex * 3];
          outNormals[base + 1] = normals[normalIndex * 3 + 1];
          outNormals[base + 2] = normals[normalIndex * 3 + 2];
        } else {
          outNormals[base] = face[0] / length;
          outNormals[base + 1] = face[1] / length;
          outNormals[base + 2] = face[2] / length;
        }
      });
    }
    const make = (data) => {
      const buffer = gl.createBuffer();
      gl.bindBuffer(gl.ARRAY_BUFFER, buffer);
      gl.bufferData(gl.ARRAY_BUFFER, data, gl.STATIC_DRAW);
      return buffer;
    };
    return { position: make(outPositions), normal: make(outNormals), count: indices.length };
  },

  drawMesh() {
    const gl = this.ensureGL();
    const canvas = $("#lab-canvas");
    if (!gl || !canvas) {
      const notice = $("#lab-notice");
      if (notice) { notice.hidden = false; notice.textContent = "Bu ortamda WebGL kullanılamıyor; ilişki haritası gösteriliyor."; }
      this.mesh = null;
      this.draw();
      return;
    }
    if (this.scene) { this.drawScene(gl, canvas, { flat: false }); return; }
    if (!this.buffers || this.buffers.meshId !== this.structure.structure_id) {
      this.buffers = this.buildBuffers();
      if (this.buffers) this.buffers.meshId = this.structure.structure_id;
    }
    if (!this.buffers) return;
    const dpr = Math.min(devicePixelRatio || 1, 2);
    const rect = canvas.getBoundingClientRect();
    canvas.width = Math.max(1, Math.round(rect.width * dpr));
    canvas.height = Math.max(1, Math.round(rect.height * dpr));
    gl.viewport(0, 0, canvas.width, canvas.height);
    gl.clearColor(0, 0, 0, 0);
    gl.clear(gl.COLOR_BUFFER_BIT | gl.DEPTH_BUFFER_BIT);
    gl.useProgram(this.program);
    const aspect = canvas.width / Math.max(1, canvas.height);
    const projection = perspective(0.9, aspect, 0.05, 40);
    const view = lookAtView(this.camera);
    const model = identity();
    gl.uniformMatrix4fv(this.locations.projection, false, projection);
    gl.uniformMatrix4fv(this.locations.view, false, view);
    gl.uniformMatrix4fv(this.locations.model, false, model);
    const light = document.body.classList.contains("light");
    gl.uniform1f(this.locations.flat, 0);
    gl.uniform3fv(this.locations.colour, light ? [0.82, 0.80, 0.76] : [0.74, 0.72, 0.68]);
    gl.uniform3fv(this.locations.rim, light ? [0.20, 0.45, 0.55] : [0.35, 0.72, 0.95]);
    this.drawBuffers(gl, this.buffers);
  },

  drawBuffers(gl, buffers) {
    gl.bindBuffer(gl.ARRAY_BUFFER, buffers.position);
    gl.enableVertexAttribArray(this.locations.position);
    gl.vertexAttribPointer(this.locations.position, 3, gl.FLOAT, false, 0, 0);
    gl.bindBuffer(gl.ARRAY_BUFFER, buffers.normal);
    gl.enableVertexAttribArray(this.locations.normal);
    gl.vertexAttribPointer(this.locations.normal, 3, gl.FLOAT, false, 0, 0);
    gl.drawArrays(gl.TRIANGLES, 0, buffers.count);
  },

  drawScene(gl, canvas, { flat }) {
    const scene = this.scene;
    if (!scene || !scene.bounds) return;
    const space = meshSpace({ bounds: scene.bounds, up_axis: scene.upAxis });
    const dpr = Math.min(devicePixelRatio || 1, 2);
    const rect = canvas.getBoundingClientRect();
    canvas.width = Math.max(1, Math.round(rect.width * dpr));
    canvas.height = Math.max(1, Math.round(rect.height * dpr));
    gl.viewport(0, 0, canvas.width, canvas.height);
    gl.clearColor(0, 0, 0, 0);
    gl.clear(gl.COLOR_BUFFER_BIT | gl.DEPTH_BUFFER_BIT);
    gl.useProgram(this.program);
    const aspect = canvas.width / Math.max(1, canvas.height);
    gl.uniformMatrix4fv(this.locations.projection, false, perspective(0.9, aspect, 0.05, 40));
    gl.uniformMatrix4fv(this.locations.view, false, lookAtView(this.camera));
    gl.uniformMatrix4fv(this.locations.model, false, identity());
    gl.uniform1f(this.locations.flat, flat ? 1 : 0);
    const light = document.body.classList.contains("light");
    gl.uniform3fv(this.locations.rim, light ? [0.20, 0.45, 0.55] : [0.35, 0.72, 0.95]);
    const selected = this.structure ? this.structure.structure_id : null;
    scene.items.forEach((item, index) => {
      if (!scene.visible.has(item.kind)) return;
      if (!item.buffers) item.buffers = this.buildBuffersFor(item.mesh, space);
      if (!item.buffers) return;
      if (flat) {
        // The picking pass paints each mesh in its own id colour; index+1 keeps 0 for "nothing".
        const id = index + 1;
        gl.uniform3fv(this.locations.colour, [(id & 255) / 255, ((id >> 8) & 255) / 255, ((id >> 16) & 255) / 255]);
      } else {
        const base = LAB_KIND_COLOURS[item.kind] || [0.6, 0.6, 0.6];
        const lit = item.structure_id === selected ? base.map((value) => Math.min(1, value * 1.25 + 0.12)) : base;
        gl.uniform3fv(this.locations.colour, lit);
      }
      this.drawBuffers(gl, item.buffers);
    });
  },

  pickAt(clientX, clientY) {
    const gl = this.gl;
    const canvas = $("#lab-canvas");
    if (!gl || !canvas || !this.scene || !this.scene.bounds) return null;
    this.drawScene(gl, canvas, { flat: true });
    const rect = canvas.getBoundingClientRect();
    const x = Math.round((clientX - rect.left) * (canvas.width / Math.max(1, rect.width)));
    const y = Math.round((rect.bottom - clientY) * (canvas.height / Math.max(1, rect.height)));
    const pixel = new Uint8Array(4);
    gl.readPixels(x, y, 1, 1, gl.RGBA, gl.UNSIGNED_BYTE, pixel);
    this.drawScene(gl, canvas, { flat: false });
    const id = pixel[0] + (pixel[1] << 8) + (pixel[2] << 16);
    return id > 0 ? this.scene.items[id - 1] || null : null;
  },

  drawLabels() {
    const overlay = $("#lab-overlay");
    const canvas = $("#lab-canvas");
    const selectedItem = this.scene && this.structure ? this.scene.items.find((item) => item.structure_id === this.structure.structure_id) : null;
    const mesh = this.scene ? (selectedItem ? selectedItem.mesh : null) : this.mesh;
    if (!overlay || !canvas || !mesh) { if (overlay) overlay.innerHTML = ""; return; }
    if (!this.showLabels) { overlay.innerHTML = ""; return; }
    const anchors = mesh.landmarks || {};
    const rect = canvas.getBoundingClientRect();
    const projection = perspective(0.9, rect.width / Math.max(1, rect.height), 0.05, 40);
    const view = lookAtView(this.camera);
    // The anchor is written in the asset's own coordinates, exactly like
    // the vertices, so it has to travel through the same normalization as
    // the geometry (meshSpace) before it is projected. Projecting it raw
    // puts a Latin name over a part of the bone the mesh never claimed.
    const space = meshSpace(this.scene ? { bounds: this.scene.bounds, up_axis: this.scene.upAxis } : mesh);
    const meta = mesh.landmark_meta || {};
    const parts = [];
    const station = this.bell && this.bell.current ? this.bell.current : null;
    (this.structure.landmarks || []).forEach((landmark) => {
      const anchor = anchors[landmark.landmark_id];
      if (!anchor) return;
      const point = space.place(anchor);
      // A point the mesh's own bounds cannot contain is not a point on
      // this bone: draw nothing rather than a label we cannot justify.
      if (point.some((value) => Math.abs(value) > LAB_ANCHOR_LIMIT)) return;
      const projected = project(point, view, projection, rect.width, rect.height);
      if (!projected) return;
      const approx = (meta[landmark.landmark_id] || {}).confidence === "approximate";
      const at = `left:${projected.x.toFixed(1)}px; top:${projected.y.toFixed(1)}px`;
      if (station && station.structure_id === this.structure.structure_id) {
        // During a bell-ringer only the station's pin is on screen, unnamed:
        // the number is the question, the name is the answer.
        if (station.landmark_id === landmark.landmark_id) parts.push(`<span class="lab-pin station" style="${at}">${this.bell.index + 1}</span>`);
        return;
      }
      if (this.detailed) parts.push(`<span class="lab-pin ${approx ? "approx" : ""}" style="${at}"></span>`);
      parts.push(`<span class="lab-label ${this.detailed ? "detailed" : ""} ${approx ? "approx" : ""} ${this.highlight.includes(landmark.landmark_id) ? "active" : ""}" data-label="${esc(landmark.landmark_id)}"
        style="${at}" title="${approx ? "Yaklaşık: kemiğin geometrisinden türetildi" : ""}">${esc(landmark.latin)}</span>`);
    });
    overlay.innerHTML = parts.join("") + (this.pinCard ? this.pinCardMarkup() : "");
    $$("[data-label]", overlay).forEach((node) => node.addEventListener("click", (event) => {
      if (this.detailed) this.openPinCard(node.dataset.label, event.clientX, event.clientY);
      else this.toggleHighlight(node.dataset.label);
    }));
    $$("[data-pin-close]", overlay).forEach((node) => node.addEventListener("click", () => { this.pinCard = null; this.drawLabels(); }));
    $$("[data-pin-doc]", overlay).forEach((node) => node.addEventListener("click", () => {
      const [documentId, page] = node.dataset.pinDoc.split("|");
      Medical.show("library");
      Medical.openDocument(documentId).then(() => Medical.openPage(Number(page)));
    }));
  },

  /* ── detailed mode: a pin's name, its note and where the lecture mentions it ── */

  toggleDetailed() {
    this.detailed = !this.detailed;
    this.pinCard = null;
    const button = $("#lab-detailed");
    if (button) button.classList.toggle("active", this.detailed);
    this.draw();
  },

  async openPinCard(landmarkId, clientX, clientY) {
    const landmark = (this.structure.landmarks || []).find((item) => item.landmark_id === landmarkId);
    if (!landmark) return;
    const canvas = $("#lab-canvas");
    const rect = canvas ? canvas.getBoundingClientRect() : { left: 0, top: 0, width: 600, height: 400 };
    const x = clamp(clientX - rect.left + 12, 0, Math.max(0, rect.width - 330));
    const y = clamp(clientY - rect.top + 12, 0, Math.max(0, rect.height - 160));
    this.pinCard = { landmark, x, y, docs: null };
    this.highlight = [landmarkId];
    this.drawLabels();
    // The lecture pages that name this landmark, from the student's own library.
    const result = await Medical.request("search", { query: landmark.latin, limit: 6 });
    if (!this.pinCard || this.pinCard.landmark.landmark_id !== landmarkId) return;
    this.pinCard.docs = (result.ok === false ? [] : (result.hits || [])).filter((hit) => hit.kind === "chunk").slice(0, 4);
    this.drawLabels();
  },

  pinCardMarkup() {
    const card = this.pinCard;
    if (!card) return "";
    const mesh = this.scene ? (this.scene.items.find((item) => item.structure_id === this.structure.structure_id) || {}).mesh : this.mesh;
    const meta = ((mesh && mesh.landmark_meta) || {})[card.landmark.landmark_id] || {};
    const docs = card.docs === null
      ? `<div class="lab-pin-note">Ders notlarında aranıyor…</div>`
      : card.docs.length
        ? `<div class="lab-pin-docs">${card.docs.map((hit) => `<button type="button" class="lab-pin-doc" data-pin-doc="${esc(hit.document_id + "|" + hit.page_number)}">${esc(hit.title)} · s. ${hit.page_number}</button>`).join("")}</div>`
        : `<div class="lab-pin-note">Kütüphanendeki ders notlarında bu ad geçmiyor.</div>`;
    return `<div class="lab-pin-card" style="left:${card.x.toFixed(0)}px; top:${card.y.toFixed(0)}px">
      <h4>${esc(card.landmark.latin)}</h4>
      <div>${esc(card.landmark.turkish)}${card.landmark.note ? " · " + esc(card.landmark.note) : ""}</div>
      ${meta.confidence === "approximate" ? `<div class="lab-pin-note">≈ Pin konumu kemiğin geometrisinden yaklaşık türetildi; atlasta doğrula.</div>` : ""}
      ${docs}
      <div class="btn-row" style="justify-content:flex-end;margin-top:6px"><button type="button" class="btn btn-ghost small" data-pin-close>Kapat</button></div>
    </div>`;
  },

  /* ── bell-ringer: numbered stations, a pin each, a bell between them ── */

  bellStations() {
    const stations = [];
    const items = this.scene ? this.scene.items : (this.mesh && this.structure ? [{ structure_id: this.structure.structure_id, mesh: this.mesh, canonical: this.structure.canonical }] : []);
    items.forEach((item) => {
      const anchors = item.mesh.landmarks || {};
      Object.keys(anchors).forEach((landmarkId) => stations.push({ structure_id: item.structure_id, landmark_id: landmarkId }));
    });
    return stations;
  },

  startBellRinger() {
    const pool = this.bellStations();
    if (!pool.length) { toast("Zilli sınav için pinli bir model gerekli; sahneyi ya da pinli bir kemiği aç.", true); return; }
    // The rules of the real thing, said once: numbered stations, one pinned
    // structure each, a bell between them, no going back.
    const host = $("#lab-info");
    if (!host) return;
    this.quiz = null;
    this.pinCard = null;
    host.innerHTML = `<h2>Zilli sınav</h2>
      <div class="lab-tr">${pool.length} pin · ${Math.min(10, pool.length)} istasyon</div>
      <div class="lab-section"><span class="ls-title">Kurallar</span>
        <ul><li>Her istasyonda numaralı bir pin görürsün; yapının Latince adını yaz.</li>
        <li>Süre dolunca zil çalar ve sonraki istasyona geçilir; geri dönüş yok.</li>
        <li>Sonunda her istasyonun doğru adı ve açıklaması gösterilir; sonuçlar ustalık geçmişine işlenir.</li></ul></div>
      <div class="lab-section"><span class="ls-title">İstasyon süresi</span>
        <div class="med-chips">${[30, 45, 60, 90].map((seconds) => `<button type="button" class="chip ${seconds === 60 ? "accent" : ""}" data-bell-start="${seconds}">${seconds} sn</button>`).join("")}</div></div>
      <div class="btn-row" style="justify-content:flex-start"><button type="button" class="btn btn-ghost small" data-bell-start="">Vazgeç</button></div>`;
    $$("[data-bell-start]", host).forEach((node) => node.addEventListener("click", () => {
      const seconds = Number(node.dataset.bellStart || 0);
      if (seconds) this.beginBellRinger(pool, seconds);
      else { this.renderInfo(); this.draw(); }
    }));
  },

  beginBellRinger(pool, seconds) {
    const shuffled = pool.slice();
    for (let index = shuffled.length - 1; index > 0; index -= 1) {
      const swapAt = Math.floor(Math.random() * (index + 1));
      [shuffled[index], shuffled[swapAt]] = [shuffled[swapAt], shuffled[index]];
    }
    const count = Math.min(10, shuffled.length);
    this.bell = { stations: shuffled.slice(0, count), index: -1, seconds: clamp(seconds, 15, 300), answers: [], current: null, timer: null, remaining: 0, pool };
    this.nextStation();
  },

  ring() {
    try {
      const context = new (window.AudioContext || window.webkitAudioContext)();
      const oscillator = context.createOscillator();
      const gain = context.createGain();
      oscillator.type = "sine";
      oscillator.frequency.value = 880;
      gain.gain.setValueAtTime(0.0001, context.currentTime);
      gain.gain.exponentialRampToValueAtTime(0.25, context.currentTime + 0.02);
      gain.gain.exponentialRampToValueAtTime(0.0001, context.currentTime + 0.6);
      oscillator.connect(gain).connect(context.destination);
      oscillator.start();
      oscillator.stop(context.currentTime + 0.65);
    } catch (error) { /* no audio output: the strip still changes, the timer still runs */ }
  },

  async nextStation() {
    const bell = this.bell;
    if (!bell) return;
    clearInterval(bell.timer);
    bell.index += 1;
    if (bell.index >= bell.stations.length) { this.finishBellRinger(); return; }
    bell.current = bell.stations[bell.index];
    bell.remaining = bell.seconds;
    this.ring();
    if (!this.structure || this.structure.structure_id !== bell.current.structure_id) await this.select(bell.current.structure_id);
    this.highlight = [];
    this.renderInfo();
    this.renderBellStrip();
    this.draw();
    bell.timer = setInterval(() => {
      bell.remaining -= 1;
      const timer = $("#lab-bell-timer");
      if (timer) { timer.textContent = `${bell.remaining} sn`; timer.classList.toggle("low", bell.remaining <= 10); }
      if (bell.remaining <= 0) this.answerStation("", { timedOut: true });
    }, 1000);
  },

  renderBellStrip() {
    const viewport = $("#lab-viewport");
    if (!viewport) return;
    const strip = $("#lab-bell-strip");
    if (!strip) return;
    strip.hidden = false;
    const bell = this.bell;
    strip.innerHTML = `<span class="chip warn">İstasyon ${bell.index + 1}/${bell.stations.length}</span>
      <span id="lab-bell-timer" class="lab-bell-timer">${bell.remaining} sn</span>
      <input id="lab-bell-answer" type="text" placeholder="Pinli yapının Latince adı…" autocomplete="off" spellcheck="false">
      <button type="button" class="btn btn-primary small" data-bell="answer">Cevapla</button>
      <button type="button" class="btn btn-ghost small" data-bell="skip">Geç</button>
      <button type="button" class="btn btn-ghost small" data-bell="stop">Bitir</button>`;
    const input = $("#lab-bell-answer");
    if (input) {
      input.addEventListener("keydown", (event) => { if (event.key === "Enter") this.answerStation(input.value); });
      // A plain focus() scrolls the whole shell to the input for a frame.
      input.focus({ preventScroll: true });
    }
    $$("[data-bell]", strip).forEach((node) => node.addEventListener("click", () => {
      if (node.dataset.bell === "answer") this.answerStation(($("#lab-bell-answer") || {}).value || "");
      else if (node.dataset.bell === "skip") this.answerStation("", { skipped: true });
      else this.finishBellRinger();
    }));
  },

  async answerStation(text, { timedOut = false, skipped = false } = {}) {
    const bell = this.bell;
    if (!bell || !bell.current) return;
    clearInterval(bell.timer);
    const station = bell.current;
    const landmark = (this.structure.landmarks || []).find((item) => item.landmark_id === station.landmark_id) || { latin: station.landmark_id, turkish: "" };
    const correct = !timedOut && !skipped && latinMatches(text, landmark.latin);
    bell.answers.push({ station, given: text, latin: landmark.latin, turkish: landmark.turkish, correct, timedOut, skipped, structure: this.structure.canonical });
    await Medical.request("anatomy_answer", { structure_id: station.structure_id, landmark_id: station.landmark_id, correct });
    bell.current = null;
    this.nextStation();
  },

  finishBellRinger() {
    const bell = this.bell;
    if (!bell) return;
    clearInterval(bell.timer);
    const strip = $("#lab-bell-strip");
    if (strip) { strip.hidden = true; strip.innerHTML = ""; }
    this.bell = null;
    this.draw();
    const host = $("#lab-info");
    if (!host) return;
    const right = bell.answers.filter((item) => item.correct).length;
    host.innerHTML = `<h2>Zilli sınav bitti</h2><div class="lab-tr">${bell.answers.length} istasyon · ${right} doğru</div>
      <div class="lab-bell-results">${bell.answers.map((item, index) => `<div class="lab-bell-row ${item.correct ? "ok" : "bad"}">
        <span>${index + 1}</span><span><b>${esc(item.latin)}</b> · ${esc(item.structure)}${item.turkish ? "<br>" + esc(item.turkish) : ""}
        <br><span class="faint">${item.correct ? "Doğru" : item.timedOut ? "Süre doldu" : item.skipped ? "Geçildi" : "Senin cevabın: " + esc(item.given || "—")}</span></span></div>`).join("")}</div>
      <div class="btn-row" style="justify-content:flex-start;margin-top:8px"><button type="button" class="btn btn-ghost small" data-bell-done="again">Yeniden</button>
      <button type="button" class="btn btn-ghost small" data-bell-done="close">Karta dön</button></div>`;
    $$("[data-bell-done]", host).forEach((node) => node.addEventListener("click", () => {
      if (node.dataset.bellDone === "again") this.beginBellRinger(bell.pool || this.bellStations(), bell.seconds);
      else { this.renderInfo(); this.draw(); }
    }));
  },

  /* ── quiz ──────────────────────────────────────────────────────── */

  async startQuiz() {
    if (!this.structure) return;
    const result = await Medical.request("anatomy_quiz", { structure_id: this.structure.structure_id, count: 5 });
    if (result.ok === false) { toast(result.error || "Quiz hazırlanamadı.", true); return; }
    const questions = result.questions || [];
    if (!questions.length) { toast("Bu yapı için quiz üretilemedi.", true); return; }
    this.quiz = { questions, index: 0, correct: 0 };
    this.renderQuiz();
  },

  renderQuiz() {
    const host = $("#lab-info");
    if (!host || !this.quiz) return;
    const quiz = this.quiz;
    if (quiz.index >= quiz.questions.length) {
      host.innerHTML = `<h2>Quiz bitti</h2><div class="med-evidence">${quiz.questions.length} sorudan ${quiz.correct} doğru.</div>
        <div class="btn-row" style="justify-content:flex-start"><button type="button" class="btn btn-ghost small" data-quiz="again">Tekrar</button>
        <button type="button" class="btn btn-ghost small" data-quiz="close">Karta dön</button></div>`;
    } else {
      const question = quiz.questions[quiz.index];
      this.highlight = question.landmark_id ? [question.landmark_id] : [];
      this.draw();
      host.innerHTML = `<h2>Quiz</h2><div class="lab-tr">${quiz.index + 1} / ${quiz.questions.length}</div>
        <div class="mq-stem" style="font-size:var(--text-md)">${esc(question.stem)}</div>
        <div class="med-options">${question.options.map((option) => `<button type="button" class="med-option" data-quiz-option="${esc(option.key)}">
          <span class="mo-key">${esc(option.key)}</span><span>${esc(option.text)}</span></button>`).join("")}</div>
        <div class="btn-row" style="justify-content:flex-start"><button type="button" class="btn btn-ghost small" data-quiz="close">Quizden çık</button></div>`;
    }
    $$("[data-quiz-option]", host).forEach((node) => node.addEventListener("click", () => this.answerQuiz(node.dataset.quizOption)));
    $$("[data-quiz]", host).forEach((node) => node.addEventListener("click", () => {
      if (node.dataset.quiz === "again") this.startQuiz();
      else { this.quiz = null; this.highlight = []; this.renderInfo(); this.draw(); }
    }));
  },

  async answerQuiz(key) {
    const quiz = this.quiz;
    if (!quiz) return;
    const question = quiz.questions[quiz.index];
    const correct = key === question.correct_key;
    if (correct) quiz.correct += 1;
    toast(correct ? `Doğru — ${question.explanation}` : `Yanlış. Doğru cevap ${question.correct_key}) — ${question.explanation}`, correct ? "ok" : true);
    await Medical.request("anatomy_answer", {
      structure_id: question.structure_id,
      landmark_id: question.landmark_id || "",
      correct,
    });
    quiz.index += 1;
    this.renderQuiz();
  },

  /* ── interaction ───────────────────────────────────────────────── */

  bind() {
    const canvas = $("#lab-canvas");
    if (!canvas) return;
    canvas.addEventListener("pointerdown", (event) => {
      this.dragging = { x: event.clientX, y: event.clientY, pan: event.shiftKey || event.button === 1 || event.button === 2, startX: event.clientX, startY: event.clientY, moved: 0 };
      canvas.classList.add("dragging");
      canvas.setPointerCapture(event.pointerId);
      if (typeof canvas.focus === "function") canvas.focus({ preventScroll: true });
    });
    canvas.addEventListener("contextmenu", (event) => event.preventDefault());
    canvas.addEventListener("keydown", (event) => {
      const step = { ArrowLeft: "left", ArrowRight: "right", ArrowUp: "up", ArrowDown: "down", "+": "in", "=": "in", "-": "out", "_": "out", r: "reset", R: "reset" }[event.key];
      if (!step) return;
      event.preventDefault();
      const rotating = event.shiftKey && (step === "left" || step === "right" || step === "up" || step === "down");
      this.nudge(rotating ? { left: "rotl", right: "rotr", up: "rotu", down: "rotd" }[step] : step);
    });
    $$("[data-nav]", $("#lab-nav") || document).forEach((node) => node.addEventListener("click", () => this.nudge(node.dataset.nav)));
    const detailed = $("#lab-detailed");
    if (detailed) detailed.addEventListener("click", () => this.toggleDetailed());
    const bellButton = $("#lab-bell");
    if (bellButton) bellButton.addEventListener("click", () => this.startBellRinger());
    canvas.addEventListener("pointermove", (event) => {
      if (!this.dragging) return;
      const dx = event.clientX - this.dragging.x;
      const dy = event.clientY - this.dragging.y;
      this.dragging.x = event.clientX;
      this.dragging.y = event.clientY;
      this.dragging.moved += Math.abs(dx) + Math.abs(dy);
      if (this.dragging.pan) {
        this.camera.panX += dx * 0.003 * this.camera.distance;
        this.camera.panY -= dy * 0.003 * this.camera.distance;
      } else {
        this.camera.yaw += dx * 0.008;
        this.camera.pitch = clamp(this.camera.pitch + dy * 0.008, -1.45, 1.45);
      }
      this.drawMesh();
      this.drawLabels();
    });
    const release = (event) => {
      const drag = this.dragging;
      this.dragging = null;
      canvas.classList.remove("dragging");
      if (drag && drag.moved < 4 && event && event.type === "pointerup" && this.scene) {
        const hit = this.pickAt(event.clientX, event.clientY);
        if (hit && (!this.structure || hit.structure_id !== this.structure.structure_id)) this.select(hit.structure_id);
      }
      if (event && event.pointerId !== undefined && canvas.hasPointerCapture && canvas.hasPointerCapture(event.pointerId)) {
        canvas.releasePointerCapture(event.pointerId);
      }
    };
    canvas.addEventListener("pointerup", release);
    canvas.addEventListener("pointercancel", release);
    canvas.addEventListener("wheel", (event) => {
      event.preventDefault();
      this.camera.distance = clamp(this.camera.distance * (1 + Math.sign(event.deltaY) * 0.12), 0.8, 12);
      this.drawMesh();
      this.drawLabels();
    }, { passive: false });
  },
};

/* ── tiny matrix helpers (no dependencies, column-major like WebGL) ── */

/* The one transform between an asset's own coordinates and what the
   viewer draws: the mesh is centred on its bounds and scaled so its
   longest axis is 1, and the shader runs with uModel = identity. Both
   the geometry (buildBuffers) and the landmark labels (drawLabels) go
   through this single function, so the two spaces cannot drift apart.

   The manifest's `scale` is deliberately not applied: it multiplies
   vertices and landmark anchors alike, and a uniform factor cancels out
   of (p - centre) / extent, so honouring it would move nothing. */
/* Does a typed answer name the pinned structure? Diacritics, case, the
   abbreviations students write ("m.", "n.", "a.", "v.") and the small
   Latin function words are ignored; every remaining word of the target
   must appear in the answer, so "tuberculum majus" is not "tuberculum
   minus" and "humeri" alone is not a landmark. */
function latinMatches(answer, latin) {
  const fold = (text) => String(text || "").toLowerCase()
    .replace(/[çÇ]/g, "c").replace(/[ğĞ]/g, "g").replace(/[ıİ]/g, "i").replace(/[öÖ]/g, "o").replace(/[şŞ]/g, "s").replace(/[üÜ]/g, "u")
    .normalize("NFD").replace(/[\u0300-\u036f]/g, "")
    .replace(/\b(musculus|nervus|arteria|vena|m|n|a|v)\.?\b/g, " ")
    .replace(/[^a-z0-9 ]+/g, " ").split(/\s+/).filter(Boolean);
  const wanted = fold(latin).filter((word) => !["et", "de", "ad"].includes(word));
  const given = new Set(fold(answer));
  if (!wanted.length || !given.size) return false;
  return wanted.every((word) => given.has(word) || [...given].some((token) => token.length >= 5 && (word.startsWith(token) || token.startsWith(word))));
}

function meshSpace(mesh) {
  const bounds = (mesh && mesh.bounds) || { min: [-1, -1, -1], max: [1, 1, 1] };
  const centre = [0, 1, 2].map((axis) => (bounds.min[axis] + bounds.max[axis]) / 2);
  const extent = Math.max(...[0, 1, 2].map((axis) => bounds.max[axis] - bounds.min[axis])) || 1;
  // An asset written z-up (BodyParts3D) is turned once, here, so vertices,
  // bounds and landmark anchors all arrive in the viewer's y-up frame together.
  const zUp = !!(mesh && String(mesh.up_axis || "").toLowerCase() === "z");
  return {
    centre,
    extent,
    zUp,
    // One point of `source` (a flat [x,y,z,…] array) in model space.
    place(source, index = 0) {
      const base = index * 3;
      const x = (source[base] - centre[0]) / extent;
      const y = (source[base + 1] - centre[1]) / extent;
      const z = (source[base + 2] - centre[2]) / extent;
      return zUp ? [x, z, -y] : [x, y, z];
    },
  };
}

/* Model space puts every vertex within half the longest axis of the
   origin, so |0.5| is the whole bone; past 1.5× that a landmark anchor
   is written in some other space and cannot be placed on this mesh. */
const LAB_ANCHOR_LIMIT = 0.75;

function identity() {
  return new Float32Array([1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1]);
}

function perspective(fov, aspect, near, far) {
  const f = 1 / Math.tan(fov / 2);
  const range = 1 / (near - far);
  return new Float32Array([
    f / aspect, 0, 0, 0,
    0, f, 0, 0,
    0, 0, (near + far) * range, -1,
    0, 0, near * far * range * 2, 0,
  ]);
}

function lookAtView(camera) {
  const cp = Math.cos(camera.pitch), sp = Math.sin(camera.pitch);
  const cy = Math.cos(camera.yaw), sy = Math.sin(camera.yaw);
  const eye = [
    camera.distance * cp * sy + camera.panX,
    camera.distance * sp + camera.panY,
    camera.distance * cp * cy,
  ];
  const target = [camera.panX, camera.panY, 0];
  const forward = normalize([target[0] - eye[0], target[1] - eye[1], target[2] - eye[2]]);
  const right = normalize(cross(forward, [0, 1, 0]));
  const up = cross(right, forward);
  return new Float32Array([
    right[0], up[0], -forward[0], 0,
    right[1], up[1], -forward[1], 0,
    right[2], up[2], -forward[2], 0,
    -dot(right, eye), -dot(up, eye), dot(forward, eye), 1,
  ]);
}

function project(point, view, projection, width, height) {
  const apply = (matrix, vector) => [0, 1, 2, 3].map((row) =>
    matrix[row] * vector[0] + matrix[4 + row] * vector[1] + matrix[8 + row] * vector[2] + matrix[12 + row] * vector[3]);
  const eye = apply(view, [point[0], point[1], point[2], 1]);
  const clip = apply(projection, eye);
  if (!clip[3] || clip[3] <= 0) return null;
  return { x: ((clip[0] / clip[3]) * 0.5 + 0.5) * width, y: (0.5 - (clip[1] / clip[3]) * 0.5) * height };
}

function cross(a, b) { return [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]]; }
function dot(a, b) { return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]; }
function normalize(v) { const length = Math.hypot(v[0], v[1], v[2]) || 1; return [v[0] / length, v[1] / length, v[2] / length]; }

/* ── wiring ───────────────────────────────────────────────────────── */

function bindMedical() {
  Medical.buildTabs();
  const search = $("#med-topic-search");
  if (search) search.addEventListener("input", () => Medical.renderTree(search.value));
  const importBtn = $("#med-import");
  if (importBtn) importBtn.addEventListener("click", () => Medical.importDocument());
  const docRefresh = $("#med-doc-refresh");
  if (docRefresh) { docRefresh.innerHTML = icon("refresh"); docRefresh.addEventListener("click", () => Medical.loadDocuments()); }
  const noteForm = $("#med-note-form");
  if (noteForm) noteForm.addEventListener("submit", (event) => Medical.createNote(event));
  const examForm = $("#med-exam-form");
  if (examForm) examForm.addEventListener("submit", (event) => Medical.createExam(event, false));
  const examBank = $("#med-exam-bank");
  if (examBank) examBank.addEventListener("click", () => Medical.createExam(null, true));
  const bankRefresh = $("#med-bank-refresh");
  if (bankRefresh) { bankRefresh.innerHTML = icon("refresh"); bankRefresh.addEventListener("click", () => Medical.loadBank()); }
  ["med-bank-subject", "med-bank-origin", "med-bank-answered"].forEach((id) => {
    const node = $("#" + id);
    if (node) node.addEventListener("change", () => Medical.loadBank());
  });
  const bankSearch = $("#med-bank-search");
  if (bankSearch) {
    let timer = 0;
    bankSearch.addEventListener("input", () => { clearTimeout(timer); timer = setTimeout(() => Medical.loadBank(), 260); });
  }
  const profForm = $("#med-prof-form");
  if (profForm) profForm.addEventListener("submit", (event) => Medical.addProfessor(event));
  const labSearch = $("#lab-search");
  if (labSearch) labSearch.addEventListener("input", () => Lab.renderList(labSearch.value));
  const labels = $("#lab-labels");
  if (labels) labels.addEventListener("click", () => { Lab.showLabels = !Lab.showLabels; labels.classList.toggle("active", Lab.showLabels); Lab.drawLabels(); });
  const quiz = $("#lab-quiz");
  if (quiz) quiz.addEventListener("click", () => Lab.startQuiz());
  const movement = $("#lab-movement");
  if (movement) movement.addEventListener("click", () => {
    const structure = Lab.structure;
    if (!structure) return;
    const movements = structure.movements || [];
    if (!movements.length) { toast("Bu yapı için hareket verisi yok.", true); return; }
    const host = $("#lab-info");
    host.innerHTML = `<h2>${esc(structure.canonical)} · hareketler</h2>` + movements.map((item) => `<div class="lab-section">
      <span class="ls-title">${esc((item.pair || []).join(" ↔ ") || "Hareket")}</span>
      <ul><li>${esc(item.text)}</li>${item.axis ? `<li>Eksen: ${esc(item.axis)}</li>` : ""}${item.plane ? `<li>Düzlem: ${esc(item.plane)}</li>` : ""}
      ${(item.muscles || []).map((muscle) => `<li>${esc(muscle)}</li>`).join("")}</ul></div>`).join("") +
      `<div class="btn-row" style="justify-content:flex-start"><button type="button" class="btn btn-ghost small" id="lab-movement-back">Karta dön</button></div>`;
    const back = $("#lab-movement-back");
    if (back) back.addEventListener("click", () => Lab.renderInfo());
  });
  const reset = $("#lab-reset");
  if (reset) reset.addEventListener("click", () => { Lab.resetCamera(); Lab.draw(); });
  const teach = $("#lab-teach");
  if (teach) teach.addEventListener("click", () => {
    if (!Lab.structure) return;
    Medical.quickAsk(`${Lab.structure.canonical} yapısını anlat`);
  });
  Lab.bind();
}
