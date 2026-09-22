"""Routes des comptes utilisateurs : inscription, connexion, session.

L'application exige un compte : ces routes sont donc parmi les rares à rester
ouvertes sans session (voir `_PUBLIC_COMPTE` dans backend/main.py). Tout le
reste est fermé tant que personne n'est connecté.

AUCUNE de ces routes ne renvoie de secret : ni hash de mot de passe, ni jeton
de session dans le corps (il part en cookie `HttpOnly`, inaccessible au
JavaScript, donc à une éventuelle injection).
"""
import logging
from typing import Any, Dict

from fastapi import APIRouter, Body, Request, Response

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/compte", tags=["compte"])


def _ip(request: Request) -> str:
    return getattr(getattr(request, "client", None), "host", "") or "?"


def poser_cookie(reponse: Response, jeton: str) -> None:
    """Installe le cookie de session.

    `httponly` : le jeton n'est jamais lisible en JavaScript — une faille XSS
    ne permet donc pas de le voler.
    `samesite=lax` : un autre site ne peut pas déclencher d'action authentifiée
    en faisant cliquer l'utilisateur sur un lien.
    `secure` n'est PAS posé : l'application est servie en HTTP sur le réseau
    local (accès depuis un téléphone du même Wi-Fi, cf. README). Le poser
    empêcherait toute connexion. Derrière un reverse-proxy HTTPS, l'activer.
    """
    from licence import comptes, config as lconfig
    reponse.set_cookie(
        comptes.COOKIE_SESSION, jeton,
        max_age=lconfig.SESSION_DUREE_JOURS * 86400,
        httponly=True, samesite="lax", path="/",
    )


def _etat_complet() -> Dict[str, Any]:
    """Compte + offre, tels que l'interface les attend après une connexion."""
    from licence import comptes, gate
    try:
        from agents import TOUS_LES_AGENTS
    except Exception:
        TOUS_LES_AGENTS = []
    return {"compte": comptes.public(comptes.compte_courant()),
            "licence": gate.etat_public(TOUS_LES_AGENTS)}


@router.get("")
async def compte_actuel() -> Dict[str, Any]:
    """Compte connecté et offre en cours. `compte` vide = personne connecté."""
    return _etat_complet()


@router.post("/inscription")
async def inscription(response: Response, request: Request,
                      body: Dict[str, Any] = Body(default={})) -> Dict[str, Any]:
    """Crée un compte et ouvre immédiatement l'essai gratuit.

    La session est ouverte dans la foulée : faire ressaisir ses identifiants
    à quelqu'un qui vient de les choisir n'apporte rien.
    """
    from licence import comptes
    try:
        compte = comptes.inscrire(body or {})
    except comptes.ErreurCompte as e:
        response.status_code = 409 if e.code == "compte_existant" else 400
        return {"success": False, "error": str(e), "code": e.code}

    jeton = comptes.ouvrir_session(compte["id"])
    poser_cookie(response, jeton)
    comptes.definir_compte_courant(compte)
    comptes.reinitialiser_echecs(_ip(request))
    return {"success": True, **_etat_complet()}


@router.post("/connexion")
async def connexion(response: Response, request: Request,
                    body: Dict[str, Any] = Body(default={})) -> Dict[str, Any]:
    from licence import comptes
    ip = _ip(request)
    bloque, restant = comptes.connexion_bloquee(ip)
    if bloque:
        response.status_code = 429
        return {"success": False, "code": "trop_d_essais",
                "error": f"Trop de tentatives. Réessayez dans {restant} secondes."}

    try:
        compte = comptes.connecter(str(body.get("email") or ""),
                                   str(body.get("mot_de_passe") or ""))
    except comptes.ErreurCompte as e:
        comptes.enregistrer_echec(ip)
        response.status_code = 401
        return {"success": False, "error": str(e), "code": e.code}

    comptes.reinitialiser_echecs(ip)
    jeton = comptes.ouvrir_session(compte["id"])
    poser_cookie(response, jeton)
    comptes.definir_compte_courant(compte)
    return {"success": True, **_etat_complet()}


@router.post("/deconnexion")
async def deconnexion(request: Request, response: Response) -> Dict[str, Any]:
    from licence import comptes
    comptes.fermer_session(request.cookies.get(comptes.COOKIE_SESSION, ""))
    response.delete_cookie(comptes.COOKIE_SESSION, path="/")
    comptes.definir_compte_courant(None)
    return {"success": True}


@router.get("/disponible")
async def disponible(email: str = "", telephone: str = "") -> Dict[str, Any]:
    """Ce contact est-il libre ? Confort de formulaire, pas une décision.

    Ne dit PAS lequel des deux est pris : cette route est ouverte sans
    session, et répondre « cet e-mail existe » permettrait d'éprouver une
    liste d'adresses pour savoir qui est inscrit.
    """
    from licence import comptes
    return {"disponible": not comptes.contact_deja_utilise(email, telephone)}
