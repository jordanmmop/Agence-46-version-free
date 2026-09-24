"""Remise de la clé d'abonnement à son titulaire : e-mail et SMS.

POURQUOI CE MODULE EXISTE
-------------------------
Une clé d'abonnement émise et jamais remise ne sert à rien. L'abonné qui vient
de payer doit la recevoir sur les coordonnées de SON compte — celles qu'il a
saisies à l'inscription, jamais une adresse fournie dans la requête : sinon il
suffirait de demander l'envoi vers une autre boîte pour détourner un abonnement.

AUCUN SECRET ICI
----------------
Ni identifiant SMTP, ni jeton de passerelle SMS n'est écrit dans ce fichier.
Tout vient de l'environnement du serveur (`python/.env`, mode 600, ignoré par
git). Sans configuration, l'envoi n'a pas lieu et le module le DIT : il ne
renvoie jamais « envoyé » pour un message qui n'est pas parti. C'est ce qui
permet à l'écran d'abonnement d'afficher la clé quand elle n'a pas pu être
expédiée, plutôt que de laisser l'abonné l'attendre.

CANAUX
------
E-mail   : SMTP, bibliothèque standard, aucune dépendance supplémentaire.
SMS      : passerelle au choix — Twilio, OVH, ou n'importe quelle passerelle
           HTTP maison. Aucun opérateur n'est imposé, et aucun n'est requis :
           l'e-mail suffit à remettre la clé.

Un envoi qui échoue n'annule jamais l'abonnement : le paiement est encaissé, la
clé est émise et consultable dans l'application. L'échec est journalisé (sans
la clé) et remonté à l'appelant.
"""
import logging
import os
import re
import time
import unicodedata
from typing import Any, Dict, Optional, Tuple

logger = logging.getLogger(__name__)

TIMEOUT_S = 15
EXPEDITEUR_PAR_DEFAUT = "Agence Numérique Financière"


def _env(nom: str, defaut: str = "") -> str:
    return (os.getenv(nom, "") or defaut).strip()


def _sans_accent(texte: str) -> str:
    """Translittération ASCII — pour les SMS uniquement.

    Un seul caractère accentué fait basculer un SMS en UCS-2 : le message
    passe de 160 à 70 caractères et se fragmente en deux envois facturés.
    """
    decompose = unicodedata.normalize("NFKD", texte)
    return "".join(c for c in decompose if not unicodedata.combining(c))


def masquer_email(adresse: str) -> str:
    """« jean.dupont@example.com » → « j••••••••@example.com ».

    Ce qu'on réaffiche après un envoi : assez pour que le titulaire reconnaisse
    sa boîte, pas assez pour qu'un tiers apprenne l'adresse d'un abonné.
    """
    adresse = (adresse or "").strip()
    if "@" not in adresse:
        return ""
    local, _, domaine = adresse.partition("@")
    return f"{local[:1]}{'•' * max(1, len(local) - 1)}@{domaine}"


def masquer_telephone(numero: str) -> str:
    """« +33612345678 » → « ••••••••78 »."""
    chiffres = re.sub(r"\D", "", numero or "")
    if len(chiffres) < 4:
        return ""
    return "•" * (len(chiffres) - 2) + chiffres[-2:]


# ═══════════════════════════ E-MAIL (SMTP) ════════════════════════════════

def email_configure() -> bool:
    return bool(_env("SMTP_HOTE"))


def _expediteur_email() -> str:
    return _env("SMTP_EXPEDITEUR") or _env("SMTP_UTILISATEUR")


def envoyer_email(destinataire: str, sujet: str, corps: str) -> Tuple[bool, str]:
    """Envoie un e-mail en texte brut. Renvoie (succès, explication).

    Ne lève jamais : un serveur SMTP injoignable ne doit pas faire échouer la
    confirmation d'un paiement déjà encaissé.
    """
    if not email_configure():
        return False, "Aucun serveur SMTP configuré (SMTP_HOTE)."
    destinataire = (destinataire or "").strip()
    if "@" not in destinataire:
        return False, "Adresse e-mail du compte inutilisable."

    hote = _env("SMTP_HOTE")
    securite = (_env("SMTP_SECURITE", "starttls")).lower()
    defaut = 465 if securite == "ssl" else 587
    try:
        port = int(_env("SMTP_PORT") or defaut)
    except ValueError:
        logger.error("[envoi] SMTP_PORT illisible (« %s ») : %d utilisé.",
                     _env("SMTP_PORT"), defaut)
        port = defaut
    utilisateur = _env("SMTP_UTILISATEUR")
    mot_de_passe = os.getenv("SMTP_MOTDEPASSE", "")
    expediteur = _expediteur_email()
    if not expediteur:
        return False, "Expéditeur non configuré (SMTP_EXPEDITEUR)."

    try:
        import smtplib
        from email.message import EmailMessage
        from email.utils import formataddr

        message = EmailMessage()
        message["Subject"] = sujet
        message["From"] = formataddr((EXPEDITEUR_PAR_DEFAUT, expediteur))
        message["To"] = destinataire
        message.set_content(corps)

        if securite == "ssl":
            serveur = smtplib.SMTP_SSL(hote, port, timeout=TIMEOUT_S)
        else:
            serveur = smtplib.SMTP(hote, port, timeout=TIMEOUT_S)
        with serveur:
            if securite == "starttls":
                # STARTTLS par défaut : les identifiants SMTP et la clé
                # d'abonnement ne doivent pas traverser le réseau en clair.
                serveur.starttls()
            # Authentifier UNIQUEMENT quand il y a de quoi : un relais interne
            # (`SMTP_SECURITE=aucune`, sans mot de passe) n'annonce pas
            # l'extension AUTH, et `login()` y échoue avec
            # « SMTP AUTH extension not supported by server » — un message qui
            # ne dit pas que le problème est d'avoir voulu s'authentifier.
            if utilisateur and mot_de_passe:
                serveur.login(utilisateur, mot_de_passe)
            serveur.send_message(message)
        return True, f"E-mail envoyé à {masquer_email(destinataire)}"
    except Exception as e:
        logger.error("[envoi] E-mail non envoyé (%s) : %s", hote, e)
        return False, f"Envoi de l'e-mail impossible : {e}"


# ═══════════════════════════ SMS ══════════════════════════════════════════

FOURNISSEURS_SMS = ("twilio", "ovh", "http")


def fournisseur_sms() -> str:
    """Passerelle SMS retenue, ou chaîne vide si aucune n'est configurée."""
    nom = _env("SMS_FOURNISSEUR").lower()
    if nom in FOURNISSEURS_SMS:
        return nom
    # Déduction du fournisseur à partir des variables présentes : un
    # administrateur qui renseigne ses identifiants Twilio n'a pas à déclarer
    # deux fois la même chose.
    if _env("TWILIO_ACCOUNT_SID"):
        return "twilio"
    if _env("OVH_APPLICATION_KEY"):
        return "ovh"
    if _env("SMS_URL"):
        return "http"
    return ""


def sms_configure() -> bool:
    return bool(fournisseur_sms())


def envoyer_sms(destinataire: str, texte: str) -> Tuple[bool, str]:
    """Envoie un SMS par la passerelle configurée. Renvoie (succès, explication)."""
    fournisseur = fournisseur_sms()
    if not fournisseur:
        return False, "Aucune passerelle SMS configurée (SMS_FOURNISSEUR)."
    numero = (destinataire or "").strip()
    if not numero.startswith("+"):
        # Les passerelles exigent le format international. Un « 06… » français
        # envoyé tel quel est refusé côté opérateur, sans message clair.
        indicatif = _env("SMS_INDICATIF_DEFAUT", "+33")
        chiffres = re.sub(r"\D", "", numero).lstrip("0")
        numero = f"{indicatif}{chiffres}" if chiffres else ""
    if len(re.sub(r"\D", "", numero)) < 8:
        return False, "Numéro de téléphone du compte inutilisable."

    texte = _sans_accent(texte)
    try:
        if fournisseur == "twilio":
            return _sms_twilio(numero, texte)
        if fournisseur == "ovh":
            return _sms_ovh(numero, texte)
        return _sms_http(numero, texte)
    except Exception as e:
        logger.error("[envoi] SMS non envoyé (%s) : %s", fournisseur, e)
        return False, f"Envoi du SMS impossible : {e}"


def _sms_twilio(numero: str, texte: str) -> Tuple[bool, str]:
    import requests
    sid = _env("TWILIO_ACCOUNT_SID")
    jeton = os.getenv("TWILIO_AUTH_TOKEN", "")
    expediteur = _env("TWILIO_EXPEDITEUR")
    if not (sid and jeton and expediteur):
        return False, ("Configuration Twilio incomplète (TWILIO_ACCOUNT_SID, "
                       "TWILIO_AUTH_TOKEN, TWILIO_EXPEDITEUR).")
    reponse = requests.post(
        f"https://api.twilio.com/2010-04-01/Accounts/{sid}/Messages.json",
        auth=(sid, jeton), timeout=TIMEOUT_S,
        data={"To": numero, "From": expediteur, "Body": texte})
    if reponse.status_code >= 300:
        return False, f"Twilio a refusé l'envoi (HTTP {reponse.status_code})."
    return True, f"SMS envoyé au {masquer_telephone(numero)}"


def _sms_ovh(numero: str, texte: str) -> Tuple[bool, str]:
    """OVHcloud SMS — requête signée (SHA-1 sur secret + clé client + corps).

    L'horodatage est celui du SERVEUR OVH, pas celui de la machine : une
    dérive d'horloge de plus de quelques secondes fait rejeter la signature,
    et le message d'erreur d'OVH ne le dit pas.
    """
    import hashlib
    import json
    import requests

    point = _env("OVH_ENDPOINT", "https://eu.api.ovh.com/1.0").rstrip("/")
    application = _env("OVH_APPLICATION_KEY")
    secret = os.getenv("OVH_APPLICATION_SECRET", "")
    consommateur = os.getenv("OVH_CONSUMER_KEY", "")
    service = _env("OVH_SERVICE_SMS")
    if not (application and secret and consommateur and service):
        return False, ("Configuration OVH incomplète (OVH_APPLICATION_KEY, "
                       "OVH_APPLICATION_SECRET, OVH_CONSUMER_KEY, OVH_SERVICE_SMS).")

    try:
        horodatage = str(int(requests.get(f"{point}/auth/time", timeout=TIMEOUT_S).text))
    except Exception:
        horodatage = str(int(time.time()))

    url = f"{point}/sms/{service}/jobs"
    charge: Dict[str, Any] = {
        "message": texte,
        "receivers": [numero],
        "charset": "UTF-8",
        # Message transactionnel : pas de mention « STOP », qui est réservée
        # à la prospection et rognerait la place utile.
        "noStopClause": True,
        "priority": "high",
        "senderForResponse": False,
    }
    expediteur = _env("OVH_EXPEDITEUR")
    if expediteur:
        charge["sender"] = expediteur
    corps = json.dumps(charge)
    a_signer = f"{secret}+{consommateur}+POST+{url}+{corps}+{horodatage}"
    signature = "$1$" + hashlib.sha1(a_signer.encode("utf-8")).hexdigest()

    reponse = requests.post(url, data=corps, timeout=TIMEOUT_S, headers={
        "Content-Type": "application/json",
        "X-Ovh-Application": application,
        "X-Ovh-Consumer": consommateur,
        "X-Ovh-Timestamp": horodatage,
        "X-Ovh-Signature": signature,
    })
    if reponse.status_code >= 300:
        return False, f"OVH a refusé l'envoi (HTTP {reponse.status_code})."
    return True, f"SMS envoyé au {masquer_telephone(numero)}"


def _sms_http(numero: str, texte: str) -> Tuple[bool, str]:
    """Passerelle HTTP quelconque : POST JSON {destinataire, message}.

    Permet de brancher n'importe quel opérateur sans modifier ce fichier —
    y compris un service interne. HTTPS exigé : le message contient la clé.
    """
    import requests
    url = _env("SMS_URL")
    if not url:
        return False, "SMS_URL non configurée."
    if not url.startswith("https://"):
        return False, "SMS_URL doit être en HTTPS (le message contient la clé)."
    entetes = {"Content-Type": "application/json"}
    autorisation = os.getenv("SMS_AUTORISATION", "")
    if autorisation:
        entetes["Authorization"] = autorisation
    reponse = requests.post(url, json={"destinataire": numero, "message": texte},
                            headers=entetes, timeout=TIMEOUT_S)
    if reponse.status_code >= 300:
        return False, f"La passerelle SMS a refusé l'envoi (HTTP {reponse.status_code})."
    return True, f"SMS envoyé au {masquer_telephone(numero)}"


# ═══════════════════════════ MESSAGE DE LA CLÉ ════════════════════════════

def _libelle_formule(formule: str) -> str:
    from licence import config as lconfig
    fiche = lconfig.FORMULES.get(formule or "")
    if not fiche:
        return "Pro"
    return f"{fiche['libelle']} — {fiche['prix']:.2f} € par {fiche['periode']}".replace(".", ",")


def corps_email(cle: str, formule: str, echeance: Optional[float] = None) -> str:
    fin = ""
    if echeance:
        fin = ("\nVos droits Pro sont ouverts jusqu'au "
               + time.strftime("%d/%m/%Y", time.localtime(echeance)) + ".\n")
    return f"""Votre paiement est confirmé — merci.

Formule souscrite : {_libelle_formule(formule)}
{fin}
VOTRE CLÉ D'ABONNEMENT :

    {cle}

Comment l'utiliser :
  1. Ouvrez l'Agence Numérique Financière.
  2. Cliquez sur « Passer à la version Pro ».
  3. Collez la clé ci-dessus, puis validez.

La version Pro débloque l'ensemble des agents, l'exécution simultanée, les
workflows avancés, les automatisations et les tâches longues, sans limite de
requêtes.

Conservez ce message : cette clé est votre preuve d'abonnement. Elle ne peut
pas être réaffichée — si vous la perdez, demandez-en une nouvelle depuis
l'écran d'abonnement de l'application (la précédente est alors remplacée).

Ne la transmettez à personne : elle ouvre l'accès à votre abonnement.

— {EXPEDITEUR_PAR_DEFAUT}
"""


def corps_sms(cle: str) -> str:
    return (f"{_sans_accent(EXPEDITEUR_PAR_DEFAUT)} : votre cle d'abonnement Pro "
            f"est {cle}. A coller dans l'application, rubrique "
            f"\"Passer a la version Pro\". Ne la partagez pas.")


def envoyer_cle(compte: Dict[str, Any], cle: str,
                formule: str = "") -> Dict[str, Any]:
    """Remet la clé au titulaire du compte, par e-mail ET par SMS.

    Les destinataires viennent du COMPTE en base — jamais d'un paramètre
    d'appel. Renvoie le détail par canal, sans jamais recopier la clé.
    """
    resultat: Dict[str, Any] = {"email": False, "sms": False, "canaux": "",
                                "details": [], "cle_remise": False}
    if not cle or not compte:
        resultat["details"].append("Aucune clé à remettre.")
        return resultat

    echeance = compte.get("abonne_jusqua")

    def _tenter(canal: str, envoi) -> bool:
        """Isole un canal : ce qu'il casse ne sort pas d'ici.

        `envoyer_email` et `envoyer_sms` attrapent déjà leurs propres pannes,
        mais pas ce qui les précède — une variable d'environnement aberrante,
        un module absent, un destinataire impossible à mettre en forme. Sans
        cette barrière, un paiement ENCAISSÉ se solderait par une erreur
        serveur parce qu'un SMTP est mal configuré.
        """
        try:
            ok, detail = envoi()
        except Exception as e:
            logger.error("[envoi] Canal %s en échec : %s", canal, e, exc_info=True)
            ok, detail = False, f"Envoi {canal} impossible : {e}"
        resultat["details"].append(detail)
        return bool(ok)

    ok_mail = _tenter("e-mail", lambda: envoyer_email(
        str(compte.get("email") or ""),
        "Votre clé d'abonnement Pro — Agence Numérique Financière",
        corps_email(cle, formule, echeance)))
    resultat["email"] = ok_mail

    ok_sms = _tenter("SMS", lambda: envoyer_sms(
        str(compte.get("telephone") or ""), corps_sms(cle)))
    resultat["sms"] = ok_sms

    canaux = [nom for nom, ok in (("email", ok_mail), ("sms", ok_sms)) if ok]
    resultat["canaux"] = "+".join(canaux)
    resultat["cle_remise"] = bool(canaux)
    if not canaux:
        logger.warning("[envoi] Clé émise mais NON remise (compte %s) : %s",
                       compte.get("id"), " / ".join(resultat["details"]))
    return resultat


def diagnostic() -> Dict[str, Any]:
    """Ce qui est configuré pour la remise des clés — sans aucun secret."""
    return {
        "email": email_configure(),
        "email_hote": _env("SMTP_HOTE"),
        "email_expediteur": masquer_email(_expediteur_email()),
        "sms": sms_configure(),
        "sms_fournisseur": fournisseur_sms(),
    }
