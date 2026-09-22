/* ════════════════════════════════════════════════════════════════════
   JARVIS · NOVA — rooms: what the academy and the research room share
   A room is a screen that takes the whole window: its own chrome, its
   own palette under a body class, an opening with sound, and two
   switches the student can flip (night, silence). This file holds the
   parts that do not care which room they serve: the audio engine, the
   remembered switches, and the veil runner that plays an opening
   timeline and lets one click end it.
   ════════════════════════════════════════════════════════════════════ */
"use strict";

/* ── sound: one context for every room, resumed on each use ──────── */

const RoomAudio = {
  context: null,
  master: null,

  /* A browser only lets audio start from a user gesture; entering a room
     is one. Without a device the picture still plays. */
  ensure() {
    try {
      if (!this.context) this.context = new (window.AudioContext || window.webkitAudioContext)();
      if (this.context.state === "suspended") this.context.resume();
      if (!this.master) {
        this.master = this.context.createGain();
        this.master.gain.value = 0.9;
        this.master.connect(this.context.destination);
      }
      return this.context;
    } catch (_error) { return null; }
  },

  tone(when, { freq, to = freq, duration, peak, type = "sine", attack = 0.012 }) {
    const context = this.ensure();
    if (!context || !this.master) return;
    const start = context.currentTime + Math.max(0, when) / 1000;
    const oscillator = context.createOscillator();
    const gain = context.createGain();
    oscillator.type = type;
    oscillator.frequency.setValueAtTime(freq, start);
    if (to !== freq) oscillator.frequency.exponentialRampToValueAtTime(to, start + duration);
    gain.gain.setValueAtTime(0.0001, start);
    gain.gain.exponentialRampToValueAtTime(peak, start + attack);
    gain.gain.exponentialRampToValueAtTime(0.0001, start + duration);
    oscillator.connect(gain).connect(this.master);
    oscillator.start(start);
    oscillator.stop(start + duration + 0.05);
  },

  /* A skipped opening goes quiet at once: the mix ramps down and is dropped. */
  hush() {
    const master = this.master;
    if (!this.context || !master) return;
    this.master = null;
    try {
      const now = this.context.currentTime;
      master.gain.cancelScheduledValues(now);
      master.gain.setValueAtTime(master.gain.value, now);
      master.gain.linearRampToValueAtTime(0.0001, now + 0.08);
      setTimeout(() => { try { master.disconnect(); } catch (_error) { /* already gone */ } }, 140);
    } catch (_error) { /* nothing was playing */ }
  },
};

/* ── the two switches a room remembers ────────────────────────────── */

function roomSwitch(key) {
  return {
    on() { return store(key) !== "off"; },
    set(on) { store(key, on ? "on" : "off"); },
  };
}

function roomNight(key, className) {
  return {
    dark() { return store(key) === "dark"; },
    set(dark) { store(key, dark ? "dark" : "light"); },
    apply() { document.body.classList.toggle(className, this.dark()); },
  };
}

/* ── the opening ──────────────────────────────────────────────────── */

/* Runs a timeline of { at, run } steps over a veil element. Each step
   receives a tracker so its animations are cancelled with the veil. The
   veil lifts on its own at `hold`; a click skips it sooner. `silence` is
   called on a skip or an abort so no scheduled sound outlives the picture. */
function runRoomOpening(veil, { steps, hold, fade, skipFade = 180, onReveal = null, silence = null }) {
  veil.hidden = false;
  veil.setAttribute("aria-hidden", "false");
  veil.style.opacity = "1";
  const timers = [];
  const animations = [];
  const track = (animation) => { if (animation) animations.push(animation); return animation; };
  steps.forEach(({ at, run }) => timers.push(setTimeout(() => run(track), at)));
  let finished = false;
  const close = () => {
    veil.hidden = true;
    veil.setAttribute("aria-hidden", "true");
    veil.style.opacity = "";
    animations.forEach((animation) => { try { animation.cancel(); } catch (_error) { /* finished */ } });
  };
  const finish = (duration) => {
    if (finished) return;
    finished = true;
    timers.forEach(clearTimeout);
    let closed = false;
    const lift = veil.animate(
      [{ opacity: 1, transform: "scale(1)" }, { opacity: 0, transform: "scale(1.02)" }],
      { duration, easing: Motion.exit, fill: "forwards" });
    const done = () => { if (closed) return; closed = true; close(); try { lift.cancel(); } catch (_error) { /* gone */ } };
    lift.onfinish = done;
    setTimeout(done, duration + 80);   // the frame lands even if the animation never reports
    if (onReveal) onReveal();
  };
  timers.push(setTimeout(() => finish(fade), hold));
  return {
    get running() { return !finished; },
    skip() { if (finished) return; if (silence) silence(); finish(skipFade); },
    abort() {
      if (finished) return;
      finished = true;
      timers.forEach(clearTimeout);
      if (silence) silence();
      close();
    },
  };
}
