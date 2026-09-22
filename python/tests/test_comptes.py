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


def test_liens_de_paiement_exacts():
    """Les deux liens fournis, à la lettre."""
    from licence import config as lconfig
    assert lconfig.FORMULES["mensuel"]["lien_paiement"] == \
        "https://buy.stripe.com/test_aFaaEX7ol6Nc9ZsewvdZ601"
    assert lconfig.FORMULES["annuel"]["lien_paiement"] == \
        "https://buy.stripe.com/test_00w8wP3851sS0oS1JJdZ602"
    assert lconfig.FORMULES["mensuel"]["prix"] == 78.79
    assert lconfig.FORMULES["annuel"]["prix"] == 849.99
    print("  OK — liens Stripe et tarifs conformes")


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
