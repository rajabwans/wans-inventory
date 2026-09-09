const VERSION = 'wanplan-v7';

const PRECACHE = [
  '/wans/static/style.css',
  '/wans/static/logo.svg',
  '/wans/static/manifest.webmanifest',
  '/wans/static/icon-192.png',
  '/wans/static/icon-512.png',
  '/wans/static/apple-touch-icon.png',
  '/wans/static/icon-maskable-512.png',
  '/wans/static/vendor/bootstrap/bootstrap.min.css',
  '/wans/static/vendor/bootstrap/bootstrap.bundle.min.js',
  '/wans/static/vendor/bootstrap-icons/bootstrap-icons.min.css',
  '/wans/static/vendor/bootstrap-icons/fonts/bootstrap-icons.woff2',
  '/wans/static/vendor/bootstrap-icons/fonts/bootstrap-icons.woff',
  '/wans/static/offline.js'
];

self.addEventListener('install', (e) => {
  self.skipWaiting();
  e.waitUntil(
    caches.open(VERSION).then((cache) => cache.addAll(PRECACHE))
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
  return caches.match(request, { ignoreSearch: true }).then((hit) => {
    const network = fetch(request)
      .then((res) => cacheInto(VERSION, request, res))
      .catch(() => hit);
    return hit || network;
  });
}

function networkFirstForNavigation(request) {
  return fetch(request)
    .then((res) => {
      if (res.ok) cacheInto(VERSION, request, res);
      return res;
    })
    .catch(() =>
      caches.match(request, { ignoreSearch: true }).then((hit) =>
        hit || caches.match('/wanplan-shell')
      )
    );
}

self.addEventListener('fetch', (e) => {
  const req = e.request;
  if (req.method !== 'GET') return;
  const url = new URL(req.url);

  if (url.origin === location.origin) {
    if (req.mode === 'navigate') {
      e.respondWith(networkFirstForNavigation(req));
      return;
    }
    if (url.pathname.indexOf('/wans/static/') === 0 ||
        url.pathname === '/wans/offline.js') {
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