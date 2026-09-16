/* JARVIS mobile companion service worker.
   Caches the versioned UI shell only. Anything under /api/ is network-only:
   conversations, approvals and session cookies never enter a cache. An
   offline UI is not an offline assistant - the page says so itself. */
const VERSION = "__STAMP__";
const CACHE = "jarvis-mobile-" + VERSION;
const SHELL = [
  "/",
  "/index.html",
  "/offline.html",
  "/manifest.webmanifest",
  "/app.css?v=" + VERSION,
  "/app.js?v=" + VERSION,
  "/tokens.css?v=" + VERSION,
  "/icons/icon.svg",
  "/icons/icon-192.png",
  "/icons/icon-512.png",
];

self.addEventListener("install", (event) => {
  event.waitUntil(caches.open(CACHE).then((cache) => cache.addAll(SHELL)).then(() => self.skipWaiting()));
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(keys.filter((key) => key.startsWith("jarvis-mobile-") && key !== CACHE).map((key) => caches.delete(key))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener("fetch", (event) => {
  const request = event.request;
  const url = new URL(request.url);
  if (url.origin !== self.location.origin) return;
  if (url.pathname.startsWith("/api/")) {
    // Never cached, never served stale: a failure is reported as one.
    event.respondWith(fetch(request).catch(() => new Response(
      JSON.stringify({ ok: false, offline: true, error: "Bilgisayara ulaşılamıyor." }),
      { status: 503, headers: { "Content-Type": "application/json; charset=utf-8", "Cache-Control": "no-store" } }
    )));
    return;
  }
  if (request.mode === "navigate") {
    event.respondWith(
      fetch(request).then((response) => {
        const copy = response.clone();
        caches.open(CACHE).then((cache) => cache.put("/index.html", copy)).catch(() => {});
        return response;
      }).catch(() => caches.match("/index.html").then((cached) => cached || caches.match("/offline.html")))
    );
    return;
  }
  event.respondWith(
    caches.match(request).then((cached) => cached || fetch(request).then((response) => {
      if (response.ok && (url.search.includes("v=") || url.pathname.startsWith("/icons/"))) {
        const copy = response.clone();
        caches.open(CACHE).then((cache) => cache.put(request, copy)).catch(() => {});
      }
      return response;
    }))
  );
});
