"""Émission des jetons de licence signés (Ed25519), côté serveur.

CE QUE FAIT CE MODULE
---------------------
Il transforme un abonnement CONSTATÉ EN BASE en jeton `AGENCE1.…` signé, que
l'installation enregistre et revérifie ensuite toute seule, hors ligne, jusqu'à
l'échéance. C'est la contrepartie de `licence/verification.py`, qui ne sait que
VÉRIFIER : ici on signe, là-bas on contrôle.

Il n'est jamais appelé sur la seule demande d'un utilisateur : l'appelant
(`licence/abonnement.py`) valide d'abord une clé d'abonnement contre la base
des comptes. Signer sans cette vérification reviendrait à distribuer Pro.

LA CLÉ PRIVÉE
-------------
Elle ne se trouve NI dans le dépôt, NI dans le paquet distribué, NI dans une
réponse HTTP. Deux origines possibles, dans cet ordre :

1. `AGENCE_LICENCE_PRIVKEY` — 64 caractères hexadécimaux, dans l'environnement
   du serveur (`python/.env`, mode 600). C'est la forme à retenir pour un
   déploiement où le serveur d'abonnement est distinct des postes clients :
   la même clé signe pour tout le parc.
2. `~/.agence_financiere/licence_emetteur.json`, mode 600, créé au premier
   besoin si rien n'est configuré.

CE QUE CELA PROUVE — ET CE QUE CELA NE PROUVE PAS
-------------------------------------------------
Un jeton signé prouve qu'un émetteur détenant la clé privée a constaté
l'abonnement. Quand le serveur est distant et administré par l'éditeur, c'est
une garantie forte : le poste client ne peut pas se fabriquer de licence.

Quand l'application tourne ENTIÈREMENT sur la machine de l'utilisateur (cas du
paquet MSIX, où le backend est local), l'émetteur est cette même machine. La
signature protège alors contre une licence bricolée à la main ou recopiée d'un
autre poste, mais PAS contre quelqu'un qui contrôle la machine et sait aller
chercher la clé privée du fichier. Ce n'est pas un défaut de ce module : c'est
la limite de tout contrôle local, et elle est écrite ici plutôt que passée sous
silence. Un parc où cela compte fait tourner l'émetteur sur un serveur, avec
`AGENCE_LICENCE_PRIVKEY` renseignée là-bas et `AGENCE_LICENCE_PUBKEY` (la clé
PUBLIQUE, qui n'est pas un secret) livrée aux postes.
"""
import json
import logging
import os
import time
from typing import Dict, Optional

from licence import config as lconfig
from licence.verification import EMETTEUR_ATTENDU, PREFIXE, b64e

logger = logging.getLogger(__name__)

FICHIER = "licence_emetteur.json"


def _chemin_fichier():
    from utils import app_config
    return app_config._PATH.parent / FICHIER


def _cryptographie():
    """Le module `cryptography`, ou None. Aucune émission sans lui."""
    try:
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
        return Ed25519PrivateKey
    except ImportError:
        logger.error("[emetteur] Module « cryptography » absent : aucune "
                     "licence ne peut être signée.")
        return None


def _lire_fichier() -> Dict[str, str]:
    chemin = _chemin_fichier()
    try:
        if chemin.exists():
            donnees = json.loads(chemin.read_text())
            if isinstance(donnees, dict) and donnees.get("privee"):
                return donnees
    except Exception as e:
        logger.error("[emetteur] Clé d'émission illisible (%s) : %s", chemin, e)
    return {}


def _creer_fichier() -> Dict[str, str]:
    """Crée la paire de clés de cet émetteur, en 0600.

    Le fichier est écrit avec des permissions restreintes DÈS SA CRÉATION
    (`os.open` + mode), et non ouvert puis corrigé : entre les deux, la clé
    privée serait lisible par les autres comptes de la machine.
    """
    Ed25519PrivateKey = _cryptographie()
    if Ed25519PrivateKey is None:
        return {}
    from cryptography.hazmat.primitives import serialization

    privee = Ed25519PrivateKey.generate()
    brut_prive = privee.private_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PrivateFormat.Raw,
        encryption_algorithm=serialization.NoEncryption())
    brut_public = privee.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw)

    donnees = {"privee": brut_prive.hex(), "publique": brut_public.hex(),
               "cree_le": int(time.time())}
    chemin = _chemin_fichier()
    chemin.parent.mkdir(parents=True, exist_ok=True)
    descripteur = os.open(str(chemin), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descripteur, "w") as f:
        json.dump(donnees, f)
    logger.info("[emetteur] Paire de clés d'émission créée : %s", chemin)
    return donnees


def cle_privee(creer: bool = False) -> Optional[bytes]:
    """Clé privée de signature, ou None si aucune n'est disponible."""
    brut = (os.getenv("AGENCE_LICENCE_PRIVKEY", "") or "").strip()
    if brut:
        try:
            cle = bytes.fromhex(brut)
        except ValueError:
            logger.error("[emetteur] AGENCE_LICENCE_PRIVKEY illisible "
                         "(64 caractères hexadécimaux attendus).")
            return None
        if len(cle) != 32:
            logger.error("[emetteur] AGENCE_LICENCE_PRIVKEY de taille inattendue "
                         "(%d octets au lieu de 32).", len(cle))
            return None
        return cle

    donnees = _lire_fichier() or (_creer_fichier() if creer else {})
    if not donnees:
        return None
    try:
        return bytes.fromhex(donnees["privee"])
    except Exception:
        return None


def cle_publique_hex() -> str:
    """Clé PUBLIQUE de cet émetteur, en hexadécimal — à livrer aux postes.

    Ce n'est pas un secret : elle ne permet QUE de vérifier une signature.
    Chaîne vide si aucun émetteur n'existe sur cette machine.
    """
    brut = (os.getenv("AGENCE_LICENCE_PRIVKEY", "") or "").strip()
    if brut:
        privee = cle_privee()
        Ed25519PrivateKey = _cryptographie()
        if privee and Ed25519PrivateKey is not None:
            from cryptography.hazmat.primitives import serialization
            return Ed25519PrivateKey.from_private_bytes(privee).public_key().public_bytes(
                encoding=serialization.Encoding.Raw,
                format=serialization.PublicFormat.Raw).hex()
        return ""
    return str(_lire_fichier().get("publique", ""))


def disponible() -> bool:
    """Cette installation peut-elle signer des licences ?"""
    return _cryptographie() is not None and (
        bool((os.getenv("AGENCE_LICENCE_PRIVKEY", "") or "").strip())
        or bool(_lire_fichier()))


def emettre(sujet: str, expire_le: float, creer: bool = True) -> str:
    """Signe un jeton `AGENCE1.…` pour ce sujet, valable jusqu'à `expire_le`.

    Renvoie une chaîne VIDE si la signature est impossible — jamais un jeton
    de complaisance : un jeton non signé serait refusé à la vérification, et
    en fabriquer un donnerait l'illusion d'une activation réussie.

    `creer=False` : ne fabrique PAS de paire de clés si aucune n'existe. C'est
    ce que passe le chemin du paiement. Une paire créée toute seule sur le
    serveur signerait des licences qu'AUCUNE application installée ne saurait
    vérifier — sa clé publique n'ayant jamais été embarquée à la compilation.
    L'abonné recevrait alors une licence d'apparence valide et inutilisable.
    L'émetteur se crée donc explicitement, par `scripts/abonnement.py emetteur`.
    """
    Ed25519PrivateKey = _cryptographie()
    if Ed25519PrivateKey is None:
        return ""
    privee = cle_privee(creer=creer)
    if not privee:
        return ""

    maintenant = int(time.time())
    expiration = int(expire_le or 0)
    if expiration <= maintenant:
        logger.error("[emetteur] Échéance déjà passée : aucun jeton émis.")
        return ""

    charge = {
        "sub": str(sujet),
        "plan": "PRO",
        "iat": maintenant,
        "exp": expiration,
        "iss": EMETTEUR_ATTENDU,
    }
    # `separators` et `sort_keys` : la signature porte sur des octets précis,
    # donc l'encodage doit être reproductible d'une version de Python à l'autre.
    charge_encodee = b64e(json.dumps(charge, separators=(",", ":"),
                                     sort_keys=True).encode("utf-8"))
    signature = Ed25519PrivateKey.from_private_bytes(privee).sign(
        charge_encodee.encode("ascii"))
    return f"{PREFIXE}.{charge_encodee}.{b64e(signature)}"


def duree_max_jours(formule: str) -> float:
    """Durée maximale d'un jeton pour cette formule.

    Un jeton signé n'est pas révocable : une fois remis, il vaut jusqu'à son
    échéance, même si l'abonnement s'arrête entre-temps. On ne signe donc
    jamais plus loin que la période déjà réglée.
    """
    return float(lconfig.DUREE_DROITS_JOURS.get(formule or "", 34))
