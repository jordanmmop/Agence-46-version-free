"""SOURCE UNIQUE des limites de la version d'essai et du périmètre Pro.

Tout ce qui distingue l'essai de l'abonnement se règle ICI, dans ce seul
fichier. Aucun autre module ne doit coder « 6 agents » ou « 20 requêtes » en
dur : les restrictions passent toutes par `licence.gate`, qui lit ces
constantes. Changer une valeur ci-dessous change le comportement de toute
l'application, sans aucune autre modification.

POURQUOI PAS DE VARIABLE D'ENVIRONNEMENT ICI
--------------------------------------------
Le reste du projet lit volontiers ses réglages dans `.env` — mais pas ces
constantes-là. Une limite d'essai que l'utilisateur peut relever en écrivant
`TRIAL_MAX_AGENTS=45` dans un fichier texte n'est pas une limite : ce serait
livrer le contournement avec le produit. Ces valeurs sont donc figées dans le
code, et le statut Pro ne s'obtient que par une licence VÉRIFIÉE
(cf. `licence/verification.py`).
"""

# ═══════════════════════════ AGENTS IA ════════════════════════════════════

# Version d'essai : nombre d'agents réellement exploitables. Les autres
# restent VISIBLES dans l'interface (découverte de l'offre Pro) mais ne
# participent plus aux cycles d'analyse.
TRIAL_MAX_AGENTS = 6

# Version Pro : TOUS les agents du projet, sans plafond.
#
# ATTENTION — écart assumé avec le cahier des charges : celui-ci parle de
# « 36 agents ». Le projet en compte 45 (cf. `agents/__init__.py`, qui
# l'affirme par assertion, plus le Chef d'Orchestre = 46 au total). Fixer
# PRO_MAX_AGENTS = 36 VERROUILLERAIT 9 agents aux abonnés payants — l'inverse
# de ce qui est demandé. `None` signifie donc « aucun plafond » : Pro donne
# accès à l'intégralité des agents, quel que soit leur nombre aujourd'hui et
# demain. Pour plafonner malgré tout, remplacer None par un entier.
PRO_MAX_AGENTS = None

# Les 6 agents déverrouillés en essai : UN PAR FAMILLE — analyse de marché,
# stratégies, risques, exécution, data, reporting. Un choix explicite plutôt
# que « les 6 premiers de la liste » : l'essai doit montrer l'ÉVENTAIL de
# l'application, pas six variantes de la même famille. Un identifiant inconnu
# est ignoré et le quota est complété dans l'ordre de la liste globale, pour
# qu'un agent renommé ne réduise jamais l'essai à moins de TRIAL_MAX_AGENTS.
TRIAL_AGENTS_IDS = (
    "AT-001",   # Analyste Technique        (analyse de marché)
    "DT-012",   # Trader Intraday           (stratégies)
    "RM-021",   # Gestionnaire des Risques  (gestion des risques)
    "EX-029",   # Agent d'Exécution         (exécution & opérations)
    "DC-036",   # Collecteur de Données     (data & intelligence)
    "PL-043",   # Tracker P&L               (reporting & communication)
)

# ═══════════════════════════ QUOTAS DE REQUÊTES ═══════════════════════════

# Une « requête IA » = UN cycle d'analyse demandé par l'utilisateur, c'est-à-dire
# un appel à /api/analyser (quel que soit le nombre d'agents mobilisés).
# C'est l'unité que l'interface affiche : « 18 / 20 requêtes utilisées ».
TRIAL_DAILY_REQUEST_LIMIT = 20
TRIAL_HOURLY_REQUEST_LIMIT = 5

# Pro : aucune limite applicative. Les seules limites restantes sont
# techniques (vitesse du moteur IA local, disponibilité des cours).
PRO_DAILY_REQUEST_LIMIT = None
PRO_HOURLY_REQUEST_LIMIT = None

# Version d'essai : nombre de symboles analysables dans un même cycle.
#
# C'est la traduction CONCRÈTE de la ligne « exécution simultanée de plusieurs
# agents / multi-agents » du tableau des offres : dans ce projet, le
# parallélisme utilisateur est le nombre de symboles qu'un cycle traite d'un
# coup (le Chef d'Orchestre lance les agents en parallèle sur chacun). Pro n'a
# pas de plafond ici — seulement celui, technique, de `normaliser_symboles`.
TRIAL_MAX_SYMBOLES_PAR_ANALYSE = 1

# ═══════════════════════════ FONCTIONNALITÉS ══════════════════════════════

# Libellés AFFICHÉS à l'utilisateur (interface + message de verrouillage).
FEATURES = {
    "agents_complets":        "Accès aux 46 agents IA (45 spécialistes + Chef d'Orchestre)",
    "multi_agents":           "Exécution simultanée des agents sur plusieurs symboles",
    "workflows_avances":      "Workflows avancés : backtest, pré-vol, rapports",
    "automatisations":        "Trading automatique et automatisations",
    "taches_arriere_plan":    "Tâches longues et analyses en arrière-plan",
    "parametres_avances":     "Paramètres avancés des agents (levier, risque, plafonds)",
    "administration_avancee": "Administration avancée (multi-comptes, maintenance)",
    "requetes_illimitees":    "Requêtes IA sans limite journalière ni horaire",
    # ── Socle commun, disponible dès l'essai ──
    "analyse_manuelle":       "Analyse à la demande",
    "consultation":           "Consultation du portefeuille, des signaux et des rapports",
    "connexion_courtier":     "Connexion à un compte MetaTrader 5",
}

# Réservé à l'abonnement Pro.
PRO_FEATURES = frozenset({
    "agents_complets",
    "multi_agents",
    "workflows_avances",
    "automatisations",
    "taches_arriere_plan",
    "parametres_avances",
    "administration_avancee",
    "requetes_illimitees",
})

# Disponible en version d'essai (dans la limite des quotas ci-dessus).
TRIAL_FEATURES = frozenset({
    "analyse_manuelle",
    "consultation",
    "connexion_courtier",
})

# Garde-fou : toute fonctionnalité déclarée doit appartenir à exactement une
# des deux offres. Sans lui, ajouter une clé à FEATURES en oubliant de la
# classer la rendrait silencieusement... interdite aux deux.
assert TRIAL_FEATURES.isdisjoint(PRO_FEATURES), "Une fonctionnalité ne peut pas être à la fois Essai et Pro"
assert (TRIAL_FEATURES | PRO_FEATURES) == set(FEATURES), "FEATURES et les offres ont divergé"

# ═══════════════════════════ COMPTE UTILISATEUR ═══════════════════════════

# L'application exige un COMPTE. Sans inscription puis connexion, rien n'est
# utilisable : c'est le compte qui porte l'essai, l'abonnement et les quotas.
COMPTE_OBLIGATOIRE = True

# Durée de l'essai gratuit, à compter de la CRÉATION du compte. Passé ce
# délai sans abonnement, le compte est SUSPENDU et l'application inutilisable.
TRIAL_DUREE_JOURS = 3

# Durée d'une session ouverte (jours). Au-delà, il faut se reconnecter.
SESSION_DUREE_JOURS = 30

# Champs EXIGÉS à l'inscription. La carte bancaire n'y figure pas
# VOLONTAIREMENT : elle est saisie chez Stripe, sur ses pages, jamais ici.
# Voir la note « CARTE BANCAIRE » plus bas.
CHAMPS_INSCRIPTION = ("email", "mot_de_passe", "telephone", "adresse",
                      "code_postal", "ville", "pays")

# Longueur minimale du mot de passe.
MOT_DE_PASSE_MIN = 8

# ═══════════════════════════ FORMULES D'ABONNEMENT ════════════════════════
#
# CARTE BANCAIRE — CE QUE L'APPLICATION NE FAIT PAS, ET POURQUOI
# --------------------------------------------------------------
# L'application ne demande, ne transporte et n'enregistre AUCUN numéro de
# carte, date d'expiration ou cryptogramme. Jamais.
#
# Ce n'est pas un raccourci : détenir ces données impose la conformité PCI-DSS
# (audit, cloisonnement réseau, chiffrement, journalisation), engage la
# responsabilité de l'éditeur en cas de fuite, et n'apporte rien — Stripe le
# fait déjà, mieux, sur ses propres pages.
#
# Le paiement se fait donc INTÉGRALEMENT sur les pages Stripe ci-dessous :
# l'utilisateur y saisit sa carte, Stripe encaisse, et l'application n'apprend
# que le RÉSULTAT (payé / non payé) — jamais le moyen de paiement.

# ── Liens de paiement Stripe ──────────────────────────────────────────────
#
# Un lien de paiement n'est PAS un secret : c'est une URL publique, faite pour
# être partagée. Elle a donc sa place ici, et peut aussi être remplacée sans
# recompiler, par variable d'environnement :
#
#     STRIPE_LIEN_MENSUEL=https://buy.stripe.com/VOTRE_LIEN
#     STRIPE_LIEN_ANNUEL=https://buy.stripe.com/VOTRE_LIEN
#
# C'est ce qui permet de passer en production sur un serveur déjà installé —
# et de revenir en test — sans reconstruire l'application.
#
# LIENS DE PRODUCTION — ils encaissent des paiements RÉELS.
#
# Vérifiés en ouvrant chaque page : le lien mensuel facture bien 78,79 € par
# mois et l'annuel 849,99 € par an (les pages affichent ces montants convertis
# dans la devise du visiteur). Un mappage inversé ferait payer 849,99 € pour un
# mois : c'est pourquoi la correspondance a été contrôlée sur les pages
# elles-mêmes, et non seulement recopiée.
#
# ⚠️ Les deux liens accordent un ESSAI DE 3 JOURS côté Stripe (« 3 days free »).
# Conséquence à connaître : le premier événement reçu à la souscription porte un
# montant de ZÉRO, puisque rien n'est encore encaissé. La formule ne peut donc
# pas être déduite du seul montant — voir `_formule_depuis_evenement` dans
# licence/stripe_paiement.py.

LIEN_PAIEMENT_MENSUEL_DEFAUT = "https://buy.stripe.com/00w00l1Qn1hI8x4cgI4wM03"
LIEN_PAIEMENT_ANNUEL_DEFAUT = "https://buy.stripe.com/7sY9AVamT0dE00yeoQ4wM04"

# Préfixe exigé pour tout lien de paiement. Un lien mal collé (page du tableau
# de bord, lien raccourci, adresse d'hameçonnage glissée dans une variable
# d'environnement) est REFUSÉ et l'on retombe sur le défaut : l'application ne
# doit jamais envoyer un client payer ailleurs que chez Stripe.
PREFIXE_LIEN_STRIPE = "https://buy.stripe.com/"


def _lien_paiement(variable: str, defaut: str) -> str:
    """Lien de paiement retenu : la variable d'environnement, sinon le défaut."""
    import logging
    import os
    brut = (os.getenv(variable, "") or "").strip()
    if not brut:
        return defaut
    if not brut.startswith(PREFIXE_LIEN_STRIPE):
        logging.getLogger(__name__).error(
            "[abonnement] %s ignorée : un lien de paiement doit commencer par "
            "« %s » (reçu : %.40s…)", variable, PREFIXE_LIEN_STRIPE, brut)
        return defaut
    return brut


def lien_de_test(lien: str) -> bool:
    """Ce lien pointe-t-il vers l'environnement de test de Stripe ?"""
    return "/test_" in (lien or "")


FORMULES = {
    "mensuel": {
        "id": "mensuel",
        "libelle": "Pro — Mensuel",
        "prix": 78.79,
        "devise": "EUR",
        "periode": "mois",
        "description": "78,79 € par mois, sans engagement de durée.",
        "lien_paiement": _lien_paiement("STRIPE_LIEN_MENSUEL",
                                        LIEN_PAIEMENT_MENSUEL_DEFAUT),
    },
    "annuel": {
        "id": "annuel",
        "libelle": "Pro — Annuel",
        "prix": 849.99,
        "devise": "EUR",
        "periode": "an",
        "description": "849,99 € par an, soit environ 10 % d'économie "
                       "par rapport au mensuel.",
        "lien_paiement": _lien_paiement("STRIPE_LIEN_ANNUEL",
                                        LIEN_PAIEMENT_ANNUEL_DEFAUT),
    },
}

FORMULE_DEFAUT = "mensuel"

# Durée de droits ouverte par un paiement confirmé, par formule (en jours).
# Un abonnement Stripe renouvelle de lui-même ; cette durée est la VALIDITÉ
# LOCALE accordée entre deux confirmations, avec une marge pour absorber un
# prélèvement décalé de quelques jours.
DUREE_DROITS_JOURS = {"mensuel": 31 + 3, "annuel": 365 + 7}

# ═══════════════════════════ BOUTIQUE / ABONNEMENT ════════════════════════

# Fiche officielle de l'application. Ce n'est PAS un secret : une URL publique
# de boutique, affichée telle quelle par l'interface.
MICROSOFT_STORE_URL = "https://apps.microsoft.com/detail/9nltgfr2btsp?hl=fr-FR&gl=FR"

# Durée de validité, en secondes, du dernier état de licence vérifié en ligne.
# Passé ce délai, l'application revérifie. Une licence signée reste valable
# hors ligne jusqu'à sa date d'expiration (voir verification.py) : couper le
# réseau ne doit pas transformer un abonné en version d'essai.
LICENCE_CACHE_S = 6 * 3600

# Tolérance d'horloge à la vérification d'expiration (secondes) : deux
# machines ne sont jamais parfaitement à l'heure, et une licence valable ne
# doit pas être refusée pour quelques secondes de dérive.
LICENCE_TOLERANCE_HORLOGE_S = 300


# ═══════════════════════════ LICENCES AGENCE NOVIA ════════════════════════
#
# Agence 46 reste une application LOCALE : données, projets, SQLite, modèles
# et Ollama ne quittent jamais la machine. Le SEUL échange réseau de ce
# système est l'activation / la revalidation d'une licence achetée sur le
# site Agence Novia — et il ne transporte que quatre champs (voir
# licence/novia_client.py).
#
# MODE NOVIA
# ----------
# Il s'active quand l'URL de l'API ET au moins une clé publique sont
# configurées. Tant qu'il manque l'une des deux, l'application garde
# EXACTEMENT son fonctionnement actuel (compte local, essai, Stripe) : c'est
# ce qui permet de livrer ce code avant que le serveur Novia n'existe, sans
# rien casser. Une URL sans clé publique ne suffit pas : aucune licence ne
# pourrait être vérifiée, et l'application serait fermée à tous.
#
# CE QUI N'EST PAS ICI, ET NE DOIT JAMAIS Y ÊTRE
# ----------------------------------------------
# La clé PRIVÉE de signature de Novia. Elle vit sur le serveur Novia et nulle
# part ailleurs. L'application n'embarque que la clé PUBLIQUE, qui ne permet
# QUE de vérifier une signature — jamais d'en fabriquer une.

import os as _os


def _env_texte(nom: str, defaut: str = "") -> str:
    return (_os.getenv(nom, "") or defaut).strip()


def _env_nombre(nom: str, defaut: float, minimum: float, maximum: float) -> float:
    """Nombre lu dans l'environnement, BORNÉ.

    Borné, parce qu'une période hors ligne réglée à 100 000 jours par une
    variable d'environnement transformerait une licence mensuelle en licence
    perpétuelle. Les bornes sont les limites raisonnables du produit, pas une
    préférence de l'utilisateur.
    """
    brut = _env_texte(nom)
    if not brut:
        return defaut
    try:
        valeur = float(brut)
    except ValueError:
        import logging
        logging.getLogger(__name__).error(
            "[novia] %s illisible (« %s ») : %s retenu", nom, brut, defaut)
        return defaut
    return max(minimum, min(maximum, valeur))


# URL de l'API de licences Novia — À RENSEIGNER quand le serveur existe.
# Volontairement VIDE : l'URL définitive n'est pas connue, et une URL inventée
# enverrait les clés de licence des clients vers un domaine qui n'est pas le
# vôtre. HTTPS obligatoire (vérifié par novia_client.py).
NOVIA_API_URL_DEFAUT = ""

# Identifiant du produit côté Novia. Permet à un même serveur de licences de
# servir plusieurs applications : une licence Agence 46 n'active pas une autre
# application Novia, et inversement.
NOVIA_PRODUIT_DEFAUT = "agence46"

# Clés PUBLIQUES Ed25519 de Novia, indexées par identifiant (`kid` de l'en-tête
# du jeton). Plusieurs clés = rotation sans interruption : Novia signe avec la
# nouvelle pendant que les jetons émis avec l'ancienne restent vérifiables
# jusqu'à leur échéance. Format : 64 caractères hexadécimaux (32 octets).
#
# VIDE tant que Novia n'a pas généré sa paire de clés. Une clé publique n'est
# pas un secret : le jour venu, elle a sa place ici, en clair, dans le dépôt.
NOVIA_CLES_PUBLIQUES: dict = {}

# Pages du site Novia. Centralisées ICI et nulle part ailleurs : l'interface
# les reçoit par l'API locale, elle ne les écrit jamais en dur.
NOVIA_URL_ACHAT_DEFAUT = ""        # « Acheter Agence 46 »
NOVIA_URL_COMPTE_DEFAUT = ""       # « Gérer mon abonnement »

# Nombre de jours pendant lesquels l'application fonctionne SANS contacter le
# serveur après une validation réussie. Le serveur fixe sa propre limite dans
# le jeton signé ; cette valeur ne peut que la RACCOURCIR, jamais l'allonger.
OFFLINE_GRACE_PERIOD_JOURS = _env_nombre("OFFLINE_GRACE_PERIOD", 30, 1, 90)

# Intervalle entre deux revalidations en ligne. Aucune requête n'est faite à
# chaque démarrage : la licence locale signée fait foi entre deux contrôles.
LICENSE_CHECK_INTERVAL_JOURS = _env_nombre("LICENSE_CHECK_INTERVAL", 7, 1, 30)

# Que devient l'application quand la licence EXPIRE ?
#   "limite"  — mode gratuit : fonctionnalités de base, limites de l'essai.
#   "bloque"  — application fermée jusqu'au renouvellement.
# Dans les deux cas, les projets et les données locales restent intacts.
LICENSE_EXPIRED_POLICY = _env_texte("LICENSE_EXPIRED_POLICY", "limite").lower()
if LICENSE_EXPIRED_POLICY not in ("limite", "bloque"):
    LICENSE_EXPIRED_POLICY = "limite"

# Niveaux de licence. Seuls ceux de NIVEAUX_PREMIUM ouvrent l'accès complet ;
# FREE applique les limites de la version d'essai. Ajouter un niveau ici suffit
# à le reconnaître partout — c'est le seul endroit qui les énumère.
NIVEAUX_LICENCE = ("FREE", "PRO", "BUSINESS", "ENTERPRISE")
NIVEAUX_PREMIUM = frozenset({"PRO", "BUSINESS", "ENTERPRISE"})

# Tolérance d'horloge pour les dates signées par le serveur (secondes).
NOVIA_TOLERANCE_HORLOGE_S = 300

# Recul d'horloge au-delà duquel l'événement est journalisé comme suspect.
NOVIA_RECUL_HORLOGE_SUSPECT_S = 24 * 3600


def novia_api_url() -> str:
    """URL de l'API Novia retenue, SANS barre finale. Vide si non configurée."""
    return _env_texte("LICENSE_API_URL", NOVIA_API_URL_DEFAUT).rstrip("/")


def novia_produit() -> str:
    return _env_texte("LICENSE_PRODUCT_ID", NOVIA_PRODUIT_DEFAUT) or "agence46"


def novia_cles_publiques() -> dict:
    """Clés publiques connues : celles du paquet, plus `LICENSE_PUBLIC_KEY`.

    La variable d'environnement sert au développement et aux tests ; elle
    s'AJOUTE au jeu embarqué sous l'identifiant « env » et ne peut en retirer
    aucune.
    """
    cles = dict(NOVIA_CLES_PUBLIQUES)
    env = _env_texte("LICENSE_PUBLIC_KEY")
    if env:
        cles.setdefault("env", env)
    return cles


def novia_url_achat() -> str:
    return _env_texte("LICENSE_PURCHASE_URL", NOVIA_URL_ACHAT_DEFAUT)


def novia_url_compte() -> str:
    return _env_texte("LICENSE_ACCOUNT_URL", NOVIA_URL_COMPTE_DEFAUT)


def novia_actif() -> bool:
    """Le mode licence Novia est-il en service ?

    URL ET clé publique : l'une sans l'autre ne permet pas d'activer quoi que
    ce soit, et basculer quand même fermerait l'application à tout le monde.
    """
    return bool(novia_api_url() and novia_cles_publiques())


def novia_configuration_incomplete() -> str:
    """Explication si la configuration est à moitié faite, vide sinon."""
    url, cles = novia_api_url(), novia_cles_publiques()
    if url and not cles:
        return ("LICENSE_API_URL est renseignée mais aucune clé publique Novia "
                "n'est configurée : le mode Novia reste désactivé.")
    if cles and not url:
        return ("Une clé publique Novia est configurée mais pas LICENSE_API_URL : "
                "le mode Novia reste désactivé.")
    return ""
