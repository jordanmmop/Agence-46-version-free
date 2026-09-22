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

# Les deux formules proposées dans l'application.
#
# ⚠️ Les liens ci-dessous sont des liens Stripe de TEST (« /test_ ») : ils
# n'encaissent aucun paiement réel. Les remplacer par les liens de production
# avant toute mise en vente — c'est le SEUL changement à faire ici.
FORMULES = {
    "mensuel": {
        "id": "mensuel",
        "libelle": "Pro — Mensuel",
        "prix": 78.79,
        "devise": "EUR",
        "periode": "mois",
        "description": "78,79 € par mois, sans engagement de durée.",
        "lien_paiement": "https://buy.stripe.com/test_aFaaEX7ol6Nc9ZsewvdZ601",
    },
    "annuel": {
        "id": "annuel",
        "libelle": "Pro — Annuel",
        "prix": 849.99,
        "devise": "EUR",
        "periode": "an",
        "description": "849,99 € par an, soit environ 10 % d'économie "
                       "par rapport au mensuel.",
        "lien_paiement": "https://buy.stripe.com/test_00w8wP3851sS0oS1JJdZ602",
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
