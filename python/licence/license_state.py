"""États d'une licence Agence Novia, et niveaux d'offre.

Ce module ne fait RIEN : il nomme. Toute la logique vit dans
`licence/license_manager.py`, qui est le seul à produire ces états.

    NOT_ACTIVATED   aucune licence sur cette installation
    ACTIVE          licence valide, revalidée en ligne récemment
    OFFLINE_VALID   licence valide, pas revalidée depuis plus de
                    LICENSE_CHECK_INTERVAL, mais encore dans sa période
                    hors ligne : AUCUNE connexion n'est exigée
    EXPIRED         licence arrivée à échéance — ou période hors ligne épuisée
                    (voir `Raison` pour distinguer les deux)
    SUSPENDED       suspendue par Novia (impayé, litige en cours…)
    REVOKED         révoquée par Novia
    INVALID         clé inconnue, jeton illisible, signature fausse, jeton
                    d'une autre installation ou d'un autre produit

Aucun de ces états ne supprime quoi que ce soit : ils décident de ce qui est
ACCESSIBLE, jamais de ce qui est CONSERVÉ. Projets et données locales restent
intacts dans tous les cas.
"""
from enum import Enum


class LicenseState(str, Enum):
    NOT_ACTIVATED = "NOT_ACTIVATED"
    ACTIVE = "ACTIVE"
    OFFLINE_VALID = "OFFLINE_VALID"
    EXPIRED = "EXPIRED"
    SUSPENDED = "SUSPENDED"
    REVOKED = "REVOKED"
    INVALID = "INVALID"

    @property
    def valide(self) -> bool:
        """La licence ouvre-t-elle les droits de son niveau ?"""
        return self in (LicenseState.ACTIVE, LicenseState.OFFLINE_VALID)

    @property
    def activee(self) -> bool:
        """Une activation est-elle enregistrée sur cette installation ?

        Vrai même pour une licence expirée ou révoquée : l'utilisateur a
        quelque chose à renouveler ou à contester, pas à découvrir.
        """
        return self not in (LicenseState.NOT_ACTIVATED, LicenseState.INVALID)

    @classmethod
    def depuis(cls, valeur) -> "LicenseState":
        """Toute valeur non reconnue vaut INVALID — jamais un droit."""
        if isinstance(valeur, cls):
            return valeur
        try:
            return cls(str(valeur).strip().upper())
        except (ValueError, AttributeError):
            return cls.INVALID


class LicenseTier(str, Enum):
    """Niveau d'offre porté par la licence.

    Les niveaux reconnus viennent de `licence/config.py` (NIVEAUX_LICENCE) :
    en ajouter un là suffit, ce module n'en tient pas la liste.
    """
    FREE = "FREE"
    PRO = "PRO"
    BUSINESS = "BUSINESS"
    ENTERPRISE = "ENTERPRISE"

    @property
    def premium(self) -> bool:
        from licence import config as lconfig
        return self.value in lconfig.NIVEAUX_PREMIUM

    @classmethod
    def depuis(cls, valeur) -> "LicenseTier":
        """Un niveau inconnu vaut FREE : le moins ouvert, jamais le plus."""
        try:
            return cls(str(valeur).strip().upper())
        except (ValueError, AttributeError):
            return cls.FREE


class Raison(str, Enum):
    """Pourquoi l'état est ce qu'il est — pour un message juste à l'écran."""
    AUCUNE = ""
    LICENCE_ECHUE = "licence_echue"
    HORS_LIGNE_EPUISE = "hors_ligne_epuise"
    SIGNATURE = "signature_invalide"
    AUTRE_INSTALLATION = "autre_installation"
    AUTRE_PRODUIT = "autre_produit"
    VERSION = "version_incompatible"
    CLE_INCONNUE = "cle_inconnue"
    APPAREILS = "trop_d_appareils"
    NON_CONFIGURE = "non_configure"


MESSAGES = {
    LicenseState.NOT_ACTIVATED:
        "Activez votre licence Agence 46 pour utiliser l'application.",
    LicenseState.ACTIVE: "Licence active.",
    LicenseState.OFFLINE_VALID:
        "Licence valide hors ligne. Elle sera revérifiée dès qu'une connexion "
        "sera disponible.",
    LicenseState.EXPIRED: "Votre licence a expiré.",
    LicenseState.SUSPENDED:
        "Votre licence Agence 46 est suspendue. Veuillez contacter le support.",
    LicenseState.REVOKED:
        "Votre licence Agence 46 a été révoquée. Veuillez contacter le support.",
    LicenseState.INVALID: "Cette clé de licence est invalide.",
}

MESSAGES_RAISON = {
    Raison.HORS_LIGNE_EPUISE:
        "Votre licence n'a pas pu être revérifiée depuis trop longtemps. "
        "Connectez-vous à Internet pour la revalider.",
    Raison.SIGNATURE:
        "La licence enregistrée n'est pas authentique. Réactivez votre clé.",
    Raison.AUTRE_INSTALLATION:
        "Cette licence a été activée sur un autre appareil. Réactivez votre "
        "clé sur celui-ci.",
    Raison.AUTRE_PRODUIT: "Cette licence n'est pas une licence Agence 46.",
    Raison.VERSION:
        "Votre licence ne couvre pas cette version d'Agence 46.",
    Raison.CLE_INCONNUE: "Cette clé de licence est invalide.",
    Raison.APPAREILS:
        "Le nombre maximal d'appareils autorisés est atteint.",
    Raison.NON_CONFIGURE:
        "Le système de licences n'est pas configuré dans cette version.",
}
