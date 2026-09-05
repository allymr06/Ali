/* ════════════════════════════════════════════════════════════════════
   JARVIS · NOVA — activity
   What JARVIS is doing, made visible: the execution model of a turn
   (understanding → tools → verification → answer), its inline strip in
   the conversation, the timeline in the context drawer, the permission
   overlay (one exact action, one single-use token) and the trust screen.
   No private reasoning is shown — only observed events.
   ════════════════════════════════════════════════════════════════════ */
"use strict";

const TOOL_STATUS_CLASS = {
  success: "success", failed: "failed", partial: "failed", blocked: "blocked",
  cancelled: "cancelled", timeout: "timeout", unknown: "failed", running: "running",
};

const Activity = {
  current: null,
  recent: [],
  _settleTimer: 0,

  active() { return !!this.current || Presence.toolLabel !== null; },

  _turn(goal, implicit) {
    return {
      id: `turn-${Date.now()}-${Math.floor(Math.random() * 1e6)}`,
      goal, implicit, startedAt: Date.now(), finishedAt: null,
      status: "thinking", streaming: false, error: null,
      tools: [], approvals: [], strip: null,
    };
  },

  _mountStrip(turn) {
    const host = $("#chat-list");
    if (!host) return;
    const strip = el("div", "activity-strip");
    strip.hidden = true;
    host.appendChild(strip);
    turn.strip = strip;
  },

  beginTurn(text) {
    if (this.current) this.finish(this.current);
    const turn = this._turn(text, false);
    this._mountStrip(turn);
    this.current = turn;
    Presence.toolLabel = null;
    Presence.streaming = false;
    this.render();
    return turn;
  },

  /* Tool activity that arrives outside a typed turn (a voice turn, a
     scheduled reminder, the tray) still deserves a place in the story. */
  _ensureTurn() {
    if (this.current) return this.current;
    const turn = this._turn(State.voiceActive ? "Sesli istek" : "Arka plan etkinliği", true);
    this._mountStrip(turn);
    this.current = turn;
    return turn;
  },

  onToolEvent(payload) {
    const turn = this._ensureTurn();
    clearTimeout(this._settleTimer);
    if (payload.phase === "started") {
      turn.tools.push({
        id: payload.execution_id, tool: payload.tool, operation: payload.operation,
        status: "running", startedAt: payload.at || Date.now(), finishedAt: null,
        verified: false, message: "", failed: false, durationMs: null,
      });
      Presence.toolLabel = toolLabel(payload.tool, false);
      Presence.apply();
      Context.autoOpen();
    } else {
      let node = turn.tools.find((item) => item.id === payload.execution_id);
      if (!node) {
        node = { id: payload.execution_id, tool: payload.tool, operation: payload.operation, startedAt: payload.at || Date.now() };
        turn.tools.push(node);
      }
      node.status = String(payload.status || "unknown");
      node.verified = !!payload.verified;
      node.message = String(payload.message || "");
      node.failed = !!payload.failed;
      node.durationMs = payload.duration_ms ?? null;
      node.finishedAt = payload.at || Date.now();
      const running = turn.tools.filter((item) => item.status === "running");
      Presence.toolLabel = running.length ? toolLabel(running[running.length - 1].tool, false) : null;
      Presence.apply();
      if (node.status === "success" && node.verified) Engine.wake();
    }
    this.render();
    if (turn.implicit && !turn.tools.some((item) => item.status === "running")) {
      this._settleTimer = setTimeout(() => this.settle(), 2500);
    }
  },

  onStream() {
    const turn = this.current;
    if (!turn || turn.streaming) return;
    turn.streaming = true;
    Presence.streaming = true;
    Presence.apply();
    this.render();
  },

  onReply(message) {
    const turn = this.current;
    if (!turn) return;
    turn.status = message.role === "system" ? "failed" : "completed";
    if (message.role === "system") { turn.error = message.text; Presence.error("istek tamamlanamadı"); }
    this.finish(turn);
  },

  /* Voice answers arrive as voice_message, not reply. */
  settle() {
    clearTimeout(this._settleTimer);
    const turn = this.current;
    if (!turn || !turn.implicit) return;
    if (turn.tools.some((item) => item.status === "running")) return;
    turn.status = turn.tools.some((item) => item.failed) ? "failed" : "completed";
    this.finish(turn);
  },

  abortTurn(error) {
    const turn = this.current;
    if (!turn) return;
    turn.status = "failed";
    turn.error = error;
    this.finish(turn);
  },

  finish(turn) {
    turn.finishedAt = turn.finishedAt || Date.now();
    if (turn.status === "thinking") turn.status = "completed";
    Presence.toolLabel = null;
    Presence.streaming = false;
    if (this.current === turn) this.current = null;
    if (turn.tools.length || turn.approvals.length || !turn.implicit) {
      this.recent.unshift(turn);
      this.recent.length = Math.min(this.recent.length, 12);
    }
    this.render();
    Presence.apply();
    Context.autoSettle();
  },

  onApproval(payload) {
    const turn = this._ensureTurn();
    turn.approvals.push({ token: payload.token, tool: payload.tool, risk: payload.risk, decision: null, at: Date.now() });
    Presence.approval = { tool: payload.tool, risk: payload.risk };
    Presence.apply();
    Context.autoOpen();
    this.render();
  },

  onApprovalClosed(token, decision) {
    const turn = this.current || this.recent[0];
    const entry = turn?.approvals.find((item) => item.token === token);
    if (entry && entry.decision === null) entry.decision = decision === null ? "expired" : decision ? "allowed" : "denied";
    Presence.approval = null;
    Presence.apply();
    this.render();
  },

  reset() {
    clearTimeout(this._settleTimer);
    this.current = null;
    this.recent = [];
    Presence.toolLabel = null;
    Presence.streaming = false;
    Presence.approval = null;
    this.render();
  },

  /* ── derived timeline: only observed, user-facing events ── */
  nodes(turn) {
    const nodes = [];
    const live = turn.status === "thinking";
    const firstEvent = Math.min(...turn.tools.map((t) => t.startedAt), ...turn.approvals.map((a) => a.at), Infinity);
    nodes.push({ label: "İstek alındı", status: "completed", meta: fmtClock(new Date(turn.startedAt)) });
    const understood = turn.tools.length || turn.approvals.length || turn.streaming || !live;
    nodes.push({ label: "Anlaşılıyor", status: understood ? "completed" : "running",
                 meta: understood && Number.isFinite(firstEvent) ? fmtDuration(firstEvent - turn.startedAt) : "" });
    const events = [
      ...turn.tools.map((t) => ({ kind: "tool", at: t.startedAt, item: t })),
      ...turn.approvals.map((a) => ({ kind: "approval", at: a.at, item: a })),
    ].sort((a, b) => a.at - b.at);
    for (const event of events) {
      if (event.kind === "approval") {
        const a = event.item;
        const status = a.decision === null ? "waiting" : a.decision === "allowed" ? "completed" : "failed";
        const meta = a.decision === null ? "onayın bekleniyor" : a.decision === "allowed" ? "izin verildi" : a.decision === "denied" ? "reddedildi" : "süresi doldu";
        nodes.push({ label: `İzin istendi · ${toolLabel(a.tool, false).toLocaleLowerCase("tr")}`, status, meta, tone: status === "completed" ? "ok" : status === "failed" ? "bad" : "warn" });
        continue;
      }
      const t = event.item;
      const running = t.status === "running";
      const status = running ? "running" : t.status === "success" ? "completed"
        : t.status === "blocked" ? "blocked" : t.status === "cancelled" ? "cancelled" : "failed";
      const parts = [];
      if (t.durationMs !== null && t.durationMs !== undefined) parts.push(fmtDuration(t.durationMs));
      if (!running && t.verified) parts.push("doğrulandı");
      if (!running && t.status === "blocked") parts.push("izin verilmedi");
      if (!running && !t.verified && t.status === "success") parts.push("doğrulanamadı");
      nodes.push({ label: toolLabel(t.tool, !running), status, meta: parts.join(" · "),
                   tone: t.verified ? "ok" : status === "failed" ? "bad" : status === "blocked" ? "warn" : "" });
    }
    if (turn.streaming || (!live && !turn.implicit && turn.status === "completed")) {
      nodes.push({ label: "Yanıt yazılıyor", status: live ? "running" : "completed", meta: "" });
    }
    if (!live) {
      const ok = turn.status === "completed";
      nodes.push({ label: ok ? "Tamamlandı" : "Tamamlanamadı", status: ok ? "completed" : "failed",
                   meta: turn.finishedAt ? fmtDuration(turn.finishedAt - turn.startedAt) : "", tone: ok ? "ok" : "bad" });
    }
    return nodes;
  },

  timelineHTML(turn) {
    return `<div class="timeline">${this.nodes(turn).map((node) =>
      `<div class="tl-node ${esc(node.status)}"><div class="tl-name">${esc(node.label)}</div>${node.meta ? `<div class="tl-meta"><span class="${esc(node.tone || "")}">${esc(node.meta)}</span></div>` : ""}</div>`).join("")}</div>`;
  },

  renderTimeline(host) {
    if (!host) return;
    const turn = this.current || this.recent[0];
    if (!turn) {
      host.innerHTML = '<div class="ctx-empty">Bekleyen yürütme yok. Bir komut verdiğinde adımlar burada belirir.</div>';
      return;
    }
    host.innerHTML =
      `<div class="task-goal" style="font-size:var(--text-sm);margin-bottom:.6rem;color:var(--ink-2)">${esc(turn.goal)}</div>` +
      this.timelineHTML(turn);
  },

  renderStrip(turn) {
    const strip = turn.strip;
    if (!strip) return;
    const pills = [];
    for (const t of turn.tools) {
      const cls = TOOL_STATUS_CLASS[t.status] || "running";
      const light = t.status === "running" ? "busy" : t.status === "success" ? "ok" : t.status === "blocked" ? "warn" : "bad";
      pills.push(`<span class="tool-pill ${cls}" title="${esc(t.tool)}${t.message ? " · " + esc(t.message) : ""}">` +
        `<span class="status-light ${light}"></span>${esc(toolLabel(t.tool, t.status !== "running"))}` +
        (t.durationMs !== null && t.durationMs !== undefined ? `<span class="pill-time">${esc(fmtDuration(t.durationMs))}</span>` : "") +
        (t.verified ? `<span class="pill-check" title="Sonuç doğrulandı">✓</span>` : "") + `</span>`);
    }
    for (const a of turn.approvals) {
      const cls = a.decision === null ? "waiting" : a.decision === "allowed" ? "success" : "blocked";
      const text = a.decision === null ? "İzin bekleniyor" : a.decision === "allowed" ? "İzin verildi" : a.decision === "denied" ? "Reddedildi" : "Onay süresi doldu";
      pills.push(`<span class="tool-pill ${cls}"><span class="status-light ${a.decision === null ? "warn" : a.decision === "allowed" ? "ok" : "bad"}"></span>${esc(text)} · ${esc(toolLabel(a.tool, false).toLocaleLowerCase("tr"))}</span>`);
    }
    strip.hidden = !pills.length;
    strip.innerHTML = `<div class="strip-row">${pills.join("")}</div>`;
  },

  render() {
    if (this.current) this.renderStrip(this.current);
    Context.render();
    renderHomeActivity();
  },
};

/* ── approval overlay: one exact action, one single-use token ─────── */

let activeApproval = null;

function openApproval(payload) {
  activeApproval = payload;
  const risk = String(payload.risk || "high");
  const veil = $("#approval");
  veil.classList.toggle("critical", risk === "critical");
  $("#approval-risk").textContent = `RİSK · ${tr(risk)}`;
  $("#approval-risk").className = `chip risk-chip ${esc(risk)}`;
  $("#approval-title").textContent = risk === "critical" ? "Kritik bir eylem için iznin gerekiyor" : "JARVIS bir eylem için izin istiyor";
  $("#approval-tool-label").textContent = toolLabel(payload.tool, false);
  $("#approval-tool-raw").textContent = payload.tool || "";
  $("#approval-operation").textContent = payload.operation || "—";
  $("#approval-reason").textContent = reasonLabel(payload.reason) || "—";
  $("#approval-source").textContent = payload.source === "voice" ? "sesli istek" : payload.source === "text" ? "yazılı istek" : (payload.source || "—");
  const effect = $("#approval-effect");
  const effectText = toolEffect(payload.tool, payload.description);
  effect.textContent = effectText;
  effect.parentElement.hidden = !effectText;
  const params = $("#approval-params");
  const entries = Object.entries(payload.parameters || {});
  params.innerHTML = entries.length
    ? entries.map(([key, value]) => `<div><b>${esc(key)}</b>: ${esc(String(value))}</div>`).join("")
    : "<div>parametre yok</div>";
  $("#approval-details").open = risk === "critical" || risk === "high";
  veil.hidden = false;
  const bar = $("#approval-timer-bar");
  bar.style.transition = "none";
  bar.style.transform = "scaleX(1)";
  requestAnimationFrame(() => {
    bar.style.transition = `transform ${payload.seconds || 30}s linear`;
    bar.style.transform = "scaleX(0)";
  });
  Motion.rise(veil.querySelector(".modal"), { y: 16, scale: 0.97, duration: Motion.panel });
  Activity.onApproval(payload);
  Engine.wake();
  $("#approval-deny").focus();
}

function closeApproval(token, approved) {
  if (!activeApproval) return;
  if (token && activeApproval.token !== token) return;
  const current = activeApproval;
  activeApproval = null;
  if (approved !== null) {
    Bridge.resolve_approval(current.token, approved);
    State.approvals.push({ tool: current.tool, operation: current.operation, risk: current.risk, approved, at: Date.now() });
    renderApprovalLog();
  }
  Activity.onApprovalClosed(current.token, approved);
  $("#approval").hidden = true;
}

/* ── trust screen ─────────────────────────────────────────────────── */

function renderApprovalLog() {
  const host = $("#approval-log");
  if (!host) return;
  if (!State.approvals.length) {
    host.innerHTML = '<div class="ctx-empty">Bu oturumda onay istenmedi.</div>';
    return;
  }
  host.innerHTML = State.approvals.slice(-8).reverse().map((a) =>
    `<div class="status-row"><span class="status-light ${a.approved ? "ok" : "bad"}"></span>` +
    `<span class="status-name">${esc(toolLabel(a.tool, false))} <span class="faint mono" style="font-size:.62rem">${esc(a.operation)}</span></span>` +
    `<span class="status-note">${a.approved ? "İZİN" : "RET"} · ${esc(fmtClock(new Date(a.at)))}</span></div>`).join("");
}

const Trust = {
  async refresh() {
    renderRiskBars(State.snapshot?.tools || []);
    renderApprovalLog();
    const host = $("#audit-list");
    const result = await call("permission_audit", 40);
    if (result.ok === false) { host.innerHTML = `<div class="ctx-empty">${esc(result.error || "Denetim kaydı okunamadı.")}</div>`; return; }
    const entries = result.entries || [];
    if (!entries.length) { host.innerHTML = '<div class="ctx-empty">İzin motoru henüz bir değerlendirme kaydetmedi.</div>'; return; }
    host.innerHTML = entries.map((item) => {
      const cls = item.decision === "allow" ? "ok" : item.decision === "deny" ? "bad" : "warn";
      return `<div class="audit-item"><span class="chip ${cls}">${esc(tr(item.decision))}</span>` +
        `<span class="audit-tool">${esc(toolLabel(item.tool || item.operation, false))} <span class="faint">· ${esc(tr(item.risk))}</span></span>` +
        `<span class="audit-time">${esc(fmtTime(item.evaluated_at))}</span>` +
        `<span class="audit-reason">${esc(item.reason || "")}</span></div>`;
    }).join("");
  },
};

function renderRiskBars(tools) {
  const counts = { read_only: 0, low: 0, medium: 0, high: 0, critical: 0 };
  tools.forEach((t) => { const risk = String(t.risk ?? "low"); if (risk in counts) counts[risk] += 1; });
  const total = Math.max(1, tools.length);
  const host = $("#risk-bars");
  if (!host) return;
  host.innerHTML = Object.entries(counts).map(([risk, count]) =>
    `<div class="risk-row"><div class="rr-head"><span>${esc(tr(risk))}</span><span>${count}</span></div>
     <div class="progress-track"><div class="progress-fill ${risk === "critical" ? "" : ""}" style="transform: scaleX(${count / total})"></div></div></div>`).join("");
}

function bindActivity() {
  $("#approval-allow").addEventListener("click", () => closeApproval(null, true));
  $("#approval-deny").addEventListener("click", () => closeApproval(null, false));
}
