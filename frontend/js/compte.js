// ═══════════════ Compte utilisateur, essai et abonnement ═══════════════
// Écran d'inscription / connexion, bandeau d'essai, mur de paiement.
//
// CE FICHIER NE DÉCIDE DE RIEN. C'est le serveur qui accorde ou refuse :
// 401 « créez un compte », 402 « abonnement requis ». L'interface se contente
// d'afficher l'écran correspondant. Modifier ce fichier — ou l'état du
// navigateur — n'ouvre aucune porte : chaque route repart en 401 / 402.
//
// AUCUNE DONNÉE DE CARTE ICI. Le formulaire d'inscription ne demande ni
// numéro, ni date d'expiration, ni cryptogramme : la carte se saisit sur les
// pages de Stripe, jamais dans cette application.

(function () {
  let etat = null;          // dernier /api/licence reçu
  let ecranOuvert = null;   // 'auth' | 'paywall' | null

  function E(s) {
    return (typeof esc === 'function') ? esc(s) : String(s == null ? '' : s)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  }
  function h(html) { const d = document.createElement('div'); d.innerHTML = html; return d.firstElementChild; }
  function euros(n) { return Number(n).toFixed(2).replace('.', ','); }

  window.compteEtat = () => etat;

  // ── Lecture de l'état (route ouverte sans session) ───────────────
  async function charger() {
    let d = null;
    try {
      const r = await fetch('/api/licence');
      if (r.ok) d = await r.json();
    } catch (e) { /* serveur injoignable : app.js affiche déjà son bandeau */ }
    if (d) appliquer(d);
    return d;
  }
  window.compteCharger = charger;

  function appliquer(d) {
    if (!d || !d.etat) return;
    etat = d;
    if (typeof window.licenceAppliquer === 'function') window.licenceAppliquer(d);
    peindreBandeau();
    arbitrerEcran();
  }
  window.compteAppliquer = appliquer;

  // ── Quel écran montrer ? ─────────────────────────────────────────
  // C'est l'état renvoyé par le serveur qui tranche, jamais une préférence
  // locale : un compte suspendu doit revoir le mur de paiement à chaque
  // chargement, même s'il a fermé la fenêtre la fois précédente.
  function arbitrerEcran() {
    if (!etat) return;
    if (etat.compte_requis) { ouvrirAuth(); return; }
    if (etat.compte_suspendu) { ouvrirPaywall(); return; }
    fermerEcran();
  }

  function fermerEcran() {
    const m = document.getElementById('compte-modal');
    if (m) m.remove();
    ecranOuvert = null;
  }

  function ouvrirEcran(id, contenu, fermable) {
    const existant = document.getElementById('compte-modal');
    if (existant) existant.remove();
    const m = h(`<div class="lic-modal compte-modal" id="compte-modal">
      <div class="lic-box compte-box">${contenu}</div></div>`);
    document.body.appendChild(m);
    ecranOuvert = id;
    if (fermable) {
      m.addEventListener('click', e => { if (e.target === m) fermerEcran(); });
    }
    return m;
  }

  // ═══════════════ INSCRIPTION / CONNEXION ═══════════════
  function ouvrirAuth(mode) {
    mode = mode || 'inscription';
    const essai = (etat && etat.essai && etat.essai.jours) || 3;
    const inscription = mode === 'inscription';

    ouvrirEcran('auth', `
      <div class="lic-head">
        <span>${inscription ? 'Créer un compte' : 'Se connecter'}</span>
      </div>
      <div class="lic-body">
        <p class="lic-intro">${inscription
          ? `Créez votre compte pour utiliser l'application. L'inscription ouvre
             un essai gratuit de <strong>${E(essai)} jours</strong>.`
          : 'Connectez-vous pour retrouver votre compte.'}</p>

        <div class="cpt-onglets">
          <button class="cpt-onglet ${inscription ? 'actif' : ''}" data-mode="inscription">Créer un compte</button>
          <button class="cpt-onglet ${inscription ? '' : 'actif'}" data-mode="connexion">Se connecter</button>
        </div>

        <form id="cpt-form" autocomplete="on" novalidate>
          <label class="lic-label" for="cpt-email">E-mail</label>
          <input id="cpt-email" class="lic-input cpt-large" type="email"
                 autocomplete="email" placeholder="vous@exemple.fr" required>

          <label class="lic-label" for="cpt-mdp">Mot de passe</label>
          <input id="cpt-mdp" class="lic-input cpt-large" type="password"
                 autocomplete="${inscription ? 'new-password' : 'current-password'}"
                 placeholder="${inscription ? '8 caractères minimum' : 'Votre mot de passe'}" required>

          ${inscription ? `
          <label class="lic-label" for="cpt-tel">Téléphone</label>
          <input id="cpt-tel" class="lic-input cpt-large" type="tel"
                 autocomplete="tel" placeholder="06 12 34 56 78" required>

          <label class="lic-label" for="cpt-adresse">Adresse postale</label>
          <input id="cpt-adresse" class="lic-input cpt-large" type="text"
                 autocomplete="street-address" placeholder="12 rue de la Paix" required>

          <div class="cpt-ligne">
            <div>
              <label class="lic-label" for="cpt-cp">Code postal</label>
              <input id="cpt-cp" class="lic-input" type="text"
                     autocomplete="postal-code" placeholder="75002" required>
            </div>
            <div>
              <label class="lic-label" for="cpt-ville">Ville</label>
              <input id="cpt-ville" class="lic-input" type="text"
                     autocomplete="address-level2" placeholder="Paris" required>
            </div>
          </div>

          <label class="lic-label" for="cpt-pays">Pays</label>
          <input id="cpt-pays" class="lic-input cpt-large" type="text"
                 autocomplete="country-name" value="France" required>

          <p class="cpt-note">🔒 Aucune donnée de carte bancaire ne vous est
          demandée ici. Le paiement, si vous vous abonnez, se fait sur les
          pages sécurisées de Stripe.</p>
          ` : ''}

          <button type="submit" class="lic-btn lic-btn-pro cpt-submit" id="cpt-submit">
            ${inscription ? `Créer mon compte et démarrer l'essai` : 'Se connecter'}
          </button>
        </form>
        <div id="cpt-erreur" class="cpt-erreur"></div>
      </div>`, false);

    document.querySelectorAll('.cpt-onglet').forEach(b => {
      b.addEventListener('click', () => ouvrirAuth(b.dataset.mode));
    });
    document.getElementById('cpt-form').addEventListener('submit', e => {
      e.preventDefault();
      soumettre(inscription);
    });
    setTimeout(() => { const i = document.getElementById('cpt-email'); if (i) i.focus(); }, 40);
  }
  window.compteOuvrirAuth = ouvrirAuth;

  async function soumettre(inscription) {
    const val = id => (document.getElementById(id) || {}).value || '';
    const bouton = document.getElementById('cpt-submit');
    const sortie = document.getElementById('cpt-erreur');
    sortie.textContent = '';
    bouton.disabled = true;
    bouton.textContent = inscription ? 'Création…' : 'Connexion…';

    const corps = inscription
      ? {
          email: val('cpt-email').trim(),
          mot_de_passe: val('cpt-mdp'),
          telephone: val('cpt-tel').trim(),
          adresse: val('cpt-adresse').trim(),
          code_postal: val('cpt-cp').trim(),
          ville: val('cpt-ville').trim(),
          pays: val('cpt-pays').trim(),
        }
      : { email: val('cpt-email').trim(), mot_de_passe: val('cpt-mdp') };

    let d = {};
    try {
      const r = await fetch(inscription ? '/api/compte/inscription' : '/api/compte/connexion',
        { method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(corps) });
      d = await r.json();
    } catch (e) {
      d = { error: 'Serveur injoignable.' };
    }

    bouton.disabled = false;
    bouton.textContent = inscription ? `Créer mon compte et démarrer l'essai` : 'Se connecter';

    if (d && d.success) {
      if (d.licence) appliquer(d.licence);
      fermerEcran();
      rafraichirEcranCourant();
      return;
    }
    sortie.innerHTML = '❌ ' + E((d && d.error) || 'Impossible pour le moment.');
    // Contact déjà pris : l'utilisateur a presque toujours simplement oublié
    // qu'il avait un compte — on l'emmène sur la connexion.
    if (d && d.code === 'compte_existant') {
      sortie.innerHTML += ' <a href="#" id="cpt-vers-connexion">Se connecter</a>';
      const a = document.getElementById('cpt-vers-connexion');
      if (a) a.addEventListener('click', ev => { ev.preventDefault(); ouvrirAuth('connexion'); });
    }
  }

  // Après une connexion, une inscription ou un paiement, l'écran VISIBLE doit
  // repartir sur le nouvel état — pas seulement le tableau de bord.
  //
  // Sans cela, l'écran d'accueil gardait le message « Créez un compte pour
  // commencer » et son bouton « Lancer l'application » grisé alors que le
  // compte venait d'être créé : l'utilisateur se retrouvait bloqué devant une
  // application qui l'avait pourtant accepté.
  function rafraichirEcranCourant() {
    // L'écran d'accueil est masqué par `style.display = 'none'`
    // (transitionerVersDashboard), pas par une classe : c'est donc le style
    // calculé qui fait foi.
    const accueil = document.getElementById('launch-screen');
    const surAccueil = !!accueil && accueil.style.display !== 'none'
                       && !accueil.classList.contains('fade-out');
    if (surAccueil && typeof initLaunchScreen === 'function') {
      initLaunchScreen();
      return;
    }
    if (typeof init === 'function') init();
  }

  window.compteDeconnexion = async function () {
    try { await fetch('/api/compte/deconnexion', { method: 'POST' }); } catch (e) {}
    etat = null;
    await charger();
    ouvrirAuth('connexion');
  };

  // ═══════════════ MUR DE PAIEMENT ═══════════════
  function ouvrirPaywall() {
    const paiement = (etat && etat.paiement) || {};
    const formules = paiement.formules || {};
    const expire = etat && etat.etat === 'PRO_EXPIRED';

    const cartes = Object.values(formules).map(f => `
      <div class="cpt-formule">
        <div class="cpt-f-titre">${E(f.libelle)}</div>
        <div class="cpt-f-prix">${E(euros(f.prix))} €<span>/${E(f.periode)}</span></div>
        <div class="cpt-f-desc">${E(f.description || '')}</div>
        <a class="lic-btn lic-btn-pro cpt-f-btn" href="${E(f.lien_paiement)}"
           target="_blank" rel="noopener noreferrer" data-formule="${E(f.id)}">
          S'abonner
        </a>
      </div>`).join('');

    const avertissementTest = paiement.mode_test ? `
      <p class="cpt-avert">⚠️ Les liens de paiement pointent actuellement vers
      l'environnement de <strong>test</strong> de Stripe : aucun règlement réel
      n'est encaissé.</p>` : '';

    const avertissementVerif = paiement.encaissement_configure ? '' : `
      <p class="cpt-avert">⚠️ La vérification automatique des paiements n'est pas
      encore configurée sur ce serveur : un règlement ne débloquera pas le
      compte tant qu'elle ne l'est pas.</p>`;

    ouvrirEcran('paywall', `
      <div class="lic-head"><span>${expire ? 'Abonnement expiré' : 'Essai terminé'}</span></div>
      <div class="lic-body">
        <p class="lic-intro">${E((etat && etat.message) || '')}</p>
        ${avertissementTest}${avertissementVerif}

        <div class="cpt-formules">${cartes}</div>

        <p class="cpt-note">Le paiement se fait sur les pages sécurisées de
        Stripe. Une fois le règlement confirmé, votre compte passe en
        <strong>abonné</strong> et l'intégralité des agents IA et des
        fonctionnalités avancées est débloquée.</p>

        <div class="lic-actions">
          <button class="lic-btn lic-btn-ghost" id="cpt-deja-paye">J'ai payé — vérifier</button>
          <button class="lic-btn lic-btn-ghost" id="cpt-changer-compte">Changer de compte</button>
        </div>
        <div id="cpt-verif" class="cpt-erreur"></div>
        ${typeof window.licenceBlocStore === 'function' ? window.licenceBlocStore() : ''}
      </div>`, false);

    document.getElementById('cpt-deja-paye').addEventListener('click', verifierPaiement);
    document.getElementById('cpt-changer-compte').addEventListener('click', window.compteDeconnexion);
  }
  window.compteOuvrirPaywall = ouvrirPaywall;

  // « J'ai payé » ne débloque RIEN par lui-même : il redemande au serveur, qui
  // redemande à Stripe. Sans confirmation de Stripe, le compte reste fermé.
  async function verifierPaiement() {
    const sortie = document.getElementById('cpt-verif');
    sortie.textContent = 'Vérification auprès de Stripe…';
    const d = await charger();
    if (d && !d.compte_suspendu) {
      sortie.innerHTML = '✅ Abonnement actif — accès rétabli.';
      fermerEcran();
      rafraichirEcranCourant();
      return;
    }
    sortie.innerHTML = "⏳ Aucun paiement confirmé pour l'instant. "
      + "Si vous venez de régler, patientez quelques instants puis réessayez.";
  }

  // ═══════════════ BANDEAU D'ESSAI ═══════════════
  function peindreBandeau() {
    const el = document.getElementById('cpt-bandeau');
    if (!el || !etat) return;
    const essai = etat.essai || {};
    if (!essai.en_cours) { el.style.display = 'none'; return; }

    const jours = Math.max(0, Number(essai.jours_restants || 0));
    const reste = jours < 1
      ? `${Math.max(1, Math.round(jours * 24))} h`
      : `${Math.ceil(jours)} jour${Math.ceil(jours) > 1 ? 's' : ''}`;
    el.style.display = '';
    el.classList.toggle('cpt-urgent', jours < 1);
    el.innerHTML = `<span>Essai gratuit — <strong>${E(reste)}</strong> restant.
      Abonnez-vous pour débloquer les ${E(String((etat.agents || {}).total || ''))} agents
      IA et les fonctionnalités avancées.</span>
      <button class="lic-btn lic-btn-pro" id="cpt-bandeau-btn">S'abonner</button>`;
    const b = document.getElementById('cpt-bandeau-btn');
    if (b) b.addEventListener('click', () => ouvrirAbonnement());
  }

  // Écran d'abonnement ouvert VOLONTAIREMENT (essai en cours) : mêmes
  // formules, mais refermable — l'utilisateur n'est pas bloqué.
  function ouvrirAbonnement() {
    const paiement = (etat && etat.paiement) || {};
    const formules = paiement.formules || {};
    const cartes = Object.values(formules).map(f => `
      <div class="cpt-formule">
        <div class="cpt-f-titre">${E(f.libelle)}</div>
        <div class="cpt-f-prix">${E(euros(f.prix))} €<span>/${E(f.periode)}</span></div>
        <div class="cpt-f-desc">${E(f.description || '')}</div>
        <a class="lic-btn lic-btn-pro cpt-f-btn" href="${E(f.lien_paiement)}"
           target="_blank" rel="noopener noreferrer">S'abonner</a>
      </div>`).join('');
    ouvrirEcran('abonnement', `
      <div class="lic-head"><span>Passer à la version Pro</span>
        <button class="lic-close" id="cpt-fermer" aria-label="Fermer">✕</button></div>
      <div class="lic-body">
        <p class="lic-intro">Débloquez l'ensemble des agents IA, les workflows
        multi-agents, les automatisations et les paramètres avancés.</p>
        ${paiement.mode_test ? `<p class="cpt-avert">⚠️ Liens de paiement en
          environnement de <strong>test</strong> Stripe.</p>` : ''}
        <div class="cpt-formules">${cartes}</div>
        <p class="cpt-note">Paiement sur les pages sécurisées de Stripe.
        Aucune donnée de carte n'est saisie dans cette application.</p>
        ${typeof window.licenceBlocStore === 'function' ? window.licenceBlocStore() : ''}
      </div>`, true);
    document.getElementById('cpt-fermer').addEventListener('click', fermerEcran);
  }
  window.compteOuvrirAbonnement = ouvrirAbonnement;

  document.addEventListener('DOMContentLoaded', charger);
})();
