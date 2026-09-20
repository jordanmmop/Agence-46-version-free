// ========================= AUTO-TRADER MODULE =========================

const AT = {
  running:    false,
  interval:   30,
  symboles:   [],
  cycleCount: 0,
  lastRun:    null,
  _pollTimer: null,
  _countdownTimer: null,
  _nextRunAt:  null,
};

// ── Init ──────────────────────────────────────────────────────────────────
function initAutoTrader() {
  _atInitChips();
  _atInitIntervalBtns();
  atChargerStatut();
  AT._pollTimer = setInterval(atChargerStatut, 3000);
}

// ── Chips symboles ────────────────────────────────────────────────────────
// Liste de repli si /api/symboles ne répond pas. Auparavant cette liste était
// la SEULE source : les paires réellement proposées par le courtier (USDCHF,
// AUDUSD, NZDUSD, USDCAD, USDSEK...) restaient introuvables dans le panneau
// de trading automatique, même après ajout dans la configuration du serveur.
const AT_SYMBOLES_REPLI = ['BTC-USD', 'ETH-USD', 'EURUSD=X', 'GBPUSD=X',
                           'USDJPY=X', 'GC=F', 'CL=F', 'AAPL', 'MSFT', 'NVDA'];

async function _atInitChips() {
  const container = document.getElementById('at-symboles-chips');
  if (!container) return;

  let symboles = AT_SYMBOLES_REPLI;
  const data = await _atFetch('/api/symboles');
  if (data && typeof symbolesProposes === 'function') {
    const proposes = symbolesProposes(data);
    if (proposes.length) symboles = proposes;
  }
  AT.symboles = symboles.slice(0, 3);

  container.innerHTML = '';
  symboles.forEach(sym => {
    const chip = document.createElement('button');
    chip.className = 'chip' + (AT.symboles.includes(sym) ? ' chip-active' : '');
    // Affiché comme dans le Market Watch du courtier (EURUSD), envoyé au
    // backend au format Yahoo (EURUSD=X) — textContent, donc jamais interprété.
    chip.textContent = typeof labelSymbole === 'function' ? labelSymbole(sym) : sym;
    chip.title = sym;
    chip.onclick = () => {
      if (AT.running) return;
      if (AT.symboles.includes(sym)) {
        if (AT.symboles.length === 1) return;
        AT.symboles = AT.symboles.filter(s => s !== sym);
        chip.classList.remove('chip-active');
      } else if (AT.symboles.length < 5) {
        AT.symboles.push(sym);
        chip.classList.add('chip-active');
      }
    };
    container.appendChild(chip);
  });
}

// ── Boutons intervalle ────────────────────────────────────────────────────
function _atInitIntervalBtns() {
  document.querySelectorAll('.at-interval-btn').forEach(btn => {
    btn.addEventListener('click', () => {
      if (AT.running) return;
      document.querySelectorAll('.at-interval-btn').forEach(b => b.classList.remove('active'));
      btn.classList.add('active');
      AT.interval = parseInt(btn.dataset.s);
    });
  });
}

// Nom courtier pour l'affichage (délègue à app.js, avec repli si non chargé)
function _atLabel(s) {
  return typeof labelSymbole === 'function' ? labelSymbole(s) : String(s ?? '');
}

// ── Statut ────────────────────────────────────────────────────────────────
async function atChargerStatut() {
  const data = await _atFetch('/api/auto-trader/status');
  if (!data) return;

  const wasRunning = AT.running;
  AT.running    = data.running || false;
  AT.cycleCount = data.cycle_count || 0;
  AT.lastRun    = data.last_run || null;
  AT.mode       = data.mode || AT.mode;            // mémorise le mode réel/sim
  if (data.interval) AT.interval = data.interval;  // aligne le décompte sur le serveur

  AT.algotrading = data.algotrading;
  AT.suspendu    = !!data.suspendu;
  AT.raisonSuspension = data.raison_suspension || '';
  _atRenderBadge(data.mode);
  _atRenderAbstentions(data.abstentions || []);
  _atRenderControls();
  _atRenderModeNotice(data.mode, data.mt5_connected, data.mode_raison, data.algotrading);
  _atRenderPositions(data.positions || []);
  _atRenderSignals(data.last_signals || {});
  _atRenderJournal(data.journal || []);
  _atRenderErrors(data.errors || []);
  _atRenderRisk(data.risk || null, data.trading || null);

  if (AT.running) {
    if (!wasRunning) _atStartCountdown();
    document.getElementById('at-cycle').textContent    = `#${AT.cycleCount}`;
    document.getElementById('at-last-run').textContent = AT.lastRun || '—';
  } else {
    _atStopCountdown();
  }
}

// ── Démarrer ──────────────────────────────────────────────────────────────
async function atDemarrer() {
  // Argent réel : confirmation explicite avant d'engager le compte (un clic
  // accidentel ne doit jamais lancer des ordres réels). La confirmation ne
  // s'affiche QUE sur un compte financé : la déclencher aussi en démo la
  // banaliserait, et elle ne serait plus lue le jour où elle compte.
  if (AT.mode === 'reel') {
    const ok = window.confirm(
      '⚠️ TRADING RÉEL\n\nVous allez lancer le trading automatique sur votre compte '
      + 'AvaTrade RÉEL. Les 46 agents passeront des ordres avec de l\'argent réel, '
      + 'sans confirmation supplémentaire.\n\nConfirmer le démarrage ?');
    if (!ok) return;
  }
  // Blocage connu : sans le bouton AlgoTrading du terminal, aucun ordre ne
  // partira. Prévenir maintenant évite un cycle entier d'ordres rejetés.
  if (AT.algotrading === false) {
    const ok = window.confirm(
      '⛔ Le bouton « AlgoTrading » est désactivé dans MetaTrader 5.\n\n'
      + 'Tous les ordres seront REJETÉS (code 10027) tant qu\'il ne sera pas activé.\n\n'
      + 'Ouvrez MetaTrader 5 → barre d\'outils → « AlgoTrading » (Ctrl+E), '
      + 'le bouton doit devenir vert.\n\nDémarrer quand même (analyse seule) ?');
    if (!ok) return;
  }
  // Démarrage direct — MT5 réel si connecté, simulation sinon
  const data = await _atFetch('/api/auto-trader/start', {
    method:  'POST',
    headers: { 'Content-Type': 'application/json' },
    body:    JSON.stringify({ symboles: AT.symboles, interval: AT.interval }),
  });

  if (data?.success) {
    AT.running = true;
    AT._nextRunAt = Date.now() + AT.interval * 1000;
    _atRenderBadge();
    _atRenderControls();
    _atStartCountdown();
    _atNotif(`🚀 Trading automatique démarré — analyse toutes les ${AT.interval}s`, 'success');
  } else {
    _atNotif('❌ ' + (data?.error || 'Erreur démarrage'), 'error');
  }
}

// ── Arrêter ───────────────────────────────────────────────────────────────
async function atArreter() {
  const data = await _atFetch('/api/auto-trader/stop', {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}',
  });
  if (data?.success) {
    AT.running = false;
    _atStopCountdown();
    _atRenderBadge();
    _atRenderControls();
    _atNotif('⏹ Trading automatique arrêté', 'info');
  }
}

// ── Countdown ─────────────────────────────────────────────────────────────
function _atStartCountdown() {
  AT._nextRunAt = Date.now() + AT.interval * 1000;
  _atStopCountdown();
  AT._countdownTimer = setInterval(() => {
    const remaining = Math.max(0, Math.round((AT._nextRunAt - Date.now()) / 1000));
    document.getElementById('at-countdown').textContent = remaining + 's';
    if (remaining === 0) AT._nextRunAt = Date.now() + AT.interval * 1000;
  }, 500);
}
function _atStopCountdown() {
  if (AT._countdownTimer) { clearInterval(AT._countdownTimer); AT._countdownTimer = null; }
  const el = document.getElementById('at-countdown');
  if (el) el.textContent = '—';
}

// ── Rendu badge ───────────────────────────────────────────────────────────
function _atRenderBadge(mode) {
  const badge = document.getElementById('at-badge');
  if (!badge) return;
  // Sans argument (démarrage/arrêt), retomber sur le dernier mode connu :
  // sinon une session RÉELLE serait brièvement étiquetée « SIM » à tort.
  if (mode === undefined) mode = AT.mode;
  if (AT.running && AT.suspendu) {
    // La boucle tourne mais N'OUVRE PLUS RIEN (kill-switch). Afficher « ACTIF »
    // ici était trompeur : l'utilisateur voyait un robot vert qui ne passait
    // aucun ordre, sans savoir que la sécurité l'avait suspendu.
    badge.textContent = '⏸ SUSPENDU';
    badge.className = 'at-status-badge at-badge-suspendu';
  } else if (AT.running) {
    // Trois états : RÉEL (argent engagé), DÉMO (ordres réels, argent fictif),
    // SIM (rien n'est envoyé au courtier).
    const modeLbl = mode === 'reel' ? '' : (mode === 'demo' ? ' · DÉMO' : ' · SIM');
    badge.textContent = `● ACTIF${modeLbl}`;
    badge.className = `at-status-badge at-badge-active ${mode === 'reel' ? '' : 'at-badge-sim'}`.trim();
  } else {
    badge.textContent = 'INACTIF';
    badge.className = 'at-status-badge';
  }
}

// ── Rendu sécurité (RiskGuard) ────────────────────────────────────────────
function _atRenderRisk(risk, trading) {
  let el = document.getElementById('at-risk');
  if (!el) {
    el = document.createElement('div');
    el.id = 'at-risk';
    el.className = 'at-risk';
    const badge = document.getElementById('at-badge');
    if (badge && badge.parentNode) badge.parentNode.appendChild(el);
    else return;
  }

  // Agressivité RÉELLE du robot : affichée en permanence, y compris quand les
  // garde-fous sont désactivés — c'est justement là qu'il faut la voir.
  const perf = trading && trading.levier
    ? `⚡ Levier x${Number(trading.levier).toFixed(1)}/${Number(trading.levier_max).toFixed(0)} `
      + `(${esc(trading.levier_source || 'réglage')}) · risque/ordre ${trading.risque_par_trade_pct}%`
    : '';

  if (risk && risk.kill_switch) {
    el.style.display = 'block';
    el.className = 'at-risk at-risk-kill';
    el.innerHTML =
      `🛑 <b>Sécurité déclenchée</b> — ${esc(risk.raison || 'trading suspendu')}` +
      ` <button class="at-risk-reset" onclick="atResetRisk()">Réarmer</button>`;
    return;
  }
  if (!risk || !risk.actif) {
    if (!perf) { el.style.display = 'none'; return; }
    el.style.display = 'block';
    el.className = 'at-risk';
    el.innerHTML = perf + ' · <b>garde-fous désactivés</b>';
    return;
  }
  el.style.display = 'block';
  el.className = 'at-risk';
  const perte = (risk.perte_jour_pct || 0);
  el.innerHTML =
    (perf ? perf + '<br>' : '') +
    `🛡️ Sécurité active · perte jour ${perte.toFixed(1)}%/${risk.perte_max_pct}% · ` +
    `max ${risk.max_positions} pos. · expo ${risk.exposition_max_pct}%`;
}

// ── Pourquoi aucun ordre n'est parti ? ────────────────────────────────────
// Le tableau de bord affichait « Aucun ordre exécuté » sans plus d'explication :
// impossible de distinguer un robot en panne d'un robot qui applique ses règles
// (marché fermé, pas de consensus, cooldown, plafond atteint, sécurité).
function _atRenderAbstentions(abstentions) {
  let el = document.getElementById('at-abstentions');
  if (!el) {
    const journal = document.getElementById('at-journal');
    if (!journal || !journal.parentNode) return;
    el = document.createElement('div');
    el.id = 'at-abstentions';
    el.className = 'at-abstentions';
    journal.parentNode.appendChild(el);
  }

  // La suspension par la sécurité prime : c'est LA raison qui bloque tout.
  const bloc = [];
  if (AT.suspendu) {
    bloc.push(
      `<div class="at-abst-kill">⏸ <b>Ouvertures suspendues par la sécurité</b>`
      + `<div>${esc(AT.raisonSuspension || 'kill-switch déclenché')}</div>`
      + `<button class="at-risk-reset" onclick="atResetRisk()">Réarmer et reprendre</button>`
      + `</div>`);
  }
  if (abstentions.length) {
    bloc.push('<div class="at-abst-titre">Pourquoi aucun ordre</div>');
    bloc.push(abstentions.map(a =>
      `<div class="at-abst-ligne"><span class="at-abst-sym" title="${esc(a.symbole || '')}">`
      + `${esc(_atLabel(a.symbole))}</span>`
      + `<span class="at-abst-motif">${esc(a.motif || '')}</span>`
      + `<span class="at-abst-heure">${esc(a.heure || '')}</span></div>`).join(''));
  }
  if (!bloc.length) { el.style.display = 'none'; el.innerHTML = ''; return; }
  el.style.display = '';
  el.innerHTML = bloc.join('');
}

async function atResetRisk() {
  await _atFetch('/api/risk/reset', { method: 'POST' });
  atChargerStatut();
}
window.atResetRisk = atResetRisk;

// ── Notice simulation / réel ──────────────────────────────────────────────
function _atRenderModeNotice(mode, mt5Connected, raison, algotrading) {
  let el = document.getElementById('at-mode-notice');
  if (!el) {
    el = document.createElement('div');
    el.id = 'at-mode-notice';
    const errEl = document.getElementById('at-errors');
    if (errEl) errEl.parentNode.insertBefore(el, errEl);
  }
  // Bouton AlgoTrading du terminal éteint : TOUS les ordres seront rejetés
  // (code 10027). Affiché en tête, avant toute autre information de mode :
  // sans lui, l'utilisateur voit « Mode RÉEL » et croit le robot opérationnel.
  const algoKo = (algotrading === false)
    ? `<div style="margin-bottom:0.6rem;padding:0.5rem 0.7rem;border-radius:6px;
         background:rgba(239,68,68,0.12);border:1px solid var(--red);color:var(--red);font-weight:600">
         ⛔ Bouton « AlgoTrading » désactivé dans MetaTrader 5 — aucun ordre ne peut partir.
         <div style="font-weight:400;margin-top:0.3rem;color:var(--text-muted)">
           Ouvrez le terminal MetaTrader 5 → cliquez « AlgoTrading » dans la barre d'outils
           (il doit devenir vert). Raccourci : Ctrl+E.
         </div>
       </div>`
    : '';

  if (mode === 'reel') {
    el.className = 'at-mode-notice at-mode-real';
    el.innerHTML = algoKo
      + '✅ Mode RÉEL — les ordres partent sur votre compte AvaTrade, avec de l\'argent réel.';
  } else if (mode === 'demo') {
    // Vraie connexion MetaTrader, argent FICTIF : ne pas annoncer « réel ».
    el.className = 'at-mode-notice at-mode-real';
    el.innerHTML = algoKo
      + '🧪 Mode DÉMO — les ordres partent bien sur votre compte AvaTrade, '
      + 'mais l\'argent est <b>fictif</b>. C\'est ici qu\'il faut valider symboles, '
      + 'volumes et stops avant de passer sur un compte financé.';
  } else {
    el.className = 'at-mode-notice at-mode-sim';
    const diag = raison ? `<div style="margin-bottom:0.6rem;color:var(--yellow)">Raison : ${esc(raison)}</div>` : '';
    el.innerHTML = `
      <div class="at-sim-title">⚠ Mode Simulation — ordres non envoyés à AvaTrade</div>
      <div class="at-sim-steps">
        ${diag}
        <strong>Pour envoyer de vrais ordres (gratuit, sans service tiers) :</strong>
        <ol>
          <li>Ouvrez <strong>Réglages ⚙️ → Trading réel AvaTrade</strong> et cliquez
              <strong>« Installer MetaTrader 5 »</strong> (Windows)</li>
          <li>Cliquez <strong>« Connexion AvaTrade »</strong> et saisissez votre numéro
              de compte, votre mot de passe et votre serveur</li>
          <li>Lancez <strong>Réglages ⚙️ → « Vérification avant trading réel »</strong>
              pour contrôler ce qui serait envoyé</li>
        </ol>
      </div>`;
  }
}

// ── Rendu contrôles ───────────────────────────────────────────────────────
function _atRenderControls() {
  const btnStart = document.getElementById('at-btn-start');
  const btnStop  = document.getElementById('at-btn-stop');
  const live     = document.getElementById('at-live');
  if (!btnStart || !btnStop) return;

  if (AT.running) {
    btnStart.style.display = 'none';
    btnStop.style.display  = '';
    if (live) live.style.display = '';
    document.querySelectorAll('.at-interval-btn, #at-symboles-chips .chip').forEach(el => el.style.opacity = '0.4');
  } else {
    btnStart.style.display = '';
    btnStop.style.display  = 'none';
    if (live) live.style.display = 'none';
    document.querySelectorAll('.at-interval-btn, #at-symboles-chips .chip').forEach(el => el.style.opacity = '');
  }
}

// ── Rendu positions ───────────────────────────────────────────────────────
function _atRenderPositions(positions) {
  const el = document.getElementById('at-positions');
  if (!el) return;
  if (!positions.length) {
    el.innerHTML = '<p class="at-empty">Aucune position ouverte</p>';
    return;
  }
  el.innerHTML = positions.map(p => {
    const pnlClass = p.profit >= 0 ? 'positive' : 'negative';
    const dir = p.type === 'BUY' ? '▲' : '▼';
    const dirClass = p.type === 'BUY' ? 'buy' : 'sell';
    // Toutes les valeurs viennent du courtier (ou, en simulation, du journal
    // alimenté par /api/mt5/trade) : échappées ou converties en nombre.
    const prix = Number(p.price_current ?? p.price_open);
    return `
      <div class="at-position-row">
        <span class="at-pos-dir ${dirClass}">${dir} ${esc(p.type)}</span>
        <span class="at-pos-sym">${esc(p.symbol)}</span>
        <span class="at-pos-vol">${(Number(p.volume) || 0).toFixed(2)} lot</span>
        <span class="at-pos-price">${Number.isFinite(prix) ? prix.toFixed(5) : '—'}</span>
        <span class="at-pos-pnl ${pnlClass}">${p.profit >= 0 ? '+' : ''}${Number(p.profit) || 0} $</span>
      </div>`;
  }).join('');
}

// ── Rendu signaux ─────────────────────────────────────────────────────────
function _atRenderSignals(signals) {
  const el = document.getElementById('at-signals');
  if (!el) return;
  const entries = Object.entries(signals);
  if (!entries.length) {
    el.innerHTML = '<p class="at-empty">En attente d\'analyse...</p>';
    return;
  }
  el.innerHTML = entries.map(([sym, dec]) => {
    const action = dec.action || 'HOLD';
    const conf   = dec.confiance || 0;
    const cls    = action === 'BUY' ? 'buy' : action === 'SELL' ? 'sell' : '';
    const ico    = action === 'BUY' ? '🟢' : action === 'SELL' ? '🔴' : '⚪';
    return `
      <div class="at-signal-row">
        <span class="at-sig-sym" title="${esc(sym)}">${esc(_atLabel(sym))}</span>
        <span class="at-sig-action ${cls}">${ico} ${esc(action)}</span>
        <span class="at-sig-conf">${conf}% confiance</span>
      </div>`;
  }).join('');
}

// ── Rendu erreurs ─────────────────────────────────────────────────────────
function _atRenderJournal(journal) {
  const el = document.getElementById('at-journal');
  if (!el) return;
  if (!journal.length) { el.innerHTML = '<p class="at-empty">Aucun ordre exécuté</p>'; return; }
  el.innerHTML = journal.slice(0, 10).map(e => {
    const cls = e.action === 'BUY' ? 'buy' : 'sell';
    const ico = e.action === 'BUY' ? '🟢' : '🔴';
    // e.error vient du courtier : échappement obligatoire (cf. _atRenderErrors)
    const ok  = e.success ? '✓' : `✗ ${esc(e.error || '')}`;
    const sim = e.price && parseFloat(e.price) > 0 ? parseFloat(e.price).toFixed(4) : '—';
    return `<div class="at-position-row">
      <span class="at-pos-dir ${cls}">${ico} ${esc(e.action)}</span>
      <span class="at-pos-sym" title="${esc(e.symbol)}">${esc(_atLabel(e.symbol))}</span>
      <span class="at-pos-vol">${parseFloat(e.volume||0).toFixed(2)} lot</span>
      <span class="at-pos-price">${sim}</span>
      <span class="at-pos-pnl ${e.success ? 'positive' : 'negative'}">${ok}</span>
    </div>`;
  }).join('');
}

function _atRenderErrors(errors) {
  const el = document.getElementById('at-errors');
  if (!el) return;
  if (!errors.length) { el.style.display = 'none'; return; }
  el.style.display = '';
  // esc() OBLIGATOIRE : ces messages contiennent du texte venu du courtier et
  // des noms de symboles saisis par l'utilisateur. Sans échappement, un nom de
  // symbole contenant du HTML s'exécuterait dans le tableau de bord — qui est
  // exposé sur le réseau Wi-Fi local (accès iPhone/Android documenté).
  el.innerHTML = errors.map(e => `<div class="at-error-line">⚠ ${esc(e)}</div>`).join('');
}

// ── Helpers ───────────────────────────────────────────────────────────────
async function _atFetch(url, opts = {}) {
  try {
    const r = await fetch(url, opts);
    if (r.status === 401) { if (window._authRedirect) window._authRedirect(); return null; }
    return await r.json();
  } catch { return null; }
}

function _atNotif(msg, type = 'info') {
  if (typeof mt5AfficherNotif === 'function') mt5AfficherNotif(msg, type);
}

// ── Export + init ─────────────────────────────────────────────────────────
window.atDemarrer = atDemarrer;
window.atArreter  = atArreter;

document.addEventListener('DOMContentLoaded', initAutoTrader);
