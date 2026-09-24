"""Tests des comptes utilisateurs, de l'essai de 3 jours et de l'abonnement Stripe.

Ce que la suite vérifie :

- sans compte, l'application est INUTILISABLE (401 partout) ;
- l'inscription ouvre exactement 3 jours d'essai ;
- un contact déjà enregistré ne peut pas resservir — y compris déguisé
  (casse, alias « + », téléphone au format international) ;
- passé les 3 jours, le compte est SUSPENDU et l'application fermée, seul
  l'abonnement reste joignable ;
- aucun paiement ne s'auto-déclare : ni depuis le navigateur, ni par un
  webhook non signé ;
- un paiement confirmé ouvre les droits Pro, et son expiration les referme ;
- aucun numéro de carte n'entre dans l'application ni dans sa base.
"""
import _setup  # noqa: F401
import itertools
import json
import os
import tempfile
import time
from pathlib import Path

_RACINE = Path(__file__).resolve().parents[2]

_INSCRIPTION = {
    "email": "Alice.Martin@Exemple.fr",
    "mot_de_passe": "un-mot-de-passe-solide",
    "telephone": "06 11 22 33 44",
    "adresse": "12 rue de la Paix",
    "code_postal": "75002",
    "ville": "Paris",
    "pays": "France",
}


def _isoler():
    """Configuration ET base dans un dossier temporaire, comptes compris."""
    import importlib
    dossier = Path(tempfile.mkdtemp())
    from utils import app_config
    app_config._PATH = dossier / "config.json"
    app_config._cache = None
    os.environ["AGENCE_DATA_DIR"] = str(dossier / "data")

    import config as app_cfg
    app_cfg.DB_PATH = dossier / "data" / "agence.db"
    app_cfg.DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    import utils.database as database
    importlib.reload(database)

    from licence import abonnement, comptes
    os.environ.pop("AGENCE_LICENCE_JETON", None)
    os.environ.pop("STRIPE_SECRET_KEY", None)
    os.environ.pop("STRIPE_WEBHOOK_SECRET", None)
    abonnement.invalider_cache()
    comptes.definir_compte_courant(None)
    comptes.reinitialiser_echecs()
    return dossier


def _app():
    import sys
    sys.path.insert(0, str(_RACINE))
    from fastapi.testclient import TestClient
    from backend.main import app
    return TestClient(app, raise_server_exceptions=False)


def _inscrire(client, **surcharges):
    corps = dict(_INSCRIPTION)
    corps.update(surcharges)
    return client.post("/api/compte/inscription", json=corps)


# ═══════════════════════════ SANS COMPTE, RIEN ════════════════════════════

def test_sans_compte_application_fermee():
    _isoler()
    c = _app()
    fermees = ["/api/status", "/api/agents", "/api/portfolio", "/api/signaux",
               "/api/mt5/status", "/api/auto-trader/status", "/api/symboles",
               "/api/trading/config", "/api/performance"]
    for url in fermees:
        r = c.get(url)
        assert r.status_code == 401, f"{url} → {r.status_code} au lieu de 401"
        assert r.json()["compte_requis"] is True
    assert c.post("/api/analyser", json=["BTC-USD"]).status_code == 401
    print(f"  OK — sans compte, {len(fermees) + 1} routes répondent 401")


def test_routes_d_inscription_restent_ouvertes():
    """Il faut bien pouvoir créer son compte et payer : ces routes-là sont
    ouvertes, et elles seules."""
    _isoler()
    c = _app()
    for url in ("/api/health", "/api/licence", "/api/compte",
                "/api/abonnement", "/api/abonnement/formules"):
        assert c.get(url).status_code == 200, url
    # L'interface elle-même doit pouvoir s'afficher pour proposer l'inscription.
    assert c.get("/").status_code == 200
    print("  OK — inscription, connexion, offre et paiement restent joignables")


# ═══════════════════════════ INSCRIPTION ══════════════════════════════════

def test_aucune_page_ne_renvoie_du_json_brut():
    """Une NAVIGATION de navigateur ne doit jamais tomber sur du JSON.

    C'est arrivé : la fenêtre du bureau démarre sur /setup, que la garde de
    compte avait fermé. L'utilisateur n'a vu qu'une ligne de texte —
    `{"error":"Créez un compte…","compte_requis":true}` — sans aucun moyen
    d'agir, dès l'installation.

    Deux garanties sont donc vérifiées ici, et sur TOUTES les pages, pas
    seulement celles qu'on a en tête : une page refusée REDIRIGE vers la
    racine, qui sait afficher l'écran d'inscription.
    """
    _isoler()
    import backend.main as bm
    c = _app()

    pages = sorted({
        getattr(r, "path", "") for r in bm.app.routes
        if getattr(r, "path", "") and "{" not in getattr(r, "path", "")
        and "GET" in (getattr(r, "methods", set()) or set())
        and not getattr(r, "path", "").startswith("/api")
    })
    assert pages, "aucune page trouvée — le test ne vérifie rien"

    fautes = []
    for chemin in pages:
        r = c.get(chemin, follow_redirects=False)
        type_contenu = r.headers.get("content-type", "").split(";")[0]
        if r.status_code >= 400 and type_contenu == "application/json":
            fautes.append(f"{chemin} → {r.status_code} {type_contenu}")
    assert not fautes, ("pages renvoyant du JSON brut à un navigateur :\n  "
                        + "\n  ".join(fautes))
    print(f"  OK — aucune des {len(pages)} pages ne renvoie de JSON brut")


def test_ecran_de_premier_lancement_accessible():
    """La fenêtre du bureau démarre sur /setup : le fermer rend l'application
    inutilisable dès l'installation, avant même qu'un compte puisse exister."""
    _isoler()
    c = _app()

    r = c.get("/setup")
    assert r.status_code == 200, f"/setup → {r.status_code} : la fenêtre du bureau serait vide"
    assert r.headers.get("content-type", "").startswith("text/html")
    assert c.get("/setup/state").status_code == 200, "/setup/state fermé"
    assert c.post("/setup/mode", json={"mode": "ollama"}).json()["success"] is True

    # L'écran ne doit laisser filtrer aucune donnée de compte.
    brut = c.get("/setup/state").text
    for interdit in ("mot_de_passe", "licence_jeton", "email", "stripe"):
        assert interdit not in brut.lower(), f"/setup/state expose « {interdit} »"

    # Et la racine, où /setup renvoie ensuite, propose bien l'inscription.
    racine = c.get("/")
    assert racine.status_code == 200 and "js/compte.js" in racine.text

    # app_window.py doit toujours démarrer sur une route ouverte : si ce
    # fichier change d'URL de départ, ce test doit le signaler.
    fenetre = (_RACINE / "app_window.py").read_text(encoding="utf-8")
    import re
    depart = re.search(r'url\s*=\s*f?"http://127\.0\.0\.1:\{?PORT\}?(/[^"]*)"', fenetre)
    assert depart, "URL de départ de app_window.py introuvable"
    chemin = depart.group(1)
    assert c.get(chemin, follow_redirects=False).status_code in (200, 302), \
        f"la fenêtre du bureau démarre sur {chemin}, qui est fermé"
    print(f"  OK — /setup accessible, fenêtre du bureau démarre sur {chemin}")


def test_toutes_les_routes_de_l_ecran_de_lancement_repondent():
    """Chaque route que /setup appelle doit répondre SANS compte.

    La liste n'est pas écrite ici : elle est EXTRAITE de la page elle-même.
    Une liste recopiée à la main vieillit en silence — c'est précisément ce qui
    est arrivé. Ajouter un `fetch()` à l'écran de lancement fera désormais
    échouer ce test tant que la route n'est pas ouverte.

    Ce que coûtait le défaut : la page recevait un 401 dépourvu des champs
    qu'elle lit, affichait « undefined » sous sa barre de progression, et
    concluait « Ollama non détecté » sur une machine où Ollama était installé.
    L'installation automatique ne partait jamais.
    """
    import re
    _isoler()
    import backend.main as bm
    c = _app()

    appels = sorted(set(re.findall(r"fetch\('([^']+)'", bm._SETUP_HTML)))
    assert len(appels) >= 8, f"extraction douteuse : {appels}"

    fermees = []
    for url in appels:
        r = c.get(url)
        if r.status_code == 405:                  # route en POST seulement
            r = c.post(url, json={})
        if r.status_code in (401, 402):
            fermees.append(f"{url} → {r.status_code}")
    assert not fermees, ("l'écran de premier lancement ne peut pas fonctionner, "
                         "ces routes lui sont fermées :\n  " + "\n  ".join(fermees))
    print(f"  OK — les {len(appels)} routes de l'écran de lancement répondent sans compte")


def test_etat_du_moteur_ia_exploitable_par_la_page():
    """Les réponses doivent porter les CHAMPS que la page lit.

    Vérifier le code 200 ne suffit pas : un corps valide mais sans `etape` ni
    `detail` produit exactement le même « undefined » à l'écran.
    """
    _isoler()
    c = _app()

    etat = c.get("/api/ollama/embedded/status").json()
    # Ce que fait le JavaScript : (s.detail || s.etape)
    assert (etat.get("detail") or etat.get("etape")) is not None, \
        "la barre de progression afficherait « undefined »"
    for champ in ("etape", "progression", "installe", "embarque"):
        assert champ in etat, f"/api/ollama/embedded/status sans « {champ} »"

    # Les trois branches de détection de la page reposent sur ces champs.
    ollama = c.get("/api/ollama/status").json()
    for champ in ("available", "installe"):
        assert champ in ollama, (
            f"/api/ollama/status sans « {champ} » : la page conclurait "
            f"« Ollama non détecté » même sur une machine équipée")

    # Et rien de tout cela ne doit laisser filtrer de données de compte.
    for url in ("/api/ollama/status", "/api/ollama/embedded/status",
                "/api/hermes/status"):
        brut = c.get(url).text.lower()
        for interdit in ("mot_de_passe", "licence_jeton", "stripe", "@"):
            assert interdit not in brut, f"{url} expose « {interdit} »"
    print("  OK — l'état du moteur IA porte les champs lus par la page")


def test_aucune_boucle_de_redirection_sur_401():
    """Un 401 ne doit JAMAIS provoquer de navigation.

    Ce défaut a rendu l'application inutilisable : mt5.js, auto_trader.js,
    charts.js et tools.js appellent tous `_authRedirect()` quand ils reçoivent
    un 401. Cette fonction envoyait vers /login, qui redirige vers / quand le
    code d'accès est désactivé — son cas par défaut. Chaque sondage périodique
    relançait donc un cycle complet : la page se rechargeait sans fin et
    l'écran d'accueil restait figé sur « Backend hors ligne », alors que le
    serveur répondait parfaitement.

    On vérifie la PROPRIÉTÉ qui empêche la boucle : l'écran de compte est
    consulté avant toute navigation.
    """
    app_js = (_RACINE / "frontend" / "js" / "app.js").read_text(encoding="utf-8")
    debut = app_js.index("function _authRedirect()")
    corps = app_js[debut:debut + 900]

    avant_navigation = corps.index("location.href")
    for sortie in ("window.compteCharger", "window.compteOuvrirAuth"):
        assert sortie in corps[:avant_navigation], (
            f"_authRedirect navigue sans consulter {sortie} : la boucle "
            f"de rechargement peut revenir")
    assert "_redirectionFaite" in corps, "aucun garde-fou contre une seconde redirection"

    # Et /login doit toujours renvoyer vers / : c'est l'autre moitié du cycle.
    _isoler()
    c = _app()
    r = c.get("/login", follow_redirects=False)
    assert r.status_code == 302 and r.headers.get("location") == "/", (
        "/login ne renvoie plus vers / — vérifier que _authRedirect ne peut "
        "toujours pas boucler")
    print("  OK — un 401 ouvre l'écran de compte, sans navigation ni boucle")


def test_ecran_d_accueil_ne_ment_pas_sur_le_backend():
    """« Backend hors ligne » ne doit s'afficher que si le serveur est ÉTEINT.

    L'écran sondait /api/status, qui exige un compte : son 401 se lisait
    « backend hors ligne » sur une application servie par ce même backend.
    Le message accusait le serveur d'être éteint alors qu'il venait d'envoyer
    la page qu'on était en train de lire.
    """
    _isoler()
    c = _app()

    # La sonde de vivacité doit être une route TOUJOURS ouverte.
    assert c.get("/api/health").status_code == 200

    app_js = (_RACINE / "frontend" / "js" / "app.js").read_text(encoding="utf-8")
    debut = app_js.index("async function initLaunchScreen()")
    corps = app_js[debut:app_js.index("async function", debut + 10)]

    assert "'/api/health'" in corps, "la vivacité n'est plus sondée sur /api/health"
    position_sante = corps.index("'/api/health'")
    position_hors_ligne = corps.index("Backend hors ligne")
    assert position_sante < position_hors_ligne, (
        "« Backend hors ligne » est affiché avant d'avoir vérifié /api/health")
    # Et l'absence de compte doit être dite pour ce qu'elle est.
    assert "compte_requis" in corps, "l'accueil ne distingue pas « pas de compte » de « serveur éteint »"
    print("  OK — l'accueil distingue serveur éteint et compte manquant")


def test_formulaire_d_inscription_ne_se_redessine_pas():
    """Le formulaire ne doit JAMAIS être reconstruit sous les doigts.

    Défaut vécu : les sondages périodiques recevaient un 401, ce qui relançait
    l'arbitrage d'écran, qui détruisait et recréait la modale toutes les trois
    secondes. Le formulaire se vidait pendant la frappe — l'inscription était
    tout simplement impossible.

    On vérifie les PROPRIÉTÉS qui l'empêchent, dans le code servi.
    """
    compte_js = (_RACINE / "frontend" / "js" / "compte.js").read_text(encoding="utf-8")

    # 1. L'arbitrage ne rouvre pas un écran déjà ouvert.
    debut = compte_js.index("function arbitrerEcran()")
    arbitrage = compte_js[debut:debut + 700]
    assert "ecranOuvert === 'auth'" in arbitrage and "return" in arbitrage, \
        "arbitrerEcran rouvre l'écran d'inscription même s'il est déjà affiché"

    # 2. ouvrirAuth est idempotente — dernière barrière, quel que soit l'appelant.
    debut = compte_js.index("function ouvrirAuth(mode)")
    ouverture = compte_js[debut:debut + 900]
    assert "modeAuthOuvert === mode" in ouverture, \
        "ouvrirAuth reconstruit le formulaire même dans le mode déjà affiché"

    # 3. La saisie vit HORS du DOM : un redessin ne peut pas l'effacer, et
    #    l'onglet « Se connecter » n'affiche pas les champs d'adresse.
    assert "const saisie = {}" in compte_js and "memoriserSaisie" in compte_js, \
        "la saisie n'est pas conservée hors du DOM"
    assert "delete saisie[id]" in compte_js, \
        "le mot de passe reste en mémoire après la création du compte"

    # 4. Les 401 en rafale ne déclenchent pas une requête par sondage.
    assert "chargementEnCours" in compte_js and "dernierChargement" in compte_js, \
        "charger() n'est pas mutualisé : les sondages provoqueraient une rafale"
    print("  OK — le formulaire résiste aux 401 répétés et conserve la saisie")


def test_inscription_ouvre_trois_jours():
    _isoler()
    c = _app()
    r = _inscrire(c)
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["success"] is True
    compte = d["compte"]
    # E-mail normalisé, téléphone normalisé.
    assert compte["email"] == "alice.martin@exemple.fr"
    assert compte["telephone"] == "0611223344"
    assert d["licence"]["etat"] == "TRIAL"
    assert 2.9 < compte["essai_jours_restants"] <= 3.0
    from licence import config as lconfig
    assert lconfig.TRIAL_DUREE_JOURS == 3
    # Et l'application s'ouvre aussitôt.
    assert c.get("/api/status").status_code == 200
    print("  OK — inscription : essai de 3 jours exactement, accès immédiat")


def test_champs_obligatoires():
    _isoler()
    c = _app()
    for champ in ("email", "telephone", "adresse", "code_postal", "ville", "pays"):
        r = _inscrire(c, **{champ: ""})
        assert r.status_code == 400, f"{champ} vide accepté"
        assert r.json()["code"] == "validation"
    assert _inscrire(c, mot_de_passe="court").status_code == 400
    assert _inscrire(c, email="pas-un-email").status_code == 400
    assert _inscrire(c, telephone="123").status_code == 400
    print("  OK — e-mail, mot de passe, téléphone et adresse sont exigés")


def test_contact_deja_enregistre_refuse():
    """Le point qui empêche de rouvrir un essai indéfiniment."""
    _isoler()
    c = _app()
    assert _inscrire(c).status_code == 200

    deguisements = [
        ("même e-mail, autre casse", {"email": "ALICE.MARTIN@exemple.fr",
                                      "telephone": "0755667788"}),
        ("alias « + »", {"email": "alice.martin+essai2@exemple.fr",
                         "telephone": "0755667799"}),
        ("téléphone international", {"email": "bob@exemple.fr",
                                     "telephone": "+33 6 11 22 33 44"}),
        ("téléphone 00 33", {"email": "carla@exemple.fr",
                             "telephone": "0033611223344"}),
        ("téléphone espacé", {"email": "david@exemple.fr",
                              "telephone": "06.11.22.33.44"}),
    ]
    for libelle, surcharges in deguisements:
        r = _inscrire(c, **surcharges)
        assert r.status_code == 409, f"{libelle} : accepté ({r.status_code})"
        assert r.json()["code"] == "compte_existant"

    # Un contact RÉELLEMENT différent passe : la règle ne bloque pas tout.
    assert _inscrire(c, email="eve@exemple.fr", telephone="0799887766").status_code == 200
    print(f"  OK — {len(deguisements)} contournements de réinscription refusés")


def test_aucune_carte_bancaire_acceptee():
    """L'application ne doit JAMAIS recevoir de données de carte."""
    _isoler()
    c = _app()
    for champ in ("numero_carte", "carte", "cvc", "iban", "card_number"):
        r = _inscrire(c, **{champ: "4242424242424242"})
        assert r.status_code == 400, f"{champ} accepté"
        assert "carte" in r.json()["error"].lower()

    # Et rien de tel n'existe dans le schéma de la base.
    from licence.comptes import DepotComptes
    import sqlite3
    d = DepotComptes()
    with d._conn() as conn:
        colonnes = {r[1].lower() for r in conn.execute("PRAGMA table_info(comptes)")}
    interdits = {"carte", "numero_carte", "card", "card_number", "cvc", "cvv",
                 "iban", "expiration"}
    assert not (colonnes & interdits), f"colonnes de carte en base : {colonnes & interdits}"
    print("  OK — aucune donnée de carte acceptée ni stockée")


def test_mot_de_passe_jamais_en_clair():
    _isoler()
    c = _app()
    _inscrire(c)
    from licence import comptes
    stocke = comptes.depot().par_email("alice.martin@exemple.fr")
    assert stocke["mot_de_passe"].startswith("pbkdf2$")
    assert _INSCRIPTION["mot_de_passe"] not in stocke["mot_de_passe"]
    assert comptes.verifier_mot_de_passe(_INSCRIPTION["mot_de_passe"],
                                         stocke["mot_de_passe"])
    assert not comptes.verifier_mot_de_passe("autre-chose", stocke["mot_de_passe"])
    # La vue publique n'expose ni le hash, ni l'adresse postale.
    public = comptes.public(stocke)
    assert "mot_de_passe" not in public and "adresse" not in public
    print("  OK — mot de passe en PBKDF2, jamais exposé")


# ═══════════════════════════ CONNEXION / SESSION ══════════════════════════

def test_connexion_et_deconnexion():
    _isoler()
    c = _app()
    _inscrire(c)
    c.post("/api/compte/deconnexion")
    assert c.get("/api/status").status_code == 401

    r = c.post("/api/compte/connexion",
               json={"email": "ALICE.MARTIN@exemple.fr",
                     "mot_de_passe": _INSCRIPTION["mot_de_passe"]})
    assert r.status_code == 200 and r.json()["success"] is True
    assert c.get("/api/status").status_code == 200
    print("  OK — connexion (casse indifférente), déconnexion, session")


def test_mauvais_identifiants_indistinguables():
    """Le message ne doit pas révéler quelles adresses sont inscrites."""
    _isoler()
    c = _app()
    _inscrire(c)
    c.post("/api/compte/deconnexion")

    inconnu = c.post("/api/compte/connexion",
                     json={"email": "personne@exemple.fr", "mot_de_passe": "x"})
    faux_mdp = c.post("/api/compte/connexion",
                      json={"email": "alice.martin@exemple.fr", "mot_de_passe": "x"})
    assert inconnu.status_code == faux_mdp.status_code == 401
    assert inconnu.json()["error"] == faux_mdp.json()["error"]
    print("  OK — e-mail inconnu et mot de passe faux : réponse identique")


def test_force_brute_bloquee():
    _isoler()
    c = _app()
    _inscrire(c)
    c.post("/api/compte/deconnexion")
    codes = [c.post("/api/compte/connexion",
                    json={"email": "alice.martin@exemple.fr",
                          "mot_de_passe": f"faux{i}"}).status_code
             for i in range(8)]
    assert 429 in codes, f"aucun blocage après 8 tentatives : {codes}"
    print("  OK — tentatives répétées bloquées (429)")


def test_session_revocable():
    """Une session doit pouvoir être coupée NET — c'est ce que permet un jeton
    en base, et pas un jeton signé autoporteur."""
    _isoler()
    _app()
    from licence import comptes
    compte = comptes.inscrire(dict(_INSCRIPTION))
    jeton = comptes.ouvrir_session(compte["id"])
    assert comptes.compte_de_session(jeton)["id"] == compte["id"]
    comptes.suspendre(compte["id"], "test")
    assert comptes.compte_de_session(jeton) is None
    print("  OK — suspendre un compte ferme ses sessions ouvertes")


# ═══════════════════════════ FIN D'ESSAI ══════════════════════════════════

def test_essai_ecoule_ferme_l_application():
    _isoler()
    c = _app()
    cid = _inscrire(c).json()["compte"]["id"]
    from licence import comptes
    comptes.depot().modifier(cid, essai_fin=time.time() - 1)

    r = c.get("/api/status")
    assert r.status_code == 402, r.status_code
    d = r.json()
    assert d["etat"] == "TRIAL_EXPIRED"
    assert d["abonnement_requis"] is True and d["compte_suspendu"] is True
    assert c.post("/api/analyser", json=["BTC-USD"]).status_code == 402
    assert c.get("/api/agents").status_code == 402

    # Les deux formules sont proposées, au bon prix.
    formules = d["paiement"]["formules"]
    assert formules["mensuel"]["prix"] == 78.79
    assert formules["annuel"]["prix"] == 849.99
    assert "buy.stripe.com" in formules["mensuel"]["lien_paiement"]
    # Le lien porte le compte : sans cela, Stripe confirmerait un paiement
    # sans qu'on sache à qui ouvrir les droits.
    assert f"client_reference_id={cid}" in formules["mensuel"]["lien_paiement"]

    # Mais l'abonnement reste joignable : c'est le seul moyen de régulariser.
    assert c.get("/api/abonnement").status_code == 200
    print("  OK — essai écoulé : application fermée (402), paiement joignable")


def test_essai_expire_ne_se_recree_pas():
    """Après expiration, se réinscrire avec le même contact est refusé."""
    _isoler()
    c = _app()
    cid = _inscrire(c).json()["compte"]["id"]
    from licence import comptes
    comptes.depot().modifier(cid, essai_fin=time.time() - 1)
    c.post("/api/compte/deconnexion")
    r = _inscrire(c)
    assert r.status_code == 409 and r.json()["code"] == "compte_existant"
    print("  OK — essai écoulé : impossible de repartir sur un nouveau compte")


# ═══════════════════════════ PAIEMENT ═════════════════════════════════════

def test_aucun_paiement_ne_s_auto_declare():
    """Rien de ce qui vient du navigateur ne vaut preuve de paiement."""
    _isoler()
    c = _app()
    cid = _inscrire(c).json()["compte"]["id"]
    from licence import comptes

    # 1. Référence inventée, sans clé Stripe configurée → refus.
    r = c.post("/api/abonnement/verifier", json={"session_id": "cs_test_invente"})
    assert r.status_code == 402 and r.json()["success"] is False

    # 2. Webhook NON signé → refusé, et aucun droit ouvert.
    r = c.post("/api/abonnement/webhook", json={
        "type": "checkout.session.completed",
        "data": {"object": {"id": "cs_x", "client_reference_id": cid,
                            "amount_total": 84999}}})
    assert r.status_code == 400
    assert comptes.depot().par_id(cid)["abonne_jusqua"] in (None, 0)

    # 3. Webhook signé avec un MAUVAIS secret → refusé.
    os.environ["STRIPE_WEBHOOK_SECRET"] = "whsec_le_vrai"
    import hashlib
    import hmac
    charge = json.dumps({"type": "checkout.session.completed",
                         "data": {"object": {"id": "cs_y",
                                             "client_reference_id": cid,
                                             "amount_total": 84999}}}).encode()
    t = str(int(time.time()))
    fausse = hmac.new(b"whsec_un_autre", f"{t}.".encode() + charge,
                      hashlib.sha256).hexdigest()
    r = c.post("/api/abonnement/webhook", content=charge,
               headers={"stripe-signature": f"t={t},v1={fausse}",
                        "content-type": "application/json"})
    assert r.status_code == 400
    assert comptes.depot().par_id(cid)["abonne_jusqua"] in (None, 0)
    print("  OK — aucune activation sans confirmation vérifiée de Stripe")


def test_webhook_signe_active_l_abonnement():
    _isoler()
    c = _app()
    cid = _inscrire(c).json()["compte"]["id"]
    from licence import comptes
    comptes.depot().modifier(cid, essai_fin=time.time() - 1)
    assert c.get("/api/status").status_code == 402       # fermé avant paiement

    import hashlib
    import hmac
    secret = "whsec_secret_de_test"
    os.environ["STRIPE_WEBHOOK_SECRET"] = secret
    charge = json.dumps({
        "type": "checkout.session.completed",
        "data": {"object": {"id": "cs_reel", "client_reference_id": cid,
                            "amount_total": 7879}},      # 78,79 € → mensuel
    }).encode()
    t = str(int(time.time()))
    signature = hmac.new(secret.encode(), f"{t}.".encode() + charge,
                         hashlib.sha256).hexdigest()
    r = c.post("/api/abonnement/webhook", content=charge,
               headers={"stripe-signature": f"t={t},v1={signature}",
                        "content-type": "application/json"})
    assert r.status_code == 200 and r.json()["success"] is True
    assert r.json()["formule"] == "mensuel"

    # L'application se rouvre, en Pro complet.
    r = c.get("/api/status")
    assert r.status_code == 200
    licence = r.json()["licence"]
    assert licence["etat"] == "PRO_ACTIVE" and licence["est_pro"] is True
    assert licence["agents"]["autorises"] == licence["agents"]["total"] == 45
    assert licence["quotas"]["illimite"] is True
    assert c.post("/api/backtest", json={"symbole": "BTC-USD"}).status_code == 200
    print("  OK — webhook Stripe signé : abonnement activé, application rouverte")


def test_formule_deduite_du_montant():
    """C'est le MONTANT PAYÉ qui décide de la formule, pas un paramètre d'URL —
    sinon on choisirait l'annuel en réglant le tarif mensuel."""
    _isoler()
    from licence import stripe_paiement as sp
    assert sp._formule_depuis_montant(7879) == "mensuel"
    assert sp._formule_depuis_montant(84999) == "annuel"
    assert sp._formule_depuis_montant(100) == ""
    assert sp._formule_depuis_montant(None) == ""
    print("  OK — formule déduite du montant réellement encaissé")


def test_rejeu_de_webhook_refuse():
    """Un appel capté puis rejoué plus tard doit être refusé, même signé."""
    _isoler()
    import hashlib
    import hmac
    from licence import stripe_paiement as sp
    secret = "whsec_rejeu"
    os.environ["STRIPE_WEBHOOK_SECRET"] = secret
    charge = b'{"type":"checkout.session.completed"}'
    vieux = str(int(time.time()) - sp.TOLERANCE_WEBHOOK_S - 60)
    signature = hmac.new(secret.encode(), f"{vieux}.".encode() + charge,
                         hashlib.sha256).hexdigest()
    valide, raison = sp.verifier_signature(charge, f"t={vieux},v1={signature}")
    assert valide is False and "expirée" in raison
    print("  OK — webhook trop ancien refusé (anti-rejeu)")


def test_abonnement_expire_referme_l_application():
    _isoler()
    c = _app()
    cid = _inscrire(c).json()["compte"]["id"]
    from licence import comptes
    comptes.activer_abonnement(cid, "mensuel", "cs_ok")
    assert c.get("/api/status").status_code == 200

    comptes.depot().modifier(cid, abonne_jusqua=time.time() - 1,
                             essai_fin=time.time() - 1)
    r = c.get("/api/status")
    assert r.status_code == 402 and r.json()["etat"] == "PRO_EXPIRED"
    print("  OK — abonnement échu : application refermée (PRO_EXPIRED)")


def test_renouvellement_prolonge_sans_perdre_de_jours():
    _isoler()
    _app()
    from licence import comptes
    compte = comptes.inscrire(dict(_INSCRIPTION))
    comptes.activer_abonnement(compte["id"], "mensuel", "cs_1")
    premier = comptes.depot().par_id(compte["id"])["abonne_jusqua"]
    comptes.activer_abonnement(compte["id"], "mensuel", "cs_2")
    second = comptes.depot().par_id(compte["id"])["abonne_jusqua"]
    assert second > premier, "payer en avance a fait perdre des jours"
    print("  OK — un renouvellement prolonge le terme au lieu de l'écraser")


def test_secrets_stripe_jamais_exposes():
    _isoler()
    os.environ["STRIPE_SECRET_KEY"] = "sk_test_ne_doit_pas_sortir"
    os.environ["STRIPE_WEBHOOK_SECRET"] = "whsec_ne_doit_pas_sortir"
    c = _app()
    _inscrire(c)
    for url in ("/api/abonnement", "/api/licence", "/api/compte", "/api/status"):
        brut = c.get(url).text
        assert "sk_test_ne_doit_pas_sortir" not in brut, url
        assert "whsec_ne_doit_pas_sortir" not in brut, url
        assert "STRIPE_SECRET_KEY" not in brut, url
    print("  OK — les secrets Stripe ne sortent par aucune route")


def test_liens_de_paiement_surchargeables_et_valides():
    """Passer en production ne doit pas exiger de recompiler — ni permettre
    d'envoyer un client payer ailleurs que chez Stripe."""
    import importlib
    ancien = {k: os.environ.get(k) for k in ("STRIPE_LIEN_MENSUEL", "STRIPE_LIEN_ANNUEL")}
    try:
        import licence.config as lc

        # 1. Sans variable : les liens du fichier de configuration.
        for k in ancien:
            os.environ.pop(k, None)
        importlib.reload(lc)
        assert lc.FORMULES["mensuel"]["lien_paiement"].startswith(
            lc.PREFIXE_LIEN_STRIPE), "lien par défaut inattendu"

        # 2. Avec variables : liens de production, sans recompilation.
        os.environ["STRIPE_LIEN_MENSUEL"] = "https://buy.stripe.com/4gwPRODM"
        os.environ["STRIPE_LIEN_ANNUEL"] = "https://buy.stripe.com/8xyPRODA"
        importlib.reload(lc)
        assert lc.FORMULES["mensuel"]["lien_paiement"].endswith("4gwPRODM")
        assert not lc.lien_de_test(lc.FORMULES["annuel"]["lien_paiement"])

        # 3. Lien hostile : REFUSÉ, retour au défaut. Une variable
        #    d'environnement détournée ne doit pas pouvoir rediriger un
        #    paiement vers un site tiers.
        for hostile in ("https://paiement-pirate.example/voler",
                        "http://buy.stripe.com/pasdehttps",
                        "https://buy.stripe.com.pirate.example/x"):
            os.environ["STRIPE_LIEN_MENSUEL"] = hostile
            importlib.reload(lc)
            assert lc.FORMULES["mensuel"]["lien_paiement"].startswith(
                lc.PREFIXE_LIEN_STRIPE), f"lien hostile accepté : {hostile}"
            assert hostile not in lc.FORMULES["mensuel"]["lien_paiement"]
    finally:
        for k, v in ancien.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        import licence.config as lc
        importlib.reload(lc)
    print("  OK — liens surchargeables, tout lien hors buy.stripe.com refusé")


def test_incoherence_cle_et_liens_detectee():
    """Le mélange clé / liens est SILENCIEUX et coûteux : il faut le dire.

    Une clé de test avec des liens de production fait payer le client
    réellement, sans jamais confirmer le paiement : son compte reste suspendu
    alors qu'il a été débité.
    """
    import importlib
    sauve = {k: os.environ.get(k) for k in
             ("STRIPE_SECRET_KEY", "STRIPE_LIEN_MENSUEL", "STRIPE_LIEN_ANNUEL")}
    LIVE = {"STRIPE_LIEN_MENSUEL": "https://buy.stripe.com/4gwPRODM",
            "STRIPE_LIEN_ANNUEL": "https://buy.stripe.com/8xyPRODA"}
    # Les liens LIVRÉS sont désormais ceux de production : pour éprouver le cas
    # « test + test », il faut donc poser explicitement des liens de test.
    TEST = {"STRIPE_LIEN_MENSUEL": "https://buy.stripe.com/test_M",
            "STRIPE_LIEN_ANNUEL": "https://buy.stripe.com/test_A"}
    try:
        import licence.config as lc
        import licence.stripe_paiement as sp

        def alerte(env):
            for k in sauve:
                os.environ.pop(k, None)
            os.environ.update(env)
            importlib.reload(lc)
            importlib.reload(sp)
            return sp.incoherence_environnement()

        assert alerte({"STRIPE_SECRET_KEY": "sk_test_x", **TEST}) == "", \
            "test + test ne devrait pas alerter"
        assert alerte({"STRIPE_SECRET_KEY": "sk_live_x", **LIVE}) == "", \
            "live + live ne devrait pas alerter"

        grave = alerte({"STRIPE_SECRET_KEY": "sk_test_x", **LIVE})
        assert "DANGER" in grave and "suspendu" in grave, grave

        manque = alerte({"STRIPE_SECRET_KEY": "sk_live_x", **TEST})
        assert "TEST" in manque and "encaiss" in manque, manque

        melange = alerte({"STRIPE_SECRET_KEY": "sk_live_x",
                          "STRIPE_LIEN_MENSUEL": LIVE["STRIPE_LIEN_MENSUEL"],
                          "STRIPE_LIEN_ANNUEL": TEST["STRIPE_LIEN_ANNUEL"]})
        assert "mélangent" in melange, melange

        # Sans clé configurée, aucune alerte : rien n'est encore branché.
        assert alerte({}) == ""

        # Les liens LIVRÉS sont ceux de production : les utiliser avec une clé
        # de test est le cas dangereux, il doit être signalé sans rien poser
        # d'autre que la clé.
        livres = alerte({"STRIPE_SECRET_KEY": "sk_test_x"})
        assert "DANGER" in livres, (
            "clé de test + liens de production livrés : aucune alerte")
    finally:
        for k, v in sauve.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        import licence.config as lc
        import licence.stripe_paiement as sp
        importlib.reload(lc)
        importlib.reload(sp)
    print("  OK — clé et liens désaccordés : détecté et expliqué")


def test_version_4_1_0_coherente():
    """La version et sa note doivent exister et concorder."""
    version = (_RACINE / "VERSION").read_text(encoding="utf-8").strip()
    assert version == "4.1.0", f"VERSION vaut {version!r}"

    note = _RACINE / f"RELEASE_NOTES_{version}.md"
    assert note.is_file(), f"note de version manquante : {note.name}"
    texte = note.read_text(encoding="utf-8")
    assert version in texte.splitlines()[0], "le titre ne porte pas la version"

    # La note doit annoncer ce que cette version change réellement.
    for element in ("3 jours", "78,79", "849,99", "PBKDF2", "carte bancaire",
                    "Stripe", "IPv4"):
        assert element in texte, f"la note ne mentionne pas « {element} »"

    # Et les tarifs annoncés doivent être ceux appliqués.
    from licence import config as lconfig
    assert f"{lconfig.FORMULES['mensuel']['prix']:.2f}".replace(".", ",") in texte
    assert f"{lconfig.FORMULES['annuel']['prix']:.2f}".replace(".", ",") in texte
    print(f"  OK — version {version}, note présente et cohérente avec les tarifs")


def test_liens_de_paiement_exacts():
    """Les deux liens fournis, à la lettre."""
    from licence import config as lconfig
    assert lconfig.FORMULES["mensuel"]["lien_paiement"] == \
        "https://buy.stripe.com/00w00l1Qn1hI8x4cgI4wM03"
    assert lconfig.FORMULES["annuel"]["lien_paiement"] == \
        "https://buy.stripe.com/7sY9AVamT0dE00yeoQ4wM04"
    assert lconfig.FORMULES["mensuel"]["prix"] == 78.79
    assert lconfig.FORMULES["annuel"]["prix"] == 849.99

    # Liens de PRODUCTION : plus aucun « /test_ ». Un retour en arrière
    # involontaire ferait cliquer les clients sur un lien qui n'encaisse rien.
    for f in lconfig.FORMULES.values():
        assert not lconfig.lien_de_test(f["lien_paiement"]), \
            f"la formule « {f['id']} » pointe encore vers l'environnement de test"

    # Et les deux formules doivent rester DISTINCTES : un copier-coller qui
    # duplique un lien ferait facturer le même montant dans les deux cas.
    liens = {f["lien_paiement"] for f in lconfig.FORMULES.values()}
    assert len(liens) == len(lconfig.FORMULES), "deux formules partagent un lien"
    print("  OK — liens Stripe et tarifs conformes")


def test_cookie_secure_selon_le_transport():
    """Le cookie de session ne doit jamais circuler en clair sur un serveur
    public — ni bloquer la connexion sur le réseau local en HTTP."""
    _isoler()
    import sys
    sys.path.insert(0, str(_RACINE))
    from fastapi.testclient import TestClient
    from backend.main import app

    compteur = itertools.count(1)

    def cookie(schema, forcage):
        if forcage is None:
            os.environ.pop("AGENCE_COOKIE_SECURE", None)
        else:
            os.environ["AGENCE_COOKIE_SECURE"] = forcage
        c = TestClient(app, base_url=f"{schema}://testserver",
                       raise_server_exceptions=False)
        # Contact unique à CHAQUE appel, même pour un couple (schéma, forçage)
        # déjà vu : une seconde inscription avec le même contact est refusée
        # (409) et ne pose aucun cookie — le test mesurerait alors une chaîne
        # vide au lieu de l'en-tête.
        n = next(compteur)
        corps = dict(_INSCRIPTION)
        corps["email"] = f"cookie-{n}@exemple.fr"
        corps["telephone"] = f"07{n:08d}"
        reponse = c.post("/api/compte/inscription", json=corps)
        assert reponse.status_code == 200, reponse.text
        return reponse.headers.get("set-cookie", "")

    try:
        assert "Secure" not in cookie("http", None), \
            "Secure posé en HTTP : la connexion serait impossible sur le réseau local"
        assert "Secure" in cookie("https", None), \
            "Secure absent en HTTPS : le jeton de session pourrait fuiter en clair"
        assert "Secure" in cookie("http", "1"), "forçage à 1 ignoré"
        assert "Secure" not in cookie("https", "0"), "forçage à 0 ignoré"
        # Et le reste des protections tient dans tous les cas.
        entete = cookie("https", None)
        assert "HttpOnly" in entete and "SameSite=lax" in entete.replace("Lax", "lax")
    finally:
        os.environ.pop("AGENCE_COOKIE_SECURE", None)
    print("  OK — cookie Secure : auto selon le transport, forçable, HttpOnly")


def test_origines_cors_restreignables():
    """Sur un serveur public, on doit pouvoir limiter les origines."""
    import backend.main as bm
    try:
        os.environ.pop("AGENCE_CORS_ORIGINS", None)
        assert bm._origines_autorisees() == ["*"], "le défaut réseau local a changé"
        os.environ["AGENCE_CORS_ORIGINS"] = "https://a.fr, https://b.fr"
        assert bm._origines_autorisees() == ["https://a.fr", "https://b.fr"]
    finally:
        os.environ.pop("AGENCE_CORS_ORIGINS", None)
    print("  OK — origines CORS restreignables par variable d'environnement")


def test_guide_de_deploiement_complet():
    """Le guide doit nommer TOUT ce sans quoi un paiement n'aboutit pas."""
    guide = (_RACINE / "DEPLOIEMENT.md").read_text(encoding="utf-8")
    for element in ("STRIPE_SECRET_KEY", "STRIPE_WEBHOOK_SECRET",
                    "/api/abonnement/webhook",
                    "session_id={CHECKOUT_SESSION_ID}",
                    "checkout.session.completed", "invoice.paid",
                    "AGENCE_COOKIE_SECURE", "--proxy-headers",
                    "X-Forwarded-Proto", "proxy_read_timeout",
                    "python/licence/config.py"):
        assert element in guide, f"DEPLOIEMENT.md ne mentionne pas : {element}"
    # Et il doit avertir du partage de l'état de trading.
    assert "singletons" in guide.lower() or "PARTAGÉ" in guide

    # Les deux scripts qu'il cite doivent exister et être exécutables.
    for nom in ("configurer-serveur.sh", "verifier-serveur.sh"):
        script = _RACINE / "scripts" / nom
        assert script.is_file(), f"scripts/{nom} manquant"
        assert nom in guide, f"DEPLOIEMENT.md ne cite pas scripts/{nom}"
        texte = script.read_text(encoding="utf-8")
        # Un script de configuration ne doit contenir AUCUN secret en dur :
        # il les demande, il ne les porte pas.
        import re
        assert not re.search(r"sk_(live|test)_[A-Za-z0-9]{10,}", texte), \
            f"{nom} contient une clé Stripe"
        assert not re.search(r"whsec_[A-Za-z0-9]{16,}", texte), \
            f"{nom} contient un secret de webhook"
    # Le fichier produit doit être en 600 : il porte les secrets.
    conf = (_RACINE / "scripts" / "configurer-serveur.sh").read_text(encoding="utf-8")
    assert "chmod 600" in conf and "umask 077" in conf, \
        "le script n'assure pas la confidentialité du .env qu'il écrit"
    print("  OK — guide complet, scripts présents et sans secret en dur")


def test_interface_expose_inscription_et_formules():
    frontend = _RACINE / "frontend"
    compte_js = (frontend / "js" / "compte.js").read_text(encoding="utf-8")
    index = (frontend / "index.html").read_text(encoding="utf-8")
    assert 'src="js/compte.js"' in index
    assert 'id="cpt-bandeau"' in index
    for texte in ("Créer un compte", "Se connecter", "Mot de passe",
                  "Téléphone", "Adresse postale", "Code postal", "Ville",
                  "S'abonner", "essai gratuit"):
        assert texte in compte_js, f"texte absent de l'interface : « {texte} »"

    # Les TARIFS ne sont pas écrits en dur dans l'interface : elle les affiche
    # depuis la réponse du serveur (`f.prix`, mise en forme par `euros`).
    # C'est ce qui garantit que l'écran ne peut pas annoncer un prix différent
    # de celui que Stripe encaisse — et que changer un tarif dans
    # licence/config.py suffit.
    assert "euros(f.prix)" in compte_js
    assert "f.lien_paiement" in compte_js
    assert "78.79" not in compte_js and "849.99" not in compte_js, (
        "un tarif est codé en dur dans l'interface : il pourrait diverger "
        "du montant réellement facturé")

    # La durée de l'essai vient elle aussi du serveur, avec un repli neutre.
    assert "etat.essai.jours" in compte_js
    # L'interface ne doit jamais demander de carte.
    for interdit in ("numero_carte", "cardnumber", "Numéro de carte", "CVC"):
        assert interdit not in compte_js, f"l'interface demande une carte : {interdit}"
    print("  OK — écran d'inscription, formules affichées, aucune carte demandée")


def run():
    print("═══ Comptes, essai de 3 jours et abonnement Stripe ═══")
    from utils import app_config
    import config as app_cfg
    _etat0 = (app_config._PATH, app_config._cache, app_cfg.DB_PATH,
              os.environ.get("AGENCE_DATA_DIR"),
              os.environ.get("STRIPE_SECRET_KEY"),
              os.environ.get("STRIPE_WEBHOOK_SECRET"),
              os.environ.get("AGENCE_LICENCE_JETON"))
    try:
        test_sans_compte_application_fermee()
        test_routes_d_inscription_restent_ouvertes()
        test_aucune_page_ne_renvoie_du_json_brut()
        test_ecran_de_premier_lancement_accessible()
        test_toutes_les_routes_de_l_ecran_de_lancement_repondent()
        test_etat_du_moteur_ia_exploitable_par_la_page()
        test_aucune_boucle_de_redirection_sur_401()
        test_ecran_d_accueil_ne_ment_pas_sur_le_backend()
        test_formulaire_d_inscription_ne_se_redessine_pas()
        test_inscription_ouvre_trois_jours()
        test_champs_obligatoires()
        test_contact_deja_enregistre_refuse()
        test_aucune_carte_bancaire_acceptee()
        test_mot_de_passe_jamais_en_clair()
        test_connexion_et_deconnexion()
        test_mauvais_identifiants_indistinguables()
        test_force_brute_bloquee()
        test_session_revocable()
        test_essai_ecoule_ferme_l_application()
        test_essai_expire_ne_se_recree_pas()
        test_aucun_paiement_ne_s_auto_declare()
        test_webhook_signe_active_l_abonnement()
        test_formule_deduite_du_montant()
        test_rejeu_de_webhook_refuse()
        test_abonnement_expire_referme_l_application()
        test_renouvellement_prolonge_sans_perdre_de_jours()
        test_secrets_stripe_jamais_exposes()
        test_liens_de_paiement_exacts()
        test_liens_de_paiement_surchargeables_et_valides()
        test_incoherence_cle_et_liens_detectee()
        test_version_4_1_0_coherente()
        test_cookie_secure_selon_le_transport()
        test_origines_cors_restreignables()
        test_guide_de_deploiement_complet()
        test_interface_expose_inscription_et_formules()
    finally:
        import importlib
        (app_config._PATH, app_config._cache, app_cfg.DB_PATH,
         _data, _sk, _wh, _jeton) = _etat0
        for nom, valeur in (("AGENCE_DATA_DIR", _data),
                            ("STRIPE_SECRET_KEY", _sk),
                            ("STRIPE_WEBHOOK_SECRET", _wh),
                            ("AGENCE_LICENCE_JETON", _jeton)):
            if valeur is None:
                os.environ.pop(nom, None)
            else:
                os.environ[nom] = valeur
        import utils.database as database
        importlib.reload(database)
        from licence import abonnement, comptes
        abonnement.invalider_cache()
        comptes.definir_compte_courant(None)


if __name__ == "__main__":
    run()
    print("✅ Comptes OK")
