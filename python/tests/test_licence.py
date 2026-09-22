"""Tests de la licence, de la version d'essai et de la version Pro.

Ce que la suite vérifie, dans l'ordre du cahier des charges :

- les limites d'essai appliquées sont EXACTEMENT celles configurées
  (6 agents, 20 requêtes/jour, 5 requêtes/heure) ;
- un utilisateur n'est JAMAIS Pro sur la foi d'une valeur locale modifiable :
  seule une licence signée par l'émetteur débloque quoi que ce soit ;
- la version Pro retrouve TOUS les agents et n'a plus de quota ;
- les agents verrouillés restent visibles (découverte de l'offre Pro) ;
- les routes réservées répondent 402, celles qui doivent rester ouvertes
  répondent normalement ;
- aucune clé secrète n'est embarquée dans le code distribué.
"""
import _setup  # noqa: F401
import json
import os
import tempfile
import time
from pathlib import Path

_RACINE = Path(__file__).resolve().parents[2]


# ═══════════════════════════ OUTILLAGE ════════════════════════════════════

def _isoler(tmp=None):
    """Config + base de données dans un dossier temporaire.

    Sans cette isolation, la suite écrirait dans la configuration et
    l'historique RÉELS de la machine qui la lance — et le quota de l'essai
    consommerait les requêtes de l'utilisateur.
    """
    dossier = Path(tmp or tempfile.mkdtemp())
    from utils import app_config
    app_config._PATH = dossier / "config.json"
    app_config._cache = None
    os.environ["AGENCE_DATA_DIR"] = str(dossier / "data")

    # config.DB_PATH est lu à l'import : le recalculer pour ce dossier.
    import config as app_cfg
    import importlib
    app_cfg.DB_PATH = dossier / "data" / "agence.db"
    app_cfg.DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    import utils.database as database
    importlib.reload(database)

    # `_setup.py` place une licence Pro éphémère pour que la suite historique
    # exerce l'application complète. CE module teste l'inverse : chaque cas
    # repart donc SANS licence, et installe la sienne s'il en a besoin.
    from licence import abonnement, comptes
    os.environ.pop("AGENCE_LICENCE_JETON", None)
    abonnement.invalider_cache()
    # Aucun compte connecté au départ : chaque cas pose le sien s'il en veut
    # un. Sans cette remise à zéro, le compte d'un cas précédent — dans une
    # base qui n'existe plus — resterait le compte courant.
    comptes.definir_compte_courant(None)
    return dossier


def _compte_essai(abonne: bool = False):
    """Crée un compte et le pose comme COMPTE COURANT du test.

    Les fonctions de `licence.gate` lisent le compte de la requête en cours.
    Hors HTTP — ce que fait ce module quand il teste la porte directement —
    il faut donc poser ce compte à la main, exactement comme le fait le
    middleware à chaque requête.
    """
    import sys
    sys.path.insert(0, str(_RACINE))
    import _client as outil
    from licence import comptes
    compte = outil.creer_compte(abonne=abonne)
    comptes.definir_compte_courant(compte)
    return compte


class _Emetteur:
    """Émetteur de licences de test : joue le rôle du serveur.

    La clé PRIVÉE naît ici et meurt ici — exactement comme en production, où
    elle ne quitte jamais le serveur. L'application ne reçoit que la publique.
    """

    def __init__(self):
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
        from cryptography.hazmat.primitives import serialization
        self._priv = Ed25519PrivateKey.generate()
        self.publique = self._priv.public_key().public_bytes(
            serialization.Encoding.Raw, serialization.PublicFormat.Raw).hex()

    def installer(self):
        os.environ["AGENCE_LICENCE_PUBKEY"] = self.publique

    def emettre(self, sujet="client-test", validite_s=86400, plan="PRO",
                emetteur="agence46"):
        from licence.verification import b64e
        charge = b64e(json.dumps({
            "sub": sujet, "plan": plan, "iss": emetteur,
            "iat": int(time.time()), "exp": int(time.time() + validite_s),
        }).encode())
        return f"AGENCE1.{charge}.{b64e(self._priv.sign(charge.encode('ascii')))}"


def _client(abonne: bool = False):
    """Client HTTP sur l'application réelle (routes et gardes comprises).

    Compte NON abonné par défaut : ce module vérifie précisément ce que la
    version d'essai autorise et refuse.
    """
    import sys
    sys.path.insert(0, str(_RACINE))
    import _client as outil
    from backend.main import app
    return outil.client(app, abonne=abonne)


# ═══════════════════════════ LIMITES DE L'ESSAI ═══════════════════════════

def test_limites_configurees_exactement():
    """Les valeurs du cahier des charges, à la lettre, dans UN seul fichier."""
    from licence import config as lc
    assert lc.TRIAL_MAX_AGENTS == 6
    assert lc.TRIAL_DAILY_REQUEST_LIMIT == 20
    assert lc.TRIAL_HOURLY_REQUEST_LIMIT == 5
    # Pro sans plafond d'agents : le projet en compte 45, en figer 36 en
    # verrouillerait 9 aux abonnés payants.
    assert lc.PRO_MAX_AGENTS is None
    assert lc.PRO_DAILY_REQUEST_LIMIT is None and lc.PRO_HOURLY_REQUEST_LIMIT is None
    assert lc.MICROSOFT_STORE_URL == \
        "https://apps.microsoft.com/detail/9nltgfr2btsp?hl=fr-FR&gl=FR"
    print("  OK — limites d'essai exactes (6 agents, 20/jour, 5/heure), Pro illimité")


def test_essai_six_agents_les_autres_restent_visibles():
    _isoler()
    from agents import TOUS_LES_AGENTS
    from licence import gate

    actifs = gate.agents_autorises(TOUS_LES_AGENTS)
    assert len(actifs) == 6, f"{len(actifs)} agents actifs au lieu de 6"
    # AUCUN agent n'a disparu du projet : ils sont tous instanciés et listés.
    assert len(TOUS_LES_AGENTS) == 45
    etat = gate.etat_public(TOUS_LES_AGENTS)
    assert etat["agents"]["total"] == 45
    assert etat["agents"]["autorises"] == 6
    # Un agent par famille : l'essai montre l'éventail, pas six variantes.
    familles = {a.groupe for a in actifs}
    assert len(familles) == 6, f"agents d'essai concentrés sur {familles}"
    print("  OK — 6 agents actifs (un par famille), les 45 restent présents et listés")


def test_cycle_n_interroge_que_les_agents_autorises():
    """Le plafond vit dans l'ORCHESTRATEUR, pas dans l'interface : une requête
    fabriquée à la main ne peut pas réveiller les 45 agents."""
    _isoler()
    from agents import TOUS_LES_AGENTS
    from agents.orchestrateur import ChefOrchestre

    actifs, verrouilles = ChefOrchestre._agents_du_cycle(TOUS_LES_AGENTS)
    assert len(actifs) == 6
    assert verrouilles == 39
    print("  OK — un cycle d'essai n'interroge que 6 agents sur 45")


def test_quota_horaire_puis_journalier():
    _isoler()
    _compte_essai()
    from licence import gate

    for i in range(5):
        gate.autoriser_analyse(["BTC-USD"])
    try:
        gate.autoriser_analyse(["BTC-USD"])
        raise AssertionError("la 6e requête de l'heure aurait dû être refusée")
    except gate.QuotaDepasse as e:
        assert e.periode == "heure" and e.limite == 5
        assert e.payload()["pro_requis"] is True
    print("  OK — 5 requêtes par heure, la 6e est refusée")


def test_quota_compte_dans_la_base_pas_dans_le_navigateur():
    """Le compteur est rattaché au COMPTE, côté serveur. Le vider côté client
    n'existe pas : il n'y a rien à vider côté client."""
    _isoler()
    _compte_essai()
    from licence import abonnement, quota

    compte = abonnement.compte_id()
    # Rattaché au COMPTE UTILISATEUR : le quota le suit d'un appareil à
    # l'autre, et se déconnecter ne le remet pas à zéro.
    assert compte.startswith("compte:"), compte
    quota.enregistrer(compte)
    quota.enregistrer(compte)
    assert quota.utilisation(compte)["jour"] == 2
    # Un AUTRE compte a son propre compteur (un abonné ne récupère pas le
    # quota consommé pendant son essai, et réciproquement).
    assert quota.utilisation("compte:quelqu-un-dautre")["jour"] == 0
    print("  OK — compteur en base, rattaché au compte, invisible du navigateur")


def test_un_seul_symbole_par_analyse_en_essai():
    _isoler()
    _compte_essai()
    from licence import gate
    retenus, ecartes = gate.limiter_symboles(["BTC-USD", "AAPL", "ETH-USD"])
    assert retenus == ["BTC-USD"]
    assert ecartes == ["AAPL", "ETH-USD"]
    print("  OK — un symbole par analyse en essai, les autres sont signalés")


# ═══════════════════════════ ON NE DEVIENT PAS PRO SANS LICENCE ═══════════

def test_aucune_valeur_locale_ne_rend_pro():
    """Le cœur de la protection : écrire « je suis Pro » ne suffit pas."""
    _isoler()
    os.environ.pop("AGENCE_LICENCE_PUBKEY", None)
    from utils import app_config
    from licence import abonnement, gate

    for cle, valeur in [("pro", True), ("est_pro", "true"), ("plan", "PRO"),
                        ("licence_jeton", "PRO"), ("licence_jeton", "AGENCE1.x.y")]:
        app_config.set(cle, valeur)
        abonnement.invalider_cache()
        assert not abonnement.est_pro(), f"« {cle}={valeur} » a suffi à passer Pro"
        assert not gate.autorise("automatisations")
    print("  OK — aucune valeur locale ne donne le statut Pro")


def test_licence_contrefaite_refusee():
    """Une licence signée par une AUTRE clé est refusée : sans la clé privée
    de l'émetteur, on ne fabrique pas de jeton valide."""
    _isoler()
    vrai, faux = _Emetteur(), _Emetteur()
    vrai.installer()                       # l'application ne connaît que « vrai »
    from utils import app_config
    from licence import abonnement

    app_config.set("licence_jeton", faux.emettre())
    abonnement.invalider_cache()
    assert not abonnement.est_pro()
    assert abonnement.etat_complet()["etat"] == "PAYMENT_REQUIRED"
    print("  OK — licence signée par un autre émetteur : refusée")


def test_licence_alteree_refusee():
    """Modifier la charge utile (allonger la validité) invalide la signature."""
    _isoler()
    e = _Emetteur(); e.installer()
    from licence.verification import b64e, lire_jeton
    from licence.etat import EtatLicence

    _, charge, signature = e.emettre().split(".")
    trafiquee = b64e(json.dumps({"sub": "moi", "plan": "PRO", "iss": "agence46",
                                 "exp": int(time.time() + 10 ** 9)}).encode())
    etat, infos = lire_jeton(f"AGENCE1.{trafiquee}.{signature}")
    assert etat is EtatLicence.TRIAL
    assert "Signature" in infos.get("erreur", "")
    print("  OK — charge utile altérée : signature invalide")


def test_sans_cle_publique_aucune_licence_ne_passe():
    """Échec FERMÉ : sans émetteur connu, même un jeton bien formé est refusé."""
    _isoler()
    e = _Emetteur()                        # volontairement NON installé
    os.environ.pop("AGENCE_LICENCE_PUBKEY", None)
    from licence.verification import lire_jeton
    from licence.etat import EtatLicence
    etat, _ = lire_jeton(e.emettre())
    assert etat is EtatLicence.TRIAL
    print("  OK — sans clé publique configurée, aucune licence n'est acceptée")


def test_aucune_cle_privee_dans_le_code_distribue():
    """Le dépôt n'embarque QUE des clés publiques — et pas même celle-ci tant
    qu'aucun émetteur n'existe."""
    from licence import verification
    assert verification.CLE_PUBLIQUE_EMETTEUR == "" or \
        len(bytes.fromhex(verification.CLE_PUBLIQUE_EMETTEUR)) == 32
    source = (_RACINE / "python" / "licence").rglob("*.py")
    for fichier in source:
        texte = fichier.read_text(encoding="utf-8")
        assert "PRIVATE KEY" not in texte, f"{fichier.name} contient une clé privée"
        assert "Ed25519PrivateKey.generate" not in texte, (
            f"{fichier.name} fabrique une clé privée : elle n'a rien à faire "
            "dans le code distribué (seul l'émetteur en produit)")
    print("  OK — aucune clé privée dans le paquet licence")


# ═══════════════════════════ VERSION PRO ══════════════════════════════════

def test_pro_debloque_tout():
    _isoler()
    e = _Emetteur(); e.installer()
    from agents import TOUS_LES_AGENTS
    from licence import abonnement, gate

    res = abonnement.enregistrer_jeton(e.emettre(sujet="abonne-1"))
    assert res["success"] and res["etat"] == "PRO_ACTIVE"

    etat = gate.etat_public(TOUS_LES_AGENTS)
    assert etat["est_pro"] is True
    assert etat["agents"]["autorises"] == len(TOUS_LES_AGENTS) == 45
    assert etat["quotas"]["illimite"] is True
    assert all(etat["features"].values()), "une fonctionnalité reste verrouillée en Pro"
    assert gate.limiter_symboles(["BTC-USD", "AAPL", "ETH-USD"])[1] == []
    # Le quota ne bloque plus, quel que soit le nombre de requêtes.
    for _ in range(30):
        gate.verifier_quota()
    assert abonnement.compte_id() == "pro:abonne-1"
    print("  OK — Pro : 45/45 agents, quotas levés, toutes fonctionnalités ouvertes")


def test_licence_expiree_revient_a_lessai():
    _isoler()
    e = _Emetteur(); e.installer()
    from agents import TOUS_LES_AGENTS
    from utils import app_config
    from licence import abonnement, gate

    app_config.set("licence_jeton", e.emettre(validite_s=-90000))
    abonnement.invalider_cache()
    etat = gate.etat_public(TOUS_LES_AGENTS)
    assert etat["etat"] == "PRO_EXPIRED"
    assert etat["est_pro"] is False
    assert etat["agents"]["autorises"] == 6
    assert not gate.autorise("automatisations")
    print("  OK — licence expirée : PRO_EXPIRED, retour aux limites d'essai")


def test_etats_du_cycle_de_vie():
    from licence.etat import EtatLicence
    attendus = {"COMPTE_REQUIS", "TRIAL", "TRIAL_EXPIRED", "PRO_ACTIVE",
                "PRO_EXPIRED", "PAYMENT_REQUIRED", "SUSPENDU"}
    assert {e.value for e in EtatLicence} == attendus
    # UN SEUL état débloque les fonctionnalités Pro.
    assert [e.value for e in EtatLicence if e.est_pro] == ["PRO_ACTIVE"]
    # DEUX SEULEMENT rendent l'application utilisable.
    assert sorted(e.value for e in EtatLicence if e.utilisable) == \
        ["PRO_ACTIVE", "TRIAL"]
    # Une valeur inconnue retombe sur l'état le plus FERMÉ, jamais sur l'essai.
    assert EtatLicence.depuis("n_importe_quoi") is EtatLicence.COMPTE_REQUIS
    print("  OK — 7 états, 1 seul Pro, 2 seuls utilisables, repli fermé")


def test_aucun_faux_paiement():
    """Sans émetteur configuré, l'activation ÉCHOUE en le disant — elle ne
    prétend jamais qu'un paiement a eu lieu."""
    _isoler()
    os.environ.pop("LICENCE_API_URL", None)
    from licence import abonnement
    res = abonnement.activer("UNE-CLE-QUELCONQUE")
    assert res["success"] is False
    assert res["etat"] == "PAYMENT_REQUIRED"
    assert not abonnement.est_pro()
    # HTTP non chiffré refusé : le jeton transiterait en clair.
    os.environ["LICENCE_API_URL"] = "http://exemple.invalide"
    assert abonnement.url_emetteur() == ""
    os.environ["LICENCE_API_URL"] = "https://exemple.invalide"
    assert abonnement.url_emetteur() == "https://exemple.invalide"
    os.environ.pop("LICENCE_API_URL", None)
    print("  OK — aucun paiement simulé, émetteur en HTTPS obligatoire")


# ═══════════════════════════ ROUTES HTTP ══════════════════════════════════

def test_routes_pro_refusees_en_essai():
    _isoler()
    c = _client()
    reservees = [
        ("POST", "/api/auto-trader/start", {"symboles": ["BTC-USD"]}, "automatisations"),
        ("POST", "/api/backtest", {"symbole": "BTC-USD"}, "workflows_avances"),
        ("POST", "/api/preflight", {}, "workflows_avances"),
        ("POST", "/api/rapport/test", {}, "workflows_avances"),
        ("POST", "/api/trading/config", {"levier": 3}, "parametres_avances"),
        ("POST", "/api/risk/config", {"actif": True}, "parametres_avances"),
        ("POST", "/api/trading/levier", {"levier": 4, "source": "interface"}, "parametres_avances"),
        ("POST", "/api/comptes/basculer", {"id": "x"}, "administration_avancee"),
        ("POST", "/api/mt5/auto-trading", {"active": True}, "automatisations"),
    ]
    for methode, url, corps, feature in reservees:
        r = c.request(methode, url, json=corps)
        assert r.status_code == 402, f"{url} → {r.status_code} au lieu de 402"
        d = r.json()
        assert d["pro_requis"] is True and d["feature"] == feature, f"{url} : {d}"
        assert d["error"] == "Cette fonctionnalité est disponible dans la version Pro."
        assert d["store_url"].startswith("https://apps.microsoft.com/")
    print(f"  OK — {len(reservees)} routes Pro refusées en 402 avec le message d'offre")


def test_routes_ouvertes_le_restent():
    """L'essai doit RESTER utilisable : rien de ce qui n'est pas Pro n'a été
    fermé au passage, et l'arrêt d'une automatisation n'est jamais bloqué."""
    _isoler()
    c = _client()
    ouvertes = [
        ("GET", "/api/status", None),
        ("GET", "/api/agents", None),
        ("GET", "/api/licence", None),
        ("GET", "/api/licence/offre", None),
        ("GET", "/api/portfolio", None),
        ("GET", "/api/signaux", None),
        ("GET", "/api/symboles", None),
        ("GET", "/api/trading/config", None),
        ("GET", "/api/risk/status", None),
        ("GET", "/api/comptes", None),
        ("GET", "/api/auto-trader/status", None),
        ("POST", "/api/auto-trader/stop", {}),
        ("POST", "/api/mt5/auto-trading", {"active": False}),
    ]
    for methode, url, corps in ouvertes:
        r = c.request(methode, url, json=corps)
        assert r.status_code == 200, f"{url} → {r.status_code} (devrait rester ouvert)"
    print(f"  OK — {len(ouvertes)} routes restent accessibles en version d'essai")


def test_api_agents_marque_les_verrouilles():
    _isoler()
    c = _client()
    d = c.get("/api/agents").json()
    assert len(d["agents"]) == 45, "des agents ont disparu de la liste"
    verrouilles = [a for a in d["agents"] if a.get("verrouille")]
    assert len(verrouilles) == 39
    # Chaque agent verrouillé garde son nom et sa description : c'est ce qui
    # permet de découvrir l'offre Pro.
    assert all(a.get("nom") and a.get("description") for a in verrouilles)
    assert d["licence"]["agents"]["autorises"] == 6
    print("  OK — /api/agents liste les 45 agents, 39 marqués verrouillés")


def test_api_status_transporte_loffre():
    _isoler()
    c = _client()
    d = c.get("/api/status").json()
    lic = d.get("licence")
    assert lic and lic["etat"] == "TRIAL"
    assert lic["quotas"]["resume"].endswith("requêtes utilisées aujourd'hui")
    assert lic["limites_essai"]["max_agents"] == 6
    # Aucun secret ne sort par cette route.
    brut = json.dumps(d)
    assert "licence_jeton" not in brut and "installation" not in brut
    print("  OK — /api/status transporte l'offre, sans aucun secret")


def test_quota_route_analyser_repond_429():
    """La limite est tenue par le SERVEUR : atteinte, /api/analyser refuse."""
    _isoler()
    from licence import quota
    c = _client()
    # Le quota est rattaché au COMPTE du client, pas à l'installation : il
    # faut donc le remplir pour CE compte-là. (Le remplir « pour
    # l'installation » ne bloquait rien et laissait passer l'analyse — la
    # première version de ce test mesurait autre chose que ce qu'elle croyait.)
    compte = f"compte:{c.compte_agence['id']}"
    for _ in range(5):
        quota.enregistrer(compte)
    r = c.post("/api/analyser", json=["BTC-USD"])
    assert r.status_code == 429, f"{r.status_code} au lieu de 429"
    d = r.json()
    assert d["quota_depasse"] is True and d["pro_requis"] is True
    assert d["feature"] == "requetes_illimitees"
    print("  OK — quota atteint : /api/analyser répond 429 avec l'offre Pro")


def test_activation_par_route_http():
    _isoler()
    e = _Emetteur(); e.installer()
    c = _client()
    r = c.post("/api/licence/activer", json={"cle": e.emettre(sujet="abonne-http")})
    d = r.json()
    assert d["success"] is True
    assert d["licence"]["est_pro"] is True
    assert d["licence"]["agents"]["autorises"] == 45
    # Les routes Pro s'ouvrent immédiatement.
    assert c.post("/api/trading/levier", json={"levier": 2, "source": "interface"}).status_code == 200
    # Retrait de la licence → retour à l'essai.
    assert c.post("/api/licence/desactiver").json()["licence"]["est_pro"] is False
    assert c.post("/api/trading/levier", json={"levier": 2, "source": "interface"}).status_code == 402
    print("  OK — activation puis retrait de la licence, effet immédiat sur les routes")


# ═══════════════════════════ INTERFACE ════════════════════════════════════

def test_interface_expose_loffre_et_le_store():
    """L'interface porte bien le badge, le lien Store et les boutons Pro."""
    frontend = _RACINE / "frontend"
    index = (frontend / "index.html").read_text(encoding="utf-8")
    licence_js = (frontend / "js" / "licence.js").read_text(encoding="utf-8")
    tools_js = (frontend / "js" / "tools.js").read_text(encoding="utf-8")
    css = (frontend / "css" / "style.css").read_text(encoding="utf-8")

    assert 'src="js/licence.js"' in index, "licence.js n'est pas chargé par la page"
    assert 'id="licence-chip"' in index and 'id="licence-quota"' in index

    url = "https://apps.microsoft.com/detail/9nltgfr2btsp?hl=fr-FR&gl=FR"
    assert url in licence_js, "URL Microsoft Store absente ou modifiée"
    assert url.replace("&", "&amp;") in tools_js, "bouton Store absent des réglages"
    for texte in ("Disponible également sur le Microsoft Store",
                  "Télécharger sur le Microsoft Store",
                  "Cette fonctionnalité est disponible dans la version Pro.",
                  "Passer à Pro", "Voir les fonctionnalités Pro",
                  "Version d'essai — Fonctionnalités limitées"):
        assert texte in licence_js, f"texte manquant dans l'interface : « {texte} »"

    # Le thème existant est réutilisé, aucune couleur nouvelle n'est introduite.
    assert ".lic-btn-pro { background: var(--accent)" in css
    print("  OK — badge, boutons Pro, lien Store et textes d'offre présents")


def test_interface_ne_decide_rien():
    """L'interface AFFICHE l'offre ; elle ne la calcule pas. Un `est_pro` posé
    dans le navigateur ne doit ouvrir aucune porte."""
    licence_js = (_RACINE / "frontend" / "js" / "licence.js").read_text(encoding="utf-8")
    for motif in ("localStorage.setItem('pro", 'localStorage.setItem("pro',
                  "localStorage.setItem('est_pro", "localStorage.setItem('licence_jeton"):
        assert motif not in licence_js, f"l'interface stocke un droit : {motif}"
    # Le seul usage de localStorage est un confort d'affichage.
    assert licence_js.count("localStorage") <= 2
    assert "lic-essai-vu" in licence_js
    print("  OK — l'interface ne stocke aucun droit, seulement un confort d'affichage")


def test_agents_verrouilles_sans_onclick_interpole():
    """Même règle que le reste du tableau de bord : jamais de valeur dynamique
    dans un attribut onclick (esc() y est le mauvais échappement)."""
    app = (_RACINE / "frontend" / "js" / "app.js").read_text(encoding="utf-8")
    assert "data-verrou-id=" in app, "les cartes verrouillées n'utilisent pas data-*"
    assert "licenceAgentVerrouille(carte.dataset" in app
    assert 'onclick="licenceAgentVerrouille(${' not in app
    print("  OK — agents verrouillés câblés par data-* et addEventListener")


# ═══════════════════════════ SÉCURITÉ / SECRETS ═══════════════════════════

def test_aucun_secret_embarque():
    """Balayage du code DISTRIBUÉ : aucune clé, aucun jeton, aucun mot de passe.

    Les tests sont exclus : ils fabriquent volontairement des valeurs factices
    pour vérifier qu'elles sont bien rejetées ou effacées.
    """
    import re
    motifs = [
        (r"sk-ant-api03-[A-Za-z0-9_\-]{20,}", "clé Anthropic"),
        (r"sk-proj-[A-Za-z0-9_\-]{20,}", "clé OpenAI"),
        (r"\bAIza[0-9A-Za-z_\-]{35}", "clé Google"),
        (r"\bghp_[A-Za-z0-9]{36}", "jeton GitHub"),
        (r"\bxox[baprs]-[A-Za-z0-9\-]{10,}", "jeton Slack"),
        (r"\bAKIA[0-9A-Z]{16}", "clé AWS"),
        (r"-----BEGIN [A-Z ]*PRIVATE KEY-----", "clé privée"),
        (r"\bsk_live_[A-Za-z0-9]{20,}", "clé de paiement"),
    ]
    exclus = {"__pycache__", ".git", "node_modules", "runtime", "dist", "build",
              "tests", ".venv", "venv"}
    fautes = []
    for dossier in ("python", "backend", "frontend", "packaging", "installer",
                    "tools", "scripts"):
        base = _RACINE / dossier
        if not base.exists():
            continue
        for fichier in base.rglob("*"):
            if not fichier.is_file() or any(p in exclus for p in fichier.parts):
                continue
            if fichier.suffix.lower() not in {".py", ".js", ".json", ".html", ".css",
                                              ".bat", ".ps1", ".yml", ".yaml", ".xml",
                                              ".md", ".txt", ".iss", ".toml"}:
                continue
            try:
                texte = fichier.read_text(encoding="utf-8", errors="ignore")
            except Exception:
                continue
            for motif, quoi in motifs:
                if re.search(motif, texte):
                    fautes.append(f"{fichier.relative_to(_RACINE)} : {quoi}")
    assert not fautes, "secrets détectés dans le code distribué :\n  " + "\n  ".join(fautes)
    print("  OK — aucun secret détecté dans le code distribué")


def test_env_example_sans_valeur_reelle():
    """`.env.example` documente les variables — avec des repères, jamais des
    valeurs utilisables."""
    exemple = (_RACINE / "python" / ".env.example").read_text(encoding="utf-8")
    import re
    for ligne in exemple.splitlines():
        ligne = ligne.strip()
        if not ligne or ligne.startswith("#") or "=" not in ligne:
            continue
        nom, _, valeur = ligne.partition("=")
        valeur = valeur.strip()
        if not any(m in nom.upper() for m in ("KEY", "SECRET", "TOKEN", "PASSWORD", "JETON")):
            continue
        assert not valeur or re.fullmatch(r"[<>a-z_\-\. ]*", valeur), (
            f"{nom} porte une valeur d'apparence réelle dans .env.example")
    assert "AGENCE_LICENCE_PUBKEY" in exemple or "LICENCE_API_URL" in exemple
    print("  OK — .env.example ne contient que des repères")


def test_gitignore_couvre_les_fichiers_sensibles():
    ignore = (_RACINE / ".gitignore").read_text(encoding="utf-8")
    for motif in (".env", "*.pfx", "*.p12", "*.key", "*.pem"):
        assert motif in ignore, f"{motif} n'est pas ignoré par git"
    print("  OK — .gitignore couvre .env, certificats et clés privées")


def run():
    print("═══ Licence, version d'essai et version Pro ═══")
    from utils import app_config
    import config as app_cfg
    _etat0 = (app_config._PATH, app_config._cache, app_cfg.DB_PATH,
              os.environ.get("AGENCE_DATA_DIR"),
              os.environ.get("AGENCE_LICENCE_PUBKEY"),
              os.environ.get("AGENCE_LICENCE_JETON"))
    try:
        test_limites_configurees_exactement()
        test_essai_six_agents_les_autres_restent_visibles()
        test_cycle_n_interroge_que_les_agents_autorises()
        test_quota_horaire_puis_journalier()
        test_quota_compte_dans_la_base_pas_dans_le_navigateur()
        test_un_seul_symbole_par_analyse_en_essai()
        test_aucune_valeur_locale_ne_rend_pro()
        test_licence_contrefaite_refusee()
        test_licence_alteree_refusee()
        test_sans_cle_publique_aucune_licence_ne_passe()
        test_aucune_cle_privee_dans_le_code_distribue()
        test_pro_debloque_tout()
        test_licence_expiree_revient_a_lessai()
        test_etats_du_cycle_de_vie()
        test_aucun_faux_paiement()
        test_routes_pro_refusees_en_essai()
        test_routes_ouvertes_le_restent()
        test_api_agents_marque_les_verrouilles()
        test_api_status_transporte_loffre()
        test_quota_route_analyser_repond_429()
        test_activation_par_route_http()
        test_interface_expose_loffre_et_le_store()
        test_interface_ne_decide_rien()
        test_agents_verrouilles_sans_onclick_interpole()
        test_aucun_secret_embarque()
        test_env_example_sans_valeur_reelle()
        test_gitignore_couvre_les_fichiers_sensibles()
    finally:
        # Ne jamais laisser la configuration ni la base de l'utilisateur
        # détournées vers un dossier temporaire.
        (app_config._PATH, app_config._cache, app_cfg.DB_PATH,
         _data_dir, _pubkey, _jeton) = _etat0
        for nom, valeur in (("AGENCE_DATA_DIR", _data_dir),
                            ("AGENCE_LICENCE_PUBKEY", _pubkey),
                            ("AGENCE_LICENCE_JETON", _jeton)):
            if valeur is None:
                os.environ.pop(nom, None)
            else:
                os.environ[nom] = valeur
        import importlib
        import utils.database as database
        importlib.reload(database)
        from licence import abonnement
        abonnement.invalider_cache()


if __name__ == "__main__":
    run()
    print("✅ Licence OK")
