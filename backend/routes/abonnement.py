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


# Dernière réémission de clé, par compte. Chaque envoi coûte un SMS RÉEL :
# sans ce délai, un clic répété — ou une session volée — se traduirait en
# facture. En mémoire du processus, ce qui suffit : redémarrer le serveur pour
# contourner une minute d'attente n'a aucun intérêt pour un attaquant.
_DELAI_REEMISSION_S = 60
_dernieres_reemissions: Dict[str, float] = {}


def _reemission_trop_frequente(compte_id: str) -> int:
    """Secondes restant à attendre, ou 0 si la demande peut passer."""
    import time
    precedente = _dernieres_reemissions.get(compte_id, 0.0)
    reste = _DELAI_REEMISSION_S - (time.time() - precedente)
    return int(reste) + 1 if reste > 0 else 0


@router.get("")
async def offre_abonnement() -> Dict[str, Any]:
    """Les deux formules, avec le lien de paiement rattaché au compte."""
    from licence import stripe_paiement
    return stripe_paiement.etat_paiement(_compte_id())


@router.get("/formules")
async def formules() -> Dict[str, Any]:
    from licence import stripe_paiement
    return stripe_paiement.etat_paiement(_compte_id())


def _proteger_cle(resultat: Dict[str, Any]) -> Dict[str, Any]:
    """Retire la clé en clair si le demandeur n'est pas le compte payeur.

    La référence de session Stripe circule dans une URL : elle peut se
    retrouver dans un historique de navigation, un presse-papiers, un message
    de support. Elle suffit à CONSTATER un paiement — pas à obtenir la clé qui
    ouvre l'abonnement. Pour la voir, il faut en plus être connecté au compte
    concerné ; sinon elle a été envoyée par e-mail et SMS, et reste
    réémettable depuis l'application.
    """
    if "cle" not in resultat:
        return resultat
    paye_par = str((resultat.get("compte") or {}).get("id") or "")
    if paye_par and paye_par == _compte_id():
        return resultat
    resultat.pop("cle", None)
    resultat["cle_masquee"] = True
    resultat["cle_message"] = (
        "Votre clé d'abonnement vous a été envoyée. Connectez-vous à votre "
        "compte dans l'application pour la retrouver ou en demander une "
        "nouvelle.")
    return resultat


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
    return _proteger_cle(resultat)


@router.get("/cle")
async def cles_du_compte(response: Response) -> Dict[str, Any]:
    """Clés d'abonnement du compte connecté — MASQUÉES, jamais en clair.

    Une clé n'est lisible qu'une fois, à son émission : la base n'en conserve
    que l'empreinte. Cette route sert à savoir ce qui existe et par quels
    canaux la remise a eu lieu.
    """
    from licence import cles, comptes, notifications
    compte = comptes.compte_courant()
    if not compte:
        response.status_code = 401
        return {"success": False, "error": "Connectez-vous pour voir vos clés."}
    return {"success": True,
            "cles": cles.cles_du_compte(compte["id"]),
            "remise": notifications.diagnostic()}


@router.post("/cle")
async def renvoyer_cle(response: Response) -> Dict[str, Any]:
    """Émet une NOUVELLE clé pour le compte connecté et la lui envoie.

    C'est le rattrapage : e-mail jamais arrivé, clé perdue, téléphone changé.
    Réservé à un compte ABONNÉ — un essai en cours n'ouvre aucun droit Pro, et
    ne doit donc pas pouvoir se faire remettre une clé.

    L'ancienne clé est révoquée : celle qui traîne dans une boîte mail est
    précisément celle dont on ne veut plus. Les installations déjà activées
    ne sont pas touchées — elles détiennent leur propre jeton signé.
    """
    from licence import cles, comptes, notifications

    compte = comptes.compte_courant()
    if not compte:
        response.status_code = 401
        return {"success": False, "error": "Connectez-vous pour demander une clé."}

    compte = comptes.depot().par_id(compte["id"]) or compte
    etat = comptes.etat_du_compte(compte)
    if not etat.est_pro:
        response.status_code = 402
        return {"success": False, "etat": etat.value,
                "error": "Aucun abonnement actif sur ce compte : il n'y a pas "
                         "de clé à remettre."}

    attente = _reemission_trop_frequente(compte["id"])
    if attente:
        response.status_code = 429
        return {"success": False, "error": f"Une clé vient d'être envoyée. "
                                           f"Patientez {attente} seconde(s) "
                                           f"avant d'en demander une autre."}

    import time as _time
    _dernieres_reemissions[compte["id"]] = _time.time()
    emission = cles.remplacer(compte["id"], compte.get("formule") or "",
                              compte.get("abonne_jusqua"))
    cle = emission.get("cle", "")
    if not cle:
        response.status_code = 500
        return {"success": False, "error": "La clé n'a pas pu être émise."}

    envoi = notifications.envoyer_cle(compte, cle, compte.get("formule") or "")
    cles.marquer_envoi(emission["enregistrement"]["cle_hash"], envoi.get("canaux", ""))

    from licence.stripe_paiement import _message_envoi
    return {"success": True, "cle": cle, "envoi": envoi,
            "message": _message_envoi(envoi, compte),
            "avertissement": "Votre clé précédente a été remplacée : elle ne "
                             "fonctionne plus."}


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

    resultat = _proteger_cle(resultat)
    ok = bool(resultat.get("success"))
    titre = "Abonnement activé" if ok else "Paiement non confirmé"
    icone = "✅" if ok else "⏳"
    message = resultat.get("message") if ok else resultat.get("error", "")
    if not ok and resultat.get("paiement_non_verifiable"):
        message += (" Votre règlement a bien pu aboutir chez Stripe : il sera "
                    "pris en compte dès que la vérification sera configurée "
                    "sur ce serveur.")

    from html import escape

    # La clé n'est lisible QU'ICI et qu'une fois : la base n'en garde que
    # l'empreinte. Elle est donc affichée en grand, avec la consigne de la
    # conserver — un abonné qui ferme cette page sans la noter devra en
    # demander une nouvelle depuis l'application.
    bloc_cle = ""
    if resultat.get("cle"):
        bloc_cle = f"""
<div class="k"><div class="kl">Votre clé d'abonnement</div>
<div class="kv">{escape(str(resultat['cle']))}</div>
<div class="kh">Conservez-la. Dans l'application : « Passer à la version Pro »,
puis collez cette clé.</div></div>
<p class="sub">{escape(str(resultat.get('cle_message') or ''))}</p>"""
    elif ok and resultat.get("cle_message"):
        bloc_cle = f"""<p class="sub">{escape(str(resultat['cle_message']))}</p>"""
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
.k{{background:#0c1725;border:1px solid #1f3347;border-radius:10px;
padding:18px 14px;margin:0 0 18px}}
.kl{{color:#79839a;font-size:11px;letter-spacing:.09em;text-transform:uppercase;
margin-bottom:10px}}
.kv{{color:#2ebd85;font-family:ui-monospace,SFMono-Regular,Menlo,monospace;
font-size:20px;font-weight:700;letter-spacing:.06em;word-break:break-all;
user-select:all}}
.kh{{color:#79839a;font-size:12px;line-height:1.5;margin-top:10px}}
.sub{{font-size:12.5px;margin-bottom:20px}}
</style></head><body>
<div class="c"><div class="i">{icone}</div>
<h1>{escape(titre)}</h1>
<p>{escape(message or '')}</p>
{bloc_cle}
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
