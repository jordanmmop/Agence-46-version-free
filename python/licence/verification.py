"""Vérification d'une licence Pro — signature ASYMÉTRIQUE, jamais un booléen.

CE QUE CE MODULE REFUSE DE FAIRE
--------------------------------
Il ne suffit PAS d'écrire `pro = true` quelque part pour devenir Pro. Ni dans
la configuration, ni dans une variable d'environnement, ni dans le stockage du
navigateur. L'unique façon d'obtenir `PRO_ACTIVE` est de présenter un JETON
SIGNÉ par la clé privée de l'émetteur de licences — clé qui ne quitte jamais
son serveur et n'existe nulle part dans l'application distribuée.

L'application n'embarque que la clé PUBLIQUE correspondante. Une clé publique
n'est pas un secret : elle ne permet QUE de vérifier une signature, jamais
d'en fabriquer une. C'est précisément ce qui permet de la livrer dans le
paquet MSIX sans rien exposer.

FORMAT DU JETON
---------------
    AGENCE1.<charge_utile_b64url>.<signature_b64url>

`charge_utile` est un JSON UTF-8 :

    {
      "sub":  "<identifiant du compte abonné>",
      "plan": "PRO",
      "iat":  <horodatage d'émission, secondes UNIX>,
      "exp":  <horodatage d'expiration, secondes UNIX>,
      "iss":  "agence46"
    }

La signature est un Ed25519 sur les octets EXACTS de la charge utile encodée.

HORS LIGNE
----------
Un jeton valide reste valide jusqu'à `exp`, sans réseau : couper le Wi-Fi ne
transforme pas un abonné en version d'essai. Passé `exp`, l'état devient
`PRO_EXPIRED` — jamais `PRO_ACTIVE`.

ÉTAT ACTUEL DU PROJET
---------------------
Aucune infrastructure de licences n'est déployée à ce jour : `_cle_publique()`
ne renvoie donc rien et TOUTE vérification échoue → l'application est en
version d'essai. C'est le comportement voulu tant que l'émetteur n'existe pas.
Le jour où il existe, il suffit de renseigner `CLE_PUBLIQUE_EMETTEUR`
ci-dessous (ou la variable d'environnement `AGENCE_LICENCE_PUBKEY` pour un
test) : rien d'autre ne change dans l'application.
"""
import base64
import json
import logging
import os
import time
from typing import Any, Dict, Optional, Tuple

from licence import config as lconfig
from licence.etat import EtatLicence

logger = logging.getLogger(__name__)

PREFIXE = "AGENCE1"
EMETTEUR_ATTENDU = "agence46"

# Clé publique Ed25519 de l'émetteur de licences, en hexadécimal (32 octets).
#
# VIDE TANT QU'AUCUN ÉMETTEUR N'EXISTE. Ce n'est pas un oubli : une clé
# inventée ici donnerait une chaîne d'apparence crédible que personne ne peut
# utiliser, et masquerait le fait qu'il n'y a pas encore de serveur de
# licences. Une clé PUBLIQUE n'est pas un secret — le jour venu, elle a sa
# place ici, en clair, dans le dépôt.
CLE_PUBLIQUE_EMETTEUR = ""


def _cle_publique() -> Optional[bytes]:
    """Clé publique de l'émetteur, ou None si aucune n'est configurée.

    Trois origines, de la plus explicite à la plus locale :

    1. `AGENCE_LICENCE_PUBKEY` — la clé de l'émetteur du parc, livrée aux
       postes. Prioritaire : un administrateur qui la renseigne veut que SEUL
       cet émetteur soit reconnu.
    2. `CLE_PUBLIQUE_EMETTEUR` — la même, figée dans le paquet distribué.
    3. La clé publique de l'émetteur LOCAL, quand cette installation en a un
       (voir `licence/emetteur.py`). C'est ce qui permet à une installation
       autonome de reconnaître les jetons qu'elle s'est signés après
       validation d'une clé d'abonnement en base — sans quoi une activation
       par clé ne pourrait aboutir nulle part. Elle n'est consultée qu'à
       défaut des deux précédentes : brancher un émetteur central n'ouvre
       donc pas, au passage, la porte aux licences locales.
    """
    brut = (os.getenv("AGENCE_LICENCE_PUBKEY", "") or CLE_PUBLIQUE_EMETTEUR).strip()
    if not brut:
        try:
            from licence import emetteur
            brut = emetteur.cle_publique_hex().strip()
        except Exception as e:                  # émetteur absent ou illisible
            logger.debug("[licence] Aucun émetteur local : %s", e)
            brut = ""
    if not brut:
        return None
    try:
        cle = bytes.fromhex(brut)
    except ValueError:
        logger.warning("[licence] Clé publique illisible (hexadécimal attendu)")
        return None
    if len(cle) != 32:
        logger.warning("[licence] Clé publique de taille inattendue (%d octets)", len(cle))
        return None
    return cle


def _b64d(texte: str) -> bytes:
    """base64url sans remplissage → octets. Lève ValueError si invalide."""
    rembourrage = "=" * (-len(texte) % 4)
    return base64.urlsafe_b64decode(texte + rembourrage)


def b64e(donnees: bytes) -> str:
    """Octets → base64url sans remplissage (utilisé aussi par les tests et
    par l'outil d'émission de licences côté serveur)."""
    return base64.urlsafe_b64encode(donnees).decode("ascii").rstrip("=")


def _verifier_signature(charge_encodee: str, signature: bytes) -> bool:
    """Ed25519. Renvoie False — jamais une exception — si la vérification est
    impossible : sans `cryptography`, sans clé, ou sur signature invalide.

    ÉCHEC FERMÉ : tout ce qui n'est pas une signature formellement valide vaut
    refus. Une bibliothèque manquante ne doit pas ouvrir l'accès Pro.
    """
    cle = _cle_publique()
    if not cle:
        return False
    try:
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
        from cryptography.exceptions import InvalidSignature
    except ImportError:
        logger.warning("[licence] Module « cryptography » absent : licence non vérifiable")
        return False
    try:
        Ed25519PublicKey.from_public_bytes(cle).verify(signature, charge_encodee.encode("ascii"))
        return True
    except InvalidSignature:
        return False
    except Exception as e:                  # clé mal formée, entrée aberrante
        logger.warning("[licence] Vérification impossible : %s", e)
        return False


def lire_jeton(jeton: str) -> Tuple[EtatLicence, Dict[str, Any]]:
    """(état, informations) déduits d'un jeton de licence.

    Ne lève jamais : une entrée corrompue, tronquée ou hostile vaut TRIAL.
    """
    vide: Dict[str, Any] = {}
    if not jeton or not isinstance(jeton, str):
        return EtatLicence.TRIAL, vide

    morceaux = jeton.strip().split(".")
    if len(morceaux) != 3 or morceaux[0] != PREFIXE:
        return EtatLicence.TRIAL, {"erreur": "Format de licence non reconnu"}

    _, charge_encodee, signature_encodee = morceaux
    try:
        signature = _b64d(signature_encodee)
        charge = json.loads(_b64d(charge_encodee).decode("utf-8"))
    except Exception:
        return EtatLicence.TRIAL, {"erreur": "Licence illisible"}
    if not isinstance(charge, dict):
        return EtatLicence.TRIAL, {"erreur": "Licence illisible"}

    # La signature d'abord : rien de ce que contient la charge utile n'a de
    # valeur tant qu'on ne sait pas qui l'a écrite.
    if not _verifier_signature(charge_encodee, signature):
        return EtatLicence.TRIAL, {"erreur": "Signature de licence invalide"}

    if str(charge.get("iss", "")) != EMETTEUR_ATTENDU:
        return EtatLicence.TRIAL, {"erreur": "Émetteur de licence inattendu"}
    if str(charge.get("plan", "")).upper() != "PRO":
        return EtatLicence.TRIAL, {"erreur": "Licence sans plan Pro"}

    infos = {
        "sujet": str(charge.get("sub", "")),
        "plan": "PRO",
        "emise_le": _entier(charge.get("iat")),
        "expire_le": _entier(charge.get("exp")),
    }

    expire = infos["expire_le"]
    if expire is None:
        return EtatLicence.TRIAL, {"erreur": "Licence sans date d'expiration"}
    if expire + lconfig.LICENCE_TOLERANCE_HORLOGE_S < time.time():
        infos["erreur"] = "Abonnement expiré"
        return EtatLicence.PRO_EXPIRED, infos

    return EtatLicence.PRO_ACTIVE, infos


def _entier(valeur) -> Optional[int]:
    try:
        return int(valeur)
    except (TypeError, ValueError):
        return None
