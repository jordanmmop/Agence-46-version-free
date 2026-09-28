"""Client HTTPS de l'API de licences Novia — le SEUL accès réseau du système.

CE QUI PART DE LA MACHINE
-------------------------
Exactement ceci, et rien d'autre :

    license_key       la clé saisie par l'utilisateur
    installation_id   UUID aléatoire tiré à l'installation (aucune donnée
                      matérielle, aucune donnée personnelle)
    product           « agence46 » — pour qu'un serveur Novia puisse servir
                      plusieurs applications
    app_version       la version d'Agence 46
    platform          « windows », « macos » ou « linux »

Ni fichier, ni projet, ni prompt, ni conversation, ni modèle, ni donnée
Ollama, ni nom d'utilisateur, ni nom de machine. Les corps de requête sont
construits ICI, en un seul endroit, à partir de ces champs nommés : aucun
appelant ne peut y glisser autre chose. Un test le vérifie champ par champ.

TLS
---
HTTPS obligatoire, certificats vérifiés par la bibliothèque standard. Une
erreur de certificat n'est JAMAIS contournée : elle est signalée comme telle,
et non confondue avec une absence de réseau — c'est peut-être une attaque.

RÉPONSES
--------
Validées strictement : type, statut connu, produit attendu, jeton présent
quand la licence est déclarée valide. Mais la réponse JSON reste INDICATIVE :
c'est le jeton signé, vérifié ensuite par le LicenseManager, qui fait foi.
"""
import logging
import sys
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Optional, Tuple

logger = logging.getLogger(__name__)

TIMEOUT_S = 15

STATUTS = ("ACTIVE", "EXPIRED", "SUSPENDED", "REVOKED", "INVALID")

# Codes d'erreur documentés pour le développeur Novia.
CODES_ERREUR = (
    "INVALID_KEY", "LICENSE_EXPIRED", "LICENSE_SUSPENDED", "LICENSE_REVOKED",
    "DEVICE_LIMIT_REACHED", "PRODUCT_MISMATCH", "INSTALLATION_NOT_FOUND",
    "INVALID_REQUEST", "RATE_LIMITED", "SERVER_ERROR",
)


class ErreurNovia(Exception):
    """Échange impossible ou inexploitable. Jamais une décision de licence."""
    type_erreur = "reseau"


class PasDeConnexion(ErreurNovia):
    type_erreur = "reseau"


class ServeurIndisponible(ErreurNovia):
    type_erreur = "serveur"


class CertificatRefuse(ErreurNovia):
    type_erreur = "tls"


class ReponseInvalide(ErreurNovia):
    type_erreur = "reponse"


class ConfigurationInvalide(ErreurNovia):
    type_erreur = "configuration"


@dataclass
class ReponseNovia:
    """Réponse validée du serveur. `statut` ∈ STATUTS."""
    valide: bool
    statut: str
    jeton: str = ""
    code_erreur: str = ""
    message: str = ""
    heure_serveur: Optional[float] = None
    brut: Dict[str, Any] = field(default_factory=dict)


def plateforme() -> str:
    if sys.platform.startswith("win"):
        return "windows"
    if sys.platform == "darwin":
        return "macos"
    return "linux"


Transport = Callable[[str, Dict[str, Any], float], Tuple[int, Any]]


def _transport_requests(url: str, corps: Dict[str, Any], timeout: float) -> Tuple[int, Any]:
    """POST JSON réel. `verify` n'est JAMAIS passé : la vérification des
    certificats reste celle, stricte, de la bibliothèque."""
    import requests
    try:
        reponse = requests.post(url, json=corps, timeout=timeout,
                                headers={"Accept": "application/json",
                                         "User-Agent": "Agence46-Licence/1"})
    except requests.exceptions.SSLError as e:
        raise CertificatRefuse(
            "Connexion sécurisée au serveur de licences impossible : son "
            "certificat n'a pas pu être vérifié.") from e
    except (requests.exceptions.ConnectionError,
            requests.exceptions.Timeout) as e:
        raise PasDeConnexion("Impossible de contacter le serveur de licences.") from e
    except requests.exceptions.RequestException as e:
        raise PasDeConnexion(f"Échange avec le serveur de licences impossible : {e}") from e
    try:
        return reponse.status_code, reponse.json()
    except ValueError:
        return reponse.status_code, None


class NoviaClient:
    """Les trois appels de l'API Novia. Aucun état : tout est dans le manager."""

    def __init__(self, url_api: str, produit: str,
                 transport: Optional[Transport] = None, timeout: float = TIMEOUT_S):
        self.url_api = (url_api or "").rstrip("/")
        self.produit = produit
        self.timeout = timeout
        self._transport = transport or _transport_requests

    # ── Construction des corps : le SEUL endroit où ils naissent ──
    def corps_activation(self, cle: str, installation_id: str,
                         version: str) -> Dict[str, Any]:
        return {"license_key": cle, "installation_id": installation_id,
                "product": self.produit, "app_version": version,
                "platform": plateforme()}

    def corps_desactivation(self, cle: str, installation_id: str) -> Dict[str, Any]:
        return {"license_key": cle, "installation_id": installation_id,
                "product": self.produit}

    # ── Appels ──
    def activer(self, cle: str, installation_id: str, version: str) -> ReponseNovia:
        return self._appeler("/api/license/activate",
                             self.corps_activation(cle, installation_id, version))

    def valider(self, cle: str, installation_id: str, version: str) -> ReponseNovia:
        return self._appeler("/api/license/validate",
                             self.corps_activation(cle, installation_id, version))

    def desactiver(self, cle: str, installation_id: str) -> ReponseNovia:
        return self._appeler("/api/license/deactivate",
                             self.corps_desactivation(cle, installation_id),
                             jeton_requis=False)

    def _appeler(self, chemin: str, corps: Dict[str, Any],
                 jeton_requis: bool = True) -> ReponseNovia:
        if not self.url_api:
            raise ConfigurationInvalide("Aucune URL de serveur de licences configurée.")
        if not self.url_api.startswith("https://"):
            # Jamais de repli en HTTP : la clé de licence y circulerait en clair.
            raise ConfigurationInvalide(
                "L'URL du serveur de licences doit être en HTTPS.")

        statut_http, donnees = self._transport(f"{self.url_api}{chemin}", corps,
                                               self.timeout)
        if statut_http == 429:
            raise ServeurIndisponible(
                "Le serveur de licences est temporairement indisponible "
                "(trop de requêtes). Réessayez dans quelques minutes.")
        if statut_http >= 500:
            raise ServeurIndisponible(
                "Le serveur de licences est temporairement indisponible.")
        return self._valider_reponse(donnees, jeton_requis)

    def _valider_reponse(self, donnees: Any, jeton_requis: bool) -> ReponseNovia:
        """Contrôle strict de la forme. Aucune valeur n'est prise sur parole."""
        if not isinstance(donnees, dict):
            raise ReponseInvalide("Réponse du serveur de licences illisible.")

        valide = donnees.get("valid")
        if not isinstance(valide, bool):
            raise ReponseInvalide("Réponse sans champ « valid » booléen.")

        statut = donnees.get("license_status")
        if statut is None and not valide:
            statut = "INVALID"
        if statut not in STATUTS:
            raise ReponseInvalide(f"Statut de licence inconnu : {statut!r}.")

        produit = donnees.get("product")
        if produit is not None and produit != self.produit:
            raise ReponseInvalide("Réponse concernant un autre produit.")

        # Une réponse qui se dit valide sans jeton ne prouve rien : un simple
        # proxy qui répondrait {"valid": true} ouvrirait sinon l'application.
        jeton = donnees.get("signed_token") or ""
        if valide and jeton_requis and (not isinstance(jeton, str) or not jeton):
            raise ReponseInvalide("Réponse valide sans jeton signé : refusée.")
        if valide and statut != "ACTIVE":
            raise ReponseInvalide("Réponse incohérente : valide mais non active.")

        code = donnees.get("error_code") or ""
        if code and code not in CODES_ERREUR:
            code = "SERVER_ERROR"

        heure = donnees.get("server_time")
        if not isinstance(heure, (int, float)):
            heure = None

        message = donnees.get("message")
        return ReponseNovia(valide=valide, statut=statut,
                            jeton=jeton if isinstance(jeton, str) else "",
                            code_erreur=code,
                            message=message if isinstance(message, str) else "",
                            heure_serveur=heure, brut=donnees)
