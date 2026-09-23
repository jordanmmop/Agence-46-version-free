"""Abonnement Pro par Stripe : liens de paiement et VÉRIFICATION du règlement.

LE PRINCIPE
-----------
L'application n'encaisse rien et ne voit aucune carte. Elle ouvre une page
Stripe (`config.FORMULES[...]["lien_paiement"]`), et Stripe lui répond si le
paiement a eu lieu. Les droits Pro ne s'ouvrent QUE sur cette réponse.

CE QUI NE VAUT PAS PREUVE DE PAIEMENT
--------------------------------------
Rien de ce qui vient du navigateur. Ni « j'ai payé » cliqué dans l'interface,
ni un retour sur l'URL de succès, ni un identifiant de session collé à la
main. Le navigateur de l'utilisateur est sous son contrôle : le croire
reviendrait à distribuer l'abonnement gratuitement.

DEUX SOURCES DE VÉRITÉ, TOUTES DEUX CÔTÉ SERVEUR
------------------------------------------------
1. **Webhook** (`/api/abonnement/webhook`) — Stripe appelle l'application
   quand un paiement aboutit. La requête est SIGNÉE ; la signature est
   vérifiée avec `STRIPE_WEBHOOK_SECRET`. C'est le chemin de référence : il
   fonctionne même si l'utilisateur ferme son navigateur après avoir payé, et
   c'est lui qui porte les renouvellements mensuels et annuels.

2. **Relecture de session** — au retour de Stripe, l'application interroge
   l'API Stripe avec `STRIPE_SECRET_KEY` pour lire l'état RÉEL de la session
   de paiement. Sert de rattrapage immédiat quand le webhook tarde.

Sans `STRIPE_SECRET_KEY` ni `STRIPE_WEBHOOK_SECRET`, AUCUN paiement ne peut
être confirmé : l'application le dit clairement et reste fermée. C'est le
comportement voulu — mieux vaut un abonnement qu'on ne peut pas encore
encaisser qu'un abonnement qu'on offre à qui le demande.

CLÉS ET SECRETS
---------------
`STRIPE_SECRET_KEY` et `STRIPE_WEBHOOK_SECRET` sont des SECRETS : ils vivent
dans l'environnement du serveur, jamais dans le dépôt, jamais dans le paquet
distribué, jamais dans une réponse HTTP. Les liens de paiement, eux, sont
publics — ce sont des URL que l'on partage.
"""
import hashlib
import hmac
import json
import logging
import os
import time
from typing import Any, Dict, Optional, Tuple

from licence import comptes
from licence import config as lconfig

logger = logging.getLogger(__name__)

API_STRIPE = "https://api.stripe.com/v1"

# Tolérance sur l'horodatage d'un webhook (secondes) : au-delà, la requête est
# refusée même si sa signature est bonne. C'est ce qui empêche de rejouer
# indéfiniment un ancien appel capté sur le réseau.
TOLERANCE_WEBHOOK_S = 300


# ═══════════════════════════ CONFIGURATION ════════════════════════════════

def cle_secrete() -> str:
    return (os.getenv("STRIPE_SECRET_KEY", "") or "").strip()


def secret_webhook() -> str:
    return (os.getenv("STRIPE_WEBHOOK_SECRET", "") or "").strip()


def encaissement_configure() -> bool:
    """Le serveur peut-il CONFIRMER un paiement ?

    Sans l'un ou l'autre de ces secrets, la réponse est non — et l'application
    ne doit alors promettre aucune activation automatique.
    """
    return bool(cle_secrete() or secret_webhook())


def formules_publiques(compte_id: str = "") -> Dict[str, Any]:
    """Les formules telles que l'interface les affiche.

    Le `client_reference_id` rattache le paiement au COMPTE : sans lui, Stripe
    confirmerait un règlement sans qu'on sache à qui ouvrir les droits.
    """
    sortie = {}
    for cle, f in lconfig.FORMULES.items():
        lien = f["lien_paiement"]
        if compte_id:
            separateur = "&" if "?" in lien else "?"
            lien = f"{lien}{separateur}client_reference_id={compte_id}"
        sortie[cle] = {**f, "lien_paiement": lien,
                       "test": "/test_" in f["lien_paiement"]}
    return sortie


# ═══════════════════════════ APPEL À L'API STRIPE ═════════════════════════

def _get_stripe(chemin: str, params: Dict[str, str] = None) -> Optional[Dict[str, Any]]:
    """GET authentifié sur l'API Stripe. None si impossible ou en erreur."""
    cle = cle_secrete()
    if not cle:
        return None
    try:
        import requests
        r = requests.get(f"{API_STRIPE}/{chemin}", params=params or {},
                         auth=(cle, ""), timeout=15)
    except Exception as e:
        logger.warning("[stripe] API injoignable : %s", e)
        return None
    if r.status_code != 200:
        logger.warning("[stripe] %s → HTTP %s", chemin, r.status_code)
        return None
    try:
        return r.json()
    except Exception:
        return None


def _formule_depuis_montant(montant_centimes: Optional[int],
                            defaut: str = "") -> str:
    """Retrouve la formule réglée à partir du montant encaissé.

    Le montant fait foi : c'est ce que l'utilisateur a réellement payé. Se fier
    à un paramètre d'URL laisserait choisir l'abonnement annuel en payant le
    tarif mensuel.
    """
    if not montant_centimes:
        return defaut
    for cle, f in lconfig.FORMULES.items():
        if abs(int(round(f["prix"] * 100)) - int(montant_centimes)) <= 1:
            return cle
    return defaut


# ═══════════════════════════ RELECTURE D'UNE SESSION ══════════════════════

def verifier_session(session_id: str) -> Dict[str, Any]:
    """Interroge Stripe sur une session de paiement et active si elle est réglée.

    `session_id` vient du navigateur et n'est donc PAS digne de confiance : il
    n'est qu'une référence à vérifier auprès de Stripe. C'est la réponse de
    Stripe — payée ou non, et pour quel compte — qui décide.
    """
    session_id = str(session_id or "").strip()
    if not session_id.startswith("cs_"):
        return {"success": False, "error": "Référence de paiement invalide."}
    if not cle_secrete():
        return {"success": False, "paiement_non_verifiable": True,
                "error": "La vérification automatique des paiements n'est pas "
                         "configurée sur ce serveur (STRIPE_SECRET_KEY)."}

    session = _get_stripe(f"checkout/sessions/{session_id}")
    if not session:
        return {"success": False,
                "error": "Paiement non confirmé par Stripe pour le moment."}

    if str(session.get("payment_status")) != "paid":
        return {"success": False,
                "error": f"Paiement non abouti (état Stripe : "
                         f"{session.get('payment_status', 'inconnu')})."}

    compte_id = str(session.get("client_reference_id") or "")
    if not compte_id:
        return {"success": False,
                "error": "Ce paiement n'est rattaché à aucun compte. "
                         "Contactez le support avec la référence " + session_id}

    formule = _formule_depuis_montant(session.get("amount_total"),
                                      lconfig.FORMULE_DEFAUT)
    return _activer(compte_id, formule, session_id)


def _activer(compte_id: str, formule: str, reference: str) -> Dict[str, Any]:
    try:
        compte = comptes.activer_abonnement(compte_id, formule, reference)
    except comptes.ErreurCompte as e:
        return {"success": False, "error": str(e)}
    return {"success": True, "formule": formule,
            "compte": comptes.public(compte),
            "message": "Paiement confirmé — abonnement Pro activé."}


# ═══════════════════════════ WEBHOOK ══════════════════════════════════════

def verifier_signature(charge: bytes, entete: str) -> Tuple[bool, str]:
    """Vérifie l'en-tête `Stripe-Signature` d'un webhook.

    Format : « t=<horodatage>,v1=<signature>[,v1=<autre>] ». La signature est
    un HMAC-SHA256 de « <t>.<charge brute> » avec le secret du webhook.

    ÉCHEC FERMÉ : sans secret configuré, en-tête absent, horodatage trop
    ancien ou signature fausse — on refuse. Un webhook non vérifié serait une
    route publique capable d'offrir des abonnements à quiconque la découvre.
    """
    secret = secret_webhook()
    if not secret:
        return False, "Webhook Stripe non configuré (STRIPE_WEBHOOK_SECRET)."
    if not entete:
        return False, "Signature Stripe absente."

    horodatage, signatures = "", []
    for partie in entete.split(","):
        cle, _, valeur = partie.strip().partition("=")
        if cle == "t":
            horodatage = valeur
        elif cle == "v1":
            signatures.append(valeur)
    if not horodatage or not signatures:
        return False, "Signature Stripe malformée."

    try:
        ecart = abs(time.time() - int(horodatage))
    except ValueError:
        return False, "Horodatage de signature invalide."
    if ecart > TOLERANCE_WEBHOOK_S:
        return False, "Signature Stripe expirée (requête rejouée ?)."

    attendu = hmac.new(secret.encode(),
                       f"{horodatage}.".encode() + charge,
                       hashlib.sha256).hexdigest()
    # compare_digest sur CHAQUE signature : Stripe en envoie plusieurs pendant
    # une rotation de secret.
    if any(hmac.compare_digest(attendu, s) for s in signatures):
        return True, ""
    return False, "Signature Stripe invalide."


# Événements qui ouvrent ou prolongent des droits. Tout le reste est ignoré
# (et acquitté) : un webhook inconnu ne doit ni activer ni faire échouer.
EVENEMENTS_PAIEMENT = ("checkout.session.completed",
                       "invoice.paid",
                       "invoice.payment_succeeded")

EVENEMENTS_FIN = ("customer.subscription.deleted",
                  "invoice.payment_failed")


def traiter_webhook(charge: bytes, entete_signature: str) -> Dict[str, Any]:
    """Point d'entrée du webhook Stripe. Ne lève jamais.

    Renvoie `{"success": bool, ...}` ; l'appelant traduit en code HTTP.
    """
    valide, raison = verifier_signature(charge, entete_signature)
    if not valide:
        logger.warning("[stripe] Webhook refusé : %s", raison)
        return {"success": False, "error": raison, "code": 400}

    try:
        evenement = json.loads(charge.decode("utf-8"))
    except Exception:
        return {"success": False, "error": "Charge utile illisible.", "code": 400}

    type_evt = str(evenement.get("type") or "")
    objet = (evenement.get("data") or {}).get("object") or {}

    if type_evt in EVENEMENTS_PAIEMENT:
        compte_id = str(objet.get("client_reference_id")
                        or (objet.get("metadata") or {}).get("compte_id") or "")
        if not compte_id:
            # Paiement réel mais orphelin : on l'acquitte (Stripe cesserait
            # de réessayer de toute façon) en le journalisant pour le support.
            logger.error("[stripe] Paiement sans compte rattaché : %s",
                         objet.get("id"))
            return {"success": True, "ignore": "paiement sans compte rattaché"}
        montant = objet.get("amount_total") or objet.get("amount_paid")
        formule = _formule_depuis_montant(montant, lconfig.FORMULE_DEFAUT)
        resultat = _activer(compte_id, formule, str(objet.get("id") or ""))
        resultat.setdefault("evenement", type_evt)
        return resultat

    if type_evt in EVENEMENTS_FIN:
        # Ni suspension immédiate, ni activation : le compte garde les droits
        # déjà payés jusqu'à leur terme, puis `etat_du_compte` les retire de
        # lui-même. Couper net priverait un abonné d'un mois déjà réglé sur un
        # simple incident de renouvellement.
        logger.info("[stripe] Fin d'abonnement signalée (%s)", type_evt)
        return {"success": True, "evenement": type_evt,
                "info": "droits conservés jusqu'à leur terme"}

    return {"success": True, "ignore": type_evt}


# ═══════════════════════════ ÉTAT POUR L'INTERFACE ════════════════════════

def etat_paiement(compte_id: str = "") -> Dict[str, Any]:
    """Ce que l'interface a besoin de savoir pour afficher l'écran d'abonnement.

    Ne contient AUCUN secret : ni clé Stripe, ni secret de webhook.
    """
    formules = formules_publiques(compte_id)
    return {
        "formules": formules,
        "formule_defaut": lconfig.FORMULE_DEFAUT,
        "encaissement_configure": encaissement_configure(),
        "verification_auto": bool(cle_secrete()),
        "webhook_configure": bool(secret_webhook()),
        # Vrai tant que les liens pointent sur l'environnement de test Stripe :
        # l'interface doit le dire, sinon un utilisateur croirait avoir payé.
        "mode_test": any(f.get("test") for f in formules.values()),
        "essai_jours": lconfig.TRIAL_DUREE_JOURS,
    }
