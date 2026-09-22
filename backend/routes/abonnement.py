"""Routes de l'abonnement Pro : formules, retour de paiement, webhook Stripe.

Ces routes restent accessibles à un compte SUSPENDU — c'est le seul moyen
pour lui de régulariser. Elles n'ouvrent aucun droit par elles-mêmes : seule
une confirmation de Stripe, vérifiée côté serveur, active l'abonnement.
"""
import logging
from typing import Any, Dict

from fastapi import APIRouter, Body, Request, Response
from fastapi.responses import HTMLResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/abonnement", tags=["abonnement"])


def _compte_id() -> str:
    from licence import comptes
    return (comptes.compte_courant() or {}).get("id", "")


@router.get("")
async def offre_abonnement() -> Dict[str, Any]:
    """Les deux formules, avec le lien de paiement rattaché au compte."""
    from licence import stripe_paiement
    return stripe_paiement.etat_paiement(_compte_id())


@router.get("/formules")
async def formules() -> Dict[str, Any]:
    from licence import stripe_paiement
    return stripe_paiement.etat_paiement(_compte_id())


@router.post("/verifier")
async def verifier(response: Response,
                   body: Dict[str, Any] = Body(default={})) -> Dict[str, Any]:
    """Demande à Stripe si une session de paiement a bien été réglée.

    Appelée au retour de la page Stripe. La référence vient du navigateur et
    ne prouve RIEN : c'est la réponse de Stripe qui décide, et elle seule.
    """
    from licence import stripe_paiement
    resultat = stripe_paiement.verifier_session(str(body.get("session_id") or ""))
    if not resultat.get("success"):
        # 402 et non 400 : ce n'est pas une requête mal formée, c'est un
        # paiement qui n'est pas (encore) confirmé.
        response.status_code = 402
    return resultat


@router.get("/retour", response_class=HTMLResponse)
async def retour_stripe(request: Request) -> HTMLResponse:
    """Page d'atterrissage après un paiement Stripe.

    À renseigner comme URL de succès du lien de paiement, avec le paramètre
    que Stripe remplace lui-même :

        http://127.0.0.1:8000/api/abonnement/retour?session_id={CHECKOUT_SESSION_ID}

    La page ne fait qu'AFFICHER le résultat de la vérification faite côté
    serveur — elle n'active rien par sa seule ouverture.
    """
    from licence import stripe_paiement
    session_id = request.query_params.get("session_id", "")
    resultat = stripe_paiement.verifier_session(session_id) if session_id else {
        "success": False, "error": "Aucune référence de paiement reçue."}

    ok = bool(resultat.get("success"))
    titre = "Abonnement activé" if ok else "Paiement non confirmé"
    icone = "✅" if ok else "⏳"
    message = resultat.get("message") if ok else resultat.get("error", "")
    if not ok and resultat.get("paiement_non_verifiable"):
        message += (" Votre règlement a bien pu aboutir chez Stripe : il sera "
                    "pris en compte dès que la vérification sera configurée "
                    "sur ce serveur.")

    from html import escape
    return HTMLResponse(f"""<!DOCTYPE html>
<html lang="fr"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{escape(titre)} — Agence Numérique Financière</title>
<style>
*{{box-sizing:border-box;margin:0;padding:0}}
body{{background:#0a0c10;color:#dde3ec;font-family:system-ui,-apple-system,sans-serif;
display:flex;align-items:center;justify-content:center;min-height:100vh;padding:16px}}
.c{{background:#10131a;border:1px solid #232a37;border-radius:14px;padding:40px 34px;
max-width:460px;width:100%;text-align:center;box-shadow:0 24px 64px rgba(0,0,0,.6)}}
.i{{font-size:40px;margin-bottom:14px}}
h1{{color:{'#2ebd85' if ok else '#d8a03d'};font-size:19px;margin-bottom:10px}}
p{{color:#79839a;font-size:14px;line-height:1.55;margin-bottom:24px}}
a{{display:inline-block;padding:12px 22px;background:#5b8def;color:#fff;
border-radius:8px;text-decoration:none;font-weight:600;font-size:14px}}
</style></head><body>
<div class="c"><div class="i">{icone}</div>
<h1>{escape(titre)}</h1>
<p>{escape(message or '')}</p>
<a href="/">Retour à l'application</a></div></body></html>""")


@router.post("/webhook")
async def webhook(request: Request, response: Response) -> Dict[str, Any]:
    """Webhook Stripe — chemin de référence pour confirmer un paiement.

    La requête est vérifiée par SIGNATURE (`STRIPE_WEBHOOK_SECRET`) avant
    d'être lue. Sans signature valide, rien n'est activé : cette route est
    publique, et sans ce contrôle, quiconque la découvrirait pourrait s'offrir
    un abonnement en envoyant un simple POST.

    On lit le corps BRUT : la signature porte sur les octets exacts reçus, pas
    sur un JSON reformaté.
    """
    from licence import stripe_paiement
    charge = await request.body()
    resultat = stripe_paiement.traiter_webhook(
        charge, request.headers.get("stripe-signature", ""))
    if not resultat.get("success"):
        response.status_code = int(resultat.pop("code", 400))
    return resultat
