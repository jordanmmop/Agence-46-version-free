"""Routes HTTP de la licence et de l'abonnement Pro.

Regroupées dans un routeur dédié : l'état de l'offre, l'activation et le
périmètre Pro n'ont rien à voir avec le trading, et les mélanger à
`backend/main.py` aurait dispersé les restrictions — exactement ce que cette
architecture cherche à éviter.

AUCUNE de ces routes ne renvoie de secret : ni le jeton de licence, ni
l'identifiant d'installation. L'interface reçoit l'offre en cours, les quotas
et la liste des fonctionnalités — rien de plus.
"""
import logging
from typing import Any, Dict

from fastapi import APIRouter, Body

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/licence", tags=["licence"])


def _agents():
    """Liste des agents, ou liste vide si l'arborescence n'est pas chargeable.

    L'état de licence doit rester consultable même quand les agents ne se
    chargent pas : c'est précisément l'écran qu'on regarde quand quelque chose
    ne va pas.
    """
    try:
        from agents import TOUS_LES_AGENTS
        return TOUS_LES_AGENTS
    except Exception as e:
        logger.warning("[licence] Agents indisponibles : %s", e)
        return []


@router.get("")
async def licence_etat() -> Dict[str, Any]:
    """Offre en cours, quotas restants et périmètre des fonctionnalités."""
    from licence import gate
    return gate.etat_public(_agents())


@router.get("/offre")
async def licence_offre() -> Dict[str, Any]:
    """Comparatif Essai / Pro — ce qu'affiche l'écran « Voir les fonctionnalités Pro ».

    Construit depuis `licence/config.py` : le tableau montré à l'utilisateur ne
    peut donc pas diverger des limites réellement appliquées.
    """
    from licence import config as lconfig
    from licence import gate

    total_agents = len(_agents())
    return {
        "essai": {
            "titre": "Version d'essai",
            "agents": lconfig.TRIAL_MAX_AGENTS,
            "agents_total": total_agents,
            "requetes_jour": lconfig.TRIAL_DAILY_REQUEST_LIMIT,
            "requetes_heure": lconfig.TRIAL_HOURLY_REQUEST_LIMIT,
            "symboles_par_analyse": lconfig.TRIAL_MAX_SYMBOLES_PAR_ANALYSE,
            "features": sorted(lconfig.TRIAL_FEATURES),
        },
        "pro": {
            "titre": "Version Pro",
            # None = aucun plafond : Pro donne accès à TOUS les agents du
            # projet (voir le commentaire de PRO_MAX_AGENTS).
            "agents": lconfig.PRO_MAX_AGENTS if lconfig.PRO_MAX_AGENTS is not None else total_agents,
            "agents_total": total_agents,
            "requetes_jour": lconfig.PRO_DAILY_REQUEST_LIMIT,
            "requetes_heure": lconfig.PRO_HOURLY_REQUEST_LIMIT,
            "symboles_par_analyse": None,
            "features": sorted(lconfig.PRO_FEATURES),
        },
        "features_libelles": dict(lconfig.FEATURES),
        "etat": gate.etat_public(_agents()),
        "store_url": lconfig.MICROSOFT_STORE_URL,
    }


@router.post("/activer")
async def licence_activer(body: Dict[str, Any] = Body(default={})) -> Dict[str, Any]:
    """Active l'abonnement Pro à partir d'une clé d'abonnement ou d'une licence signée.

    Ne simule AUCUN paiement : sans émetteur de licences configuré, la réponse
    dit ce qui manque et l'application reste en version d'essai.
    """
    from licence import abonnement, gate
    cle = str(body.get("cle") or body.get("licence") or "").strip()
    resultat = abonnement.activer(cle)
    resultat["licence"] = gate.etat_public(_agents())
    return resultat


@router.post("/rafraichir")
async def licence_rafraichir() -> Dict[str, Any]:
    """Revérifie la licence immédiatement (sans attendre l'expiration du cache)."""
    from licence import abonnement, gate
    abonnement.invalider_cache()
    abonnement.etat_complet(forcer=True)
    return gate.etat_public(_agents())


@router.post("/desactiver")
async def licence_desactiver() -> Dict[str, Any]:
    """Retire la licence de cette installation (retour à la version d'essai)."""
    from licence import abonnement, gate
    abonnement.effacer_licence()
    return {"success": True, "licence": gate.etat_public(_agents())}
