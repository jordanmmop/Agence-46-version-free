"""Vérification LOCALE des jetons de licence signés par Novia.

FORMAT : JWT compact signé en EdDSA (Ed25519), RFC 7519 + RFC 8037
-----------------------------------------------------------------
    base64url(en-tête) . base64url(charge utile) . base64url(signature)

Un JWT plutôt qu'un format maison : le site Novia peut le produire avec la
bibliothèque standard de n'importe quel langage (PHP/libsodium, Node/crypto,
Python/cryptography), sans réimplémenter un encodage propriétaire. Le format
exact attendu est documenté dans docs/novia-licensing-integration.md.

POURQUOI UNE VÉRIFICATION ÉCRITE ICI, ET NON UNE BIBLIOTHÈQUE JWT
-----------------------------------------------------------------
Les failles classiques des JWT viennent de bibliothèques trop accommodantes :
`alg: none` accepté, algorithme choisi par le jeton lui-même, clé HMAC
confondue avec une clé publique. Ce module n'accepte qu'UN algorithme, fixé
ici et non lu dans le jeton, avec des clés Ed25519 connues d'avance. Il tient
en quelques dizaines de lignes lisibles, sans dépendance nouvelle
(`cryptography` est déjà requis par l'application).

ÉCHEC FERMÉ
-----------
Toute anomalie — format, algorithme, clé inconnue, signature, produit,
installation, dates — lève `JetonRefuse`. Aucune branche ne renvoie « valide »
par défaut.
"""
import base64
import json
import logging
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

ALGORITHME = "EdDSA"
EMETTEUR = "novia"


class JetonRefuse(Exception):
    """Jeton inutilisable. `raison` alimente le message affiché."""

    def __init__(self, message: str, raison: str = "signature_invalide"):
        super().__init__(message)
        self.raison = raison


def _b64d(texte: str) -> bytes:
    if not isinstance(texte, str) or not texte:
        raise ValueError("segment vide")
    return base64.urlsafe_b64decode(texte + "=" * (-len(texte) % 4))


def b64e(donnees: bytes) -> str:
    return base64.urlsafe_b64encode(donnees).decode("ascii").rstrip("=")


def _cle_publique(brute: str):
    """Clé Ed25519 à partir de 64 caractères hexadécimaux ou de base64url."""
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    brute = (brute or "").strip()
    try:
        octets = bytes.fromhex(brute)
    except ValueError:
        octets = _b64d(brute)
    if len(octets) != 32:
        raise ValueError(f"clé publique de {len(octets)} octets (32 attendus)")
    return Ed25519PublicKey.from_public_bytes(octets)


def lire_sans_verifier(jeton: str) -> Dict[str, Any]:
    """Charge utile SANS vérification — pour l'affichage uniquement.

    Ne jamais en tirer un droit. Sert à montrer à l'utilisateur pourquoi un
    jeton est refusé (« émis pour un autre appareil »), jamais à l'accepter.
    """
    try:
        return json.loads(_b64d(jeton.split(".")[1]).decode("utf-8"))
    except Exception:
        return {}


def verifier(jeton: str, cles_publiques: Dict[str, str], produit: str,
             installation_id: str, maintenant: float,
             version_app: str = "", tolerance_s: int = 300) -> Dict[str, Any]:
    """Vérifie un jeton et renvoie ses revendications. Lève `JetonRefuse`.

    `maintenant` est fourni par l'appelant — et non lu ici — parce que c'est
    l'appelant qui sait corriger une horloge reculée (voir LicenseManager).
    """
    if not isinstance(jeton, str) or jeton.count(".") != 2:
        raise JetonRefuse("Jeton de licence mal formé.")
    en_tete_b64, charge_b64, signature_b64 = jeton.split(".")

    try:
        en_tete = json.loads(_b64d(en_tete_b64).decode("utf-8"))
        charge = json.loads(_b64d(charge_b64).decode("utf-8"))
        signature = _b64d(signature_b64)
    except Exception:
        raise JetonRefuse("Jeton de licence illisible.")
    if not isinstance(en_tete, dict) or not isinstance(charge, dict):
        raise JetonRefuse("Jeton de licence illisible.")

    # L'algorithme est IMPOSÉ, pas négocié : un jeton qui annonce « none » ou
    # « HS256 » est refusé avant même de chercher une clé. C'est la faille JWT
    # la plus répandue, et elle se ferme en une ligne.
    if en_tete.get("alg") != ALGORITHME:
        raise JetonRefuse(f"Algorithme de signature refusé : {en_tete.get('alg')!r}.")

    if not cles_publiques:
        raise JetonRefuse("Aucune clé publique Novia n'est configurée.",
                          "non_configure")

    # Avec un `kid`, on essaie CETTE clé ; sans, toutes les clés connues. Un
    # `kid` inconnu n'est pas une erreur fatale en soi (rotation en cours) :
    # on retombe sur l'ensemble, et c'est la signature qui tranche.
    kid = en_tete.get("kid")
    candidates = ([cles_publiques[kid]] if isinstance(kid, str) and kid in cles_publiques
                  else list(cles_publiques.values()))

    message = f"{en_tete_b64}.{charge_b64}".encode("ascii")
    from cryptography.exceptions import InvalidSignature
    signee = False
    for brute in candidates:
        try:
            _cle_publique(brute).verify(signature, message)
            signee = True
            break
        except InvalidSignature:
            continue
        except Exception as e:                      # clé mal saisie dans la config
            logger.error("[novia] Clé publique inutilisable : %s", e)
            continue
    if not signee:
        raise JetonRefuse("La signature de la licence est invalide.")

    # ── À partir d'ici, la charge utile est AUTHENTIQUE. On la contrôle. ──
    if charge.get("iss") != EMETTEUR:
        raise JetonRefuse("Licence émise par un autre émetteur.")
    if charge.get("aud") != produit:
        raise JetonRefuse("Cette licence n'est pas une licence de ce produit.",
                          "autre_produit")
    # Liée à l'appareil : un jeton recopié sur un autre poste n'y vaut rien.
    if charge.get("inst") != installation_id:
        raise JetonRefuse("Cette licence a été activée sur un autre appareil.",
                          "autre_installation")

    for champ in ("iat", "offline_until"):
        if not isinstance(charge.get(champ), (int, float)):
            raise JetonRefuse(f"Licence sans champ « {champ} » valide.")
    if charge.get("exp") is not None and not isinstance(charge["exp"], (int, float)):
        raise JetonRefuse("Licence avec une échéance illisible.")

    # Un jeton émis dans le futur trahit une horloge serveur déréglée ou un
    # jeton fabriqué : dans les deux cas, on ne s'y fie pas.
    if charge["iat"] > maintenant + tolerance_s:
        raise JetonRefuse("Licence datée dans le futur.")

    if version_app and not _version_couverte(version_app, charge.get("min_version"),
                                             charge.get("max_version")):
        raise JetonRefuse("Votre licence ne couvre pas cette version.", "version")

    return charge


def _version_couverte(version: str, minimum: Optional[str],
                      maximum: Optional[str]) -> bool:
    def t(v):
        try:
            return tuple(int(x) for x in str(v).split("-")[0].split(".")[:3])
        except ValueError:
            return None
    courante = t(version)
    if courante is None:
        return True                     # version de développement : pas de blocage
    if minimum and t(minimum) and courante < t(minimum):
        return False
    if maximum and t(maximum) and courante > t(maximum):
        return False
    return True
