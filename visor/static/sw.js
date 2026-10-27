// Guarda la "cáscara" de la app (estilos, scripts, iconos) para que abra
// rápido. Los datos (/api) siempre se piden a la red: nunca se muestran
// números viejos como si fueran actuales.
var VERSION = 'visor-v1';
var CASCARA = [
    '/static/style.css', '/static/app.js', '/static/vendor/chart.umd.min.js',
    '/static/icons/icon-192.png', '/static/icons/icon-512.png'
];

self.addEventListener('install', function (e) {
    e.waitUntil(caches.open(VERSION).then(function (c) { return c.addAll(CASCARA); }));
    self.skipWaiting();
});

self.addEventListener('activate', function (e) {
    e.waitUntil(caches.keys().then(function (claves) {
        return Promise.all(claves.filter(function (k) { return k !== VERSION; })
            .map(function (k) { return caches.delete(k); }));
    }));
    self.clients.claim();
});

self.addEventListener('fetch', function (e) {
    var url = new URL(e.request.url);
    if (e.request.method !== 'GET' || !url.pathname.startsWith('/static/')) return;
    // primero la red (para recibir cambios), la copia guardada si no hay internet
    e.respondWith(fetch(e.request).then(function (r) {
        var copia = r.clone();
        caches.open(VERSION).then(function (c) { c.put(e.request, copia); });
        return r;
    }).catch(function () { return caches.match(e.request); }));
});
