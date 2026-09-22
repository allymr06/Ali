/* ════════════════════════════════════════════════════════════════════
   JARVIS · NOVA — conversation
   The chat surface (messages, streaming, conditional auto-scroll), the
   stored-conversation list, command submission, and the voice
   experience (immersive stage, captions, microphone level).
   ════════════════════════════════════════════════════════════════════ */
"use strict";

/* ── messages ─────────────────────────────────────────────────────── */

/* Web sources for an answer that ran research_web this turn. Hosts are
   shown, the full URL rides the tooltip, and a click reruns the exact
   research on its own screen (served from the cache). */
function researchSourcesMarkup(payload) {
  const sources = (payload && payload.sources) || [];
  if (!sources.length) return "";
  const host = (url) => {
    const match = /^[a-z]+:\/\/([^\/?#]+)/i.exec(String(url || ""));
    return match ? match[1].replace(/^www\./, "") : "";
  };
  const chips = sources.map((source) => {
    const name = host(source.url) || "kaynak";
    return `<button type="button" class="chip" data-open-url="${esc(source.url || "")}" title="${esc(source.title || "")} — ${esc(source.url || "")} · tarayıcıda açar">${esc(name)}</button>`;
  });
  const report = `<button type="button" class="chip accent" data-research-query="${esc(payload.query || "")}" title="Tam raporu Araştırma ekranında açar">rapor</button>`;
  return `<div class="msg-sources"><span class="msg-sources-label">Web kaynakları</span>${report}${chips.join("")}</div>`;
}

function bindResearchChips(node) {
  $$("[data-research-query]", node).forEach((chip) => chip.addEventListener("click", () => {
    showScreen("research");
    const input = $("#research-input");
    if (input && chip.dataset.researchQuery) {
      input.value = chip.dataset.researchQuery;
      $("#research-submit")?.click();
    }
  }));
  $$("[data-open-url]", node).forEach((chip) => chip.addEventListener("click", async () => {
    const result = await call("open_external", chip.dataset.openUrl);
    if (result.ok === false) toast(result.error || "Bağlantı açılamadı.", true);
  }));
}

function assuranceChips(metadata) {
  if (!metadata || typeof metadata !== "object") return "";
  const chips = [];
  if (metadata.assurance_level) chips.push(`<span class="chip">güvence · ${esc(tr(metadata.assurance_level))}</span>`);
  if (metadata.reasoning_level) chips.push(`<span class="chip violet">muhakeme · ${esc(tr(metadata.reasoning_level))}</span>`);
  if (metadata.uncertainty_summary) chips.push(`<span class="chip warn" title="${esc(metadata.uncertainty_summary)}">belirsizlik</span>`);
  /* Real numbers from the core's own clock: how long the answer took and
     how many tools ran. Absent metadata means no chip, never a guess. */
  const seconds = Number(metadata.elapsed_seconds);
  if (Number.isFinite(seconds) && seconds >= 0) chips.push(`<span class="chip mono" title="Çekirdek yanıt süresi">${esc(fmtSecondsTr(seconds))}</span>`);
  const toolCalls = Number(metadata.tool_calls);
  if (Number.isFinite(toolCalls) && toolCalls > 0) chips.push(`<span class="chip">araç · ${toolCalls}</span>`);
  return chips.length ? `<div class="assurance">${chips.join("")}</div>` : "";
}

function fmtSecondsTr(seconds) {
  const digits = seconds >= 10 ? 0 : 1;
  return `${seconds.toLocaleString("tr-TR", { minimumFractionDigits: digits, maximumFractionDigits: digits })} sn`;
}

/* ── mini-Markdown for assistant bubbles ─────────────────────────────
   The model answers with light Markdown; showing the asterisks raw is
   noise. This renders ONLY a safe subset - bold, italics, inline code,
   simple lists, heading lines - after escaping everything, so no HTML
   from the model or a web page ever executes. Anything else stays
   literal text. */
function renderMarkdownLite(raw) {
  const escaped = esc(String(raw ?? ""));
  const inline = (text) => text
    .replace(/`([^`]+)`/g, "<code>$1</code>")
    .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
    .replace(/(^|[\s(])\*([^*\s][^*]*)\*(?=[\s).,;:!?]|$)/g, "$1<em>$2</em>");
  const lines = escaped.split(/\r?\n/);
  const parts = [];
  let list = null; // "ul" | "ol" | null
  let fence = null; // collected lines of an open ``` block
  let fenceLang = ""; // the opener's info word, only when it is a clean token
  const isRow = (line) => /^\s*\|.*\|\s*$/.test(line || "");
  const isRule = (line) => isRow(line) && /^[\s|:\-]+$/.test(line || "") && (line || "").includes("-");
  const cells = (line) => String(line).trim().replace(/^\|/, "").replace(/\|$/, "").split("|").map((cell) => cell.trim());
  const closeList = () => { if (list) { parts.push(`</${list}>`); list = null; } };
  const closeFence = () => {
    if (fence === null) return;
    const lang = fenceLang ? `<span class="code-lang">${fenceLang}</span>` : "";
    parts.push('<pre class="md-code">' + lang + '<button type="button" class="code-copy" data-code-copy title="Kodu kopyala">⎘</button><code>'
      + fence.join("\n") + "</code></pre>");
    fence = null;
    fenceLang = "";
  };
  for (let at = 0; at < lines.length; at += 1) {
    const line = lines[at];
    // ``` opens and closes a literal block; inline markdown stays out
    // of it, and a stream cut mid-block still renders what arrived.
    const fenceMark = /^\s*```\s*(\S*)/.exec(line);
    if (fenceMark) {
      if (fence === null) {
        closeList();
        fence = [];
        // The info word rides the badge only as a clean token; anything
        // stranger (already HTML-escaped here) earns no badge at all.
        fenceLang = /^[A-Za-z0-9_+#.\-]{1,24}$/.test(fenceMark[1]) ? fenceMark[1] : "";
      } else closeFence();
      continue;
    }
    if (fence !== null) { fence.push(line); continue; }
    // A table is a header row, a rule, then body rows - anything less
    // stays plain text, so a stray pipe never becomes a broken grid.
    if (isRow(line) && !isRule(line) && isRule(lines[at + 1])) {
      closeList();
      const header = cells(line);
      const body = [];
      let cursor = at + 2;
      while (cursor < lines.length && isRow(lines[cursor]) && !isRule(lines[cursor])) {
        body.push(cells(lines[cursor]));
        lines[cursor] = "\u0000consumed"; // this row is the table's now
        cursor += 1;
      }
      parts.push('<table class="md-table"><thead><tr>'
        + header.map((cell) => `<th>${inline(cell)}</th>`).join("")
        + "</tr></thead><tbody>"
        + body.map((row) => "<tr>" + header.map((cell, index) => `<td>${inline(row[index] || "")}</td>`).join("") + "</tr>").join("")
        + "</tbody></table>");
      lines[at + 1] = "\u0000consumed";
      continue;
    }
    if (line === "\u0000consumed") continue;
    const bullet = /^\s*[-•] +(.*)$/.exec(line);
    const numbered = /^\s*\d+[.)] +(.*)$/.exec(line);
    const heading = /^\s*#{1,4} +(.*)$/.exec(line);
    if (bullet || numbered) {
      const kind = bullet ? "ul" : "ol";
      if (list !== kind) { closeList(); parts.push(`<${kind}>`); list = kind; }
      parts.push(`<li>${inline((bullet || numbered)[1])}</li>`);
      continue;
    }
    closeList();
    if (heading) { parts.push(`<div class="md-h">${inline(heading[1])}</div>`); continue; }
    if (!line.trim()) { parts.push('<div class="md-gap"></div>'); continue; }
    parts.push(`<div>${inline(line)}</div>`);
  }
  closeList();
  closeFence();
  return parts.join("");
}

/* ── drawer pins & in-chat find (pure) ─────────────────────────── */

/* Pins live in this device's localStorage, not in the conversation
   store: a convenience of this screen, honest about its scope. */
function convPinsParse(rawValue) {
  return new Set(String(rawValue || "").split(",").map((piece) => piece.trim()).filter(Boolean));
}

function convOrder(items, pins, { hideArchived = false } = {}) {
  const source = Array.isArray(items) ? items : [];
  // The active thread stays visible even when archived threads hide:
  // the list must never lose the conversation that is open right now.
  const list = hideArchived
    ? source.filter((item) => item.status !== "archived" || item.active)
    : source;
  return {
    pinned: list.filter((item) => pins.has(item.conversation_id)),
    rest: list.filter((item) => !pins.has(item.conversation_id)),
    hiddenCount: source.length - list.length,
  };
}

/* Which message indexes match the query; null means the filter is off. */
function chatFindFilter(texts, query) {
  const needle = searchFold(String(query || "").trim());
  if (!needle) return null;
  const hits = [];
  (texts || []).forEach((text, index) => {
    if (searchFold(text).includes(needle)) hits.push(index);
  });
  return hits;
}

/* The copy control every full-size bubble carries. */
function copyButton(slim) {
  return slim ? "" : '<button type="button" class="msg-copy" data-copy title="Metni kopyala">⎘</button>';
}

function appendMessage(host, message, slim, { animate = true } = {}) {
  if (!host || !message || !String(message.text ?? "").trim()) return null;
  const node = el("div", `msg ${esc(message.role)}`);
  const roleLabel = message.role === "user" ? "SEN" : message.role === "assistant" ? "JARVIS" : "";
  const time = message.at ? `<span class="msg-time">${esc(fmtClock(new Date(message.at)))}</span>` : "";
  const speakButton = message.role === "assistant" && !slim && State.snapshot?.voice_available
    ? '<button type="button" class="msg-speak" data-speak title="Sesli oku">🔊</button>' : "";
  node.innerHTML =
    (roleLabel && !slim ? `<div class="msg-meta"><span class="msg-role">${roleLabel}</span>${time}${speakButton}${copyButton(slim)}</div>` : "") +
    `<div class="msg-body"></div>` +
    (message.role === "assistant" && !slim ? assuranceChips(message.metadata) : "");
  if (message.role === "assistant") node.querySelector(".msg-body").innerHTML = renderMarkdownLite(message.text);
  else node.querySelector(".msg-body").textContent = message.text;
  host.appendChild(node);
  const find = typeof document !== "undefined" ? document.getElementById("chat-find") : null;
  if (find && find.value.trim()) applyChatFind();
  if (animate) Motion.rise(node, { y: 10, duration: 360 });
  return node;
}

function ensurePendingBubble() {
  if (State.pendingEl) return State.pendingEl;
  const host = $("#chat-list");
  const node = el("div", "msg assistant pending");
  node.innerHTML = '<div class="msg-meta"><span class="msg-role">JARVIS</span></div><div class="msg-body"></div>';
  host.appendChild(node);
  State.pendingEl = node;
  hideChatEmpty();
  return node;
}

function showThinking() {
  const node = ensurePendingBubble();
  node.querySelector(".msg-body").innerHTML =
    '<span class="thinking"><span class="orbit"></span>düşünüyor…</span>';
}

function finalizePendingBubble(message) {
  const node = State.pendingEl;
  State.pendingEl = null;
  message.at = message.at || Date.now();
  if (node) {
    if (message.role === "assistant") {
      node.classList.remove("pending");
      const meta = node.querySelector(".msg-meta");
      if (meta) meta.innerHTML = `<span class="msg-role">JARVIS</span><span class="msg-time">${esc(fmtClock(new Date(message.at)))}</span>`;
      node.querySelector(".msg-body").innerHTML = renderMarkdownLite(message.text);
      node.insertAdjacentHTML("beforeend", assuranceChips(message.metadata));
      if (State.pendingSources) {
        node.insertAdjacentHTML("beforeend", researchSourcesMarkup(State.pendingSources));
        bindResearchChips(node);
        State.pendingSources = null;
      }
      return;
    }
    node.remove();
  }
  appendMessage($("#chat-list"), message, false);
}

/* The busy push opens a turn on every surface that did not start it, and
   only the answer closes it again. A submission that produces no answer —
   refused by the runner, cancelled on the way down — ends with busy:false
   and nothing else, and an unclosed bubble is handed to the NEXT question
   by ensurePendingBubble. sendCommand does this for the page that typed
   the message; this is the same cleanup for the page that only watched. */
function closeWatchedTurn() {
  const turn = State.watchedTurn;
  State.watchedTurn = null;
  if (!turn) return;
  State.pendingEl?.remove();
  State.pendingEl = null;
  if (Activity.current === turn) Activity.abortTurn("Yanıt gelmeden tur kapandı.");
}

function hideChatEmpty() { const empty = $("#chat-empty"); if (empty) empty.hidden = true; }

function renderChatHistory() {
  const host = $("#chat-list");
  host.innerHTML = "";
  State.pendingEl = null;
  State.messages.forEach((message) => appendMessage(host, message, false, { animate: false }));
  $("#chat-empty").hidden = State.messages.length > 0;
  renderChatTitle();
}

function renderChatTitle() {
  const active = State.conversations.find((item) => item.active);
  $("#chat-title").textContent = active ? active.title : (State.messages.length ? "Konuşma" : "Yeni konuşma");
  // The pencil exists only when there is a stored thread to rename.
  const pencil = $("#chat-rename");
  if (pencil) pencil.hidden = !active;
}

async function renameActiveConversation() {
  const active = State.conversations.find((item) => item.active);
  if (!active) return;
  const name = await promptDialog({
    title: "Konuşmayı yeniden adlandır",
    body: "Boş bırakırsan başlık otomatiğe döner (ilk mesajın).",
    value: active.title, placeholder: "Yeni başlık",
  });
  if (name === null) return;
  const result = await call("rename_conversation", active.conversation_id, name);
  if (result.ok === false) { toast(result.error || "Adlandırılamadı.", true); return; }
  toast(result.message, "ok");
  refreshConversations();
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

/* ── sending ──────────────────────────────────────────────────────── */

async function sendCommand(raw) {
  const text = String(raw ?? "").trim();
  if (State.paused) { toast(PAUSED_NOTICE, true); return; }
  if (!text || State.busy || !bridgeReady()) return;
  const message = { role: "user", text, at: Date.now() };
  // The palette's short memory: this device only, five entries, newest first.
  store("nova.palette.recent", paletteRecentAdd(store("nova.palette.recent"), text));
  State.pendingSources = null; // sources belong to the turn that earned them
  hideChatEmpty();
  updateChat(() => {
    appendMessage($("#chat-list"), message, false);
    Activity.beginTurn(text);
    showThinking();
  }, { force: true });
  State.messages.push(message);
  setBusy(true, "PROCESSING");
  renderHomeSession();
  const result = await call("submit_command", text);
  if (result.ok === false) {
    State.pendingEl?.remove(); State.pendingEl = null;
    Activity.abortTurn(result.error || "Komut gönderilemedi.");
    setBusy(false, READY);
    toast(result.error || "Komut gönderilemedi.", true);
    Presence.error("komut gönderilemedi");
  }
}

/* ── stored conversations ─────────────────────────────────────────── */

/* Which drawer group a conversation belongs to, by calendar days
   between local midnights - so 23:59 yesterday is still "Dün". */
function convGroupLabel(iso, now) {
  const day = (value) => { const d = new Date(value); d.setHours(0, 0, 0, 0); return d.getTime(); };
  const diff = Math.round((day(now || Date.now()) - day(iso)) / 86400000);
  if (!Number.isFinite(diff) || diff < 0) return "Bugün";
  if (diff === 0) return "Bugün";
  if (diff === 1) return "Dün";
  if (diff <= 7) return "Bu hafta";
  if (diff <= 31) return "Bu ay";
  return "Daha eski";
}

function renderConversations() {
  const host = $("#conv-items");
  if (!host) return;
  const items = State.conversations || [];
  if (!items.length) {
    host.innerHTML = '<div class="ctx-empty" style="padding:.8rem .4rem">Henüz kayıtlı konuşma yok.</div>';
    renderChatTitle();
    return;
  }
  const pins = convPinsParse(store("nova.conv.pins"));
  const hideArchived = store("nova.conv.hidearchive") === "1";
  const ordered = convOrder(items, pins, { hideArchived });
  const row = (item) => `
    <button type="button" class="conv-item ${item.active ? "active" : ""}" data-id="${esc(item.conversation_id)}" title="${esc(item.title)}">
      <span class="conv-title">${esc(item.title)}</span>
      <span class="conv-meta"><span>${item.turn_count} mesaj${item.status === "archived" ? " · arşiv" : ""}</span><span>${esc(fmtRelative(item.updated_at))}</span></span>
      <span class="conv-ren" data-ren="${esc(item.conversation_id)}" title="Yeniden adlandır">✎</span>
      <span class="conv-pin ${pins.has(item.conversation_id) ? "on" : ""}" data-pin="${esc(item.conversation_id)}" title="${pins.has(item.conversation_id) ? "Sabitlemeyi kaldır" : "Sabitle (bu cihazda)"}">📌</span>
    </button>`;
  let group = null;
  const parts = [];
  if (ordered.pinned.length) {
    parts.push('<div class="conv-group">Sabitlenmiş</div>');
    ordered.pinned.forEach((item) => parts.push(row(item)));
  }
  ordered.rest.forEach((item) => {
    const label = convGroupLabel(item.updated_at);
    if (label !== group) { parts.push(`<div class="conv-group">${label}</div>`); group = label; }
    parts.push(row(item));
  });
  if (ordered.hiddenCount) {
    parts.push(`<button type="button" class="conv-archtoggle" data-arch-show>${ordered.hiddenCount} arşivli konuşma gizli · göster</button>`);
  } else if (hideArchived) {
    parts.push('<button type="button" class="conv-archtoggle" data-arch-show>Arşivliler gizleniyor · göster</button>');
  } else if (items.some((item) => item.status === "archived")) {
    parts.push('<button type="button" class="conv-archtoggle" data-arch-hide>Arşivlileri gizle</button>');
  }
  host.innerHTML = parts.join("");
  $$("[data-arch-show]", host).forEach((node) => node.addEventListener("click", () => {
    store("nova.conv.hidearchive", "0");
    renderConversations();
  }));
  $$("[data-arch-hide]", host).forEach((node) => node.addEventListener("click", () => {
    store("nova.conv.hidearchive", "1");
    renderConversations();
  }));
  $$(".conv-pin", host).forEach((node) => node.addEventListener("click", (event) => {
    event.stopPropagation();
    const current = convPinsParse(store("nova.conv.pins"));
    if (current.has(node.dataset.pin)) current.delete(node.dataset.pin);
    else current.add(node.dataset.pin);
    store("nova.conv.pins", [...current].join(","));
    renderConversations();
  }));
  $$(".conv-ren", host).forEach((node) => node.addEventListener("click", async (event) => {
    event.stopPropagation();
    const item = items.find((entry) => entry.conversation_id === node.dataset.ren);
    if (!item) return;
    const name = await promptDialog({
      title: "Konuşmayı yeniden adlandır",
      body: "Boş bırakırsan başlık otomatiğe döner (ilk mesajın).",
      value: item.title, placeholder: "Yeni başlık",
    });
    if (name === null) return;
    const result = await call("rename_conversation", item.conversation_id, name);
    if (result.ok === false) { toast(result.error || "Adlandırılamadı.", true); return; }
    toast(result.message, "ok");
    refreshConversations();
  }));
  $$(".conv-item", host).forEach((node) => {
    node.addEventListener("click", () => openConversation(node.dataset.id));
    node.addEventListener("contextmenu", async (event) => {
      event.preventDefault();
      const item = items.find((entry) => entry.conversation_id === node.dataset.id);
      if (!item) return;
      if (item.status === "archived") {
        const restore = await confirmDialog({
          title: "Arşivden çıkarılsın mı?",
          body: `“${item.title}” yeniden aktif listeye dönecek.`,
          confirmLabel: "ÇIKAR",
        });
        if (restore) {
          const result = await call("unarchive_conversation", item.conversation_id);
          if (result.ok === false) { toast(result.error || "Çıkarılamadı.", true); return; }
          toast("Konuşma arşivden çıkarıldı.", "ok");
          refreshConversations();
        }
        return;
      }
      const confirmed = await confirmDialog({
        title: "Konuşma arşivlensin mi?",
        body: `“${item.title}” arşive kaldırılacak. Arşivdeki konuşmalar silinmez; listeden tekrar açılabilir.`,
        confirmLabel: "ARŞİVLE",
      });
      if (confirmed) archiveConversation(item.conversation_id);
    });
  });
  renderChatTitle();
}

/* Search across stored conversations: matching titles and excerpts of
   what was actually said, newest first. Pure builder, testable alone. */
function convSearchMarkup(payload) {
  if (!payload || payload.ok === false) return `<div class="ctx-empty" style="padding:.8rem .4rem">${esc((payload && payload.error) || "Arama yapılamadı.")}</div>`;
  const rows = payload.results || [];
  if (!rows.length) return `<div class="ctx-empty" style="padding:.8rem .4rem">“${esc(payload.query)}” hiçbir konuşmada geçmiyor.</div>`;
  const mark = (text) => {
    const safe = esc(text);
    const needle = esc(payload.query);
    const index = searchFold(safe).indexOf(searchFold(needle));
    if (index < 0) return safe;
    return safe.slice(0, index) + "<mark>" + safe.slice(index, index + needle.length) + "</mark>" + safe.slice(index + needle.length);
  };
  return rows.map((item) => `
    <button type="button" class="conv-item ${item.active ? "active" : ""}" data-id="${esc(item.conversation_id)}" title="${esc(item.title)}">
      <span class="conv-title">${mark(item.title)}</span>
      ${item.excerpt ? `<span class="conv-excerpt">${item.excerpt_role === "user" ? "Sen: " : ""}${mark(item.excerpt)}</span>` : ""}
      <span class="conv-meta"><span>${item.matches} eşleşme${item.status === "archived" ? " · arşiv" : ""}</span><span>${esc(fmtRelative(item.updated_at))}</span></span>
    </button>`).join("");
}

let convSearchTimer = 0;
async function runConvSearch(raw) {
  const host = $("#conv-items");
  if (!host) return;
  const query = String(raw || "").trim();
  if (query.length < 2) { renderConversations(); return; }
  const payload = await call("search_conversations", query);
  host.innerHTML = convSearchMarkup(payload);
  $$(".conv-item", host).forEach((node) => node.addEventListener("click", () => {
    openConversation(node.dataset.id);
    const box = $("#conv-search");
    if (box) box.value = "";
  }));
}

function bindConvSearch() {
  const box = $("#conv-search");
  if (!box) return;
  box.addEventListener("input", () => {
    clearTimeout(convSearchTimer);
    convSearchTimer = setTimeout(() => runConvSearch(box.value), 250);
  });
  box.addEventListener("keydown", (event) => {
    if (event.key === "Escape") { box.value = ""; renderConversations(); }
    if (event.key === "Enter") {
      const first = $("#conv-items .conv-item");
      if (first) first.click();
    }
  });
}

async function refreshConversations() {
  const result = await call("list_conversations");
  if (result.ok === false) return;
  State.conversations = result.conversations || [];
  renderConversations();
}

function adoptConversation(result) {
  State.messages = (result.messages || []).map((message) => Object.assign({}, message));
  State.voiceMessages = [];
  $("#voice-list").innerHTML = "";
  Activity.reset();
  renderChatHistory();
  scrollChat({ force: true, instant: true });
  renderHomeSession();
}

async function openConversation(conversationId) {
  const result = await call("open_conversation", conversationId);
  if (result.ok === false) { toast(result.error || "Konuşma açılamadı.", true); return; }
  adoptConversation(result);
  await refreshConversations();
  showScreen("chat");
}

async function newConversation() {
  if (State.busy) { toast("Yanıt tamamlanmadan yeni konuşma açılamaz.", true); return; }
  const result = await call("new_conversation");
  if (result.ok === false) { toast(result.error || "Yeni konuşma açılamadı.", true); return; }
  adoptConversation(result);
  State.conversations = State.conversations.map((item) => Object.assign({}, item, { active: false }));
  renderConversations();
  showScreen("chat");
  toast("Yeni konuşma başladı.", "ok");
}

async function archiveConversation(conversationId) {
  const result = await call("archive_conversation", conversationId);
  if (result.ok === false) { toast(result.error || "Konuşma arşivlenemedi.", true); return; }
  if (result.current_archived) adoptConversation({ messages: [] });
  await refreshConversations();
  toast("Konuşma arşivlendi.", "ok");
}

/* ── voice ────────────────────────────────────────────────────────── */

const VOICE_HINTS = {
  listening: "konuşabilirsin · bitirmek için Esc",
  transcribing: "söylediğin çözümleniyor",
  processing: "yanıt düşünülüyor",
  synthesizing: "ses üretiliyor",
  speaking: "sözünü kesmek için tıkla ya da Esc",
};

const VoiceStage = {
  get active() { const host = $("#voice-stage"); return !!host && !host.hidden; },
  _captionTimers: [],

  open() {
    const host = $("#voice-stage");
    if (!host.hidden) return;
    host.hidden = false;
    $("#voice-captions").innerHTML = "";
    this.phase(State.voicePhase);
    Engine.resize();
    Engine.wake();
    Motion.fade(host, { duration: Motion.cinematic * 0.7 });
    if (Motion.allowed()) {
      host.querySelector(".voice-core-frame").animate(
        [{ transform: "scale(0.86)", opacity: 0 }, { transform: "scale(1)", opacity: 1 }],
        { duration: Motion.cinematic, easing: Motion.enter });
    }
    $("#voice-close").focus();
  },

  close() {
    const host = $("#voice-stage");
    if (host.hidden) return;
    const finish = () => { if (!State.voiceActive) { host.hidden = true; Engine.resize(); } };
    if (!Motion.allowed()) { finish(); return; }
    const fade = host.animate([{ opacity: 1 }, { opacity: 0 }], { duration: 360, easing: Motion.exit });
    fade.onfinish = finish;
    setTimeout(finish, 480);   // safety net if the animation never finishes
  },

  phase(phase) {
    const key = phase || "listening";
    $("#voice-phase").textContent = VOICE_PHASE_TR[key] || "DİNLİYOR";
    $("#voice-hint").textContent = VOICE_HINTS[key] || "uyandırma sözcüğü bekleniyor · bitirmek için Esc";
    Engine.wake();
  },

  level(value) {
    const bar = $("#voice-level i");
    if (bar) bar.style.transform = `scaleX(${clamp(value, 0, 1)})`;
  },

  caption(message) {
    const host = $("#voice-captions");
    if (!host || !message || !String(message.text ?? "").trim()) return;
    const node = el("div", `caption ${esc(message.role)}`);
    node.textContent = message.text;
    host.appendChild(node);
    Motion.rise(node, { y: 8, duration: 320 });
    while (host.children.length > 3) host.firstElementChild.remove();
    const timer = setTimeout(() => Motion.leave(node).then(() => node.remove()), 14_000);
    this._captionTimers.push(timer);
  },
};

async function toggleVoice() {
  if (!bridgeReady()) return;
  if (State.paused && !State.voiceActive) { toast(PAUSED_NOTICE, true); return; }
  if (State.voiceActive) {
    if (State.voicePhase === "speaking") Presence.interrupted();
    VoiceStage.phase("interrupted");
    const result = await call("stop_voice");
    if (result.ok === false) toast(result.error || "Sesli oturum durdurulamadı.", true);
    if (State.demo) { State.voiceActive = false; State.voicePhase = null; VoiceStage.close(); }
    updateVoiceUI();
    return;
  }
  const result = await call("start_voice");
  if (result.ok === false) {
    toast(result.error || "Sesli oturum başlatılamadı.", true);
    Presence.error("ses başlatılamadı");
    return;
  }
  State.voiceActive = true;
  State.voicePhase = null;
  VoiceStage.open();
  updateVoiceUI();
}

function updateVoiceUI() {
  Presence.voiceActive = State.voiceActive;
  const btn = $("#voice-toggle");
  btn.classList.toggle("live", State.voiceActive);
  btn.innerHTML = `${icon(State.voiceActive ? "stop" : "voice")}<span>${State.voiceActive ? "SESLİ OTURUMU DURDUR" : "SESLİ OTURUMU BAŞLAT"}</span>`;
  $("#composer-voice").classList.toggle("live", State.voiceActive);
  $("#mini-mic").classList.toggle("live", State.voiceActive);
  $(`#rail .nav-btn[data-screen="voice"]`).classList.toggle("live", State.voiceActive);
  if (!State.voiceActive) VoiceStage.level(0);
  Presence.apply();
}

/* ── wiring ───────────────────────────────────────────────────────── */

function applyChatFind() {
  const input = $("#chat-find");
  const count = $("#chat-find-count");
  if (!input || !count) return;
  const nodes = $$("#chat-list .msg");
  const hits = chatFindFilter(nodes.map((node) => node.innerText), input.value);
  nodes.forEach((node, index) => node.classList.toggle("find-miss", hits !== null && !hits.includes(index)));
  count.hidden = hits === null;
  if (hits !== null) count.textContent = hits.length ? `${hits.length} eşleşme` : "eşleşme yok";
}

function clearChatFind() {
  const input = $("#chat-find");
  if (input && input.value) { input.value = ""; applyChatFind(); }
}

function bindChatFind() {
  const input = $("#chat-find");
  if (!input) return;
  input.addEventListener("input", () => applyChatFind());
  input.addEventListener("keydown", (event) => {
    if (event.key === "Escape") { event.stopPropagation(); clearChatFind(); input.blur(); }
  });
}

function bindConversation() {
  $("#quick-form").addEventListener("submit", (event) => {
    event.preventDefault();
    const text = $("#quick-input").value;
    if (!text.trim()) return;
    if (State.busy) { toast("JARVIS hâlâ yanıtlıyor; komutun bekliyor, yanıt bitince gönder.", true); return; }
    $("#quick-input").value = "";
    showScreen("chat", { focus: false });
    sendCommand(text);
  });
  $("#quick-send").innerHTML = icon("send");

  let historyIndex = -1; // -1 = the live draft; 0.. walks the sent history
  let historyDraft = "";
  $("#chat-form").addEventListener("submit", (event) => {
    event.preventDefault();
    const input = $("#chat-input");
    const text = input.value;
    if (!text.trim()) return;
    if (State.busy) { toast("JARVIS hâlâ yanıtlıyor; mesajın bekliyor, yanıt bitince gönder.", true); return; }
    input.value = ""; input.style.height = "auto";
    historyIndex = -1; historyDraft = "";
    sendCommand(text);
  });
  const chatInput = $("#chat-input");
  chatInput.addEventListener("keydown", (event) => {
    if (event.key === "Enter" && !event.shiftKey) { event.preventDefault(); $("#chat-form").requestSubmit(); }
    // Ctrl+ArrowUp/Down: step through the short sent history, terminal
    // style; the unsent draft waits at the bottom of the walk.
    if (event.ctrlKey && !event.altKey && !event.shiftKey && (event.key === "ArrowUp" || event.key === "ArrowDown")) {
      if (historyIndex === -1) historyDraft = chatInput.value;
      const step = historyStep(paletteRecentParse(store("nova.palette.recent")), historyIndex,
        event.key === "ArrowUp" ? "back" : "forward", historyDraft);
      if (!step) return;
      event.preventDefault();
      historyIndex = step.index;
      chatInput.value = step.text;
      chatInput.style.height = "auto";
      chatInput.style.height = Math.min(chatInput.scrollHeight, 176) + "px";
      chatInput.setSelectionRange(chatInput.value.length, chatInput.value.length);
    }
  });
  chatInput.addEventListener("input", () => {
    historyIndex = -1; // typing by hand leaves the walk
    chatInput.style.height = "auto";
    chatInput.style.height = Math.min(chatInput.scrollHeight, 176) + "px";
  });
  $("#chat-scroll").addEventListener("scroll", () => { if (chatAtBottom()) $("#chat-jump").hidden = true; }, { passive: true });
  $("#chat-jump").addEventListener("click", () => scrollChat({ force: true }));
  $("#composer-send").innerHTML = icon("send");
  $("#composer-voice").innerHTML = icon("voice");
  $("#composer-voice").addEventListener("click", () => toggleVoice());
  $("#conv-toggle").addEventListener("click", () => {
    const list = $("#conv-list");
    list.hidden = !list.hidden;
    if (!list.hidden) { refreshConversations(); Motion.rise(list, { y: 0, duration: Motion.panel }); }
  });
  $("#conv-new").addEventListener("click", newConversation);
  $("#chat-new").addEventListener("click", newConversation);
  $("#chat-rename").addEventListener("click", renameActiveConversation);
  bindConvSearch();
  const chatExport = $("#chat-export");
  if (chatExport) chatExport.addEventListener("click", async () => {
    const listing = await call("list_conversations");
    const active = listing.ok === false ? null : (listing.conversations || []).find((item) => item.active);
    if (!active || !active.turn_count) { toast("Dışa aktarılacak bir konuşma yok; önce bir şey yaz.", true); return; }
    const picked = await call("pick_folder");
    if (picked.ok === false) { toast(picked.error || "Klasör seçilemedi.", true); return; }
    if (!picked.path) return;
    const result = await call("export_conversation", active.conversation_id, picked.path);
    if (result.ok === false) { toast(result.error || "Dışa aktarılamadı.", true); return; }
    toast(`Konuşma kaydedildi: ${result.file} (${result.messages} mesaj).`, "ok");
  });

  $("#voice-toggle").addEventListener("click", toggleVoice);
  $("#voice-close").addEventListener("click", () => toggleVoice());
  $("#voice-stage").addEventListener("click", (event) => {
    if (event.target === $("#voice-stage") || event.target.closest(".voice-core-frame")) toggleVoice();
  });
  /* The core itself is the voice switch: click it, start talking. */
  $$("#stage .core-frame").forEach((frame) => frame.addEventListener("click", () => toggleVoice()));
  updateVoiceUI();
}

/* ── read a reply aloud ───────────────────────────────────────────────
   One shared <audio> element: starting a bubble stops the previous one,
   clicking the same bubble again stops it. The audio comes back from the
   bridge as base64 through the same cloud-then-local voices the phone
   uses; a refusal keeps the text and says why. */
const Readaloud = {
  audio: null,
  active: null,

  stop() {
    Speech.stop();
    if (this.active) this.active.classList.remove("speaking");
    this.active = null;
  },

  async toggle(button) {
    if (this.active === button) { this.stop(); return; }
    this.stop();
    // The click is the moment the audio context can be unlocked; the
    // clip arriving seconds later then plays without an activation.
    Speech.unlock();
    const body = button.closest(".msg")?.querySelector(".msg-body");
    const text = body ? body.textContent : "";
    if (!text.trim() || !bridgeReady()) return;
    button.classList.add("speaking");
    this.active = button;
    const result = await call("speak_text", text);
    if (this.active !== button) return;   // stopped or replaced while synthesizing
    if (result.ok === false) { this.stop(); toast(result.error || "Ses üretilemedi.", true); return; }
    const played = await Speech.play(result.audio, () => { if (this.active === button) this.stop(); });
    if (!played) this.stop();
  },
};

function bindReadaloud() {
  const host = $("#chat-list");
  if (!host) return;
  host.addEventListener("click", (event) => {
    const button = event.target.closest("[data-speak]");
    if (button) Readaloud.toggle(button);
    const copy = event.target.closest("[data-copy]");
    if (copy) {
      const body = copy.closest(".msg")?.querySelector(".msg-body");
      if (body) copyTextToClipboard(body.innerText);
    }
    const codeCopy = event.target.closest("[data-code-copy]");
    if (codeCopy) {
      const code = codeCopy.closest(".md-code")?.querySelector("code");
      if (code) copyTextToClipboard(code.innerText);
    }
  });
}

/* One speech player for the page. A click unlocks the shared
   AudioContext immediately (that part must happen inside the gesture);
   the clip that arrives seconds later then plays through it, which no
   autoplay policy blocks. Success and failure both speak. */
const Speech = {
  context: null,
  source: null,
  onended: null,

  unlock() {
    try {
      if (!this.context) this.context = new (window.AudioContext || window.webkitAudioContext)();
      if (this.context.state === "suspended") this.context.resume();
      return true;
    } catch (error) {
      return false;
    }
  },

  async play(base64, onended) {
    if (!this.context) this.unlock();
    if (!this.context) { toast("Ses çalınamadı.", true); return false; }
    this.stop();
    try {
      const raw = atob(base64);
      const bytes = new Uint8Array(raw.length);
      for (let index = 0; index < raw.length; index += 1) bytes[index] = raw.charCodeAt(index);
      const buffer = await this.context.decodeAudioData(bytes.buffer);
      const source = this.context.createBufferSource();
      source.buffer = buffer;
      source.connect(this.context.destination);
      this.source = source;
      this.onended = onended || null;
      source.onended = () => {
        if (this.source === source) {
          this.source = null;
          if (this.onended) this.onended();
        }
      };
      source.start();
      return true;
    } catch (error) {
      toast("Ses çalınamadı.", true);
      return false;
    }
  },

  stop() {
    if (this.source) {
      const source = this.source;
      this.source = null;
      this.onended = null;
      try { source.stop(); } catch (error) { /* already ended */ }
    }
  },
};

/* One clipboard hand for the page: reports what actually happened. */
async function copyTextToClipboard(text) {
  const value = String(text ?? "");
  if (!value.trim()) { toast("Kopyalanacak metin yok.", true); return false; }
  try {
    await navigator.clipboard.writeText(value);
  } catch (error) {
    toast("Panoya erişilemedi.", true);
    return false;
  }
  toast("Panoya kopyalandı.", "ok");
  return true;
}
