/* ════════════════════════════════════════════════════════════════════
   JARVIS · NOVA — Tıp Akademisi: entering the room
   The academy opens like a place, not a tab. A heartbeat draws across
   the window, the monitor answers it, the masthead settles, and only
   then is the workspace revealed. Everything here is presentation: the
   opening shows real state (the exam countdown when a plan has one) and
   invents nothing. Its sounds are synthesized on the spot with Web
   Audio, so no file ships and nothing plays unless the student allows
   it. A click or a key skips the whole thing.
   ════════════════════════════════════════════════════════════════════ */
"use strict";

const ACADEMY_INTRO = {
  draw: 1650,          // ms for the trace to cross the window
  title: 1400,         // ms when the masthead starts to rise
  hold: 2650,          // ms when the veil begins to lift
  fade: 420,           // ms for the lift itself
  skipFade: 180,       // ms when the student skips
  peaks: [262, 512],   // x of the two R waves, in the trace's own coordinates
};

/* What the opening may say under the title: the countdown the core
   reported for the nearest exam, or nothing. Never a made-up date. */
function academyIntroLine(countdown) {
  if (!countdown || !countdown.name) return "";
  const days = Number(countdown.days_left);
  if (!Number.isFinite(days) || days < 0) return "";
  if (days === 0) return `${countdown.name} · bugün`;
  return `${countdown.name} · ${days} gün kaldı`;
}

/* The masthead greets by the clock, which is the one thing about the
   student the page knows for certain. */
function academyGreeting(date) {
  const hour = date.getHours();
  if (hour < 6) return "İyi geceler";
  if (hour < 12) return "Günaydın";
  if (hour < 18) return "İyi günler";
  return "İyi akşamlar";
}

/* Where along the trace each R wave sits, as a moment in the drawing.
   The sound is scheduled from the picture, so the two cannot drift. */
function academyPeakTimes(path, total, peaksX, drawMs) {
  const times = [];
  if (!(total > 0)) return times;
  const step = Math.max(1, total / 400);
  let cursor = 0;
  peaksX.forEach((x) => {
    while (cursor < total && path.getPointAtLength(cursor).x < x) cursor += step;
    times.push(Math.min(drawMs, (Math.min(cursor, total) / total) * drawMs));
  });
  return times;
}

/* ── sound: three small instruments, one context ──────────────────── */

const AcademySound = {
  context: null,
  master: null,

  enabled() { return store("nova.academy.sound") !== "off"; },
  setEnabled(on) { store("nova.academy.sound", on ? "on" : "off"); },

  /* One context for the session, resumed on each use: a browser only lets
     audio start from a user gesture, and entering the academy is one. */
  ensure() {
    if (!this.enabled()) return null;
    try {
      if (!this.context) this.context = new (window.AudioContext || window.webkitAudioContext)();
      if (this.context.state === "suspended") this.context.resume();
      if (!this.master) {
        this.master = this.context.createGain();
        this.master.gain.value = 0.9;
        this.master.connect(this.context.destination);
      }
      return this.context;
    } catch (_error) { return null; }   // no audio device: the picture still plays
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

  /* the monitor: one short blip per R wave */
  beep(when) { this.tone(when, { freq: 940, duration: 0.09, peak: 0.07 }); },

  /* the heart: a low "lub", then a softer "dub" */
  heartbeat(when) {
    this.tone(when, { freq: 72, to: 38, duration: 0.19, peak: 0.55, attack: 0.008 });
    this.tone(when + 135, { freq: 62, to: 34, duration: 0.17, peak: 0.4, attack: 0.008 });
  },

  /* the masthead arriving: a quiet chord that dies away on its own */
  chime(when) {
    this.tone(when, { freq: 659.25, duration: 1.3, peak: 0.055, attack: 0.03 });
    this.tone(when + 60, { freq: 987.77, duration: 1.4, peak: 0.045, attack: 0.03 });
    this.tone(when + 120, { freq: 1318.5, duration: 1.1, peak: 0.02, attack: 0.03 });
  },

  /* A skipped opening goes quiet at once instead of finishing its
     sentence: the whole mix ramps down and is dropped. */
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

/* ── theme: daylight by default, night on request ─────────────────── */

const AcademyTheme = {
  dark() { return store("nova.academy.theme") === "dark"; },
  set(dark) { store("nova.academy.theme", dark ? "dark" : "light"); },
  /* The class means something only while body.academy is on; it is put on
     at entry so the opening already wears the right dress. */
  apply() { document.body.classList.toggle("academy-dark", this.dark()); },
};

/* ── the room ─────────────────────────────────────────────────────── */

const Academy = {
  active: false,
  playing: null,   // the running opening: { animations, timers, veil }

  /* No opening without motion: the student who turned animation off gets
     the workspace at once, and silence with it. */
  shouldPlayIntro() { return Motion.allowed() && !State.compact; },

  enter() {
    document.body.classList.add("academy");
    AcademyTheme.apply();
    this.active = true;
    const greeting = $("#med-greeting");
    if (greeting) greeting.textContent = academyGreeting(new Date());
    this.syncSoundButton();
    this.syncThemeButton();
    if (this.shouldPlayIntro()) this.playIntro();
  },

  leave() {
    if (!this.active && !document.body.classList.contains("academy")) return;
    this.active = false;
    this.abortIntro();
    document.body.classList.remove("academy", "academy-dark");
  },

  playIntro() {
    this.abortIntro();
    const veil = $("#academy-intro");
    const path = $("#academy-ecg-path");
    const heart = $("#academy-heart");
    const word = veil ? veil.querySelector(".ai-word") : null;
    if (!veil || !path || !word) return;

    $("#academy-intro-line").textContent = academyIntroLine(State.examCountdown);
    veil.hidden = false;
    veil.setAttribute("aria-hidden", "false");
    veil.style.opacity = "1";
    word.style.opacity = "0";

    const animations = [];
    const timers = [];
    const total = typeof path.getTotalLength === "function" ? path.getTotalLength() : 0;
    path.style.strokeDasharray = `${total} ${total}`;
    path.style.strokeDashoffset = String(total);
    animations.push(path.animate(
      [{ strokeDashoffset: total }, { strokeDashoffset: 0 }],
      { duration: ACADEMY_INTRO.draw, easing: "linear", fill: "forwards" }));

    academyPeakTimes(path, total, ACADEMY_INTRO.peaks, ACADEMY_INTRO.draw).forEach((at) => {
      timers.push(setTimeout(() => {
        if (!heart) return;
        animations.push(heart.animate(
          [{ transform: "scale(1)" }, { transform: "scale(1.22)", offset: 0.3 }, { transform: "scale(1)" }],
          { duration: 420, easing: Motion.standard }));
      }, at));
      AcademySound.beep(at);
      AcademySound.heartbeat(at + 40);
    });

    timers.push(setTimeout(() => {
      word.style.opacity = "";
      animations.push(word.animate(
        [{ opacity: 0, transform: "translateY(14px)" }, { opacity: 1, transform: "translateY(0)" }],
        { duration: 700, easing: Motion.enter, fill: "forwards" }));
    }, ACADEMY_INTRO.title));
    AcademySound.chime(ACADEMY_INTRO.title + 120);

    timers.push(setTimeout(() => this.finishIntro(ACADEMY_INTRO.fade), ACADEMY_INTRO.hold));
    this.playing = { animations, timers, veil };
  },

  /* The veil lifts and the workspace beneath rises into place. */
  finishIntro(fade) {
    const playing = this.playing;
    if (!playing) return;
    this.playing = null;
    playing.timers.forEach(clearTimeout);
    const veil = playing.veil;
    let closed = false;
    const close = () => {
      if (closed) return;
      closed = true;
      veil.hidden = true;
      veil.setAttribute("aria-hidden", "true");
      veil.style.opacity = "";
      playing.animations.forEach((animation) => { try { animation.cancel(); } catch (_error) { /* finished */ } });
    };
    const lift = veil.animate(
      [{ opacity: 1, transform: "scale(1)" }, { opacity: 0, transform: "scale(1.02)" }],
      { duration: fade, easing: Motion.exit, fill: "forwards" });
    lift.onfinish = () => { close(); try { lift.cancel(); } catch (_error) { /* gone */ } };
    setTimeout(close, fade + 80);   // the frame lands even if the animation never reports
    this.reveal();
  },

  skipIntro() {
    if (!this.playing) return;
    AcademySound.hush();
    this.finishIntro(ACADEMY_INTRO.skipFade);
  },

  /* Leaving mid-opening: no reveal, no sound, the veil simply goes. */
  abortIntro() {
    const playing = this.playing;
    if (!playing) return;
    this.playing = null;
    playing.timers.forEach(clearTimeout);
    playing.animations.forEach((animation) => { try { animation.cancel(); } catch (_error) { /* finished */ } });
    AcademySound.hush();
    playing.veil.hidden = true;
    playing.veil.setAttribute("aria-hidden", "true");
    playing.veil.style.opacity = "";
  },

  reveal() {
    if (!Motion.allowed()) return;
    Motion.stagger($$(".med-side > *, .med-head, .med-body .med-view:not([hidden]) > *"), { step: 55, y: 14 });
  },

  toggleTheme() {
    AcademyTheme.set(!AcademyTheme.dark());
    AcademyTheme.apply();
    this.syncThemeButton();
  },

  syncThemeButton() {
    const button = $("#med-theme");
    if (!button) return;
    const dark = AcademyTheme.dark();
    button.setAttribute("aria-pressed", dark ? "true" : "false");
    button.title = dark ? "Koyu tema açık · aydınlığa dönmek için tıkla" : "Aydınlık tema açık · koyuya geçmek için tıkla";
    button.innerHTML = `${icon(dark ? "moon" : "sun")}<span>${dark ? "Koyu" : "Aydınlık"}</span>`;
  },

  toggleSound() {
    const next = !AcademySound.enabled();
    AcademySound.setEnabled(next);
    if (!next) AcademySound.hush();
    this.syncSoundButton();
    toast(next ? "Akademi sesleri açık." : "Akademi sesleri kapalı.", next ? "ok" : undefined);
  },

  syncSoundButton() {
    const button = $("#med-sound");
    if (!button) return;
    const on = AcademySound.enabled();
    button.setAttribute("aria-pressed", on ? "true" : "false");
    button.title = on ? "Açılış sesleri açık · kapatmak için tıkla" : "Açılış sesleri kapalı · açmak için tıkla";
    button.innerHTML = `${icon(on ? "sound" : "mute")}<span>${on ? "Ses açık" : "Ses kapalı"}</span>`;
  },
};

function bindAcademy() {
  const veil = $("#academy-intro");
  if (veil) veil.addEventListener("pointerdown", () => Academy.skipIntro());
  // Registered in the capture phase on the window so it runs before the
  // shell's own key handling: Escape during the opening skips the opening
  // instead of leaving the academy.
  addEventListener("keydown", (event) => {
    if (!Academy.playing) return;
    if (event.key === "Escape" || event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      event.stopImmediatePropagation();
    }
    Academy.skipIntro();
  }, true);
  const back = $("#med-back");
  if (back) back.addEventListener("click", () => showScreen("home"));
  const theme = $("#med-theme");
  if (theme) theme.addEventListener("click", () => Academy.toggleTheme());
  const sound = $("#med-sound");
  if (sound) sound.addEventListener("click", () => Academy.toggleSound());
  Academy.syncThemeButton();
  Academy.syncSoundButton();
}
