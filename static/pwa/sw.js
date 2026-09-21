/* ContaStock Pro - Service Worker (PWA) */
var CACHE = 'contastock-pro-static-v1';
var PRECACHE = [
  '/static/style.css',
  '/static/icons/icon-192.png',
  '/static/icons/icon-512.png'
];

self.addEventListener('install', function (event) {
  event.waitUntil(
    caches.open(CACHE).then(function (cache) {
      return cache.addAll(PRECACHE);
    }).then(function () {
      return self.skipWaiting();
    })
  );
});

self.addEventListener('activate', function (event) {
  event.waitUntil(self.clients.claim());
});

self.addEventListener('fetch', function (event) {
  var request = event.request;

  if (request.method !== 'GET' || !request.url.startsWith(self.location.origin)) {
    return;
  }

  // Navegaciones: siempre red (contenido autenticado y en vivo)
  if (request.mode === 'navigate') {
    return;
  }

  // Recursos estáticos: caché primero, luego red
  event.respondWith(
    caches.match(request).then(function (cached) {
      if (cached) {
        return cached;
      }
      return fetch(request).then(function (response) {
        if (response && response.status === 200 && response.type === 'basic') {
          var copy = response.clone();
          caches.open(CACHE).then(function (cache) {
            cache.put(request, copy);
          });
        }
        return response;
      });
    })
  );
});