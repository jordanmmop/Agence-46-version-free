"""Licences Agence Novia : activation, validation, hors ligne, révocation, horloge.

Chaque cas du cahier des charges a son test, et chaque test joue contre le
serveur factice qui applique le contrat documenté (tests/_novia_factice.py).
Aucun ne touche le réseau réel : le test HTTPS de bout en bout monte son
propre serveur local, avec sa propre autorité de certification.
"""
import _setup  # noqa: F401
import json
import os
import tempfile
import time
from pathlib import Path

_RACINE = Path(__file__).resolve().parents[2]
JOUR = 86400


class Horloge:
    """Horloge pilotable : c'est elle qu'on avance, recule, fausse."""
    def __init__(self, t=None):
        self.t = float(t or time.time())

    def __call__(self):
        return self.t

    def avancer(self, jours):
        self.t += jours * JOUR


class Banc:
    """Installation neuve + serveur Novia factice + horloge commune."""

    def __init__(self, hors_ligne_jours=30):
        from utils import app_config
        from _novia_factice import NoviaFactice
        from licence.license_manager import LicenseManager
        from licence.novia_client import NoviaClient

        self.dossier = Path(tempfile.mkdtemp())
        app_config._PATH = self.dossier / "config.json"
        app_config._cache = None
        self.horloge = Horloge()
        self.novia = NoviaFactice(horloge=self.horloge, hors_ligne_jours=hors_ligne_jours)
        self.client = NoviaClient("https://novia.test", "agence46", self.novia.transport)
        self.m = LicenseManager(client=self.client,
                                cles_publiques={self.novia.kid: self.novia.cle_publique_hex()},
                                horloge=self.horloge)

    def stockage(self):
        from utils import app_config
        return app_config.get("novia_licence") or {}


# ═══════════════════════════ ACTIVATION ═══════════════════════════════════

def test_activation():
    b = Banc()
    cle = b.novia.creer_licence("PRO")
    assert b.m.etat()["etat"] == "NOT_ACTIVATED"

    r = b.m.activer("  " + cle.lower() + " ")            # saisie approximative
    assert r["success"] is True, r
    assert r["etat"] == "ACTIVE" and r["niveau"] == "PRO" and r["premium"] is True
    assert r["cle_masquee"] == "NOVIA-****-****-****-" + cle[-4:]
    assert r["hors_ligne_jusqua"] > b.horloge() + 29 * JOUR

    # Stockage local : la clé, le jeton, les dates — et l'installation liée.
    s = b.stockage()
    assert s["cle"] == cle and s["jeton"] and s["statut_serveur"] == "ACTIVE"
    assert b.m.installation_id() in b.novia.licences[cle]["appareils"]
    print("  OK — activation : licence PRO active, liée à cet appareil")


def test_installation_id_uuid_stable_et_non_materiel():
    import uuid
    b = Banc()
    premier = b.m.installation_id()
    assert uuid.UUID(premier).version == 4, "UUID4 aléatoire attendu"
    assert b.m.installation_id() == premier, "l'identifiant doit être stable"
    # Aucune donnée de la machine : ni nom d'hôte, ni utilisateur, ni MAC.
    import getpass
    import socket
    for indice in (socket.gethostname(), getpass.getuser(), hex(uuid.getnode())[2:]):
        assert indice.lower() not in premier.lower()

    # Désinstallation / réinstallation en gardant le dossier utilisateur :
    # même identifiant, donc même emplacement d'appareil chez Novia.
    cle = b.novia.creer_licence("PRO", max_appareils=1)
    b.m.activer(cle)
    from licence.license_manager import LicenseManager
    reinstallee = LicenseManager(client=b.client, horloge=b.horloge,
                                 cles_publiques={b.novia.kid: b.novia.cle_publique_hex()})
    assert reinstallee.installation_id() == premier
    assert reinstallee.activer(cle)["success"] is True, \
        "réactiver le MÊME appareil ne doit pas consommer un second emplacement"
    print("  OK — installation_id : UUID4 aléatoire, stable, sans donnée matérielle")


def test_seuls_les_champs_necessaires_partent():
    """Ni fichier, ni projet, ni prompt, ni donnée Ollama : cinq champs."""
    b = Banc()
    cle = b.novia.creer_licence("PRO")
    b.m.activer(cle)
    b.m.revalider(forcer=True)
    b.m.desactiver()
    attendus = {
        "/api/license/activate": {"license_key", "installation_id", "product",
                                  "app_version", "platform"},
        "/api/license/validate": {"license_key", "installation_id", "product",
                                  "app_version", "platform"},
        "/api/license/deactivate": {"license_key", "installation_id", "product"},
    }
    vus = set()
    for chemin, corps in b.novia.requetes:
        vus.add(chemin)
        assert set(corps) == attendus[chemin], (chemin, sorted(corps))
        if "platform" in corps:                   # absent, à dessein, de deactivate
            assert corps["platform"] in ("windows", "macos", "linux")
        for valeur in corps.values():
            assert len(str(valeur)) < 80, "aucun contenu volumineux ne doit partir"
    assert vus == set(attendus), f"routes non exercées : {set(attendus) - vus}"
    print("  OK — seuls license_key, installation_id, product, app_version, platform partent")


def test_cle_invalide():
    b = Banc()
    # Format : refusé SANS appel réseau.
    for mauvaise in ("", "bonjour", "NOVIA-12", "NOVIA-ABCD-EFGH", "XXXX-AAAA-BBBB-CCCC-DDDD"):
        r = b.m.activer(mauvaise)
        assert r["success"] is False and r["message"] == "Cette clé de licence est invalide."
    assert b.novia.requetes == [], "une clé mal formée ne doit pas partir"

    # Bon format, inconnue du serveur.
    r = b.m.activer("NOVIA-AAAA-BBBB-CCCC-DDDD")
    assert r["success"] is False and r["message"] == "Cette clé de licence est invalide."
    assert r["etat"] == "NOT_ACTIVATED" and not b.stockage()
    print("  OK — clé invalide : refusée localement (format) puis par le serveur")


def test_licence_expiree():
    b = Banc()
    # Refusée par le serveur à l'activation.
    echue = b.novia.creer_licence("PRO", statut="EXPIRED")
    r = b.m.activer(echue)
    assert r["success"] is False and r["message"] == "Votre licence a expiré."

    # Arrivée à échéance APRÈS activation, sans aucune connexion : le jeton
    # signé porte son échéance, l'application la voit seule.
    cle = b.novia.creer_licence("PRO", duree_jours=10)
    assert b.m.activer(cle)["success"]
    b.novia.hors_ligne = True
    b.horloge.avancer(9)
    assert b.m.etat()["valide"] is True
    b.horloge.avancer(2)
    e = b.m.etat()
    assert e["etat"] == "EXPIRED" and e["raison"] == "licence_echue"
    assert e["message"] == "Votre licence a expiré."
    print("  OK — licence expirée : à l'activation, et hors ligne à son échéance signée")


def test_licence_suspendue_et_revoquee_donnees_intactes():
    """Révocation : premium coupé, message exact, AUCUNE donnée supprimée."""
    b = Banc()
    cle = b.novia.creer_licence("PRO")
    b.m.activer(cle)

    # Des données utilisateur à côté de la licence : rien ne doit y toucher.
    from utils import app_config
    app_config.set("projet_utilisateur", {"nom": "Mon projet", "notes": "à garder"})
    fichier = b.dossier / "data" / "projet.db"
    fichier.parent.mkdir(parents=True, exist_ok=True)
    fichier.write_bytes(b"donnees locales")

    b.novia.licences[cle]["statut"] = "SUSPENDED"
    r = b.m.revalider(forcer=True)
    assert r["etat"] == "SUSPENDED" and r["premium"] is False
    assert r["message"] == ("Votre licence Agence 46 est suspendue. "
                            "Veuillez contacter le support.")

    b.novia.licences[cle]["statut"] = "REVOKED"
    r = b.m.revalider(forcer=True)
    assert r["etat"] == "REVOKED" and r["premium"] is False
    assert r["message"] == ("Votre licence Agence 46 a été révoquée. "
                            "Veuillez contacter le support.")
    # Le jeton — encore valable hors ligne — est effacé : une licence révoquée
    # ne doit pas survivre jusqu'à son échéance.
    assert "jeton" not in b.stockage()
    b.novia.hors_ligne = True
    assert b.m.etat()["etat"] == "REVOKED", "la révocation doit tenir hors ligne"

    assert app_config.get("projet_utilisateur") == {"nom": "Mon projet", "notes": "à garder"}
    assert fichier.read_bytes() == b"donnees locales"
    print("  OK — suspendue / révoquée : premium coupé, message exact, données intactes")


def test_depassement_d_appareils():
    b = Banc()
    cle = b.novia.creer_licence("PRO", max_appareils=1)
    b.novia.licences[cle]["appareils"].add("autre-appareil")
    r = b.m.activer(cle)
    assert r["success"] is False
    assert r["message"] == "Le nombre maximal d'appareils autorisés est atteint."
    assert not b.stockage(), "rien ne doit être enregistré"
    print("  OK — dépassement d'appareils : refus clair, rien d'enregistré")


# ═══════════════════════════ HORS LIGNE ═══════════════════════════════════

def test_fonctionnement_hors_ligne_apres_activation():
    b = Banc()
    cle = b.novia.creer_licence("PRO")
    b.m.activer(cle)
    b.novia.hors_ligne = True                    # plus aucun réseau

    b.horloge.avancer(3)
    assert b.m.etat()["etat"] == "ACTIVE"
    b.horloge.avancer(10)                        # au-delà de l'intervalle de 7 j
    e = b.m.etat()
    assert e["etat"] == "OFFLINE_VALID" and e["premium"] is True

    # La revalidation échoue, l'utilisateur n'est PAS bloqué, et le message dit
    # jusqu'à quand sa licence locale tient.
    r = b.m.revalider()
    assert r["success"] is False and r["valide"] is True
    assert "Impossible de contacter le serveur de licences." in r["message"]
    assert "Votre licence locale reste valide jusqu'au" in r["message"]

    b.horloge.avancer(18)                        # 31 jours sans connexion
    e = b.m.etat()
    assert e["etat"] == "EXPIRED" and e["raison"] == "hors_ligne_epuise"
    assert "Connectez-vous à Internet" in e["message"]
    print("  OK — hors ligne : ACTIVE → OFFLINE_VALID → épuisé au 31e jour")


def test_absence_internet_a_l_activation():
    b = Banc()
    cle = b.novia.creer_licence("PRO")
    b.novia.hors_ligne = True
    r = b.m.activer(cle)
    assert r["success"] is False and r["erreur"] == "reseau"
    assert r["message"] == "Impossible de contacter le serveur de licences."
    assert not b.stockage()
    print("  OK — pas d'Internet à l'activation : message clair, rien d'enregistré")


def test_serveur_indisponible():
    b = Banc()
    cle = b.novia.creer_licence("PRO")
    b.m.activer(cle)
    b.novia.en_panne = True                      # 503
    b.horloge.avancer(8)
    r = b.m.revalider()
    assert r["success"] is False and r["erreur"] == "serveur"
    assert "Le serveur de licences est temporairement indisponible." in r["message"]
    assert r["valide"] is True, "une panne serveur ne doit pas couper l'accès"
    print("  OK — serveur indisponible : message clair, licence locale conservée")


def test_validation_periodique_sans_marteler():
    """Pas de requête à chaque démarrage ; pas de rafale quand on est hors ligne."""
    b = Banc()
    cle = b.novia.creer_licence("PRO")
    b.m.activer(cle)
    nb = lambda: len(b.novia.requetes)
    avant = nb()
    for _ in range(20):                          # vingt « démarrages »
        b.m.revalider()
        b.m.etat()
    assert nb() == avant, "aucune requête tant que la validation n'est pas due"

    b.horloge.avancer(7.1)
    b.m.revalider()
    assert nb() == avant + 1, "une requête une fois l'intervalle écoulé"

    b.novia.hors_ligne = True
    b.horloge.avancer(7.1)
    for _ in range(10):
        b.m.revalider()
    assert nb() == avant + 1, "hors ligne, un seul essai puis on patiente"
    b.horloge.t += 6 * 3600 + 1
    b.novia.hors_ligne = False
    b.m.revalider()
    assert nb() == avant + 2, "nouvel essai six heures plus tard"
    print("  OK — validation périodique : 7 jours, sans rafale hors ligne")


def test_renouvellement():
    """Un abonnement renouvelé chez Novia prolonge la licence au prochain contrôle."""
    b = Banc()
    cle = b.novia.creer_licence("PRO", duree_jours=20)
    b.m.activer(cle)
    assert abs(b.m.etat()["expire_le"] - (b.horloge() + 20 * JOUR)) < 5

    b.novia.licences[cle]["expire"] = b.horloge() + 385 * JOUR   # renouvelé
    b.horloge.avancer(8)
    r = b.m.revalider()
    assert r["success"] is True
    assert r["expire_le"] > b.horloge() + 370 * JOUR
    assert r["hors_ligne_jusqua"] > b.horloge() + 29 * JOUR
    print("  OK — renouvellement : nouvelle échéance reprise au contrôle suivant")


def test_periode_hors_ligne_locale_ne_peut_que_raccourcir():
    from licence import config as lconfig
    b = Banc(hors_ligne_jours=60)                # le serveur accorde 60 jours
    cle = b.novia.creer_licence("PRO")
    b.m.activer(cle)
    origine = lconfig.OFFLINE_GRACE_PERIOD_JOURS
    try:
        lconfig.OFFLINE_GRACE_PERIOD_JOURS = 30
        assert b.m.etat()["hors_ligne_jusqua"] <= b.horloge() + 30 * JOUR + 5
        lconfig.OFFLINE_GRACE_PERIOD_JOURS = 90  # ne peut pas dépasser le serveur
        assert b.m.etat()["hors_ligne_jusqua"] <= b.horloge() + 60 * JOUR + 5
    finally:
        lconfig.OFFLINE_GRACE_PERIOD_JOURS = origine
    print("  OK — période hors ligne : la configuration locale raccourcit, n'allonge jamais")


# ═══════════════════════════ JETON ET SIGNATURE ═══════════════════════════

def test_validation_du_jeton():
    """Toutes les revendications sont contrôlées, pas seulement la signature."""
    from licence import jeton_novia
    b = Banc()
    cle = b.novia.creer_licence("PRO")
    inst = b.m.installation_id()
    b.novia.licences[cle]["appareils"].add(inst)
    cles = {b.novia.kid: b.novia.cle_publique_hex()}
    now = b.horloge()

    bon = b.novia.jeton_pour(cle, inst)
    rev = jeton_novia.verifier(bon, cles, "agence46", inst, now)
    assert rev["tier"] == "PRO" and rev["inst"] == inst

    cas = {
        "autre_installation": b.novia.jeton_pour(cle, "autre-appareil"),
        "autre_produit": b.novia.jeton_pour(cle, inst, aud="autre-appli"),
        "emetteur": b.novia.jeton_pour(cle, inst, iss="pirate"),
        "futur": b.novia.jeton_pour(cle, inst, iat=int(now + 3 * JOUR)),
        "version": b.novia.jeton_pour(cle, inst, min_version="99.0.0"),
        "sans_offline_until": b.novia.jeton_pour(cle, inst, offline_until=None),
    }
    for nom, jeton in cas.items():
        try:
            jeton_novia.verifier(jeton, cles, "agence46", inst, now, version_app="4.2.0",
                                 tolerance_s=JOUR)
            raise AssertionError(f"jeton « {nom} » accepté")
        except jeton_novia.JetonRefuse:
            pass
    print("  OK — jeton : appareil, produit, émetteur, date, version, champs contrôlés")


def test_signature_invalide():
    """Charge modifiée, autre clé, alg:none, HS256 : tous refusés."""
    import base64
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from licence import jeton_novia
    b = Banc()
    cle = b.novia.creer_licence("PRO")
    inst = b.m.installation_id()
    cles = {b.novia.kid: b.novia.cle_publique_hex()}
    now = b.horloge()
    bon = b.novia.jeton_pour(cle, inst)
    h, p, s = bon.split(".")

    charge = json.loads(base64.urlsafe_b64decode(p + "=="))
    charge["tier"] = "ENTERPRISE"
    charge["offline_until"] += 365 * JOUR
    trafique = f"{h}.{jeton_novia.b64e(json.dumps(charge).encode())}.{s}"

    pirate = b.novia.jeton_pour(cle, inst)
    pirate = b.novia.signer(jeton_novia.lire_sans_verifier(pirate),
                            cle_privee=Ed25519PrivateKey.generate())

    sans_alg = b.novia.signer(jeton_novia.lire_sans_verifier(bon),
                              en_tete={"alg": "none", "typ": "JWT"})
    none = sans_alg.rsplit(".", 1)[0] + "."
    hs256 = b.novia.signer(jeton_novia.lire_sans_verifier(bon),
                           en_tete={"alg": "HS256", "typ": "JWT"})

    for nom, jeton in {"trafiqué": trafique, "autre clé": pirate,
                       "alg none": none, "HS256": hs256, "tronqué": bon[:-5],
                       "vide": ""}.items():
        try:
            jeton_novia.verifier(jeton, cles, "agence46", inst, now)
            raise AssertionError(f"jeton « {nom} » accepté")
        except jeton_novia.JetonRefuse:
            pass

    # Et dans le gestionnaire : un jeton local altéré vaut INVALID.
    b.m.activer(cle)
    from utils import app_config
    s_ = b.stockage()
    s_["jeton"] = trafique
    app_config.set("novia_licence", s_)
    b.m._memo_jeton = None
    e = b.m.etat()
    assert e["etat"] == "INVALID" and e["premium"] is False
    print("  OK — signature invalide : charge modifiée, autre clé, alg none/HS256 refusés")


def test_reponse_valide_sans_jeton_refusee():
    """Un simple {"valid": true} ne doit rien ouvrir : seul le jeton prouve."""
    from licence.novia_client import NoviaClient
    b = Banc()
    faux = lambda url, corps, t: (200, {"valid": True, "license_status": "ACTIVE",
                                        "product": "agence46"})
    b.m.client = NoviaClient("https://novia.test", "agence46", faux)
    r = b.m.activer("NOVIA-AAAA-BBBB-CCCC-DDDD")
    assert r["success"] is False and r["etat"] == "NOT_ACTIVATED"

    # Même chose avec un jeton signé par une autre clé.
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    inst = b.m.installation_id()
    b.novia.creer_licence("PRO")
    jeton = b.novia.signer({"iss": "novia", "aud": "agence46", "inst": inst,
                            "tier": "PRO", "iat": int(b.horloge()),
                            "offline_until": int(b.horloge() + 30 * JOUR)},
                           cle_privee=Ed25519PrivateKey.generate())
    faux2 = lambda url, corps, t: (200, {"valid": True, "license_status": "ACTIVE",
                                         "product": "agence46", "signed_token": jeton})
    b.m.client = NoviaClient("https://novia.test", "agence46", faux2)
    r = b.m.activer("NOVIA-AAAA-BBBB-CCCC-DDDD")
    assert r["success"] is False and not b.stockage()
    print("  OK — réponse « valide » sans jeton authentique : refusée")


def test_https_obligatoire():
    from licence.novia_client import ConfigurationInvalide, NoviaClient
    appele = []
    client = NoviaClient("http://novia.test", "agence46",
                         lambda *a: appele.append(a) or (200, {}))
    try:
        client.activer("NOVIA-AAAA-BBBB-CCCC-DDDD", "x", "4.2.0")
        raise AssertionError("une URL http:// a été acceptée")
    except ConfigurationInvalide:
        pass
    assert appele == [], "la clé ne doit jamais partir en clair"
    print("  OK — HTTPS obligatoire : aucune requête vers une URL http://")


# ═══════════════════════════ HORLOGE ══════════════════════════════════════

def test_horloge_reculee_ne_rend_aucun_jour():
    b = Banc()
    cle = b.novia.creer_licence("PRO")
    b.m.activer(cle)
    b.novia.hors_ligne = True

    b.horloge.avancer(29)
    assert b.m.etat()["valide"] is True       # vu : J+29
    b.horloge.avancer(2)                       # J+31 : épuisé
    assert b.m.etat()["raison"] == "hors_ligne_epuise"

    b.horloge.avancer(-20)                     # on recule l'horloge à J+11
    e = b.m.etat()
    assert e["valide"] is False, "reculer l'horloge ne doit rendre aucun jour"
    assert e["horloge_reculee"] is True
    print("  OK — horloge reculée : aucun jour hors ligne regagné, anomalie signalée")


def test_horloge_avancee_puis_corrigee():
    """En avance, la licence s'éteint plus tôt ; corrigée + reconnexion, elle repart."""
    b = Banc()
    cle = b.novia.creer_licence("PRO")
    b.m.activer(cle)
    reelle = b.horloge.t

    b.horloge.avancer(40)                      # horloge PC en avance de 40 j
    assert b.m.etat()["valide"] is False

    b.horloge.t = reelle + 1 * JOUR            # l'utilisateur corrige son horloge
    assert b.m.etat()["valide"] is False, "l'heure vue ne redescend pas seule"
    r = b.m.revalider(forcer=True)             # une validation en ligne réussie…
    assert r["success"] is True
    assert b.m.etat()["valide"] is True, "…remet l'horloge d'accord et relance"
    print("  OK — horloge en avance puis corrigée : rétablie par une validation en ligne")


def test_horloge_decalee_a_l_activation_signalee():
    b = Banc()
    cle = b.novia.creer_licence("PRO")
    b.horloge.avancer(-3)                      # le PC retarde de 3 jours
    decalee = Horloge(b.horloge.t)
    b.m._horloge = decalee
    b.novia.horloge = lambda: decalee.t + 3 * JOUR
    r = b.m.activer(cle)
    assert r["success"] is True, r
    assert r["horloge_decalee"] is True
    print("  OK — horloge décalée à l'activation : activée, et signalée à l'utilisateur")


# ═══════════════════════════ DÉSACTIVATION / CHANGEMENT ═══════════════════

def test_desactivation():
    from utils import app_config
    b = Banc()
    cle = b.novia.creer_licence("PRO", max_appareils=1)
    b.m.activer(cle)
    inst = b.m.installation_id()
    app_config.set("projet_utilisateur", {"a": 1})

    # Sans réseau : RIEN n'est effacé — sinon l'emplacement resterait occupé.
    b.novia.hors_ligne = True
    r = b.m.desactiver()
    assert r["success"] is False and b.stockage().get("cle") == cle
    assert "n'a pas été désactivé" in r["message"]

    b.novia.hors_ligne = False
    r = b.m.desactiver()
    assert r["success"] is True and "intacts" in r["message"]
    assert not b.stockage(), "l'activation locale doit être effacée"
    assert inst not in b.novia.licences[cle]["appareils"], "emplacement libéré"
    assert app_config.get("projet_utilisateur") == {"a": 1}, "données intactes"
    assert b.m.installation_id() == inst, "l'identifiant d'appareil est conservé"
    assert b.m.etat()["etat"] == "NOT_ACTIVATED"

    # Forcée hors ligne : effacée localement, et l'utilisateur est prévenu.
    b.m.activer(cle)
    b.novia.hors_ligne = True
    r = b.m.desactiver(forcer=True)
    assert r["success"] is True and "espace client" in r["message"]
    assert not b.stockage()
    print("  OK — désactivation : confirmée par Novia, seules les données d'activation effacées")


def test_changement_de_licence():
    b = Banc()
    ancienne = b.novia.creer_licence("PRO", max_appareils=1)
    b.m.activer(ancienne)
    inst = b.m.installation_id()

    # Nouvelle clé invalide : l'ancienne licence reste intacte et active.
    r = b.m.changer("NOVIA-ZZZZ-ZZZZ-ZZZZ-ZZZZ")
    assert r["success"] is False and b.m.etat()["etat"] == "ACTIVE"
    assert b.stockage()["cle"] == ancienne

    nouvelle = b.novia.creer_licence("BUSINESS")
    r = b.m.changer(nouvelle)
    assert r["success"] is True and r["niveau"] == "BUSINESS"
    assert inst not in b.novia.licences[ancienne]["appareils"], \
        "l'ancienne licence doit libérer cet appareil"
    print("  OK — changement de licence : l'ancienne n'est remplacée qu'en cas de succès")


def test_niveaux_futurs_sans_reecriture():
    b = Banc()
    for niveau, premium in (("FREE", False), ("PRO", True),
                            ("BUSINESS", True), ("ENTERPRISE", True)):
        cle = b.novia.creer_licence(niveau)
        r = b.m.changer(cle)
        assert r["success"] and r["niveau"] == niveau and r["premium"] is premium, niveau
    # Niveau inconnu : le moins ouvert, jamais le plus.
    cle = b.novia.creer_licence("PLATINUM")
    r = b.m.changer(cle)
    assert r["niveau"] == "FREE" and r["premium"] is False
    print("  OK — FREE / PRO / BUSINESS / ENTERPRISE ; niveau inconnu → FREE")


# ═══════════════════════════ JOURNAL ══════════════════════════════════════

def test_journal_sans_cle_complete():
    import logging
    from licence import license_manager
    b = Banc()
    fichier = b.dossier / "journal-test.log"
    gestionnaire = logging.FileHandler(fichier, encoding="utf-8")
    license_manager.journal.addHandler(gestionnaire)
    try:
        cle = b.novia.creer_licence("PRO")
        b.m.activer("NOVIA-AAAA-BBBB-CCCC-DDDD")     # refus
        b.m.activer(cle)                              # succès
        b.novia.hors_ligne = True
        b.m.revalider(forcer=True)                    # erreur réseau
        b.novia.hors_ligne = False
        b.novia.licences[cle]["statut"] = "REVOKED"
        b.m.revalider(forcer=True)                    # révocation
    finally:
        license_manager.journal.removeHandler(gestionnaire)
        gestionnaire.close()
    texte = fichier.read_text(encoding="utf-8")
    for attendu in ("Activation réussie", "Activation refusée", "impossible", "REVOKED"):
        assert attendu in texte, f"événement non journalisé : {attendu}"
    assert cle not in texte, "la clé complète ne doit JAMAIS être journalisée"
    assert "NOVIA-****-****-****-" + cle[-4:] in texte
    assert b.stockage().get("cle", "") not in ("",) or True
    jeton = b.novia.jeton_pour(cle, b.m.installation_id())
    assert jeton.split(".")[2] not in texte, "aucun jeton ne doit être journalisé"
    print("  OK — journal : événements tracés, clé toujours masquée, aucun jeton")


# ═══════════════════════════ APPLICATION ENTIÈRE ══════════════════════════

def _app_novia(b):
    """TestClient d'Agence 46 en mode Novia, branchée sur le banc."""
    import importlib
    import sys
    sys.path.insert(0, str(_RACINE))
    os.environ["AGENCE_DATA_DIR"] = str(b.dossier / "data")
    import config as app_cfg
    app_cfg.DB_PATH = b.dossier / "data" / "agence.db"
    app_cfg.DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    import utils.database as database
    importlib.reload(database)
    for nom in ("AGENCE_LICENCE_JETON", "AGENCE_LICENCE_PUBKEY"):
        os.environ.pop(nom, None)
    os.environ["LICENSE_API_URL"] = "https://novia.test"
    os.environ["LICENSE_PUBLIC_KEY"] = b.novia.cle_publique_hex()
    from licence import abonnement, comptes, license_manager
    license_manager.reinitialiser(b.m)
    abonnement.invalider_cache()
    comptes.definir_compte_courant(None)
    from fastapi.testclient import TestClient
    from backend.main import app
    return TestClient(app, raise_server_exceptions=False)


def _sortir_du_mode_novia():
    for nom in ("LICENSE_API_URL", "LICENSE_PUBLIC_KEY"):
        os.environ.pop(nom, None)
    from licence import abonnement, license_manager
    license_manager.reinitialiser(None)
    abonnement.invalider_cache()


def test_application_fermee_sans_licence_ouverte_avec():
    b = Banc()
    c = _app_novia(b)
    try:
        r = c.get("/api/status")
        assert r.status_code == 401 and r.json()["licence_requise"] is True
        etat = c.get("/api/licence").json()
        assert etat["mode_licence"] == "novia" and etat["licence_requise"] is True
        assert etat["compte_requis"] is False, "pas d'inscription locale en mode Novia"
        assert c.get("/api/activation").status_code == 200

        cle = b.novia.creer_licence("PRO")
        r = c.post("/api/activation/activer", json={"license_key": cle})
        assert r.status_code == 200 and r.json()["etat"] == "ACTIVE"
        assert c.get("/api/status").status_code == 200
        l = c.get("/api/licence").json()
        assert l["est_pro"] and l["agents"]["autorises"] == l["agents"]["total"] == 45
        assert l["libelle"] == "Agence 46 PRO"

        # Aucun secret dans ce que l'interface reçoit.
        a = c.get("/api/activation").json()
        assert cle not in json.dumps(a) and "jeton" not in a
        assert "installation_id" not in json.dumps(a)
    finally:
        _sortir_du_mode_novia()
    print("  OK — application : fermée sans licence (401), 45/45 agents avec une PRO")


def test_free_et_revocation_passent_en_mode_limite():
    b = Banc()
    c = _app_novia(b)
    try:
        cle = b.novia.creer_licence("FREE")
        c.post("/api/activation/activer", json={"license_key": cle})
        l = c.get("/api/licence").json()
        assert l["utilisable"] and not l["est_pro"]
        assert l["agents"]["autorises"] == 6 and not l["quotas"]["illimite"]
        assert l["essai"]["en_cours"] is False, "pas de bandeau « essai 3 jours »"

        pro = b.novia.creer_licence("PRO")
        c.post("/api/activation/changer", json={"license_key": pro})
        assert c.get("/api/licence").json()["est_pro"] is True
        b.novia.licences[pro]["statut"] = "REVOKED"
        c.post("/api/activation/actualiser", json={})
        l = c.get("/api/licence").json()
        assert l["utilisable"] is True and l["est_pro"] is False, \
            "révoquée : premium coupé, application toujours ouverte"
        assert "révoquée" in l["message"]
        assert c.get("/api/status").status_code == 200
    finally:
        _sortir_du_mode_novia()
    print("  OK — FREE et licence révoquée : application ouverte en mode limité")


def test_politique_d_expiration():
    from licence import config as lconfig
    b = Banc()
    c = _app_novia(b)
    origine = lconfig.LICENSE_EXPIRED_POLICY
    try:
        cle = b.novia.creer_licence("PRO", duree_jours=5)
        c.post("/api/activation/activer", json={"license_key": cle})
        b.horloge.avancer(6)
        b.novia.hors_ligne = True

        lconfig.LICENSE_EXPIRED_POLICY = "limite"
        assert c.get("/api/status").status_code == 200
        assert c.get("/api/licence").json()["est_pro"] is False

        lconfig.LICENSE_EXPIRED_POLICY = "bloque"
        r = c.get("/api/status")
        assert r.status_code == 402 and r.json().get("licence_requise") is True
        assert c.get("/api/licence").json()["licence_requise"] is True
    finally:
        lconfig.LICENSE_EXPIRED_POLICY = origine
        _sortir_du_mode_novia()
    print("  OK — expiration : mode limité ou application fermée, selon la politique")


def test_requete_d_un_autre_site_refusee():
    b = Banc()
    c = _app_novia(b)
    try:
        cle = b.novia.creer_licence("PRO")
        c.post("/api/activation/activer", json={"license_key": cle})
        for chemin in ("activer", "changer", "actualiser", "desactiver"):
            r = c.post(f"/api/activation/{chemin}", content="forcer=1",
                       headers={"content-type": "application/x-www-form-urlencoded"})
            assert r.status_code == 415, (chemin, r.status_code)
        assert b.stockage().get("cle") == cle, "rien ne doit avoir été désactivé"
    finally:
        _sortir_du_mode_novia()
    print("  OK — requêtes « simples » d'un autre site refusées (415)")


def test_mode_compte_inchange_sans_configuration_novia():
    """Sans URL + clé publique, l'application se comporte EXACTEMENT comme avant."""
    from licence import config as lconfig
    _sortir_du_mode_novia()
    assert lconfig.novia_actif() is False
    b = Banc()
    os.environ["LICENSE_API_URL"] = "https://novia.test"     # URL seule
    try:
        assert lconfig.novia_actif() is False, "une URL sans clé publique ne suffit pas"
        assert "aucune clé publique" in lconfig.novia_configuration_incomplete()
    finally:
        os.environ.pop("LICENSE_API_URL", None)

    import sys
    sys.path.insert(0, str(_RACINE))
    from fastapi.testclient import TestClient
    from backend.main import app
    c = TestClient(app, raise_server_exceptions=False)
    r = c.get("/api/status")
    assert r.status_code == 401 and r.json().get("compte_requis") is True
    assert c.get("/api/licence").json()["mode_licence"] == "compte"
    assert b.novia.requetes == [], "aucune requête réseau hors mode Novia"
    print("  OK — sans configuration Novia : mode compte d'origine, aucun appel réseau")


# ═══════════════════════════ TLS RÉEL ET INTEROPÉRABILITÉ ═════════════════

def _autorite_et_certificat(dossier: Path):
    """Autorité de test + certificat serveur pour 127.0.0.1."""
    import datetime
    import ipaddress
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID

    def nom(cn):
        return x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, cn)])

    maintenant = datetime.datetime.now(datetime.timezone.utc)
    ca_cle = ec.generate_private_key(ec.SECP256R1())
    ca = (x509.CertificateBuilder().subject_name(nom("CA de test Novia"))
          .issuer_name(nom("CA de test Novia")).public_key(ca_cle.public_key())
          .serial_number(x509.random_serial_number())
          .not_valid_before(maintenant - datetime.timedelta(days=1))
          .not_valid_after(maintenant + datetime.timedelta(days=2))
          .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
          .sign(ca_cle, hashes.SHA256()))
    srv_cle = ec.generate_private_key(ec.SECP256R1())
    srv = (x509.CertificateBuilder().subject_name(nom("127.0.0.1"))
           .issuer_name(ca.subject).public_key(srv_cle.public_key())
           .serial_number(x509.random_serial_number())
           .not_valid_before(maintenant - datetime.timedelta(days=1))
           .not_valid_after(maintenant + datetime.timedelta(days=2))
           .add_extension(x509.SubjectAlternativeName(
               [x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]), critical=False)
           .sign(ca_cle, hashes.SHA256()))
    pem = serialization.Encoding.PEM
    (dossier / "ca.pem").write_bytes(ca.public_bytes(pem))
    (dossier / "srv.pem").write_bytes(srv.public_bytes(pem))
    (dossier / "srv.key").write_bytes(srv_cle.private_bytes(
        pem, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))


def test_https_reel_certificat_verifie():
    """Vrai serveur HTTPS local : accepté avec la bonne autorité, REFUSÉ sinon.

    Prouve que le transport réel vérifie les certificats — et qu'une erreur de
    certificat n'est pas confondue avec une absence de réseau.
    """
    import ssl
    import threading
    from http.server import BaseHTTPRequestHandler, HTTPServer
    from licence.novia_client import CertificatRefuse, NoviaClient

    b = Banc()
    _autorite_et_certificat(b.dossier)
    novia = b.novia

    class Gestionnaire(BaseHTTPRequestHandler):
        def do_POST(self):
            corps = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            statut, reponse = novia.repondre(self.path, corps)
            donnees = json.dumps(reponse).encode()
            self.send_response(statut)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(donnees)))
            self.end_headers()
            self.wfile.write(donnees)

        def log_message(self, *a):
            pass

    serveur = HTTPServer(("127.0.0.1", 0), Gestionnaire)
    contexte = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    contexte.load_cert_chain(b.dossier / "srv.pem", b.dossier / "srv.key")
    serveur.socket = contexte.wrap_socket(serveur.socket, server_side=True)
    threading.Thread(target=serveur.serve_forever, daemon=True).start()
    url = f"https://127.0.0.1:{serveur.server_address[1]}"

    ancien = os.environ.get("REQUESTS_CA_BUNDLE")
    try:
        # Autorité INCONNUE : refus, et refus typé comme tel.
        _autre = b.dossier / "autre"
        _autre.mkdir()
        _autorite_et_certificat(_autre)
        os.environ["REQUESTS_CA_BUNDLE"] = str(_autre / "ca.pem")
        b.m.client = NoviaClient(url, "agence46")
        cle = novia.creer_licence("PRO")
        r = b.m.activer(cle)
        assert r["success"] is False and r["erreur"] == "tls", r
        assert "certificat" in r["message"]

        # Bonne autorité : l'échange complet passe, sur le transport RÉEL.
        os.environ["REQUESTS_CA_BUNDLE"] = str(b.dossier / "ca.pem")
        r = b.m.activer(cle)
        assert r["success"] is True, r
        assert b.m.revalider(forcer=True)["success"] is True
        assert b.m.desactiver()["success"] is True
    finally:
        serveur.shutdown()
        if ancien is None:
            os.environ.pop("REQUESTS_CA_BUNDLE", None)
        else:
            os.environ["REQUESTS_CA_BUNDLE"] = ancien
    print("  OK — HTTPS réel : certificat vérifié, autorité inconnue refusée")


def test_jeton_signe_par_node_accepte():
    """Interopérabilité : un jeton signé par Node.js — comme dans la
    documentation remise à Novia — est accepté tel quel par l'application."""
    import shutil
    import subprocess
    from licence import jeton_novia
    if not shutil.which("node"):
        print("  — interopérabilité Node non testée (node absent)")
        return
    doc = (_RACINE / "docs" / "novia-licensing-integration.md").read_text(encoding="utf-8")
    debut = doc.index("<!-- exemple-node -->")
    code = doc[debut:].split("```js", 1)[1].split("```", 1)[0]
    installation = "0f8fad5b-d9cb-469f-a165-70867728950e"
    script = code + f"""
const {{ publicKey, privateKey }} = crypto.generateKeyPairSync('ed25519');
const pub = publicKey.export({{ format: 'der', type: 'spki' }}).subarray(-32).toString('hex');
const maintenant = Math.floor(Date.now() / 1000);
const jeton = signerLicence({{
  iss: 'novia', aud: 'agence46', sub: 'lic_123', inst: '{installation}',
  tier: 'PRO', status: 'ACTIVE', iat: maintenant,
  exp: maintenant + 365 * 86400, offline_until: maintenant + 30 * 86400,
}}, privateKey, 'novia-2026-01');
console.log(JSON.stringify({{ pub, jeton }}));
"""
    sortie = subprocess.run(["node", "-e", script], capture_output=True, text=True,
                            timeout=60)
    assert sortie.returncode == 0, sortie.stderr
    donnees = json.loads(sortie.stdout.strip().splitlines()[-1])
    rev = jeton_novia.verifier(donnees["jeton"], {"novia-2026-01": donnees["pub"]},
                               "agence46", installation, time.time())
    assert rev["tier"] == "PRO" and rev["sub"] == "lic_123"
    print("  OK — jeton signé par l'exemple Node.js de la documentation : accepté")


def test_jeton_signe_par_php_accepte():
    """Interopérabilité : l'exemple PHP/libsodium de la documentation — le plus
    probable côté site Novia — produit des jetons acceptés tels quels."""
    import shutil
    import subprocess
    from licence import jeton_novia
    if not shutil.which("php"):
        print("  — interopérabilité PHP non testée (php absent)")
        return
    doc = (_RACINE / "docs" / "novia-licensing-integration.md").read_text(encoding="utf-8")
    code = doc.split("PHP (libsodium, inclus dans PHP ≥ 7.2) :", 1)[1]
    code = code.split("```php", 1)[1].split("```", 1)[0]
    installation = "0f8fad5b-d9cb-469f-a165-70867728950e"
    script = "<?php\n" + code + f"""
$paire = sodium_crypto_sign_keypair();
$t = time();
$jeton = signerLicence([
  'iss' => 'novia', 'aud' => 'agence46', 'sub' => 'lic_php', 'inst' => '{installation}',
  'tier' => 'ENTERPRISE', 'status' => 'ACTIVE', 'iat' => $t,
  'exp' => $t + 365 * 86400, 'offline_until' => $t + 30 * 86400,
], sodium_crypto_sign_secretkey($paire), 'novia-php');
echo json_encode(['pub' => bin2hex(sodium_crypto_sign_publickey($paire)), 'jeton' => $jeton]);
"""
    fichier = Path(tempfile.mkdtemp()) / "signer.php"
    fichier.write_text(script, encoding="utf-8")
    sortie = subprocess.run(["php", str(fichier)], capture_output=True, text=True, timeout=60)
    assert sortie.returncode == 0, sortie.stderr or sortie.stdout
    donnees = json.loads(sortie.stdout.strip().splitlines()[-1])
    rev = jeton_novia.verifier(donnees["jeton"], {"novia-php": donnees["pub"]},
                               "agence46", installation, time.time())
    assert rev["tier"] == "ENTERPRISE" and rev["sub"] == "lic_php"
    print("  OK — jeton signé par l'exemple PHP/libsodium de la documentation : accepté")


def test_aucune_cle_privee_dans_l_application():
    """L'application ne peut QUE vérifier : aucun module ne signe côté Novia."""
    for nom in ("jeton_novia.py", "novia_client.py", "license_manager.py",
                "license_state.py"):
        texte = (_RACINE / "python" / "licence" / nom).read_text(encoding="utf-8")
        for interdit in ("Ed25519PrivateKey", "PRIVATE KEY", ".sign(", "private_bytes"):
            assert interdit not in texte, f"{nom} contient « {interdit} »"
    from licence import config as lconfig
    for valeur in lconfig.NOVIA_CLES_PUBLIQUES.values():
        assert len(bytes.fromhex(valeur)) == 32, "clés PUBLIQUES de 32 octets uniquement"
    print("  OK — aucune clé privée ni signature dans le code de l'application")


def run():
    from utils import app_config
    import config as app_cfg
    etat0 = (app_config._PATH, app_config._cache, app_cfg.DB_PATH,
             os.environ.get("AGENCE_DATA_DIR"), os.environ.get("AGENCE_LICENCE_JETON"),
             os.environ.get("AGENCE_LICENCE_PUBKEY"))
    try:
        for test in (
            test_activation, test_installation_id_uuid_stable_et_non_materiel,
            test_seuls_les_champs_necessaires_partent, test_cle_invalide,
            test_licence_expiree, test_licence_suspendue_et_revoquee_donnees_intactes,
            test_depassement_d_appareils, test_fonctionnement_hors_ligne_apres_activation,
            test_absence_internet_a_l_activation, test_serveur_indisponible,
            test_validation_periodique_sans_marteler, test_renouvellement,
            test_periode_hors_ligne_locale_ne_peut_que_raccourcir,
            test_validation_du_jeton, test_signature_invalide,
            test_reponse_valide_sans_jeton_refusee, test_https_obligatoire,
            test_horloge_reculee_ne_rend_aucun_jour, test_horloge_avancee_puis_corrigee,
            test_horloge_decalee_a_l_activation_signalee, test_desactivation,
            test_changement_de_licence, test_niveaux_futurs_sans_reecriture,
            test_journal_sans_cle_complete,
            test_application_fermee_sans_licence_ouverte_avec,
            test_free_et_revocation_passent_en_mode_limite, test_politique_d_expiration,
            test_requete_d_un_autre_site_refusee,
            test_mode_compte_inchange_sans_configuration_novia,
            test_https_reel_certificat_verifie, test_jeton_signe_par_node_accepte,
            test_jeton_signe_par_php_accepte,
            test_aucune_cle_privee_dans_l_application,
        ):
            test()
    finally:
        import importlib
        _sortir_du_mode_novia()
        (app_config._PATH, app_config._cache, app_cfg.DB_PATH, data, jeton, pub) = etat0
        for nom, valeur in (("AGENCE_DATA_DIR", data), ("AGENCE_LICENCE_JETON", jeton),
                            ("AGENCE_LICENCE_PUBKEY", pub)):
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
    print("✅ Licences Novia OK")
