/* ════════════════════════════════════════════════════════════════════
   JARVIS · NOVA — presence
   The state machine that decides what JARVIS is doing right now, the
   central intelligence visualization (JarvisCore), the ambient
   backdrop, and the single requestAnimationFrame engine that drives
   every canvas (delta-time based, DPR-scaled, paused when hidden).
   ════════════════════════════════════════════════════════════════════ */
"use strict";

/* ── presence: one honest answer to "what is JARVIS doing?" ─────────── */

const CORE_READOUT = {
  offline: "ÇEVRİMDIŞI", idle: "HAZIR", listening: "DİNLİYOR", understanding: "ANLIYOR",
  thinking: "DÜŞÜNÜYOR", tool: "ÇALIŞIYOR", waiting: "BEKLİYOR", permission: "İZİN BEKLİYOR",
  speaking: "KONUŞUYOR", responding: "YANITLIYOR", interrupted: "KESİLDİ",
  paused: "DURAKLATILDI", error: "HATA",
};
const CORE_CAPTION = Object.assign({}, CORE_READOUT, { idle: "ÇEVRİMİÇİ" });

const Presence = {
  connected: false,
  paused: false,
  busy: false,
  streaming: false,
  voiceActive: false,
  toolLabel: null,
  approval: null,          // {tool, risk}
  errorUntil: 0, errorDetail: "",
  interruptedUntil: 0,
  settleUntil: 0,
  state: "offline",
  label: "",
  detail: "",
  _timer: 0,

  compute() {
    const now = performance.now();
    const model = State.snapshot?.model || "";
    if (!this.connected) return ["offline", "", ""];
    if (this.approval) return ["permission", toolLabel(this.approval.tool, false), "permission"];
    if (this.paused) return ["paused", "tepsi menüsünden ya da paletten devam et", ""];
    if (now < this.errorUntil) return ["error", this.errorDetail, ""];
    if (now < this.interruptedUntil) return ["interrupted", "", ""];
    if (this.voiceActive) {
      switch (State.voicePhase) {
        case "listening": return ["listening", "konuşabilirsin", ""];
        case "transcribing": return ["understanding", "söylediğin çözümleniyor", ""];
        case "processing":
          return this.toolLabel ? ["tool", this.toolLabel, ""] : ["thinking", "", ""];
        case "synthesizing": return ["thinking", "ses üretiliyor", ""];
        case "speaking": return ["speaking", "", ""];
        default: return ["listening", "uyandırma sözcüğü bekleniyor", ""];
      }
    }
    if (this.toolLabel) return ["tool", this.toolLabel, ""];
    if (this.busy) return this.streaming ? ["speaking", "yanıt yazılıyor", "responding"] : ["thinking", "", ""];
    if (now < this.settleUntil) return ["idle", "yanıt verildi", ""];
    return ["idle", model, ""];
  },

  apply() {
    const [state, detail, variant] = this.compute();
    const changed = state !== this.state;
    this.state = state;
    this.detail = detail;
    State.core = state;
    const readout = variant === "responding" ? CORE_READOUT.responding
      : variant === "permission" ? CORE_READOUT.permission : CORE_READOUT[state];
    this.label = readout;
    document.body.dataset.core = state;

    const orb = $("#presence-orb");
    if (orb) orb.dataset.state = state;
    const label = $("#presence-label");
    if (label) label.textContent = readout;
    const detailNode = $("#presence-detail");
    if (detailNode) detailNode.textContent = detail || "";

    const caption = variant === "responding" ? CORE_READOUT.responding : CORE_CAPTION[state];
    for (const [stateId, subId] of [["#core-state", "#core-sub"], ["#voice-screen-state", "#voice-screen-sub"]]) {
      const node = $(stateId);
      if (node) node.textContent = caption;
      const sub = $(subId);
      if (sub) sub.textContent = detail || (state === "idle" ? "" : "");
    }
    const miniOrb = $("#mini-orb");
    if (miniOrb) miniOrb.dataset.state = state;
    const miniLabel = $("#mini-label");
    if (miniLabel) miniLabel.textContent = readout;

    if (changed) Engine.wake();
    this._scheduleTransient();
  },

  _scheduleTransient() {
    clearTimeout(this._timer);
    const now = performance.now();
    const deadlines = [this.errorUntil, this.interruptedUntil, this.settleUntil].filter((t) => t > now);
    if (!deadlines.length) return;
    this._timer = setTimeout(() => this.apply(), Math.min(...deadlines) - now + 16);
  },

  error(detail, ms = 3200) { this.errorUntil = performance.now() + ms; this.errorDetail = detail || ""; this.apply(); },
  interrupted(ms = 1400) { this.interruptedUntil = performance.now() + ms; this.apply(); },
  spoke(ms = 1800) { if (!this.voiceActive) { this.settleUntil = performance.now() + ms; this.apply(); } },
};

/* ── the visual vocabulary of each state ──────────────────────────── */

const CORE_PROFILES = {
  offline:       { energy: 0.02, coherence: 1.00, bright: 0.30, tint: "accent", spokes: 0.00, ripple: 0.00, halo: 0.00, beam: 0 },
  idle:          { energy: 0.16, coherence: 1.00, bright: 0.88, tint: "accent", spokes: 0.06, ripple: 0.12, halo: 0.00, beam: 0 },
  listening:     { energy: 0.42, coherence: 1.00, bright: 1.00, tint: "accent", spokes: 0.15, ripple: 1.00, halo: 0.15, beam: 0 },
  understanding: { energy: 0.60, coherence: 0.94, bright: 1.00, tint: "ice",    spokes: 0.55, ripple: 0.25, halo: 0.25, beam: 0 },
  thinking:      { energy: 0.86, coherence: 0.88, bright: 1.00, tint: "ice",    spokes: 1.00, ripple: 0.00, halo: 0.30, beam: 0 },
  tool:          { energy: 0.80, coherence: 0.92, bright: 1.00, tint: "accent", spokes: 1.00, ripple: 0.00, halo: 0.25, beam: 1 },
  waiting:       { energy: 0.30, coherence: 0.96, bright: 0.90, tint: "warn",   spokes: 0.20, ripple: 0.30, halo: 0.20, beam: 0 },
  permission:    { energy: 0.30, coherence: 0.96, bright: 0.90, tint: "warn",   spokes: 0.20, ripple: 0.30, halo: 0.20, beam: 0 },
  speaking:      { energy: 0.70, coherence: 0.96, bright: 1.00, tint: "voice",  spokes: 0.30, ripple: 0.35, halo: 1.00, beam: 0 },
  interrupted:   { energy: 0.22, coherence: 0.55, bright: 0.80, tint: "bad",    spokes: 0.10, ripple: 0.00, halo: 0.00, beam: 0 },
  paused:        { energy: 0.05, coherence: 1.00, bright: 0.45, tint: "accent", spokes: 0.00, ripple: 0.00, halo: 0.00, beam: 0 },
  error:         { energy: 0.50, coherence: 0.40, bright: 0.90, tint: "bad",    spokes: 0.40, ripple: 0.00, halo: 0.00, beam: 0 },
};

const CORE_TINTS = {
  dark:  { accent: [142, 224, 255], ice: [216, 242, 255], warn: [240, 198, 138], bad: [255, 158, 158], voice: [150, 236, 214] },
  light: { accent: [11, 110, 143],  ice: [18, 136, 173],  warn: [143, 93, 22],   bad: [179, 61, 61],   voice: [29, 125, 85] },
};

/* Deterministic pseudo-noise so the core never uses Math.random per frame. */
function wave(seed, t, speed) { return 0.5 + 0.5 * Math.sin(t * speed + seed * 12.9898); }

/* The living core: index ring, segmented arcs, inclined orbits with
   satellites, radial data spokes carrying light, an interference field
   while listening, a breathing nucleus with a speech halo. Every layer
   is driven by the eased state parameters, so transitions are motion. */
class JarvisCore {
  constructor(canvas, visible, options = {}) {
    this.canvas = canvas;
    this.visible = visible;
    this.ctx = canvas.getContext("2d");
    this.size = 0;
    this.rotation = [0, 0, 0, 0];
    this.ripples = [];
    this.rippleClock = 0;
    this.offset = { x: 0, y: 0 };
    this.detail = options.detail ?? 1;         // 0.6 for small canvases
    this.params = Object.assign({}, CORE_PROFILES.offline, { tintRgb: [142, 224, 255] });
    this.spokes = Array.from({ length: 24 }, (_, i) => ({
      angle: (i / 24) * Math.PI * 2,
      phase: (i * 0.618) % 1,
      speed: 0.55 + ((i * 7) % 5) * 0.12,
    }));
    this.satellites = Array.from({ length: 7 }, (_, i) => ({
      band: i % 3, angle: (i / 7) * Math.PI * 2, speed: 0.22 + (i % 4) * 0.07, size: 1 + (i % 3) * 0.5,
    }));
  }

  resize() {
    const rect = this.canvas.getBoundingClientRect();
    if (!rect.width) { this.size = 0; return; }
    const dpr = Math.min(devicePixelRatio || 1, 3);
    this.size = rect.width;
    this.canvas.width = rect.width * dpr;
    this.canvas.height = rect.height * dpr;
    this.ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  }

  ease(dt) {
    const profile = CORE_PROFILES[State.core] || CORE_PROFILES.idle;
    const tints = CORE_TINTS[document.body.classList.contains("light") ? "light" : "dark"];
    const target = tints[profile.tint] || tints.accent;
    const k = Math.min(dt * 3.2, 1);
    const p = this.params;
    for (const key of ["energy", "coherence", "bright", "spokes", "ripple", "halo", "beam"]) {
      p[key] += (profile[key] - p[key]) * k;
    }
    p.tintRgb = p.tintRgb.map((v, i) => v + (target[i] - v) * k);
    const px = (State.pointer.x - 0.5) * 8, py = (State.pointer.y - 0.5) * 8;
    this.offset.x += (px - this.offset.x) * Math.min(dt * 2, 1);
    this.offset.y += (py - this.offset.y) * Math.min(dt * 2, 1);
  }

  draw(dt, t) {
    if (!this.size) { this.resize(); if (!this.size) return; }
    this.ease(dt);
    const { ctx } = this;
    const p = this.params;
    const size = this.size, R = size / 2;
    const c = size / 2;
    const cx = c + this.offset.x * 0.35, cy = c + this.offset.y * 0.35;
    const tint = p.tintRgb.map(Math.round).join(",");
    const bright = p.bright;
    const e = p.energy;
    const level = State.voiceLevel;
    const coherent = p.coherence;
    const listening = State.core === "listening";

    ctx.clearRect(0, 0, size, size);

    const speeds = [0.02 + e * 0.08, -(0.05 + e * 0.38), 0.09 + e * 0.6, 0.012 + e * 0.05];
    this.rotation = this.rotation.map((r, i) => r + speeds[i] * dt);

    /* ── outer index ring ── */
    ctx.save();
    ctx.translate(c, c);
    ctx.rotate(this.rotation[0]);
    ctx.lineCap = "butt";
    for (const [long, alpha, width] of [[true, 0.55, 1.3], [false, 0.26, 1]]) {
      ctx.beginPath();
      for (let i = 0; i < 96; i++) {
        if ((i % 8 === 0) !== long) continue;
        const angle = (i / 96) * Math.PI * 2;
        const r1 = R * 0.985, r2 = R * (long ? 0.94 : 0.962);
        ctx.moveTo(Math.cos(angle) * r1, Math.sin(angle) * r1);
        ctx.lineTo(Math.cos(angle) * r2, Math.sin(angle) * r2);
      }
      ctx.strokeStyle = `rgba(${tint},${alpha * bright})`;
      ctx.lineWidth = width;
      ctx.stroke();
    }
    ctx.restore();

    /* ── segmented arc ring ── */
    const segments = 6, span = 0.74;
    for (let i = 0; i < segments; i++) {
      const jitter = (1 - coherent) * 0.1 * Math.sin(t * 13 + i * 2.1);
      const start = this.rotation[1] + (i / segments) * Math.PI * 2 + jitter;
      const highlight = 0.35 + 0.65 * wave(i, t, 1.2 + e * 3.5);
      const alpha = (0.28 + 0.5 * highlight) * bright * (0.55 + e * 0.5);
      for (const [width, a] of [[5, 0.09], [1.5, 1]]) {
        ctx.beginPath();
        ctx.arc(cx, cy, R * 0.87, start, start + span);
        ctx.strokeStyle = `rgba(${tint},${alpha * a})`;
        ctx.lineWidth = width;
        ctx.lineCap = "round";
        ctx.stroke();
      }
    }

    /* ── dashed inner ring ── */
    ctx.save();
    ctx.translate(cx, cy);
    ctx.rotate(-this.rotation[2] * 0.5);
    ctx.setLineDash([2, 9]);
    ctx.beginPath();
    ctx.arc(0, 0, R * 0.75, 0, Math.PI * 2);
    ctx.strokeStyle = `rgba(${tint},${0.38 * bright})`;
    ctx.lineWidth = 1;
    ctx.stroke();
    ctx.setLineDash([]);
    ctx.restore();

    /* ── inclined orbits with satellites ── */
    for (let band = 0; band < 3; band++) {
      const radius = R * (0.62 - band * 0.08);
      const squash = 0.36 + band * 0.1;
      const tilt = band * (Math.PI / 3) + this.rotation[3] * (1 + band * 0.4);
      ctx.save();
      ctx.translate(cx, cy);
      ctx.rotate(tilt);
      ctx.beginPath();
      ctx.ellipse(0, 0, radius, radius * squash, 0, 0, Math.PI * 2);
      ctx.strokeStyle = `rgba(${tint},${0.10 * bright})`;
      ctx.lineWidth = 1;
      ctx.stroke();
      ctx.restore();
    }
    for (const s of this.satellites) {
      s.angle += s.speed * (0.35 + e * 1.8) * dt;
      const radius = R * (0.62 - s.band * 0.08);
      const squash = 0.36 + s.band * 0.1;
      const tilt = s.band * (Math.PI / 3) + this.rotation[3] * (1 + s.band * 0.4);
      const px = Math.cos(s.angle) * radius, py = Math.sin(s.angle) * radius * squash;
      const x = cx + px * Math.cos(tilt) - py * Math.sin(tilt);
      const y = cy + px * Math.sin(tilt) + py * Math.cos(tilt);
      const depth = 0.5 + 0.5 * Math.sin(s.angle);
      ctx.globalAlpha = (0.2 + depth * 0.7) * bright;
      ctx.fillStyle = `rgba(${tint},1)`;
      ctx.beginPath();
      ctx.arc(x, y, s.size * (0.6 + depth * 0.6) * this.detail, 0, Math.PI * 2);
      ctx.fill();
    }
    ctx.globalAlpha = 1;

    /* ── data spokes: light travelling outward while computing ── */
    if (p.spokes > 0.03) {
      ctx.save();
      ctx.translate(cx, cy);
      ctx.beginPath();
      for (const spoke of this.spokes) {
        ctx.moveTo(Math.cos(spoke.angle) * R * 0.30, Math.sin(spoke.angle) * R * 0.30);
        ctx.lineTo(Math.cos(spoke.angle) * R * 0.60, Math.sin(spoke.angle) * R * 0.60);
      }
      ctx.strokeStyle = `rgba(${tint},${0.08 * p.spokes * bright})`;
      ctx.lineWidth = 1;
      ctx.stroke();
      ctx.lineCap = "round";
      for (const spoke of this.spokes) {
        const toward = Math.cos(spoke.angle);                    // +1 points to the context panel
        const beam = p.beam * Math.max(0, toward) * 0.9;
        const outer = 0.60 + beam * 0.34;
        const pos = (t * spoke.speed * (0.5 + e) + spoke.phase) % 1;
        const head = 0.30 + pos * (outer - 0.30);
        const tail = Math.max(0.30, head - 0.14);
        const fade = Math.sin(pos * Math.PI);
        ctx.beginPath();
        ctx.moveTo(Math.cos(spoke.angle) * R * tail, Math.sin(spoke.angle) * R * tail);
        ctx.lineTo(Math.cos(spoke.angle) * R * head, Math.sin(spoke.angle) * R * head);
        ctx.strokeStyle = `rgba(${tint},${(0.25 + 0.65 * fade) * p.spokes * bright * (0.6 + beam * 0.6)})`;
        ctx.lineWidth = 1.2 + beam;
        ctx.stroke();
      }
      ctx.restore();
    }

    /* ── interference field: rings born from what the microphone hears ── */
    if (p.ripple > 0.03) {
      this.rippleClock -= dt;
      if (this.rippleClock <= 0) {
        const strength = listening ? 0.2 + level * 0.8 : 0.35;
        this.ripples.push({ r: R * (0.24 + level * 0.1), alpha: (0.18 + strength * 0.5) * p.ripple, width: 0.8 + level * 1.4 });
        this.rippleClock = listening ? 1.1 / (0.5 + level * 5) : 1.6;
      }
    }
    this.ripples = this.ripples.filter((ripple) => ripple.alpha > 0.008);
    for (const ripple of this.ripples) {
      ripple.r += dt * R * 0.42;
      ripple.alpha *= 1 - dt * 1.7;
      ctx.beginPath();
      ctx.arc(cx, cy, ripple.r, 0, Math.PI * 2);
      ctx.strokeStyle = `rgba(${tint},${ripple.alpha * bright})`;
      ctx.lineWidth = ripple.width;
      ctx.stroke();
    }

    /* ── nucleus: breath, speech halo, microphone response ── */
    const breath = 1 + 0.045 * Math.sin(t * (0.55 + e * 2.2));
    const envelope = p.halo * (0.35 + 0.65 * Math.abs(Math.sin(t * 7.3) * Math.sin(t * 2.9 + 1.3) * Math.sin(t * 11.1 + 0.4)));
    const flicker = coherent < 0.85 ? 1 - (1 - coherent) * 0.5 * wave(3, t, 37) : 1;
    const nucleusR = R * 0.19 * (breath + envelope * 0.16 + level * 0.14) * flicker;
    const glowR = nucleusR * 2.6;
    const glow = ctx.createRadialGradient(cx, cy, 0, cx, cy, glowR);
    glow.addColorStop(0, `rgba(${tint},${(0.5 + e * 0.4) * bright})`);
    glow.addColorStop(0.4, `rgba(${tint},${(0.16 + e * 0.22) * bright})`);
    glow.addColorStop(1, `rgba(${tint},0)`);
    ctx.fillStyle = glow;
    ctx.beginPath();
    ctx.arc(cx, cy, glowR, 0, Math.PI * 2);
    ctx.fill();
    if (p.halo > 0.03) {
      ctx.beginPath();
      ctx.arc(cx, cy, R * (0.27 + envelope * 0.1), 0, Math.PI * 2);
      ctx.strokeStyle = `rgba(${tint},${0.42 * envelope * bright})`;
      ctx.lineWidth = 1.5;
      ctx.stroke();
    }
    ctx.fillStyle = `rgba(${p.tintRgb.map((v) => Math.round(v * 0.3 + 255 * 0.7)).join(",")},${0.92 * bright})`;
    ctx.beginPath();
    ctx.arc(cx, cy, nucleusR * 0.4, 0, Math.PI * 2);
    ctx.fill();
  }
}

/* ── ambient backdrop: slow illumination and drifting dust ────────── */

class Ambient {
  constructor(canvas) {
    this.canvas = canvas;
    this.ctx = canvas.getContext("2d");
    this.dust = [];
    this.w = 0; this.h = 0;
  }
  resize() {
    const rect = this.canvas.getBoundingClientRect();
    const dpr = Math.min(devicePixelRatio || 1, 2);
    this.w = rect.width || innerWidth;
    this.h = rect.height || innerHeight;
    this.canvas.width = this.w * dpr;
    this.canvas.height = this.h * dpr;
    this.ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    const count = Math.floor((this.w * this.h) / 15000);
    this.dust = Array.from({ length: count }, () => ({
      x: Math.random() * this.w, y: Math.random() * this.h,
      z: 0.3 + Math.random() * 0.7, p: Math.random() * Math.PI * 2,
    }));
  }
  draw(dt, t) {
    const { ctx, w, h } = this;
    const dark = !document.body.classList.contains("light");
    const px = (State.pointer.x - 0.5) * 14, py = (State.pointer.y - 0.5) * 14;
    ctx.clearRect(0, 0, w, h);
    const veils = [
      [w * (0.26 + 0.05 * Math.sin(t * 0.045)) - px, h * (0.18 + 0.05 * Math.cos(t * 0.037)) - py,
       w * 0.55, dark ? "142,224,255" : "11,110,143", dark ? 0.040 : 0.05],
      [w * (0.78 + 0.04 * Math.cos(t * 0.031)) + px, h * (0.74 + 0.05 * Math.sin(t * 0.041)) + py,
       w * 0.6, dark ? "60,130,230" : "20,120,170", dark ? 0.034 : 0.04],
    ];
    for (const [x, y, r, rgb, alpha] of veils) {
      const gradient = ctx.createRadialGradient(x, y, 0, x, y, r);
      gradient.addColorStop(0, `rgba(${rgb},${alpha})`);
      gradient.addColorStop(1, "rgba(0,0,0,0)");
      ctx.fillStyle = gradient;
      ctx.fillRect(0, 0, w, h);
    }
    if (dark && State.ambient) {
      ctx.fillStyle = "#cfeefa";
      for (const mote of this.dust) {
        mote.x -= mote.z * dt * 1.6;
        if (mote.x < -2) mote.x = w + 2;
        const twinkle = 0.55 + 0.45 * Math.sin(t * (0.5 + mote.z) + mote.p);
        ctx.globalAlpha = (0.1 + 0.42 * mote.z * twinkle);
        const size = 0.8 + mote.z * 0.8;
        ctx.fillRect(mote.x - px * mote.z * 0.4, mote.y - py * mote.z * 0.4, size, size);
      }
      ctx.globalAlpha = 1;
    }
  }
}

/* ── the engine: one loop, every canvas ───────────────────────────── */

const Engine = {
  cores: [], ambient: null, voiceAmbient: null,
  staticFrame: false, _staticDrawn: false, _raf: 0, _last: 0, _lastAmbient: 0, _lastIdle: 0, _wake: 0,

  init() {
    this.ambient = new Ambient($("#ambient"));
    this.voiceAmbient = new Ambient($("#voice-ambient"));
    const shellVisible = () => State.booted && !State.compact && !VoiceStage.active;
    this.cores = [
      new JarvisCore($("#boot-core"), () => !State.booted || !$("#boot").classList.contains("gone"), { detail: 0.8 }),
      new JarvisCore($("#home-core"), () => shellVisible() && State.screen === "home"),
      new JarvisCore($("#voice-screen-core"), () => shellVisible() && State.screen === "voice"),
      new JarvisCore($("#voice-core"), () => VoiceStage.active),
      new JarvisCore($("#mini-canvas"), () => State.compact, { detail: 0.6 }),
    ];
    this.resize();
    addEventListener("resize", () => this.resize());
    document.addEventListener("visibilitychange", () => {
      if (!document.hidden) { this._last = performance.now(); this.wake(); }
    });
    addEventListener("pointermove", (event) => {
      State.pointer.x = event.clientX / Math.max(1, innerWidth);
      State.pointer.y = event.clientY / Math.max(1, innerHeight);
    }, { passive: true });
    this._raf = requestAnimationFrame((t) => this.tick(t));
  },

  resize() {
    this.ambient.resize();
    if (VoiceStage.active) this.voiceAmbient.resize();
    this.cores.forEach((core) => core.resize());
    this._staticDrawn = false;   // a resize wipes canvases: repaint even when static
  },

  /* Full frame rate for a moment after any state change or interaction. */
  wake() { this._wake = performance.now() + 1500; this._staticDrawn = false; },

  tick(now) {
    this._raf = requestAnimationFrame((t) => this.tick(t));
    if (document.hidden) return;
    const t = now / 1000;
    const calm = ["idle", "paused", "offline"].includes(State.core) && now > this._wake;
    // Calm states run the cores at 30 fps; skipped frames keep their time.
    if (calm && !this.staticFrame && now - this._lastIdle < 32) return;
    this._lastIdle = now;
    const dt = Math.min((now - (this._last || now)) / 1000, 0.08);
    this._last = now;

    if (this.staticFrame) {
      // Reduced motion: one calm frame, then nothing moves.
      if (!this._staticDrawn) {
        this.ambient.draw(0, 0);
        if (VoiceStage.active) this.voiceAmbient.draw(0, 0);
        this.cores.forEach((core) => { if (core.visible()) core.draw(0.016, 0); });
        this._staticDrawn = true;
      }
      return;
    }
    this._staticDrawn = false;
    // The backdrop is slow by design: 30 fps, 20 fps while calm.
    if (VoiceStage.active) {
      this.voiceAmbient.draw(dt, t);
    } else if (!State.compact && now - this._lastAmbient >= (calm ? 50 : 33)) {
      this.ambient.draw(Math.min((now - this._lastAmbient) / 1000, 0.2), t);
      this._lastAmbient = now;
    }
    this.cores.forEach((core) => { if (core.visible()) core.draw(dt, t); });
  },
};
