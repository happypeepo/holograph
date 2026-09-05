/* Translucent service worker.

   Two caches, deliberately different strategies:
     shell  network-first  — translucent.html is served no-store and edited constantly during the
                             hackathon. Cache-first here would serve yesterday's page and
                             cost an afternoon to diagnose.
     state  stale-while-revalidate — ~120 KB per building, changes only when a human edits a
                             fixture. Serving it stale is what makes the diagram render offline.

   Anything that needs Gemini or networkx is never intercepted at all, so a stale route can
   never be presented as live. Offline the page says so; it does not invent an answer. */

const SHELL = 'translucent-shell-v1';
const STATE = 'translucent-state-v1';
const PRE = ['/', '/icon-192.png', '/icon-512.png', '/manifest.webmanifest'];

self.addEventListener('install', e => {
  // addAll is atomic — one 404 and nothing is cached, which is the honest outcome
  e.waitUntil(caches.open(SHELL).then(c => c.addAll(PRE)).then(() => self.skipWaiting()));
});

self.addEventListener('activate', e => {
  e.waitUntil(caches.keys()
    .then(k => Promise.all(k.filter(n => n !== SHELL && n !== STATE).map(n => caches.delete(n))))
    .then(() => self.clients.claim()));
});

const LIVE = /\/(chat|route|block|analytics\.js)$/;

self.addEventListener('fetch', e => {
  const r = e.request;
  if (r.method !== 'GET') return;                 // POSTs are not cacheable anyway
  const u = new URL(r.url);
  if (u.origin !== location.origin) return;
  if (LIVE.test(u.pathname)) return;              // straight to network, uncached, unwrapped

  if (u.pathname.endsWith('/state') || u.pathname === '/buildings') {
    e.respondWith(caches.open(STATE).then(async c => {
      const hit = await c.match(r);
      const net = fetch(r).then(res => { if (res.ok) c.put(r, res.clone()); return res })
                          .catch(() => hit);
      return hit || net;                          // stale immediately, fresh next launch
    }));
    return;
  }

  e.respondWith((async () => {
    try {
      const res = await fetch(r);
      if (res.ok) (await caches.open(SHELL)).put(r, res.clone());
      return res;
    } catch (err) {
      const hit = await caches.match(r);
      if (hit) return hit;
      throw err;
    }
  })());
});
