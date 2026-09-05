/* ════════════════════════════════════════════════════════════════════
   JARVIS · NOVA — conversation
   The chat surface (messages, streaming, conditional auto-scroll), the
   stored-conversation list, command submission, and the voice
   experience (immersive stage, captions, microphone level).
   ════════════════════════════════════════════════════════════════════ */
"use strict";

/* ── messages ─────────────────────────────────────────────────────── */

function assuranceChips(metadata) {
  if (!metadata || typeof metadata !== "object") return "";
  const chips = [];
  if (metadata.assurance_level) chips.push(`<span class="chip">güvence · ${esc(tr(metadata.assurance_level))}</span>`);
  if (metadata.reasoning_level) chips.push(`<span class="chip violet">muhakeme · ${esc(tr(metadata.reasoning_level))}</span>`);
  if (metadata.uncertainty_summary) chips.push(`<span class="chip warn" title="${esc(metadata.uncertainty_summary)}">belirsizlik</span>`);
  return chips.length ? `<div class="assurance">${chips.join("")}</div>` : "";
}

function appendMessage(host, message, slim, { animate = true } = {}) {
  if (!host || !message || !String(message.text ?? "").trim()) return null;
  const node = el("div", `msg ${esc(message.role)}`);
  const roleLabel = message.role === "user" ? "SEN" : message.role === "assistant" ? "JARVIS" : "";
  const time = message.at ? `<span class="msg-time">${esc(fmtClock(new Date(message.at)))}</span>` : "";
  node.innerHTML =
    (roleLabel && !slim ? `<div class="msg-meta"><span class="msg-role">${roleLabel}</span>${time}</div>` : "") +
    `<div class="msg-body"></div>` +
    (message.role === "assistant" && !slim ? assuranceChips(message.metadata) : "");
  node.querySelector(".msg-body").textContent = message.text;
  host.appendChild(node);
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
      node.querySelector(".msg-body").textContent = message.text;
      node.insertAdjacentHTML("beforeend", assuranceChips(message.metadata));
      return;
    }
    node.remove();
  }
  appendMessage($("#chat-list"), message, false);
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

function renderConversations() {
  const host = $("#conv-items");
  if (!host) return;
  const items = State.conversations || [];
  if (!items.length) {
    host.innerHTML = '<div class="ctx-empty" style="padding:.8rem .4rem">Henüz kayıtlı konuşma yok.</div>';
    renderChatTitle();
    return;
  }
  host.innerHTML = items.map((item) => `
    <button type="button" class="conv-item ${item.active ? "active" : ""}" data-id="${esc(item.conversation_id)}" title="${esc(item.title)}">
      <span class="conv-title">${esc(item.title)}</span>
      <span class="conv-meta"><span>${item.turn_count} mesaj${item.status === "archived" ? " · arşiv" : ""}</span><span>${esc(fmtRelative(item.updated_at))}</span></span>
    </button>`).join("");
  $$(".conv-item", host).forEach((node) => {
    node.addEventListener("click", () => openConversation(node.dataset.id));
    node.addEventListener("contextmenu", async (event) => {
      event.preventDefault();
      const item = items.find((entry) => entry.conversation_id === node.dataset.id);
      if (!item || item.status === "archived") return;
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

  $("#chat-form").addEventListener("submit", (event) => {
    event.preventDefault();
    const input = $("#chat-input");
    const text = input.value;
    if (!text.trim()) return;
    if (State.busy) { toast("JARVIS hâlâ yanıtlıyor; mesajın bekliyor, yanıt bitince gönder.", true); return; }
    input.value = ""; input.style.height = "auto";
    sendCommand(text);
  });
  const chatInput = $("#chat-input");
  chatInput.addEventListener("keydown", (event) => {
    if (event.key === "Enter" && !event.shiftKey) { event.preventDefault(); $("#chat-form").requestSubmit(); }
  });
  chatInput.addEventListener("input", () => {
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

  $("#voice-toggle").addEventListener("click", toggleVoice);
  $("#voice-close").addEventListener("click", () => toggleVoice());
  $("#voice-stage").addEventListener("click", (event) => {
    if (event.target === $("#voice-stage") || event.target.closest(".voice-core-frame")) toggleVoice();
  });
  /* The core itself is the voice switch: click it, start talking. */
  $$("#stage .core-frame").forEach((frame) => frame.addEventListener("click", () => toggleVoice()));
  updateVoiceUI();
}
