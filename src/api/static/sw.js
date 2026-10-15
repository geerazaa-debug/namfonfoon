// Offline shell for the public map. Report data: network first, last copy when offline.
// OSM tiles are deliberately not cached (tile usage policy forbids bulk caching); OSRM routes neither.
const VERSION = "fw-v5";
const SHELL = ["/map", "/manifest.webmanifest", "/icons/icon-192.png", "/icons/icon-512.png"];
const CDN = ["https://cdnjs.cloudflare.com/", "https://fonts.googleapis.com/", "https://fonts.gstatic.com/"];

self.addEventListener("install", (e) => {
  e.waitUntil(caches.open(VERSION).then((c) => c.addAll(SHELL)).then(() => self.skipWaiting()));
});

self.addEventListener("activate", (e) => {
  e.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(keys.filter((k) => k !== VERSION).map((k) => caches.delete(k))))
      .then(() => self.clients.claim()),
  );
});

async function networkFirst(req, markOffline) {
  const cache = await caches.open(VERSION);
  try {
    const res = await fetch(req);
    if (res.ok) cache.put(req, res.clone());
    return res;
  } catch (err) {
    const hit = await cache.match(req);
    if (!hit) throw err;
    if (!markOffline) return hit;
    const headers = new Headers(hit.headers);
    headers.set("X-FW-Offline", "1");
    return new Response(await hit.blob(), { status: 200, headers });
  }
}

async function cacheFirst(req) {
  const cache = await caches.open(VERSION);
  const hit = await cache.match(req);
  if (hit) return hit;
  const res = await fetch(req);
  if (res.ok || res.type === "opaque") cache.put(req, res.clone());
  return res;
}

self.addEventListener("fetch", (e) => {
  const req = e.request;
  if (req.method !== "GET") return;
  const url = new URL(req.url);
  if (url.origin === location.origin) {
    if (url.pathname === "/v1/public/reports") return e.respondWith(networkFirst(req, true));
    if (["/v1/public/rain", "/v1/public/air", "/v1/public/road-flood", "/v1/public/river"].includes(url.pathname)) {
      return e.respondWith(networkFirst(req, false));
    }
    if (SHELL.includes(url.pathname)) return e.respondWith(networkFirst(req, false));
    return;
  }
  if (CDN.some((p) => req.url.startsWith(p))) e.respondWith(cacheFirst(req));
});
