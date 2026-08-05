/* Service worker du Lecteur latin.

   Deux caches, deux politiques :
   - les fichiers statiques (versionnés par ?v=) : cache d'abord —
     une URL versionnée ne change jamais de contenu ;
   - les pages et fragments HTML : réseau d'abord, avec repli sur la
     dernière version vue — on peut relire un texte déjà ouvert sans
     réseau. Les mutations (POST) ne passent jamais par ici : la file
     hors-ligne de offline.js s'en charge.
*/

const VERSION = 'v1';
const STATIC_CACHE = `statique-${VERSION}`;
const PAGE_CACHE = `pages-${VERSION}`;

self.addEventListener('install', (event) => {
  event.waitUntil(self.skipWaiting());
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(
        keys
          .filter((k) => k !== STATIC_CACHE && k !== PAGE_CACHE)
          .map((k) => caches.delete(k))
      )
    ).then(() => self.clients.claim())
  );
});

self.addEventListener('fetch', (event) => {
  const req = event.request;
  if (req.method !== 'GET') return;
  const url = new URL(req.url);
  if (url.origin !== self.location.origin) return;

  // Statique versionné : cache d'abord.
  if (url.pathname.startsWith('/static/')) {
    event.respondWith(
      caches.open(STATIC_CACHE).then(async (cache) => {
        const hit = await cache.match(req);
        if (hit) return hit;
        const res = await fetch(req);
        if (res.ok) cache.put(req, res.clone());
        return res;
      })
    );
    return;
  }

  // Médias et exports : réseau seul (volumineux, ou téléchargements).
  if (url.pathname.startsWith('/media/') || url.pathname.startsWith('/audio/')
      || url.pathname.startsWith('/export/')) {
    return;
  }

  // Pages et fragments : réseau d'abord, cache en repli.
  event.respondWith(
    caches.open(PAGE_CACHE).then(async (cache) => {
      try {
        const res = await fetch(req);
        if (res.ok && res.type === 'basic') cache.put(req, res.clone());
        return res;
      } catch (err) {
        const hit = await cache.match(req);
        if (hit) return hit;
        throw err;
      }
    })
  );
});
