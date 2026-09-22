"""Abonnement Pro : état courant, activation, identité du compte.

RÔLE
----
Ce module est le SEUL endroit qui décide si l'application est en Pro. Il lit
le jeton de licence enregistré, le fait vérifier par `licence.verification`
(signature Ed25519), et met l'état en cache pour ne pas revérifier à chaque
requête HTTP.

CE QU'IL NE FAIT PAS
--------------------
Il ne facture rien et ne simule aucun paiement. Il n'existe à ce jour AUCUN
prestataire de paiement branché sur ce projet : l'architecture est prête
(`FOURNISSEURS` ci-dessous), l'implémentation ne sera écrite que le jour où un
vrai prestataire sera choisi. Tant qu'aucun n'est configuré, `activer()`
renvoie `PAYMENT_REQUIRED` en expliquant ce qui manque — il ne prétend jamais
qu'un paiement a eu lieu.

DEUX CHEMINS D'ABONNEMENT PRÉVUS
--------------------------------
`serveur`         : un émetteur de licences HTTPS (variable `LICENCE_API_URL`).
                    L'application lui présente une clé d'abonnement, il répond
                    un jeton signé. Aucune clé secrète côté client.
`microsoft_store` : l'abonnement est vendu comme extension de la fiche Store.
                    La vérification passe par le SDK Windows (StoreContext),
                    absent de cet environnement — le chemin est déclaré
                    indisponible plutôt que simulé.
"""
import logging
import os
import threading
import time
from typing import Any, Dict, Optional

from licence import config as lconfig
from licence.etat import EtatLicence
from licence.verification import PREFIXE, lire_jeton

logger = logging.getLogger(__name__)

# Clés de ~/.agence_financiere/config.json utilisées par ce module.
CLE_JETON = "licence_jeton"
CLE_INSTALLATION = "licence_installation_id"

FOURNISSEURS = ("serveur", "microsoft_store")

_cache: Optional[Dict[str, Any]] = None
_cache_expire = 0.0
_lock = threading.RLock()


# ═══════════════════════════ IDENTITÉ DU COMPTE ═══════════════════════════

def identifiant_installation() -> str:
    """Identifiant stable de cette installation, créé au premier appel.

    Il sert de compte de rattachement tant qu'aucune licence n'est activée :
    les quotas d'essai y sont associés (et non au navigateur), ce qui les rend
    insensibles à un vidage du stockage local ou à un changement d'appareil
    d'accès — l'interface est consultable depuis un téléphone du même Wi-Fi.

    Ce n'est PAS un secret et il n'identifie aucune personne : 16 octets
    aléatoires, générés localement, jamais dérivés du matériel.
    """
    from utils import app_config
    with _lock:
        existant = app_config.get(CLE_INSTALLATION)
        if existant:
            return str(existant)
        import secrets
        nouveau = secrets.token_hex(16)
        app_config.set(CLE_INSTALLATION, nouveau)
        return nouveau


def compte_id() -> str:
    """Compte auquel rattacher les quotas.

    Abonné : le sujet de sa licence (le même compte le suit d'une machine à
    l'autre). Sinon : l'installation. Dans les deux cas, le compteur vit côté
    serveur applicatif (base SQLite locale), jamais dans le navigateur — une
    remise à zéro depuis l'interface est impossible.
    """
    infos = etat_complet()
    sujet = infos.get("sujet") or ""
    if infos.get("est_pro") and sujet:
        return f"pro:{sujet}"
    return f"trial:{identifiant_installation()}"


# ═══════════════════════════ ÉTAT COURANT ═════════════════════════════════

def _jeton_enregistre() -> str:
    """Jeton de licence enregistré. `AGENCE_LICENCE_JETON` est prioritaire :
    elle permet de valider une licence en recette sans écrire la config —
    sans affaiblir quoi que ce soit, puisque le jeton reste vérifié par
    signature."""
    env = (os.getenv("AGENCE_LICENCE_JETON", "") or "").strip()
    if env:
        return env
    try:
        from utils import app_config
        return str(app_config.get(CLE_JETON) or "")
    except Exception as e:
        logger.warning("[licence] Configuration illisible : %s", e)
        return ""


def _calculer_etat() -> Dict[str, Any]:
    jeton = _jeton_enregistre()
    if not jeton:
        etat, infos = EtatLicence.TRIAL, {}
    else:
        etat, infos = lire_jeton(jeton)
        # Un jeton PRÉSENT mais refusé n'est pas une absence de licence : le
        # dire, sinon l'utilisateur qui vient de coller sa clé ne comprend pas
        # pourquoi rien ne change.
        if etat is EtatLicence.TRIAL and infos.get("erreur"):
            etat = EtatLicence.PAYMENT_REQUIRED

    return {
        "etat": etat.value,
        "libelle": etat.libelle,
        "message": infos.get("erreur") and f"{etat.message} ({infos['erreur']})" or etat.message,
        "est_pro": etat.est_pro,
        "sujet": infos.get("sujet", ""),
        "expire_le": infos.get("expire_le"),
        "fournisseur": fournisseur_configure(),
        "verifie_le": int(time.time()),
    }


def etat_complet(forcer: bool = False) -> Dict[str, Any]:
    """État d'abonnement, mis en cache `LICENCE_CACHE_S` secondes.

    La vérification est peu coûteuse mais elle est appelée sur CHAQUE requête
    gardée : sans cache, chaque battement de cœur du tableau de bord relirait
    la configuration sur disque.
    """
    global _cache, _cache_expire
    with _lock:
        if not forcer and _cache is not None and time.time() < _cache_expire:
            return dict(_cache)
        _cache = _calculer_etat()
        _cache_expire = time.time() + lconfig.LICENCE_CACHE_S
        return dict(_cache)


def etat() -> EtatLicence:
    return EtatLicence.depuis(etat_complet().get("etat"))


def est_pro() -> bool:
    """Vrai UNIQUEMENT sur licence Pro vérifiée et non expirée."""
    return bool(etat_complet().get("est_pro"))


def invalider_cache() -> None:
    global _cache, _cache_expire
    with _lock:
        _cache = None
        _cache_expire = 0.0


# ═══════════════════════════ ACTIVATION ═══════════════════════════════════

def fournisseur_configure() -> str:
    """Fournisseur d'abonnement retenu pour cette installation.

    `AGENCE_LICENCE_FOURNISSEUR` = « serveur » ou « microsoft_store ». Par
    défaut « serveur » : c'est le seul chemin implémentable sans SDK Windows.
    """
    nom = (os.getenv("AGENCE_LICENCE_FOURNISSEUR", "") or "").strip().lower()
    return nom if nom in FOURNISSEURS else "serveur"


def url_emetteur() -> str:
    """URL de l'émetteur de licences, ou chaîne vide s'il n'y en a pas.

    HTTPS EXIGÉ : le jeton transite dessus, et une URL http:// sur un réseau
    partagé le livrerait en clair.
    """
    url = (os.getenv("LICENCE_API_URL", "") or "").strip().rstrip("/")
    if url and not url.startswith("https://"):
        logger.warning("[licence] LICENCE_API_URL ignorée : HTTPS obligatoire")
        return ""
    return url


def enregistrer_jeton(jeton: str) -> Dict[str, Any]:
    """Enregistre un jeton APRÈS l'avoir vérifié. Un jeton refusé n'est jamais
    écrit : la configuration ne doit pas conserver une licence inexploitable
    qui ferait afficher « paiement requis » indéfiniment."""
    etat_jeton, infos = lire_jeton(jeton)
    if etat_jeton is not EtatLicence.PRO_ACTIVE:
        return {"success": False,
                "error": infos.get("erreur") or "Licence non valide",
                "etat": etat_jeton.value}
    from utils import app_config
    app_config.set(CLE_JETON, jeton.strip())
    invalider_cache()
    return {"success": True, **etat_complet(forcer=True)}


def effacer_licence() -> Dict[str, Any]:
    """Retire la licence de cette installation (retour à la version d'essai)."""
    from utils import app_config
    app_config.set(CLE_JETON, "")
    invalider_cache()
    return {"success": True, **etat_complet(forcer=True)}


def activer(cle_abonnement: str) -> Dict[str, Any]:
    """Échange une clé d'abonnement contre un jeton signé, auprès de l'émetteur.

    Renvoie toujours un dictionnaire — jamais d'exception — avec `success` et,
    en cas d'échec, `etat` = PAYMENT_REQUIRED et un message expliquant ce qui
    manque. AUCUN paiement n'est simulé ici.
    """
    cle_abonnement = (cle_abonnement or "").strip()

    # Une licence déjà signée collée directement dans le champ : l'accepter
    # (c'est le chemin d'activation hors ligne prévu pour le support).
    if cle_abonnement.startswith(f"{PREFIXE}."):
        return enregistrer_jeton(cle_abonnement)

    fournisseur = fournisseur_configure()
    if fournisseur == "microsoft_store":
        return _activer_microsoft_store()
    return _activer_serveur(cle_abonnement)


def _activer_serveur(cle_abonnement: str) -> Dict[str, Any]:
    url = url_emetteur()
    if not url:
        return {
            "success": False,
            "etat": EtatLicence.PAYMENT_REQUIRED.value,
            "error": "Aucun émetteur de licences n'est configuré pour cette "
                     "installation (LICENCE_API_URL). L'abonnement Pro n'est "
                     "donc pas encore activable depuis cette version.",
            "action": "store",
        }
    if not cle_abonnement:
        return {"success": False, "etat": EtatLicence.PAYMENT_REQUIRED.value,
                "error": "Clé d'abonnement requise."}
    try:
        import requests
        reponse = requests.post(
            f"{url}/licences/activer",
            json={"cle": cle_abonnement, "installation": identifiant_installation()},
            timeout=15,
        )
    except Exception as e:
        return {"success": False, "etat": EtatLicence.PAYMENT_REQUIRED.value,
                "error": f"Émetteur de licences injoignable : {e}"}
    if reponse.status_code != 200:
        detail = ""
        try:
            detail = str(reponse.json().get("error", ""))
        except Exception:
            pass
        return {"success": False, "etat": EtatLicence.PAYMENT_REQUIRED.value,
                "error": detail or f"Activation refusée (HTTP {reponse.status_code})"}
    try:
        jeton = str(reponse.json().get("jeton", ""))
    except Exception:
        jeton = ""
    if not jeton:
        return {"success": False, "etat": EtatLicence.PAYMENT_REQUIRED.value,
                "error": "Réponse de l'émetteur sans jeton de licence."}
    # Le jeton reçu est vérifié par signature comme n'importe quel autre : une
    # réponse HTTP n'est PAS une preuve d'abonnement.
    return enregistrer_jeton(jeton)


def _activer_microsoft_store() -> Dict[str, Any]:
    """Abonnement vendu comme extension de la fiche Microsoft Store.

    Exige le SDK Windows Runtime (`StoreContext.GetAppLicenseAsync`), absent
    de l'application aujourd'hui. Plutôt que d'inventer une réponse, on le dit.
    """
    return {
        "success": False,
        "etat": EtatLicence.PAYMENT_REQUIRED.value,
        "error": "L'abonnement via le Microsoft Store n'est pas encore "
                 "raccordé dans cette version (SDK Windows Store requis).",
        "action": "store",
    }
