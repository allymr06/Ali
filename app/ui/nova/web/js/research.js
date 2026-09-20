/* ════════════════════════════════════════════════════════════════════
   JARVIS · NOVA — Araştırma: a room of its own
   Research opens like the academy does: the rail and the top bar step
   aside, a constellation of sources lights up, and the workspace is
   revealed. The page picks where to look - the open web, YouTube, GitHub,
   Wikipedia, PubMed, arXiv, Stack Overflow, Hacker News, one named site -
   and the report that comes back is drawn as cards by kind, findings with
   their citations, and the uncertainties the service admitted. Nothing on
   this screen is invented: every card is a source the core returned, every
   figure on it came from that source's own endpoint.
   ════════════════════════════════════════════════════════════════════ */
"use strict";

const RESEARCH_INTRO = { title: 1500, hold: 2700, fade: 420, skipFade: 180 };
const RESEARCH_INTRO_ORDER = ["web", "wikipedia", "youtube", "github", "pubmed", "arxiv", "stackoverflow", "hackernews"];
const RESEARCH_MAX_SOURCES = 10;

/* What each kind of source is called and drawn with. */
const RESEARCH_KINDS = {
  web: ["globe", "Web sayfası"],
  video: ["play", "Video"],
  repo: ["repo", "Depo"],
  paper: ["paper", "Makale"],
  discussion: ["discuss", "Tartışma"],
  encyclopedia: ["book", "Ansiklopedi"],
};

/* A preset is a starting selection, not a rule: the chips stay editable. */
const RESEARCH_PRESETS = [
  ["general", "Genel", ["web", "wikipedia", "youtube", "github"]],
  ["code", "Kod", ["github", "stackoverflow", "hackernews", "web"]],
  ["science", "Bilim", ["pubmed", "arxiv", "wikipedia", "web"]],
  ["video", "Video", ["youtube"]],
  ["all", "Hepsi", null],
];

/* The service writes its uncertainties as English machine strings. The
   known ones read in Turkish; an unknown one is shown as it came, never
   invented. */
const RESEARCH_UNCERTAINTY_TR = [
  [/^Source (.+) was unavailable \((.+?)\)\.$/, (m) => `${m[1]} kaynağına ulaşılamadı (${m[2]}).`],
  [/^(\d+) candidate source\(s\) could not be safely collected\.$/, (m) => `${m[1]} aday kaynak güvenle toplanamadı.`],
  [/^No eligible source content was collected\.$/, () => "Kullanılabilir kaynak içeriği toplanamadı."],
  [/^The publication dates of all collected sources are unknown\.$/, () => "Toplanan kaynakların yayın tarihleri bilinmiyor."],
  [/^The evidence was not corroborated across independent domains\.$/, () => "Kanıt bağımsız alan adlarında doğrulanmadı."],
  [/^Live research was unavailable; cached evidence was returned and may be outdated \((\w+)\)\.$/,
    (m) => `Canlı araştırma yapılamadı; önbellekteki kanıt döndü, güncel olmayabilir (${m[1]}).`],
  [/^Live research returned no usable sources; cached evidence was returned and may be outdated\.$/,
    () => "Canlı araştırma kullanılabilir kaynak bulamadı; önbellekteki kanıt döndü, güncel olmayabilir."],
];

function researchUncertaintyTr(text) {
  const value = String(text == null ? "" : text).trim();
  if (!value) return "";
  for (const [pattern, render] of RESEARCH_UNCERTAINTY_TR) {
    const match = pattern.exec(value);
    if (match) return render(match);
  }
  return value;
}

const RESEARCH_FRESHNESS_TR = { current: "güncel", aging: "eskiyor", stale: "eski", unknown: "tarih bilinmiyor" };

function researchPreset(id, catalogue) {
  const preset = RESEARCH_PRESETS.find(([key]) => key === id) || RESEARCH_PRESETS[0];
  const available = (catalogue || []).map((item) => item.id).filter((item) => item !== "site");
  const wanted = preset[2] === null ? available : preset[2];
  return wanted.filter((item) => available.includes(item));
}

/* The facts a card shows under its title, by what the source is. */
function researchMetaChips(source) {
  const meta = source.meta || {};
  const chips = [];
  const push = (text, cls = "") => { if (text) chips.push(`<span class="res-chip ${cls}">${esc(text)}</span>`); };
  if (source.kind === "repo") {
    push(meta.stars ? `★ ${meta.stars}` : "");
    push(meta.language);
    push(meta.updated ? `güncelleme ${meta.updated}` : "");
  } else if (source.kind === "video") {
    push(meta.channel);
    if (meta.confirmed === "no") push("kanal doğrulanamadı", "warn");
  } else if (source.kind === "paper") {
    push(meta.journal);
    push(meta.year);
    push(meta.authors);
  } else if (source.kind === "discussion") {
    push(meta.points ? `${meta.points} puan` : (meta.score ? `puan ${meta.score}` : ""));
    push(meta.comments ? `${meta.comments} yorum` : (meta.answers ? `${meta.answers} cevap` : ""));
    if (meta.answered === "yes") push("kabul edilmiş cevap", "ok");
    push(meta.tags);
  } else if (source.kind === "encyclopedia") {
    push(meta.language ? `${meta.language}.wikipedia` : "");
  } else if (meta.site) {
    push(meta.site);
  }
  return chips.join("");
}

function researchSourceMarkup(source, catalogue) {
  const [iconName, kindLabel] = RESEARCH_KINDS[source.kind] || RESEARCH_KINDS.web;
  const label = ((catalogue || []).find((item) => item.id === source.source) || {}).label || "";
  const freshness = RESEARCH_FRESHNESS_TR[source.freshness] || RESEARCH_FRESHNESS_TR.unknown;
  const findings = Array.isArray(source.prompt_injection_findings) ? source.prompt_injection_findings.length : 0;
  let host = "";
  try { host = new URL(source.url).hostname.replace(/^www\./, ""); } catch (_error) { host = ""; }
  return `<article class="res-card kind-${esc(source.kind || "web")}">
    <div class="res-card-head">
      <span class="res-kind">${icon(iconName)}${esc(kindLabel)}</span>
      ${label ? `<span class="res-src">${esc(label)}</span>` : ""}
      <span class="res-fresh ${esc(source.freshness || "unknown")}">${esc(freshness)}</span>
      ${findings ? '<span class="res-chip warn" title="Kaynak metninde bir yönlendirme kalıbı görüldü; içerik talimat sayılmaz">yönlendirme kalıbı</span>' : ""}
      <span class="res-id">${esc(source.id || "")}</span>
    </div>
    <h4 class="res-card-title">${esc(source.title || host || source.url)}</h4>
    <div class="res-card-meta">${researchMetaChips(source)}</div>
    ${source.excerpt ? `<p class="res-card-excerpt">${esc(source.excerpt)}</p>` : ""}
    <div class="res-card-actions">
      <button type="button" class="btn btn-ghost small" data-open-url="${esc(source.url)}" title="Tarayıcıda açar">Aç</button>
      <span class="res-card-url">${esc(host)}</span>
    </div>
  </article>`;
}

function researchClaimsMarkup(claims) {
  if (!Array.isArray(claims) || !claims.length) return "";
  return `<ol class="res-claims">${claims.map((claim) => {
    const cites = (claim.citations || []).map((id) => `<span class="res-cite">${esc(id)}</span>`).join("");
    const confidence = Number(claim.confidence);
    const level = Number.isFinite(confidence) ? (confidence >= 0.75 ? "birden çok kaynak" : "tek kaynak") : "";
    return `<li><span class="res-claim-text">${esc(claim.text)}</span><span class="res-claim-meta">${cites}${level ? `<span class="res-claim-level">${esc(level)}</span>` : ""}</span></li>`;
  }).join("")}</ol>`;
}

/* ── the workspace ────────────────────────────────────────────────── */

const Research = {
  catalogue: [],
  selected: new Set(),
  preset: store("nova.research.preset") || "general",
  loaded: false,

  async loadSources() {
    if (!bridgeReady()) return;
    const result = await call("research_sources");
    if (result.ok === false) { toast(result.error || "Kaynaklar okunamadı.", true); return; }
    this.catalogue = Array.isArray(result.sources) ? result.sources : [];
    const remembered = (store("nova.research.sources") || "").split(",").filter(Boolean);
    const known = new Set(this.catalogue.map((item) => item.id));
    const chosen = remembered.filter((id) => known.has(id) && id !== "site");
    this.selected = new Set(chosen.length ? chosen : researchPreset(this.preset, this.catalogue));
    this.loaded = true;
    this.renderPresets();
    this.renderSources();
  },

  renderPresets() {
    const host = $("#res-presets");
    if (!host) return;
    host.innerHTML = RESEARCH_PRESETS.map(([id, label]) =>
      `<button type="button" class="res-preset ${id === this.preset ? "active" : ""}" data-preset="${id}">${esc(label)}</button>`).join("");
    $$("[data-preset]", host).forEach((node) => node.addEventListener("click", () => this.applyPreset(node.dataset.preset)));
  },

  renderSources() {
    const host = $("#res-sources");
    if (!host) return;
    host.innerHTML = this.catalogue.filter((item) => item.id !== "site").map((item) => {
      const [iconName] = RESEARCH_KINDS[item.kind] || RESEARCH_KINDS.web;
      const on = this.selected.has(item.id);
      return `<button type="button" class="res-source ${on ? "on" : ""}" data-source="${esc(item.id)}" aria-pressed="${on ? "true" : "false"}" title="${esc(item.description || "")}">${icon(iconName)}<span>${esc(item.label)}</span></button>`;
    }).join("");
    $$("[data-source]", host).forEach((node) => node.addEventListener("click", () => this.toggle(node.dataset.source)));
    const site = $("#research-site");
    if (site) site.disabled = !this.catalogue.some((item) => item.id === "site");
  },

  applyPreset(id) {
    this.preset = id;
    store("nova.research.preset", id);
    this.selected = new Set(researchPreset(id, this.catalogue));
    this.persist();
    this.renderPresets();
    this.renderSources();
  },

  toggle(id) {
    if (this.selected.has(id)) {
      if (this.selected.size === 1) { toast("En az bir kaynak seçili kalmalı.", true); return; }
      this.selected.delete(id);
    } else {
      this.selected.add(id);
    }
    this.preset = "";
    store("nova.research.preset", "");
    this.persist();
    this.renderPresets();
    this.renderSources();
  },

  persist() { store("nova.research.sources", [...this.selected].join(",")); },

  selection() {
    const order = this.catalogue.map((item) => item.id);
    return [...this.selected].filter((id) => id !== "site").sort((a, b) => order.indexOf(a) - order.indexOf(b));
  },
};

async function renderResearchHistory() {
  const host = $("#research-history");
  if (!host || !bridgeReady()) return;
  const history = await call("research_history");
  if (history.ok === false || !(history.items || []).length) { host.innerHTML = '<p class="res-empty">Henüz araştırma yok.</p>'; return; }
  host.innerHTML = history.items.map((item) =>
    `<button type="button" class="res-history-item" data-history-query="${esc(item.question)}" title="${item.sources} kaynak · yeniden açar"><span class="res-history-q">${esc(item.question)}</span><span class="res-history-n">${esc(String(item.sources))} kaynak</span></button>`).join("");
  $$("[data-history-query]", host).forEach((node) => node.addEventListener("click", () => {
    $("#research-input").value = node.dataset.historyQuery;
    $("#research-form").requestSubmit();
  }));
}

async function submitResearch(event) {
  event.preventDefault();
  if (State.paused) { toast(PAUSED_NOTICE, true); return; }
  if (State.busy || !bridgeReady()) return;
  const query = $("#research-input").value.trim();
  if (!query) return;
  if (!Research.loaded) await Research.loadSources();
  const sources = Research.selection();
  const site = ($("#research-site") ? $("#research-site").value : "").trim();
  if (!sources.length && !site) { toast("En az bir kaynak seç ya da bir site yaz.", true); return; }
  const panel = $("#research-result");
  panel.hidden = false;
  panel.classList.remove("err");
  const where = Research.catalogue.filter((item) => sources.includes(item.id)).map((item) => item.label).concat(site ? [site] : []).join(", ");
  panel.innerHTML = `<div class="res-working"><span class="thinking"><span class="orbit"></span>${esc(where)} taranıyor, kaynaklar damgalanıyor…</span></div>`;
  $("#research-submit").disabled = true;
  setBusy(true, "RESEARCHING");
  const result = await call("run_research", query, Number($("#research-count").value), sources, site || null);
  if (result.ok === false) renderResearch(false, null, result.error || "Araştırma başlatılamadı.");
}

/* The report as a Markdown page: exactly what the cards say, in order,
   with the same provenance line and nothing added. */
function researchReportMarkdown(report, catalogue) {
  const lines = [`# Araştırma raporu: ${report.question || report.query || ""}`, ""];
  const sources = Array.isArray(report.sources) ? report.sources : [];
  const when = report.cached_at || report.created_at || "";
  lines.push(`*${sources.length} kaynak · ${report.cache_hit ? "önbellekten" : "canlı"}${when ? " · " + when : ""}${report.stale ? " · güncel olmayabilir" : ""}*`, "");
  if (report.claims && report.claims.length) {
    lines.push("## Bulgular", "");
    report.claims.forEach((claim) => lines.push(`- ${claim.text} _[${(claim.citations || []).join(", ")}]_`));
    lines.push("");
  }
  if (sources.length) {
    lines.push("## Kaynaklar", "");
    sources.forEach((source) => {
      const label = ((catalogue || []).find((item) => item.id === source.source) || {}).label || source.source || "";
      lines.push(`### ${source.id} · ${source.title}`, "", `- ${source.url}`,
        `- ${label}${source.freshness ? " · " + (RESEARCH_FRESHNESS_TR[source.freshness] || source.freshness) : ""}${source.published_at ? " · " + String(source.published_at).slice(0, 10) : ""}`);
      if (source.excerpt) lines.push("", `> ${source.excerpt}`);
      lines.push("");
    });
  }
  const uncertainties = Array.isArray(report.uncertainties) ? report.uncertainties.map(researchUncertaintyTr).filter(Boolean) : [];
  if (uncertainties.length) {
    lines.push("## Belirsizlikler", "");
    uncertainties.forEach((item) => lines.push(`- ${item}`));
    lines.push("");
  }
  return lines.join("\n");
}

async function exportResearchReport() {
  const report = State.lastResearchReport;
  if (!report) { toast("Dışa aktarılacak rapor yok; önce bir araştırma çalıştır.", true); return; }
  const picked = await call("pick_folder");
  if (picked.ok === false) { toast(picked.error || "Klasör seçilemedi.", true); return; }
  if (!picked.path) return;
  const result = await call("save_markdown", picked.path, `arastirma-${report.question || "rapor"}`,
    researchReportMarkdown(report, Research.catalogue));
  if (result.ok === false) { toast(result.error || "Rapor kaydedilemedi.", true); return; }
  toast(`Rapor kaydedildi: ${result.file}`, "ok");
}

function renderResearch(ok, report, error) {
  const panel = $("#research-result");
  panel.hidden = false;
  panel.classList.toggle("err", !ok);
  $("#research-submit").disabled = false;
  if (State.busy) setBusy(false, READY);
  if (!ok) { panel.innerHTML = `<p class="res-error">${esc(error || "Araştırma başarısız.")}</p>`; Presence.error("araştırma başarısız"); return; }
  State.lastResearchReport = report;
  const sources = Array.isArray(report.sources) ? report.sources : [];
  const kinds = [...new Set(sources.map((source) => (RESEARCH_KINDS[source.kind] || RESEARCH_KINDS.web)[1]))];
  const when = report.cached_at ? new Date(report.cached_at) : (report.created_at ? new Date(report.created_at) : null);
  const stamp = when && !Number.isNaN(when.getTime()) ? when.toLocaleString("tr-TR", { dateStyle: "medium", timeStyle: "short" }) : "";
  const provenance = [
    `${sources.length} kaynak`,
    kinds.join(" · "),
    report.cache_hit ? `önbellekten${stamp ? " · " + stamp : ""}` : `canlı${stamp ? " · " + stamp : ""}`,
    report.stale ? "güncel olmayabilir" : "",
  ].filter(Boolean).join(" · ");
  const uncertainties = Array.isArray(report.uncertainties) ? report.uncertainties.map(researchUncertaintyTr).filter(Boolean) : [];
  panel.innerHTML = `
    <div class="res-report-head">
      <span class="kicker">Rapor</span><button type="button" id="res-export" class="btn btn-ghost small res-export" title="Raporu Markdown olarak kaydet">Dışa aktar (.md)</button>
      <h2>${esc(report.question || report.query || "")}</h2>
      <p class="res-report-meta">${esc(provenance)}</p>
    </div>
    ${sources.length ? `<section class="res-section"><h3>Kaynaklar</h3><div class="res-cards">${sources.map((source) => researchSourceMarkup(source, Research.catalogue)).join("")}</div></section>` : '<p class="res-empty">Hiçbir kaynak toplanamadı.</p>'}
    ${report.claims && report.claims.length ? `<section class="res-section"><h3>Bulgular</h3>${researchClaimsMarkup(report.claims)}</section>` : ""}
    ${uncertainties.length ? `<section class="res-section"><h3>Belirsizlikler</h3><ul class="res-uncertain">${uncertainties.map((text) => `<li>${esc(text)}</li>`).join("")}</ul></section>` : ""}`;
  $$("[data-open-url]", panel).forEach((node) => node.addEventListener("click", async () => {
    const opened = await call("open_external", node.dataset.openUrl);
    if (opened.ok === false) toast(opened.error || "Bağlantı açılamadı.", true);
  }));
  const exporter = $("#res-export", panel);
  if (exporter) exporter.addEventListener("click", exportResearchReport);
  if (Motion.allowed()) Motion.stagger($$(".res-card", panel), { step: 45, y: 10 });
}

/* ── the room ─────────────────────────────────────────────────────── */

const ResearchRoom = {
  active: false,
  opening: null,
  sound: roomSwitch("nova.research.sound"),
  night: roomNight("nova.research.theme", "research-dark"),

  shouldPlayIntro() { return Motion.allowed() && !State.compact; },

  enter() {
    document.body.classList.add("research");
    this.night.apply();
    this.active = true;
    this.syncThemeButton();
    this.syncSoundButton();
    if (!Research.loaded) Research.loadSources();
    if (this.shouldPlayIntro()) this.playIntro();
  },

  leave() {
    if (!this.active && !document.body.classList.contains("research")) return;
    this.active = false;
    this.abortIntro();
    document.body.classList.remove("research", "research-dark");
  },

  /* The constellation: each source lights up on its own line to the hub,
     with a rising ping; the wordmark settles once they are all there. */
  playIntro() {
    this.abortIntro();
    const veil = $("#research-intro");
    if (!veil) return;
    const word = veil.querySelector(".ri-word");
    const nodes = RESEARCH_INTRO_ORDER.map((id) => veil.querySelector(`.ri-node[data-source="${id}"]`)).filter(Boolean);
    const lines = RESEARCH_INTRO_ORDER.map((id) => veil.querySelector(`.ri-line[data-source="${id}"]`)).filter(Boolean);
    if (word) word.style.opacity = "0";
    // The outer group carries the node's place on the map; the inner one is
    // what pops, so the animation's transform never displaces the position.
    const pops = nodes.map((node) => node.querySelector(".ri-pop") || node);
    pops.forEach((pop) => { pop.style.opacity = "0"; });
    lines.forEach((line) => {
      const length = typeof line.getTotalLength === "function" ? line.getTotalLength() : 400;
      line.style.strokeDasharray = `${length} ${length}`;
      line.style.strokeDashoffset = String(length);
    });
    const play = this.sound.on();
    const steps = [];
    const notes = [523.25, 587.33, 659.25, 698.46, 783.99, 880, 987.77, 1046.5];
    lines.forEach((line, index) => {
      const at = 140 + index * 150;
      steps.push({ at, run: (track) => {
        const length = parseFloat(line.style.strokeDasharray) || 400;
        track(line.animate([{ strokeDashoffset: length }, { strokeDashoffset: 0 }], { duration: 260, easing: Motion.standard, fill: "forwards" }));
        const pop = pops[index];
        if (pop) {
          pop.style.opacity = "";
          track(pop.animate([{ opacity: 0, transform: "scale(0.4)" }, { opacity: 1, transform: "scale(1.12)", offset: 0.7 }, { opacity: 1, transform: "scale(1)" }],
            { duration: 380, delay: 180, easing: Motion.enter, fill: "both" }));
        }
      } });
      if (play) RoomAudio.tone(at + 180, { freq: notes[index % notes.length], duration: 0.12, peak: 0.05, attack: 0.008 });
    });
    steps.push({ at: RESEARCH_INTRO.title, run: (track) => {
      if (!word) return;
      word.style.opacity = "";
      track(word.animate([{ opacity: 0, transform: "translateY(14px)" }, { opacity: 1, transform: "translateY(0)" }],
        { duration: 700, easing: Motion.enter, fill: "forwards" }));
    } });
    if (play) {
      RoomAudio.tone(RESEARCH_INTRO.title + 100, { freq: 523.25, duration: 1.3, peak: 0.05, attack: 0.03 });
      RoomAudio.tone(RESEARCH_INTRO.title + 160, { freq: 783.99, duration: 1.4, peak: 0.04, attack: 0.03 });
      RoomAudio.tone(RESEARCH_INTRO.title + 220, { freq: 1046.5, duration: 1.1, peak: 0.02, attack: 0.03 });
    }
    this.opening = runRoomOpening(veil, {
      steps,
      hold: RESEARCH_INTRO.hold,
      fade: RESEARCH_INTRO.fade,
      skipFade: RESEARCH_INTRO.skipFade,
      silence: () => RoomAudio.hush(),
      onReveal: () => this.reveal(),
    });
  },

  skipIntro() { if (this.opening && this.opening.running) this.opening.skip(); },
  abortIntro() { if (this.opening && this.opening.running) this.opening.abort(); this.opening = null; },
  get playing() { return !!(this.opening && this.opening.running); },

  reveal() {
    if (!Motion.allowed()) return;
    Motion.stagger($$(".res-side > *, .res-head, .res-form, #research-result"), { step: 55, y: 14 });
  },

  toggleTheme() {
    this.night.set(!this.night.dark());
    this.night.apply();
    this.syncThemeButton();
  },

  syncThemeButton() {
    const button = $("#res-theme");
    if (!button) return;
    const dark = this.night.dark();
    button.setAttribute("aria-pressed", dark ? "true" : "false");
    button.title = dark ? "Koyu tema açık · aydınlığa dönmek için tıkla" : "Aydınlık tema açık · koyuya geçmek için tıkla";
    button.innerHTML = `${icon(dark ? "moon" : "sun")}<span>${dark ? "Koyu" : "Aydınlık"}</span>`;
  },

  toggleSound() {
    const next = !this.sound.on();
    this.sound.set(next);
    if (!next) RoomAudio.hush();
    this.syncSoundButton();
    toast(next ? "Araştırma sesleri açık." : "Araştırma sesleri kapalı.", next ? "ok" : undefined);
  },

  syncSoundButton() {
    const button = $("#res-sound");
    if (!button) return;
    const on = this.sound.on();
    button.setAttribute("aria-pressed", on ? "true" : "false");
    button.title = on ? "Açılış sesleri açık · kapatmak için tıkla" : "Açılış sesleri kapalı · açmak için tıkla";
    button.innerHTML = `${icon(on ? "sound" : "mute")}<span>${on ? "Ses açık" : "Ses kapalı"}</span>`;
  },
};

function bindResearch() {
  const veil = $("#research-intro");
  if (veil) veil.addEventListener("pointerdown", () => ResearchRoom.skipIntro());
  // Capture phase on the window, ahead of the shell: Escape during the
  // opening skips the opening rather than leaving the room.
  addEventListener("keydown", (event) => {
    if (!ResearchRoom.playing) return;
    if (event.key === "Escape" || event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      event.stopImmediatePropagation();
    }
    ResearchRoom.skipIntro();
  }, true);
  const back = $("#res-back");
  if (back) back.addEventListener("click", () => showScreen("home"));
  const theme = $("#res-theme");
  if (theme) theme.addEventListener("click", () => ResearchRoom.toggleTheme());
  const sound = $("#res-sound");
  if (sound) sound.addEventListener("click", () => ResearchRoom.toggleSound());
  ResearchRoom.syncThemeButton();
  ResearchRoom.syncSoundButton();
}
