"""États d'un compte et de son abonnement, et ce que chacun autorise.

    COMPTE_REQUIS    personne n'est connecté : l'application est inutilisable
    TRIAL            essai en cours (3 jours), limites de licence/config
    TRIAL_EXPIRED    les 3 jours sont écoulés sans paiement → compte SUSPENDU
    PRO_ACTIVE       abonnement payé et vérifié : tout est débloqué
    PRO_EXPIRED      abonnement échu ou non renouvelé → compte SUSPENDU
    PAYMENT_REQUIRED paiement attendu ou refusé → compte SUSPENDU
    SUSPENDU         compte suspendu par l'éditeur (abus, impayé constaté)

TROIS NIVEAUX DE DROITS, ET TROIS SEULEMENT

    `est_pro`      un seul état : PRO_ACTIVE. Lui seul débloque quoi que ce soit.
    `utilisable`   PRO_ACTIVE et TRIAL. Partout ailleurs, l'application est
                   fermée : plus d'analyse, plus d'agents, plus de trading.
    le reste       écran d'inscription/connexion ou écran de paiement.

C'est délibérément asymétrique : un état inconnu, illisible ou corrompu
retombe sur COMPTE_REQUIS — ni Pro, ni même utilisable. Une erreur ne doit
jamais ouvrir une porte.
"""
from enum import Enum


class EtatLicence(str, Enum):
    COMPTE_REQUIS = "COMPTE_REQUIS"
    TRIAL = "TRIAL"
    TRIAL_EXPIRED = "TRIAL_EXPIRED"
    PRO_ACTIVE = "PRO_ACTIVE"
    PRO_EXPIRED = "PRO_EXPIRED"
    PAYMENT_REQUIRED = "PAYMENT_REQUIRED"
    SUSPENDU = "SUSPENDU"

    @property
    def est_pro(self) -> bool:
        """Cet état donne-t-il les droits Pro ? Un seul le fait."""
        return self is EtatLicence.PRO_ACTIVE

    @property
    def utilisable(self) -> bool:
        """L'application est-elle utilisable du tout ?

        Seuls l'abonné et l'essai EN COURS le sont. Tous les autres états —
        essai écoulé, abonnement échu, paiement attendu, compte suspendu —
        ferment l'application jusqu'au paiement.
        """
        return self in (EtatLicence.PRO_ACTIVE, EtatLicence.TRIAL)

    @property
    def compte_suspendu(self) -> bool:
        """Compte existant mais fermé faute de paiement."""
        return self in (EtatLicence.TRIAL_EXPIRED, EtatLicence.PRO_EXPIRED,
                        EtatLicence.PAYMENT_REQUIRED, EtatLicence.SUSPENDU)

    @property
    def libelle(self) -> str:
        return _LIBELLES[self]

    @property
    def message(self) -> str:
        return _MESSAGES[self]

    @classmethod
    def depuis(cls, valeur) -> "EtatLicence":
        """Convertit une valeur quelconque en état.

        TOUT ce qui n'est pas reconnu retombe sur COMPTE_REQUIS — l'état le
        plus fermé. Une valeur corrompue en base, une réponse tronquée ou une
        entrée hostile ne doivent jamais valoir un droit.
        """
        if isinstance(valeur, cls):
            return valeur
        try:
            return cls(str(valeur).strip().upper())
        except (ValueError, AttributeError):
            return cls.COMPTE_REQUIS


_LIBELLES = {
    EtatLicence.COMPTE_REQUIS:    "Compte requis",
    EtatLicence.TRIAL:            "Essai gratuit",
    EtatLicence.TRIAL_EXPIRED:    "Essai terminé",
    EtatLicence.PRO_ACTIVE:       "Abonnement Pro actif",
    EtatLicence.PRO_EXPIRED:      "Abonnement expiré",
    EtatLicence.PAYMENT_REQUIRED: "Paiement requis",
    EtatLicence.SUSPENDU:         "Compte suspendu",
}

_MESSAGES = {
    EtatLicence.COMPTE_REQUIS:
        "Créez un compte ou connectez-vous pour utiliser l'application. "
        "L'inscription ouvre un essai gratuit de 3 jours.",
    EtatLicence.TRIAL:
        "Essai gratuit en cours — fonctionnalités volontairement limitées. "
        "L'abonnement Pro débloque l'intégralité des agents IA et des "
        "fonctionnalités avancées.",
    EtatLicence.TRIAL_EXPIRED:
        "Votre essai gratuit de 3 jours est terminé et votre compte est "
        "suspendu. Souscrivez un abonnement Pro pour retrouver l'accès à "
        "l'application.",
    EtatLicence.PRO_ACTIVE:
        "Abonnement Pro actif — toutes les fonctionnalités sont débloquées.",
    EtatLicence.PRO_EXPIRED:
        "Votre abonnement Pro a expiré et votre compte est suspendu. "
        "Renouvelez-le pour retrouver l'accès complet.",
    EtatLicence.PAYMENT_REQUIRED:
        "Un paiement est requis pour activer ou réactiver votre abonnement "
        "Pro. L'application reste fermée tant qu'il n'est pas confirmé.",
    EtatLicence.SUSPENDU:
        "Votre compte est suspendu. Contactez le support ou régularisez "
        "votre abonnement pour le réactiver.",
}
