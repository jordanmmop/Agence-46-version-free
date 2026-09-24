// ========================= STATE =========================
const API = '';
let agentsData      = null;
let symbolesSel     = ['BTC-USD', 'AAPL', 'ETH-USD'];
let launchSymbSel   = ['BTC-USD', 'AAPL', 'ETH-USD'];
let timerRefresh    = null;
let timerMt5Sync    = null;   // poller de synchro MT5 (évite l'empilement)
let groupeActif     = 'tous';

const GROUPES_INFO = {
  'tous':              { label: 'Tous (46)',           couleur: '#3b82f6' },
  'analyse_marche':    { label: 'Analyse Marché (10)', couleur: '#06b6d4' },
  'strategies':        { label: 'Stratégies (10)',     couleur: '#10b981' },
  'risques':           { label: 'Risques (8)',          couleur: '#ef4444' },
  'execution':         { label: 'Exécution (7)',        couleur: '#f97316' },
  'data_intelligence': { label: 'Data/IA (6)',          couleur: '#8b5cf6' },
  'reporting':         { label: 'Reporting (4)',        couleur: '#f59e0b' },
};

const ACTION_LABELS = {
  'BUY':   '🟢 ACHAT',
  'SELL':  '🔴 VENTE',
  'HOLD':  '⚪ HOLD',
  'WATCH': '👁️ SURVEILLER',
  'ALERT': '⚠️ ALERTE',
};

// ========================= HELPERS =========================
// Échappe les textes dynamiques (raisonnement des agents, avis LLM)
// avant insertion dans le HTML.
function esc(s) {
  return String(s ?? '')
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}

// Nom du symbole TEL QUE LE COURTIER L'AFFICHE dans le Market Watch
// MetaTrader : « EURUSD=X » → « EURUSD », « BTC-USD » → « BTCUSD »,
// « ^GSPC » → « GSPC ». La valeur envoyée au backend reste au format Yahoo
// (source des cours) ; seul l'affichage change, pour que l'utilisateur
// reconnaisse ses paires sans avoir à traduire mentalement.
// Instruments dont le ticker de cours n'évoque RIEN dans le Market Watch :
// « GC=F » ne se lit pas « GOLD.TR ». Miroir exact de NOMS_AFFICHES dans
// python/config.py.
const NOMS_AFFICHES = {
  'GC=F': 'GOLD.TR',
  'CL=F': 'CrudeOIL',
};

function labelSymbole(s) {
  const brut = String(s ?? '');
  if (NOMS_AFFICHES[brut.toUpperCase()]) return NOMS_AFFICHES[brut.toUpperCase()];
  return brut.replace(/=X$/i, '').replace(/^\^/, '').replace(/-USD$/i, 'USD');
}

// Sélection équilibrée des chips : sans cela, un simple `slice()` sur
// crypto+actions masquait TOUT le forex et tous les indices — les paires du
// courtier étaient injoignables depuis le tableau de bord. Les matières
// premières (GOLD.TR, CrudeOIL) suivent la même règle : proposées par
// /api/symboles mais absentes d'ici, elles resteraient inutilisables.
function symbolesProposes(data) {
  return [
    ...(data.crypto   || []).slice(0, 4),
    ...(data.forex    || []).slice(0, 8),
    ...(data.matieres || []).slice(0, 4),
    ...(data.actions  || []).slice(0, 6),
    ...(data.indices  || []).slice(0, 2),
  ];
}

// Le symbole voyage par `data-symbole` et le clic passe par un écouteur, JAMAIS
// par `onclick="handler('...')"`. Dans un attribut onclick, esc() est le MAUVAIS
// échappement : le navigateur décode l'entité HTML (&#39; → ') AVANT d'analyser
// le JavaScript, donc une apostrophe refermait la chaîne et le reste du symbole
// s'exécutait comme du code. Un `data-` + textContent n'a pas ce défaut.
function renderChips(conteneur, symboles, selection, handler) {
  if (!conteneur) return;
  conteneur.innerHTML = '';
  symboles.forEach(s => {
    const chip = document.createElement('span');
    chip.className = 'chip' + (selection.includes(s) ? ' selected' : '');
    chip.title = s;
    chip.dataset.symbole = s;
    chip.textContent = labelSymbole(s);
    chip.addEventListener('click', () => {
      const fn = window[handler];
      if (typeof fn === 'function') fn(chip.dataset.symbole, chip);
    });
    conteneur.appendChild(chip);
  });
}

// ========================= API =========================
let _backendDown = false;

// Réponse 401 reçue par n'importe quel module (mt5.js, auto_trader.js,
// charts.js, tools.js appellent tous celle-ci).
//
// ATTENTION — cette fonction a provoqué une BOUCLE DE RECHARGEMENT INFINIE.
// Elle envoyait vers /login ; or /login redirige vers / quand le code d'accès
// est désactivé (c'est le cas par défaut). Chaque sondage périodique d'un de
// ces modules recevait un 401, redirigeait, revenait sur /, resondait… La page
// n'avait jamais le temps de s'afficher : l'écran d'accueil restait figé sur
// « Backend hors ligne » alors que le serveur répondait parfaitement.
//
// Désormais : quand l'application dispose de l'écran de compte — c'est-à-dire
// toujours —, un 401 signifie « personne n'est connecté ». On AFFICHE l'écran
// d'inscription, sans navigation. Le repli vers /login ne sert plus qu'aux
// installations où seul l'ancien code d'accès existe.
let _redirectionFaite = false;

function _authRedirect() {
  // L'écran de compte sait quoi montrer (inscription, connexion, ou mur de
  // paiement). Aucune navigation : donc aucune boucle possible.
  if (typeof window.compteCharger === 'function') {
    window.compteCharger();
    return;
  }
  if (typeof window.compteOuvrirAuth === 'function') {
    window.compteOuvrirAuth('inscription');
    return;
  }
  // Repli historique. Le drapeau est une seconde barrière : même si /login
  // renvoyait un jour vers une page qui redemande, on ne boucle qu'une fois.
  if (_redirectionFaite || location.pathname.startsWith('/login')) return;
  _redirectionFaite = true;
  location.href = '/login?next=' + encodeURIComponent(location.pathname);
}
window._authRedirect = _authRedirect;

async function fetchJSON(url, opts = {}) {
  try {
    const r = await fetch(API + url, opts);
    // 401 = personne n'est connecté : l'application est fermée, on montre
    // l'écran d'inscription / connexion plutôt qu'une erreur.
    if (r.status === 401) {
      const body = await r.json().catch(() => ({}));
      if (body && body.compte_requis && typeof window.compteOuvrirAuth === 'function') {
        if (typeof window.compteCharger === 'function') window.compteCharger();
        else window.compteOuvrirAuth('inscription');
        return null;
      }
      _authRedirect();
      return null;
    }
    // 402 = soit le compte est suspendu (essai écoulé, abonnement échu) et
    // l'application entière est fermée, soit une fonctionnalité Pro est
    // verrouillée dans un essai qui tourne. 429 = quota d'essai atteint.
    // Un SEUL endroit traduit ces refus : l'appelant n'a rien à en savoir.
    if (r.status === 402 || r.status === 429) {
      const body = await r.json().catch(() => ({}));
      const corps = (body && (body.pro_requis || body.abonnement_requis)) ? body
                  : (body && body.detail) ? body.detail : null;
      // Compte suspendu : mur de paiement, pas un simple message « Pro ».
      if (corps && corps.abonnement_requis) {
        if (typeof window.compteCharger === 'function') window.compteCharger();
        return null;
      }
      if (corps && corps.pro_requis && typeof window.licenceVerrou === 'function') {
        window.licenceVerrou(corps);
        // Les quotas ont pu changer : remettre le compteur de l'en-tête à jour.
        if (typeof window.licenceCharger === 'function') window.licenceCharger(false);
        return null;
      }
    }
    if (!r.ok) {
      // Lire le body erreur pour un meilleur message
      const errBody = await r.json().catch(() => ({}));
      const msg = errBody.detail || errBody.error || `HTTP ${r.status}`;
      console.error(`Fetch ${url}: ${msg}`);
      if (r.status === 500) _afficherBanniereErreur(`Erreur serveur sur ${url} — ${msg}`);
      return null;
    }
    if (_backendDown) _masquerBanniereErreur();
    _backendDown = false;
    return await r.json();
  } catch (e) {
    _backendDown = true;
    console.error(`Fetch ${url}:`, e);
    _afficherBanniereErreur('Serveur inaccessible — tentative de reconnexion...');
    return null;
  }
}

// Vrai tant qu'un bandeau « état dégradé » est affiché : il doit disparaître
// dès que le serveur retrouve son état complet.
let _bandeauDegrade = false;

function _afficherBanniereErreur(msg) {
  let b = document.getElementById('backend-banner');
  if (!b) {
    b = document.createElement('div');
    b.id = 'backend-banner';
    b.style.cssText = 'position:fixed;top:0;left:0;right:0;z-index:9999;background:#ef4444;' +
      'color:#fff;text-align:center;padding:0.5rem 1rem;font-size:0.85rem;font-weight:600;' +
      'display:flex;align-items:center;justify-content:center;gap:1rem;';
    b.innerHTML = `<span id="backend-banner-msg"></span>
      <button onclick="location.reload()" style="background:rgba(255,255,255,0.2);border:1px solid rgba(255,255,255,0.4);
        color:#fff;padding:0.2rem 0.7rem;border-radius:4px;cursor:pointer;font-size:0.8rem;">
        Recharger
      </button>`;
    document.body.prepend(b);
  }
  document.getElementById('backend-banner-msg').textContent = '⚠ ' + msg;
}

function _masquerBanniereErreur() {
  const b = document.getElementById('backend-banner');
  if (b) b.remove();
}

// ========================= LAUNCH SCREEN =========================
async function initLaunchScreen() {
  const dot = document.getElementById('status-dot');
  const msg = document.getElementById('launch-status-msg');
  dot.className = 'status-dot loading';

  // Vivacité du serveur : /api/health, la seule route toujours ouverte. Sonder
  // /api/status ici était une faute de raisonnement — cette route exige un
  // compte, et son 401 se lisait « backend hors ligne » sur une application
  // servie… par ce même backend. Le message accusait le serveur d'être éteint
  // alors qu'il venait d'envoyer la page qu'on était en train de lire.
  let enLigne = false;
  try {
    enLigne = (await fetch('/api/health')).ok;
  } catch (e) { enLigne = false; }

  if (!enLigne) {
    dot.className = 'status-dot error';
    msg.textContent = 'Backend hors ligne — lancez AgenceNumerique.exe ou start.py';
    return;
  }

  // Le serveur répond : reste à savoir ce que l'utilisateur a le droit de
  // faire. /api/licence est ouverte sans compte, c'est elle qui l'indique.
  let licence = null;
  try {
    const r = await fetch('/api/licence');
    if (r.ok) licence = await r.json();
  } catch (e) { /* traité juste après */ }

  const btn = document.getElementById('btn-launch');

  if (licence && licence.compte_requis) {
    dot.className = 'status-dot loading';
    msg.textContent = `Créez un compte pour commencer — ${licence.essai?.jours || 3} jours d'essai gratuit`;
    btn.disabled = true;
    if (typeof window.compteOuvrirAuth === 'function') window.compteOuvrirAuth('inscription');
  } else if (licence && licence.compte_suspendu) {
    dot.className = 'status-dot error';
    msg.textContent = licence.libelle || 'Abonnement requis';
    btn.disabled = true;
    if (typeof window.compteOuvrirPaywall === 'function') window.compteOuvrirPaywall();
  } else {
    const status = await fetchJSON('/api/status');
    dot.className = 'status-dot ok';
    msg.textContent = `Connecté • ${status?.nb_total || 46} agents prêts`;
    btn.disabled = false;
  }

  await chargerSymbolesLancement();
}

async function chargerSymbolesLancement() {
  const data = await fetchJSON('/api/symboles');
  if (!data) return;
  renderChips(document.getElementById('launch-chips'),
              symbolesProposes(data), launchSymbSel, 'toggleLaunchSymbole');
}

function toggleLaunchSymbole(symbole, el) {
  if (launchSymbSel.includes(symbole)) {
    launchSymbSel = launchSymbSel.filter(s => s !== symbole);
    el.classList.remove('selected');
  } else if (launchSymbSel.length < 5) {
    launchSymbSel.push(symbole);
    el.classList.add('selected');
  }
}

async function demarrerApp() {
  const btn     = document.getElementById('btn-launch');
  const inner   = document.getElementById('btn-launch-inner');
  const loading = document.getElementById('btn-launch-loading');

  btn.disabled = true;
  inner.classList.add('btn-hidden');
  loading.classList.remove('btn-hidden');

  // Sync symbols from launch screen
  symbolesSel = [...launchSymbSel];

  // Transition to dashboard
  transitionerVersDashboard();

  // Load dashboard data
  // Mêmes chargements que init() — chargerObjectif() incluse : entrer par le
  // bouton principal laissait sinon l'encart « objectif du jour » vide jusqu'au
  // premier rafraîchissement automatique, soit 30 s d'écart de comportement
  // entre les deux points d'entrée du tableau de bord.
  await Promise.all([
    chargerStatus(),
    chargerAgents(),
    chargerSymboles(),
    chargerSignaux(),
    chargerHistoriquePortfolio(),
    chargerObjectif(),
  ]);

  // Auto-start analysis with selected symbols
  await lancerAnalyse();

  // Mêmes synchronisations que init() : sans cela, l'entrée par le bouton
  // principal affichait jusqu'à 30 s les valeurs simulées au lieu de l'équité
  // réelle du compte connecté (comportement différent selon le point d'entrée).
  await syncPortfolioMT5();
  if (!timerRefresh) timerRefresh = setInterval(rafraichir, 30000);
  if (!timerMt5Sync) timerMt5Sync = setInterval(syncPortfolioMT5, 5000);
  demarrerCompteur();
}

async function allerDashboardDirect() {
  transitionerVersDashboard();
  await init();
}

function transitionerVersDashboard() {
  const ls  = document.getElementById('launch-screen');
  const app = document.getElementById('app');
  ls.classList.add('fade-out');
  setTimeout(() => {
    ls.style.display = 'none';
    app.classList.remove('app-hidden');
    app.classList.add('app-visible');
  }, 600);
}

function retourAccueil() {
  if (timerRefresh) { clearInterval(timerRefresh); timerRefresh = null; }
  if (timerMt5Sync) { clearInterval(timerMt5Sync); timerMt5Sync = null; }
  const ls  = document.getElementById('launch-screen');
  const app = document.getElementById('app');
  app.classList.remove('app-visible');
  app.classList.add('app-hidden');
  ls.style.opacity = '0';
  ls.style.display = 'flex';
  ls.classList.remove('fade-out');
  requestAnimationFrame(() => {
    ls.style.transition = 'opacity 0.45s ease';
    ls.style.opacity    = '1';
  });
}

// ========================= PROGRESS BAR =========================
function showProgress(label = 'Analyse en cours...') {
  const bar  = document.getElementById('progress-bar');
  const fill = document.getElementById('progress-fill');
  const lbl  = document.getElementById('progress-label');
  if (!bar) return null;
  bar.classList.remove('prog-hidden');
  if (lbl) lbl.textContent = label;
  fill.style.width = '5%';
  let w = 5;
  return setInterval(() => {
    if (w < 85) { w += Math.random() * 7; fill.style.width = `${Math.min(w, 85)}%`; }
  }, 600);
}

function hideProgress(timer) {
  if (timer) clearInterval(timer);
  const bar  = document.getElementById('progress-bar');
  const fill = document.getElementById('progress-fill');
  if (!bar) return;
  fill.style.width = '100%';
  setTimeout(() => bar.classList.add('prog-hidden'), 450);
}

// ========================= REFRESH COUNTDOWN =========================
let _refreshSecondes = 30;
let _timerCountdown = null;

function demarrerCompteur() {
  if (_timerCountdown) clearInterval(_timerCountdown);
  _refreshSecondes = 30;
  _mettreAJourCompteur();
  _timerCountdown = setInterval(() => {
    _refreshSecondes = Math.max(0, _refreshSecondes - 1);
    _mettreAJourCompteur();
  }, 1000);
}

function _mettreAJourCompteur() {
  const el = document.getElementById('refresh-label');
  const chip = document.getElementById('refresh-chip');
  if (!el) return;
  el.textContent = `${_refreshSecondes}s`;
  if (chip) chip.classList.toggle('refreshing', _refreshSecondes === 0);
}

// ========================= SPARKLINE =========================
function renderSparkline(valeurs, largeur = 180, hauteur = 36) {
  const wrap = document.getElementById('sparkline-wrap');
  if (!wrap) return;
  if (!valeurs || valeurs.length < 2) { wrap.innerHTML = ''; return; }

  const min = Math.min(...valeurs);
  const max = Math.max(...valeurs);
  const range = max - min || 1;
  const pas = largeur / (valeurs.length - 1);

  const pts = valeurs.map((v, i) => {
    const x = (i * pas).toFixed(1);
    const y = (hauteur - ((v - min) / range) * hauteur).toFixed(1);
    return `${x},${y}`;
  }).join(' ');

  const isUp = valeurs[valeurs.length - 1] >= valeurs[0];
  const couleur = isUp ? '#10b981' : '#ef4444';
  const fill_pts = `0,${hauteur} ${pts} ${largeur},${hauteur}`;

  wrap.innerHTML = `
    <svg width="${largeur}" height="${hauteur}" viewBox="0 0 ${largeur} ${hauteur}">
      <defs>
        <linearGradient id="sg" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stop-color="${couleur}" stop-opacity="0.25"/>
          <stop offset="100%" stop-color="${couleur}" stop-opacity="0"/>
        </linearGradient>
      </defs>
      <polygon points="${fill_pts}" fill="url(#sg)"/>
      <polyline points="${pts}" fill="none" stroke="${couleur}" stroke-width="1.5" stroke-linejoin="round"/>
    </svg>`;
}

async function chargerHistoriquePortfolio() {
  const data = await fetchJSON('/api/portfolio/historique?limite=60');
  if (!data?.historique?.length) return;
  const valeurs = data.historique.map(h => h.valeur_totale).filter(v => v != null);
  renderSparkline(valeurs);
}

async function syncPortfolioMT5() {
  const data = await fetchJSON('/api/mt5/portfolio');
  if (!data?.synced) {
    // Compte non synchronisé (déconnecté) : retirer le badge « MT5 LIVE »
    // pour ne pas laisser croire que les chiffres affichés sont temps réel.
    document.querySelector('.card-portfolio h2 .mt5-sync-badge')?.remove();
    return false;
  }

  const cur  = data.currency || 'USD';
  const fmt  = v => v != null
    ? `${parseFloat(v).toLocaleString('fr', { minimumFractionDigits: 2, maximumFractionDigits: 2 })} ${cur}`
    : '—';
  const pct  = v => v !== undefined ? `${v >= 0 ? '+' : ''}${parseFloat(v).toFixed(2)}%` : '—';

  document.getElementById('val-totale').textContent    = fmt(data.valeur_totale);
  document.getElementById('capital-dispo').textContent = fmt(data.capital_disponible);
  document.getElementById('nb-positions').textContent  = data.nb_positions ?? '—';
  document.getElementById('exposition').textContent    = pct(data.exposition_pct);

  const pnlEl = document.getElementById('pnl-total');
  const pnl   = parseFloat(data.pnl_total || 0);
  pnlEl.textContent = `${pnl >= 0 ? '+' : ''}${fmt(pnl)} (${pct(data.pnl_total_pct)})`;
  pnlEl.className   = `value ${pnl >= 0 ? 'positive' : 'negative'}`;

  const ddEl = document.getElementById('drawdown');
  const dd   = parseFloat(data.drawdown_actuel || 0);
  ddEl.textContent = `${dd.toFixed(2)}%`;
  ddEl.className   = `value ${dd < -3 ? 'negative' : ''}`;

  // Badge "MT5 SYNC" sur le titre portefeuille
  const h2 = document.querySelector('.card-portfolio h2');
  if (h2 && !h2.querySelector('.mt5-sync-badge')) {
    const badge = document.createElement('span');
    badge.className = 'mt5-sync-badge';
    badge.textContent = 'MT5 LIVE';
    h2.appendChild(badge);
  }

  return true;
}

// ========================= DASHBOARD INIT =========================
async function init() {
  await Promise.all([
    chargerStatus(),
    chargerAgents(),
    chargerSymboles(),
    chargerSignaux(),
    chargerHistoriquePortfolio(),
    chargerObjectif(),
  ]);
  await syncPortfolioMT5();
  if (!timerRefresh) timerRefresh = setInterval(rafraichir, 30000);
  // Sync portefeuille MT5 toutes les 5s si connecté (une seule instance :
  // sans garde, chaque retour au tableau de bord empilait un poller de plus).
  if (!timerMt5Sync) timerMt5Sync = setInterval(syncPortfolioMT5, 5000);
  demarrerCompteur();
}

async function chargerStatus() {
  const data = await fetchJSON('/api/status');
  if (!data) return;
  document.getElementById('statut-chip').textContent  = `⚡ ${data.statut?.toUpperCase() || 'ACTIF'}`;
  document.getElementById('agents-chip').textContent  = data.nb_assistants
    ? `${data.nb_total} Agents + ${data.nb_assistants} Assistants`
    : `${data.nb_total} Agents`;
  document.getElementById('cycle-chip').textContent   = `Cycle #${data.orchestrateur?.nb_cycles || 0}`;
  // /api/status transporte l'offre en cours : badge et compteur de requêtes
  // se mettent à jour sans requête supplémentaire.
  if (data.licence && typeof window.licenceAppliquer === 'function') {
    window.licenceAppliquer(data.licence);
  }
  if (data.orchestrateur)            renderOrchestrateurCard(data.orchestrateur);
  if (data.orchestrateur?.portfolio) updatePortfolio(data.orchestrateur.portfolio);
  // Le serveur répond, mais amputé d'une partie de son état : le dire, avec
  // la cause. Sans ce message, un tableau de bord privé de son Chef
  // d'Orchestre paraissait simplement vide, sans rien expliquer.
  if (data.erreurs && data.erreurs.length) {
    _afficherBanniereErreur(data.erreurs[0]
      + ' — détails dans Réglages ⚙️ → Diagnostic');
  } else if (_bandeauDegrade) {
    _masquerBanniereErreur();
  }
  _bandeauDegrade = !!(data.erreurs && data.erreurs.length);
}

function renderOrchestrateurCard(orch) {
  document.getElementById('orchestrateur-info').innerHTML = `
    <div class="orch-id">${esc(orch.id || 'ORCH-000')}</div>
    <div class="orch-name">🎼 ${esc(orch.nom || 'Chef d\'Orchestre')}</div>
    <div class="orch-desc">${esc(orch.description || '')}</div>
    <div class="orch-stats">
      <span class="orch-stat">📊 ${orch.nb_agents || 45} agents sous coordination</span>
      <span class="orch-stat">🤝 ${(orch.nb_agents || 45) + 1} assistants IA locaux</span>
      <span class="orch-stat">🔄 ${orch.nb_cycles || 0} cycles</span>
      <span class="orch-stat">⏱️ ${orch.derniere_analyse ? new Date(orch.derniere_analyse).toLocaleTimeString('fr') : 'Jamais'}</span>
    </div>`;
}

function updatePortfolio(p) {
  // v != null && !isNaN : une valeur légitimement égale à 0 (P&L à plat,
  // capital nul au démarrage) doit s'afficher « 0€ », pas « — ».
  const fmt = v => (v != null && !isNaN(v)) ? `${parseFloat(v).toLocaleString('fr', { minimumFractionDigits: 0, maximumFractionDigits: 0 })}€` : '—';
  const pct = v => (v != null && !isNaN(v)) ? `${v >= 0 ? '+' : ''}${parseFloat(v).toFixed(2)}%` : '—';

  document.getElementById('val-totale').textContent = fmt(p.valeur_totale);
  const pnlEl = document.getElementById('pnl-total');
  const pnl   = parseFloat(p.pnl_total || 0);
  pnlEl.textContent = `${pnl >= 0 ? '+' : ''}${fmt(pnl)} (${pct(p.pnl_total_pct)})`;
  pnlEl.className   = `value ${pnl >= 0 ? 'positive' : 'negative'}`;
  document.getElementById('capital-dispo').textContent = fmt(p.capital_disponible);
  document.getElementById('nb-positions').textContent  = p.nb_positions || 0;
  document.getElementById('exposition').textContent    = `${parseFloat(p.exposition_pct || 0).toFixed(1)}%`;
  const ddEl = document.getElementById('drawdown');
  const dd   = parseFloat(p.drawdown_actuel || 0);
  ddEl.textContent = `${dd.toFixed(2)}%`;
  ddEl.className   = `value ${dd < -3 ? 'negative' : ''}`;
}

async function chargerAgents() {
  agentsData = await fetchJSON('/api/agents');
  if (!agentsData) return;
  if (agentsData.licence && typeof window.licenceAppliquer === 'function') {
    window.licenceAppliquer(agentsData.licence);
  }
  renderGroupeTabs();
  renderAgents(groupeActif);
}

function renderGroupeTabs() {
  // data-groupe + écouteur, jamais onclick="handler('...')" : même règle que
  // renderChips() ci-dessus. Le nom de groupe est aujourd'hui constant, mais
  // la règle vaut pour TOUTE valeur interpolée — c'est ce qui empêche qu'une
  // source dynamique s'y glisse plus tard sans que personne ne le remarque.
  const conteneur = document.getElementById('groupe-tabs');
  if (!conteneur) return;
  conteneur.innerHTML = Object.entries(GROUPES_INFO).map(([g, info]) =>
    `<button class="tab-btn ${g === groupeActif ? 'active' : ''}"
      data-groupe="${esc(g)}">${esc(info.label)}</button>`
  ).join('');
  conteneur.querySelectorAll('[data-groupe]').forEach(btn => {
    btn.addEventListener('click', () => selectGroupe(btn.dataset.groupe));
  });
}

function selectGroupe(groupe) {
  groupeActif = groupe;
  renderGroupeTabs();
  renderAgents(groupe);
}

function renderAgents(groupe) {
  if (!agentsData) return;
  const grid = document.getElementById('agents-grid');
  let agents = [];

  if (groupe === 'tous') {
    if (agentsData.orchestrateur) agents.push({ ...agentsData.orchestrateur, _isOrch: true });
    agents = agents.concat(agentsData.agents || []);
  } else {
    agents = agentsData.par_groupe?.[groupe] || [];
  }

  if (!agents.length) {
    grid.innerHTML = '<p class="empty-state">Aucun agent dans ce groupe</p>';
    return;
  }

  grid.innerHTML = agents.map(a => {
    const signal     = a.dernier_signal;
    const action     = signal?.action || '';
    const confiance  = signal?.confiance || 0;
    const actClass   = action === 'BUY' ? 'signal-buy' : action === 'SELL' ? 'signal-sell' : action === 'ALERT' ? 'signal-alert' : 'idle';
    const signalLabel = action ? `${ACTION_LABELS[action] || action} (${confiance.toFixed(0)}%)` : '';
    const signalClass = action === 'BUY' ? 'buy' : action === 'SELL' ? 'sell' : action === 'ALERT' ? 'alert' : 'hold';
    const ast = a.assistant;
    const astStats = ast && ast.nb_verifications > 0
      ? ` · ${ast.nb_verifications} vérif.${ast.nb_corrections ? ` · ${ast.nb_corrections} corr.` : ''}${ast.nb_avis_llm ? ` · ${ast.nb_avis_llm} avis IA` : ''}`
      : '';
    const astLine = ast
      ? `<div class="agent-assistant" title="${ast.dernier_avis ? esc(ast.dernier_avis) : 'Assistant IA local : prépare les données et vérifie les signaux'}">🤝 ${esc(ast.nom)}${astStats}</div>`
      : '';
    // Agent hors du périmètre de l'offre : TOUJOURS AFFICHÉ — c'est ce qui
    // permet de découvrir ce que la version Pro apporte —, mis en retrait et
    // marqué d'un cadenas. Le serveur seul décide de ce drapeau.
    const lock = a.verrouille
      ? '<div class="agent-lock" aria-label="Agent réservé à la version Pro">🔒 PRO</div>'
      : '';
    return `
      <div class="agent-card ${actClass} groupe-${a.groupe || 'other'}${a.verrouille ? ' verrouille' : ''}"
           ${a.verrouille ? `data-verrou-id="${esc(a.id || '')}" data-verrou-nom="${esc(a.nom || '')}" role="button" tabindex="0"` : ''}>
        ${lock}
        <div class="agent-header">
          <div class="agent-status-dot"></div>
          <span class="agent-id">${esc(a.id || '')}</span>
        </div>
        <div class="agent-nom">${a._isOrch ? '🎼 ' : ''}${esc(a.nom)}</div>
        <div class="agent-desc">${esc(a.description || '')}</div>
        ${signalLabel ? `<div class="agent-signal ${signalClass}">${esc(signalLabel)}</div>` : ''}
        ${a.nb_analyses > 0 ? `<div style="font-size:0.65rem;color:var(--text-muted);margin-top:0.3rem">${a.nb_analyses} analyses</div>` : ''}
        ${astLine}
      </div>`;
  }).join('');

  // data-* + écouteur, JAMAIS onclick="handler('...')" : même règle que
  // renderChips() et renderGroupeTabs(). Un nom d'agent interpolé dans un
  // attribut onclick suffirait à y glisser du code.
  grid.querySelectorAll('[data-verrou-id]').forEach(carte => {
    const ouvrir = () => {
      if (typeof window.licenceAgentVerrouille === 'function') {
        window.licenceAgentVerrouille(carte.dataset.verrouId, carte.dataset.verrouNom);
      }
    };
    carte.addEventListener('click', ouvrir);
    carte.addEventListener('keydown', e => {
      if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); ouvrir(); }
    });
  });

  peindreBandeauAgents();
}

// Bandeau au-dessus de la grille : périmètre de l'offre et accès à Pro.
// Peint depuis l'état que le serveur a renvoyé avec /api/agents ou
// /api/status — l'interface n'invente aucun chiffre.
function peindreBandeauAgents() {
  const el = document.getElementById('lic-bandeau-agents');
  if (!el) return;
  const lic = (agentsData && agentsData.licence)
    || (typeof window.licenceEtat === 'function' ? window.licenceEtat() : null);
  if (!lic || !lic.agents) { el.style.display = 'none'; return; }

  el.style.display = '';
  if (lic.est_pro) {
    el.innerHTML = `<span><strong>★ Version Pro</strong> — les
      ${esc(String(lic.agents.total))} agents IA sont actifs.</span>`;
    return;
  }
  el.innerHTML = `<span><strong>${esc(String(lic.agents.autorises))} agents sur
      ${esc(String(lic.agents.total))}</strong> sont actifs en version d'essai.
      Les autres restent visibles et se débloquent avec la version Pro.</span>
    <button class="lic-btn lic-btn-pro" id="lic-bandeau-btn">Passer à Pro</button>`;
  const btn = document.getElementById('lic-bandeau-btn');
  if (btn) btn.addEventListener('click', () => window.licencePasserPro && window.licencePasserPro());
}

// licence.js redessine les agents quand l'offre change (activation, expiration).
window.licenceRafraichirAgents = function () {
  if (agentsData) renderAgents(groupeActif);
};

async function chargerSymboles() {
  const data = await fetchJSON('/api/symboles');
  if (!data) return;
  renderChips(document.getElementById('symboles-chips'),
              symbolesProposes(data), symbolesSel, 'toggleSymbole');
}

function toggleSymbole(symbole, el) {
  if (symbolesSel.includes(symbole)) {
    symbolesSel = symbolesSel.filter(s => s !== symbole);
    el.classList.remove('selected');
  } else if (symbolesSel.length < 5) {
    symbolesSel.push(symbole);
    el.classList.add('selected');
  }
}

async function lancerAnalyse() {
  const btn    = document.getElementById('btn-analyser');
  const status = document.getElementById('analyse-status');
  btn.disabled    = true;
  btn.textContent = '⏳ Analyse en cours...';
  // Nombre d'agents RÉELLEMENT mobilisés dans l'offre en cours : annoncer 45
  // alors que 6 travaillent en version d'essai serait un mensonge d'interface.
  const _lic = (typeof window.licenceEtat === 'function' && window.licenceEtat()) || null;
  const _nbAgents = _lic && _lic.agents ? _lic.agents.autorises : 45;
  status.textContent = `Interrogation de ${_nbAgents} agents sur ${symbolesSel.join(', ')}...`;

  const progressTimer = showProgress('Analyse en cours...');

  const data = await fetchJSON('/api/analyser', {
    method:  'POST',
    headers: { 'Content-Type': 'application/json' },
    body:    JSON.stringify(symbolesSel),
  });

  hideProgress(progressTimer);
  btn.disabled    = false;
  btn.textContent = 'Analyser avec les 46 agents';

  if (!data) {
    status.textContent = '❌ Erreur lors de l\'analyse';
    return;
  }

  // Le serveur renvoie avec chaque rapport les quotas à jour et les symboles
  // écartés faute d'abonnement : le dire ici, à l'endroit où l'utilisateur
  // vient de cliquer, plutôt que de le laisser deviner.
  let _suffixe = '';
  if (data.licence) {
    if (typeof window.licenceCharger === 'function') window.licenceCharger(false);
    const q = data.licence.quotas || {};
    if (!q.illimite && q.resume) _suffixe += ` · ${q.resume}`;
    const ecartes = data.licence.symboles_ecartes || [];
    if (ecartes.length) {
      _suffixe += ` · ${ecartes.length} symbole(s) écarté(s) — version Pro requise`;
    }
  }
  status.textContent = `✅ Analyse terminée — Cycle #${data.cycle || '?'}${_suffixe}`;
  renderDecisions(data.decisions || {});
  if (data.portfolio) updatePortfolio(data.portfolio);
  document.getElementById('cycle-chip').textContent = `Cycle #${data.cycle || '?'}`;

  await chargerSignaux();
  await chargerAgents();
  await chargerHistoriquePortfolio();
  demarrerCompteur();
}

function renderDecisions(decisions) {
  const el = document.getElementById('decisions-list');
  if (!Object.keys(decisions).length) {
    el.innerHTML = '<p class="empty-state">Aucune décision</p>';
    return;
  }
  el.innerHTML = Object.entries(decisions).map(([sym, d]) => {
    const cls  = d.action === 'BUY' ? 'buy' : d.action === 'SELL' ? 'sell' : d.action === 'ALERT' ? 'alert' : '';
    const prix = d.prix_entree ? ` @ ${parseFloat(d.prix_entree).toFixed(4)}` : '';
    return `
      <div class="decision-item ${cls}">
        <span class="decision-symbol" title="${esc(sym)}">${esc(labelSymbole(sym))}</span>
        <span class="decision-action">${esc(ACTION_LABELS[d.action] || d.action)}</span>
        <span class="decision-conf">${d.confiance?.toFixed(0) || 0}%</span>
        <span class="decision-reason">${prix} | ${esc(d.raisonnement || '')}</span>
      </div>`;
  }).join('');
}

async function chargerSignaux() {
  const data = await fetchJSON('/api/signaux?limite=30');
  if (!data?.signaux) return;
  const feed = document.getElementById('signaux-feed');
  if (!data.signaux.length) {
    feed.innerHTML = '<p class="empty-state">Pas encore de signaux</p>';
    return;
  }
  feed.innerHTML = data.signaux.map(s => {
    const t      = new Date(s.timestamp).toLocaleTimeString('fr', { hour: '2-digit', minute: '2-digit' });
    const action = s.action || 'HOLD';
    return `
      <div class="signal-item ${action}">
        <span class="signal-time">${esc(t)}</span>
        <span class="signal-agent">${esc(s.agent_nom || '?')}</span>
        <span class="signal-sym" title="${esc(s.symbole || '')}">${esc(labelSymbole(s.symbole || '?'))}</span>
        <span class="signal-action ${action}">${action}</span>
        <span class="signal-conf">${(s.confiance || 0).toFixed(0)}%</span>
        <span class="signal-reason">${esc((s.raisonnement || '').substring(0, 60))}</span>
      </div>`;
  }).join('');
}

async function rafraichir() {
  await Promise.all([chargerStatus(), chargerSignaux(), chargerHistoriquePortfolio(),
                     chargerObjectif()]);
  await syncPortfolioMT5();
  if (window.renderPnlChart) window.renderPnlChart();
  demarrerCompteur();
}

// ========================= OBJECTIF DU JOUR =========================
// Réalisé (deals clôturés) + latent (positions ouvertes) : c'est ce que le
// compte affiche réellement, donc la seule mesure honnête d'un objectif
// exprimé en euros par jour.
async function chargerObjectif() {
  const box = document.getElementById('objectif-box');
  if (!box) return;
  const d = await fetchJSON('/api/objectif');
  if (!d) return;

  const dev   = d.devise || '';
  const total = Number(d.total) || 0;
  const obj   = Number(d.objectif) || 0;
  const pct   = Math.max(0, Math.min(100, Number(d.progression_pct) || 0));

  document.getElementById('objectif-val').textContent =
    `${total >= 0 ? '+' : ''}${total.toFixed(2)} / ${obj.toFixed(0)} ${dev}`;
  const fill = document.getElementById('objectif-fill');
  fill.style.width = pct + '%';
  fill.className = 'objectif-fill' + (d.atteint ? ' atteint' : (total < 0 ? ' negatif' : ''));

  const detail = [];
  detail.push(`réalisé ${(Number(d.realise) || 0).toFixed(2)}`);
  detail.push(`latent ${(Number(d.latent) || 0).toFixed(2)}`);
  if (d.rendement_vise_pct) {
    detail.push(`cible ${d.rendement_vise_pct}%/jour sur ${(Number(d.capital_reference) || 0).toFixed(0)} ${dev}`);
  }
  if (!d.synced) detail.push('compte non connecté');
  document.getElementById('objectif-sub').textContent = detail.join(' · ');
}

// ========================= FERMETURE GLOBALE =========================
// Bouton d'urgence de l'accueil : met le compte à plat en un clic.
async function fermerToutesPositions() {
  const btn    = document.getElementById('btn-fermer-tout');
  const statut = document.getElementById('flat-status');
  const enCours = window.AT && AT.running;

  if (!window.confirm(
      '🛑 FERMER TOUTES LES POSITIONS\n\n'
      + 'Toutes les positions ouvertes seront clôturées immédiatement au prix du '
      + 'marché. Les pertes ou gains latents deviennent définitifs.\n\nConfirmer ?')) {
    return;
  }
  // Sans arrêt de la boucle, les agents rouvrent au cycle suivant : la
  // question doit être posée, pas décidée à la place de l'utilisateur.
  const arreter = enCours && window.confirm(
    'Arrêter aussi le trading automatique ?\n\n'
    + 'OK : la boucle est coupée, aucune nouvelle position ne sera ouverte.\n'
    + 'Annuler : les agents continueront d\'analyser et pourront rouvrir.');

  if (btn) { btn.disabled = true; btn.textContent = '⏳ Fermeture en cours...'; }
  if (statut) statut.textContent = '';

  const d = await fetchJSON('/api/positions/fermer-tout', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ arreter_auto: !!arreter }),
  });

  if (btn) { btn.disabled = false; btn.textContent = '🛑 Fermer toutes les positions'; }
  if (!d) {
    if (statut) { statut.textContent = '❌ Échec de la fermeture'; statut.className = 'flat-status ko'; }
    return;
  }
  const msg = (d.message || '') + (d.auto_trader_arrete ? ' · trading automatique arrêté' : '');
  if (statut) {
    statut.textContent = (d.echecs ? '⚠️ ' : '✅ ') + msg;
    statut.className = 'flat-status ' + (d.echecs ? 'ko' : 'ok');
  }
  // Les échecs viennent du courtier : affichés tels quels, jamais interprétés.
  (d.details || []).filter(x => !x.succes).forEach(x =>
    console.warn('Fermeture échouée', x.symbole, x.erreur));
  if (typeof mt5AfficherNotif === 'function') {
    mt5AfficherNotif((d.echecs ? '⚠️ ' : '✅ ') + msg, d.echecs ? 'error' : 'success');
  }
  await syncPortfolioMT5();
  if (typeof atChargerStatut === 'function') atChargerStatut();
  await chargerObjectif();
}

// ========================= EXPORTS =========================
window.lancerAnalyse        = lancerAnalyse;
window.toggleSymbole        = toggleSymbole;
window.selectGroupe         = selectGroupe;
window.demarrerApp          = demarrerApp;
window.allerDashboardDirect = allerDashboardDirect;
window.toggleLaunchSymbole  = toggleLaunchSymbole;
window.retourAccueil        = retourAccueil;
window.labelSymbole         = labelSymbole;
window.symbolesProposes     = symbolesProposes;
window.fermerToutesPositions = fermerToutesPositions;
window.chargerObjectif      = chargerObjectif;

document.addEventListener('DOMContentLoaded', initLaunchScreen);
