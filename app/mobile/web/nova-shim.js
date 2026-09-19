/* JARVIS phone shim: the desktop Nova page, served to a phone.

   Nova talks to Python through window.pywebview.api and receives pushes
   through window.NOVA.push. On the phone there is no pywebview, so this
   file - loaded before every Nova script - provides the same two things
   over the paired mobile session: each api call becomes an authenticated
   POST to /api/bridge/<method> that the desktop process answers with the
   real NovaBridge, and every push the desktop window receives is mirrored
   over the server-sent-events channel. Nothing is simulated: a method
   the phone may not run answers with an honest refusal, a lost
   connection says so, and reconnecting re-reads the live snapshot. */
(() => {
  "use strict";

  /* Kept in step with the server's PHONE_DENIED set: window and native
     dialogs, the PC microphone and screen, credential and pairing
     management stay on the PC. */
  const DENIED = new Set([
    "delete_api_key", "save_settings", "test_connection",
    "mobile_pairing_code", "mobile_revoke_all", "mobile_revoke_session", "mobile_status",
    "medical_pick_file", "pick_file_root", "pick_folder", "export_conversation", "open_external",
    "grant_file_root", "revoke_file_root", "restore_snapshot",
    "set_compact", "set_visible", "start_voice", "stop_voice", "run_vision",
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

  window.pywebview = {
    api: new Proxy({}, {
      get(_target, name) {
        if (typeof name !== "string" || name === "then") return undefined;
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
      try { if (window.NOVA) window.NOVA.push(JSON.parse(event.data)); } catch (_error) { /* a malformed push is dropped, never guessed */ }
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
    if (document.hidden || !everConnected) return;
    if (!stream) { clearTimeout(reconnectTimer); backoff = 1000; connect(); }
    else resync();
  });
  window.addEventListener("online", () => { if (!stream) { clearTimeout(reconnectTimer); backoff = 1000; connect(); } });
})();
