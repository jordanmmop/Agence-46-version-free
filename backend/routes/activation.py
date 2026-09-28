"""Routes locales d'activation de licence Novia.

Joignables SANS licence (c'est leur objet), elles ne font que relayer le
LicenseManager : aucune logique de licence n'est écrite ici.

PROTECTION CONTRE LES REQUÊTES D'UN AUTRE SITE
----------------------------------------------
Le backend écoute sur la machine de l'utilisateur. Une page web malveillante
ouverte dans son navigateur peut tenter un POST vers 127.0.0.1 — et une
requête « simple » (formulaire, text/plain) part sans contrôle CORS préalable.
Les routes qui MODIFIENT la licence exigent donc un corps JSON : un site tiers
ne peut pas en envoyer sans une requête préalable que la politique CORS
refuse. Sans cette garde, n'importe quel site pourrait désactiver la licence
de son visiteur.
"""
import logging
from typing import Any, Dict

from fastapi import APIRouter, Request, Response

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/activation", tags=["activation"])


def _manager():
    from licence import license_manager
    return license_manager.manager()


def _apres_changement() -> None:
    from licence import abonnement
    abonnement.invalider_cache()


async def _corps_json(request: Request, response: Response) -> Dict[str, Any]:
    """Corps JSON obligatoire. Renvoie None (et pose 415) sinon."""
    type_contenu = request.headers.get("content-type", "").split(";")[0].strip().lower()
    if type_contenu != "application/json":
        response.status_code = 415
        return None
    try:
        corps = await request.json()
    except Exception:
        corps = {}
    return corps if isinstance(corps, dict) else {}


_REFUS_TYPE = {"success": False,
               "message": "Requête refusée : corps JSON attendu."}


@router.get("")
async def etat_activation() -> Dict[str, Any]:
    """Tout ce qu'affichent l'écran d'activation et Réglages → Licence.

    Aucun secret : ni la clé complète (seulement sa forme masquée), ni le
    jeton signé, ni l'identifiant d'installation.
    """
    from licence import config as lconfig
    from licence.license_manager import _version_app
    etat = _manager().etat()
    return {
        "mode_novia": lconfig.novia_actif(),
        "configuration_incomplete": lconfig.novia_configuration_incomplete(),
        "produit": lconfig.novia_produit(),
        "version": _version_app(),
        "url_achat": lconfig.novia_url_achat(),
        "url_compte": lconfig.novia_url_compte(),
        "hors_ligne_jours": lconfig.OFFLINE_GRACE_PERIOD_JOURS,
        **etat,
    }


@router.post("/activer")
async def activer(request: Request, response: Response) -> Dict[str, Any]:
    corps = await _corps_json(request, response)
    if corps is None:
        return _REFUS_TYPE
    resultat = _manager().activer(str(corps.get("license_key") or corps.get("cle") or ""))
    _apres_changement()
    if not resultat.get("success"):
        response.status_code = 400 if resultat.get("refus_etat") else 503
    return resultat


@router.post("/changer")
async def changer(request: Request, response: Response) -> Dict[str, Any]:
    corps = await _corps_json(request, response)
    if corps is None:
        return _REFUS_TYPE
    resultat = _manager().changer(str(corps.get("license_key") or corps.get("cle") or ""))
    _apres_changement()
    if not resultat.get("success"):
        response.status_code = 400 if resultat.get("refus_etat") else 503
    return resultat


@router.post("/actualiser")
async def actualiser(request: Request, response: Response) -> Dict[str, Any]:
    corps = await _corps_json(request, response)
    if corps is None:
        return _REFUS_TYPE
    resultat = _manager().revalider(forcer=True)
    _apres_changement()
    return resultat


@router.post("/desactiver")
async def desactiver(request: Request, response: Response) -> Dict[str, Any]:
    corps = await _corps_json(request, response)
    if corps is None:
        return _REFUS_TYPE
    resultat = _manager().desactiver(forcer=bool(corps.get("forcer")))
    _apres_changement()
    if not resultat.get("success"):
        response.status_code = 503
    return resultat
