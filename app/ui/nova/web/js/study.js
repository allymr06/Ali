/* ════════════════════════════════════════════════════════════════════
   JARVIS · NOVA — Tıp Akademisi: bağlantılı çalışma akışı
   The exam plan and its "Bugün" view, the understanding findings with
   their diagnosis and repair sessions, the prerequisite diagnosis, the
   histology practicals, the confidence and reasoning that travel with an
   answer, the source-support status of a question and the student's own
   "Soruda hata olabilir" flag.

   Every figure comes from app/medical/study.py through
   Medical.request(action, params). Nothing here estimates on its own:
   an estimate is the planner's and is labelled as one; a specimen's
   answer is hidden until it is given; a model verdict names its assessor.
   ════════════════════════════════════════════════════════════════════ */
"use strict";

const STUDY_CONFIDENCE = [["sure", "Eminim"], ["unsure", "Kararsızım"], ["guess", "Tahmin ettim"]];
const STUDY_FLAG_KINDS = [
  ["disputed_answer", "Cevap anahtarı tartışmalı"],
  ["multiple_answers", "Birden fazla doğru olabilir"],
  ["source_mismatch", "Kaynak uyuşmuyor"],
  ["figure_problem", "Şekil okunmuyor ya da yanıltıcı"],
];
const STUDY_COVERAGE_TONE = { misconception: "bad", due_review: "warn", unstudied: "", studied_unassessed: "", assessed_limited: "violet", demonstrated: "ok" };
const STUDY_FINDING_TONE = { hypothesis: "", supported: "bad", disputed: "warn", repair_demonstrated: "violet", resolved: "ok", reopened: "bad", dismissed: "", withdrawn: "" };
const STUDY_SUPPORT_TONE = { source_supported: "ok", imported: "ok", needs_review: "warn", conflicting_evidence: "bad", insufficient_evidence: "warn", unresolved: "warn", not_applicable: "", stale: "warn", unavailable: "bad", invalidated: "bad" };
const STUDY_ACTIVITY_TONE = { planned: "", started: "accent", completed: "ok", skipped: "", missed: "bad" };
const STUDY_QUALITY_TONE = { specific: "ok", partial: "violet", generic: "warn", wrong: "bad", unassessed: "" };
const STUDY_MASTERY_TR = { weak: "zayıf", moderate: "orta", strong: "güçlü", unknown: "bilinmiyor" };
const STUDY_OUTCOME_TR = { against: "bulguyu destekliyor", confirms: "bulguyu doğruladı", for: "bulguya karşı", unclear: "belirsiz", neutral: "nötr" };
const STUDY_BASIS_OPTIONS = [["user_confirmed", "Ben onaylıyorum"], ["page_caption", "Sayfadaki başlık ya da etiket"], ["none", "Henüz bilmiyorum (adsız kaydet)"]];

function studyMinutes(value) {
  const number = Number(value);
  return Number.isFinite(number) ? `${Math.round(number)} dk` : "—";
}

function studyDate(value) {
  const text = String(value || "").slice(0, 10);
  if (!text) return "—";
  try {
    const date = new Date(`${text}T00:00:00`);
    if (Number.isNaN(date.getTime())) return text;
    return date.toLocaleDateString("tr-TR", { day: "numeric", month: "long", weekday: "long" });
  } catch (error) {
    return text;
  }
}

function studyAssessor(value) {
  // Who judged this: a rule in the core, or the model — named when the
  // academy pins one, plain "Model" when the gateway routes it.
  const text = String(value || "");
  if (!text || text === "none") return "Model kapalı";
  if (text === "rule") return "Kural";
  if (text.startsWith("model:")) {
    const name = text.slice(6);
    return !name || name === "unknown" ? "Model" : `Model · ${name}`;
  }
  return text;
}

function studyOptions(options, { chosen = null, correctKey = null, revealed = false } = {}) {
  return `<div class="med-options">${(options || []).map((option) => {
    const isChosen = chosen === option.key;
    const isCorrect = revealed && correctKey === option.key;
    const isWrong = revealed && isChosen && correctKey !== option.key;
    return `<button type="button" class="med-option ${isChosen ? "chosen" : ""} ${isCorrect ? "correct" : ""} ${isWrong ? "wrong" : ""}" data-study-option="${esc(option.key)}" ${revealed ? "disabled" : ""}>
      <span class="mo-key">${esc(option.key)}</span><span>${esc(option.text)}</span></button>`;
  }).join("")}</div>`;
}

/* ════════════════════════════════════════════════════════════════════
   Study: plan, understanding, histology, and the answer's context
   ════════════════════════════════════════════════════════════════════ */

const Study = {
  plans: [],
  plan: null,
  understanding: null,
  finding: null,
  findingEvents: [],
  repair: null,
  diagnosis: null,
  check: null,
  struggling: [],
  recentDiagnoses: [],
  histology: null,
  specimen: null,
  session: null,
  sessionIndex: 0,
  sessionTimer: 0,
  sessionDeadline: 0,
  cropCache: new Map(),
  pendingExplain: {},     // question_id → {event_id, reason, text?, sent?, note?}
  sentReasons: {},        // event_id → classification label (results view)
  selection: null,        // the histology region being drawn on a page

  request(action, params) { return Medical.request(action, params || {}); },
  viewIs(view) { return State.screen === "medical" && Medical.view === view; },
  submissionId(...parts) { return parts.map((part) => String(part || "")).join(":"); },

  /* ── pure markup ──────────────────────────────────────────────── */

  confidenceChips(selected, { locked = false } = {}) {
    return `<div class="study-confidence"><span class="sc-label">Ne kadar eminsin?</span>${STUDY_CONFIDENCE.map(([key, label]) =>
      `<button type="button" class="chip ${selected === key ? "active accent" : ""}" data-confidence="${key}" ${locked ? "disabled" : ""}>${label}</button>`).join("")}</div>`;
  },

  classificationLabel(event) {
    if (!event) return "";
    return event.classification_label || {
      correct_supported: "Doğru cevap, gerekçe destekliyor",
      correct_unsupported: "Doğru cevap, gerekçe yok ya da tahmin",
      correct_contradictory: "Doğru cevap, gerekçe çelişkili",
      wrong_low_confidence: "Yanlış cevap, düşük güven",
      wrong_high_confidence: "Yanlış cevap, yüksek güven",
    }[event.classification] || "Sınıflanmadı";
  },

  explainBox(pending) {
    if (!pending) return "";
    if (pending.sent) {
      return `<div class="med-explain study-explain"><h4>Gerekçen</h4>${esc(pending.text)}
        <br><span class="faint">${esc(pending.note || "Değerlendiriliyor…")}</span></div>`;
    }
    const why = pending.reason === "open_finding"
      ? "Bu kavramda açık bir bulgu var; bir iki cümleyle neden bu şıkkı seçtiğini yaz."
      : "Ara sıra sorulur: bir iki cümleyle neden bu şıkkı seçtiğini yaz. Gerekçe kaynağa göre değerlendirilir; puanı değiştirmez.";
    return `<div class="med-explain study-explain"><h4>Kısaca neden?</h4>
      <p class="med-review-note">${why}</p>
      <textarea class="study-textarea" data-explain-text rows="2" maxlength="1200" placeholder="Çünkü…"></textarea>
      <div class="btn-row" style="justify-content:flex-start">
        <button type="button" class="btn btn-primary small" data-explain-send>Gönder</button>
        <button type="button" class="btn btn-ghost small" data-explain-skip>Geç</button></div></div>`;
  },

  assessmentMarkup(event) {
    if (!event || !event.assessment || event.assessment.status !== "done") return "";
    const assessment = event.assessment;
    return `<div class="med-explain study-assessment"><h4>Gerekçe değerlendirmesi</h4>
      <span class="chip ${String(event.classification || "").startsWith("wrong") || event.classification === "correct_contradictory" ? "warn" : "ok"}">${esc(this.classificationLabel(event))}</span>
      ${assessment.suspected_misconception ? `<br>Olası yanlış anlama: ${esc(assessment.suspected_misconception)}` : ""}
      ${assessment.note ? `<br>${esc(assessment.note)}` : ""}
      <br><span class="faint">Değerlendiren: ${esc(studyAssessor(assessment.assessor || "rule"))}</span></div>`;
  },

  supportChip(support) {
    if (!support || !support.status) return "";
    const tone = STUDY_SUPPORT_TONE[support.status] || "";
    const title = [support.reason, support.checked_at ? `İncelendi: ${support.checked_at.slice(0, 16).replace("T", " ")}` : ""].filter(Boolean).join(" · ");
    return `<span class="chip ${tone}" title="${esc(title)}">${esc(support.label || support.status)}${support.scored ? "" : " · puansız"}</span>`;
  },

  activityMarkup(activity, { actions = true } = {}) {
    if (!activity) return "";
    const status = activity.status || "planned";
    return `<div class="study-activity ${status}" data-activity="${esc(activity.activity_id)}">
      <div class="sa-head"><span class="chip ${STUDY_ACTIVITY_TONE[status] || ""}">${esc(activity.status_label || status)}</span>
        <span class="chip">${esc(activity.kind_label || activity.kind)}</span>
        <span class="sa-estimate" title="${esc(activity.estimate_label || "tahmini")}">≈ ${studyMinutes(activity.estimate_minutes)} <i>(${esc(activity.estimate_label || "tahmini")})</i></span></div>
      <div class="sa-title">${esc(activity.title)}</div>
      <div class="sa-reason">${esc(activity.reason || "")}</div>
      ${actions && (status === "planned" || status === "started") ? `<div class="btn-row" style="justify-content:flex-start">
        <button type="button" class="btn btn-primary small" data-activity-run="${esc(activity.activity_id)}">${status === "started" ? "Devam et" : "Başla"}</button>
        <button type="button" class="btn btn-ghost small" data-activity-done="${esc(activity.activity_id)}">Bitti</button>
        <button type="button" class="btn btn-ghost small" data-activity-skip="${esc(activity.activity_id)}">Atla</button></div>` : ""}
      ${status === "completed" && activity.actual_minutes ? `<div class="sa-reason faint">${studyMinutes(activity.actual_minutes)} sürdü</div>` : ""}
    </div>`;
  },

  todayMarkup(today, { compact = false } = {}) {
    if (!today || !today.plan) {
      return `<div class="study-today empty"><p>${esc((today && today.message) || "Sınav planı yok.")}</p>
        <button type="button" class="btn btn-ghost small" data-study-go="plan">Plan oluştur</button></div>`;
    }
    const plan = today.plan;
    const head = `<div class="study-today-head"><span class="st-name">${esc(plan.name)}</span>
      <span class="faint">${esc(studyDate(plan.exam_date))}${Number.isFinite(Number(plan.days_left)) ? ` · ${plan.days_left} gün kaldı` : ""}</span></div>`;
    if (today.proposed_scope) {
      return `<div class="study-today">${head}<p class="med-review-note">${esc(today.message || "")}</p>
        <button type="button" class="btn btn-primary small" data-study-go="plan">Kapsamı onayla</button></div>`;
    }
    const items = today.activities || [];
    const progress = `<div class="study-today-meter"><span>${studyMinutes(today.done_minutes)} bitti · ${studyMinutes(today.planned_minutes)} planlı · ${studyMinutes(today.budget)} ayrılmış</span>
      <span class="med-bar"><i class="${today.budget && today.done_minutes / today.budget < 0.5 ? "low" : ""}" style="transform: scaleX(${(today.budget ? clamp(today.done_minutes / today.budget, 0, 1) : 0).toFixed(3)})"></i></span></div>`;
    const overload = today.overload ? `<p class="med-review-note warn-text">${esc(today.overload.message)}</p>` : "";
    const message = today.message ? `<p class="med-review-note">${esc(today.message)}</p>` : "";
    if (compact) {
      return `<div class="study-today">${head}${progress}${overload}${message}
        ${today.next ? this.activityMarkup(today.next) : ""}
        ${items.length > 1 ? `<button type="button" class="btn btn-ghost small" data-study-go="plan">Bugünün tamamı (${items.length} etkinlik)</button>` : ""}</div>`;
    }
    return `<div class="study-today">${head}${progress}${overload}${message}
      ${items.length ? items.map((item) => this.activityMarkup(item)).join("") : ""}</div>`;
  },

  coverageMarkup(coverage) {
    if (!coverage || !coverage.topics) return "";
    const labels = coverage.labels || {};
    const counts = coverage.counts || {};
    const chips = Object.keys(labels).filter((key) => counts[key]).map((key) =>
      `<span class="chip ${STUDY_COVERAGE_TONE[key] || ""}">${esc(labels[key])} · ${counts[key]}</span>`).join("");
    const rows = coverage.topics.map((row) => `<div class="study-cov-row ${esc(row.state)}">
      <span class="chip ${STUDY_COVERAGE_TONE[row.state] || ""}">${esc(row.state_label)}</span>
      <span class="sc-title">${esc(row.title)}</span>
      <span class="sc-side">${row.attempts ? `${row.correct}/${row.attempts}` : "ölçülmedi"}${row.findings ? ` · ${row.findings} bulgu` : ""}</span></div>`).join("");
    return `<div class="study-coverage"><div class="med-chips">${chips || '<span class="chip">Kapsamda konu yok</span>'}</div>${rows}</div>`;
  },

  daysMarkup(days) {
    if (!days || !days.length) return "";
    return `<div class="study-days">${days.map((day) => `<div class="study-day ${day.budget === 0 ? "off" : ""}">
      <div class="sd-head"><b>${esc(studyDate(day.date))}</b><span class="faint">${day.budget === 0 ? "boş gün" : `${studyMinutes(day.planned_minutes)} / ${studyMinutes(day.budget)}`}</span></div>
      ${(day.activities || []).map((item) => `<div class="sd-item ${esc(item.status)}"><span class="chip ${STUDY_ACTIVITY_TONE[item.status] || ""}">${esc(item.kind_label)}</span> ${esc(item.title)} <i>≈ ${studyMinutes(item.estimate_minutes)}</i></div>`).join("") || '<div class="sd-item faint">etkinlik yok</div>'}
    </div>`).join("")}</div>`;
  },

  findingRow(finding, { active = false } = {}) {
    return `<button type="button" class="med-row ${active ? "active" : ""}" data-finding="${esc(finding.finding_id)}">
      <span class="med-row-title">${esc(finding.concept_name)}</span>
      <span class="med-row-side"><span class="chip ${STUDY_FINDING_TONE[finding.status] || ""}">${esc(finding.status_label)}</span></span>
      <span class="med-row-meta">${esc(finding.statement)}</span></button>`;
  },

  findingMarkup(finding) {
    if (!finding) return medEmpty("Bir bulgu seç", "Sol listeden bir bulgu açınca kanıtı, kaynağı ve onarım adımları burada görünür.");
    const evidence = (finding.evidence || []).map((item) => `<div class="study-evidence ${item.valid === false ? "invalid" : ""}">
      <span class="chip ${item.outcome === "for" ? "ok" : item.outcome === "against" || item.outcome === "confirms" ? "bad" : ""}">${esc(STUDY_OUTCOME_TR[item.outcome] || item.outcome || "")}</span>
      <span class="chip">${esc(item.kind || "")}</span>
      ${item.valid === false ? '<span class="chip">geçersiz sayıldı</span>' : ""}
      <div class="se-text">${esc(item.excerpt || "")}</div>${item.note ? `<div class="faint">${esc(item.note)}</div>` : ""}</div>`).join("");
    const sources = (finding.sources || []).map((ref) => `<button type="button" class="chip" data-source="${esc(ref.document_id + "|" + ref.page_number)}">${esc(ref.title || "Kaynak")} · s. ${ref.page_number}</button>`).join("");
    const followUps = (finding.follow_ups || []).map((item) => `<div class="med-row"><span class="med-row-sub">Gecikmeli tekrar: ${esc(studyDate(item.due_at))} · ${item.done_at ? (item.outcome === "confirmed" ? "doğru cevaplandı" : esc(item.outcome || "yapıldı")) : "bekliyor"}</span></div>`).join("");
    const limitations = (finding.limitations || []).length ? `<div class="med-explain"><h4>Sınırlar</h4>${finding.limitations.map((line) => esc(line)).join("<br>")}</div>` : "";
    const closed = ["resolved", "dismissed", "withdrawn"].includes(finding.status);
    const diagnostic = finding.pending_diagnostic
      ? `<div class="med-explain study-diagnostic"><h4>Kısa teşhis sorusu</h4>${esc(finding.pending_diagnostic.question)}
          <textarea class="study-textarea" data-diagnostic-text rows="2" maxlength="800" placeholder="Kendi cümlelerinle cevapla…"></textarea>
          <div class="btn-row" style="justify-content:flex-start"><button type="button" class="btn btn-primary small" data-finding-act="diagnostic_answer">Cevapla</button></div></div>`
      : "";
    const repairs = (finding.repairs || []).map((session) => `<button type="button" class="med-row" data-repair="${esc(session.session_id)}">
      <span class="med-row-title">Onarım oturumu</span><span class="med-row-side">${esc(session.status === "open" ? "açık" : session.outcome || session.status)}</span>
      <span class="med-row-meta">${esc(String(session.started_at || "").slice(0, 16).replace("T", " "))}</span></button>`).join("");
    const history = (finding.history || []).slice(-6).map((item) => `<div class="med-row"><span class="med-row-sub">${esc(String(item.at || "").slice(0, 16).replace("T", " "))} · ${esc(item.note)}</span></div>`).join("");
    return `<div class="panel med-card study-finding">
      <div class="panel-title"><span class="kicker">${esc(finding.concept_name)}</span><span class="chip ${STUDY_FINDING_TONE[finding.status] || ""}">${esc(finding.status_label)}</span></div>
      <div class="mq-stem">${esc(finding.statement)}</div>
      <div class="med-chips"><span class="chip">${esc(finding.subject_label || "")}</span><span class="chip">öncelik ${esc(finding.priority)}</span><span class="chip">${finding.evidence_count || 0} kanıt</span>${sources}</div>
      <div class="btn-row" style="justify-content:flex-start">
        ${!closed ? `<button type="button" class="btn btn-primary small" data-finding-act="repair">${finding.repair ? "Onarımı aç" : "Onarımı başlat"}</button>` : ""}
        ${!closed && !finding.pending_diagnostic ? '<button type="button" class="btn btn-ghost small" data-finding-act="diagnostic_ask" title="Model kısa bir açık uçlu soru sorar; cevabın bulguyu doğrular ya da çürütür">Teşhis sorusu</button>' : ""}
        ${!closed ? '<button type="button" class="btn btn-ghost small" data-finding-act="diagnosis" title="Bu kavramın ön koşullarını sırayla kontrol eder">Ön koşulu teşhis et</button>' : ""}
        ${!closed ? '<button type="button" class="btn btn-ghost small" data-finding-act="challenge">İtiraz et</button>' : ""}
        ${!closed ? '<button type="button" class="btn btn-ghost small" data-finding-act="dismiss">Yok say</button>' : '<button type="button" class="btn btn-ghost small" data-finding-act="reopen">Yeniden aç</button>'}
      </div>
      ${diagnostic}
      <h4 class="study-h4">Kanıt</h4>${evidence || medEmpty("Kanıt yok")}
      ${followUps ? `<h4 class="study-h4">Gecikmeli tekrar</h4>${followUps}` : ""}
      ${repairs ? `<h4 class="study-h4">Onarımlar</h4>${repairs}` : ""}
      ${limitations}
      <div data-prereqs="${esc(finding.concept_id)}"></div>
      <h4 class="study-h4">Geçmiş</h4>${history}
    </div>`;
  },

  prerequisitesMarkup(payload) {
    if (!payload) return "";
    const reviewed = (payload.prerequisites || []).map((item) => `<div class="med-row"><span class="med-row-title">${esc(item.name)}</span>
      <span class="med-row-side">derinlik ${item.depth}</span>
      <span class="med-row-meta"><span class="chip">${esc(item.provenance_label || item.provenance)}</span>${item.note ? ` ${esc(item.note)}` : ""}</span></div>`).join("");
    const pending = (payload.pending || []).map((edge) => `<div class="med-row"><span class="med-row-title">${esc(payload.name)} ← ${esc(edge.requires_name || edge.requires)}</span>
      <span class="med-row-side"><button type="button" class="chip ok" data-edge-confirm="${esc(edge.edge_id)}">Onayla</button> <button type="button" class="chip" data-edge-reject="${esc(edge.edge_id)}">Reddet</button></span>
      <span class="med-row-meta"><span class="chip warn">${esc(edge.provenance_label || edge.provenance)} · onay bekliyor</span>${edge.note ? ` ${esc(edge.note)}` : ""}</span></div>`).join("");
    return `<h4 class="study-h4">Ön koşullar · ${esc(payload.name)}</h4>
      ${payload.known === false ? '<p class="med-review-note">Bu kavram kavram grafiğinde kayıtlı değil.</p>' : ""}
      ${reviewed || (payload.known === false ? "" : '<p class="med-review-note">Onaylı ön koşul yok.</p>')}
      ${pending}
      ${payload.known === false ? "" : `<div class="study-prereq-form"><input type="text" list="study-prereq-options" data-prereq-query maxlength="80" placeholder="Ön koşul öner: kavram adı yaz…" autocomplete="off"><datalist id="study-prereq-options"></datalist>
        <button type="button" class="btn btn-ghost small" data-prereq-suggest title="Senin önerin senin onayınla kaydedilir; döngü oluşturan bir bağ reddedilir">Öner ve onayla</button></div>`}`;
  },

  async renderPrerequisites(host) {
    const slot = host ? host.querySelector("[data-prereqs]") : null;
    if (!slot) return;
    const conceptId = slot.dataset.prereqs;
    const result = await this.request("prerequisites", { concept_id: conceptId });
    if (result.ok === false) { slot.innerHTML = `<p class="med-review-note">${esc(result.error || "Ön koşullar okunamadı.")}</p>`; return; }
    const names = {};
    (result.prerequisites || []).forEach((item) => { names[item.concept_id] = item.name; });
    result.pending = (result.pending || []).map((edge) => ({ ...edge, requires_name: names[edge.requires] || edge.requires }));
    slot.innerHTML = this.prerequisitesMarkup(result);
    const refresh = () => this.renderPrerequisites(host);
    $$("[data-edge-confirm]", slot).forEach((node) => node.addEventListener("click", async () => {
      const outcome = await this.request("prerequisite_confirm", { edge_id: node.dataset.edgeConfirm });
      if (outcome.ok === false) { toast(outcome.error || "Onaylanamadı.", true); return; }
      toast("Ön koşul onaylandı.", "ok"); refresh();
    }));
    $$("[data-edge-reject]", slot).forEach((node) => node.addEventListener("click", async () => {
      const outcome = await this.request("prerequisite_reject", { edge_id: node.dataset.edgeReject });
      if (outcome.ok === false) { toast(outcome.error || "Reddedilemedi.", true); return; }
      toast("Öneri reddedildi.", "ok"); refresh();
    }));
    const query = slot.querySelector("[data-prereq-query]");
    const list = slot.querySelector("#study-prereq-options");
    let matches = [];
    if (query && list) {
      let timer = 0;
      query.addEventListener("input", () => {
        clearTimeout(timer);
        timer = setTimeout(async () => {
          const found = await this.request("concept_search", { text: query.value });
          matches = found.ok === false ? [] : (found.concepts || []);
          list.innerHTML = matches.map((item) => `<option value="${esc(item.name)}">${esc(item.subject_label || "")}</option>`).join("");
        }, 220);
      });
    }
    const suggest = slot.querySelector("[data-prereq-suggest]");
    if (suggest) suggest.addEventListener("click", async () => {
      const typed = ((query && query.value) || "").trim();
      const match = matches.find((item) => item.name.toLocaleLowerCase("tr") === typed.toLocaleLowerCase("tr")) || (matches.length === 1 ? matches[0] : null);
      if (!match) { toast("Listeden bir kavram seç.", true); return; }
      const proposed = await this.request("prerequisite_suggest", { concept_id: conceptId, requires: match.concept_id, provenance: "student", note: "Öğrenci Anlama ekranından önerdi." });
      if (proposed.ok === false) { toast(proposed.error || "Öneri kaydedilemedi.", true); return; }
      const confirmed = await this.request("prerequisite_confirm", { edge_id: proposed.edge.edge_id });
      if (confirmed.ok === false) { toast(confirmed.error || "Onaylanamadı.", true); refresh(); return; }
      toast(`Ön koşul kaydedildi: ${match.name}.`, "ok");
      refresh();
    });
  },

  repairMarkup(session) {
    if (!session) return "";
    const steps = (session.steps || []).map((step) => {
      let body = "";
      if (step.step === "passage") {
        body = `<div class="med-page-text">${esc(step.text || "Kaynak pasaj bulunamadı.")}</div>
          <div class="med-chips">${(step.sources || []).map((ref) => `<button type="button" class="chip" data-source="${esc(ref.document_id + "|" + ref.page_number)}">${esc(ref.title || "Kaynak")} · s. ${ref.page_number}</button>`).join("")}</div>`;
      } else if (step.step === "transfer") {
        const question = step.question || null;
        const answered = step.answered || null;
        body = question
          ? `<div class="mq-stem">${esc(question.stem)}</div>
             ${answered ? "" : this.confidenceChips(null)}
             ${studyOptions(question.options, { chosen: answered ? answered.answer_key : null, correctKey: answered ? question.correct_key : null, revealed: !!answered })}
             ${answered ? `<div class="med-explain">${answered.correct ? "Doğru: aktarım gösterildi." : "Yanlış: bulgu açık kalır, gecikmeli tekrar yine yapılır."}${question.explanation ? `<br>${esc(question.explanation)}` : ""}</div>` : ""}
             <p class="med-review-note">Benzerlik: ${esc(step.similarity || "bilinmiyor")}${step.note ? ` · ${esc(step.note)}` : ""}</p>
             ${(step.limitations || []).length ? `<p class="med-review-note">${step.limitations.map((line) => esc(line)).join(" ")}</p>` : ""}`
          : `<p class="med-review-note">${esc(step.note || "Aktarım sorusu üretilemedi.")}</p>`;
      } else {
        body = `<div class="med-page-text">${esc(step.text || "")}</div>${step.assessor ? `<span class="faint">Anlatımı yazan: ${esc(studyAssessor(step.assessor))}</span>` : ""}`;
      }
      const done = step.done ? '<span class="chip ok">tamam</span>' : (step.step === "follow_up" ? `<span class="chip">${esc(studyDate(step.due_at))}</span>` : "");
      const action = !step.done && ["problem", "passage", "explanation"].includes(step.step)
        ? `<button type="button" class="btn btn-ghost small" data-repair-step="${esc(step.step)}">Okudum</button>` : "";
      return `<div class="study-step ${step.done ? "done" : ""}" data-step="${esc(step.step)}"><div class="ss-head"><b>${esc(step.title)}</b>${done}${action}</div>${body}</div>`;
    }).join("");
    return `<div class="panel med-card study-repair">
      <div class="panel-title"><span class="kicker">Onarım · ${esc(session.concept_name)}</span><span class="chip ${session.status === "open" ? "accent" : "ok"}">${esc(session.status === "open" ? "açık" : session.outcome || session.status)}</span></div>
      <div class="study-steps">${steps}</div>
      <div class="btn-row" style="justify-content:flex-start"><button type="button" class="btn btn-ghost small" data-repair-close>Bulguya dön</button></div>
    </div>`;
  },

  diagnosisMarkup(diagnosis) {
    if (!diagnosis) return "";
    const candidates = (diagnosis.candidates || []).map((item) => {
      const question = item.question || null;
      const answered = item.answer || null;
      const body = !question
        ? '<p class="med-review-note">Bu ön koşul için bankada anahtarlı soru yok.</p>'
        : `<div class="mq-stem">${esc(question.stem)}</div>
           ${answered || item.skipped ? "" : this.confidenceChips(null)}
           ${studyOptions(question.options, { chosen: answered ? answered.answer_key : null, correctKey: answered ? question.correct_key : null, revealed: !!answered || !!item.skipped })}
           ${answered ? `<div class="med-explain">${answered.correct ? "Doğru." : "Yanlış."}${question.explanation ? ` ${esc(question.explanation)}` : ""}</div>` : ""}
           ${!answered && !item.skipped && diagnosis.status === "open" ? `<div class="btn-row" style="justify-content:flex-start"><button type="button" class="btn btn-ghost small" data-dx-skip="${esc(item.concept_id)}">Atla</button></div>` : ""}`;
      return `<div class="study-step ${answered ? "done" : ""}" data-dx-candidate="${esc(item.concept_id)}">
        <div class="ss-head"><b>${esc(item.name)}</b><span class="chip">${esc(item.subject_label || "")}</span><span class="chip">derinlik ${item.depth}</span><span class="chip">${esc(item.provenance || "")}</span><span class="chip ${item.mastery === "weak" ? "bad" : item.mastery === "strong" ? "ok" : ""}">${esc(STUDY_MASTERY_TR[item.mastery] || item.mastery || "")}</span>${item.skipped ? '<span class="chip">atlandı</span>' : ""}</div>
        ${body}</div>`;
    }).join("");
    const path = (diagnosis.path || []).length ? `<div class="med-chips">${diagnosis.path.map((id, index) => `<span class="chip ${index === 0 ? "accent" : ""}">${esc(((diagnosis.steps || []).find((step) => step.concept_id === id) || {}).name || id)}</span>`).join('<span class="faint">→</span>')}</div>` : "";
    const steps = (diagnosis.steps || []).map((step) => `<div class="study-activity ${esc(step.status)}">
      <div class="sa-head"><span class="chip ${step.status === "completed" ? "ok" : ""}">${esc(step.status === "completed" ? "Tamamlandı" : "Planlandı")}</span><span class="sa-estimate">≈ ${studyMinutes(step.estimate_minutes)} <i>(${esc(step.estimate_label || "tahmini")})</i></span></div>
      <div class="sa-title">${esc(step.name)}${step.objective ? " · hedef" : ""}</div><div class="sa-reason">${esc(step.activity)}</div>
      ${step.status !== "completed" && diagnosis.status === "located" ? `<div class="btn-row" style="justify-content:flex-start"><button type="button" class="btn btn-ghost small" data-dx-step="${esc(step.concept_id)}">Adımı tamamla</button>${!step.objective ? `<button type="button" class="btn btn-ghost small" data-dx-redirect="${esc(step.concept_id)}" title="Bu adımdan sonra doğrudan hedefe dön">Hedefe yönlendir</button>` : ""}</div>` : ""}</div>`).join("");
    return `<div class="panel med-card study-diagnosis">
      <div class="panel-title"><span class="kicker">Ön koşul teşhisi · ${esc(diagnosis.concept_name)}</span><span class="chip ${diagnosis.status === "located" ? "accent" : diagnosis.status === "closed" ? "ok" : ""}">${esc({ open: "soru soruluyor", located: "kök bulundu", closed: "kapandı", no_prerequisites: "ön koşul yok" }[diagnosis.status] || diagnosis.status)}</span></div>
      <p class="med-review-note">${esc(diagnosis.intro || "")}</p>
      ${(diagnosis.limitations || []).length ? `<div class="med-explain"><h4>Sınırlar</h4>${diagnosis.limitations.map((line) => esc(line)).join("<br>")}</div>` : ""}
      ${(diagnosis.fallback || []).length ? `<div class="med-explain"><h4>Ne yapabilirsin</h4>${diagnosis.fallback.map((line) => `• ${esc(line)}`).join("<br>")}</div>` : ""}
      <div data-prereqs="${esc(diagnosis.concept_id)}"></div>
      ${candidates ? `<h4 class="study-h4">Ön koşul soruları</h4><div class="study-steps">${candidates}</div>` : ""}
      ${path ? `<h4 class="study-h4">Yol</h4>${path}` : ""}
      ${steps ? `<div class="study-steps">${steps}</div>` : ""}
      <div class="btn-row" style="justify-content:flex-start">
        ${diagnosis.status === "open" ? '<button type="button" class="btn btn-ghost small" data-dx-act="shorten" title="Kalan soruları atla ve en yakın ön koşuldan başla">Kısalt</button>' : ""}
        ${diagnosis.status !== "closed" ? '<button type="button" class="btn btn-ghost small" data-dx-act="finish">Bitir</button>' : ""}
        <button type="button" class="btn btn-ghost small" data-dx-act="close">Kapat</button></div>
    </div>`;
  },

  checkMarkup(check) {
    if (!check) return "";
    const question = check.question || {};
    const result = check.result || null;
    return `<div class="panel med-card study-check">
      <div class="panel-title"><span class="kicker">Anlama kontrolü</span><span class="chip">${result ? (result.correct ? "doğru" : "yanlış") : "gerekçe zorunlu"}</span></div>
      <div class="mq-stem">${esc(question.stem || "")}</div>
      ${result ? "" : this.confidenceChips(null)}
      ${studyOptions(question.options, { chosen: result ? result.answer_key : null, correctKey: result ? question.correct_key : null, revealed: !!result })}
      ${result ? `<div class="med-explain">${esc(question.explanation || "")}<br><span class="chip">${esc(this.classificationLabel({ classification: result.classification }))}</span></div>`
        : `<textarea class="study-textarea" data-check-text rows="2" maxlength="1200" placeholder="Neden bu şık? (zorunlu)"></textarea>
           <div class="btn-row" style="justify-content:flex-start"><button type="button" class="btn btn-primary small" data-check-send>Cevapla</button><button type="button" class="btn btn-ghost small" data-check-close>Vazgeç</button></div>`}
      ${result ? '<div class="btn-row" style="justify-content:flex-start"><button type="button" class="btn btn-ghost small" data-check-close>Kapat</button></div>' : ""}
    </div>`;
  },

  specimenRow(specimen, { active = false } = {}) {
    return `<button type="button" class="med-row ${active ? "active" : ""}" data-specimen="${esc(specimen.specimen_id)}">
      <span class="med-row-title">${esc(specimen.label || "(adsız örnek)")}</span>
      <span class="med-row-side"><span class="chip ${specimen.status === "eligible" ? "ok" : specimen.status === "unreadable" ? "bad" : ""}">${esc(specimen.status_label)}</span></span>
      <span class="med-row-meta">${esc(specimen.document_title || "")} · s. ${specimen.page_number}${specimen.source_changed ? ' · <span class="warn-text">kaynak değişti</span>' : ""}</span></button>`;
  },

  specimenMarkup(specimen, { reveal = true } = {}) {
    if (!specimen) return medEmpty("Bir örnek seç", "Sol listeden bir örnek açınca kırpılmış görüntü, dayanağı ve özellikleri burada görünür.");
    const features = (specimen.features || []).map((item) => `<li>${esc(item)}</li>`).join("");
    const answerBlock = reveal
      ? `<div class="mq-stem">${esc(specimen.label || "Adı kaydedilmedi")}${specimen.latin ? ` <span class="faint">(${esc(specimen.latin)})</span>` : ""}</div>
         <div class="med-chips"><span class="chip ${specimen.basis === "user_confirmed" || specimen.basis === "page_caption" ? "ok" : "warn"}">${esc(specimen.basis_label)}</span>
           ${specimen.stain ? `<span class="chip">boya: ${esc(specimen.stain)}</span>` : '<span class="chip">boya bilinmiyor</span>'}
           ${specimen.magnification ? `<span class="chip">büyütme: ${esc(specimen.magnification)}</span>` : '<span class="chip">büyütme bilinmiyor</span>'}
           <span class="chip">${specimen.exposures || 0} kez gösterildi</span></div>
         ${features ? `<h4 class="study-h4">Ayırt edici özellikler</h4><ul class="study-features">${features}</ul>` : '<p class="med-review-note">Özellik kaydedilmedi.</p>'}
         ${specimen.model_description ? `<div class="med-explain"><h4>Model betimlemesi (cevap değil)</h4>${esc(specimen.model_description)}</div>` : ""}
         ${specimen.caption_excerpt ? `<div class="med-explain"><h4>Sayfa metni</h4>${esc(specimen.caption_excerpt)}</div>` : ""}
         ${specimen.notes ? `<div class="med-explain">${esc(specimen.notes)}</div>` : ""}`
      : `<div class="med-chips">${specimen.stain ? `<span class="chip">boya: ${esc(specimen.stain)}</span>` : ""}<span class="chip">${esc(specimen.status_label)}</span></div>`;
    return `<div class="study-specimen">
      <div class="study-crop" data-crop="${esc(specimen.specimen_id)}"><span class="mq-figure-wait">Görüntü yükleniyor…</span></div>
      <figcaption class="faint">${esc(specimen.document_title || "")} · s. ${specimen.page_number}${specimen.source_changed ? " · kaynak belge değişti" : ""}</figcaption>
      ${answerBlock}</div>`;
  },

  sessionItemMarkup(session, index) {
    const item = (session.items || []).find((entry) => entry.index === index) || null;
    if (!item) return medEmpty("Bu sırada örnek yok");
    const specimen = item.specimen || {};
    const answered = item.answer || null;
    const total = (session.items || []).length;
    const head = `<div class="panel-title"><span class="kicker">${session.mode === "timed" ? "Süreli pratik" : "Çalışma oturumu"} · ${index + 1}/${total}</span>
      <span>${item.scored ? '<span class="chip ok">puanlı</span>' : '<span class="chip">yalnız çalışma</span>'}${session.mode === "timed" && !answered ? ' <span id="study-timer" class="chip accent study-timer"></span>' : ""}</span></div>`;
    if (!answered) {
      return `<div class="panel med-card study-session">${head}
        ${this.specimenMarkup(specimen, { reveal: false })}
        <label class="med-field"><span>Bu nedir?</span><input id="study-answer-text" type="text" maxlength="120" placeholder="Doku ya da yapı adı" autocomplete="off"></label>
        ${this.confidenceChips(null)}
        <textarea class="study-textarea" data-session-explain rows="2" maxlength="800" placeholder="Hangi özelliklerden tanıdın? (isteğe bağlı; puanlı örneklerde değerlendirilir)"></textarea>
        <div class="btn-row" style="justify-content:flex-start"><button type="button" class="btn btn-primary small" data-session-answer>Cevapla</button>
          <button type="button" class="btn btn-ghost small" data-session-finish>Oturumu bitir</button></div></div>`;
    }
    const quality = answered.explanation_quality || "unassessed";
    return `<div class="panel med-card study-session">${head}
      ${this.specimenMarkup(specimen, { reveal: true })}
      <div class="med-explain"><h4>${answered.timed_out ? "Süre doldu" : answered.correct ? "Doğru tanıdın" : "Yanlış"}</h4>
        Cevabın: ${esc(answered.given || "(boş)")}${answered.confidence ? ` · ${esc((STUDY_CONFIDENCE.find(([key]) => key === answered.confidence) || [])[1] || answered.confidence)}` : ""}
        ${answered.explanation ? `<br>Açıklaman: ${esc(answered.explanation)}<br><span class="chip ${STUDY_QUALITY_TONE[quality] || ""}">${esc(answered.explanation_quality_label || quality)}</span>${(answered.features_named || []).length ? ` <span class="faint">adı geçen özellikler: ${answered.features_named.map((feature) => esc(feature)).join(", ")}</span>` : ""}` : ""}
        ${answered.explanation_note ? `<br><span class="faint">${esc(answered.explanation_note)}</span>` : ""}</div>
      <div class="btn-row" style="justify-content:flex-start">
        ${index + 1 < total ? '<button type="button" class="btn btn-primary small" data-session-next>Sonraki</button>' : '<button type="button" class="btn btn-primary small" data-session-finish>Sonuçları gör</button>'}
        <button type="button" class="btn btn-ghost small" data-compare="${esc(specimen.specimen_id)}">Karıştırılanlarla kıyasla</button></div></div>`;
  },

  sessionResultsMarkup(session) {
    const results = session.results || {};
    const quality = results.explanation_quality || {};
    return `<div class="panel med-card study-results">
      <div class="panel-title"><span class="kicker">Oturum sonucu</span><span class="chip">${esc(session.mode === "timed" ? "süreli" : "çalışma")}</span></div>
      <div class="med-score"><span class="ms-value">${results.identification_accuracy === null || results.identification_accuracy === undefined ? "—" : "%" + Math.round(results.identification_accuracy * 100)}</span>
        <span class="ms-note">${results.identified || 0}/${results.scored || 0} puanlı örnek tanındı · ${results.shown || 0} gösterildi${results.study_only ? ` · ${results.study_only} yalnız çalışma` : ""}</span></div>
      ${Object.keys(quality).length ? `<div class="med-chips">${Object.entries(quality).map(([key, count]) => `<span class="chip ${STUDY_QUALITY_TONE[key] || ""}">açıklama ${esc(key)} · ${count}</span>`).join("")}</div>` : '<p class="med-review-note">Açıklama değerlendirilmedi.</p>'}
      ${results.note ? `<p class="med-review-note">${esc(results.note)}</p>` : ""}
      <div class="med-bank-list">${(session.items || []).map((item) => `<div class="med-row"><span class="med-row-title">${esc((item.specimen || {}).label || "(adsız)")}</span>
        <span class="med-row-side">${item.answer ? (item.answer.timed_out ? "süre doldu" : item.answer.correct ? "✓" : "✗") : "—"}</span>
        <span class="med-row-meta">${item.answer ? esc(item.answer.given || "(boş)") : "cevaplanmadı"}${item.scored ? "" : " · puansız"}</span></div>`).join("")}</div>
      <div class="btn-row" style="justify-content:flex-start"><button type="button" class="btn btn-ghost small" data-session-close>Kapat</button></div></div>`;
  },

  reasoningPrompts(exam) {
    const analysis = (exam && exam.analysis) || {};
    const events = analysis.events || {};
    const wrong = (exam.questions || []).filter((question) => question.answer && question.correct === false && events[question.question_id]).slice(0, 5);
    if (!wrong.length) return "";
    return `<div class="panel med-card study-reasons"><div class="panel-title"><span class="kicker">Yanlışların için gerekçe</span><span class="faint">isteğe bağlı</span></div>
      <p class="med-review-note">Neden o şıkkı seçtiğini bir iki cümleyle yaz. JARVIS gerekçeyi ders kaynağına göre değerlendirir; tutarlı bir yanlış anlama görürse Anlama ekranında bulgu açar. Puanı değiştirmez.</p>
      ${wrong.map((question) => {
        const eventId = events[question.question_id];
        const sent = this.sentReasons[eventId];
        return `<div class="study-reason" data-reason="${esc(eventId)}">
          <div class="mb-stem">${esc(question.stem)}</div>
          ${sent ? `<span class="chip ${sent === "pending" ? "" : "warn"}">${esc(sent === "pending" ? "Değerlendiriliyor…" : sent)}</span>`
            : `<textarea class="study-textarea" data-reason-text rows="2" maxlength="1200" placeholder="Çünkü…"></textarea>
               <div class="btn-row" style="justify-content:flex-start"><button type="button" class="btn btn-ghost small" data-reason-send="${esc(eventId)}">Gönder</button></div>`}
        </div>`;
      }).join("")}</div>`;
  },

  /* ── dialogs ─────────────────────────────────────────────────── */

  dialog({ title, html, okLabel = "KAYDET", danger = false, collect }) {
    return new Promise((resolve) => {
      const veil = $("#confirm");
      const ok = $("#confirm-ok"), cancel = $("#confirm-cancel");
      const textNode = $("#confirm-text");
      if (!veil || !ok || !cancel || !textNode) { resolve(null); return; }
      $("#confirm-title").textContent = title;
      textNode.innerHTML = html;
      ok.textContent = okLabel;
      cancel.textContent = "VAZGEÇ";
      ok.className = danger ? "btn btn-danger" : "btn btn-primary";
      const finish = (value) => {
        ok.onclick = null; cancel.onclick = null;
        window.removeEventListener("keydown", onKey, true);
        veil.hidden = true;
        textNode.textContent = "";
        resolve(value);
      };
      const onKey = (event) => { if (event.key === "Escape") { event.preventDefault(); event.stopPropagation(); finish(null); } };
      ok.onclick = () => finish(collect(textNode));
      cancel.onclick = () => finish(null);
      window.addEventListener("keydown", onKey, true);
      veil.hidden = false;
      const modal = veil.querySelector(".modal");
      if (modal && typeof Motion !== "undefined") Motion.rise(modal, { y: 14, scale: 0.97, duration: Motion.panel });
      const first = textNode.querySelector("input, textarea, select");
      if (first) first.focus();
    });
  },

  noteDialog(title, placeholder, { okLabel = "KAYDET", required = false } = {}) {
    return this.dialog({
      title,
      okLabel,
      html: `<textarea id="study-dialog-note" class="mem-edit study-textarea" rows="4" maxlength="400" placeholder="${esc(placeholder)}"></textarea>`,
      collect: (host) => {
        const node = host.querySelector("#study-dialog-note");
        const text = ((node && node.value) || "").trim();
        return required && !text ? null : text;
      },
    });
  },

  flagDialog() {
    return this.dialog({
      title: "Soruda hata olabilir",
      okLabel: "İŞARETLE",
      html: `<p>Neyin yanlış olabileceğini seç; not isteğe bağlı. İşaret soruyu değiştirmez, soru bankasında ve incelemede görünür.</p>
        <div class="study-flag-kinds">${STUDY_FLAG_KINDS.map(([key, label], index) => `<label class="switch-row small"><span>${label}</span><input type="radio" name="study-flag-kind" value="${key}" ${index === 0 ? "checked" : ""}></label>`).join("")}</div>
        <textarea id="study-flag-note" class="mem-edit study-textarea" rows="3" maxlength="400" placeholder="Not (isteğe bağlı): sence doğru cevap ne, neden?"></textarea>`,
      collect: (host) => {
        const picked = host.querySelector('input[name="study-flag-kind"]:checked');
        const note = host.querySelector("#study-flag-note");
        return { kind: picked ? picked.value : STUDY_FLAG_KINDS[0][0], note: ((note && note.value) || "").trim() };
      },
    });
  },

  /* ── questions: flags, review, invalidation, reasoning ───────── */

  async flagQuestion(questionId, context = {}) {
    const choice = await this.flagDialog();
    if (!choice) return;
    const result = await this.request("flag_question", { question_id: questionId, kind: choice.kind, note: choice.note, exam_id: context.exam_id || null, attempt_id: context.attempt_id || null });
    if (result.ok === false) { toast(result.error || "İşaret kaydedilemedi.", true); return; }
    toast(`İşaretlendi: ${(result.flag && result.flag.kind_label) || "soruda hata olabilir"}.`, "ok");
    if (this.viewIs("bank")) Medical.loadBank();
  },

  async reviewQuestion(questionId) {
    const result = await this.request("question_review", { question_id: questionId });
    if (result.ok === false) { toast(result.error || "İnceleme başlatılamadı.", true); return; }
    toast(result.message || "Kaynak desteği inceleniyor.", "ok");
  },

  async invalidateQuestion(questionId) {
    const reason = await this.noteDialog("Soru geçersiz sayılsın mı?", "Neden? (ör. anahtar hatalı, iki doğru şık var)", { okLabel: "GEÇERSİZ SAY", required: true });
    if (reason === null) return;
    const result = await this.request("invalidate_question", { question_id: questionId, reason: reason || "Öğrenci geçersiz saydı.", confirmed: true });
    if (result.ok === false) { toast(result.error || "Soru geçersiz sayılamadı.", true); return; }
    const corrected = (result.mastery_corrected || []).length;
    const withdrawn = (result.understanding || {}).findings || 0;
    toast(`Soru geçersiz sayıldı${corrected ? `; ${corrected} kavramın ustalığı düzeltildi` : ""}${withdrawn ? `; ${withdrawn} bulgu geri çekildi` : ""}. Deneme kayıtları korunur.`, "ok");
    if (this.viewIs("bank")) Medical.loadBank();
  },

  async sendReasoning(eventId, text) {
    const reasoning = String(text || "").trim();
    if (!reasoning) { toast("Önce gerekçeyi yaz.", true); return false; }
    const saved = await this.request("understanding_explain", { event_id: eventId, reasoning });
    if (saved.ok === false) { toast(saved.error || "Gerekçe kaydedilemedi.", true); return false; }
    const started = await this.request("understanding_assess", { event_id: eventId });
    if (started.ok === false) { toast(started.error || "Değerlendirme başlatılamadı; gerekçe kaydedildi.", true); return true; }
    toast(started.message || "Gerekçe değerlendiriliyor.", "ok");
    return true;
  },

  pendingFor(question) { return question ? this.pendingExplain[question.question_id] || null : null; },

  notePending(question, result) {
    if (!question || !result) return;
    if (result.confidence !== undefined) question.confidence = result.confidence;
    if (result.explain && result.event_id) {
      this.pendingExplain[question.question_id] = { event_id: result.event_id, reason: result.explain_reason || "sample" };
    }
  },

  async sendExplanation(question, text) {
    const pending = this.pendingFor(question);
    if (!pending) return;
    const ok = await this.sendReasoning(pending.event_id, text);
    if (!ok) return;
    pending.sent = true;
    pending.text = String(text || "").trim();
    Medical.renderRunner();
  },

  skipExplanation(question) {
    delete this.pendingExplain[question.question_id];
    Medical.renderRunner();
  },

  bindRunner(host, question) {
    $$("[data-confidence]", host).forEach((node) => node.addEventListener("click", () => {
      question.confidence = node.dataset.confidence;
      $$("[data-confidence]", host).forEach((chip) => chip.classList.toggle("active", chip === node));
      $$("[data-confidence]", host).forEach((chip) => chip.classList.toggle("accent", chip === node));
      if (question.answer && Medical.exam && !Medical.exam.config.immediate_feedback) Medical.answer(question.question_id, question.answer);
    }));
    const send = host.querySelector("[data-explain-send]");
    if (send) send.addEventListener("click", () => { const box = host.querySelector("[data-explain-text]"); this.sendExplanation(question, box ? box.value : ""); });
    const skip = host.querySelector("[data-explain-skip]");
    if (skip) skip.addEventListener("click", () => this.skipExplanation(question));
  },

  bindResults(host, exam) {
    $$("[data-reason-send]", host).forEach((node) => node.addEventListener("click", async () => {
      const row = node.closest("[data-reason]");
      const box = row ? row.querySelector("[data-reason-text]") : null;
      const eventId = node.dataset.reasonSend;
      const ok = await this.sendReasoning(eventId, box ? box.value : "");
      if (!ok) return;
      this.sentReasons[eventId] = "pending";
      Medical.renderRunner();
    }));
    $$("[data-flag-question]", host).forEach((node) => node.addEventListener("click", () =>
      this.flagQuestion(node.dataset.flagQuestion, { exam_id: exam.exam_id, attempt_id: exam.attempt ? exam.attempt.attempt_id : null })));
  },

  onAssessed(event) {
    const label = this.classificationLabel(event);
    const note = (event.assessment && event.assessment.suspected_misconception) ? `${label} · olası yanlış anlama: ${event.assessment.suspected_misconception}` : label;
    if (event.event_id && this.sentReasons[event.event_id] !== undefined) this.sentReasons[event.event_id] = label;
    const pending = this.pendingExplain[event.question_id];
    if (pending && pending.event_id === event.event_id) { pending.note = note; pending.sent = true; }
    if (Medical.exam) {
      const question = (Medical.exam.questions || []).find((item) => item.question_id === event.question_id);
      if (question) question.assessment = event;
    }
    if (this.check && this.check.result && this.check.result.event_id === event.event_id) { this.check.result.classification = event.classification; }
    toast(`Gerekçe değerlendirildi: ${note}`, String(event.classification || "").startsWith("wrong") || event.classification === "correct_contradictory" ? "" : "ok");
    if (this.viewIs("exam")) Medical.renderRunner();
    if (this.viewIs("understanding")) this.openUnderstanding();
  },

  /* ── dashboard cards ─────────────────────────────────────────── */

  renderDashboardCards(study) {
    const todayHost = $("#med-today");
    if (todayHost) {
      const today = (study && study.today) || null;
      todayHost.innerHTML = this.todayMarkup(today, { compact: true });
      this.bindToday(todayHost);
    }
    const note = $("#med-today-note");
    if (note) { const today = study && study.today; note.textContent = today && today.date ? studyDate(today.date) : ""; }
    const host = $("#med-understanding-card");
    if (host) {
      if (!study) { host.innerHTML = medEmpty("Anlama verisi yok"); return; }
      const histology = study.histology || {};
      host.innerHTML = `<div class="med-chips">
          <span class="chip ${study.findings_active ? "bad" : study.findings_open ? "warn" : "ok"}">${study.findings_open || 0} açık bulgu${study.findings_active ? ` · ${study.findings_active} desteklenen` : ""}</span>
          <span class="chip ${study.open_flags ? "warn" : ""}">${study.open_flags || 0} soru işareti</span>
          <span class="chip">${(histology.eligible || 0) + (histology.study_only || 0)} histoloji örneği${histology.eligible ? ` · ${histology.eligible} sınava uygun` : ""}</span>
          <span class="chip">${study.plans || 0} plan</span></div>
        <div class="btn-row" style="justify-content:flex-start">
          <button type="button" class="btn btn-ghost small" data-study-go="understanding">Bulgular</button>
          <button type="button" class="btn btn-ghost small" data-study-check title="Bankadan bir soru: cevap, güven ve gerekçe birlikte kaydedilir">Anlama kontrolü</button>
          <button type="button" class="btn btn-ghost small" data-study-go="histology">Histoloji</button></div>`;
      $$("[data-study-go]", host).forEach((node) => node.addEventListener("click", () => Medical.show(node.dataset.studyGo)));
      const check = host.querySelector("[data-study-check]");
      if (check) check.addEventListener("click", () => { Medical.show("understanding"); this.startCheck(); });
    }
  },

  bindToday(host) {
    $$("[data-study-go]", host).forEach((node) => node.addEventListener("click", () => Medical.show(node.dataset.studyGo)));
    $$("[data-activity-run]", host).forEach((node) => node.addEventListener("click", () => this.runActivity(node.dataset.activityRun)));
    $$("[data-activity-done]", host).forEach((node) => node.addEventListener("click", () => this.activityAction("plan_activity_complete", node.dataset.activityDone)));
    $$("[data-activity-skip]", host).forEach((node) => node.addEventListener("click", () => this.activityAction("plan_activity_skip", node.dataset.activitySkip)));
  },

  findActivity(activityId) {
    const pools = [];
    if (Medical.state && Medical.state.study && Medical.state.study.today) pools.push(Medical.state.study.today.activities || []);
    if (this.plan && this.plan.today_view) pools.push(this.plan.today_view.activities || []);
    if (this.plan && this.plan.days) this.plan.days.forEach((day) => pools.push(day.activities || []));
    for (const pool of pools) {
      const hit = pool.find((item) => item.activity_id === activityId);
      if (hit) return hit;
    }
    return null;
  },

  async activityAction(action, activityId, extra = {}) {
    const result = await this.request(action, { activity_id: activityId, plan_id: this.plan ? this.plan.plan_id : null, ...extra });
    if (result.ok === false) { toast(result.error || "Etkinlik güncellenemedi.", true); return null; }
    if (result.today && Medical.state && Medical.state.study) Medical.state.study.today = result.today;
    if (this.viewIs("plan")) await this.openPlan();
    else if (this.viewIs("dashboard")) this.renderDashboardCards(Medical.state && Medical.state.study);
    return result;
  },

  async runActivity(activityId) {
    const activity = this.findActivity(activityId);
    const result = await this.activityAction("plan_activity_start", activityId);
    if (!result) return;
    const item = (result.activity) || activity;
    if (!item) return;
    if (item.kind === "repair") { Medical.show("understanding"); await this.openUnderstanding(); const finding = (this.understanding && this.understanding.findings || []).find((entry) => entry.concept_id === item.concept_id || entry.topic_id === item.topic_id); if (finding) this.openFinding(finding.finding_id); return; }
    if (item.kind === "prerequisite") { Medical.show("understanding"); if (item.concept_id) this.startDiagnosis(item.concept_id, "Plandaki ön koşul kontrolü"); return; }
    if (item.kind === "read") { Medical.show("library"); toast(`${item.title}: ilgili belgeyi Kütüphane'den aç; okuduğun sayfalar plana işlenir.`, "ok"); return; }
    if (item.kind === "recap") { Medical.quickAsk(`${item.title} konusunu kısaca hatırlat`); return; }
    Medical.quickAsk(`${item.title} konusundan 5 soruluk kısa test hazırla`);
  },

  /* ── plan view ───────────────────────────────────────────────── */

  async openPlan() {
    const result = await this.request("plans", {});
    if (result.ok === false) { toast(result.error || "Planlar okunamadı.", true); return; }
    this.plans = result.plans || [];
    const subjects = $("#med-plan-subjects");
    if (subjects && !subjects.options.length) {
      const options = ((Medical.state && Medical.state.session && Medical.state.session.options) || {}).subjects || [];
      subjects.innerHTML = options.map((item) => `<option value="${esc(item.value)}">${esc(item.label)}</option>`).join("");
    }
    const date = $("#med-plan-date");
    if (date && !date.value && this.plans.length === 0) {
      const soon = new Date(); soon.setDate(soon.getDate() + 14);
      date.value = soon.toISOString().slice(0, 10);
    }
    if (this.plans.length && !(this.plan && this.plans.some((item) => item.plan_id === this.plan.plan_id))) this.plan = this.plans[0];
    else if (this.plan) this.plan = this.plans.find((item) => item.plan_id === this.plan.plan_id) || null;
    this.renderPlans();
    await this.renderPlanDetail();
  },

  renderPlans() {
    const host = $("#med-plan-list");
    const count = $("#med-plan-count");
    if (count) count.textContent = this.plans.length ? `${this.plans.length} plan` : "";
    if (!host) return;
    host.innerHTML = this.plans.length
      ? this.plans.map((plan) => `<button type="button" class="med-row ${this.plan && this.plan.plan_id === plan.plan_id ? "active" : ""}" data-plan="${esc(plan.plan_id)}">
          <span class="med-row-title">${esc(plan.name)}</span><span class="med-row-side">${plan.days_left} gün</span>
          <span class="med-row-meta">${esc(studyDate(plan.exam_date))}${plan.scope_confirmed ? "" : ' · <span class="warn-text">kapsam onay bekliyor</span>'}${plan.fit ? "" : ' · <span class="warn-text">sığmıyor</span>'}</span></button>`).join("")
      : medEmpty("Sınav planı yok", "Aşağıdaki formdan sınavı, tarihini ve günlük süreyi gir.");
    $$("[data-plan]", host).forEach((node) => node.addEventListener("click", () => { this.plan = this.plans.find((item) => item.plan_id === node.dataset.plan) || null; this.renderPlans(); this.renderPlanDetail(); }));
  },

  async renderPlanDetail() {
    const host = $("#med-plan-detail");
    if (!host) return;
    const plan = this.plan;
    if (!plan) { host.innerHTML = medEmpty("Plan seçilmedi", "Bir plan oluşturunca bugünün etkinlikleri, kapsam durumu ve haftalık yerleşim burada görünür."); return; }
    if (!plan.scope_confirmed) {
      const proposed = plan.proposed_scope || {};
      host.innerHTML = `<div class="panel med-card"><div class="panel-title"><span class="kicker">${esc(plan.name)}</span><span class="chip warn">kapsam onay bekliyor</span></div>
        <p class="med-review-note">Kapsam kütüphanendeki belgelerden önerildi. Onaylamadan plan yapılmaz.</p>
        <div class="med-chips">${(proposed.subjects || []).map((item) => `<span class="chip accent">${esc(item)}</span>`).join("")}${(proposed.document_ids || []).map((item) => `<span class="chip">${esc(item)}</span>`).join("")}${(proposed.topic_ids || []).map((item) => `<span class="chip violet">${esc(item)}</span>`).join("")}</div>
        <div class="btn-row" style="justify-content:flex-start"><button type="button" class="btn btn-primary small" data-plan-act="confirm">Kapsamı onayla</button><button type="button" class="btn btn-ghost small" data-plan-act="delete">Planı sil</button></div></div>`;
      this.bindPlanDetail(host);
      return;
    }
    const today = await this.request("plan_today", { plan_id: plan.plan_id });
    if (today.ok === false) { toast(today.error || "Bugün görünümü okunamadı.", true); return; }
    // Reading today may have planned a new day; the header must say what the
    // plan looks like after that, not before.
    const fresh = await this.request("plan", { plan_id: plan.plan_id });
    if (fresh.ok !== false && fresh.plan) Object.assign(plan, fresh.plan);
    plan.today_view = today.today;
    const weekdays = (plan.budget && plan.budget.weekdays) || {};
    const budgetLine = ["Pzt", "Sal", "Çar", "Per", "Cum", "Cmt", "Paz"].map((name, index) => `${name} ${weekdays[String(index)] !== undefined ? weekdays[String(index)] : 45}`).join(" · ");
    host.innerHTML = `
      <div class="panel med-card"><div class="panel-title"><span class="kicker">${esc(plan.name)}</span><span class="faint">${esc(studyDate(plan.exam_date))} · ${plan.days_left} gün kaldı</span></div>
        <div class="med-chips"><span class="chip ${plan.fit ? "ok" : "bad"}">${plan.fit ? "kapsam süreye sığıyor" : "kapsam sığmıyor"}</span><span class="chip">${studyMinutes(plan.needed_minutes)} iş · ${studyMinutes(plan.available_minutes)} boş</span><span class="chip">${plan.planned} planlı etkinlik</span>${plan.notify && plan.notify.enabled ? `<span class="chip">hatırlatma ${esc(plan.notify.time || "09:00")}</span>` : ""}</div>
        ${plan.overload ? `<p class="med-review-note warn-text">${esc(plan.overload.message)}</p>` : ""}
        <p class="faint">Günlük süre (dk): ${esc(budgetLine)}${(plan.budget && (plan.budget.unavailable || []).length) ? ` · boş günler: ${plan.budget.unavailable.map((day) => esc(day)).join(", ")}` : ""}</p>
        <div class="btn-row" style="justify-content:flex-start">
          <button type="button" class="btn btn-ghost small" data-plan-act="replan">Yeniden planla</button>
          <button type="button" class="btn btn-ghost small" data-plan-act="budget">Günlük süreyi değiştir</button>
          <button type="button" class="btn btn-ghost small" data-plan-act="dayoff">Bugünü boş bırak</button>
          <button type="button" class="btn btn-ghost small" data-plan-act="manual">Elle etkinlik ekle</button>
          <button type="button" class="btn btn-ghost small" data-plan-act="delete">Planı sil</button></div></div>
      <div class="panel med-card"><div class="panel-title"><span class="kicker">Bugün</span><span class="faint">${esc(studyDate(today.today.date))}</span></div>${this.todayMarkup(today.today)}</div>
      <div class="panel med-card"><div class="panel-title"><span class="kicker">Kapsam</span><span class="faint">${plan.coverage ? plan.coverage.total : 0} konu</span></div>${this.coverageMarkup(plan.coverage)}
        ${(plan.uncovered || []).length ? `<h4 class="study-h4">Açıkta kalanlar</h4>${plan.uncovered.map((item) => `<div class="med-row"><span class="med-row-title">${esc(item.title)}</span><span class="med-row-side">≈ ${studyMinutes(item.estimate_minutes)}</span><span class="med-row-meta">${esc(item.reason)}</span></div>`).join("")}` : ""}</div>
      <div class="panel med-card"><div class="panel-title"><span class="kicker">Bu hafta</span></div>${this.daysMarkup(plan.days)}</div>
      ${(plan.history || []).length ? `<div class="panel med-card"><div class="panel-title"><span class="kicker">Plan geçmişi</span></div>${plan.history.slice(-6).reverse().map((item) => `<div class="med-row"><span class="med-row-sub">${esc(String(item.at || "").slice(0, 16).replace("T", " "))} · ${esc(item.note)}</span></div>`).join("")}</div>` : ""}`;
    this.bindPlanDetail(host);
    this.bindToday(host);
  },

  bindPlanDetail(host) {
    $$("[data-plan-act]", host).forEach((node) => node.addEventListener("click", () => this.planAction(node.dataset.planAct)));
  },

  async planAction(action) {
    const plan = this.plan;
    if (!plan) return;
    if (action === "confirm") {
      const result = await this.request("plan_confirm_scope", { plan_id: plan.plan_id });
      if (result.ok === false) { toast(result.error || "Kapsam onaylanamadı.", true); return; }
      toast("Kapsam onaylandı; plan yapıldı.", "ok");
      await this.openPlan();
      return;
    }
    if (action === "delete") {
      const ok = await confirmDialog({ title: "Plan silinsin mi?", body: `${plan.name} planı ve etkinlikleri kaldırılacak. Çalışma kayıtları ve sınavlar kalır.`, confirmLabel: "SİL", danger: true });
      if (!ok) return;
      const result = await this.request("plan_delete", { plan_id: plan.plan_id, confirmed: true });
      if (result.ok === false) { toast(result.error || "Plan silinemedi.", true); return; }
      this.plan = null;
      await this.openPlan();
      Medical.refresh();
      return;
    }
    if (action === "replan") {
      const result = await this.request("plan_replan", { plan_id: plan.plan_id, reason: "öğrenci istedi" });
      if (result.ok === false) { toast(result.error || "Yeniden planlanamadı.", true); return; }
      toast("Plan yenilendi; tamamlananlar ve elle eklenenler korundu.", "ok");
      await this.openPlan();
      return;
    }
    if (action === "budget") {
      const minutes = await this.dialog({
        title: "Günlük süre",
        okLabel: "UYGULA",
        html: `<p>Hafta içi ve hafta sonu için dakika. Plan yeniden yapılır; bitenler korunur.</p>
          <label class="med-field"><span>Hafta içi</span><input id="study-budget-week" type="number" min="0" max="600" value="${Number(((plan.budget || {}).weekdays || {})["0"] !== undefined ? plan.budget.weekdays["0"] : 45)}"></label>
          <label class="med-field"><span>Hafta sonu</span><input id="study-budget-weekend" type="number" min="0" max="600" value="${Number(((plan.budget || {}).weekdays || {})["5"] !== undefined ? plan.budget.weekdays["5"] : 45)}"></label>`,
        collect: (host) => ({ week: Number(host.querySelector("#study-budget-week").value), weekend: Number(host.querySelector("#study-budget-weekend").value) }),
      });
      if (!minutes) return;
      const weekdays = {};
      [0, 1, 2, 3, 4].forEach((day) => { weekdays[String(day)] = minutes.week; });
      [5, 6].forEach((day) => { weekdays[String(day)] = minutes.weekend; });
      const result = await this.request("plan_update", { plan_id: plan.plan_id, fields: { daily_minutes: weekdays } });
      if (result.ok === false) { toast(result.error || "Süre güncellenemedi.", true); return; }
      toast("Günlük süre güncellendi.", "ok");
      await this.openPlan();
      return;
    }
    if (action === "dayoff") {
      const day = (plan.today_view && plan.today_view.date) || plan.today;
      const unavailable = Array.from(new Set([...(((plan.budget || {}).unavailable) || []), day]));
      const result = await this.request("plan_update", { plan_id: plan.plan_id, fields: { unavailable } });
      if (result.ok === false) { toast(result.error || "Gün boş bırakılamadı.", true); return; }
      toast("Bugün boş bırakıldı; kalan iş sonraki günlere yayıldı.", "ok");
      await this.openPlan();
      return;
    }
    if (action === "manual") {
      const entry = await this.dialog({
        title: "Elle etkinlik ekle",
        okLabel: "EKLE",
        html: `<label class="med-field"><span>Başlık</span><input id="study-manual-title" type="text" maxlength="80" placeholder="Ör. Hocanın çıkmış sorularını çöz"></label>
          <label class="med-field"><span>Gün</span><input id="study-manual-day" type="date" value="${esc((plan.today_view && plan.today_view.date) || plan.today || "")}"></label>
          <label class="med-field"><span>Süre (dk, senin tahminin)</span><input id="study-manual-minutes" type="number" min="5" max="600" value="20"></label>`,
        collect: (host) => ({ title: host.querySelector("#study-manual-title").value.trim(), day: host.querySelector("#study-manual-day").value, minutes: Number(host.querySelector("#study-manual-minutes").value) }),
      });
      if (!entry) return;
      if (!entry.title || !entry.day) { toast("Başlık ve gün gerekli.", true); return; }
      const result = await this.request("plan_manual", { plan_id: plan.plan_id, title: entry.title, day: entry.day, minutes: entry.minutes, kind: "read" });
      if (result.ok === false) { toast(result.error || "Etkinlik eklenemedi.", true); return; }
      toast("Etkinlik eklendi.", "ok");
      await this.openPlan();
    }
  },

  async createPlan(event) {
    if (event) event.preventDefault();
    const name = ($("#med-plan-name") && $("#med-plan-name").value || "").trim();
    const date = $("#med-plan-date") && $("#med-plan-date").value;
    const subjectsNode = $("#med-plan-subjects");
    const subjects = subjectsNode ? Array.from(subjectsNode.selectedOptions || []).map((option) => option.value) : [];
    const minutes = Number($("#med-plan-minutes") && $("#med-plan-minutes").value) || 45;
    const notify = $("#med-plan-notify") && $("#med-plan-notify").checked;
    const time = ($("#med-plan-notify-time") && $("#med-plan-notify-time").value) || "09:00";
    if (!name || !date) { toast("Sınav adı ve tarihi gerekli.", true); return; }
    const result = await this.request("plan_create", { name, exam_date: date, subjects, daily_minutes: minutes, notify: { enabled: !!notify, time } });
    if (result.ok === false) { toast(result.error || "Plan oluşturulamadı.", true); return; }
    this.plan = result.plan;
    toast(result.plan && result.plan.scope_confirmed ? "Plan hazır; bugünün etkinlikleri yerleşti." : "Plan oluşturuldu; önerilen kapsamı onayla.", "ok");
    if ($("#med-plan-name")) $("#med-plan-name").value = "";
    await this.openPlan();
    Medical.refresh();
  },

  /* ── understanding view ───────────────────────────────────────── */

  async openUnderstanding() {
    const [overview, struggling] = await Promise.all([this.request("understanding_overview", {}), this.request("diagnosis_struggling", {})]);
    if (overview.ok === false) { toast(overview.error || "Bulgular okunamadı.", true); return; }
    this.understanding = overview;
    if (struggling.ok !== false) { this.struggling = struggling.struggling || []; this.recentDiagnoses = struggling.recent || []; }
    this.renderFindings();
    if (this.finding) {
      const fresh = [...(overview.findings || []), ...(overview.closed || [])].find((item) => item.finding_id === this.finding.finding_id);
      if (fresh) this.finding = fresh;
    }
    this.renderUnderstandingDetail();
  },

  renderFindings() {
    const overview = this.understanding || {};
    const host = $("#med-und-findings");
    const count = $("#med-und-count");
    const summary = $("#med-und-summary");
    const counts = overview.counts || {};
    if (count) count.textContent = (overview.findings || []).length ? `${overview.findings.length} açık` : "";
    if (summary) summary.innerHTML = Object.entries(counts).map(([status, number]) => `<span class="chip ${STUDY_FINDING_TONE[status] || ""}">${esc(({ hypothesis: "Hipotez", supported: "Desteklenen", disputed: "İtiraz", repair_demonstrated: "Onarım gösterildi", resolved: "Çözüldü", reopened: "Yeniden açıldı", dismissed: "Yok sayıldı", withdrawn: "Geri çekildi" })[status] || status)} · ${number}</span>`).join("") || '<span class="chip ok">Açık bulgu yok</span>';
    if (host) {
      const open = overview.findings || [];
      const closed = overview.closed || [];
      host.innerHTML = (open.length ? open.map((item) => this.findingRow(item, { active: this.finding && this.finding.finding_id === item.finding_id })).join("") : medEmpty("Açık bulgu yok", "Sınavlarda ve anlama kontrollerinde gerekçen kaynağa göre okunur; tutarlı bir yanlış anlama görülürse burada bulgu açılır."))
        + (closed.length ? `<div class="med-row"><span class="med-row-sub">Kapananlar</span></div>` + closed.slice(0, 8).map((item) => this.findingRow(item, { active: this.finding && this.finding.finding_id === item.finding_id })).join("") : "");
      $$("[data-finding]", host).forEach((node) => node.addEventListener("click", () => this.openFinding(node.dataset.finding)));
    }
    const strugglingHost = $("#med-und-struggling");
    if (strugglingHost) {
      strugglingHost.innerHTML = this.struggling.length
        ? this.struggling.map((item) => `<div class="med-row"><span class="med-row-title">${esc(item.name)}</span>
            <span class="med-row-side"><button type="button" class="chip" data-diagnose="${esc(item.concept_id)}" title="${item.prerequisites ? `${item.prerequisites} ön koşul kayıtlı` : "ön koşul kaydı yok"}">Ön koşulu teşhis et</button></span>
            <span class="med-row-meta">${esc(item.reason)}</span></div>`).join("")
        : medEmpty("Zorlanılan kavram işaretlenmedi");
      $$("[data-diagnose]", strugglingHost).forEach((node) => node.addEventListener("click", () => this.startDiagnosis(node.dataset.diagnose, "Zorlanılan kavram")));
    }
    const eventsHost = $("#med-und-events");
    if (eventsHost) {
      const events = overview.recent_events || [];
      eventsHost.innerHTML = events.length
        ? events.slice(0, 12).map((event) => `<div class="med-row ${event.invalidated ? "faint" : ""}"><span class="med-row-title">${esc(event.stem || "")}</span>
            <span class="med-row-side">${event.correct === true ? "✓" : event.correct === false ? "✗" : "—"}${event.confidence_label ? ` · ${esc(event.confidence_label)}` : ""}</span>
            <span class="med-row-meta">${esc(event.classification_label || "")}${event.invalidated ? " · soru geçersiz sayıldı" : ""}</span></div>`).join("")
        : medEmpty("Kayıt yok", "Cevapların güven ve gerekçeyle birlikte burada birikir.");
    }
  },

  async openFinding(findingId) {
    const result = await this.request("understanding_finding", { finding_id: findingId });
    if (result.ok === false) { toast(result.error || "Bulgu okunamadı.", true); return; }
    this.finding = result.finding;
    this.findingEvents = result.events || [];
    this.repair = null;
    this.diagnosis = null;
    this.check = null;
    this.renderFindings();
    this.renderUnderstandingDetail();
  },

  renderUnderstandingDetail() {
    const host = $("#med-und-detail");
    if (!host) return;
    if (this.check) { host.innerHTML = this.checkMarkup(this.check); this.bindCheck(host); return; }
    if (this.diagnosis) { host.innerHTML = this.diagnosisMarkup(this.diagnosis); this.bindDiagnosis(host); this.renderPrerequisites(host); return; }
    if (this.repair) { host.innerHTML = this.repairMarkup(this.repair); this.bindRepair(host); return; }
    host.innerHTML = this.findingMarkup(this.finding);
    this.bindFinding(host);
    this.renderPrerequisites(host);
  },

  bindSources(host) {
    $$("[data-source]", host).forEach((node) => node.addEventListener("click", () => {
      const [documentId, page] = node.dataset.source.split("|");
      Medical.show("library");
      Medical.openDocument(documentId).then(() => Medical.openPage(Number(page)));
    }));
  },

  bindFinding(host) {
    this.bindSources(host);
    $$("[data-repair]", host).forEach((node) => node.addEventListener("click", () => this.openRepair(node.dataset.repair)));
    $$("[data-finding-act]", host).forEach((node) => node.addEventListener("click", () => this.findingAction(node.dataset.findingAct, host)));
  },

  async findingAction(action, host) {
    const finding = this.finding;
    if (!finding) return;
    const id = finding.finding_id;
    if (action === "repair") {
      if (finding.repair && finding.repair.session_id) { await this.openRepair(finding.repair.session_id); return; }
      const result = await this.request("repair_start", { finding_id: id });
      if (result.ok === false) { toast(result.error || "Onarım başlatılamadı.", true); return; }
      toast(result.message || "Onarım oturumu hazırlanıyor.", "ok");
      return;
    }
    if (action === "diagnostic_ask") {
      const result = await this.request("diagnostic_ask", { finding_id: id });
      if (result.ok === false) { toast(result.error || "Teşhis sorusu hazırlanamadı.", true); return; }
      toast(result.message || "Teşhis sorusu hazırlanıyor.", "ok");
      return;
    }
    if (action === "diagnostic_answer") {
      const box = host.querySelector("[data-diagnostic-text]");
      const answer = ((box && box.value) || "").trim();
      if (!answer) { toast("Önce cevabı yaz.", true); return; }
      const result = await this.request("diagnostic_answer", { finding_id: id, answer });
      if (result.ok === false) { toast(result.error || "Cevap gönderilemedi.", true); return; }
      toast(result.message || "Teşhis cevabı değerlendiriliyor.", "ok");
      return;
    }
    if (action === "diagnosis") { await this.startDiagnosis(finding.concept_id, `Bulgu: ${finding.statement}`); return; }
    if (action === "challenge" || action === "dismiss" || action === "reopen") {
      const titles = { challenge: "İtiraz notu", dismiss: "Neden yok sayıyorsun?", reopen: "Yeniden açma notu" };
      const note = await this.noteDialog(titles[action], "Kısaca…", { okLabel: "KAYDET" });
      if (note === null) return;
      const result = await this.request(`understanding_${action}`, { finding_id: id, note });
      if (result.ok === false) { toast(result.error || "Bulgu güncellenemedi.", true); return; }
      this.finding = result.finding;
      toast(`Bulgu: ${result.finding.status_label}.`, "ok");
      await this.openUnderstanding();
    }
  },

  async openRepair(sessionId) {
    const result = await this.request("repair_get", { session_id: sessionId });
    if (result.ok === false) { toast(result.error || "Onarım oturumu okunamadı.", true); return; }
    this.repair = result.session;
    this.diagnosis = null;
    this.check = null;
    this.renderUnderstandingDetail();
  },

  bindRepair(host) {
    this.bindSources(host);
    const session = this.repair;
    let confidence = null;
    $$("[data-confidence]", host).forEach((node) => node.addEventListener("click", () => {
      confidence = node.dataset.confidence;
      $$("[data-confidence]", host).forEach((chip) => { chip.classList.toggle("active", chip === node); chip.classList.toggle("accent", chip === node); });
    }));
    $$("[data-repair-step]", host).forEach((node) => node.addEventListener("click", async () => {
      const result = await this.request("repair_step", { session_id: session.session_id, step: node.dataset.repairStep });
      if (result.ok === false) { toast(result.error || "Adım kaydedilemedi.", true); return; }
      this.repair = result.session;
      this.renderUnderstandingDetail();
    }));
    $$("[data-study-option]", host).forEach((node) => node.addEventListener("click", async () => {
      const key = node.dataset.studyOption;
      const result = await this.request("repair_answer", { session_id: session.session_id, answer_key: key, confidence, submission_id: this.submissionId(session.session_id, "transfer", key) });
      if (result.ok === false) { toast(result.error || "Cevap kaydedilemedi.", true); return; }
      this.repair = result.session;
      if (result.finding) { this.finding = result.finding; toast(`Bulgu: ${result.finding.status_label}.`, result.session.outcome === "initial_repair" ? "ok" : ""); }
      this.renderUnderstandingDetail();
    }));
    const close = host.querySelector("[data-repair-close]");
    if (close) close.addEventListener("click", () => { this.repair = null; this.openUnderstanding(); });
  },

  async startDiagnosis(conceptId, reason) {
    const result = await this.request("diagnosis_start", { concept_id: conceptId, reason: reason || "" });
    if (result.ok === false) { toast(result.error || "Teşhis başlatılamadı.", true); return; }
    this.diagnosis = result.diagnosis;
    this.check = null;
    this.repair = null;
    if (!this.viewIs("understanding")) Medical.show("understanding");
    this.renderUnderstandingDetail();
  },

  bindDiagnosis(host) {
    const diagnosis = this.diagnosis;
    const apply = (result, fallback) => {
      if (result.ok === false) { toast(result.error || fallback, true); return false; }
      this.diagnosis = result.diagnosis;
      this.renderUnderstandingDetail();
      return true;
    };
    $$("[data-dx-candidate]", host).forEach((block) => {
      let confidence = null;
      const conceptId = block.dataset.dxCandidate;
      $$("[data-confidence]", block).forEach((node) => node.addEventListener("click", () => {
        confidence = node.dataset.confidence;
        $$("[data-confidence]", block).forEach((chip) => { chip.classList.toggle("active", chip === node); chip.classList.toggle("accent", chip === node); });
      }));
      $$("[data-study-option]", block).forEach((node) => node.addEventListener("click", async () => {
        const key = node.dataset.studyOption;
        apply(await this.request("diagnosis_answer", { diagnosis_id: diagnosis.diagnosis_id, concept_id: conceptId, answer_key: key, confidence, submission_id: this.submissionId(diagnosis.diagnosis_id, conceptId, key) }), "Cevap kaydedilemedi.");
      }));
    });
    $$("[data-dx-skip]", host).forEach((node) => node.addEventListener("click", async () => apply(await this.request("diagnosis_skip", { diagnosis_id: diagnosis.diagnosis_id, concept_id: node.dataset.dxSkip }), "Atlanamadı.")));
    $$("[data-dx-step]", host).forEach((node) => node.addEventListener("click", async () => apply(await this.request("diagnosis_step", { diagnosis_id: diagnosis.diagnosis_id, concept_id: node.dataset.dxStep }), "Adım kaydedilemedi.")));
    $$("[data-dx-redirect]", host).forEach((node) => node.addEventListener("click", async () => apply(await this.request("diagnosis_redirect", { diagnosis_id: diagnosis.diagnosis_id, concept_id: node.dataset.dxRedirect }), "Yönlendirilemedi.")));
    $$("[data-dx-act]", host).forEach((node) => node.addEventListener("click", async () => {
      const action = node.dataset.dxAct;
      if (action === "close") { this.diagnosis = null; this.openUnderstanding(); return; }
      if (apply(await this.request(`diagnosis_${action}`, { diagnosis_id: diagnosis.diagnosis_id }), "Teşhis güncellenemedi.") && action === "finish") toast("Teşhis kapatıldı.", "ok");
    }));
  },

  async startCheck() {
    const session = (Medical.state && Medical.state.session) || {};
    const result = await this.request("understanding_check_start", { subject: session.subject || null, topic_id: session.topic_id || null });
    if (result.ok === false) { toast(result.error || "Anlama kontrolü başlatılamadı.", true); return; }
    if (!result.check) { toast("Bu ders ya da konu için bankada anahtarlı soru yok.", true); return; }
    this.check = result.check;
    this.diagnosis = null;
    this.repair = null;
    if (!this.viewIs("understanding")) Medical.show("understanding");
    this.renderUnderstandingDetail();
  },

  bindCheck(host) {
    const check = this.check;
    let confidence = null;
    $$("[data-confidence]", host).forEach((node) => node.addEventListener("click", () => {
      confidence = node.dataset.confidence;
      $$("[data-confidence]", host).forEach((chip) => { chip.classList.toggle("active", chip === node); chip.classList.toggle("accent", chip === node); });
    }));
    let chosen = null;
    $$("[data-study-option]", host).forEach((node) => node.addEventListener("click", () => {
      chosen = node.dataset.studyOption;
      $$("[data-study-option]", host).forEach((option) => option.classList.toggle("chosen", option === node));
    }));
    const send = host.querySelector("[data-check-send]");
    if (send) send.addEventListener("click", async () => {
      const box = host.querySelector("[data-check-text]");
      const reasoning = ((box && box.value) || "").trim();
      if (!chosen) { toast("Önce bir şık seç.", true); return; }
      if (!reasoning) { toast("Anlama kontrolünde gerekçe zorunlu.", true); return; }
      const result = await this.request("understanding_check_answer", { check_id: check.check_id, answer_key: chosen, confidence, reasoning, submission_id: this.submissionId(check.check_id, chosen) });
      if (result.ok === false) { toast(result.error || "Cevap kaydedilemedi.", true); return; }
      this.check = result.check;
      this.renderUnderstandingDetail();
      if (result.check && result.check.result && result.check.result.event_id) {
        const started = await this.request("understanding_assess", { event_id: result.check.result.event_id });
        if (started.ok !== false) toast(started.message || "Gerekçe değerlendiriliyor.", "ok");
      }
    });
    $$("[data-check-close]", host).forEach((node) => node.addEventListener("click", () => { this.check = null; this.openUnderstanding(); }));
  },

  /* ── histology view ───────────────────────────────────────────── */

  async openHistology() {
    const result = await this.request("histology_overview", {});
    if (result.ok === false) { toast(result.error || "Histoloji örnekleri okunamadı.", true); return; }
    this.histology = result;
    this.renderSpecimens();
    if (this.session) this.renderSession();
    else this.renderSpecimenDetail();
  },

  renderSpecimens() {
    const overview = this.histology || {};
    const host = $("#med-histo-list");
    const count = $("#med-histo-count");
    const empty = $("#med-histo-empty");
    const counts = overview.counts || {};
    if (count) count.textContent = overview.total ? `${overview.total} örnek · ${counts.eligible || 0} sınava uygun` : "";
    if (empty) empty.textContent = overview.empty_state || "";
    if (!host) return;
    const specimens = overview.specimens || [];
    host.innerHTML = specimens.length ? specimens.map((item) => this.specimenRow(item, { active: this.specimen && this.specimen.specimen_id === item.specimen_id })).join("") : "";
    $$("[data-specimen]", host).forEach((node) => node.addEventListener("click", () => this.openSpecimen(node.dataset.specimen)));
    const timed = $("#med-histo-timed");
    if (timed) timed.disabled = !(counts.eligible > 0);
    const study = $("#med-histo-study");
    if (study) study.disabled = !specimens.some((item) => item.status !== "unreadable");
  },

  async openSpecimen(specimenId) {
    const result = await this.request("histology_specimen", { specimen_id: specimenId });
    if (result.ok === false) { toast(result.error || "Örnek okunamadı.", true); return; }
    this.specimen = result.specimen;
    this.session = null;
    this.renderSpecimens();
    this.renderSpecimenDetail();
  },

  async loadCrop(host) {
    const nodes = $$("[data-crop]", host);
    for (const node of nodes) {
      const id = node.dataset.crop;
      let image = this.cropCache.get(id);
      if (!image) {
        const result = await this.request("histology_crop", { specimen_id: id });
        if (result.ok === false || !result.image) { node.innerHTML = `<span class="mq-figure-wait">${esc((result && result.error) || "Görüntü üretilemedi: kaynak sayfa yok.")}</span>`; continue; }
        image = result.image;
        this.cropCache.set(id, image);
      }
      node.innerHTML = `<img src="${image}" alt="Histoloji örneği">`;
    }
  },

  renderSpecimenDetail() {
    const host = $("#med-histo-detail");
    if (!host) return;
    const specimen = this.specimen;
    if (!specimen) { host.innerHTML = this.specimenMarkup(null); return; }
    host.innerHTML = `<div class="panel med-card">
      <div class="panel-title"><span class="kicker">Örnek</span><span class="chip ${specimen.status === "eligible" ? "ok" : ""}">${esc(specimen.status_label)}</span></div>
      ${this.specimenMarkup(specimen, { reveal: true })}
      <div class="btn-row" style="justify-content:flex-start">
        <button type="button" class="btn btn-primary small" data-specimen-act="confirm">${specimen.label ? "Adı ve özellikleri düzenle" : "Adını kaydet"}</button>
        <button type="button" class="btn btn-ghost small" data-specimen-act="source">Kaynak sayfayı aç</button>
        <button type="button" class="btn btn-ghost small" data-specimen-act="compare">Karıştırılanlarla kıyasla</button>
        <button type="button" class="btn btn-ghost small" data-specimen-act="unreadable">${specimen.status === "unreadable" ? "Okunabilir işaretle" : "Okunamıyor işaretle"}</button>
        <button type="button" class="btn btn-ghost small" data-specimen-act="delete">Sil</button></div>
      <div id="med-histo-compare"></div></div>`;
    this.loadCrop(host);
    $$("[data-specimen-act]", host).forEach((node) => node.addEventListener("click", () => this.specimenAction(node.dataset.specimenAct)));
  },

  async specimenAction(action) {
    const specimen = this.specimen;
    if (!specimen) return;
    if (action === "source") {
      Medical.show("library");
      Medical.openDocument(specimen.document_id).then(() => Medical.openPage(Number(specimen.page_number)));
      return;
    }
    if (action === "compare") {
      const result = await this.request("histology_compare", { specimen_id: specimen.specimen_id });
      if (result.ok === false) { toast(result.error || "Kıyaslama yapılamadı.", true); return; }
      const host = $("#med-histo-compare");
      if (!host) return;
      const block = (title, items) => items.length ? `<h4 class="study-h4">${title}</h4><div class="study-compare">${items.map((item) => `<div class="study-compare-item"><div class="study-crop" data-crop="${esc(item.specimen_id)}"></div><b>${esc(item.label)}</b><ul class="study-features">${(item.features || []).map((feature) => `<li>${esc(feature)}</li>`).join("") || "<li class=\"faint\">özellik kaydedilmedi</li>"}</ul></div>`).join("")}</div>` : "";
      host.innerHTML = `<p class="med-review-note">${esc(result.note || "")}</p>${block("Karıştırılanlar", result.confusable || [])}${block("Aynı konudan", result.same_topic || [])}`;
      this.loadCrop(host);
      return;
    }
    if (action === "confirm") {
      const entry = await this.dialog({
        title: "Örneğin adı ve dayanağı",
        okLabel: "KAYDET",
        html: `<label class="med-field"><span>Ad (Türkçe)</span><input id="study-sp-label" type="text" maxlength="120" value="${esc(specimen.label || "")}" placeholder="Ör. çok katlı yassı epitel"></label>
          <label class="med-field"><span>Latince</span><input id="study-sp-latin" type="text" maxlength="120" value="${esc(specimen.latin || "")}"></label>
          <label class="med-field"><span>Adı nereden biliyorsun?</span><select id="study-sp-basis">${STUDY_BASIS_OPTIONS.filter(([key]) => key !== "none").map(([key, label]) => `<option value="${key}" ${specimen.basis === key ? "selected" : ""}>${label}</option>`).join("")}</select></label>
          <label class="med-field"><span>Boya</span><input id="study-sp-stain" type="text" maxlength="60" value="${esc(specimen.stain || "")}" placeholder="bilinmiyorsa boş bırak"></label>
          <label class="med-field"><span>Büyütme</span><input id="study-sp-mag" type="text" maxlength="40" value="${esc(specimen.magnification || "")}" placeholder="bilinmiyorsa boş bırak"></label>
          <label class="med-field"><span>Ayırt edici özellikler (her satıra bir tane)</span><textarea id="study-sp-features" class="mem-edit study-textarea" rows="4">${esc((specimen.features || []).join("\n"))}</textarea></label>`,
        collect: (host) => ({
          label: host.querySelector("#study-sp-label").value.trim(),
          latin: host.querySelector("#study-sp-latin").value.trim(),
          basis: host.querySelector("#study-sp-basis").value,
          stain: host.querySelector("#study-sp-stain").value.trim(),
          magnification: host.querySelector("#study-sp-mag").value.trim(),
          features: host.querySelector("#study-sp-features").value.split("\n").map((line) => line.trim()).filter(Boolean),
        }),
      });
      if (!entry) return;
      if (!entry.label) { toast("Ad gerekli.", true); return; }
      const result = await this.request("histology_confirm", { specimen_id: specimen.specimen_id, label: entry.label, latin: entry.latin, features: entry.features, stain: entry.stain, magnification: entry.magnification });
      if (result.ok === false) { toast(result.error || "Kaydedilemedi.", true); return; }
      if (entry.basis !== "user_confirmed") {
        const basis = await this.request("histology_update", { specimen_id: specimen.specimen_id, fields: { basis: entry.basis } });
        if (basis.ok !== false) result.specimen = basis.specimen;
      }
      this.specimen = result.specimen;
      toast(`Kaydedildi: ${result.specimen.status_label}.`, "ok");
      await this.openHistology();
      return;
    }
    if (action === "unreadable") {
      const result = await this.request("histology_update", { specimen_id: specimen.specimen_id, fields: { unreadable: specimen.status !== "unreadable" } });
      if (result.ok === false) { toast(result.error || "Güncellenemedi.", true); return; }
      this.specimen = result.specimen;
      await this.openHistology();
      return;
    }
    if (action === "delete") {
      const ok = await confirmDialog({ title: "Örnek silinsin mi?", body: "Kırpılmış görüntü ve kayıtlı özellikler kaldırılacak; ders sayfası kalır.", confirmLabel: "SİL", danger: true });
      if (!ok) return;
      const result = await this.request("histology_delete", { specimen_id: specimen.specimen_id, confirmed: true });
      if (result.ok === false) { toast(result.error || "Silinemedi.", true); return; }
      this.specimen = null;
      this.cropCache.delete(specimen.specimen_id);
      await this.openHistology();
    }
  },

  async startSession(mode) {
    const count = Number($("#med-histo-n") && $("#med-histo-n").value) || 10;
    const seconds = Number($("#med-histo-seconds") && $("#med-histo-seconds").value) || 60;
    const result = await this.request("histology_session_start", { mode, count, seconds });
    if (result.ok === false) { toast(result.error || "Oturum başlatılamadı.", true); return; }
    if (!result.session) { toast(mode === "timed" ? "Süreli pratik için adı onaylanmış örnek yok." : "Çalışılacak örnek yok.", true); return; }
    this.session = result.session;
    this.sessionIndex = 0;
    await this.showItem(0);
  },

  async showItem(index) {
    if (!this.session) return;
    const result = await this.request("histology_show", { session_id: this.session.session_id, index });
    if (result.ok === false) { toast(result.error || "Örnek gösterilemedi.", true); return; }
    this.session = result.session;
    this.sessionIndex = index;
    this.renderSession();
  },

  stopTimer() { if (this.sessionTimer) { clearInterval(this.sessionTimer); this.sessionTimer = 0; } },

  renderSession() {
    const host = $("#med-histo-detail");
    if (!host || !this.session) return;
    this.stopTimer();
    const session = this.session;
    if (session.status === "closed") { host.innerHTML = this.sessionResultsMarkup(session); const close = host.querySelector("[data-session-close]"); if (close) close.addEventListener("click", () => { this.session = null; this.openHistology(); }); return; }
    host.innerHTML = this.sessionItemMarkup(session, this.sessionIndex);
    this.loadCrop(host);
    const item = (session.items || []).find((entry) => entry.index === this.sessionIndex) || null;
    let confidence = null;
    $$("[data-confidence]", host).forEach((node) => node.addEventListener("click", () => {
      confidence = node.dataset.confidence;
      $$("[data-confidence]", host).forEach((chip) => { chip.classList.toggle("active", chip === node); chip.classList.toggle("accent", chip === node); });
    }));
    const answer = host.querySelector("[data-session-answer]");
    const submit = async ({ timedOut = false } = {}) => {
      if (!item || item.answer) return;
      const input = $("#study-answer-text");
      const explain = host.querySelector("[data-session-explain]");
      const text = timedOut ? "" : ((input && input.value) || "").trim();
      if (!timedOut && !text) { toast("Önce adı yaz.", true); return; }
      this.stopTimer();
      if (answer) answer.disabled = true;
      const result = await this.request("histology_answer", { session_id: session.session_id, specimen_id: item.specimen_id, text, confidence, explanation: (explain && explain.value) || "", submission_id: this.submissionId(session.session_id, item.specimen_id), timed_out: timedOut });
      if (result.ok === false) { toast(result.error || "Cevap gönderilemedi.", true); if (answer) answer.disabled = false; return; }
      toast(result.message || "Cevap değerlendiriliyor.", "ok");
    };
    if (answer) answer.addEventListener("click", () => submit());
    const input = $("#study-answer-text");
    if (input) { input.addEventListener("keydown", (event) => { if (event.key === "Enter") { event.preventDefault(); submit(); } }); input.focus(); }
    const next = host.querySelector("[data-session-next]");
    if (next) next.addEventListener("click", () => this.showItem(this.sessionIndex + 1));
    $$("[data-session-finish]", host).forEach((node) => node.addEventListener("click", () => this.finishSession()));
    $$("[data-compare]", host).forEach((node) => node.addEventListener("click", async () => { this.specimen = { specimen_id: node.dataset.compare }; const detail = await this.request("histology_specimen", { specimen_id: node.dataset.compare }); if (detail.ok !== false) { this.specimen = detail.specimen; this.session = null; this.renderSpecimenDetail(); this.specimenAction("compare"); } }));
    if (session.mode === "timed" && item && !item.answer) {
      const started = item.shown_at ? Date.parse(item.shown_at) : Date.now();
      const deadline = (Number.isFinite(started) ? started : Date.now()) + Number(session.seconds || 60) * 1000;
      const tick = () => {
        const remaining = Math.max(0, Math.ceil((deadline - Date.now()) / 1000));
        const node = $("#study-timer");
        if (node) { node.textContent = `${remaining} sn`; node.classList.toggle("low", remaining <= 10); }
        if (remaining <= 0) { this.stopTimer(); submit({ timedOut: true }); }
      };
      tick();
      this.sessionTimer = setInterval(tick, 500);
    }
  },

  async finishSession() {
    if (!this.session) return;
    this.stopTimer();
    const result = await this.request("histology_finish", { session_id: this.session.session_id });
    if (result.ok === false) { toast(result.error || "Oturum bitirilemedi.", true); return; }
    this.session = result.session;
    this.renderSession();
  },

  /* ── page region selector (library) ───────────────────────────── */

  beginRegionSelect(documentId, pageNumber) {
    const frame = $("#med-page-image");
    const img = frame ? frame.querySelector("img") : null;
    if (!frame || !img) { toast("Bu sayfanın görüntüsü yok; örnek seçilemez.", true); return; }
    if (this.selection) this.endRegionSelect();
    frame.classList.add("selecting");
    const box = el("div", "study-region");
    frame.appendChild(box);
    let start = null;
    const bounds = () => img.getBoundingClientRect();
    const place = (x0, y0, x1, y1) => {
      box.style.left = `${img.offsetLeft + Math.min(x0, x1)}px`;
      box.style.top = `${img.offsetTop + Math.min(y0, y1)}px`;
      box.style.width = `${Math.abs(x1 - x0)}px`;
      box.style.height = `${Math.abs(y1 - y0)}px`;
    };
    const point = (event) => { const rect = bounds(); return [clamp(event.clientX - rect.left, 0, rect.width), clamp(event.clientY - rect.top, 0, rect.height)]; };
    const down = (event) => { if (event.button !== 0) return; event.preventDefault(); start = point(event); place(start[0], start[1], start[0], start[1]); };
    const move = (event) => { if (!start) return; const [x, y] = point(event); place(start[0], start[1], x, y); };
    const up = (event) => {
      if (!start) return;
      const [x, y] = point(event);
      const rect = bounds();
      const width = Math.abs(x - start[0]), height = Math.abs(y - start[1]);
      const origin = start; start = null;
      if (width < 8 || height < 8 || !rect.width || !rect.height) return;
      const region = { x: Math.min(origin[0], x) / rect.width, y: Math.min(origin[1], y) / rect.height, w: width / rect.width, h: height / rect.height };
      this.renderSpecimenForm(documentId, pageNumber, region);
    };
    frame.addEventListener("mousedown", down);
    window.addEventListener("mousemove", move);
    window.addEventListener("mouseup", up);
    this.selection = { frame, box, down, move, up };
    toast("Şekli fareyle çerçeve içine al.", "ok");
  },

  endRegionSelect() {
    const selection = this.selection;
    if (!selection) return;
    selection.frame.classList.remove("selecting");
    selection.frame.removeEventListener("mousedown", selection.down);
    window.removeEventListener("mousemove", selection.move);
    window.removeEventListener("mouseup", selection.up);
    if (selection.box.parentNode) selection.box.parentNode.removeChild(selection.box);
    this.selection = null;
  },

  renderSpecimenForm(documentId, pageNumber, region) {
    const host = $("#med-histo-form");
    if (!host) return;
    host.innerHTML = `<div class="panel med-card study-specimen-form">
      <div class="panel-title"><span class="kicker">Histoloji örneği</span><span class="faint">s. ${pageNumber} · ${Math.round(region.w * 100)}×${Math.round(region.h * 100)} % sayfa</span></div>
      <p class="med-review-note">Ad ve özellikler kaydedilen dayanağa göre saklanır; JARVIS bunları uydurmaz. Adı bilmiyorsan adsız kaydet, sonra Histoloji ekranından doldur.</p>
      <div class="med-form-row">
        <label class="med-field"><span>Ad (Türkçe)</span><input id="study-region-label" type="text" maxlength="120" placeholder="Ör. çok katlı yassı epitel"></label>
        <label class="med-field"><span>Latince</span><input id="study-region-latin" type="text" maxlength="120"></label>
        <label class="med-field"><span>Adı nereden biliyorsun?</span><select id="study-region-basis">${STUDY_BASIS_OPTIONS.map(([key, label]) => `<option value="${key}">${label}</option>`).join("")}</select></label>
      </div>
      <div class="med-form-row">
        <label class="med-field"><span>Boya</span><input id="study-region-stain" type="text" maxlength="60" placeholder="bilinmiyorsa boş"></label>
        <label class="med-field"><span>Büyütme</span><input id="study-region-mag" type="text" maxlength="40" placeholder="bilinmiyorsa boş"></label>
      </div>
      <label class="med-field"><span>Ayırt edici özellikler (her satıra bir tane)</span><textarea id="study-region-features" class="study-textarea" rows="3"></textarea></label>
      <div class="btn-row" style="justify-content:flex-start"><button type="button" class="btn btn-primary small" data-region-save>Kaydet</button><button type="button" class="btn btn-ghost small" data-region-cancel>Vazgeç</button></div></div>`;
    const cancel = host.querySelector("[data-region-cancel]");
    if (cancel) cancel.addEventListener("click", () => { host.innerHTML = ""; this.endRegionSelect(); });
    const save = host.querySelector("[data-region-save]");
    if (save) save.addEventListener("click", async () => {
      const basis = $("#study-region-basis").value;
      const label = basis === "none" ? "" : $("#study-region-label").value.trim();
      if (basis !== "none" && !label) { toast("Ad gir ya da 'Henüz bilmiyorum' seç.", true); return; }
      const features = $("#study-region-features").value.split("\n").map((line) => line.trim()).filter(Boolean);
      const result = await this.request("histology_add", { document_id: documentId, page_number: pageNumber, region, label, latin: $("#study-region-latin").value.trim(), basis, stain: $("#study-region-stain").value.trim() || null, magnification: $("#study-region-mag").value.trim() || null, features });
      if (result.ok === false) { toast(result.error || "Örnek kaydedilemedi.", true); return; }
      toast(`Örnek kaydedildi: ${result.specimen.status_label}.`, "ok");
      host.innerHTML = "";
      this.endRegionSelect();
      this.histology = null;
    });
  },

  /* ── pushes ──────────────────────────────────────────────────── */

  onJob(payload) {
    const action = String(payload.action || "");
    if (action === "understanding_assess" && payload.event) { this.onAssessed(payload.event); return; }
    if ((action === "diagnostic_ask" || action === "diagnostic_answer") && payload.finding) {
      this.finding = payload.finding;
      toast(action === "diagnostic_ask" ? "Teşhis sorusu hazır." : `Teşhis cevabı değerlendirildi: ${payload.finding.status_label}.`, "ok");
      if (this.viewIs("understanding")) { this.repair = null; this.diagnosis = null; this.check = null; this.renderFindings(); this.renderUnderstandingDetail(); }
      return;
    }
    if (action === "repair_start" && payload.session) {
      this.repair = payload.session;
      toast("Onarım oturumu hazır.", "ok");
      if (this.viewIs("understanding")) { this.diagnosis = null; this.check = null; this.renderUnderstandingDetail(); }
      return;
    }
    if (action === "question_review" && payload.support) {
      toast(`Kaynak incelemesi: ${payload.support.label}${payload.support.reason ? ` — ${payload.support.reason}` : ""}`, payload.support.scored ? "ok" : "");
      if (this.viewIs("bank")) Medical.loadBank();
      return;
    }
    if (action === "histology_answer" && payload.session) {
      if (this.session && this.session.session_id === payload.session.session_id) { this.session = payload.session; if (this.viewIs("histology")) this.renderSession(); }
      return;
    }
  },

  /* ── bindings ─────────────────────────────────────────────────── */

  bind() {
    const planForm = $("#med-plan-form");
    if (planForm) planForm.addEventListener("submit", (event) => this.createPlan(event));
    const check = $("#med-und-check");
    if (check) check.addEventListener("click", () => this.startCheck());
    const refresh = $("#med-und-refresh");
    if (refresh) { refresh.innerHTML = icon("refresh"); refresh.addEventListener("click", () => this.openUnderstanding()); }
    const study = $("#med-histo-study");
    if (study) study.addEventListener("click", () => this.startSession("study"));
    const timed = $("#med-histo-timed");
    if (timed) timed.addEventListener("click", () => this.startSession("timed"));
  },
};
