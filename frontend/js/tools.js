// ═══════════════ Panneau Réglages & Outils ═══════════════
// Backtest, notifications, sécurité (mot de passe, déconnexion).
// Injecté depuis JS pour ne pas alourdir index.html.

(function () {
  function h(html) { const d = document.createElement('div'); d.innerHTML = html; return d.firstElementChild; }

  // Bouton engrenage flottant
  const gear = h('<button id="tools-gear" title="Réglages" aria-label="Réglages">⚙️</button>');
  gear.onclick = ouvrirReglages;
  document.addEventListener('DOMContentLoaded', () => document.body.appendChild(gear));

  function ouvrirReglages() {
    let m = document.getElementById('tools-modal');
    if (m) { m.style.display = 'flex'; return; }
    m = h(`<div id="tools-modal">
      <div class="tools-box">
        <div class="tools-head">
          <span>⚙️ Réglages & Outils</span>
          <button class="tools-close" onclick="document.getElementById('tools-modal').style.display='none'">✕</button>
        </div>

        <div class="tools-section">
          <h3>⚡ Moteur de performance</h3>
          <p class="tools-hint">Tous les plafonds du robot, en un seul endroit. Ce sont
          les réglages que les 46 agents appliquent réellement : ils prennent effet au
          cycle suivant, sans redémarrage.</p>

          <div class="tc-grid">
            <label class="tc-field">
              <span>Levier maximum (1 à 10)</span>
              <input id="tc-levier-max" type="number" min="1" max="10" step="0.5">
            </label>
            <label class="tc-field">
              <span>Levier courant</span>
              <input id="tc-levier" type="number" min="1" max="10" step="0.5">
            </label>
            <label class="tc-field">
              <span>Objectif de gain / jour</span>
              <input id="tc-objectif" type="number" min="0" step="1">
            </label>
            <label class="tc-field">
              <span>Capital de référence</span>
              <input id="tc-capital" type="number" min="1" step="1">
            </label>
            <label class="tc-field">
              <span>Risque par ordre (% équité)</span>
              <input id="tc-risque" type="number" min="0.1" max="100" step="0.5">
            </label>
            <label class="tc-field">
              <span>Volume max (lots)</span>
              <input id="tc-volmax" type="number" min="0.01" max="100" step="0.01">
            </label>
            <label class="tc-field">
              <span>Confiance minimale (%) — 0 = aucun filtre</span>
              <input id="tc-confiance" type="number" min="0" max="100" step="1">
            </label>
            <label class="tc-field">
              <span>Délai anti ré-entrée (s)</span>
              <input id="tc-cooldown" type="number" min="0" max="86400" step="10">
            </label>
            <label class="tc-field">
              <span>Stop-loss par défaut (%)</span>
              <input id="tc-sl" type="number" min="0.1" max="50" step="0.1">
            </label>
            <label class="tc-field">
              <span>Take-profit par défaut (%)</span>
              <input id="tc-tp" type="number" min="0.1" max="200" step="0.1">
            </label>
            <label class="tc-field">
              <span>Spread max (%, 0 = aucun)</span>
              <input id="tc-spread" type="number" min="0" max="100" step="0.1">
            </label>
            <label class="tc-field">
              <span>Positions max / symbole</span>
              <input id="tc-possym" type="number" min="1" max="100" step="1">
            </label>
          </div>

          <div class="tc-switches">
            <label><input id="tc-levier-auto" type="checkbox"> Levier piloté par les agents IA</label>
            <label><input id="tc-sizing" type="checkbox"> Dimensionner les ordres au levier</label>
            <label><input id="tc-sl-oblig" type="checkbox"> Stop-loss obligatoire sur chaque ordre</label>
            <label><input id="tc-lot-min" type="checkbox"> Envoyer au lot minimum si le risque le dépasse</label>
            <label><input id="tc-exiger-risque" type="checkbox"> Refuser si la perte au stop est incalculable</label>
            <label><input id="tc-anti-emp" type="checkbox"> Anti-empilement (1 position par sens)</label>
            <label><input id="tc-heures" type="checkbox"> Respecter les heures de marché</label>
            <label><input id="tc-simulees" type="checkbox"> Bloquer les ordres réels sur prix simulés</label>
            <label><input id="tc-stop-obj" type="checkbox"> Arrêter d'ouvrir une fois l'objectif atteint</label>
          </div>

          <div class="tools-row" style="margin-top:10px">
            <button onclick="toolsSaveTrading()">Enregistrer les réglages</button>
          </div>
          <div id="tc-status" class="tools-result"></div>
          <p class="tools-hint" style="margin-top:8px">La décision finale du Chef
          d'Orchestre dépasse rarement <strong>65 % de confiance</strong> (elle est
          bridée à 90 % et pondérée à la baisse) : une « confiance minimale » au-dessus
          de 65 revient à ne plus jamais trader. Laissez 0 en cas de doute.</p>
          <p class="tools-warn">Un levier élevé multiplie les gains ET les pertes dans la
          même proportion : à x10, un mouvement de 10 % contre la position efface
          l'équivalent du capital engagé. Aucun réglage ne garantit un gain quotidien.</p>
        </div>

        <div class="tools-section">
          <h3>🛡️ Sécurité — plafonds de coupure</h3>
          <p class="tools-hint">Le filet qui coupe le trading avant le pire. Décochez
          « Garde-fous actifs » pour les lever entièrement (aucune coupure automatique).</p>
          <div class="tc-grid">
            <label class="tc-field">
              <span>Perte max / jour (%)</span>
              <input id="rg-perte" type="number" min="0.5" max="100" step="0.5">
            </label>
            <label class="tc-field">
              <span>Positions simultanées max</span>
              <input id="rg-positions" type="number" min="1" max="500" step="1">
            </label>
            <label class="tc-field">
              <span>Exposition max (%)</span>
              <input id="rg-expo" type="number" min="1" max="2000" step="10">
            </label>
          </div>
          <div class="tc-switches">
            <label><input id="rg-actif" type="checkbox"> Garde-fous actifs</label>
          </div>
          <div class="tools-row" style="margin-top:10px">
            <button onclick="toolsSaveRisk()">Enregistrer</button>
            <button onclick="toolsResetKill()">Réarmer le kill-switch</button>
          </div>
          <div id="rg-status" class="tools-result"></div>
        </div>

        <div class="tools-section">
          <h3>🚦 Vérification avant trading réel</h3>
          <p class="tools-hint">Rejoue la décision complète sur votre compte connecté et affiche
          ce qui <strong>serait</strong> envoyé (symbole chez le courtier, volume, stop-loss,
          perte au stop) — <strong>sans passer aucun ordre</strong>. À lancer avant d'engager
          de l'argent réel.</p>
          <div class="tools-row">
            <input id="pf-symboles" placeholder="BTCUSD, AAPL, EURUSD (vide = par défaut)">
            <button onclick="toolsPreflight()">Vérifier</button>
          </div>
          <div id="pf-result" class="tools-result"></div>
        </div>

        <div class="tools-section">
          <h3>📊 Backtest d'une stratégie</h3>
          <p class="tools-hint">Rejoue une stratégie technique (RSI + tendance) sur l'historique.</p>
          <div class="tools-row">
            <input id="bt-symbole" placeholder="BTC-USD" value="BTC-USD">
            <select id="bt-tf">
              <option value="1d">Journalier</option>
              <option value="1h">Horaire</option>
              <option value="1wk">Hebdo</option>
            </select>
            <button onclick="toolsBacktest()">Lancer</button>
          </div>
          <div id="bt-result" class="tools-result"></div>
        </div>

        <div class="tools-section">
          <h3>🧠 Moteur IA</h3>
          <p class="tools-hint">Qui répond aux 46 assistants et au Chef d'Orchestre.
          Les deux moteurs sont livrés avec l'application et tournent
          <strong>sur votre machine</strong> — aucune clé API, aucun envoi vers
          l'extérieur. Le choix prend effet au cycle suivant, sans redémarrage.</p>
          <div class="tools-row">
            <select id="ia-moteur">
              <option value="ollama">🖥️ Ollama — local, gratuit</option>
              <option value="hermes">🪽 Hermès — local, gratuit</option>
            </select>
            <select id="ia-modele"></select>
            <button onclick="toolsIaAppliquer()">Appliquer</button>
          </div>
          <div id="ia-status" class="tools-result lignes"></div>
        </div>

        <div class="tools-section">
          <h3>💹 Trading réel AvaTrade — MetaTrader 5 (gratuit)</h3>
          <p class="tools-hint">MetaTrader 5 est <strong>livré avec l'application</strong> :
          il démarre tout seul, il n'y a qu'à saisir vos identifiants AvaTrade. Les ordres
          partent ensuite sur votre compte, <strong>gratuitement</strong> et sans service tiers.</p>
          <div id="mt5inst-status" class="tools-result"></div>
          <div class="tools-row">
            <button onclick="toolsMt5Demarrer()">▶ Démarrer / réparer la connexion</button>
            <button onclick="toolsMt5Fermer()">✕ Fermer tous les terminaux</button>
            <button onclick="toolsMt5Check()">🔄 Vérifier</button>
            <button onclick="toolsMt5Install()">📥 Installer MetaTrader 5</button>
          </div>
          <div id="mt5inst-progress" style="display:none;margin-top:10px">
            <div style="background:var(--surface2);border-radius:6px;height:8px;overflow:hidden">
              <div id="mt5inst-bar" style="background:var(--accent);height:100%;width:0%;transition:width .4s"></div>
            </div>
            <p id="mt5inst-detail" style="font-size:12px;color:var(--text-muted);margin-top:6px"></p>
          </div>
        </div>

        <div class="tools-section">
          <h3>📈 P&amp;L réel (compte connecté)</h3>
          <p class="tools-hint">Résultats réels depuis l'historique de votre compte connecté.</p>
          <div class="tools-row">
            <select id="pnl-jours">
              <option value="7">7 jours</option>
              <option value="30" selected>30 jours</option>
              <option value="90">90 jours</option>
            </select>
            <button onclick="toolsPnlReel()">Afficher</button>
            <button onclick="toolsRapportTest()">📊 Rapport du jour</button>
          </div>
          <div id="pnl-result" class="tools-result"></div>
        </div>

        <div class="tools-section">
          <h3>📁 Export de l'historique</h3>
          <p class="tools-hint">Téléchargez tous les ordres exécutés au format CSV (ouvrable dans Excel / Google Sheets).</p>
          <div class="tools-row">
            <button onclick="toolsExportCsv()">⬇️ Exporter les trades (CSV)</button>
          </div>
        </div>

        <div class="tools-section">
          <h3>👥 Comptes AvaTrade</h3>
          <p class="tools-hint">Chaque compte MetaTrader 5 connecté est mémorisé ici.
          Basculez de l'un à l'autre en un clic — les identifiants sont réutilisés automatiquement.</p>
          <div id="comptes-list" class="comptes-list"></div>
        </div>

        <div class="tools-section">
          <h3>🔔 Notifications</h3>
          <p class="tools-hint">Recevez une alerte sur chaque ordre et sur le kill-switch. Deux canaux cumulables.</p>
          <p class="tools-hint"><strong>Webhook</strong> (Discord/Slack) — collez l'URL de votre webhook.</p>
          <div class="tools-row">
            <input id="wh-url" type="password" placeholder="https://discord.com/api/webhooks/... (https obligatoire)">
            <button onclick="toolsSaveWebhook()">Enregistrer</button>
          </div>
          <div id="wh-status" class="tools-result"></div>
          <p class="tools-hint" style="margin-top:12px"><strong>📱 Push mobile</strong> — installez l'app
            <a href="https://ntfy.sh" target="_blank" style="color:var(--accent)">ntfy</a>
            (iOS/Android, gratuite), abonnez-vous à un nom de sujet unique, puis saisissez-le ici.</p>
          <div class="tools-row">
            <input id="ntfy-topic" placeholder="ex : agence-ia-jm-8f3k (sujet secret)">
            <button onclick="toolsSaveNtfy()">Enregistrer</button>
          </div>
          <div id="ntfy-status" class="tools-result"></div>
          <div class="tools-row" style="margin-top:8px">
            <button onclick="toolsTestWebhook()">Tester les notifications</button>
          </div>
        </div>

        <div class="tools-section">
          <h3>🩺 Diagnostic</h3>
          <p class="tools-hint">Version, moteur IA, base de données et <strong>dernières
          erreurs du serveur avec leur trace complète</strong>. C'est ici qu'on retrouve la
          cause d'un bandeau rouge « Erreur interne du serveur » : la référence affichée
          dans le bandeau se retrouve dans la liste ci-dessous.</p>
          <div class="tools-row">
            <button onclick="toolsDiagnostic()">🔎 Afficher le diagnostic</button>
            <button onclick="toolsDiagnosticCopier()">📋 Copier</button>
            <button onclick="toolsDiagnosticPurger()">🧹 Vider les erreurs</button>
          </div>
          <div id="diag-result" class="tools-result lignes"></div>
        </div>

        <div class="tools-section">
          <h3>★ Offre &amp; abonnement</h3>
          <p class="tools-hint">Version en cours, agents débloqués et requêtes restantes.
          Les limites sont appliquées par le serveur de l'application : cet écran les
          affiche, il ne les fixe pas.</p>
          <div id="lic-tools-etat" class="tools-result lignes">Chargement…</div>
          <div class="tools-row">
            <button onclick="licencePasserPro()">★ Passer à Pro</button>
            <button onclick="licenceVoirOffre()">Voir les fonctionnalités Pro</button>
            <button onclick="licenceEcranEssai()">Version d'essai : ce qui est limité</button>
          </div>
          <p class="tools-hint" style="margin-top:12px">Disponible également sur le Microsoft Store</p>
          <div class="tools-row">
            <a class="lic-btn lic-btn-store"
               href="https://apps.microsoft.com/detail/9nltgfr2btsp?hl=fr-FR&amp;gl=FR"
               target="_blank" rel="noopener noreferrer">Télécharger sur le Microsoft Store</a>
          </div>
        </div>

        <div class="tools-section">
          <h3>🔒 Sécurité</h3>
          <div id="sec-avec-code" style="display:none">
            <div class="tools-row">
              <input id="pw-actuel" type="password" placeholder="Code actuel">
              <input id="pw-nouveau" type="password" placeholder="Nouveau code">
              <button onclick="toolsChangePw()">Changer</button>
            </div>
            <div id="pw-status" class="tools-result"></div>
            <button class="tools-logout" onclick="toolsLogout()">Se déconnecter</button>
          </div>
          <div id="sec-sans-code" style="display:none" class="tools-result lignes"></div>
        </div>
      </div>
    </div>`);
    document.body.appendChild(m);
    m.addEventListener('click', e => { if (e.target === m) m.style.display = 'none'; });
    const selMoteur = document.getElementById('ia-moteur');
    if (selMoteur) selMoteur.onchange = () => toolsIaRemplirModeles(selMoteur.value, '');
    toolsLoadSecurite();
    toolsLoadLicence();
    toolsLoadWebhook();
    toolsIaCharger();
    toolsMt5Check();
    toolsLoadComptes();
    toolsLoadTrading();
    toolsLoadRisk();
  }

  // ── Offre & abonnement ─────────────────────────────────────────
  async function toolsLoadLicence() {
    const el = document.getElementById('lic-tools-etat');
    if (!el) return;
    const d = await jf('/api/licence');
    if (!d) { el.textContent = 'État de l\'offre indisponible.'; return; }
    if (typeof window.licenceAppliquer === 'function') window.licenceAppliquer(d);
    const a = d.agents || {}, q = d.quotas || {};
    const lignes = [
      `Offre : ${d.libelle || d.etat}`,
      `Agents IA : ${a.autorises} / ${a.total}`,
      `Requêtes : ${q.illimite ? 'illimitées' : q.resume}`,
    ];
    if (!q.illimite && q.heure_limite != null) {
      lignes.push(`Cette heure-ci : ${q.heure_utilise} / ${q.heure_limite}`);
    }
    if (d.expire_le) {
      lignes.push(`Abonnement valable jusqu'au ${new Date(d.expire_le * 1000).toLocaleDateString('fr')}`);
    }
    el.innerHTML = lignes.map(l => esc(l)).join('<br>');
  }

  // ── Moteur de performance ──────────────────────────────────────
  // Correspondance champ ↔ réglage serveur. Une seule table : le chargement
  // et l'enregistrement ne peuvent plus diverger (un champ oublié à la
  // sauvegarde retombait silencieusement sur l'ancienne valeur).
  const TC_NOMBRES = {
    'tc-levier-max': 'levier_max',
    'tc-levier':     'levier',
    'tc-objectif':   'objectif_journalier',
    'tc-capital':    'capital_reference',
    'tc-risque':     'risque_par_trade_pct',
    'tc-volmax':     'volume_max_lot',
    'tc-confiance':  'confiance_min',
    'tc-cooldown':   'cooldown_s',
    'tc-sl':         'sl_pct',
    'tc-tp':         'tp_pct',
    'tc-spread':     'spread_max_pct',
    'tc-possym':     'max_positions_symbole',
  };
  const TC_CASES = {
    'tc-levier-auto':   'levier_auto',
    'tc-sizing':        'sizing_levier',
    'tc-sl-oblig':      'stop_loss_obligatoire',
    'tc-lot-min':       'autoriser_lot_minimum',
    'tc-exiger-risque': 'exiger_risque_calculable',
    'tc-anti-emp':      'anti_empilement',
    'tc-heures':        'respecter_heures_marche',
    'tc-simulees':      'bloquer_donnees_simulees',
    'tc-stop-obj':      'stop_sur_objectif',
  };

  // Les réglages ont-ils été RÉELLEMENT lus depuis le serveur ?
  // Sans ce drapeau, un formulaire dont le chargement avait échoué (session
  // expirée, serveur injoignable, ancienne version sans /api/trading/config)
  // présentait toutes ses cases DÉCOCHÉES — et le premier clic sur
  // « Enregistrer » désactivait d'un coup le stop-loss obligatoire, le blocage
  // des prix simulés et le respect des heures de marché, sans que personne ne
  // l'ait demandé. Un formulaire qui n'a rien chargé n'a rien à enregistrer.
  let TC_CHARGE = false;
  let RG_CHARGE = false;

  async function toolsLoadTrading() {
    const d = await jf('/api/trading/config');
    const cfg = d && d.config;
    if (!cfg) {
      const s = document.getElementById('tc-status');
      if (s) s.innerHTML = '⚠️ Réglages illisibles — enregistrement désactivé '
        + '(serveur injoignable ou version trop ancienne).';
      return;
    }
    TC_CHARGE = true;
    for (const [id, cle] of Object.entries(TC_NOMBRES)) {
      const el = document.getElementById(id);
      if (el && cfg[cle] !== undefined) el.value = cfg[cle];
    }
    for (const [id, cle] of Object.entries(TC_CASES)) {
      const el = document.getElementById(id);
      if (el) el.checked = !!cfg[cle];
    }
    const s = document.getElementById('tc-status');
    if (s) {
      s.textContent = `Levier appliqué : x${cfg.levier_actuel} (${cfg.levier_source})`
        + ` · plafond x${cfg.levier_max}`;
    }
  }
  window.toolsLoadTrading = toolsLoadTrading;

  window.toolsSaveTrading = async function () {
    const s0 = document.getElementById('tc-status');
    if (!TC_CHARGE) {
      // Nouvelle tentative de lecture : si elle réussit, l'utilisateur voit
      // enfin ses vraies valeurs et peut décider en connaissance de cause.
      await toolsLoadTrading();
      if (!TC_CHARGE) {
        if (s0) s0.innerHTML = '❌ Réglages jamais chargés : rien n\'a été '
          + 'enregistré (cela aurait désactivé toutes les protections).';
        return;
      }
      if (s0) s0.innerHTML = 'ℹ️ Réglages rechargés — vérifiez les valeurs puis '
        + 'enregistrez à nouveau.';
      return;
    }
    const corps = {};
    for (const [id, cle] of Object.entries(TC_NOMBRES)) {
      const el = document.getElementById(id);
      if (el && el.value !== '') corps[cle] = el.value;
    }
    for (const [id, cle] of Object.entries(TC_CASES)) {
      const el = document.getElementById(id);
      if (el) corps[cle] = el.checked;
    }
    const s = document.getElementById('tc-status');
    const d = await jf('/api/trading/config', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(corps),
    });
    if (!d || !d.success) {
      if (s) s.innerHTML = '❌ ' + esc((d && d.error) || 'Erreur d\'enregistrement');
      return;
    }
    // Relecture : les valeurs affichées sont celles que le serveur a RETENUES
    // (bornées), pas celles saisies — sinon l'écran mentirait sur un réglage
    // rabattu à son maximum.
    await toolsLoadTrading();
    if (s) s.innerHTML = '✅ Réglages appliqués · ' + esc(s.textContent || '');
  };

  // ── Garde-fous (RiskGuard) ─────────────────────────────────────
  async function toolsLoadRisk() {
    const d = await jf('/api/risk/status');
    if (!d || d.actif === undefined) {
      const s = document.getElementById('rg-status');
      if (s) s.innerHTML = '⚠️ Plafonds illisibles — enregistrement désactivé.';
      return;
    }
    RG_CHARGE = true;
    const set = (id, v) => { const el = document.getElementById(id); if (el) el.value = v; };
    set('rg-perte', d.perte_max_pct);
    set('rg-positions', d.max_positions);
    set('rg-expo', d.exposition_max_pct);
    const actif = document.getElementById('rg-actif');
    if (actif) actif.checked = !!d.actif;
    const s = document.getElementById('rg-status');
    if (s) {
      s.textContent = d.kill_switch
        ? `🛑 Kill-switch déclenché : ${d.raison || ''}`
        : `Perte du jour ${d.perte_jour_pct || 0}% / ${d.perte_max_pct}%`;
    }
  }
  window.toolsLoadRisk = toolsLoadRisk;

  window.toolsSaveRisk = async function () {
    if (!RG_CHARGE) {
      await toolsLoadRisk();
      const s0 = document.getElementById('rg-status');
      if (!RG_CHARGE) {
        // Même règle que les réglages de performance : ne jamais écrire un
        // « garde-fous décochés » que l'utilisateur n'a pas choisi.
        if (s0) s0.innerHTML = '❌ Plafonds jamais chargés : rien n\'a été '
          + 'enregistré (cela aurait désactivé les garde-fous).';
        return;
      }
      if (s0) s0.innerHTML = 'ℹ️ Plafonds rechargés — vérifiez puis enregistrez.';
      return;
    }
    const num = id => document.getElementById(id)?.value;
    const d = await jf('/api/risk/config', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        actif: !!document.getElementById('rg-actif')?.checked,
        perte_max_pct: num('rg-perte'),
        max_positions: num('rg-positions'),
        exposition_max_pct: num('rg-expo'),
      }),
    });
    const s = document.getElementById('rg-status');
    if (!d || !d.success) {
      if (s) s.innerHTML = '❌ ' + esc((d && d.error) || 'Erreur');
      return;
    }
    await toolsLoadRisk();
    if (s) s.innerHTML = '✅ Plafonds enregistrés · ' + esc(s.textContent || '');
  };

  window.toolsResetKill = async function () {
    await jf('/api/risk/reset', { method: 'POST' });
    await toolsLoadRisk();
  };

  // ── Diagnostic ─────────────────────────────────────────────────
  let _diagBrut = '';

  function _diagTexte(d) {
    const L = [];
    L.push(`Version ${d.version} · Python ${d.python} · ${d.systeme}`);
    L.push(`Exe compilé : ${d.compile ? 'oui' : 'non'} · Racine : ${d.racine}`);
    if (d.ia && !d.ia.erreur) {
      L.push(`Moteur IA : ${d.ia.libelle || d.ia.moteur}`);
      (d.ia.moteurs || []).forEach(m => {
        L.push(`  - ${m.libelle} : ${m.serveur_actif ? 'actif' : m.installe ? 'prêt (arrêté)' : 'absent'}`
               + (m.embarque ? ' (embarqué)' : ''));
      });
      const r = d.ia.runtime_embarque || {};
      L.push(`  Runtime embarqué : ${r.actif ? r.dossier : 'aucun'}`);
    } else if (d.ia) { L.push(`Moteur IA : ${d.ia.erreur}`); }
    if (d.agents) L.push(`Agents chargés : ${d.agents.charges != null ? d.agents.charges : d.agents.erreur}`);
    if (d.base) L.push(`Base : ${d.base.chemin || d.base.erreur} (${d.base.taille_ko || 0} Ko)`);
    if (d.mt5) L.push(`MetaTrader : ${JSON.stringify(d.mt5)}`);
    L.push('');
    const err = d.erreurs || [];
    L.push(err.length ? `${err.length} erreur(s) récente(s) — la plus récente en dernier :`
                      : 'Aucune erreur enregistrée depuis le démarrage.');
    err.forEach(e => {
      L.push('');
      L.push(`[${e.horodatage}] ${e.source}`);
      L.push(e.message);
      if (e.trace) L.push(e.trace.trim());
    });
    return L.join('\n');
  }

  window.toolsDiagnostic = async function () {
    const el = document.getElementById('diag-result');
    el.textContent = 'Collecte…';
    const d = await jf('/api/diagnostic');
    if (!d) { el.textContent = '❌ Diagnostic indisponible'; return; }
    _diagBrut = _diagTexte(d);
    // textContent : une trace peut contenir des chevrons (<module>, <stdin>)
    // qu'un innerHTML avalerait — c'est justement la ligne la plus utile.
    el.textContent = _diagBrut;
  };

  window.toolsDiagnosticCopier = async function () {
    const el = document.getElementById('diag-result');
    if (!_diagBrut) await toolsDiagnostic();
    try {
      await navigator.clipboard.writeText(_diagBrut);
      const p = document.createElement('p');
      p.className = 'tools-hint'; p.textContent = '✅ Diagnostic copié.';
      el.parentNode.appendChild(p);
      setTimeout(() => p.remove(), 3000);
    } catch (e) {
      // Presse-papiers refusé (page non sécurisée, permission) : le texte
      // reste sélectionnable à l'écran, on le dit plutôt que d'échouer en
      // silence.
      el.textContent = _diagBrut + '\n\n(Copie automatique refusée par le '
        + 'navigateur — sélectionnez le texte ci-dessus.)';
    }
  };

  window.toolsDiagnosticPurger = async function () {
    await jf('/api/diagnostic/purger', { method: 'POST' });
    await toolsDiagnostic();
  };

  // ── Moteur IA (Ollama / Hermès / Cloud) ────────────────────────
  // Modèles proposés par moteur. Le serveur reste l'autorité pour Hermès
  // (liste renvoyée par /api/hermes/status) ; cette table ne sert qu'à
  // afficher quelque chose de sensé avant sa réponse.
  const IA_MODELES = {
    ollama: [['llama3.2', 'llama3.2 (rapide)'], ['llama3.1:8b', 'llama3.1:8b (qualité)'],
             ['mistral', 'mistral:7b (français)'], ['phi4-mini', 'phi4-mini (léger)'],
             ['gemma2:9b', 'gemma2:9b'], ['deepseek-r1:7b', 'deepseek-r1:7b (raisonnement)']],
    hermes: [['hermes-3-llama-3.2-3b', 'Hermes 3 — 3B (rapide)'],
             ['hermes-3-llama-3.1-8b', 'Hermes 3 — 8B (qualité)']],
  };

  function toolsIaRemplirModeles(moteur, courant) {
    const sel = document.getElementById('ia-modele');
    if (!sel) return;
    const liste = IA_MODELES[moteur] || [];
    sel.innerHTML = '';
    // Un moteur sans modèle à choisir masque le sélecteur, plutôt que
    // d'afficher une liste vide qui laisserait croire à un réglage perdu.
    sel.style.display = liste.length ? '' : 'none';
    liste.forEach(([v, l]) => {
      const o = document.createElement('option');
      o.value = v; o.textContent = l;
      if (v === courant) o.selected = true;
      sel.appendChild(o);
    });
  }

  window.toolsIaCharger = async function () {
    const el = document.getElementById('ia-status');
    if (!el) return;
    const d = await jf('/api/ia/statut');
    if (!d) { el.textContent = '—'; return; }
    const sel = document.getElementById('ia-moteur');
    if (sel) sel.value = d.moteur;
    const actif = (d.moteurs || []).find(m => m.cle === d.moteur) || {};
    toolsIaRemplirModeles(d.moteur, actif.modele);

    const lignes = (d.moteurs || []).map(m => {
      const etat = m.serveur_actif ? '✅ actif'
                 : m.installe ? '⏸️ prêt (arrêté)'
                 : '❌ absent';
      const livre = m.embarque ? ' — livré avec l\'application' : '';
      const marque = m.cle === d.moteur ? ' ← sélectionné' : '';
      return `${m.libelle} : ${etat}${livre}${marque}`;
    });
    el.textContent = lignes.join('\n');
  };

  window.toolsIaAppliquer = async function () {
    const el = document.getElementById('ia-status');
    const moteur = document.getElementById('ia-moteur').value;
    const selModele = document.getElementById('ia-modele');
    const modele = (selModele && selModele.style.display !== 'none') ? selModele.value : '';
    el.textContent = 'Application du choix (le démarrage d\'un moteur local peut prendre 1 à 2 min)…';
    const d = await jf('/api/ia/moteur', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ moteur, modele }),
    });
    if (!d) { el.textContent = '❌ Erreur réseau'; return; }
    if (!d.success) { el.textContent = '❌ ' + (d.error || 'changement impossible'); return; }
    // Le serveur démarre le moteur en arrière-plan : on relit l'état un peu
    // plus tard pour ne pas afficher « arrêté » sur un moteur qui charge.
    setTimeout(toolsIaCharger, 4000);
    el.textContent = '✅ Moteur ' + moteur + ' sélectionné — démarrage en cours…';
  };

  window.toolsMt5Check = async function () {
    const el = document.getElementById('mt5inst-status');
    if (!el) return;
    const d = await jf('/api/mt5/install/status');
    if (!d) { el.textContent = '—'; return; }
    if (d.portable && d.courtier && d.courtier !== 'avatrade') {
      // Terminal livré mais PAS celui du courtier : il démarre, puis refuse
      // la connexion au compte. Le dire ici évite de chercher du côté du
      // mot de passe.
      el.innerHTML = '⚠️ Le terminal MetaTrader livré n\'est pas celui d\'AvaTrade '
        + '(serveurs connus : ' + esc((d.serveurs || []).join(', ') || 'aucun') + '). '
        + 'Il ne pourra pas se connecter à votre compte tant que vous n\'aurez pas '
        + 'ajouté le courtier : MetaTrader → Fichier → Ouvrir un compte → « AvaTrade ».';
    } else if (d.portable && d.pret) {
      el.innerHTML = '✅ MetaTrader 5 <strong>AvaTrade livré avec l\'application</strong> '
        + 'et prêt — connectez votre compte via « Connexion AvaTrade » (ordres RÉELS).';
    } else if (d.portable) {
      el.innerHTML = '✅ MetaTrader 5 livré avec l\'application. S\'il ne répond pas, '
        + 'cliquez « Démarrer / réparer la connexion ».';
    } else if (!d.os_windows) {
      el.innerHTML = 'ℹ️ MetaTrader 5 ne s\'installe que sur Windows. Sur macOS/Linux, installez-le via Wine, ou utilisez cette application depuis un PC Windows.';
    } else if (d.pret) {
      el.innerHTML = '✅ MetaTrader 5 prêt — connectez votre compte via « Connexion AvaTrade » (ordres RÉELS).';
    } else if (d.terminal && !d.lib) {
      el.innerHTML = '⚠️ Terminal installé mais l\'exe n\'inclut pas la librairie — recompilez avec build_exe.bat.';
    } else if (d.embarque) {
      el.innerHTML = '⚠️ MetaTrader 5 pas encore installé — l\'installeur est embarqué, '
        + 'cliquez « Installer MetaTrader 5 » (aucun téléchargement).';
    } else {
      el.innerHTML = '⚠️ MetaTrader 5 non installé — cliquez « Installer MetaTrader 5 ».';
    }
  };

  function _mt5LienManuel(afficher) {
    const det = document.getElementById('mt5inst-detail');
    let bloc = document.getElementById('mt5-lien-manuel');
    if (!afficher) { if (bloc) bloc.remove(); return; }
    if (bloc) return;
    bloc = document.createElement('p');
    bloc.id = 'mt5-lien-manuel';
    bloc.className = 'tools-hint';
    bloc.style.marginTop = '8px';
    bloc.innerHTML =
      'Téléchargez MetaTrader 5 vous-même depuis '
      + '<a href="https://www.avatrade.fr/trading-platforms/metatrader-5" target="_blank" '
      + 'rel="noopener noreferrer" style="color:var(--accent)">avatrade.fr</a> '
      + '(ou <a href="https://www.metatrader5.com/fr/download" target="_blank" '
      + 'rel="noopener noreferrer" style="color:var(--accent)">metatrader5.com</a>), '
      + 'installez-le, puis cliquez « 🔄 Vérifier ». '
      + 'L\'application le détectera automatiquement.';
    det.parentNode.appendChild(bloc);
  }

  window.toolsMt5Fermer = async function () {
    const el = document.getElementById('mt5inst-status');
    el.textContent = 'Fermeture des terminaux MetaTrader…';
    const d = await jf('/api/mt5/terminal/fermer', { method: 'POST' });
    if (!d) { el.textContent = '❌ Erreur réseau'; return; }
    el.textContent = d.success
      ? `✅ ${d.fermes || 0} terminal(aux) fermé(s) — cliquez « Démarrer / réparer ».`
      : '⚠️ ' + (d.error || 'fermeture incomplète');
  };

  window.toolsMt5Demarrer = async function () {
    const el = document.getElementById('mt5inst-status');
    el.textContent = 'Démarrage du terminal MetaTrader 5 (jusqu\'à 1 min au premier lancement)…';
    const d = await jf('/api/mt5/terminal/demarrer', { method: 'POST' });
    if (!d) { el.textContent = '❌ Erreur réseau'; return; }
    if (d.success) {
      el.textContent = d.deja_actif ? '✅ Terminal déjà actif' : '✅ Terminal démarré';
      setTimeout(toolsMt5Check, 1500);
    } else {
      // Plusieurs terminaux ouverts : le message dit quoi faire, et le bouton
      // « Fermer tous les terminaux » juste à côté le fait en un clic.
      el.textContent = '❌ ' + (d.error || 'démarrage impossible');
    }
  };

  window.toolsMt5Install = async function () {
    const prog = document.getElementById('mt5inst-progress');
    const bar = document.getElementById('mt5inst-bar');
    const det = document.getElementById('mt5inst-detail');
    prog.style.display = 'block';
    det.textContent = 'Démarrage...';
    await jf('/api/mt5/install', { method: 'POST' });
    const timer = setInterval(async () => {
      const s = await jf('/api/mt5/install/status');
      if (!s) return;
      bar.style.width = (s.progression || 0) + '%';
      det.textContent = (s.detail || s.etape) + (s.progression ? ' — ' + Math.round(s.progression) + '%' : '');
      if (s.pret || s.etape === 'pret') {
        clearInterval(timer);
        det.textContent = '✅ Installé !';
        _mt5LienManuel(false);
        toolsMt5Check();
      } else if (s.etape === 'erreur') {
        clearInterval(timer);
        // textContent : le message vient du serveur et peut contenir des
        // chevrons (détail technique SSL) qu'un innerHTML avalerait.
        det.textContent = '❌ ' + (s.erreur || 'échec');
        // Le téléchargement automatique peut être bloqué sans que ce soit
        // réparable depuis l'application (antivirus qui inspecte le HTTPS,
        // réseau d'entreprise). On propose alors la voie manuelle au lieu de
        // laisser l'utilisateur sans issue.
        _mt5LienManuel(true);
      }
    }, 1500);
  };
  window.ouvrirReglages = ouvrirReglages;

  async function jf(url, opts) {
    try { const r = await fetch(url, opts); if (r.status === 401 && window._authRedirect) window._authRedirect(); return await r.json(); }
    catch { return null; }
  }

  window.toolsPreflight = async function () {
    const el = document.getElementById('pf-result');
    el.textContent = 'Vérification en cours (interrogation du courtier)…';
    const brut = document.getElementById('pf-symboles').value.trim();
    const symboles = brut ? brut.split(/[,;\s]+/).filter(Boolean) : null;
    const d = await jf('/api/preflight', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(symboles ? { symboles } : {}),
    });
    if (!d) { el.textContent = '❌ Erreur pendant la vérification'; return; }

    const ico = s => s === 'ok' ? '✅' : s === 'attention' ? '⚠️' : '❌';
    const bandeau = d.verdict === 'pret'
      ? '<div style="color:var(--green);font-weight:600">✅ ' + esc(d.resume) + '</div>'
      : d.verdict === 'avertissement'
        ? '<div style="color:var(--yellow);font-weight:600">⚠️ ' + esc(d.resume) + '</div>'
        : '<div style="color:var(--red);font-weight:600">❌ ' + esc(d.resume) + '</div>';

    const lignes = (d.checks || []).map(c =>
      `<div style="margin:5px 0">${ico(c.statut)} <b>${esc(c.titre)}</b>`
      + (c.detail ? `<div style="font-size:11.5px;color:var(--text-muted);margin-left:20px">${esc(c.detail)}</div>` : '')
      + (c.conseil ? `<div style="font-size:11.5px;color:var(--accent);margin-left:20px">→ ${esc(c.conseil)}</div>` : '')
      + `</div>`).join('');

    const syms = (d.symboles || []).map(s => {
      const det = [];
      if (s.nom_courtier) det.push('courtier : <b>' + esc(s.nom_courtier) + '</b>');
      if (s.prix) det.push('prix ' + esc(String(s.prix)));
      if (s.volume) {
        // Volume refusé : on affiche le lot MINIMUM (seul réellement tentable)
        // et on le signale, plutôt qu'un volume théorique non envoyable.
        det.push('volume <b>' + esc(String(s.volume)) + '</b> lot'
          + (s.volume_refuse ? ' <span style="color:var(--red)">(minimum courtier — ordre refusé)</span>' : ''));
      }
      if (s.stop_loss) det.push('SL ' + esc(String(s.stop_loss)));
      if (s.perte_au_stop != null) {
        det.push('perte au stop <b>' + esc(String(s.perte_au_stop))
          + (s.perte_au_stop_pct != null ? ' (' + esc(String(s.perte_au_stop_pct)) + '%)' : '') + '</b>');
      }
      if (s.spread_pct != null) det.push('spread ' + esc(String(s.spread_pct)) + '%');
      return `<div style="margin:7px 0;padding:7px 9px;background:var(--surface2);border-radius:6px">
          ${ico(s.statut)} <b>${esc(s.symbole)}</b>
          <div style="font-size:11.5px;color:var(--text-muted);margin-top:3px">${det.join(' · ')}</div>
          ${(s.messages || []).map(m => `<div style="font-size:11.5px;margin-top:3px">• ${esc(m)}</div>`).join('')}
        </div>`;
    }).join('');

    el.innerHTML = bandeau
      + '<div style="margin-top:10px">' + lignes + '</div>'
      + (syms ? '<div style="margin-top:8px;font-size:11px;color:var(--text-muted);text-transform:uppercase;letter-spacing:.06em">Par symbole</div>' + syms : '')
      + '<p class="tools-hint" style="margin-top:8px">Aucun ordre n\'a été envoyé pendant cette vérification.</p>';
  };

  window.toolsBacktest = async function () {
    const el = document.getElementById('bt-result');
    el.textContent = 'Calcul en cours...';
    const d = await jf('/api/backtest', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        symbole: document.getElementById('bt-symbole').value.trim(),
        timeframe: document.getElementById('bt-tf').value, capital: 10000,
      }),
    });
    if (!d || !d.success) { el.textContent = '❌ ' + ((d && d.error) || 'Erreur'); return; }
    const c = d.rendement_pct >= 0 ? 'pos' : 'neg';
    const vs = d.rendement_pct >= d.buy_hold_pct ? '✅ bat' : '⚠️ sous';
    el.innerHTML =
      `<div class="bt-grid">
        <div><b class="${c}">${d.rendement_pct >= 0 ? '+' : ''}${d.rendement_pct}%</b><span>Stratégie</span></div>
        <div><b>${d.buy_hold_pct >= 0 ? '+' : ''}${d.buy_hold_pct}%</b><span>Buy &amp; Hold (${vs})</span></div>
        <div><b>${d.nb_trades}</b><span>Trades</span></div>
        <div><b>${d.taux_reussite_pct}%</b><span>Réussite</span></div>
        <div><b class="neg">${d.max_drawdown_pct}%</b><span>Drawdown max</span></div>
      </div>
      <p class="tools-warn">${esc(d.avertissement || '')}</p>`;
  };

  window.toolsPnlReel = async function () {
    const el = document.getElementById('pnl-result');
    el.textContent = 'Chargement...';
    const jours = document.getElementById('pnl-jours').value;
    const d = await jf('/api/performance/reel?jours=' + jours);
    if (!d) { el.textContent = '❌ Erreur'; return; }
    if (!d.synced) { el.textContent = 'ℹ️ ' + (d.raison || 'Compte MT5 non connecté'); return; }
    const c = d.pnl_total >= 0 ? 'pos' : 'neg';
    // `devise` vient du compte chez le COURTIER : échappée comme tout texte
    // d'origine externe inséré en innerHTML.
    el.innerHTML =
      `<div class="bt-grid">
        <div><b class="${c}">${d.pnl_total >= 0 ? '+' : ''}${Number(d.pnl_total) || 0} ${esc(d.devise || '')}</b><span>P&amp;L ${Number(d.jours) || 0}j</span></div>
        <div><b>${d.nb_trades}</b><span>Trades fermés</span></div>
        <div><b class="pos">${d.gagnants}</b><span>Gagnants</span></div>
        <div><b class="neg">${d.perdants}</b><span>Perdants</span></div>
        <div><b>${d.taux_reussite_pct}%</b><span>Réussite</span></div>
      </div>`;
  };

  window.toolsRapportTest = async function () {
    const el = document.getElementById('pnl-result');
    el.textContent = 'Génération...';
    const d = await jf('/api/rapport/test', { method: 'POST' });
    if (!d || !d.success) { el.textContent = '❌ Erreur'; return; }
    const envoi = d.envoye ? '✅ Envoyé par notification' : ('ℹ️ ' + (d.info || ''));
    el.innerHTML = '<pre style="white-space:pre-wrap;font-size:12px;color:#9aadcc;margin-top:6px">'
      + d.rapport.replace(/&/g, '&amp;').replace(/</g, '&lt;') + '</pre>' + envoi;
  };

  async function toolsLoadWebhook() {
    const d = await jf('/api/notifications/config');
    if (!d) return;
    const s = document.getElementById('wh-status');
    if (s && d.configure) s.innerHTML = `✅ Webhook configuré (${esc(d.apercu)})`;
    // Le sujet ntfy est un secret : le serveur n'en renvoie qu'un aperçu
    // masqué (on ne pré-remplit donc pas le champ de saisie).
    const n = document.getElementById('ntfy-status');
    if (n && d.ntfy_configure) {
      n.innerHTML = '✅ Push mobile actif · sujet ' + esc(d.ntfy_apercu || '••••');
    }
  }
  window.toolsSaveWebhook = async function () {
    const url = document.getElementById('wh-url').value.trim();
    const d = await jf('/api/notifications/config', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ webhook_url: url }),
    });
    document.getElementById('wh-status').innerHTML = (d && d.success)
      ? '✅ Enregistré'
      : '❌ URL invalide — une adresse https:// complète est requise';
  };
  window.toolsSaveNtfy = async function () {
    const topic = document.getElementById('ntfy-topic').value.trim();
    const d = await jf('/api/notifications/config', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ ntfy_topic: topic }),
    });
    document.getElementById('ntfy-status').innerHTML = (d && d.success)
      ? (topic ? '✅ Push mobile enregistré · abonnez-vous à « ' + esc(topic) + ' » dans l\'app ntfy' : '✅ Push mobile désactivé')
      : '❌ Sujet invalide — utilisez des lettres/chiffres/tirets, ou une URL https:// complète';
  };
  window.toolsTestWebhook = async function () {
    const d = await jf('/api/notifications/test', { method: 'POST' });
    const msg = (d && d.success)
      ? '✅ Test envoyé (' + ((d.canaux || []).join(', ') || 'aucun canal') + ')'
      : '❌ ' + ((d && d.error) || 'Échec');
    document.getElementById('wh-status').innerHTML = msg;
  };

  window.toolsExportCsv = function () {
    // navigation directe : le cookie d'auth same-origin accompagne la requête
    window.location.href = '/api/trades/export.csv';
  };

  // ── Multi-comptes ──────────────────────────────────────────────
  async function toolsLoadComptes() {
    const box = document.getElementById('comptes-list');
    if (!box) return;
    const d = await jf('/api/comptes');
    const comptes = (d && d.comptes) || [];
    if (!comptes.length) {
      box.innerHTML = '<p class="comptes-empty">Aucun compte mémorisé. Connectez-vous à AvaTrade '
        + ' — le compte apparaîtra ici.</p>';
      return;
    }
    // L'identifiant de compte voyage par `data-id` et le clic passe par un
    // écouteur, JAMAIS par onclick="handler('...')". Dans un attribut onclick,
    // esc() est le MAUVAIS échappement : le navigateur décode l'entité HTML
    // (&#39; → ') AVANT d'analyser le JavaScript, donc une apostrophe refermait
    // la chaîne et le reste s'exécutait comme du code. Même règle que
    // renderChips() dans app.js.
    box.innerHTML = comptes.map(c => `
      <div class="compte-item ${c.actif ? 'actif' : ''}">
        <div class="compte-main">
          <div class="compte-label">${esc(c.label)}</div>
          <div class="compte-sub">${esc(c.apercu)}</div>
        </div>
        ${c.actif ? '<span class="compte-badge">actif</span>'
          : `<div class="compte-actions">
               <button class="compte-btn" data-action="basculer">Basculer</button>
               <button class="compte-btn danger" data-action="supprimer">✕</button>
             </div>`}
      </div>`).join('');
    // Les identifiants sont réassociés APRÈS le rendu (jamais interpolés dans
    // le HTML) : dataset stocke la valeur brute, sans passer par l'analyseur.
    box.querySelectorAll('.compte-item').forEach((item, i) => {
      const compte = comptes[i];
      if (!compte) return;
      item.querySelectorAll('[data-action]').forEach(btn => {
        btn.dataset.id = compte.id ?? '';
        btn.addEventListener('click', () => (
          btn.dataset.action === 'basculer'
            ? window.toolsBasculerCompte(btn.dataset.id)
            : window.toolsSupprimerCompte(btn.dataset.id)));
      });
    });
  }
  window.toolsBasculerCompte = async function (id) {
    const box = document.getElementById('comptes-list');
    if (box) box.style.opacity = '0.5';
    const d = await jf('/api/comptes/basculer', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ id }),
    });
    if (box) box.style.opacity = '1';
    if (!d || !d.success) { alert('Bascule impossible : ' + ((d && d.error) || 'erreur')); }
    await toolsLoadComptes();
    if (window.mt5ChargerStatut) window.mt5ChargerStatut();
  };
  window.toolsSupprimerCompte = async function (id) {
    if (!confirm('Retirer ce compte de la liste ? (les identifiants mémorisés seront effacés)')) return;
    await jf('/api/comptes/supprimer', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ id }),
    });
    await toolsLoadComptes();
  };

  // L'application ne demande aucun code d'accès par défaut. Dans ce cas il n'y
  // a ni code à changer, ni session à fermer : afficher ces commandes
  // donnerait des boutons qui ne peuvent que répondre par une erreur. On
  // explique plutôt l'état réel, et comment rétablir la protection.
  async function toolsLoadSecurite() {
    const avec = document.getElementById('sec-avec-code');
    const sans = document.getElementById('sec-sans-code');
    if (!avec || !sans) return;
    let actif = false;
    try {
      const d = await jf('/api/status');
      actif = !!(d && d.auth);
    } catch (e) { actif = false; }   // serveur muet : on n'invente pas un code
    avec.style.display = actif ? '' : 'none';
    sans.style.display = actif ? 'none' : '';
    if (!actif) {
      sans.innerHTML =
        "Aucun code d'accès n'est demandé au lancement." +
        "<br>L'application écoute aussi sur le Wi-Fi, pour l'accès depuis un " +
        "téléphone : sur un réseau partagé, tout appareil du réseau peut donc " +
        "l'ouvrir." +
        "<br><br>Pour rétablir un code, démarrez l'application avec " +
        "<code>APP_AUTH=on</code>." +
        "<br>Pour ne plus écouter que sur cet ordinateur, utilisez " +
        "<code>BACKEND_HOST=127.0.0.1</code> (l'accès téléphone cesse alors).";
    }
  }

  window.toolsChangePw = async function () {
    const actuel = document.getElementById('pw-actuel').value;
    const nouveau = document.getElementById('pw-nouveau').value;
    const d = await jf('/api/password', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ actuel, nouveau }),
    });
    const s = document.getElementById('pw-status');
    if (d && d.success) { s.innerHTML = '✅ Code modifié'; document.getElementById('pw-actuel').value = ''; document.getElementById('pw-nouveau').value = ''; }
    else s.innerHTML = '❌ ' + esc((d && d.error) || 'Erreur');
  };

  window.toolsLogout = async function () {
    await jf('/api/logout', { method: 'POST' });
    location.href = '/login';
  };
})();
