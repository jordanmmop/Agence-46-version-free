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
import threading
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
                       "test": lconfig.lien_de_test(f["lien_paiement"])}
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


# Correspondance entre la périodicité déclarée par Stripe et nos formules.
_PERIODE_STRIPE = {"month": "mensuel", "year": "annuel"}


def _formule_depuis_periodicite(objet: Dict[str, Any]) -> str:
    """Formule déduite de la PÉRIODICITÉ de l'abonnement (« month » / « year »).

    Indispensable à cause de l'essai de 3 jours configuré sur les liens de
    paiement : à la souscription, Stripe n'encaisse RIEN et envoie donc un
    événement dont le montant vaut zéro. Le montant ne permet alors plus de
    distinguer un abonné mensuel d'un abonné annuel — et le repli sur la
    formule par défaut aurait accordé 34 jours de droits à quelqu'un venant de
    souscrire un an.

    On cherche la périodicité là où Stripe la place selon le type d'objet :
    lignes de facture, éléments de session, ou abonnement développé.
    """
    def _interval(prix: Any) -> str:
        if isinstance(prix, dict):
            recurrent = prix.get("recurring")
            if isinstance(recurrent, dict):
                return str(recurrent.get("interval") or "")
        return ""

    chemins = []
    for conteneur in ("lines", "line_items"):          # facture, session
        bloc = objet.get(conteneur)
        if isinstance(bloc, dict):
            chemins.extend(bloc.get("data") or [])
    abonnement_dev = objet.get("subscription")
    if isinstance(abonnement_dev, dict):
        articles = abonnement_dev.get("items")
        if isinstance(articles, dict):
            chemins.extend(articles.get("data") or [])

    for ligne in chemins:
        if not isinstance(ligne, dict):
            continue
        for cle in ("price", "plan"):
            formule = _PERIODE_STRIPE.get(_interval(ligne.get(cle)))
            if formule:
                return formule
    return ""


def _articles_de_session(session_id: str) -> Dict[str, Any]:
    """Lignes d'une session de paiement, lues via l'API (prix développé).

    Dernier recours quand l'événement lui-même ne porte pas la périodicité :
    Stripe n'inclut pas les articles dans la charge utile du webhook.
    """
    if not session_id:
        return {}
    reponse = _get_stripe(f"checkout/sessions/{session_id}/line_items",
                          {"expand[]": "data.price"})
    return {"line_items": reponse} if reponse else {}


def _formule_depuis_evenement(objet: Dict[str, Any], defaut: str = "") -> str:
    """Formule d'un paiement, par ordre de fiabilité décroissante.

    1. Le MONTANT encaissé : c'est ce que le client a réellement payé.
    2. La PÉRIODICITÉ portée par l'événement : seul recours pendant l'essai
       Stripe, où le montant vaut zéro.
    3. La périodicité relue via l'API, quand l'événement ne la porte pas.

    Ne retombe sur `defaut` qu'en dernier ressort, et l'appelant journalise
    alors l'incertitude : accorder la mauvaise formule est silencieux pour nous
    et coûteux pour l'abonné.
    """
    montant = objet.get("amount_total")
    if montant is None:
        montant = objet.get("amount_paid")
    formule = _formule_depuis_montant(montant)
    if formule:
        return formule

    formule = _formule_depuis_periodicite(objet)
    if formule:
        return formule

    identifiant = str(objet.get("id") or "")
    if identifiant.startswith("cs_"):
        formule = _formule_depuis_periodicite(_articles_de_session(identifiant))
        if formule:
            return formule
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

    formule = _formule_depuis_evenement(session)
    if not formule:
        logger.error("[stripe] Formule indéterminable pour la session %s — "
                     "repli sur « %s ». Vérifiez la périodicité du lien de "
                     "paiement.", session_id, lconfig.FORMULE_DEFAUT)
        formule = lconfig.FORMULE_DEFAUT
    return _activer(compte_id, formule, session_id)


def _activer(compte_id: str, formule: str, reference: str,
             differer_envoi: bool = False) -> Dict[str, Any]:
    """Ouvre les droits, ÉMET la clé d'abonnement et la remet à son titulaire.

    Les trois vont ensemble : un paiement confirmé sans clé remise laisse
    l'abonné devant un écran qui lui réclame une clé qu'il n'a jamais reçue.

    `differer_envoi` sert au webhook : Stripe considère l'appel en échec
    au-delà d'une vingtaine de secondes et le rejoue, ce qui produirait des
    envois en double. Un SMTP lent ne doit pas provoquer cela — l'envoi part
    alors dans un fil d'exécution séparé, la clé étant déjà enregistrée.
    """
    try:
        compte = comptes.activer_abonnement(compte_id, formule, reference)
    except comptes.ErreurCompte as e:
        return {"success": False, "error": str(e)}

    resultat = {"success": True, "formule": formule,
                "compte": comptes.public(compte),
                "message": "Paiement confirmé — abonnement Pro activé."}
    resultat.update(remettre_cle(compte, formule, reference, differer_envoi))
    return resultat


def _licence_signee(compte: Dict[str, Any]) -> str:
    """Licence `AGENCE1.…` pour ce compte, ou chaîne vide s'il n'y a pas
    d'émetteur configuré sur ce serveur.

    POURQUOI CE DÉTOUR PLUTÔT QUE LA SEULE CLÉ COURTE
    --------------------------------------------------
    Une clé « AGF-… » se vérifie DANS LA BASE DES COMPTES. Cela suffit quand
    l'application et la base sont au même endroit — un serveur qu'on utilise
    depuis son navigateur. Mais quand l'application est installée sur le poste
    de chaque client, ce poste n'a pas — et ne doit jamais avoir — les
    identifiants de la base : les lui livrer donnerait à chaque client un accès
    en écriture aux comptes de tous les autres.

    Une licence signée, elle, porte sa propre preuve : le poste la vérifie avec
    la clé PUBLIQUE embarquée à la compilation, sans accéder à quoi que ce
    soit. C'est donc elle qu'on remet dès qu'on peut en produire une.

    `creer=False` est essentiel : un serveur qui fabriquerait sa paire de clés
    tout seul signerait des licences qu'aucune application installée ne sait
    vérifier, sa clé publique n'ayant jamais été embarquée. L'émetteur se crée
    explicitement, par `scripts/abonnement.py emetteur`.
    """
    echeance = compte.get("abonne_jusqua")
    if not echeance:
        return ""
    try:
        from licence import emetteur
        if not emetteur.disponible():
            return ""
        return emetteur.emettre(compte.get("email") or compte["id"],
                                float(echeance), creer=False)
    except Exception as e:
        logger.error("[stripe] Licence non signée pour %s : %s",
                     compte.get("id"), e, exc_info=True)
        return ""


def remettre_cle(compte: Dict[str, Any], formule: str, reference: str,
                 differer_envoi: bool = False,
                 remplacer: bool = False) -> Dict[str, Any]:
    """Remet à l'abonné ce qui débloquera son application, et le lui envoie.

    UN SEUL chemin pour les trois voies — webhook Stripe, bouton « Je n'ai pas
    reçu ma clé », outil d'administration — sans quoi l'une d'elles finirait
    par livrer autre chose que les deux autres.

    `remplacer` : révoque les clés en cours et en émet une neuve, au lieu de
    respecter l'unicité par règlement. C'est ce que demande une réémission.

    Ne lève jamais : le paiement est encaissé et les droits sont ouverts. Une
    panne de SMTP ou de passerelle SMS ne doit pas faire répondre « échec » à
    un abonné qui a bien payé — elle se signale, et la clé reste réémettable
    depuis l'écran d'abonnement.
    """
    from licence import cles, notifications

    try:
        if remplacer:
            emission = cles.remplacer(compte["id"], formule,
                                      compte.get("abonne_jusqua"))
        else:
            emission = cles.emettre(compte["id"], formule, reference,
                                    compte.get("abonne_jusqua"))
    except Exception as e:
        logger.error("[stripe] Clé d'abonnement non émise pour %s : %s",
                     compte.get("id"), e, exc_info=True)
        return {"cle_emise": False,
                "cle_message": "L'abonnement est actif, mais la clé n'a pas pu "
                               "être émise. Demandez-la depuis l'écran "
                               "d'abonnement de l'application."}

    if emission["deja_emise"]:
        # Le webhook et le retour du navigateur annoncent le MÊME règlement :
        # la clé est déjà partie, on ne la réémet pas et on n'en renvoie pas.
        return {"cle_emise": True, "cle_deja_emise": True,
                "cle_message": "Votre clé d'abonnement vous a déjà été envoyée."}

    courte = emission["cle"]
    empreinte = emission["enregistrement"]["cle_hash"]
    licence = _licence_signee(compte)
    # Ce que l'abonné colle : la licence quand elle existe — elle fonctionne
    # partout, y compris là où la clé courte ne peut rien.
    remis = licence or courte

    def _envoi() -> Dict[str, Any]:
        envoi = notifications.envoyer_cle(compte, courte, formule, licence)
        cles.marquer_envoi(empreinte, envoi.get("canaux", ""))
        return envoi

    quoi = "licence" if licence else "clé"
    if differer_envoi:
        threading.Thread(target=_envoi, name="envoi-cle", daemon=True).start()
        return {"cle_emise": True, "cle_envoi_differe": True,
                "licence_signee": bool(licence),
                "cle_message": f"Votre {quoi} d'abonnement vous est envoyée."}

    envoi = _envoi()
    return {"cle_emise": True, "cle": remis, "cle_courte": courte,
            "licence_signee": bool(licence), "cle_envoi": envoi,
            "cle_message": _message_envoi(envoi, compte, quoi)}


def _message_envoi(envoi: Dict[str, Any], compte: Dict[str, Any],
                   quoi: str = "clé") -> str:
    """Phrase montrée à l'abonné — exacte, y compris quand rien n'est parti."""
    from licence import notifications
    parties = []
    if envoi.get("email"):
        parties.append(f"par e-mail à {notifications.masquer_email(compte.get('email', ''))}")
    if envoi.get("sms"):
        parties.append(f"par SMS au {notifications.masquer_telephone(compte.get('telephone', ''))}")
    if parties:
        return f"Votre {quoi} d'abonnement vous a été envoyée " + " et ".join(parties) + "."
    return (f"Votre {quoi} d'abonnement est affichée à l'écran : aucun envoi "
            "automatique n'a abouti, notez-la dès maintenant.")


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
        formule = _formule_depuis_evenement(objet)
        if not formule:
            # L'essai Stripe fait arriver un montant nul : sans périodicité
            # lisible non plus, on ne peut qu'avertir. Accorder la mauvaise
            # formule est silencieux pour nous, coûteux pour l'abonné.
            logger.error("[stripe] Formule indéterminable (%s, objet %s) — "
                         "repli sur « %s »", type_evt, objet.get("id"),
                         lconfig.FORMULE_DEFAUT)
            formule = lconfig.FORMULE_DEFAUT
        # `differer_envoi` : Stripe rejoue un webhook qui met trop longtemps à
        # répondre. L'envoi de la clé part donc en arrière-plan — jamais dans
        # le temps de réponse dû à Stripe.
        resultat = _activer(compte_id, formule, str(objet.get("id") or ""),
                            differer_envoi=True)
        resultat.setdefault("evenement", type_evt)
        # La clé ne sort JAMAIS par le webhook : la réponse part chez Stripe,
        # pas chez l'abonné.
        resultat.pop("cle", None)
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

def incoherence_environnement() -> str:
    """Les liens de paiement et la clé secrète parlent-ils du même monde ?

    Renvoie un message d'alerte, ou une chaîne vide si tout concorde.

    Ces deux réglages viennent d'endroits différents — un fichier de
    configuration et une variable d'environnement — et rien n'oblige à les
    changer ensemble. Or les mélanger ne produit AUCUNE erreur visible au
    moment de la configuration : la panne n'apparaît qu'au premier client.

    Le cas grave est le second :

    - clé de TEST + liens de PRODUCTION : le client paie RÉELLEMENT, la
      vérification interroge l'API de test, le paiement n'est jamais confirmé
      et le compte reste suspendu. Vous encaissez sans rien livrer.
    - clé de PRODUCTION + liens de TEST : personne ne paie vraiment, et aucun
      abonnement ne s'active. Manque à gagner, sans dommage pour le client.
    """
    cle = cle_secrete()
    if not cle:
        return ""
    liens_de_test = [f["id"] for f in lconfig.FORMULES.values()
                     if lconfig.lien_de_test(f["lien_paiement"])]
    tous_en_test = len(liens_de_test) == len(lconfig.FORMULES)
    aucun_en_test = not liens_de_test

    if cle.startswith("sk_test_") and aucun_en_test:
        return ("DANGER : clé Stripe de TEST avec des liens de paiement de "
                "PRODUCTION. Vos clients paieront réellement, mais le paiement "
                "ne pourra jamais être confirmé et leur compte restera "
                "suspendu. Utilisez une clé « sk_live_ ».")
    if cle.startswith("sk_live_") and tous_en_test:
        return ("Clé Stripe de PRODUCTION avec des liens de paiement de TEST : "
                "aucun règlement réel ne sera encaissé. Remplacez les liens "
                "(STRIPE_LIEN_MENSUEL / STRIPE_LIEN_ANNUEL, ou "
                "licence/config.py).")
    if liens_de_test and not tous_en_test:
        return ("Les formules mélangent liens de test et liens de production : "
                + ", ".join(liens_de_test) + " en test. Alignez-les.")
    return ""


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
        # Message d'alerte si la clé et les liens ne parlent pas du même monde.
        "incoherence": incoherence_environnement(),
        "essai_jours": lconfig.TRIAL_DUREE_JOURS,
        # Par quels canaux la clé d'abonnement sera remise. L'interface s'en
        # sert pour annoncer ce qui va RÉELLEMENT se passer après le paiement,
        # plutôt que de promettre un e-mail qu'aucun serveur n'enverra.
        "remise_cle": _remise_cle(),
        "cles": _cles_du_compte(compte_id),
    }


def _remise_cle() -> Dict[str, Any]:
    from licence import notifications
    diag = notifications.diagnostic()
    return {"email": diag["email"], "sms": diag["sms"],
            "sms_fournisseur": diag["sms_fournisseur"],
            "aucun_canal": not (diag["email"] or diag["sms"])}


def _cles_du_compte(compte_id: str) -> list:
    """Clés déjà émises pour ce compte — masquées, jamais en clair."""
    if not compte_id:
        return []
    try:
        from licence import cles
        return cles.cles_du_compte(compte_id)
    except Exception as e:
        logger.warning("[stripe] Clés du compte illisibles : %s", e)
        return []
