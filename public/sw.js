self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open("trade-review-v6").then((cache) =>
      cache.addAll(["/", "/index.html", "/styles.css", "/app.js", "/manifest.json"])
    )
  );
});

self.addEventListener("fetch", (event) => {
  if (event.request.method !== "GET") return;
  event.respondWith(
    fetch(event.request).catch(() => caches.match(event.request).then((res) => res || caches.match("/")))
  );
});
