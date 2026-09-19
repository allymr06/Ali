/* JARVIS phone shim: the desktop Nova page, served to a phone.

   Nova talks to Python through window.pywebview.api and receives pushes
   through window.NOVA.push. On the phone there is no pywebview, so this
   file - loaded before every Nova script - provides the same two things
   over the paired mobile session: each api call becomes an authenticated
   POST to /api/bridge/<method> that the desktop process answers with the
   real NovaBridge, and every push the desktop window receives is mirrored
   over the server-sent-events channel. Nothing is simulated: a method
   the phone may not run answers with an honest refusal, a lost
   connection says so, and reconnecting re-reads the live snapshot.

   Voice is the one thing that cannot be proxied, because the desktop's
   voice session uses the PC's microphone and speakers. On the phone the
   loop runs here instead: the phone records, the PC's own speech
   recognizer and synthesizer listen and speak (/api/voice/*), and the
   transcript enters the desktop's own submit_command like a typed
   message marked as spoken. The page's voice UI is driven with the very
   pushes the desktop session would send. */

/* ---- pure helpers, also exercised by the tests ------------------------ */
function phoneVoiceRms(samples) {
  let sum = 0;
  for (let i = 0; i < samples.length; i += 1) sum += samples[i] * samples[i];
  return samples.length ? Math.sqrt(sum / samples.length) : 0;
}

/* Float32 chunks at inRate -> one mono 16-bit PCM WAV at outRate. A box
   filter over the source samples that map onto each output sample keeps
   the downsampling honest without a dependency. */
function phoneVoiceEncodeWav(chunks, inRate, outRate) {
  const total = chunks.reduce((count, chunk) => count + chunk.length, 0);
  const merged = new Float32Array(total);
  let offset = 0;
  for (const chunk of chunks) { merged.set(chunk, offset); offset += chunk.length; }
  const ratio = inRate / outRate;
  const outLength = Math.floor(merged.length / ratio);
  const buffer = new ArrayBuffer(44 + outLength * 2);
  const view = new DataView(buffer);
  const write = (position, text) => { for (let i = 0; i < text.length; i += 1) view.setUint8(position + i, text.charCodeAt(i)); };
  write(0, "RIFF"); view.setUint32(4, 36 + outLength * 2, true); write(8, "WAVE");
  write(12, "fmt "); view.setUint32(16, 16, true); view.setUint16(20, 1, true); view.setUint16(22, 1, true);
  view.setUint32(24, outRate, true); view.setUint32(28, outRate * 2, true); view.setUint16(32, 2, true); view.setUint16(34, 16, true);
  write(36, "data"); view.setUint32(40, outLength * 2, true);
  for (let i = 0; i < outLength; i += 1) {
    const start = Math.floor(i * ratio);
    const end = Math.max(start + 1, Math.min(merged.length, Math.floor((i + 1) * ratio)));
    let acc = 0;
    for (let j = start; j < end; j += 1) acc += merged[j];
    const sample = Math.max(-1, Math.min(1, acc / (end - start)));
    view.setInt16(44 + i * 2, sample < 0 ? sample * 0x8000 : sample * 0x7fff, true);
  }
  return buffer;
}

/* ---- the shim ------------------------------------------------------------ */
(() => {
  "use strict";

  /* Kept in step with the server's PHONE_DENIED set: window and native
     dialogs, the PC screen, credential and pairing management stay on
     the PC. start_voice/stop_voice are on the server's list too - the
     PC's microphone - and are answered here by the phone's own loop. */
  const DENIED = new Set([
    "delete_api_key", "save_settings", "test_connection",
    "mobile_pairing_code", "mobile_revoke_all", "mobile_revoke_session", "mobile_status",
    "medical_pick_file", "pick_file_root", "pick_folder", "export_conversation", "open_external",
    "grant_file_root", "revoke_file_root", "restore_snapshot",
    "set_compact", "set_visible", "run_vision",
  ]);
  const DENIED_MESSAGE = "Bu işlem telefondan yapılamaz; bilgisayardaki JARVIS'te yap.";
  const HEADERS = { "X-JARVIS-Client": "pwa", "Content-Type": "application/json" };

  let stream = null;
  let backoff = 1000;
  let reconnectTimer = 0;
  let everConnected = false;
  let banner = null;

  function note(text, kind) {
    if (!banner) {
      banner = document.createElement("div");
      banner.id = "phone-link";
      banner.setAttribute("role", "status");
      document.body.appendChild(banner);
    }
    banner.textContent = text;
    banner.className = kind || "";
    banner.hidden = !text;
  }

  function expired() {
    PhoneVoice.stop();
    if (stream) { stream.close(); stream = null; }
    window.location.replace("/?expired=1");
  }

  async function invoke(method, args) {
    if (DENIED.has(method)) return { ok: false, error: DENIED_MESSAGE };
    let response;
    try {
      response = await fetch("/api/bridge/" + encodeURIComponent(method), {
        method: "POST", credentials: "same-origin", headers: HEADERS, cache: "no-store",
        body: JSON.stringify({ args: args || [] }),
      });
    } catch (_error) {
      return { ok: false, error: "Bilgisayara ulaşılamıyor." };
    }
    if (response.status === 401) { expired(); return { ok: false, error: "Oturum sona erdi." }; }
    let payload = null;
    try { payload = await response.json(); } catch (_error) { payload = null; }
    if (!response.ok) return { ok: false, error: (payload && payload.error) || ("HTTP " + response.status) };
    return payload;
  }

  /* ---- phone voice loop ------------------------------------------------ */
  const PhoneVoice = {
    THRESHOLD: 0.012,      // RMS of float samples; ~390 on the int16 scale the desktop uses
    SILENCE_MS: 900,       // quiet after speech that ends an utterance
    MIN_SPEECH_MS: 200,    // shorter bursts are noise, not words
    MAX_MS: 30000,         // the desktop's own recording limit
    IDLE_MS: 12000,        // one listening window without any speech
    IDLE_WINDOWS: 2,       // after this many, the session ends honestly
    REPLY_TIMEOUT_MS: 180000,

    active: false, listening: false, muted: false,
    ctx: null, mediaStream: null, source: null, processor: null, playing: null,
    chunks: [], preroll: [], speech: false, speechMs: 0, silenceMs: 0, elapsedMs: 0, idleWindows: 0,
    replyResolve: null, replyTimer: 0, lastLevelAt: 0,

    push(kind, payload) { if (window.NOVA) window.NOVA.push({ kind, payload }); },
    notice(text) { if (typeof window.toast === "function") window.toast(text); },
    caption(message) { if (window.VoiceStage && typeof window.VoiceStage.caption === "function") window.VoiceStage.caption(message); },

    async start() {
      if (this.active) return { ok: false, error: "Sesli oturum zaten açık." };
      if (!window.isSecureContext || !navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
        return { ok: false, error: "Bu tarayıcı mikrofon erişimi vermiyor; HTTPS adresini kullan." };
      }
      try {
        this.mediaStream = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true } });
      } catch (_error) {
        return { ok: false, error: "Mikrofon izni verilmedi." };
      }
      const Context = window.AudioContext || window.webkitAudioContext;
      if (!Context) { this.releaseMedia(); return { ok: false, error: "Bu tarayıcı ses işleyemiyor." }; }
      this.ctx = new Context();
      if (this.ctx.state === "suspended") { try { await this.ctx.resume(); } catch (_error) { /* the first tap resumes it */ } }
      this.source = this.ctx.createMediaStreamSource(this.mediaStream);
      this.processor = this.ctx.createScriptProcessor(4096, 1, 1);
      this.processor.onaudioprocess = (event) => this.onAudio(event.inputBuffer.getChannelData(0));
      // The processor only runs while connected; a silent gain keeps the
      // microphone away from the speaker.
      const sink = this.ctx.createGain();
      sink.gain.value = 0;
      this.source.connect(this.processor);
      this.processor.connect(sink);
      sink.connect(this.ctx.destination);
      this.active = true;
      this.idleWindows = 0;
      this.push("voice_state", { active: true, error: null });
      // toggleVoice() resets the phase right after start() resolves; the
      // first listening phase is announced on the next tick so it wins.
      setTimeout(() => this.listen(), 0);
      return { ok: true };
    },

    listen() {
      if (!this.active) return;
      this.chunks = []; this.preroll = [];
      this.speech = false; this.speechMs = 0; this.silenceMs = 0; this.elapsedMs = 0;
      this.muted = false; this.listening = true;
      this.push("voice_phase", { phase: "listening" });
    },

    onAudio(input) {
      if (!this.active || !this.listening || this.muted || !this.ctx) return;
      const samples = new Float32Array(input);
      const ms = (samples.length / this.ctx.sampleRate) * 1000;
      const level = phoneVoiceRms(samples);
      const now = Date.now();
      if (now - this.lastLevelAt > 90) { this.lastLevelAt = now; this.push("voice_level", { level: Math.min(1, level * 8) }); }
      this.elapsedMs += ms;
      if (level > this.THRESHOLD) { this.speech = true; this.speechMs += ms; this.silenceMs = 0; }
      else if (this.speech) this.silenceMs += ms;
      if (this.speech) this.chunks.push(samples);
      else { this.preroll.push(samples); while (this.preroll.length > 4) this.preroll.shift(); }
      if (this.speech && this.silenceMs >= this.SILENCE_MS) {
        if (this.speechMs >= this.MIN_SPEECH_MS) { this.finish(); return; }
        this.speech = false; this.speechMs = 0; this.silenceMs = 0; this.chunks = [];   // a click, not a word
      }
      if (this.speech && this.elapsedMs >= this.MAX_MS) { this.finish(); return; }
      if (!this.speech && this.elapsedMs >= this.IDLE_MS) {
        this.idleWindows += 1;
        if (this.idleWindows >= this.IDLE_WINDOWS) { this.stop("Ses algılanmadı; sesli oturum kapandı."); return; }
        this.listen();
      }
    },

    async finish() {
      this.listening = false;
      this.idleWindows = 0;
      const wav = phoneVoiceEncodeWav(this.preroll.concat(this.chunks), this.ctx.sampleRate, 16000);
      this.push("voice_phase", { phase: "transcribing" });
      let result;
      try {
        const response = await fetch("/api/voice/transcribe", {
          method: "POST", credentials: "same-origin", cache: "no-store",
          headers: { "X-JARVIS-Client": "pwa", "Content-Type": "audio/wav" }, body: wav,
        });
        if (response.status === 401) { expired(); return; }
        result = await response.json();
      } catch (_error) { result = { ok: false, error: "Bilgisayara ulaşılamıyor." }; }
      if (!this.active) return;
      if (result.ok === false) { this.notice(result.error || "Konuşma çözümlenemedi."); this.listen(); return; }
      const text = String(result.text || "").trim();
      if (!text) { this.notice("Anlaşılmadı; tekrar söyle."); this.listen(); return; }
      this.push("voice_message", { role: "user", text, at: Date.now() });
      this.push("voice_phase", { phase: "processing" });
      const reply = this.awaitReply();
      const submitted = await invoke("submit_command", [text, true]);
      if (submitted && submitted.ok === false) { this.cancelReplyWait(); this.notice(submitted.error || "Komut gönderilemedi."); this.listen(); return; }
      const message = await reply;
      if (!this.active) return;
      if (!message) { this.notice("Yanıt gelmedi."); this.listen(); return; }
      if (message.role !== "assistant" || !message.text) { this.caption(message); this.listen(); return; }
      await this.speakReply(message.text);
      if (this.active) this.listen();
    },

    awaitReply() {
      this.cancelReplyWait();
      return new Promise((resolve) => {
        this.replyResolve = resolve;
        this.replyTimer = setTimeout(() => { this.replyResolve = null; resolve(null); }, this.REPLY_TIMEOUT_MS);
      });
    },

    cancelReplyWait() {
      clearTimeout(this.replyTimer);
      const resolve = this.replyResolve;
      this.replyResolve = null;
      if (resolve) resolve(null);
    },

    onPush(kind, payload) {
      if (kind !== "reply" || !this.replyResolve) return;
      clearTimeout(this.replyTimer);
      const resolve = this.replyResolve;
      this.replyResolve = null;
      resolve(payload || null);
    },

    async speakReply(text) {
      this.push("voice_phase", { phase: "synthesizing" });
      let response = null;
      try {
        response = await fetch("/api/voice/speak", {
          method: "POST", credentials: "same-origin", cache: "no-store", headers: HEADERS, body: JSON.stringify({ text }),
        });
      } catch (_error) { response = null; }
      if (!this.active) return;
      if (!response || !response.ok) {
        let error = "Ses üretilemedi; yanıt metin olarak duruyor.";
        if (response) { try { error = (await response.json()).error || error; } catch (_error) { /* keep the default */ } }
        this.caption({ role: "assistant", text });
        this.notice(error);
        return;
      }
      const buffer = await response.arrayBuffer();
      this.caption({ role: "assistant", text });
      this.push("voice_phase", { phase: "speaking" });
      this.muted = true;
      try {
        const decoded = await this.ctx.decodeAudioData(buffer.slice(0));
        await new Promise((resolve) => {
          const node = this.ctx.createBufferSource();
          node.buffer = decoded;
          node.connect(this.ctx.destination);
          node.onended = resolve;
          this.playing = node;
          node.start();
        });
      } catch (_error) {
        this.notice("Ses çalınamadı; yanıt metin olarak duruyor.");
      }
      this.playing = null;
      this.muted = false;
    },

    releaseMedia() {
      if (this.processor) { try { this.processor.disconnect(); } catch (_error) { /* already gone */ } this.processor.onaudioprocess = null; this.processor = null; }
      if (this.source) { try { this.source.disconnect(); } catch (_error) { /* already gone */ } this.source = null; }
      if (this.mediaStream) { this.mediaStream.getTracks().forEach((track) => track.stop()); this.mediaStream = null; }
      if (this.ctx) { try { this.ctx.close(); } catch (_error) { /* already closed */ } this.ctx = null; }
    },

    stop(noticeText) {
      const wasActive = this.active;
      this.active = false; this.listening = false; this.muted = false;
      if (this.playing) { try { this.playing.stop(); } catch (_error) { /* already ended */ } this.playing = null; }
      this.cancelReplyWait();
      this.releaseMedia();
      if (wasActive) this.push("voice_state", { active: false, error: noticeText || null });
      return wasActive ? { ok: true } : { ok: false, error: "Açık sesli oturum yok." };
    },
  };

  window.pywebview = {
    api: new Proxy({}, {
      get(_target, name) {
        if (typeof name !== "string" || name === "then") return undefined;
        if (name === "start_voice") return () => PhoneVoice.start();
        if (name === "stop_voice") return () => PhoneVoice.stop();
        return (...args) => invoke(name, args);
      },
    }),
  };

  async function resync() {
    const result = await invoke("refresh", []);
    if (result && result.snapshot && window.NOVA) window.NOVA.push({ kind: "snapshot", payload: result.snapshot });
  }

  function connect() {
    if (stream) return;
    const source = new EventSource("/api/events");
    stream = source;
    source.addEventListener("hello", () => {
      backoff = 1000;
      if (everConnected) { note("Bağlandı.", "ok"); setTimeout(() => note(""), 1800); resync(); }
      else note("");
      everConnected = true;
    });
    source.addEventListener("push", (event) => {
      let data = null;
      try { data = JSON.parse(event.data); } catch (_error) { return; }   // a malformed push is dropped, never guessed
      if (window.NOVA) window.NOVA.push(data);
      PhoneVoice.onPush(data.kind, data.payload);
    });
    source.addEventListener("session_ended", expired);
    source.onerror = () => {
      source.close();
      stream = null;
      note("Bilgisayara ulaşılamıyor; yeniden bağlanıyor…", "warn");
      clearTimeout(reconnectTimer);
      reconnectTimer = setTimeout(() => { backoff = Math.min(backoff * 2, 30000); connect(); }, backoff);
    };
  }

  document.addEventListener("DOMContentLoaded", () => {
    document.body.classList.add("phone");
    if (window.matchMedia && window.matchMedia("(pointer: coarse)").matches) document.body.classList.add("touch");
    connect();
  }, { once: true });

  document.addEventListener("visibilitychange", () => {
    if (document.hidden) {
      // Android suspends audio in the background; say so instead of pretending to listen.
      if (PhoneVoice.active) PhoneVoice.stop("Sesli oturum arka planda kapandı.");
      return;
    }
    if (!everConnected) return;
    if (!stream) { clearTimeout(reconnectTimer); backoff = 1000; connect(); }
    else resync();
  });
  window.addEventListener("online", () => { if (!stream) { clearTimeout(reconnectTimer); backoff = 1000; connect(); } });
})();
