// ═══════════════ Offre : version d'essai / version Pro ═══════════════
// Badge d'offre, compteur de requêtes, cadenas sur les agents verrouillés,
// écran de présentation de l'essai, comparatif Pro et lien Microsoft Store.
//
// CE FICHIER NE DÉCIDE DE RIEN. Toutes les limites sont appliquées par le
// serveur (python/licence/gate.py) ; l'interface ne fait qu'AFFICHER ce qu'il
// répond. Modifier ce fichier — ou l'état du navigateur — ne débloque aucune
// fonctionnalité : chaque appel gardé repart en 402 / 429.

(function () {
  const STORE_URL = 'https://apps.microsoft.com/detail/9nltgfr2btsp?hl=fr-FR&gl=FR';

  // Dernier état reçu du serveur. `null` tant que rien n'a été chargé — on
  // n'affiche alors AUCUN badge, plutôt qu'un « essai » qui se révélerait faux
  // sous les yeux d'un abonné.
  let etat = null;

  function E(s) {
    // `esc` vient de app.js ; ce repli garde licence.js utilisable seul.
    return (typeof esc === 'function') ? esc(s) : String(s == null ? '' : s)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  }

  function h(html) { const d = document.createElement('div'); d.innerHTML = html; return d.firstElementChild; }

  function estPro() { return !!(etat && etat.est_pro); }
  window.licenceEstPro = estPro;
  window.licenceEtat = () => etat;

  // ── Chargement / rafraîchissement ────────────────────────────────
  async function charger(forcer) {
    const data = await fetchJSON(forcer ? '/api/licence/rafraichir' : '/api/licence',
                                 forcer ? { method: 'POST' } : {});
    if (data) appliquer(data);
    return data;
  }
  window.licenceCharger = charger;

  // Appelé aussi par chargerStatus() : /api/status transporte déjà l'offre,
  // inutile d'ajouter une requête par battement de cœur.
  function appliquer(data) {
    if (!data || !data.etat) return;
    etat = data;
    peindreBadge();
    peindreQuota();
    peindreLancement();
    presenterUneFois();
    if (typeof window.licenceRafraichirAgents === 'function') window.licenceRafraichirAgents();
  }
  window.licenceAppliquer = appliquer;

  // ── Badge d'offre + compteur de requêtes (en-tête) ───────────────
  function peindreBadge() {
    const el = document.getElementById('licence-chip');
    if (!el || !etat) return;
    const pro = estPro();
    el.className = 'stat-chip licence-chip ' + (pro ? 'is-pro' : 'is-trial');
    el.textContent = pro ? '★ PRO' : "VERSION D'ESSAI";
    el.title = etat.message || '';
    el.onclick = () => pro ? ouvrirOffre() : ouvrirEssai();
  }

  function peindreQuota() {
    const el = document.getElementById('licence-quota');
    if (!el || !etat) return;
    const q = etat.quotas || {};
    if (q.illimite) { el.style.display = 'none'; return; }
    el.style.display = '';
    el.textContent = q.resume || '';
    // Seuil d'alerte : à 3 requêtes près de la limite, l'utilisateur doit le
    // voir AVANT de lancer l'analyse qui sera refusée.
    const reste = q.jour_restant;
    el.classList.toggle('quota-bas', reste != null && reste <= 3);
    el.title = (q.heure_limite != null)
      ? `${q.heure_utilise} / ${q.heure_limite} requêtes cette heure-ci`
      : '';
  }

  // ── Écran de lancement : offre en cours + accès Microsoft Store ──
  function peindreLancement() {
    const el = document.getElementById('launch-licence');
    if (!el || !etat) return;
    el.style.display = '';
    if (estPro()) {
      el.innerHTML = `<div class="launch-lic-titre">★ Version Pro — accès complet</div>
        <div>Les ${E(String((etat.agents || {}).total || ''))} agents IA et toutes les
        fonctionnalités avancées sont actifs.</div>
        <div>Disponible également sur le Microsoft Store</div>
        <a class="lic-btn lic-btn-store" href="${STORE_URL}" target="_blank" rel="noopener noreferrer">
          Télécharger sur le Microsoft Store</a>`;
      return;
    }
    const lim = etat.limites_essai || {};
    el.innerHTML = `
      <div class="launch-lic-titre">Version d'essai — Fonctionnalités limitées</div>
      <div>${E(String(lim.max_agents))} agents IA sur ${E(String((etat.agents || {}).total || ''))}
        · ${E(String(lim.requetes_jour))} requêtes par jour · fonctionnalités avancées réservées à Pro</div>
      <div><a href="#" id="launch-lic-detail">En savoir plus sur la version d'essai</a></div>
      <div>Disponible également sur le Microsoft Store</div>
      <a class="lic-btn lic-btn-store" href="${STORE_URL}" target="_blank" rel="noopener noreferrer">
        Télécharger sur le Microsoft Store</a>`;
    const lien = document.getElementById('launch-lic-detail');
    if (lien) lien.addEventListener('click', e => { e.preventDefault(); ouvrirEssai(); });
  }

  // ── Bandeau/bouton Microsoft Store (réutilisé partout) ───────────
  function blocStore() {
    return `<div class="lic-store">
      <div class="lic-store-txt">Disponible également sur le Microsoft Store</div>
      <a class="lic-btn lic-btn-store" href="${STORE_URL}" target="_blank" rel="noopener noreferrer">
        Télécharger sur le Microsoft Store
      </a>
    </div>`;
  }
  window.licenceBlocStore = blocStore;

  // ── Modale générique ─────────────────────────────────────────────
  function ouvrirModale(id, titre, corps) {
    let m = document.getElementById(id);
    if (m) m.remove();
    m = h(`<div class="lic-modal" id="${E(id)}">
      <div class="lic-box" role="dialog" aria-modal="true" aria-label="${E(titre)}">
        <div class="lic-head">
          <span>${titre}</span>
          <button class="lic-close" aria-label="Fermer">✕</button>
        </div>
        <div class="lic-body">${corps}</div>
      </div>
    </div>`);
    document.body.appendChild(m);
    m.querySelector('.lic-close').onclick = () => m.remove();
    m.addEventListener('click', e => { if (e.target === m) m.remove(); });
    document.addEventListener('keydown', function fermer(e) {
      if (e.key === 'Escape') { m.remove(); document.removeEventListener('keydown', fermer); }
    });
    return m;
  }

  // ── « Cette fonctionnalité est disponible dans la version Pro. » ──
  // Appelée par fetchJSON dès qu'une route gardée répond 402 ou 429 : un seul
  // message, quelle que soit la fonctionnalité refusée.
  function verrou(payload) {
    payload = payload || {};
    const quota = !!payload.quota_depasse;
    const titre = quota ? '⏳ Limite de la version d\'essai' : '🔒 Fonctionnalité Pro';
    const intro = quota
      ? E(payload.error || 'Limite de la version d\'essai atteinte.')
      : 'Cette fonctionnalité est disponible dans la version Pro.';
    const quoi = payload.feature_libelle
      ? `<div class="lic-feature">${E(payload.feature_libelle)}</div>` : '';
    const detail = payload.detail ? `<p class="lic-detail">${E(payload.detail)}</p>` : '';
    const attente = quota
      ? `<p class="lic-detail">Les compteurs se libèrent d'eux-mêmes : la limite
         horaire au bout d'une heure, la limite journalière au bout de 24 h.</p>` : '';

    ouvrirModale('lic-verrou', titre, `
      <p class="lic-intro">${intro}</p>
      ${quoi}${detail}${attente}
      <div class="lic-actions">
        <button class="lic-btn lic-btn-pro" onclick="licencePasserPro()">Passer à Pro</button>
        <button class="lic-btn lic-btn-ghost" onclick="licenceVoirOffre()">Voir les fonctionnalités Pro</button>
      </div>
      ${blocStore()}`);
  }
  window.licenceVerrou = verrou;

  // ── Comparatif Essai / Pro ───────────────────────────────────────
  async function ouvrirOffre() {
    const d = await fetchJSON('/api/licence/offre');
    if (!d) return;
    if (d.etat) appliquer(d.etat);

    const lib = d.features_libelles || {};
    const actives = (etat && etat.features) || {};
    const lignes = (d.pro.features || []).map(f => `
      <tr>
        <td>${E(lib[f] || f)}</td>
        <td class="lic-no">${actives[f] ? '✅' : '❌'}</td>
        <td class="lic-yes">✅</td>
      </tr>`).join('');

    const nb = (v, total) => (v == null ? 'Illimité' : v) + (total ? ` / ${total}` : '');
    ouvrirModale('lic-offre', '🚀 Version Pro', `
      <p class="lic-intro">La version Pro débloque l'intégralité des fonctionnalités
      et capacités prévues pour l'application, notamment l'exploitation complète
      des ${E(d.pro.agents_total)} agents IA et les fonctionnalités avancées.</p>
      <table class="lic-table">
        <thead><tr><th>Fonctionnalité</th><th>Essai</th><th>Pro</th></tr></thead>
        <tbody>
          <tr><td>Agents IA</td>
              <td class="lic-no">${E(nb(d.essai.agents, d.essai.agents_total))}</td>
              <td class="lic-yes">${E(nb(d.pro.agents, d.pro.agents_total))}</td></tr>
          <tr><td>Requêtes par jour</td>
              <td class="lic-no">${E(nb(d.essai.requetes_jour))}</td>
              <td class="lic-yes">${E(nb(d.pro.requetes_jour))}</td></tr>
          <tr><td>Requêtes par heure</td>
              <td class="lic-no">${E(nb(d.essai.requetes_heure))}</td>
              <td class="lic-yes">${E(nb(d.pro.requetes_heure))}</td></tr>
          <tr><td>Symboles par analyse</td>
              <td class="lic-no">${E(nb(d.essai.symboles_par_analyse))}</td>
              <td class="lic-yes">${E(nb(d.pro.symboles_par_analyse))}</td></tr>
          ${lignes}
        </tbody>
      </table>
      <p class="lic-note">* Sous réserve des éventuelles limites techniques ou de fournisseur.</p>
      <div class="lic-actions">
        <button class="lic-btn lic-btn-pro" onclick="licencePasserPro()">Passer à Pro</button>
      </div>
      ${blocStore()}`);
  }
  window.licenceVoirOffre = ouvrirOffre;

  // ── Activation d'un abonnement ───────────────────────────────────
  function passerPro() {
    const dispo = etat && etat.abonnement_disponible;
    // Aucun émetteur configuré : le dire franchement plutôt que d'ouvrir un
    // formulaire qui ne mènerait nulle part. AUCUN paiement n'est simulé.
    // D'où vient la clé : c'est la première question de quelqu'un qui vient
    // de payer et voit un champ vide. Sans cette ligne, l'écran demande
    // quelque chose dont l'utilisateur ignore qu'il l'a déjà reçu.
    const champ = `
      <label class="lic-label" for="lic-cle">Clé d'abonnement ou licence</label>
      <div class="lic-row">
        <input id="lic-cle" class="lic-input" placeholder="AGF-XXXXX-XXXXX-XXXXX"
               autocomplete="off" spellcheck="false">
        <button class="lic-btn lic-btn-pro" onclick="licenceActiver()">Activer</button>
      </div>
      <p class="lic-detail">Votre clé vous a été envoyée par e-mail et par SMS
      après la confirmation de votre paiement. Vous ne l'avez pas reçue ?
      <a href="#" onclick="return licenceDemanderCle(event)">Demandez-en une nouvelle</a>.</p>
      <div id="lic-activation" class="lic-result"></div>`;
    // Serveur sans secret Stripe : aucun paiement ne peut y être CONFIRMÉ.
    // On le dit, sans laisser croire que la clé déjà reçue serait inutile —
    // elle s'active quand même.
    const indispo = `
      <p class="lic-detail">Ce serveur ne peut pas confirmer de paiement pour
      l'instant : la souscription depuis l'application n'est donc pas encore
      possible. Si vous disposez déjà d'une clé d'abonnement, elle reste
      activable ci-dessous.</p>` + champ;

    ouvrirModale('lic-pro', '★ Passer à la version Pro', `
      <p class="lic-intro">L'abonnement Pro débloque l'ensemble des agents IA,
      les workflows multi-agents, les automatisations, les paramètres avancés
      et supprime les limites de requêtes de la version d'essai.</p>
      ${dispo ? champ : indispo}
      ${blocStore()}`);
    setTimeout(() => { const i = document.getElementById('lic-cle'); if (i) i.focus(); }, 40);
  }
  window.licencePasserPro = passerPro;

  // Raccourci depuis l'écran « Passer à Pro » : réémet la clé et la pose
  // directement dans le champ, prête à être activée.
  window.licenceDemanderCle = function (evenement) {
    if (evenement) evenement.preventDefault();
    const sortie = document.getElementById('lic-activation');
    if (sortie) sortie.textContent = 'Émission d\'une nouvelle clé…';
    fetch('/api/abonnement/cle', { method: 'POST' })
      .then(r => r.json())
      .then(d => {
        if (!sortie) return;
        if (!d.success) {
          sortie.innerHTML = '❌ ' + E(d.error || 'Clé indisponible.');
          return;
        }
        const champ = document.getElementById('lic-cle');
        if (champ) champ.value = d.cle;
        sortie.innerHTML = '✅ ' + E(d.message || '')
          + ' Votre clé est saisie ci-dessus : cliquez sur « Activer ».';
      })
      .catch(() => { if (sortie) sortie.innerHTML = '❌ Serveur injoignable.'; });
    return false;
  };

  window.licenceActiver = async function () {
    const champ = document.getElementById('lic-cle');
    const sortie = document.getElementById('lic-activation');
    if (!champ || !sortie) return;
    const cle = champ.value.trim();
    if (!cle) { sortie.innerHTML = '⚠️ Saisissez une clé d\'abonnement.'; return; }
    sortie.textContent = 'Vérification…';
    // fetch direct : une activation REFUSÉE répond 402, et fetchJSON
    // l'afficherait comme un verrou — ici c'est le message de l'émetteur
    // qu'il faut montrer, dans le formulaire.
    let d = {};
    try {
      const r = await fetch('/api/licence/activer', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ cle }),
      });
      d = await r.json();
    } catch (e) {
      sortie.innerHTML = '❌ Serveur injoignable.';
      return;
    }
    if (d.licence) appliquer(d.licence);
    if (d.success) {
      sortie.innerHTML = '✅ Abonnement Pro activé — toutes les fonctionnalités sont débloquées.';
      if (typeof chargerAgents === 'function') chargerAgents();
      return;
    }
    sortie.innerHTML = '❌ ' + E(d.error || 'Activation impossible.');
  };

  // ── Écran de présentation de la version d'essai ──────────────────
  function ouvrirEssai() {
    const lim = (etat && etat.limites_essai) || {};
    const total = (etat && etat.agents && etat.agents.total) || '';
    ouvrirModale('lic-essai', "Version d'essai — Fonctionnalités limitées", `
      <p class="lic-intro">Cette version d'essai a été volontairement limitée afin de
      permettre la découverte de l'application et de ses principales fonctionnalités.</p>

      <h4 class="lic-h">🔒 Sécurité et informations privées</h4>
      <p>Toutes les clés API, identifiants, tokens, secrets et autres informations
      sensibles ont été retirés de la version distribuée. Aucun secret ou identifiant
      privé ne doit être présent dans le code source ou dans les fichiers publics de
      l'application. Les fonctionnalités nécessitant des services externes peuvent
      donc être limitées ou désactivées dans cette version d'essai.</p>

      <h4 class="lic-h">🤖 Agents IA</h4>
      <p>L'exploitation complète des ${E(total)} agents IA est volontairement bridée
      dans cette version d'essai : ${E(lim.max_agents)} agents sont accessibles, les
      autres restent visibles mais verrouillés. Certaines fonctionnalités, capacités
      et limites d'utilisation sont restreintes afin de proposer une démonstration du
      fonctionnement général de l'application sans donner accès à l'intégralité des
      fonctionnalités professionnelles.</p>
      <ul class="lic-ul">
        <li>${E(lim.max_agents)} agents IA utilisables sur ${E(total)}</li>
        <li>${E(lim.requetes_jour)} requêtes IA par jour, ${E(lim.requetes_heure)} par heure</li>
        <li>${E(lim.symboles_par_analyse)} symbole par analyse</li>
      </ul>

      <h4 class="lic-h">🚀 Version Pro</h4>
      <p>La version Pro complète permet de débloquer l'ensemble des fonctionnalités et
      capacités prévues pour l'application, notamment l'exploitation complète des agents
      IA et les fonctionnalités avancées. La version Pro est accessible directement
      depuis l'application via un abonnement.</p>

      <div class="lic-actions">
        <button class="lic-btn lic-btn-pro" onclick="licencePasserPro()">Passer à Pro</button>
        <button class="lic-btn lic-btn-ghost" onclick="licenceVoirOffre()">Voir les fonctionnalités Pro</button>
      </div>

      ${blocStore()}
      <p class="lic-note">Version d'essai : fonctionnalités volontairement limitées.<br>
      Version Pro : accès complet après abonnement.</p>`);
  }
  window.licenceEcranEssai = ouvrirEssai;

  // ── Agent verrouillé sélectionné ─────────────────────────────────
  window.licenceAgentVerrouille = function (agentId, nom) {
    verrou({
      feature: 'agents_complets',
      feature_libelle: nom ? `Agent « ${nom} »` : 'Agent IA supplémentaire',
      detail: "Cet agent nécessite la version Pro. En version d'essai, seuls "
            + ((etat && etat.limites_essai && etat.limites_essai.max_agents) || 6)
            + " agents participent aux analyses.",
    });
  };

  // Premier lancement : présenter l'essai une fois, sans s'imposer ensuite.
  // Le choix est per-appareil (confort d'affichage), il n'ouvre aucun droit —
  // les limites, elles, sont tenues par le serveur.
  function presenterUneFois() {
    if (estPro()) return;
    // Rien à présenter tant qu'il n'y a pas de compte, ou qu'il est suspendu :
    // l'écran d'inscription ou le mur de paiement passe avant.
    if (!etat || !etat.utilisable) return;
    try {
      if (localStorage.getItem('lic-essai-vu')) return;
    } catch (e) { return; }      // navigation privée : ne pas insister

    // Attendre d'être DANS le tableau de bord. Présentée sur l'écran
    // d'accueil, cette modale se plaçait par-dessus « Lancer l'application »
    // et empêchait purement et simplement d'entrer dans l'application.
    const accueil = document.getElementById('launch-screen');
    const surAccueil = !!accueil && accueil.style.display !== 'none';
    if (surAccueil) return;      // on réessaiera au prochain appel

    try { localStorage.setItem('lic-essai-vu', '1'); } catch (e) { return; }
    setTimeout(ouvrirEssai, 1200);
  }

  document.addEventListener('DOMContentLoaded', () => { charger(false); });
})();
