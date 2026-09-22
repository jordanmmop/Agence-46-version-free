"""Feature Gate — LE point de passage unique de toutes les restrictions.

                    Abonnement / Licence   (licence/abonnement.py)
                              ↓
                       Feature Gate        (ce module)
                              ↓
                  Agents IA  ·  Fonctionnalités

Aucun autre module du projet ne doit décider tout seul si quelque chose est
autorisé. Les routes HTTP appellent `exiger()` ou `autoriser_analyse()`, le
Chef d'Orchestre appelle `agents_autorises()` — et c'est tout. Cette
concentration est ce qui rend les limites modifiables en un seul endroit
(`licence/config.py`) et difficiles à contourner : l'interface ne décide de
rien, elle se contente d'afficher ce que le serveur lui répond.
"""
import logging
from typing import Any, Dict, List, Optional, Sequence, Tuple

from licence import config as lconfig
from licence import abonnement, quota
from licence.etat import EtatLicence

logger = logging.getLogger(__name__)


class ProRequis(Exception):
    """Fonctionnalité réservée à l'abonnement Pro.

    Porte tout ce qu'il faut pour afficher le message de verrouillage sans que
    l'appelant ait à reformuler quoi que ce soit.
    """

    def __init__(self, feature: str, detail: str = ""):
        self.feature = feature
        self.detail = detail
        self.libelle = lconfig.FEATURES.get(feature, feature)
        super().__init__(detail or f"{self.libelle} — réservé à la version Pro")

    def payload(self) -> Dict[str, Any]:
        """Corps de réponse normalisé — l'interface n'en connaît qu'un seul."""
        return {
            "error": "Cette fonctionnalité est disponible dans la version Pro.",
            "pro_requis": True,
            "feature": self.feature,
            "feature_libelle": self.libelle,
            "detail": self.detail,
            "etat": abonnement.etat_complet().get("etat"),
            "store_url": lconfig.MICROSOFT_STORE_URL,
        }


class QuotaDepasse(Exception):
    """Limite de requêtes de la version d'essai atteinte."""

    def __init__(self, periode: str, utilise: int, limite: int):
        self.periode = periode          # « jour » ou « heure »
        self.utilise = utilise
        self.limite = limite
        super().__init__(f"Limite d'essai atteinte : {utilise}/{limite} par {periode}")

    def payload(self) -> Dict[str, Any]:
        quand = "aujourd'hui" if self.periode == "jour" else "cette heure-ci"
        return {
            "error": f"Limite de la version d'essai atteinte : "
                     f"{self.limite} requêtes par {self.periode} "
                     f"({self.utilise} utilisées {quand}).",
            "pro_requis": True,
            "feature": "requetes_illimitees",
            "feature_libelle": lconfig.FEATURES["requetes_illimitees"],
            "quota_depasse": True,
            "periode": self.periode,
            "utilise": self.utilise,
            "limite": self.limite,
            "etat": abonnement.etat_complet().get("etat"),
            "store_url": lconfig.MICROSOFT_STORE_URL,
        }


# ═══════════════════════════ FONCTIONNALITÉS ══════════════════════════════

def autorise(feature: str) -> bool:
    """La fonctionnalité est-elle accessible dans l'état d'abonnement courant ?

    Une fonctionnalité INCONNUE est refusée : mieux vaut un verrou visible
    qu'une porte ouverte par oubli de déclaration dans `config.FEATURES`.
    """
    if feature not in lconfig.FEATURES:
        logger.warning("[licence] Fonctionnalité inconnue refusée : %s", feature)
        return False
    if feature in lconfig.TRIAL_FEATURES:
        return True
    return abonnement.est_pro()


def exiger(feature: str, detail: str = "") -> None:
    """Laisse passer, ou lève `ProRequis`. À appeler AVANT tout effet."""
    if not autorise(feature):
        raise ProRequis(feature, detail)


# ═══════════════════════════ AGENTS ═══════════════════════════════════════

def limite_agents() -> Optional[int]:
    """Nombre d'agents exploitables, ou None pour « tous »."""
    return lconfig.PRO_MAX_AGENTS if abonnement.est_pro() else lconfig.TRIAL_MAX_AGENTS


def ids_agents_autorises(tous_les_agents: Sequence) -> List[str]:
    """Identifiants des agents exploitables, dans l'ordre d'affichage.

    En essai : les agents de `TRIAL_AGENTS_IDS` d'abord (un par famille), puis
    l'ordre de la liste globale si la sélection est incomplète — un agent
    renommé ne doit jamais réduire l'essai à moins de `TRIAL_MAX_AGENTS`.
    """
    limite = limite_agents()
    ids_existants = [str(getattr(a, "id", getattr(a, "ID", ""))) for a in tous_les_agents]
    if limite is None:
        return ids_existants

    retenus: List[str] = []
    for agent_id in lconfig.TRIAL_AGENTS_IDS:
        if agent_id in ids_existants and agent_id not in retenus:
            retenus.append(agent_id)
        if len(retenus) >= limite:
            break
    for agent_id in ids_existants:                 # complément déterministe
        if len(retenus) >= limite:
            break
        if agent_id not in retenus:
            retenus.append(agent_id)
    return retenus[:limite]


def agents_autorises(tous_les_agents: Sequence) -> List:
    """Sous-ensemble d'agents qui participe réellement au cycle d'analyse.

    Les agents NON retenus ne sont pas supprimés : ils restent instanciés,
    listés et visibles dans l'interface (marqués comme verrouillés). L'essai
    doit laisser découvrir ce que la version Pro apporte.
    """
    permis = set(ids_agents_autorises(tous_les_agents))
    return [a for a in tous_les_agents
            if str(getattr(a, "id", getattr(a, "ID", ""))) in permis]


def agent_verrouille(agent_id: str, tous_les_agents: Sequence) -> bool:
    return str(agent_id) not in set(ids_agents_autorises(tous_les_agents))


# ═══════════════════════════ SYMBOLES ═════════════════════════════════════

def limiter_symboles(symboles: Sequence[str]) -> Tuple[List[str], List[str]]:
    """(symboles retenus, symboles écartés faute d'abonnement Pro).

    En essai, un cycle ne traite qu'un symbole à la fois — c'est la traduction
    concrète de la ligne « multi-agents » du tableau des offres. On ÉCARTE
    plutôt que de refuser : une analyse sur le premier symbole vaut mieux
    qu'une erreur, et l'interface affiche ce qui a été écarté.
    """
    propres = [s for s in (symboles or []) if s]
    if abonnement.est_pro():
        return list(propres), []
    plafond = max(1, int(lconfig.TRIAL_MAX_SYMBOLES_PAR_ANALYSE))
    return list(propres[:plafond]), list(propres[plafond:])


# ═══════════════════════════ QUOTAS ═══════════════════════════════════════

def limites_requetes() -> Tuple[Optional[int], Optional[int]]:
    if abonnement.est_pro():
        return lconfig.PRO_DAILY_REQUEST_LIMIT, lconfig.PRO_HOURLY_REQUEST_LIMIT
    return lconfig.TRIAL_DAILY_REQUEST_LIMIT, lconfig.TRIAL_HOURLY_REQUEST_LIMIT


def quotas() -> Dict[str, Any]:
    jour, heure = limites_requetes()
    return quota.details(abonnement.compte_id(), jour, heure)


def verifier_quota() -> None:
    """Lève `QuotaDepasse` si la limite est atteinte. NE CONSOMME RIEN."""
    limite_jour, limite_heure = limites_requetes()
    if limite_jour is None and limite_heure is None:
        return
    u = quota.utilisation(abonnement.compte_id())
    # L'heure d'abord : c'est la limite qui se libère le plus vite, donc le
    # message le plus utile à afficher quand les deux sont atteintes.
    if limite_heure is not None and u["heure"] >= limite_heure:
        raise QuotaDepasse("heure", u["heure"], limite_heure)
    if limite_jour is not None and u["jour"] >= limite_jour:
        raise QuotaDepasse("jour", u["jour"], limite_jour)


def consommer_requete(type_requete: str = "analyse") -> Dict[str, Any]:
    """Vérifie PUIS comptabilise une requête IA. Renvoie les quotas à jour.

    Vérifier avant de consommer, et dans le même appel : deux appels séparés
    laisseraient passer une requête de trop entre les deux.
    """
    verifier_quota()
    limite_jour, limite_heure = limites_requetes()
    compte = abonnement.compte_id()
    u = quota.enregistrer(compte, type_requete)
    return {
        "jour_utilise": u["jour"],
        "jour_limite": limite_jour,
        "jour_restant": quota.restant(u["jour"], limite_jour),
        "heure_utilise": u["heure"],
        "heure_limite": limite_heure,
        "heure_restant": quota.restant(u["heure"], limite_heure),
        "illimite": limite_jour is None and limite_heure is None,
        "resume": ("Requêtes illimitées" if limite_jour is None
                   else f"{u['jour']} / {limite_jour} requêtes utilisées aujourd'hui"),
    }


def autoriser_analyse(symboles: Sequence[str]) -> Dict[str, Any]:
    """Garde-fou complet d'un cycle d'analyse, en un seul appel.

    Lève `QuotaDepasse` si la limite d'essai est atteinte ; sinon consomme une
    requête et renvoie de quoi renseigner l'utilisateur (symboles retenus,
    symboles écartés, quotas restants).
    """
    retenus, ecartes = limiter_symboles(symboles)
    quotas_maj = consommer_requete("analyse")
    return {"symboles": retenus, "symboles_ecartes": ecartes, "quotas": quotas_maj}


# ═══════════════════════════ ÉTAT POUR L'INTERFACE ════════════════════════

def etat_public(tous_les_agents: Sequence = ()) -> Dict[str, Any]:
    """Tout ce que l'interface a besoin de savoir, en une seule réponse.

    Ne contient AUCUN secret : ni jeton de licence, ni clé, ni identifiant
    d'installation — seulement l'offre en cours, les quotas et le périmètre.
    """
    infos = abonnement.etat_complet()
    etat = EtatLicence.depuis(infos.get("etat"))
    pro = etat.est_pro
    total = len(tous_les_agents)
    autorises = ids_agents_autorises(tous_les_agents) if total else []

    return {
        "etat": etat.value,
        "libelle": etat.libelle,
        "message": infos.get("message", etat.message),
        "est_pro": pro,
        "expire_le": infos.get("expire_le"),
        "fournisseur": infos.get("fournisseur", ""),
        "abonnement_disponible": bool(abonnement.url_emetteur()),
        "agents": {
            "total": total,
            "autorises": len(autorises),
            "ids_autorises": autorises,
            "limite": limite_agents(),
        },
        "quotas": quotas(),
        "features": {cle: autorise(cle) for cle in lconfig.FEATURES},
        "features_libelles": dict(lconfig.FEATURES),
        "features_pro": sorted(lconfig.PRO_FEATURES),
        "limites_essai": {
            "max_agents": lconfig.TRIAL_MAX_AGENTS,
            "requetes_jour": lconfig.TRIAL_DAILY_REQUEST_LIMIT,
            "requetes_heure": lconfig.TRIAL_HOURLY_REQUEST_LIMIT,
            "symboles_par_analyse": lconfig.TRIAL_MAX_SYMBOLES_PAR_ANALYSE,
        },
        "store_url": lconfig.MICROSOFT_STORE_URL,
    }
