const VERSION = 'wanplan-v3';

const PRECACHE = [
  '/wans/static/style.css',
  '/wans/static/logo.svg',
  '/wans/static/manifest.webmanifest',
  '/wans/static/icon-192.png',
  '/wans/static/icon-512.png',
  '/wans/static/apple-touch-icon.png',
  '/wans/static/icon-maskable-512.png',
  '/wans/static/offline.html',
  'https://cdn.jsdelivr.net/npm/bootstrap@5.3.2/dist/css/bootstrap.min.css',
  'https://cdn.jsdelivr.net/npm/bootstrap-icons@1.11.3/font/bootstrap-icons.min.css',
  'https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap'
];

self.addEventListener('install', (e) => {
  e.waitUntil(
    caches.open(VERSION)
      .then((cache) => cache.addAll(PRECACHE))
      .then(() => self.skipWaiting())
  );
});

self.addEventListener('activate', (e) => {
  e.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(keys.filter((k) => k !== VERSION).map((k) => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

function cacheInto(cacheName, request, response) {
  if (response && (response.ok || response.type === 'opaque')) {
    const copy = response.clone();
    caches.open(cacheName).then((cache) => cache.put(request, copy));
  }
  return response;
}

function staleWhileRevalidate(request) {
  return caches.match(request).then((hit) => {
    const network = fetch(request)
      .then((res) => cacheInto(VERSION, request, res))
      .catch(() => hit);
    return hit || network;
  });
}

self.addEventListener('fetch', (e) => {
  const req = e.request;
  if (req.method !== 'GET') return;
  const url = new URL(req.url);

  if (url.origin === location.origin) {
    if (req.mode === 'navigate') {
      e.respondWith(
        fetch(req)
          .then((res) => {
            if (res.ok) cacheInto(VERSION, '/wanplan-shell', res);
            return res;
          })
          .catch(() =>
            caches.match('/wanplan-shell')
              .then((hit) => hit || caches.match('/wans/static/offline.html'))
          )
      );
      return;
    }
    if (url.pathname.indexOf('/wans/static/') === 0) {
      e.respondWith(staleWhileRevalidate(req));
      return;
    }
    e.respondWith(fetch(req).catch(() => caches.match(req)));
    return;
  }

  if (url.hostname.indexOf('jsdelivr') !== -1 ||
      url.hostname.indexOf('fonts.googleapis') !== -1 ||
      url.hostname.indexOf('fonts.gstatic') !== -1) {
    e.respondWith(staleWhileRevalidate(req));
  }
});