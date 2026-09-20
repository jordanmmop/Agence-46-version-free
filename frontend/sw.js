const CACHE = 'agence-ia-v11';
// '/' n'est PAS pré-caché : quand l'authentification est active, il répond par
// une redirection vers /login. Le pré-cache enregistrait donc la PAGE DE
// CONNEXION sous la clé '/', et un utilisateur déjà authentifié se retrouvait
// devant l'écran de connexion au moindre passage par le cache.
const STATIC = [
  '/css/style.css',
  '/js/app.js',
  '/js/mt5.js',
  '/js/auto_trader.js',
  '/js/charts.js',
  '/js/tools.js',
  '/icons/icon-192.png',
];

self.addEventListener('install', e => {
  e.waitUntil(
    caches.open(CACHE).then(c => c.addAll(STATIC)).then(() => self.skipWaiting())
  );
});

self.addEventListener('activate', e => {
  e.waitUntil(
    caches.keys().then(keys =>
      Promise.all(keys.filter(k => k !== CACHE).map(k => caches.delete(k)))
    ).then(() => self.clients.claim())
  );
});

self.addEventListener('fetch', e => {
  const url = new URL(e.request.url);
  // API → réseau uniquement (pas de cache). Hors ligne : statut 503 (et non
  // 200) pour que le client détecte la panne et affiche « Serveur inaccessible »
  // au lieu de traiter {error:'Hors ligne'} comme une réponse valide.
  if (url.pathname.startsWith('/api/')) {
    e.respondWith(fetch(e.request).catch(() =>
      new Response(JSON.stringify({ error: 'Hors ligne' }), {
        status: 503,
        headers: { 'Content-Type': 'application/json' }
      })
    ));
    return;
  }
  // Statique → RÉSEAU d'abord (les mises à jour arrivent immédiatement),
  // cache en secours pour le mode hors ligne.
  e.respondWith(
    fetch(e.request).then(res => {
      // `res.redirected` : la réponse vient d'une AUTRE URL que celle demandée
      // (typiquement la redirection vers /login quand la session a expiré).
      // La mettre en cache sous la clé d'origine servirait ensuite la page de
      // connexion à la place du tableau de bord, même une fois reconnecté.
      if (res.ok && !res.redirected && e.request.method === 'GET') {
        const clone = res.clone();
        caches.open(CACHE).then(c => c.put(e.request, clone));
      }
      return res;
    }).catch(() => caches.match(e.request))
  );
});
