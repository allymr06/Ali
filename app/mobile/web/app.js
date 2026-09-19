/* JARVIS mobile companion.
   One page, three tabs, one server-sent-events channel. The phone never
   holds credentials in script-reachable storage: the session is an
   HttpOnly cookie, and every mutation carries the page's own header.
   Whatever the connection does, a message is sent to the PC at most once
   per client id; a dropped connection shows "sonuç bilinmiyor", never a
   guessed outcome, and reconnecting asks the PC what really happened. */
(() => {
  "use strict";

  const $ = (selector, root = document) => root.querySelector(selector);
  const $$ = (selector, root = document) => Array.from(root.querySelectorAll(selector));
  const esc = (value) => String(value ?? "").replace(/[&<>"']/g, (ch) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[ch]));
  const uuid = () => (crypto.randomUUID ? crypto.randomUUID() : "c-" + Date.now().toString(36) + "-" + Math.random().toString(36).slice(2, 12));
  const reducedMotion = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  const State = {
    session: null,
    pc: "",
    paused: false,
    stamp: "",
    conversation: null,
    messages: [],
    pending: new Map(), // client_id -> { node, text, conversationId }
    approvals: new Map(),
    tab: "chat",
    connected: false,
    es: null,
    backoff: 1000,
    reconnectTimer: 0,
    tasksTimer: 0,
    sending: false,
    approvalTimer: 0,
  };

  /* ---------------------------------------------------------- transport */
  async function api(path, options = {}) {
    const init = { method: options.method || "GET", credentials: "same-origin", headers: { "X-JARVIS-Client": "pwa" }, cache: "no-store" };
    if (options.body !== undefined) {
      init.headers["Content-Type"] = "application/json";
      init.body = JSON.stringify(options.body);
    }
    let response;
    try {
      response = await fetch(path, init);
    } catch (_error) {
      throw { offline: true, error: "Bilgisayara ulaşılamıyor." };
    }
    let payload = {};
    try { payload = await response.json(); } catch (_error) { payload = {}; }
    if (response.status === 401) {
      onUnauthorized(payload.error);
      throw { unauthorized: true, error: payload.error || "Oturum sona erdi." };
    }
    if (response.status === 503 && payload.offline) throw { offline: true, error: payload.error || "Bilgisayara ulaşılamıyor." };
    if (!response.ok && payload.ok === undefined) payload = { ok: false, error: payload.error || ("HTTP " + response.status) };
    return payload;
  }

  /* --------------------------------------------------------------- ui */
  function toast(text, isError) {
    const node = $("#toast");
    node.textContent = text;
    node.classList.toggle("err", !!isError);
    node.hidden = false;
    clearTimeout(node._timer);
    node._timer = setTimeout(() => { node.hidden = true; }, 3600);
  }

  function setConn(kind, text) {
    const conn = $("#conn");
    conn.className = "conn " + kind;
    $("#conn-text").textContent = text;
    $("#set-conn").textContent = text;
  }

  function setConnected(flag) {
    State.connected = flag;
    if (flag) setConn("ok", "bağlı");
    else if (!navigator.onLine) setConn("bad", "çevrimdışı");
    else setConn("warn", "yeniden bağlanıyor");
    // The banner speaks whenever the PC is out of reach - a phone with
    // perfect signal and a sleeping PC is the common case, not an edge.
    $("#offline").hidden = flag;
  }

  function showScreen(id) {
    ["pair", "chat", "tasks", "settings"].forEach((name) => { $("#screen-" + name).hidden = name !== id; });
    $("#tabs").hidden = id === "pair";
    if (id === "pair") return;
    State.tab = id;
    $$(".tab").forEach((tab) => tab.classList.toggle("active", tab.dataset.tab === id));
    if (id === "tasks") loadTasks();
    if (id === "chat") scrollMessages(true);
  }

  function onUnauthorized(message) {
    // A first visit is not an error; a session that was there and is
    // gone deserves the server's own words.
    const hadSession = State.session !== null;
    closeEvents();
    State.session = null;
    setConn("bad", "eşleşmedi");
    const error = $("#pair-error");
    error.textContent = message || "Oturum yok ya da sona erdi; telefonu yeniden eşleştir.";
    error.hidden = !(message && hadSession);
    showScreen("pair");
  }

  /* ---------------------------------------------------------- markdown */
  function renderMarkdownLite(raw) {
    const escaped = esc(raw);
    const inline = (text) => text
      .replace(/`([^`]+)`/g, "<code>$1</code>")
      .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
    const lines = escaped.split(/\r?\n/);
    const parts = [];
    let list = null;
    const closeList = () => { if (list) { parts.push("</" + list + ">"); list = null; } };
    for (const line of lines) {
      const bullet = /^\s*[-•] +(.*)$/.exec(line);
      const numbered = /^\s*\d+[.)] +(.*)$/.exec(line);
      if (bullet || numbered) {
        const kind = bullet ? "ul" : "ol";
        if (list !== kind) { closeList(); parts.push("<" + kind + ">"); list = kind; }
        parts.push("<li>" + inline((bullet || numbered)[1]) + "</li>");
        continue;
      }
      closeList();
      parts.push(line.trim() ? "<p>" + inline(line) + "</p>" : "");
    }
    closeList();
    return parts.join("");
  }

  /* ------------------------------------------------------------ chat */
  function messageNode(message) {
    const node = document.createElement("div");
    node.className = "msg " + (message.role || "assistant");
    const body = document.createElement("div");
    body.className = "body";
    if (message.role === "assistant") body.innerHTML = renderMarkdownLite(message.text);
    else body.textContent = message.text || "";
    node.appendChild(body);
    const meta = message.metadata || {};
    const chips = [];
    if (meta.assurance_level) chips.push("güvence · " + esc(meta.assurance_level));
    if (meta.tool_calls) chips.push("araç · " + esc(meta.tool_calls));
    if (Number.isFinite(Number(meta.elapsed_seconds))) chips.push(Number(meta.elapsed_seconds).toFixed(1) + " sn");
    if (chips.length && message.role === "assistant") {
      const row = document.createElement("div");
      row.className = "meta";
      row.innerHTML = chips.map((chip) => "<span>" + chip + "</span>").join("");
      node.appendChild(row);
    }
    return node;
  }

  function renderMessages() {
    const host = $("#messages");
    host.innerHTML = "";
    if (!State.messages.length && !State.pending.size) {
      host.appendChild(emptyNode());
      return;
    }
    State.messages.forEach((message) => host.appendChild(messageNode(message)));
    scrollMessages(true);
  }

  function emptyNode() {
    const node = document.createElement("div");
    node.className = "empty";
    node.innerHTML = '<div class="empty-orb"></div><p>Bu konuşmada henüz mesaj yok.</p><p class="hint small">Yazdıkların bilgisayardaki JARVIS\'e gider; aynı konuşmayı masaüstünde de görürsün.</p>';
    return node;
  }

  function scrollMessages(force) {
    const host = $("#messages");
    const nearBottom = host.scrollHeight - host.scrollTop - host.clientHeight < 120;
    if (force || nearBottom) host.scrollTo({ top: host.scrollHeight, behavior: reducedMotion ? "auto" : "smooth" });
  }

  function setPendingState(node, kind, text, retry) {
    let state = node.querySelector(".state");
    if (!state) { state = document.createElement("div"); state.className = "state"; node.appendChild(state); }
    state.className = "state " + (kind || "");
    state.innerHTML = esc(text || "");
    if (retry) {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "btn small";
      button.textContent = "Yeniden gönder";
      button.addEventListener("click", retry);
      state.appendChild(button);
    }
  }

  function appendPending(clientId, text, conversationId) {
    const host = $("#messages");
    const empty = host.querySelector(".empty");
    if (empty) empty.remove();
    host.appendChild(messageNode({ role: "user", text }));
    const node = document.createElement("div");
    node.className = "msg assistant pending";
    node.innerHTML = '<div class="body"></div>';
    host.appendChild(node);
    State.pending.set(clientId, { node, text, conversationId });
    setPendingState(node, "", "gönderiliyor…");
    scrollMessages(true);
    return node;
  }

  function finishPending(record) {
    const entry = State.pending.get(record.client_id);
    if (!entry) return;
    const { node } = entry;
    node.classList.remove("pending");
    const state = node.querySelector(".state");
    if (state) state.remove();
    if (record.status === "done") {
      node.className = "msg " + (record.role || "assistant");
      node.querySelector(".body").innerHTML = renderMarkdownLite(record.reply || "");
      const replacement = messageNode({ role: record.role || "assistant", text: record.reply || "", metadata: record.metadata });
      node.replaceWith(replacement);
      State.messages.push({ role: "user", text: entry.text }, { role: record.role || "assistant", text: record.reply || "", metadata: record.metadata });
    } else {
      node.className = "msg system";
      node.querySelector(".body").textContent = record.error || "İstek tamamlanamadı.";
      State.messages.push({ role: "user", text: entry.text }, { role: "system", text: record.error || "İstek tamamlanamadı." });
    }
    State.pending.delete(record.client_id);
    $("#orb").classList.toggle("busy", State.pending.size > 0);
    scrollMessages(false);
  }

  function applyTurn(record) {
    if (!record) return;
    const entry = State.pending.get(record.client_id);
    if (!entry) return;
    if (record.status === "running") {
      if (record.reply) entry.node.querySelector(".body").textContent = record.reply;
      setPendingState(entry.node, "", record.reply ? "" : "JARVIS yanıt yazıyor…");
      return;
    }
    finishPending(record);
  }

  function markUnknown(clientId) {
    const entry = State.pending.get(clientId);
    if (!entry) return;
    setPendingState(entry.node, "unknown", "Bağlantı koptu; sonuç bilinmiyor. Yeniden bağlanınca durum sorulacak.");
  }

  function markNeverReceived(clientId) {
    const entry = State.pending.get(clientId);
    if (!entry) return;
    setPendingState(entry.node, "failed", "Bu mesaj bilgisayara ulaşmamış.", () => resend(clientId));
  }

  async function resend(clientId) {
    const entry = State.pending.get(clientId);
    if (!entry) return;
    setPendingState(entry.node, "", "yeniden gönderiliyor…");
    await submit(entry.text, clientId, entry.conversationId, entry.node);
  }

  async function submit(text, clientId, conversationId, existingNode) {
    if (!existingNode) appendPending(clientId, text, conversationId);
    $("#orb").classList.add("busy");
    try {
      const result = await api("/api/chat", { method: "POST", body: { text, client_id: clientId, conversation_id: conversationId || undefined } });
      if (result.ok === false) {
        const entry = State.pending.get(clientId);
        if (entry) finishPending({ client_id: clientId, status: "failed", error: result.error || "Gönderilemedi." });
        return;
      }
      if (result.turn && result.turn.conversation_id && (!State.conversation || State.conversation.conversation_id !== result.turn.conversation_id)) {
        State.conversation = { conversation_id: result.turn.conversation_id, title: State.conversation ? State.conversation.title : "Yeni konuşma" };
      }
      applyTurn(result.turn);
    } catch (error) {
      if (error && error.offline) markUnknown(clientId);
      else if (!(error && error.unauthorized)) {
        const entry = State.pending.get(clientId);
        if (entry) finishPending({ client_id: clientId, status: "failed", error: error.error || "Gönderilemedi." });
      }
    } finally {
      $("#orb").classList.toggle("busy", State.pending.size > 0);
    }
  }

  async function onSend(event) {
    event.preventDefault();
    const input = $("#composer-input");
    const text = input.value.trim();
    if (!text || State.sending) return;
    if (State.paused) { toast("JARVIS masaüstünde duraklatıldı.", true); return; }
    const running = Array.from(State.pending.values()).some((entry) => !entry.node.querySelector(".state.unknown, .state.failed"));
    if (running) { toast("Önceki yanıt tamamlanmadan yeni mesaj gönderilemez.", true); return; }
    input.value = "";
    autosize(input);
    State.sending = true;
    try {
      await submit(text, uuid(), State.conversation ? State.conversation.conversation_id : null, null);
    } finally {
      State.sending = false;
    }
  }

  function autosize(input) {
    input.style.height = "auto";
    input.style.height = Math.min(input.scrollHeight, Math.round(window.innerHeight * 0.4)) + "px";
  }

  /* --------------------------------------------------- conversations */
  async function loadMessages(conversationId) {
    if (!conversationId) { State.messages = []; renderMessages(); return; }
    const result = await api("/api/conversations/" + conversationId + "/messages");
    if (result.ok === false) { toast(result.error || "Konuşma okunamadı.", true); return; }
    State.conversation = { conversation_id: result.conversation_id, title: result.title };
    State.messages = result.messages || [];
    $("#conv-title").textContent = result.title || "Yeni konuşma";
    renderMessages();
  }

  async function openSheet() {
    const sheet = $("#sheet");
    const host = $("#conv-list");
    host.innerHTML = '<p class="hint">Yükleniyor…</p>';
    sheet.hidden = false;
    let result;
    try { result = await api("/api/conversations"); } catch (error) { host.innerHTML = '<p class="hint">' + esc(error.error || "Bilgisayara ulaşılamıyor.") + "</p>"; return; }
    if (result.ok === false) { host.innerHTML = '<p class="hint">' + esc(result.error) + "</p>"; return; }
    const rows = result.conversations || [];
    if (!rows.length) { host.innerHTML = '<p class="hint">Kayıtlı konuşma yok.</p>'; return; }
    host.innerHTML = rows.map((row) => '<button type="button" class="item ' + (row.selected ? "selected" : "") + '" data-id="' + esc(row.conversation_id) + '"><span class="title">' + esc(row.title) + '</span><span class="sub">' + esc(row.turn_count) + " mesaj" + (row.status === "archived" ? " · arşiv" : "") + "</span></button>").join("");
    $$(".item", host).forEach((node) => node.addEventListener("click", async () => {
      sheet.hidden = true;
      try {
        const selected = await api("/api/conversations/" + node.dataset.id + "/select", { method: "POST", body: {} });
        if (selected.ok === false) { toast(selected.error || "Konuşma açılamadı.", true); return; }
        State.conversation = { conversation_id: selected.conversation_id, title: selected.title };
        State.messages = selected.messages || [];
        State.pending.clear();
        $("#conv-title").textContent = selected.title || "Yeni konuşma";
        renderMessages();
      } catch (error) { toast(error.error || "Bilgisayara ulaşılamıyor.", true); }
    }));
  }

  async function newConversation() {
    try {
      const result = await api("/api/conversations", { method: "POST", body: {} });
      if (result.ok === false) { toast(result.error || "Konuşma açılamadı.", true); return; }
      State.conversation = { conversation_id: result.conversation_id, title: result.title || "Yeni konuşma" };
      State.messages = [];
      State.pending.clear();
      $("#conv-title").textContent = "Yeni konuşma";
      renderMessages();
      $("#composer-input").focus();
    } catch (error) { toast(error.error || "Bilgisayara ulaşılamıyor.", true); }
  }

  /* ----------------------------------------------------------- tasks */
  /* The engine's English failure strings are stable machine values; an
     unknown one is shown verbatim rather than guessed at. */
  const TASK_ERROR_TR = {
    "Invalid tool_name.": "Adımın aracı tanımsız.",
    "Parameters must be a dictionary.": "Araç parametreleri geçersiz.",
    "Execution time budget exhausted.": "Süre bütçesi doldu; adım yarıda kesildi.",
    "Tool result has no explicit postcondition verification.": "Aracın sonucu doğrulanamadı.",
    "Plan could not be persisted safely.": "Plan güvenle kaydedilemedi.",
    "User confirmation required.": "Bu adım için onayın gerekiyor.",
  };
  const taskErrorTr = (text) => {
    const value = String(text == null ? "" : text).trim();
    return value ? (TASK_ERROR_TR[value] || value) : "";
  };

  async function loadTasks() {
    const host = $("#tasks-list");
    let result;
    try { result = await api("/api/tasks"); } catch (error) { host.innerHTML = '<p class="hint">' + esc(error.error || "Bilgisayara ulaşılamıyor.") + "</p>"; return; }
    if (result.ok === false) { host.innerHTML = '<p class="hint">' + esc(result.error) + "</p>"; return; }
    const rows = result.tasks || [];
    if (!rows.length) { host.innerHTML = '<div class="card"><p class="hint" style="margin:0">Kayıtlı görev yok. Sohbetten çok adımlı bir iş istediğinde adımları burada izlersin.</p></div>'; return; }
    host.innerHTML = rows.map((task) => {
      const status = String(task.status || "");
      const steps = (task.steps || []).slice(0, 6).map((step) => "<li>" + esc(step.name || step.description || step) + (step.status ? " · " + esc(step.status) : "") + "</li>").join("");
      const actions = task.actions || {};
      const buttons = ["pause", "resume", "cancel"].filter((action) => actions[action]).map((action) =>
        '<button type="button" class="btn small" data-task="' + esc(task.task_id) + '" data-action="' + action + '">' + ({ pause: "Duraklat", resume: "Sürdür", cancel: "İptal et" })[action] + "</button>").join("");
      return '<div class="task"><div class="head"><strong>' + esc(task.goal || task.title || task.description || task.task_id) + '</strong><span class="status ' + esc(status) + '">' + esc(status) + "</span></div>" +
        (steps ? '<ol class="steps">' + steps + "</ol>" : "") +
        (buttons ? '<div class="actions">' + buttons + "</div>" : '<div class="none">Bu görev için uygulanabilir işlem yok.</div>') + "</div>";
    }).join("");
    $$("[data-task]", host).forEach((button) => button.addEventListener("click", async () => {
      button.disabled = true;
      try {
        const done = await api("/api/tasks/" + button.dataset.task + "/" + button.dataset.action, { method: "POST", body: {} });
        toast(done.ok ? (done.message || "Tamam.") : (done.error || "İşlem başarısız."), !done.ok);
      } catch (error) { toast(error.error || "Bilgisayara ulaşılamıyor.", true); }
      loadTasks();
    }));
  }

  function scheduleTasks() {
    clearInterval(State.tasksTimer);
    State.tasksTimer = setInterval(() => { if (State.tab === "tasks" && !document.hidden && State.session) loadTasks(); }, 15000);
  }

  /* ------------------------------------------------------- approvals */
  function applyApprovals(items) {
    State.approvals.clear();
    (items || []).forEach((item) => State.approvals.set(item.token, item));
    renderApprovals();
  }

  function renderApprovals() {
    const badge = $("#tasks-badge");
    const count = State.approvals.size;
    badge.textContent = String(count);
    badge.hidden = count === 0;
    const list = $("#approvals-list");
    list.innerHTML = Array.from(State.approvals.values()).map((item) => '<div class="approval-card"><strong>' + esc(item.description || item.tool) + '</strong><div class="hint small">' + esc(item.reason || "") + '</div><button type="button" class="btn small" data-approval="' + esc(item.token) + '">Onayı gör</button></div>').join("");
    $$("[data-approval]", list).forEach((button) => button.addEventListener("click", () => showApproval(button.dataset.approval)));
    const first = State.approvals.values().next().value;
    if (first) showApproval(first.token); else hideApproval();
  }

  function showApproval(token) {
    const item = State.approvals.get(token);
    if (!item) { hideApproval(); return; }
    const sheet = $("#approval");
    sheet.dataset.token = token;
    const params = Object.entries(item.parameters || {}).map(([key, value]) => '<div class="approval-row"><span>' + esc(key) + "</span><strong>" + esc(value) + "</strong></div>").join("");
    $("#approval-body").innerHTML = '<div class="approval-row"><span>Araç</span><strong>' + esc(item.description || item.tool) + '</strong></div>' +
      '<div class="approval-row"><span>İşlem</span><strong>' + esc(item.operation || item.tool) + '</strong></div>' +
      '<div class="approval-row"><span>Risk</span><strong class="risk ' + esc(item.risk) + '">' + esc(item.risk) + '</strong></div>' +
      '<div class="approval-row"><span>Neden</span><strong>' + esc(item.reason || "—") + "</strong></div>" + params;
    sheet.hidden = false;
    clearInterval(State.approvalTimer);
    let remaining = Number(item.seconds) || 0;
    const timer = $("#approval-timer");
    const tick = () => { timer.textContent = remaining > 0 ? remaining + " sn" : "süre doldu"; remaining -= 1; };
    tick();
    State.approvalTimer = setInterval(tick, 1000);
  }

  function hideApproval() {
    $("#approval").hidden = true;
    clearInterval(State.approvalTimer);
  }

  async function decideApproval(approved) {
    const token = $("#approval").dataset.token;
    if (!token) return;
    $("#approval-allow").disabled = $("#approval-deny").disabled = true;
    try {
      const result = await api("/api/approvals/" + token, { method: "POST", body: { approved } });
      if (result.ok === false) toast(result.error || "Onay iletilemedi.", true);
      else toast(approved ? "Onaylandı." : "Reddedildi.");
    } catch (error) { toast(error.error || "Bilgisayara ulaşılamıyor.", true); }
    $("#approval-allow").disabled = $("#approval-deny").disabled = false;
    State.approvals.delete(token);
    renderApprovals();
  }

  /* ---------------------------------------------------------- events */
  function closeEvents() {
    if (State.es) { State.es.close(); State.es = null; }
    clearTimeout(State.reconnectTimer);
  }

  function connectEvents() {
    if (State.es || !State.session) return;
    const es = new EventSource("/api/events");
    State.es = es;
    es.addEventListener("hello", (event) => {
      State.backoff = 1000;
      setConnected(true);
      try { applyApprovals(JSON.parse(event.data).pending_approvals); } catch (_error) { /* ignore */ }
    });
    es.addEventListener("delta", (event) => {
      const data = JSON.parse(event.data);
      const entry = State.pending.get(data.client_id);
      if (!entry) return;
      const body = entry.node.querySelector(".body");
      body.textContent += data.text;
      const state = entry.node.querySelector(".state");
      if (state) state.remove();
      scrollMessages(false);
    });
    es.addEventListener("turn_started", (event) => {
      const data = JSON.parse(event.data);
      const entry = State.pending.get(data.client_id);
      if (entry) setPendingState(entry.node, "", "JARVIS yanıt yazıyor…");
    });
    es.addEventListener("turn_done", (event) => finishPending(JSON.parse(event.data)));
    es.addEventListener("approval", (event) => { const item = JSON.parse(event.data); State.approvals.set(item.token, item); renderApprovals(); if (navigator.vibrate) navigator.vibrate(40); });
    es.addEventListener("approval_closed", (event) => { const item = JSON.parse(event.data); State.approvals.delete(item.token); renderApprovals(); });
    es.addEventListener("session_ended", () => { closeEvents(); onUnauthorized("Bu cihazın oturumu bilgisayardan kapatıldı."); });
    es.onerror = () => {
      closeEvents();
      setConnected(false);
      scheduleReconnect();
    };
  }

  function scheduleReconnect() {
    clearTimeout(State.reconnectTimer);
    State.reconnectTimer = setTimeout(() => { State.backoff = Math.min(State.backoff * 2, 30000); resync(); }, State.backoff);
  }

  /* ---------------------------------------------------------- resync */
  async function resync() {
    let state;
    try {
      state = await api("/api/state");
    } catch (error) {
      if (error && error.unauthorized) return;
      setConnected(false);
      State.pending.forEach((_entry, clientId) => markUnknown(clientId));
      scheduleReconnect();
      return;
    }
    if (state.ok === false) { scheduleReconnect(); return; }
    applyState(state);
    // Every message still waiting locally is asked about, never re-sent.
    for (const [clientId, entry] of Array.from(State.pending.entries())) {
      try {
        const answer = await api("/api/turns/" + clientId);
        if (answer.ok === false) markNeverReceived(clientId);
        else applyTurn(answer.turn);
      } catch (_error) {
        markUnknown(clientId);
      }
    }
    connectEvents();
  }

  function applyState(state) {
    State.session = state.session;
    State.pc = state.pc || "";
    State.paused = !!state.paused;
    State.stamp = state.stamp || "";
    $("#pc-name").textContent = State.pc;
    $("#paused").hidden = !State.paused;
    $("#set-pc").textContent = State.pc || "—";
    $("#set-origin").textContent = location.host;
    $("#set-label").textContent = state.session.label || "—";
    $("#set-expires").textContent = state.session.expires_at ? new Date(state.session.expires_at).toLocaleString("tr-TR") : "—";
    $("#set-stamp").textContent = State.stamp || "—";
    applyApprovals(state.pending_approvals);
    const serverConversation = state.conversation ? state.conversation.conversation_id : null;
    const localConversation = State.conversation ? State.conversation.conversation_id : null;
    if (serverConversation !== localConversation && !State.pending.size) {
      State.conversation = state.conversation;
      $("#conv-title").textContent = state.conversation ? state.conversation.title : "Yeni konuşma";
      loadMessages(serverConversation);
    }
  }

  async function boot() {
    setConn("warn", "bağlanıyor");
    let state;
    try { state = await api("/api/state"); } catch (error) {
      if (error && error.unauthorized) return;
      setConnected(false);
      showScreen("chat");
      scheduleReconnect();
      return;
    }
    applyState(state);
    // The paired phone gets the whole desktop page unless the light
    // client was asked for explicitly (?lite=1).
    if (new URLSearchParams(location.search).get("lite") !== "1") {
      window.location.replace("/nova/");
      return;
    }
    showScreen("chat");
    await loadMessages(state.conversation ? state.conversation.conversation_id : null);
    connectEvents();
    scheduleTasks();
  }

  /* --------------------------------------------------------- pairing */
  async function onPair(event) {
    event.preventDefault();
    const code = $("#pair-code").value.trim();
    const label = $("#pair-label").value.trim() || "Telefon";
    const error = $("#pair-error");
    error.hidden = true;
    $("#pair-submit").disabled = true;
    try {
      const result = await api("/api/pair", { method: "POST", body: { code, label } });
      if (result.ok === false) { error.textContent = result.error || "Eşleştirme başarısız."; error.hidden = false; return; }
      $("#pair-code").value = "";
      await boot();
    } catch (err) {
      if (err && err.unauthorized) { error.textContent = err.error || "Eşleştirme kodu kabul edilmedi."; error.hidden = false; return; }
      error.textContent = err.error || "Bilgisayara ulaşılamıyor.";
      error.hidden = false;
    } finally {
      $("#pair-submit").disabled = false;
    }
  }

  async function logout() {
    try { await api("/api/logout", { method: "POST", body: {} }); } catch (_error) { /* the cookie is gone either way */ }
    State.pending.clear();
    State.messages = [];
    onUnauthorized("");
    $("#pair-error").hidden = true;
  }

  /* ------------------------------------------------------------ bind */
  function bind() {
    $("#pair-form").addEventListener("submit", onPair);
    $("#pair-code").addEventListener("input", (event) => {
      const clean = event.target.value.toUpperCase().replace(/[^A-Z0-9]/g, "").slice(0, 8);
      event.target.value = clean.length > 4 ? clean.slice(0, 4) + "-" + clean.slice(4) : clean;
    });
    $("#composer").addEventListener("submit", onSend);
    const input = $("#composer-input");
    input.addEventListener("input", () => autosize(input));
    input.addEventListener("keydown", (event) => {
      if (event.key === "Enter" && !event.shiftKey && !event.isComposing) { event.preventDefault(); $("#composer").requestSubmit(); }
    });
    input.addEventListener("focus", () => setTimeout(() => scrollMessages(true), 250));
    $$(".tab").forEach((tab) => tab.addEventListener("click", () => showScreen(tab.dataset.tab)));
    $("#conv-pick").addEventListener("click", openSheet);
    $("#conv-new").addEventListener("click", newConversation);
    $("#sheet-close").addEventListener("click", () => { $("#sheet").hidden = true; });
    $("#sheet").addEventListener("click", (event) => { if (event.target === $("#sheet")) $("#sheet").hidden = true; });
    $("#tasks-refresh").addEventListener("click", loadTasks);
    $("#approval-allow").addEventListener("click", () => decideApproval(true));
    $("#approval-deny").addEventListener("click", () => decideApproval(false));
    $("#logout").addEventListener("click", logout);
    $("#conn").addEventListener("click", () => { if (!State.connected) resync(); });
    document.addEventListener("visibilitychange", () => {
      if (document.hidden) return;
      if (State.session && !State.es) resync();
      if (State.tab === "tasks" && State.session) loadTasks();
    });
    window.addEventListener("online", () => { $("#offline").hidden = true; if (State.session) resync(); });
    window.addEventListener("offline", () => { $("#offline").hidden = false; setConn("bad", "çevrimdışı"); });
    if ("serviceWorker" in navigator) navigator.serviceWorker.register("/sw.js").catch(() => {});
  }

  bind();
  if (new URLSearchParams(location.search).get("expired") === "1") {
    const error = $("#pair-error");
    error.textContent = "Oturum yok ya da sona erdi; telefonu yeniden eşleştir.";
    error.hidden = false;
  }
  boot();
})();
