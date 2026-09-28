// ═══════════════ Licence Agence Novia : activation et réglages ═══════════════
// Actif seulement en mode Novia (URL de l'API ET clé publique configurées côté
// application). Cet écran n'AFFICHE que ce que le serveur local décide : aucune
// règle de licence n'est écrite ici, et aucun droit n'est stocké dans le
// navigateur. Les URL du site Novia arrivent de /api/activation — jamais
// écrites en dur dans ce fichier.

(function () {
  let etat = null;            // dernière réponse de /api/activation
  let ouvertPar = null;       // 'blocage' | 'utilisateur' | null
  let aideRestauration = false;
  // Clé en cours de saisie, conservée HORS du DOM : si l'écran est redessiné
  // (retour du serveur, nouvel état), l'utilisateur ne perd pas ce qu'il a
  // tapé. C'est exactement le défaut qu'avait eu le formulaire d'inscription.
  const saisie = { cle: '' };

  const LIBELLES = {
    NOT_ACTIVATED: 'Non activée', ACTIVE: 'Active',
    OFFLINE_VALID: 'Valide hors ligne', EXPIRED: 'Expirée',
    SUSPENDED: 'Suspendue', REVOKED: 'Révoquée', INVALID: 'Invalide',
  };

  function E(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, c => ({
      '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  }
  function h(html) { const d = document.createElement('div'); d.innerHTML = html.trim(); return d.firstElementChild; }
  function date(ts) {
    return ts ? new Date(ts * 1000).toLocaleDateString('fr-FR',
      { day: '2-digit', month: '2-digit', year: 'numeric' }) : '—';
  }
  function dateHeure(ts) {
    return ts ? new Date(ts * 1000).toLocaleString('fr-FR',
      { day: '2-digit', month: '2-digit', year: 'numeric', hour: '2-digit', minute: '2-digit' }) : 'jamais';
  }

  async function charger() {
    try {
      const r = await fetch('/api/activation');
      if (r.ok) etat = await r.json();
    } catch (e) { /* serveur local injoignable : app.js l'affiche déjà */ }
    return etat;
  }

  // Les routes qui modifient la licence exigent du JSON (voir
  // backend/routes/activation.py) : c'est ce qui empêche un autre site de
  // les appeler depuis le navigateur de l'utilisateur.
  async function poster(chemin, corps) {
    try {
      const r = await fetch(chemin, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(corps || {}),
      });
      return await r.json();
    } catch (e) {
      return { success: false, message: 'Le serveur local de l\'application ne répond pas.' };
    }
  }

  async function apresChangement(d, messageReglages) {
    if (d && d.etat) etat = Object.assign({}, etat || {}, d);
    if (typeof window.compteCharger === 'function') await window.compteCharger(true);
    if (typeof window.chargerAgents === 'function') window.chargerAgents();
    const zone = document.getElementById('lic-mode-novia');
    if (zone && zone.offsetParent !== null) await peindreReglages(zone, messageReglages);
  }

  function formaterResultat(r) {
    return (r.success ? '✅ ' : '⚠️ ') + E(r.message || '');
  }

  // ═══════════════ ÉCRAN D'ACTIVATION ═══════════════
  async function ouvrir(options) {
    options = options || {};
    const par = options.par || 'blocage';
    // Déjà ouvert : ne RIEN reconstruire. Les sondages périodiques rappellent
    // cette fonction plusieurs fois par minute ; redessiner à chaque fois
    // viderait le champ sous les doigts de l'utilisateur.
    if (document.getElementById('activation-modal')) {
      if (par === 'utilisateur') ouvertPar = 'utilisateur';
      return;
    }
    await charger();
    if (!etat) return;
    ouvertPar = par;
    dessiner();
  }

  function dessiner() {
    const ancien = document.getElementById('activation-modal');
    if (ancien) ancien.remove();
    const d = etat || {};
    const bloquant = ouvertPar === 'blocage';

    // Pourquoi l'écran s'ouvre : licence expirée, révoquée, autre appareil…
    // Un premier lancement n'a pas de raison à afficher.
    const raison = (d.activee || d.raison) && !d.valide && d.message
      ? `<p class="act-alerte">${E(d.message)}</p>` : '';

    const achat = d.url_achat
      ? `<a class="lic-btn lic-btn-ghost" href="${E(d.url_achat)}" target="_blank"
            rel="noopener noreferrer">Acheter Agence 46</a>` : '';

    const aide = aideRestauration ? `
      <div class="act-aide">
        Retrouvez votre clé dans votre espace client Agence Novia, puis
        saisissez-la ci-dessus. Réactiver une clé sur <strong>cet</strong>
        appareil ne consomme pas d'emplacement supplémentaire.
        ${d.url_compte ? `<br><a href="${E(d.url_compte)}" target="_blank"
          rel="noopener noreferrer">Ouvrir mon espace Novia</a>` : ''}
      </div>` : '';

    // Proposé SEULEMENT quand une licence locale valide existe réellement :
    // c'est le serveur local qui le dit, pas une préférence de l'écran.
    const horsLigne = d.valide ? `
      <button class="lic-btn lic-btn-ghost" id="act-hors-ligne">
        Utiliser l'application hors ligne</button>
      <p class="lic-detail">Licence locale valide jusqu'au ${E(date(d.hors_ligne_jusqua))}.</p>` : '';

    const m = h(`
      <div class="lic-modal" id="activation-modal">
        <div class="lic-box act-box" role="dialog" aria-labelledby="act-titre">
          <div class="lic-head"><span id="act-titre">Activation de votre licence</span>
            ${bloquant ? '' : '<button class="lic-close" id="act-fermer" aria-label="Fermer">✕</button>'}
          </div>
          <div class="lic-body">
            <p class="lic-intro">Saisissez la clé de licence reçue lors de votre
            achat sur Agence Novia. Agence 46 fonctionne ensuite entièrement sur
            votre ordinateur : seule la licence est vérifiée en ligne,
            périodiquement.</p>
            ${raison}
            <label class="lic-label" for="act-cle">Clé de licence</label>
            <div class="lic-row">
              <input id="act-cle" class="lic-input" autocomplete="off" spellcheck="false"
                     placeholder="NOVIA-XXXX-XXXX-XXXX-XXXX" value="${E(saisie.cle)}">
            </div>
            <div class="lic-actions">
              <button class="lic-btn lic-btn-pro" id="act-activer">Activer Agence 46</button>
              ${achat}
              <button class="lic-btn lic-btn-ghost" id="act-restaurer">Restaurer une licence</button>
            </div>
            ${aide}
            <div id="act-resultat" class="lic-result" role="status" aria-live="polite"></div>
            ${horsLigne}
            ${d.configuration_incomplete ? `<p class="act-alerte">${E(d.configuration_incomplete)}</p>` : ''}
          </div>
        </div>
      </div>`);
    document.body.appendChild(m);

    const champ = document.getElementById('act-cle');
    champ.addEventListener('input', () => { saisie.cle = champ.value; });
    champ.addEventListener('keydown', e => { if (e.key === 'Enter') activer(); });
    document.getElementById('act-activer').addEventListener('click', activer);
    document.getElementById('act-restaurer').addEventListener('click', () => {
      aideRestauration = !aideRestauration;
      saisie.cle = champ.value;
      dessiner();
    });
    const hl = document.getElementById('act-hors-ligne');
    if (hl) hl.addEventListener('click', fermer);
    const f = document.getElementById('act-fermer');
    if (f) f.addEventListener('click', fermer);
    if (!bloquant) m.addEventListener('click', e => { if (e.target === m) fermer(); });
    setTimeout(() => champ.focus(), 40);
  }

  async function activer() {
    const champ = document.getElementById('act-cle');
    const sortie = document.getElementById('act-resultat');
    const bouton = document.getElementById('act-activer');
    if (!champ || !sortie) return;
    const cle = champ.value.trim();
    saisie.cle = cle;
    if (!cle) { sortie.innerHTML = '⚠️ Saisissez votre clé de licence.'; return; }
    sortie.textContent = 'Vérification auprès du serveur de licences…';
    bouton.disabled = true;
    const route = (etat && etat.activee) ? '/api/activation/changer' : '/api/activation/activer';
    const d = await poster(route, { license_key: cle });
    bouton.disabled = false;
    if (d.success) {
      saisie.cle = '';
      sortie.innerHTML = '✅ ' + E(d.message || 'Licence activée.');
      await apresChangement(d);
      setTimeout(fermer, 700);
      return;
    }
    sortie.innerHTML = '❌ ' + E(d.message || 'Activation impossible.');
  }

  function fermer() {
    const m = document.getElementById('activation-modal');
    if (m) m.remove();
    ouvertPar = null;
    aideRestauration = false;
  }

  // Appelée par l'arbitrage d'écran (compte.js) quand la licence redevient
  // valide. Ne ferme PAS un écran ouvert volontairement depuis les réglages
  // (« Changer de licence ») : ce serait lui retirer la main en pleine saisie.
  function fermerSiBloquant() {
    if (ouvertPar === 'blocage') fermer();
  }

  window.activationOuvrir = ouvrir;
  window.activationFermerSiBloquant = fermerSiBloquant;

  // ═══════════════ RÉGLAGES → LICENCE ═══════════════
  async function peindreReglages(zone, messageInitial) {
    if (!zone) return;
    const d = await charger();
    if (!d) { zone.textContent = 'État de la licence indisponible.'; return; }

    const titre = d.valide ? `Agence 46 ${d.niveau}` : 'Agence 46';
    const alerte = (!d.valide && d.activee) || d.raison
      ? `<p class="act-alerte">${E(d.message)}</p>` : '';
    const horloge = (d.horloge_reculee || d.horloge_decalee)
      ? `<p class="act-alerte">L'horloge de cet ordinateur semble incorrecte.
         Corrigez la date et l'heure, puis actualisez la licence.</p>` : '';

    zone.innerHTML = `
      <div class="act-fiche">
        <div class="act-titre-offre">${E(titre)}</div>
        <dl class="act-grille">
          <dt>Statut</dt><dd class="act-statut act-${E((d.etat || '').toLowerCase())}">
            ${E(LIBELLES[d.etat] || d.etat)}</dd>
          <dt>Licence</dt><dd>${E(d.cle_masquee || '—')}</dd>
          <dt>Appareil</dt><dd>${d.activee ? 'Activé' : 'Non activé'}</dd>
          <dt>Dernière vérification</dt><dd>${E(dateHeure(d.derniere_verification))}</dd>
          <dt>Licence hors ligne valide jusqu'au</dt><dd>${E(date(d.hors_ligne_jusqua))}</dd>
          ${d.expire_le ? `<dt>Échéance de la licence</dt><dd>${E(date(d.expire_le))}</dd>` : ''}
        </dl>
      </div>
      ${alerte}${horloge}
      <div class="tools-row">
        <button id="act-r-actualiser">Actualiser la licence</button>
        <button id="act-r-changer">Changer de licence</button>
        ${d.activee ? '<button id="act-r-desactiver">Désactiver cet appareil</button>' : ''}
        ${d.url_compte ? `<a class="lic-btn lic-btn-ghost" href="${E(d.url_compte)}"
            target="_blank" rel="noopener noreferrer">Gérer mon abonnement</a>` : ''}
      </div>
      <div id="act-r-resultat" class="tools-result"></div>
      <p class="tools-hint">Vos projets, fichiers, modèles et données restent sur
      cet ordinateur. Seule la licence est vérifiée en ligne, au plus tous les
      quelques jours ; hors connexion, la licence locale fait foi jusqu'à la date
      ci-dessus.</p>`;

    const sortie = document.getElementById('act-r-resultat');
    if (messageInitial) sortie.innerHTML = messageInitial;
    document.getElementById('act-r-actualiser').addEventListener('click', async () => {
      sortie.textContent = 'Vérification auprès du serveur de licences…';
      const r = await poster('/api/activation/actualiser');
      await apresChangement(r, formaterResultat(r));
    });
    document.getElementById('act-r-changer').addEventListener('click',
      () => ouvrir({ par: 'utilisateur' }));
    const desac = document.getElementById('act-r-desactiver');
    if (desac) desac.addEventListener('click', async () => {
      if (!window.confirm('Désactiver Agence 46 sur cet appareil ?\n\n'
          + 'Vos projets et vos données locales ne seront pas touchés. '
          + 'Vous pourrez réactiver votre licence ici ou sur un autre appareil.')) return;
      sortie.textContent = 'Désactivation…';
      let r = await poster('/api/activation/desactiver', {});
      if (!r.success && r.erreur && window.confirm(
          (r.message || '') + '\n\nRetirer quand même la licence de cet appareil ? '
          + 'L\'emplacement restera occupé chez Novia jusqu\'à ce que vous le '
          + 'libériez depuis votre espace client.')) {
        r = await poster('/api/activation/desactiver', { forcer: true });
      }
      await apresChangement(r, formaterResultat(r));
    });
  }
  window.activationPeindreReglages = peindreReglages;
})();
