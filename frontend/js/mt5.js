// ========================= MT5 / AVATRADE MODULE =========================
// Gère la connexion MT5, le trading automatique et le journal des opérations.

const MT5 = {
  connected:    false,
  auto_trading: false,
  account_info: null,
  journal:      [],
  _pollTimer:   null,
  // Noms de référence : seuls ceux-là peuvent être remplacés automatiquement
  // quand on bascule démo/réel (un nom saisi à la main ne l'est jamais).
  _serveursReference: ['Ava-Demo 1-MT5', 'Ava-Real 1-MT5'],
};

// Remplit les suggestions du champ « Serveur » avec les serveurs que le
// terminal MetaTrader connaît RÉELLEMENT (lus sur le disque), puis la liste
// AvaTrade de référence. Le champ reste en saisie libre : ces suggestions
// aident, elles ne limitent pas.
async function mt5ChargerServeurs() {
  const liste = document.getElementById('mt5-server-options');
  const champ = document.getElementById('mt5-server');
  if (!liste || !champ) return;
  const d = await mt5Fetch('/api/mt5/servers');
  const serveurs = (d && d.servers) || [];
  const detectes = (d && d.detectes) || [];
  if (serveurs.length) {
    liste.innerHTML = '';
    serveurs.forEach(nom => {
      const o = document.createElement('option');
      o.value = nom;                     // value : jamais interprété comme HTML
      liste.appendChild(o);
    });
  }

  // Pré-remplir avec un serveur RÉELLEMENT présent sur le terminal. Le défaut
  // « Ava-Demo 1-MT5 » était écrit en dur : un terminal qui ne connaît que
  // « MetaQuotes-Demo » recevait donc une demande de connexion vers un serveur
  // inexistant, et l'échec ne désignait pas la cause. On ne remplace jamais un
  // nom saisi à la main — seulement une valeur de référence non retouchée.
  const actuel = (champ.value || '').trim();
  if (detectes.length && (!actuel || MT5._serveursReference.includes(actuel))) {
    champ.value = detectes[0];
  }

  const hint = document.getElementById('mt5-server-hint');
  if (!hint) return;
  hint.textContent = '';
  if (detectes.length) {
    hint.appendChild(document.createTextNode('Détectés sur votre terminal — cliquez pour choisir : '));
    detectes.slice(0, 8).forEach((nom, i) => {
      if (i) hint.appendChild(document.createTextNode(' '));
      // dataset + écouteur, jamais onclick="..." : un nom de serveur contenant
      // une apostrophe casserait l'attribut (cf. renderChips dans app.js).
      const b = document.createElement('button');
      b.type = 'button';
      b.className = 'mt5-server-chip';
      b.textContent = nom;
      b.dataset.serveur = nom;
      b.addEventListener('click', () => { champ.value = b.dataset.serveur; });
      hint.appendChild(b);
    });
    hint.appendChild(document.createTextNode(
      ' Vous pouvez aussi saisir n\'importe quel autre nom de serveur.'));
  } else {
    hint.textContent = 'Nom EXACT affiché dans MetaTrader 5 → Fichier → '
      + 'Ouvrir un compte (ou dans la fenêtre « Se connecter »).';
  }
}

// ── Initialisation ────────────────────────────────────────────────────────
async function initMT5() {
  await mt5ChargerStatut();
  MT5._pollTimer = setInterval(mt5ChargerStatut, 15_000);
}

// ── API helpers ───────────────────────────────────────────────────────────
async function mt5Fetch(url, opts = {}) {
  try {
    const r = await fetch(url, opts);
    if (r.status === 401) { if (window._authRedirect) window._authRedirect(); return { success: false, error: 'Session expirée' }; }
    if (!r.ok) {
      const err = await r.json().catch(() => ({ error: `HTTP ${r.status}` }));
      return { success: false, error: err.error || err.detail || `HTTP ${r.status}` };
    }
    return await r.json();
  } catch (e) {
    return { success: false, error: e.message };
  }
}

// ── Statut ────────────────────────────────────────────────────────────────
async function mt5ChargerStatut() {
  const data = await mt5Fetch('/api/mt5/status');
  if (!data) return;
  MT5.connected    = data.connected || false;
  MT5.auto_trading = data.auto_trading || false;
  MT5.account_info = data.account_info || null;
  mt5RenderOperation(data.operation, data.occupe);
  mt5RenderPanel();
  await mt5ChargerJournal();
}

// ── Connexion ─────────────────────────────────────────────────────────────
async function mt5Connecter() {
  const account     = document.getElementById('mt5-account').value.trim();
  const password    = document.getElementById('mt5-password').value;
  const accountType = document.querySelector('input[name="mt5-type"]:checked')?.value || 'demo';
  const server      = document.getElementById('mt5-server')?.value || '';
  const btnConnect  = document.getElementById('mt5-btn-connect');
  const errEl       = document.getElementById('mt5-modal-error');

  errEl.textContent = '';

  if (!account || account.length < 5) {
    errEl.textContent = 'Numéro de compte invalide (min 5 chiffres).';
    return;
  }
  if (!password || password.length < 4) {
    errEl.textContent = 'Mot de passe invalide (min 4 caractères).';
    return;
  }

  btnConnect.disabled = true;
  btnConnect.textContent = 'Connexion en cours…';

  // Lancer MT5 d'abord (ignoré si non installé) — POST : action mutative
  await mt5Fetch('/api/mt5/launch', { method: 'POST' });

  // Compte à rebours visible : une connexion peut légitimement demander une
  // minute (le terminal démarre). Sans repère, « Connexion en cours… » fixe
  // ne se distingue pas d'une application plantée — c'est exactement ce que
  // l'utilisateur constatait.
  let ecoule = 0;
  const tic = setInterval(() => {
    ecoule += 1;
    btnConnect.textContent = `Connexion… ${ecoule}s`;
    if (ecoule === 15) {
      errEl.style.color = 'var(--text-muted)';
      errEl.textContent = 'MetaTrader peut mettre jusqu\'à une minute à répondre '
        + '(démarrage du terminal, connexion au courtier). Si une fenêtre est '
        + 'ouverte dans MetaTrader 5, fermez-la : le terminal ne répond à aucun '
        + 'programme tant qu\'une boîte de dialogue est affichée.';
    }
  }, 1000);

  let data;
  try {
    data = await mt5Fetch('/api/mt5/connect', {
      method:  'POST',
      headers: { 'Content-Type': 'application/json' },
      body:    JSON.stringify({ account, password, account_type: accountType, server }),
    });
  } finally {
    clearInterval(tic);
  }

  btnConnect.disabled = false;
  btnConnect.textContent = 'Se connecter';
  errEl.style.color = '';

  if (data.success) {
    MT5.connected    = true;
    MT5.account_info = data.account_info;
    mt5FermerModal();
    mt5RenderPanel();
    mt5AfficherNotif('✅ Connecté à MT5 — ' + (data.account_info?.server || ''), 'success');
    if (data.avertissement) mt5AfficherNotif('⚠️ ' + data.avertissement, 'error');
  } else {
    // `detail` porte l'erreur BRUTE de MetaTrader (code + étape). Sans elle,
    // toutes les causes — mauvais serveur, mot de passe, terminal absent —
    // se ressemblaient et rien ne guidait la correction.
    // textContent + `white-space: pre-line` en CSS : la marche à suivre est
    // numérotée sur plusieurs lignes, elle doit rester lisible telle quelle.
    errEl.textContent = (data.error || 'Erreur de connexion.')
      + (data.info ? '\n' + data.info : '')
      + (data.detail ? '\n\nDétail technique : ' + data.detail : '');
    if (data.serveurs_connus && data.serveurs_connus.length) mt5ChargerServeurs();
    mt5AfficherNotif('❌ ' + (data.error || 'Erreur de connexion'), 'error');
  }
}

// ── Déconnexion ───────────────────────────────────────────────────────────
async function mt5Deconnecter() {
  const data = await mt5Fetch('/api/mt5/disconnect', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}' });
  if (data.success) {
    MT5.connected    = false;
    MT5.auto_trading = false;
    MT5.account_info = null;
    mt5RenderPanel();
    mt5AfficherNotif('Déconnecté de MT5', 'info');
  }
}

// ── Trading automatique ───────────────────────────────────────────────────
async function mt5ToggleAutoTrading() {
  if (!MT5.connected) {
    mt5AfficherNotif('⚠ Connectez-vous à MT5 avant d\'activer le trading automatique.', 'warning');
    return;
  }
  const newState = !MT5.auto_trading;
  const data = await mt5Fetch('/api/mt5/auto-trading', {
    method:  'POST',
    headers: { 'Content-Type': 'application/json' },
    body:    JSON.stringify({ active: newState }),
  });
  if (data.success) {
    MT5.auto_trading = newState;
    mt5RenderPanel();
    const msg = newState ? '🤖 Trading automatique ACTIVÉ' : '⏸ Trading automatique DÉSACTIVÉ';
    mt5AfficherNotif(msg, newState ? 'success' : 'info');
  }
}

// ── Journal ───────────────────────────────────────────────────────────────
async function mt5ChargerJournal() {
  const data = await mt5Fetch('/api/mt5/journal?limite=30');
  if (!data?.journal) return;
  MT5.journal = data.journal;
  mt5RenderJournal();
}

// ── Bouton header ─────────────────────────────────────────────────────────
function mt5UpdateHeaderBtn() {
  const btn   = document.getElementById('btn-avatrade');
  const label = document.getElementById('avatrade-label');
  if (!btn || !label) return;

  const ai = MT5.account_info;
  if (MT5.connected && ai) {
    btn.classList.add('connected');
    const typeLabel = ai.account_type === 'demo' ? 'DÉMO' : 'RÉEL';
    const account   = ai.account ? ai.account.slice(0,3) + '···' + ai.account.slice(-2) : '';
    label.textContent = `✓ AvaTrade ${typeLabel} ${account}`;
    btn.title = 'Connecté — cliquer pour changer de compte';
  } else {
    btn.classList.remove('connected');
    label.textContent = 'Connexion AvaTrade';
    btn.title = 'Se connecter à AvaTrade MT5';
  }
}

// ── Opération longue en cours (connexion, bascule de compte) ──────────────
// Le lancement du terminal MetaTrader peut durer une minute. Sans ce bandeau,
// l'application semblait simplement bloquée : les chiffres se figeaient et
// rien n'indiquait qu'une connexion était en cours.
function mt5RenderOperation(operation, occupe) {
  const panel = document.getElementById('mt5-panel');
  if (!panel || !panel.parentNode) return;
  let el = document.getElementById('mt5-operation');
  if (!operation && !occupe) { if (el) el.remove(); return; }
  if (!el) {
    el = document.createElement('div');
    el.id = 'mt5-operation';
    el.className = 'mt5-operation';
    panel.parentNode.insertBefore(el, panel);
  }
  el.textContent = operation
    ? '⏳ ' + operation + ' (le terminal peut mettre jusqu\'à une minute à répondre)'
    : '⏳ Terminal occupé — chiffres affichés à la dernière valeur connue';
}

// ── Rendu du panneau ──────────────────────────────────────────────────────
function mt5RenderPanel() {
  mt5UpdateHeaderBtn();
  const panel = document.getElementById('mt5-panel');
  if (!panel) return;

  const ai    = MT5.account_info;
  const fmt   = v => v != null ? parseFloat(v).toLocaleString('fr-FR', { minimumFractionDigits: 2, maximumFractionDigits: 2 }) : '—';
  const isSim = ai?.simulated;

  // `server`, `account` et `currency` viennent de la modale de connexion
  // (POST /api/mt5/connect) puis sont renvoyés tels quels par l'API : ils sont
  // échappés ici, comme tout texte d'origine utilisateur inséré en innerHTML.
  const compte  = ai && ai.account ? esc(String(ai.account)) : '';
  const serveur = ai ? esc(ai.server || '—') : '';
  const devise  = ai ? esc(ai.currency || '') : '';
  const levier  = ai && ai.leverage ? esc(String(ai.leverage)) : '—';

  if (MT5.connected && ai) {
    panel.innerHTML = `
      <div class="mt5-status-row">
        <span class="mt5-dot connected"></span>
        <span class="mt5-status-text">Connecté</span>
        <span class="mt5-badge ${ai.account_type === 'demo' ? 'badge-demo' : 'badge-real'}">
          ${ai.account_type === 'demo' ? 'DÉMO' : 'RÉEL'}
        </span>
        ${isSim ? '<span class="mt5-badge badge-sim">SIMULATION</span>' : ''}
      </div>

      <div class="mt5-info-grid">
        <div class="mt5-info-item">
          <span class="mt5-info-label">Compte</span>
          <span class="mt5-info-value mono">${compte ? compte.slice(0,3) + '···' + compte.slice(-3) : '—'}</span>
        </div>
        <div class="mt5-info-item">
          <span class="mt5-info-label">Serveur</span>
          <span class="mt5-info-value">${serveur}</span>
        </div>
        <div class="mt5-info-item">
          <span class="mt5-info-label">Balance</span>
          <span class="mt5-info-value positive">${fmt(ai.balance)} ${devise}</span>
        </div>
        <div class="mt5-info-item">
          <span class="mt5-info-label">Équité</span>
          <span class="mt5-info-value">${fmt(ai.equity)} ${devise}</span>
        </div>
        <div class="mt5-info-item">
          <span class="mt5-info-label">Marge libre</span>
          <span class="mt5-info-value">${fmt(ai.free_margin)} ${devise}</span>
        </div>
        <div class="mt5-info-item">
          <span class="mt5-info-label">Levier</span>
          <span class="mt5-info-value">1:${levier}</span>
        </div>
      </div>

      <div class="mt5-btn-row">
        <button class="mt5-btn btn-danger-sm" onclick="mt5Deconnecter()">Déconnecter</button>
        <button class="mt5-btn btn-outline-sm" onclick="mt5OuvrirModal()">Changer de compte</button>
      </div>

      <div class="mt5-auto-section">
        <div class="mt5-auto-header">
          <span class="mt5-auto-label">Trading Automatique</span>
          <button class="mt5-toggle ${MT5.auto_trading ? 'on' : 'off'}" onclick="mt5ToggleAutoTrading()">
            <span class="mt5-toggle-thumb"></span>
            <span class="mt5-toggle-label">${MT5.auto_trading ? 'ON' : 'OFF'}</span>
          </button>
        </div>
        ${MT5.auto_trading ? '<p class="mt5-auto-hint active">Les signaux BUY/SELL sont envoyés automatiquement à MT5.</p>' :
                             '<p class="mt5-auto-hint">Activez pour envoyer automatiquement les ordres vers MT5.</p>'}
      </div>`;
  } else {
    panel.innerHTML = `
      <div class="mt5-status-row">
        <span class="mt5-dot disconnected"></span>
        <span class="mt5-status-text muted">Non connecté</span>
      </div>
      <p class="mt5-hint">Connectez-vous à votre compte AvaTrade pour activer le trading.</p>
      <div class="mt5-btn-row">
        <button class="mt5-btn btn-primary-full" onclick="mt5OuvrirModal()">
          🔐 Connexion AvaTrade
        </button>
      </div>
      <div class="mt5-auto-section">
        <div class="mt5-auto-header">
          <span class="mt5-auto-label">Trading Automatique</span>
          <button class="mt5-toggle off disabled" disabled>
            <span class="mt5-toggle-thumb"></span>
            <span class="mt5-toggle-label">OFF</span>
          </button>
        </div>
        <p class="mt5-auto-hint">Requiert une connexion MT5 active.</p>
      </div>`;
  }
}

// ── Journal des opérations ────────────────────────────────────────────────
function mt5RenderJournal() {
  const el = document.getElementById('mt5-journal');
  if (!el) return;

  if (!MT5.journal.length) {
    el.innerHTML = '<p class="mt5-empty">Aucune opération enregistrée.</p>';
    return;
  }

  // Symbole et message d'erreur proviennent de saisies utilisateur et du
  // courtier : ils DOIVENT être échappés avant insertion en innerHTML.
  el.innerHTML = MT5.journal.map(e => {
    const cls = e.action === 'BUY' ? 'buy' : e.action === 'SELL' ? 'sell' : '';
    const ico = e.action === 'BUY' ? '🟢' : e.action === 'SELL' ? '🔴' : '⚪';
    return `
      <div class="mt5-journal-row ${e.success ? '' : 'error'}">
        <span class="jrn-time">${esc(e.timestamp)}</span>
        <span class="jrn-sym">${esc(e.symbol)}</span>
        <span class="jrn-action ${cls}">${ico} ${esc(e.action)}</span>
        <span class="jrn-vol">${parseFloat(e.volume || 0).toFixed(2)} lot</span>
        <span class="jrn-price">${e.price ? parseFloat(e.price).toFixed(5) : '—'}</span>
        <span class="jrn-status ${e.success ? 'ok' : 'err'}">${e.success ? '✓' : '✗ ' + esc(e.error || '')}</span>
      </div>`;
  }).join('');
}

// ── Compte déjà ouvert dans MetaTrader 5 ──────────────────────────────────
// Le chemin le plus simple, et de loin : si le terminal est déjà connecté au
// compte du courtier (le cas normal, MetaTrader mémorise la session), il n'y a
// aucune raison de redemander numéro, mot de passe et nom EXACT du serveur.
async function mt5ChargerCompteOuvert() {
  const bloc = document.getElementById('mt5-attacher-bloc');
  if (!bloc) return;
  const d = await mt5Fetch('/api/mt5/diagnostic');
  const compte = d && d.compte_ouvert;
  if (!compte || !compte.login) {
    bloc.classList.add('hidden');
    // Terminal injoignable : le dire ICI, avec les faits, plutôt que d'attendre
    // un échec de connexion et un « IPC timeout » sans contexte.
    const hint = document.getElementById('mt5-server-hint');
    if (hint && d && d.librairie_disponible && !d.joignable) {
      hint.textContent = 'MetaTrader 5 n\'est pas joignable depuis l\'application '
        + (d.derniere_erreur ? '(' + d.derniere_erreur + '). ' : '. ')
        + 'Ouvrez le terminal, fermez ses boîtes de dialogue, et vérifiez que '
        + 'les deux programmes tournent avec les mêmes droits.';
    }
    return;
  }
  bloc.classList.remove('hidden');
  const detail = document.getElementById('mt5-attacher-detail');
  if (detail) {
    // textContent : ces valeurs viennent du courtier.
    detail.textContent = `Compte ${compte.login} · ${compte.serveur || ''}`
      + (compte.devise ? ` · ${compte.devise}` : '')
      + (compte.societe ? ` · ${compte.societe}` : '');
  }
  // Pré-remplir aussi le formulaire manuel avec ce que dit le terminal :
  // le numéro et le serveur EXACTS, plus rien à deviner.
  const champCompte = document.getElementById('mt5-account');
  const champServeur = document.getElementById('mt5-server');
  if (champCompte && !champCompte.value) champCompte.value = compte.login;
  if (champServeur && compte.serveur) champServeur.value = compte.serveur;
}

async function mt5Attacher() {
  const btn = document.getElementById('mt5-attacher-btn');
  const errEl = document.getElementById('mt5-modal-error');
  if (btn) { btn.disabled = true; btn.textContent = 'Connexion…'; }
  if (errEl) errEl.textContent = '';
  const d = await mt5Fetch('/api/mt5/attacher', { method: 'POST' });
  if (btn) { btn.disabled = false; btn.textContent = 'Utiliser ce compte (sans mot de passe)'; }
  if (d && d.success) {
    MT5.connected = true;
    MT5.account_info = d.account_info;
    mt5FermerModal();
    mt5RenderPanel();
    mt5AfficherNotif('✅ Connecté — compte ' + ((d.account_info || {}).account || ''), 'success');
    return;
  }
  if (errEl) {
    errEl.textContent = (d && d.error) || 'Reprise du compte impossible.'
      + ((d && d.detail) ? '\n\nDétail technique : ' + d.detail : '');
  }
}

window.mt5Attacher = mt5Attacher;

// ── Modal connexion ───────────────────────────────────────────────────────
async function mt5OuvrirModal() {
  const modal = document.getElementById('mt5-modal');
  if (!modal) return;
  modal.classList.remove('hidden');
  document.getElementById('mt5-modal-error').textContent = '';
  mt5ChargerServeurs();
  mt5ChargerCompteOuvert();
  // Pré-remplir avec les identifiants mémorisés (jamais le mot de passe)
  try {
    const s = await mt5Fetch('/api/mt5/saved');
    if (s && s.enregistres) {
      const acc = document.getElementById('mt5-account');
      const srv = document.getElementById('mt5-server');
      if (acc && !acc.value) acc.value = s.account || '';
      if (srv && s.server) srv.value = s.server;
      const err = document.getElementById('mt5-modal-error');
      if (err) { err.style.color = 'var(--text-muted)'; err.textContent = 'Identifiants mémorisés — entrez le mot de passe pour reconnecter, ou modifiez le compte.'; }
    }
  } catch (e) {}
  document.getElementById('mt5-password')?.focus();
}

function mt5FermerModal() {
  const modal = document.getElementById('mt5-modal');
  if (modal) modal.classList.add('hidden');
}

// Fermer la modal en cliquant l'overlay
document.addEventListener('DOMContentLoaded', () => {
  const modal = document.getElementById('mt5-modal');
  if (modal) {
    modal.addEventListener('click', e => {
      if (e.target === modal) mt5FermerModal();
    });
  }

  // Toggle visibilité du mot de passe
  const togglePwd = document.getElementById('mt5-pwd-toggle');
  const pwdInput  = document.getElementById('mt5-password');
  if (togglePwd && pwdInput) {
    togglePwd.addEventListener('click', () => {
      pwdInput.type = pwdInput.type === 'password' ? 'text' : 'password';
      togglePwd.textContent = pwdInput.type === 'password' ? '👁' : '🙈';
    });
  }

  // Serveur par défaut selon démo/réel — SANS jamais écraser un nom saisi.
  // Le champ est en saisie libre : remplacer ce que l'utilisateur vient de
  // taper parce qu'il coche « Réel » lui ferait perdre le nom exact de son
  // serveur, celui-là même que la liste figée ne proposait pas.
  document.querySelectorAll('input[name="mt5-type"]').forEach(radio => {
    radio.addEventListener('change', () => {
      const champ = document.getElementById('mt5-server');
      if (!champ) return;
      const actuel = (champ.value || '').trim();
      const remplacable = !actuel || MT5._serveursReference.includes(actuel);
      if (!remplacable) return;
      champ.value = radio.value === 'demo' ? 'Ava-Demo 1-MT5' : 'Ava-Real 1-MT5';
    });
  });

  initMT5();
});

// ── Notifications ─────────────────────────────────────────────────────────
function mt5AfficherNotif(message, type = 'info') {
  const container = document.getElementById('mt5-notif') || document.body;
  const el = document.createElement('div');
  el.className = `mt5-notif mt5-notif-${type}`;
  el.textContent = message;
  container.appendChild(el);
  setTimeout(() => el.remove(), 4000);
}

// ── Exports globaux ───────────────────────────────────────────────────────
window.mt5OuvrirModal      = mt5OuvrirModal;
window.mt5FermerModal      = mt5FermerModal;
window.mt5Connecter        = mt5Connecter;
window.mt5Deconnecter      = mt5Deconnecter;
window.mt5ToggleAutoTrading = mt5ToggleAutoTrading;
window.mt5ChargerStatut    = mt5ChargerStatut;
window.mt5ChargerServeurs  = mt5ChargerServeurs;
