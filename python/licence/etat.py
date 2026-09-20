"""États d'abonnement, et ce que chacun autorise.

    TRIAL            aucune licence : version d'essai, limites de licence/config
    PRO_ACTIVE       licence Pro vérifiée et non expirée : tout est débloqué
    PRO_EXPIRED      licence Pro connue mais périmée → retour aux limites d'essai
    PAYMENT_REQUIRED abonnement à régler / à réactiver → limites d'essai

SEUL `PRO_ACTIVE` débloque quoi que ce soit. Les trois autres états donnent
exactement les mêmes droits (ceux de l'essai) : ils ne diffèrent que par le
message montré à l'utilisateur. C'est délibéré — un état inconnu, illisible ou
corrompu ne doit jamais, par accident, valoir abonnement.
"""
from enum import Enum


class EtatLicence(str, Enum):
    TRIAL = "TRIAL"
    PRO_ACTIVE = "PRO_ACTIVE"
    PRO_EXPIRED = "PRO_EXPIRED"
    PAYMENT_REQUIRED = "PAYMENT_REQUIRED"

    @property
    def est_pro(self) -> bool:
        """Cet état donne-t-il les droits Pro ? Un seul le fait."""
        return self is EtatLicence.PRO_ACTIVE

    @property
    def libelle(self) -> str:
        return _LIBELLES[self]

    @property
    def message(self) -> str:
        return _MESSAGES[self]

    @classmethod
    def depuis(cls, valeur) -> "EtatLicence":
        """Convertit une valeur quelconque en état. TOUT ce qui n'est pas
        reconnu retombe sur TRIAL — jamais sur un état qui débloquerait."""
        if isinstance(valeur, cls):
            return valeur
        try:
            return cls(str(valeur).strip().upper())
        except (ValueError, AttributeError):
            return cls.TRIAL


_LIBELLES = {
    EtatLicence.TRIAL:            "Version d'essai",
    EtatLicence.PRO_ACTIVE:       "Version Pro",
    EtatLicence.PRO_EXPIRED:      "Abonnement Pro expiré",
    EtatLicence.PAYMENT_REQUIRED: "Paiement requis",
}

_MESSAGES = {
    EtatLicence.TRIAL:
        "Version d'essai — fonctionnalités volontairement limitées. "
        "L'abonnement Pro débloque l'intégralité des agents IA et des "
        "fonctionnalités avancées.",
    EtatLicence.PRO_ACTIVE:
        "Abonnement Pro actif — toutes les fonctionnalités sont débloquées.",
    EtatLicence.PRO_EXPIRED:
        "Votre abonnement Pro a expiré. L'application est repassée en version "
        "d'essai ; renouvelez l'abonnement pour retrouver l'accès complet.",
    EtatLicence.PAYMENT_REQUIRED:
        "Un paiement est requis pour activer ou réactiver l'abonnement Pro. "
        "En attendant, l'application fonctionne en version d'essai.",
}
