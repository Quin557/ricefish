/* ricefish 面板 Service Worker
 * 目标：装到手机桌面后，打开不白屏、弱网也能看到壳。
 * 策略：静态资源 cache-first，页面 network-first（失败回离线页）。
 */
const CACHE = "ricefish-v3";
const ASSETS = [
  "/static/style.css",
  "/static/favicon.ico",
  "/static/icon-192.png",
  "/static/icon-512.png",
  "/static/icon-maskable-512.png",
  "/static/apple-touch-icon.png",
  "/static/offline.html",
  "/manifest.json",
];

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches
      .open(CACHE)
      .then((cache) => cache.addAll(ASSETS))
      .then(() => self.skipWaiting())
      .catch(() => self.skipWaiting())
  );
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches
      .keys()
      .then((keys) =>
        Promise.all(keys.filter((key) => key !== CACHE).map((key) => caches.delete(key)))
      )
      .then(() => self.clients.claim())
  );
});

self.addEventListener("fetch", (event) => {
  const request = event.request;
  if (request.method !== "GET") return;

  const url = new URL(request.url);
  if (url.origin !== self.location.origin) return;

  if (url.pathname.startsWith("/static/") || url.pathname === "/manifest.json") {
    event.respondWith(
      caches.match(request).then(
        (hit) =>
          hit ||
          fetch(request).then((response) => {
            const copy = response.clone();
            caches.open(CACHE).then((cache) => cache.put(request, copy));
            return response;
          })
      )
    );
    return;
  }

  event.respondWith(
    fetch(request).catch(() =>
      caches.match("/static/offline.html").then(
        (hit) =>
          hit ||
          new Response("离线了，连回电脑上的面板再看吧。", {
            headers: { "Content-Type": "text/plain; charset=utf-8" },
          })
      )
    )
  );
});
