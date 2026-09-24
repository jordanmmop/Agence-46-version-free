"""Clés d'abonnement : émission au paiement, remise à l'abonné, activation Pro.

Ce que la suite vérifie :

- un paiement confirmé ÉMET une clé et la remet à son titulaire ;
- le même paiement annoncé deux fois (webhook + retour navigateur) n'émet
  QU'UNE clé — sinon l'abonné reçoit deux SMS et ne sait plus laquelle garder ;
- la clé n'est jamais stockée en clair, jamais journalisée, jamais renvoyée à
  qui n'est pas le compte payeur ;
- elle active réellement la version Pro, et le jeton produit est SIGNÉ ;
- une clé inventée, révoquée, ou dont l'abonnement est terminé, est refusée ;
- l'abonné peut en redemander une, l'ancienne cessant alors de fonctionner ;
- un compte en essai ne peut pas s'en faire remettre.
"""
import _setup  # noqa: F401
import hashlib
import hmac
import json
import os
import tempfile
import time
from pathlib import Path

_RACINE = Path(__file__).resolve().parents[2]

_INSCRIPTION = {
    "email": "Bruno.Petit@Exemple.fr",
    "mot_de_passe": "un-mot-de-passe-solide",
    "telephone": "06 55 44 33 22",
    "adresse": "3 rue des Clés",
    "code_postal": "69001",
    "ville": "Lyon",
    "pays": "France",
}

# Canaux d'envoi interceptés : la suite ne doit envoyer NI e-mail NI SMS réel.
_envois = []

# Fonctions d'envoi d'origine, remises en place par `_isoler()`. Sans ce
# rétablissement, un test qui remplace les canaux fausserait tous les suivants
# — et le test « aucun canal configuré » mesurerait son propre bouchon.
_origines = []


def _isoler():
    """Base, configuration et émetteur de licences dans un dossier temporaire."""
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

    from licence import abonnement, comptes, notifications
    if _origines:
        notifications.envoyer_email, notifications.envoyer_sms = _origines
    else:
        _origines.extend((notifications.envoyer_email, notifications.envoyer_sms))

    for nom in ("AGENCE_LICENCE_JETON", "AGENCE_LICENCE_PUBKEY",
                "AGENCE_LICENCE_PRIVKEY", "STRIPE_SECRET_KEY",
                "STRIPE_WEBHOOK_SECRET"):
        os.environ.pop(nom, None)
    abonnement.invalider_cache()
    comptes.definir_compte_courant(None)
    comptes.reinitialiser_echecs()
    _envois.clear()
    return dossier


def _neutraliser_envois():
    """Remplace les deux sorties réseau par des enregistreurs.

    On ne remplace PAS `envoyer_cle` : c'est justement la fonction à
    vérifier (destinataires tirés du compte, message, canaux retenus).
    """
    from licence import notifications
    notifications.envoyer_email = lambda dest, sujet, corps: (
        _envois.append(("email", dest, corps)) or (True, f"E-mail envoyé à {dest}"))
    notifications.envoyer_sms = lambda dest, texte: (
        _envois.append(("sms", dest, texte)) or (True, f"SMS envoyé au {dest}"))


def _app():
    import sys
    sys.path.insert(0, str(_RACINE))
    from fastapi.testclient import TestClient
    from backend.main import app
    return TestClient(app, raise_server_exceptions=False)


def _inscrire(client, **surcharges):
    return client.post("/api/compte/inscription", json={**_INSCRIPTION, **surcharges})


def _webhook(client, compte_id, reference="cs_paiement", montant=84999):
    secret = "whsec_secret_de_test"
    os.environ["STRIPE_WEBHOOK_SECRET"] = secret
    charge = json.dumps({
        "type": "checkout.session.completed",
        "data": {"object": {"id": reference, "client_reference_id": compte_id,
                            "amount_total": montant}},
    }).encode()
    t = str(int(time.time()))
    signature = hmac.new(secret.encode(), f"{t}.".encode() + charge,
                         hashlib.sha256).hexdigest()
    return client.post("/api/abonnement/webhook", content=charge,
                       headers={"stripe-signature": f"t={t},v1={signature}",
                                "content-type": "application/json"})


# ═══════════════════════════ FORMAT DE LA CLÉ ═════════════════════════════

def test_format_de_cle_lisible_et_tolerant():
    """Une clé doit survivre au trajet SMS → œil → clavier."""
    from licence import cles

    cle = cles.generer()
    assert cle.startswith("AGF-"), cle
    assert len(cle) == len("AGF-XXXXX-XXXXX-XXXXX"), cle
    assert cles.normaliser(cle) == cle

    # Aucun caractère ambigu dans l'alphabet : ni I, ni L, ni O, ni U.
    for interdit in "ILOU":
        assert interdit not in cles.ALPHABET

    # Recopiée à la main, de toutes les façons possibles : même clé.
    corps = cle[4:].replace("-", "")
    for variante in (cle.lower(), corps, f"  {cle}  ", cle.replace("-", " "),
                     f"AGF{corps}", corps.replace("0", "O").replace("1", "I")):
        assert cles.normaliser(variante) == cle, variante

    # Ce qui n'est pas une clé ne devient jamais une clé.
    for absurde in ("", "bonjour", "AGF-123", cle + "X", cle[:-1], None):
        assert cles.normaliser(absurde) == ""

    # Deux clés tirées coup sur coup ne se ressemblent pas.
    assert len({cles.generer() for _ in range(200)}) == 200
    print("  OK — format AGF-XXXXX-XXXXX-XXXXX, sans caractère ambigu")


def test_cle_jamais_stockee_en_clair():
    """Une clé ouvre des droits : la base n'en garde que l'empreinte."""
    _isoler()
    _neutraliser_envois()
    from licence import cles, comptes

    compte = comptes.inscrire(_INSCRIPTION)
    comptes.activer_abonnement(compte["id"], "annuel", "cs_x")
    cle = cles.emettre(compte["id"], "annuel", "cs_x")["cle"]

    lignes = comptes.depot().cles_du_compte(compte["id"])
    assert len(lignes) == 1
    ligne = lignes[0]
    corps = cles.normaliser(cle).replace("AGF-", "").replace("-", "")
    for valeur in ligne.values():
        assert corps not in str(valeur), "la clé en clair ne doit jamais être stockée"
    assert ligne["cle_hash"] == hashlib.sha256(cle.encode()).hexdigest()

    # Seuls les cinq derniers caractères servent de repère visuel.
    assert ligne["indice"] == cle[-5:]
    apercu = cles.cles_du_compte(compte["id"])[0]["apercu"]
    assert apercu.endswith(cle[-5:]) and "•" in apercu and corps not in apercu
    print("  OK — empreinte SHA-256 en base, jamais la clé")


# ═══════════════════════════ ÉMISSION AU PAIEMENT ═════════════════════════

def test_paiement_confirme_emet_et_envoie_la_cle():
    """LE point de départ : payer doit faire arriver une clé chez l'abonné."""
    _isoler()
    _neutraliser_envois()
    c = _app()
    compte = _inscrire(c).json()["compte"]
    cid = compte["id"]

    r = _webhook(c, cid, "cs_annuel", 84999)
    assert r.status_code == 200 and r.json()["success"] is True
    assert r.json()["formule"] == "annuel"
    assert r.json()["cle_emise"] is True
    # La clé ne part JAMAIS dans la réponse au webhook : elle part chez Stripe.
    assert "cle" not in r.json()

    # L'envoi du webhook est différé dans un fil : on l'attend brièvement.
    for _ in range(50):
        if len(_envois) >= 2:
            break
        time.sleep(0.05)

    canaux = {canal for canal, _, _ in _envois}
    assert canaux == {"email", "sms"}, f"canaux utilisés : {canaux}"

    # Destinataires : ceux du COMPTE, normalisés — jamais une valeur fournie
    # par l'appelant.
    destinataires = {canal: dest for canal, dest, _ in _envois}
    assert destinataires["email"] == "bruno.petit@exemple.fr"
    assert destinataires["sms"].endswith("655443322")

    # Le message porte une clé du bon format, et la consigne pour s'en servir.
    from licence import cles
    for _, _, corps in _envois:
        trouvees = [m for m in corps.split() if m.startswith("AGF-")]
        assert trouvees, corps
        assert cles.normaliser(trouvees[0].strip(".")), trouvees[0]
    print("  OK — paiement confirmé : clé émise et remise par e-mail ET SMS")


def test_un_seul_paiement_une_seule_cle():
    """Webhook PUIS retour du navigateur : un seul règlement, une seule clé.

    Sans cette garde, l'abonné reçoit deux clés pour un paiement et ne sait
    plus laquelle conserver — et chaque envoi supplémentaire est un SMS facturé.
    """
    _isoler()
    _neutraliser_envois()
    from licence import comptes, stripe_paiement

    compte = comptes.inscrire(_INSCRIPTION)
    for _ in range(3):
        compte_frais = comptes.activer_abonnement(compte["id"], "mensuel", "cs_unique")
        stripe_paiement.remettre_cle(compte_frais, "mensuel", "cs_unique")

    lignes = comptes.depot().cles_du_compte(compte["id"])
    assert len(lignes) == 1, f"{len(lignes)} clés émises pour un seul paiement"
    assert len(_envois) == 2, f"{len(_envois)} envois pour un seul paiement"

    # Un paiement DIFFÉRENT — le renouvellement du mois suivant — en émet une.
    compte_frais = comptes.activer_abonnement(compte["id"], "mensuel", "cs_suivant")
    stripe_paiement.remettre_cle(compte_frais, "mensuel", "cs_suivant")
    assert len(comptes.depot().cles_du_compte(compte["id"])) == 2
    print("  OK — une clé par paiement, pas une par notification")


def test_panne_d_envoi_n_annule_pas_l_abonnement():
    """SMTP en panne : le paiement reste encaissé et les droits ouverts."""
    _isoler()
    from licence import comptes, notifications, stripe_paiement

    def _casse(*a, **k):
        raise OSError("serveur SMTP injoignable")

    notifications.envoyer_email = _casse
    notifications.envoyer_sms = _casse
    try:
        compte = comptes.inscrire(_INSCRIPTION)
        compte = comptes.activer_abonnement(compte["id"], "annuel", "cs_panne")
        resultat = stripe_paiement.remettre_cle(compte, "annuel", "cs_panne")
    finally:
        _neutraliser_envois()

    # La clé est émise et RENDUE à l'appelant : c'est elle que la page de
    # retour affiche quand aucun canal ne fonctionne.
    assert resultat["cle_emise"] is True and resultat["cle"].startswith("AGF-")
    assert "aucun envoi automatique" in resultat["cle_message"].lower()
    assert comptes.etat_du_compte(comptes.depot().par_id(compte["id"])).est_pro
    print("  OK — panne d'envoi : abonnement intact, clé affichée à l'écran")


def test_relais_sans_authentification_accepte():
    """Régression : un relais SMTP interne sans mot de passe était refusé.

    `login()` était appelé dès qu'un identifiant existait. Un relais qui
    n'annonce pas l'extension AUTH répondait alors « SMTP AUTH extension not
    supported by server » — un message qui ne dit pas que la faute est
    d'avoir voulu s'authentifier. Découvert en configurant un vrai serveur.
    """
    _isoler()
    import smtplib
    from licence import notifications

    class _FauxSMTP:
        """Relais minimal : refuse l'authentification, accepte le message."""
        def __init__(self, hote, port, timeout=None):
            self.connexions = (hote, port)
            _FauxSMTP.logins = 0
            _FauxSMTP.envois = 0
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def starttls(self): pass
        def login(self, utilisateur, mot_de_passe):
            _FauxSMTP.logins += 1
            raise smtplib.SMTPNotSupportedError("SMTP AUTH extension not supported")
        def send_message(self, message): _FauxSMTP.envois += 1

    origine = smtplib.SMTP
    smtplib.SMTP = _FauxSMTP
    os.environ.update({"SMTP_HOTE": "relais.interne", "SMTP_PORT": "25",
                       "SMTP_SECURITE": "aucune",
                       "SMTP_UTILISATEUR": "no-reply@interne",
                       "SMTP_EXPEDITEUR": "no-reply@interne"})
    os.environ.pop("SMTP_MOTDEPASSE", None)
    try:
        ok, detail = notifications.envoyer_email("client@exemple.fr", "s", "corps")
        assert ok is True, detail
        assert _FauxSMTP.logins == 0, "sans mot de passe, aucune authentification"
        assert _FauxSMTP.envois == 1

        # Avec un mot de passe, l'authentification a bien lieu.
        os.environ["SMTP_MOTDEPASSE"] = "secret"
        ok, detail = notifications.envoyer_email("client@exemple.fr", "s", "corps")
        assert _FauxSMTP.logins == 1 and ok is False
        assert "impossible" in detail.lower()
    finally:
        smtplib.SMTP = origine
        for nom in ("SMTP_HOTE", "SMTP_PORT", "SMTP_SECURITE", "SMTP_UTILISATEUR",
                    "SMTP_EXPEDITEUR", "SMTP_MOTDEPASSE"):
            os.environ.pop(nom, None)

    # Un port illisible ne doit pas non plus remonter jusqu'au paiement.
    os.environ.update({"SMTP_HOTE": "relais.interne", "SMTP_PORT": "abc"})
    try:
        ok, detail = notifications.envoyer_email("client@exemple.fr", "s", "corps")
        assert ok is False and "expéditeur" in detail.lower()
    finally:
        os.environ.pop("SMTP_HOTE", None)
        os.environ.pop("SMTP_PORT", None)
    print("  OK — relais sans authentification accepté, port illisible sans casse")


def test_script_de_configuration_complete_sans_ecraser():
    """`--envoi` doit ajouter l'envoi SANS faire ressaisir les clés Stripe."""
    script = (_RACINE / "scripts" / "configurer-serveur.sh").read_text(encoding="utf-8")
    assert "--envoi" in script
    # Le mode ciblé modifie ligne à ligne ; il ne réécrit jamais tout le fichier.
    assert "definir_variable()" in script
    assert 'cp -p "$ENV_FICHIER" "$SAUVEGARDE"' in script
    # Les secrets se saisissent en aveugle.
    for variable in ("SMTP_MOTDEPASSE", "OVH_APPLICATION_SECRET",
                     "TWILIO_AUTH_TOKEN", "SMS_AUTORISATION"):
        assert f'read -r -s -p' in script, "les secrets doivent être saisis en aveugle"
        assert variable in script, f"le script n'expose pas {variable}"
    # Le fichier temporaire du remplacement ne doit jamais être lisible.
    assert 'chmod 600 "$tmp"' in script
    assert "umask 077" in script
    # Et le script doit proposer de VÉRIFIER, pas seulement d'écrire.
    assert "tester_envoi" in script
    print("  OK — configurer-serveur.sh --envoi complète sans écraser")


def test_sans_canal_configure_rien_n_est_annonce_comme_envoye():
    """Aucun SMTP, aucune passerelle : le module le DIT, il ne prétend rien."""
    _isoler()
    from licence import notifications
    for nom in ("SMTP_HOTE", "SMS_FOURNISSEUR", "SMS_URL",
                "TWILIO_ACCOUNT_SID", "OVH_APPLICATION_KEY"):
        os.environ.pop(nom, None)

    assert notifications.email_configure() is False
    assert notifications.sms_configure() is False
    ok, detail = notifications.envoyer_email("a@b.fr", "x", "y")
    assert ok is False and "SMTP" in detail
    ok, detail = notifications.envoyer_sms("+33600000000", "x")
    assert ok is False and "passerelle" in detail.lower()

    resultat = notifications.envoyer_cle(
        {"id": "c", "email": "a@b.fr", "telephone": "+33600000000"}, "AGF-XXXXX")
    assert resultat["email"] is False and resultat["sms"] is False
    assert resultat["cle_remise"] is False and resultat["canaux"] == ""
    print("  OK — sans canal configuré, aucun envoi n'est annoncé")


# ═══════════════════════════ ACTIVATION PAR LA CLÉ ════════════════════════

def test_la_cle_active_reellement_la_version_pro():
    """La promesse faite à l'abonné : coller la clé débloque l'application."""
    _isoler()
    _neutraliser_envois()
    from licence import abonnement, cles, comptes, verification
    from licence.etat import EtatLicence

    compte = comptes.inscrire(_INSCRIPTION)
    compte = comptes.activer_abonnement(compte["id"], "annuel", "cs_pro")
    cle = cles.emettre(compte["id"], "annuel", "cs_pro",
                       compte["abonne_jusqua"])["cle"]

    # Personne n'est connecté : c'est la clé SEULE qui doit ouvrir les droits.
    comptes.definir_compte_courant(None)
    abonnement.invalider_cache()
    assert abonnement.etat() is EtatLicence.COMPTE_REQUIS

    resultat = abonnement.activer(cle)
    assert resultat["success"] is True, resultat
    assert abonnement.est_pro() is True

    # Le jeton enregistré est un vrai jeton SIGNÉ, pas un drapeau.
    jeton = abonnement._jeton_enregistre()
    assert jeton.startswith("AGENCE1.")
    etat_jeton, infos = verification.lire_jeton(jeton)
    assert etat_jeton is EtatLicence.PRO_ACTIVE
    assert infos["expire_le"] == int(compte["abonne_jusqua"])

    # Une signature retouchée d'un seul caractère est refusée.
    prefixe, charge, signature = jeton.split(".")
    altere = f"{prefixe}.{charge}.{'B' if signature[0] != 'B' else 'C'}{signature[1:]}"
    assert verification.lire_jeton(altere)[0] is not EtatLicence.PRO_ACTIVE
    print("  OK — la clé reçue active Pro, par jeton signé Ed25519")


def test_cle_inventee_ou_perimee_refusee():
    """Aucune clé ne s'auto-valide : inventée, révoquée ou périmée, elle échoue."""
    _isoler()
    _neutraliser_envois()
    from licence import abonnement, cles, comptes

    compte = comptes.inscrire(_INSCRIPTION)
    compte = comptes.activer_abonnement(compte["id"], "mensuel", "cs_ref")
    cle = cles.emettre(compte["id"], "mensuel", "cs_ref",
                       compte["abonne_jusqua"])["cle"]
    comptes.definir_compte_courant(None)

    # Une clé au bon FORMAT mais jamais émise.
    inventee = cles.generer()
    assert cles.valider(inventee)["valide"] is False
    assert abonnement.activer(inventee)["success"] is False
    assert abonnement.est_pro() is False

    # Une clé valide dont l'abonnement est terminé.
    comptes.depot().modifier(compte["id"], abonne_jusqua=time.time() - 1)
    verdict = cles.valider(cle)
    assert verdict["valide"] is False and "plus actif" in verdict["erreur"]
    assert abonnement.activer(cle)["success"] is False
    assert abonnement.est_pro() is False
    print("  OK — clé inventée ou abonnement terminé : refus")


def test_reemission_revoque_la_precedente():
    """Redemander une clé remplace l'ancienne : celle qui traîne cesse d'agir."""
    _isoler()
    _neutraliser_envois()
    from licence import cles, comptes

    compte = comptes.inscrire(_INSCRIPTION)
    compte = comptes.activer_abonnement(compte["id"], "annuel", "cs_a")
    ancienne = cles.emettre(compte["id"], "annuel", "cs_a",
                            compte["abonne_jusqua"])["cle"]
    assert cles.valider(ancienne)["valide"] is True

    nouvelle = cles.remplacer(compte["id"], "annuel", compte["abonne_jusqua"])["cle"]
    assert nouvelle != ancienne
    assert cles.valider(nouvelle)["valide"] is True

    verdict = cles.valider(ancienne)
    assert verdict["valide"] is False and "remplac" in verdict["erreur"]
    print("  OK — réémission : l'ancienne clé est révoquée")


# ═══════════════════════════ ROUTES HTTP ══════════════════════════════════

def test_la_cle_ne_sort_pas_vers_un_tiers():
    """La référence Stripe circule dans une URL : elle ne doit pas suffire.

    Sans compte connecté, `/verifier` confirme le paiement mais ne livre pas
    la clé — sinon un identifiant de session retrouvé dans un historique
    donnerait l'abonnement d'autrui.
    """
    _isoler()
    _neutraliser_envois()
    c = _app()
    cid = _inscrire(c).json()["compte"]["id"]

    from licence import comptes, stripe_paiement
    reference = "cs_tiers"
    compte = comptes.activer_abonnement(cid, "mensuel", reference)
    remise = stripe_paiement.remettre_cle(compte, "mensuel", reference)
    assert remise["cle"].startswith("AGF-")

    # Stripe confirmerait ce règlement : on remplace la seule sortie réseau,
    # pas la logique de protection qu'on veut mesurer.
    origine = stripe_paiement.verifier_session
    stripe_paiement.verifier_session = lambda s: {
        "success": True, "formule": "mensuel", "message": "ok",
        "compte": comptes.public(compte), "cle": remise["cle"]}
    try:
        # Un navigateur ANONYME, muni de la seule référence de session.
        anonyme = _app()
        r = anonyme.post("/api/abonnement/verifier", json={"session_id": reference})
        assert r.status_code == 200, r.text
        corps = r.json()
        assert "cle" not in corps and corps.get("cle_masquee") is True
        assert "AGF-" not in r.text

        # Le compte payeur, lui, la reçoit.
        r = c.post("/api/abonnement/verifier", json={"session_id": reference})
        assert r.json().get("cle") == remise["cle"], r.text
    finally:
        stripe_paiement.verifier_session = origine
    print("  OK — la clé n'est pas livrée à un navigateur non connecté")


def test_page_de_retour_affiche_la_cle_au_payeur():
    """La clé n'est lisible QU'UNE FOIS : la page de retour doit la montrer.

    C'est le seul moment où l'abonné la voit si aucun envoi n'est configuré —
    la base n'en garde que l'empreinte.
    """
    _isoler()
    _neutraliser_envois()
    c = _app()
    cid = _inscrire(c).json()["compte"]["id"]

    from licence import comptes, stripe_paiement
    compte = comptes.activer_abonnement(cid, "annuel", "cs_retour")
    remise = stripe_paiement.remettre_cle(compte, "annuel", "cs_retour")
    cle = remise["cle"]

    origine = stripe_paiement.verifier_session
    stripe_paiement.verifier_session = lambda s: {
        "success": True, "formule": "annuel", "message": "Paiement confirmé.",
        "compte": comptes.public(compte), "cle": cle,
        "cle_message": remise["cle_message"]}
    try:
        r = c.get("/api/abonnement/retour?session_id=cs_retour")
        assert r.status_code == 200
        assert cle in r.text, "la clé doit être affichée au payeur"
        assert "Votre clé d'abonnement" in r.text
        assert "Conservez-la" in r.text

        # Le même lien ouvert ailleurs ne doit rien livrer.
        anonyme = _app()
        r = anonyme.get("/api/abonnement/retour?session_id=cs_retour")
        assert r.status_code == 200 and cle not in r.text
    finally:
        stripe_paiement.verifier_session = origine
    print("  OK — page de retour : clé affichée au payeur, à lui seul")


def test_renvoi_de_cle_reserve_aux_abonnes():
    """Un essai en cours n'ouvre aucun droit Pro : pas de clé à remettre."""
    _isoler()
    _neutraliser_envois()
    c = _app()
    _inscrire(c)

    r = c.post("/api/abonnement/cle")
    assert r.status_code == 402, r.text
    assert "aucun abonnement actif" in r.json()["error"].lower()

    # Sans compte du tout : 401, et surtout pas de clé.
    anonyme = _app()
    r = anonyme.post("/api/abonnement/cle")
    assert r.status_code == 401 and "AGF-" not in r.text
    print("  OK — renvoi de clé refusé hors abonnement")


def test_abonne_peut_redemander_sa_cle():
    """Le rattrapage quand l'e-mail n'est jamais arrivé."""
    _isoler()
    _neutraliser_envois()
    c = _app()
    cid = _inscrire(c).json()["compte"]["id"]
    _webhook(c, cid, "cs_redemande", 7879)

    from backend.routes import abonnement as routes_abonnement
    routes_abonnement._dernieres_reemissions.clear()

    r = c.post("/api/abonnement/cle")
    assert r.status_code == 200, r.text
    corps = r.json()
    from licence import cles
    assert cles.normaliser(corps["cle"]), corps
    assert "remplac" in corps["avertissement"].lower()

    # Deux demandes coup sur coup : la seconde attend. Chaque envoi est un SMS
    # facturé, et une session volée ne doit pas pouvoir les enchaîner.
    r2 = c.post("/api/abonnement/cle")
    assert r2.status_code == 429, r2.text

    # La liste des clés ne montre que des aperçus masqués.
    routes_abonnement._dernieres_reemissions.clear()
    r3 = c.get("/api/abonnement/cle")
    assert r3.status_code == 200
    apercus = [k["apercu"] for k in r3.json()["cles"]]
    assert apercus and all(a.startswith("AGF-•") for a in apercus), apercus
    assert corps["cle"][-5:] in apercus[0]          # de quoi la reconnaître
    assert corps["cle"] not in r3.text              # jamais la clé entière
    print("  OK — l'abonné peut redemander sa clé, avec délai anti-abus")


def test_ecran_abonnement_annonce_les_canaux_reels():
    """L'interface ne doit pas promettre un e-mail qu'aucun serveur n'enverra."""
    _isoler()
    _neutraliser_envois()
    c = _app()
    _inscrire(c)

    for nom in ("SMTP_HOTE", "SMS_FOURNISSEUR", "SMS_URL", "TWILIO_ACCOUNT_SID"):
        os.environ.pop(nom, None)
    remise = c.get("/api/abonnement").json()["remise_cle"]
    assert remise == {"email": False, "sms": False, "sms_fournisseur": "",
                      "aucun_canal": True}

    os.environ["SMTP_HOTE"] = "smtp.exemple.fr"
    os.environ["TWILIO_ACCOUNT_SID"] = "AC_de_test"
    try:
        remise = c.get("/api/abonnement").json()["remise_cle"]
        assert remise["email"] is True
        assert remise["sms"] is True and remise["sms_fournisseur"] == "twilio"
        assert remise["aucun_canal"] is False
    finally:
        os.environ.pop("SMTP_HOTE", None)
        os.environ.pop("TWILIO_ACCOUNT_SID", None)

    # Aucun secret ne transite par cette route.
    assert "TWILIO" not in c.get("/api/abonnement").text.upper()
    print("  OK — l'écran annonce les canaux réellement configurés")


def test_aucun_secret_d_envoi_expose():
    """Identifiants SMTP et jetons de passerelle ne sortent jamais par HTTP."""
    _isoler()
    _neutraliser_envois()
    os.environ["SMTP_HOTE"] = "smtp.exemple.fr"
    os.environ["SMTP_MOTDEPASSE"] = "mot-de-passe-smtp-secret"
    os.environ["TWILIO_AUTH_TOKEN"] = "jeton-twilio-secret"
    try:
        c = _app()
        _inscrire(c)
        pages = [c.get("/api/abonnement").text,
                 c.get("/api/licence").text,
                 c.get("/api/licence/offre").text,
                 c.get("/api/abonnement/cle").text]
        for texte in pages:
            assert "mot-de-passe-smtp-secret" not in texte
            assert "jeton-twilio-secret" not in texte
    finally:
        for nom in ("SMTP_HOTE", "SMTP_MOTDEPASSE", "TWILIO_AUTH_TOKEN"):
            os.environ.pop(nom, None)
    print("  OK — aucun identifiant d'envoi exposé par l'API")


# ═══════════════════════════ DOCUMENTATION / INTERFACE ═══════════════════

def test_ecran_pro_ne_dit_plus_l_abonnement_indisponible():
    """Régression : l'écran annonçait « pas encore souscriptible » à des
    clients qui venaient de payer par Stripe et tenaient leur clé en main.

    Le drapeau ne regardait que `LICENCE_API_URL`, un émetteur externe jamais
    déployé — alors que Stripe encaisse et que la clé s'active localement.
    """
    _isoler()
    _neutraliser_envois()
    c = _app()
    _inscrire(c)

    # Serveur sans aucun secret Stripe : rien ne peut être confirmé, et
    # l'écran a raison de le dire.
    for nom in ("STRIPE_SECRET_KEY", "STRIPE_WEBHOOK_SECRET"):
        os.environ.pop(nom, None)
    assert c.get("/api/licence").json()["abonnement_disponible"] is False

    # Dès qu'un des deux secrets est là, l'abonnement EST souscriptible.
    for secret in ("STRIPE_SECRET_KEY", "STRIPE_WEBHOOK_SECRET"):
        os.environ[secret] = "sk_test_x" if "SECRET_KEY" in secret else "whsec_x"
        try:
            assert c.get("/api/licence").json()["abonnement_disponible"] is True, secret
        finally:
            os.environ.pop(secret, None)

    # Et le texte de repli ne parle plus d'un émetteur de licences absent.
    licence_js = (_RACINE / "frontend" / "js" / "licence.js").read_text(encoding="utf-8")
    assert "aucun émetteur de licences n'y est raccordé" not in licence_js
    assert "activable ci-dessous" in licence_js
    print("  OK — l'écran ne déclare plus l'abonnement indisponible à tort")


def test_documentation_couvre_la_remise_des_cles():
    """Une fonctionnalité qui demande une configuration serveur doit être
    documentée là où l'administrateur la cherche."""
    guide = (_RACINE / "DEPLOIEMENT.md").read_text(encoding="utf-8")
    for element in ("AGF-XXXXX", "SMTP_HOTE", "TWILIO_ACCOUNT_SID",
                    "OVH_APPLICATION_KEY", "SMS_URL",
                    "scripts/abonnement.py", "AGENCE_LICENCE_PRIVKEY"):
        assert element in guide, f"DEPLOIEMENT.md ne documente pas « {element} »"
    # Et la limite de l'émetteur local doit être écrite, pas sous-entendue.
    assert "contrôle la machine" in guide

    exemple = (_RACINE / "python" / ".env.example").read_text(encoding="utf-8")
    for element in ("SMTP_MOTDEPASSE", "TWILIO_AUTH_TOKEN", "SMS_AUTORISATION",
                    "AGENCE_LICENCE_PRIVKEY"):
        assert element in exemple, f".env.example n'évoque pas « {element} »"
    # Des repères, jamais des valeurs : rien à droite d'un « = » actif.
    for ligne in exemple.splitlines():
        depouillee = ligne.strip()
        if depouillee.startswith("#") or "=" not in depouillee:
            continue
        nom, _, valeur = depouillee.partition("=")
        if nom.strip() in ("SMTP_MOTDEPASSE", "TWILIO_AUTH_TOKEN",
                           "OVH_APPLICATION_SECRET", "OVH_CONSUMER_KEY",
                           "SMS_AUTORISATION", "AGENCE_LICENCE_PRIVKEY"):
            assert not valeur.strip(), f".env.example porte une valeur : {nom}"

    readme = (_RACINE / "README.md").read_text(encoding="utf-8")
    assert "clé d'abonnement" in readme.lower()
    assert "scripts/abonnement.py" in readme
    print("  OK — guide, .env.example et README couvrent la remise des clés")


def test_interface_propose_la_cle():
    """L'écran doit dire d'où vient la clé et offrir de la redemander."""
    compte_js = (_RACINE / "frontend" / "js" / "compte.js").read_text(encoding="utf-8")
    licence_js = (_RACINE / "frontend" / "js" / "licence.js").read_text(encoding="utf-8")
    css = (_RACINE / "frontend" / "css" / "style.css").read_text(encoding="utf-8")

    assert "clé d'abonnement" in compte_js
    assert "/api/abonnement/cle" in compte_js
    assert "Je n'ai pas reçu ma clé" in compte_js
    # Le texte s'adapte aux canaux réellement configurés.
    assert "remise_cle" in compte_js and "aucun envoi automatique" in compte_js
    # L'écran d'activation dit d'où vient la clé et en propose une nouvelle.
    assert "AGF-XXXXX-XXXXX-XXXXX" in licence_js
    assert "licenceDemanderCle" in licence_js
    # La clé doit se sélectionner d'un geste : elle est lue une seule fois.
    assert "user-select: all" in css
    print("  OK — l'interface explique la clé et permet d'en redemander une")


def test_aucun_secret_en_dur_dans_les_nouveaux_modules():
    """Identifiants SMTP, jetons de passerelle et clés privées viennent TOUS
    de l'environnement du serveur — jamais du code."""
    import re
    suspects = ("sk_live_", "sk_test_", "whsec_", "AC0", "Bearer ")
    for nom in ("cles.py", "notifications.py", "emetteur.py"):
        texte = (_RACINE / "python" / "licence" / nom).read_text(encoding="utf-8")
        for motif in suspects:
            for ligne in texte.splitlines():
                if motif in ligne:
                    assert ligne.lstrip().startswith("#") or '"' + motif in ligne, (
                        f"{nom} : « {motif} » hors commentaire — {ligne.strip()[:60]}")
        # Aucune valeur par défaut non vide pour une variable sensible.
        for variable, defaut in re.findall(
                r'os\.getenv\("([A-Z_]+)",\s*"([^"]*)"\)', texte):
            if any(m in variable for m in ("MOTDEPASSE", "TOKEN", "SECRET",
                                           "KEY", "AUTORISATION")):
                assert defaut == "", f"{nom} : {variable} a un défaut « {defaut} »"
    print("  OK — aucun identifiant d'envoi ni clé privée en dur")


def test_diagnostic_nomme_le_premier_maillon_casse():
    """« Pourquoi mon client n'a pas reçu sa clé ? » doit avoir une réponse.

    Le diagnostic doit désigner LE maillon qui casse la chaîne, pas aligner
    des avertissements qui découlent tous de la même cause.
    """
    import subprocess
    import sys
    dossier = _isoler()
    _neutraliser_envois()
    from licence import comptes

    script = _RACINE / "scripts" / "abonnement.py"
    environnement = dict(os.environ, AGENCE_DATA_DIR=str(dossier / "data"))
    for nom in ("STRIPE_SECRET_KEY", "STRIPE_WEBHOOK_SECRET", "SMTP_HOTE"):
        environnement.pop(nom, None)

    def _lancer(env):
        return subprocess.run([sys.executable, str(script), "diagnostic"],
                              capture_output=True, text=True, timeout=180, env=env)

    # 1. Sans secret Stripe : c'est LA cause, et elle est nommée seule.
    sortie = _lancer(environnement)
    assert sortie.returncode == 1, sortie.stdout
    assert "Aucun secret Stripe" in sortie.stdout
    assert sortie.stdout.count("Ce qui bloque") == 1

    # 2. Avec les secrets mais aucun paiement reçu : le diagnostic pointe le
    #    webhook, pas l'envoi — un paiement qui n'arrive pas n'émet rien.
    comptes.inscrire(_INSCRIPTION)
    avec = dict(environnement, STRIPE_SECRET_KEY="sk_test_x",
                STRIPE_WEBHOOK_SECRET="whsec_x")
    sortie = _lancer(avec)
    assert "AUCUN paiement n'est jamais arrivé" in sortie.stdout, sortie.stdout
    assert "api/abonnement/webhook" in sortie.stdout
    assert "IPv6" in sortie.stdout

    # 3. Paiement reçu, clé émise, mais aucun canal : c'est l'envoi qu'il faut
    #    configurer, et le diagnostic le dit sans accuser Stripe.
    compte = comptes.depot().par_email(_INSCRIPTION["email"])
    compte = comptes.activer_abonnement(compte["id"], "annuel", "cs_diag")
    from licence import cles
    cles.emettre(compte["id"], "annuel", "cs_diag", compte["abonne_jusqua"])
    sortie = _lancer(avec)
    assert "aucun canal d'envoi n'est configuré" in sortie.stdout, sortie.stdout
    assert "configurer-serveur.sh --envoi" in sortie.stdout
    assert "AUCUN paiement n'est jamais arrivé" not in sortie.stdout

    # Aucun secret ne doit fuiter dans un diagnostic qu'on colle dans un ticket.
    assert "sk_test_x" not in sortie.stdout and "whsec_x" not in sortie.stdout
    print("  OK — le diagnostic nomme le maillon qui casse la chaîne")


def test_outil_admin_ne_simule_aucun_paiement():
    """`activer` exige une référence de règlement et le dit clairement."""
    import subprocess
    import sys
    script = _RACINE / "scripts" / "abonnement.py"
    texte = script.read_text(encoding="utf-8")
    assert "ne constate aucun paiement" in texte
    assert "--reference est obligatoire" in texte

    # Il s'exécute vraiment : une aide qui plante ne sert personne.
    sortie = subprocess.run([sys.executable, str(script), "canaux"],
                            capture_output=True, text=True, timeout=120)
    assert sortie.returncode == 0, sortie.stderr
    assert "Remise des clés" in sortie.stdout
    print("  OK — outil d'administration présent, sans paiement simulé")


def run():
    from utils import app_config
    import config as app_cfg
    from licence import notifications
    _etat0 = (app_config._PATH, app_config._cache, app_cfg.DB_PATH,
              os.environ.get("AGENCE_DATA_DIR"),
              os.environ.get("AGENCE_LICENCE_PUBKEY"),
              os.environ.get("AGENCE_LICENCE_JETON"),
              notifications.envoyer_email, notifications.envoyer_sms)
    try:
        test_format_de_cle_lisible_et_tolerant()
        test_cle_jamais_stockee_en_clair()
        test_paiement_confirme_emet_et_envoie_la_cle()
        test_un_seul_paiement_une_seule_cle()
        test_panne_d_envoi_n_annule_pas_l_abonnement()
        test_relais_sans_authentification_accepte()
        test_script_de_configuration_complete_sans_ecraser()
        test_sans_canal_configure_rien_n_est_annonce_comme_envoye()
        test_la_cle_active_reellement_la_version_pro()
        test_cle_inventee_ou_perimee_refusee()
        test_reemission_revoque_la_precedente()
        test_la_cle_ne_sort_pas_vers_un_tiers()
        test_page_de_retour_affiche_la_cle_au_payeur()
        test_renvoi_de_cle_reserve_aux_abonnes()
        test_abonne_peut_redemander_sa_cle()
        test_ecran_abonnement_annonce_les_canaux_reels()
        test_aucun_secret_d_envoi_expose()
        test_ecran_pro_ne_dit_plus_l_abonnement_indisponible()
        test_documentation_couvre_la_remise_des_cles()
        test_interface_propose_la_cle()
        test_aucun_secret_en_dur_dans_les_nouveaux_modules()
        test_diagnostic_nomme_le_premier_maillon_casse()
        test_outil_admin_ne_simule_aucun_paiement()
    finally:
        import importlib
        (app_config._PATH, app_config._cache, app_cfg.DB_PATH, _data,
         _pub, _jeton, notifications.envoyer_email,
         notifications.envoyer_sms) = _etat0
        for nom, valeur in (("AGENCE_DATA_DIR", _data),
                            ("AGENCE_LICENCE_PUBKEY", _pub),
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
    print("✅ Clés d'abonnement OK")
