import logging
import os
import sys
from pathlib import Path
from dotenv import load_dotenv

_logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).parent
load_dotenv(BASE_DIR / ".env")

# Dossier des données de l'UTILISATEUR : base SQLite (historique des trades,
# signaux, performances). Il ne doit jamais se trouver DANS l'application.
DOSSIER_UTILISATEUR = Path(os.path.expanduser("~")) / ".agence_financiere"

# ── L'application est 100 % LOCALE ───────────────────────────────────
# Aucun appel distant, aucune clé API, aucun abonnement : les 45 agents
# raisonnent sur des indicateurs et des règles, leurs 46 assistants et le Chef
# d'Orchestre consultent un moteur IA qui tourne SUR LA MACHINE (Ollama ou
# Hermès, tous deux embarqués dans l'application). Les seules connexions
# sortantes restantes sont les cours de marché et le courtier MetaTrader 5.

# ── IA Locale via Ollama (100% hors-ligne, sans clé API) ─────────────
# 127.0.0.1 plutôt que localhost : sur Windows, localhost peut résoudre
# en IPv6 (::1) alors qu'Ollama écoute en IPv4 → fausse "non-détection".
OLLAMA_URL   = os.getenv("OLLAMA_URL",   "http://127.0.0.1:11434")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "llama3.2")
# Conservée pour les configurations existantes : l'IA est toujours locale.
USE_OLLAMA   = True

# ── IA Locale via Hermès (llama.cpp + modèle NousResearch Hermes) ─────
# Second moteur local, au choix de l'utilisateur. Port distinct d'Ollama :
# les deux peuvent être installés côte à côte, seul le moteur sélectionné est
# démarré (voir utils/moteur_ia.demarrer_si_possible).
HERMES_URL   = os.getenv("HERMES_URL",   "http://127.0.0.1:11435")
HERMES_MODEL = os.getenv("HERMES_MODEL", "hermes-3-llama-3.2-3b")

# Moteur d'IA sélectionné : « ollama » ou « hermes ». Les deux tournent sur
# la machine — il n'existe plus de mode distant.
MOTEUR_IA = os.getenv("MOTEUR_IA", "")

MOTEURS_IA = ("ollama", "hermes")
MOTEUR_IA_DEFAUT = "ollama"


# ── Lecture dynamique (le moteur peut changer en cours d'exécution) ──
def moteur_ia_actif() -> str:
    """Moteur d'IA réellement utilisé : « ollama » ou « hermes ».

    Une valeur inconnue — dont « cloud », écrit par les versions qui
    proposaient encore une clé API — retombe sur le moteur local par défaut.
    Sans ce garde-fou, une configuration existante aurait désigné un moteur
    qui n'existe plus, et plus aucune consultation IA n'aurait abouti.
    """
    nom = (os.getenv("MOTEUR_IA", "") or "").strip().lower()
    if nom in MOTEURS_IA:
        return nom
    return MOTEUR_IA_DEFAUT


def use_ollama_actif() -> bool:
    """Compat : « l'IA tourne-t-elle en local ? » — désormais toujours vrai.

    Conservée parce que plusieurs modules historiques l'interrogent avant
    d'éviter un appel payant. Il n'y a plus d'appel payant à éviter.
    """
    return True


def ollama_model_actif() -> str:
    return os.getenv("OLLAMA_MODEL", "llama3.2")


def hermes_model_actif() -> str:
    return os.getenv("HERMES_MODEL", HERMES_MODEL) or "hermes-3-llama-3.2-3b"


def _env_float(nom: str, defaut: float) -> float:
    """float(os.getenv(...)) TOLÉRANT : virgule décimale acceptée, repli sur
    le défaut si la valeur est inexploitable.

    Ce module est importé au tout premier `import config` : un `float()` nu y
    faisait ÉCHOUER LE DÉMARRAGE COMPLET de l'application sur une simple faute
    de frappe dans le `.env` livré en exemple (« CAPITAL_INITIAL=100 000 »,
    « LEVIER_MAX=1,5 »). L'utilisateur ne voyait qu'un ValueError sans rapport
    apparent, au fond de logs/backend.log. Même convention tolérante que
    utils/trading_config et utils/preflight.
    """
    brut = os.getenv(nom)
    if brut is None:
        return defaut
    try:
        v = float(str(brut).strip().replace(",", ".").replace(" ", ""))
    except (TypeError, ValueError):
        _logger.warning(f"[config] {nom}={brut!r} illisible — défaut {defaut} appliqué")
        return defaut
    if v != v or v in (float("inf"), float("-inf")):
        _logger.warning(f"[config] {nom}={brut!r} hors bornes — défaut {defaut} appliqué")
        return defaut
    return v


def _env_int(nom: str, defaut: int) -> int:
    return int(round(_env_float(nom, defaut)))


CAPITAL_INITIAL = _env_float("CAPITAL_INITIAL", 100000.0)
# Repères par DÉFAUT du portefeuille simulé et de l'agent de conformité. Les
# limites réellement appliquées au trading viennent de utils/trading_config.py
# et de utils/risk_guard.py, tous deux réglables depuis l'interface — ces
# constantes ne servent plus que de repli quand ces modules sont indisponibles.
MAX_RISQUE_PAR_TRADE = _env_float("MAX_RISQUE_PAR_TRADE", 0.05)
MAX_DRAWDOWN_GLOBAL = _env_float("MAX_DRAWDOWN", 0.50)
MAX_POSITIONS_SIMULTANÉES = _env_int("MAX_POSITIONS", 50)
LEVIER_MAX = _env_float("LEVIER_MAX", 10.0)


def _dossier_donnees() -> Path:
    """Dossier de la base SQLite — HORS de l'application une fois installée.

    Compilée puis installée, l'application vit dans un dossier qui ne lui
    appartient plus : Windows peut le placer en lecture seule, l'installeur le
    remplace intégralement à chaque mise à jour, et le désinstalleur l'efface.
    Y écrire la base revenait donc à perdre tout l'historique de trading à la
    première mise à jour — sans que rien ne prévienne, puisque `data/` était
    recréé vide au démarrage suivant.

    La base rejoint donc les autres données personnelles dans
    `~/.agence_financiere/data/`, à côté de la configuration, des identifiants
    MetaTrader et des modèles IA. L'installeur n'y touche pas, et la
    désinstallation ne la supprime qu'à la demande explicite de l'utilisateur.

    Depuis les sources (dépôt cloné), rien ne change : la base reste dans
    `python/data/`, ignorée par git — un développeur qui essaie une branche ne
    doit pas écrire dans les données réelles de sa propre installation.
    """
    force = (os.getenv("AGENCE_DATA_DIR", "") or "").strip()
    if force:
        return Path(force).expanduser()
    if getattr(sys, "frozen", False):
        return DOSSIER_UTILISATEUR / "data"
    return BASE_DIR / "data"


def _reprendre_base_historique(cible: Path) -> None:
    """Récupère la base des versions livrées en ZIP (base DANS l'application).

    Ces versions écrivaient dans `<dossier de l'exe>/_internal/python/data`.
    Sans cette reprise, installer la version packagée par-dessus donnerait une
    application qui démarre parfaitement... avec un historique vide.
    Copie unique, et jamais par-dessus une base existante.
    """
    if cible.exists() or not getattr(sys, "frozen", False):
        return
    # Uniquement à côté de l'exécutable, JAMAIS dans le bundle : une base
    # trouvée dans le bundle serait celle de la machine de compilation, et
    # l'utilisateur démarrerait sur des trades qui ne sont pas les siens
    # (agence.spec l'exclut d'ailleurs de l'empaquetage).
    ancienne = Path(sys.executable).parent / "_internal" / "python" / "data" / "agence.db"
    try:
        if ancienne.is_file() and ancienne.resolve() != cible.resolve():
            import shutil
            cible.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(ancienne, cible)
            _logger.info(f"[config] Base de données reprise depuis {ancienne}")
    except Exception as e:                    # une reprise ratée ne bloque rien
        _logger.warning(f"[config] Reprise de la base impossible : {e}")


DB_PATH = _dossier_donnees() / "agence.db"
_reprendre_base_historique(DB_PATH)

BACKEND_HOST = os.getenv("BACKEND_HOST", "0.0.0.0")
BACKEND_PORT = _env_int("BACKEND_PORT", 8000)

# Les symboles sont écrits au format YAHOO (source des données de marché :
# yfinance). MT5Manager.resoudre_symbole() les traduit à l'exécution vers le
# nom RÉEL du courtier — « EURUSD=X » devient « EURUSD », « BTC-USD » devient
# « BTCUSD » chez AvaTrade. Ne mettez donc PAS le nom du courtier ici : le
# téléchargement des cours échouerait et les agents travailleraient sur des
# prix simulés (l'auto-trader refuserait alors tout ordre réel).
SYMBOLES_CRYPTO = ["BTC-USD", "ETH-USD", "BNB-USD", "SOL-USD", "XRP-USD"]
SYMBOLES_ACTIONS = ["AAPL", "MSFT", "NVDA", "GOOGL", "AMZN", "TSLA", "SPY", "QQQ"]
# Paires majeures proposées par AvaTrade (Market Watch MetaTrader 5).
SYMBOLES_FOREX = ["EURUSD=X", "GBPUSD=X", "USDCHF=X", "USDJPY=X",
                  "AUDUSD=X", "NZDUSD=X", "USDCAD=X", "USDSEK=X",
                  "EURGBP=X"]
SYMBOLES_INDICES = ["^GSPC", "^IXIC", "^DJI", "^VIX"]
# Matières premières du Market Watch AvaTrade : « GOLD.TR » (or) et
# « CrudeOIL » (pétrole WTI). Côté cours, on passe par les contrats à terme
# Yahoo les plus liquides — GC=F (COMEX Gold) et CL=F (WTI) — qui suivent au
# plus près les CFD du courtier. AUCUNE transformation de chaîne ne mène de
# « GC=F » à « GOLD.TR » : la traduction passe par ALIAS_COURTIER (plus bas),
# et l'affichage par NOMS_AFFICHES.
SYMBOLES_MATIERES = ["GC=F", "CL=F"]
SYMBOLES_DEFAULT = SYMBOLES_CRYPTO[:3] + SYMBOLES_ACTIONS[:5]

TOUS_SYMBOLES = (SYMBOLES_CRYPTO + SYMBOLES_ACTIONS + SYMBOLES_FOREX
                 + SYMBOLES_INDICES + SYMBOLES_MATIERES)

# Nom AFFICHÉ par l'interface pour les tickers dont la forme Yahoo n'évoque
# rien dans le Market Watch : « GC=F » ne se lit pas « GOLD.TR ». Miroir exact
# de NOMS_AFFICHES dans frontend/js/app.js (labelSymbole).
NOMS_AFFICHES = {
    "GC=F": "GOLD.TR",
    "CL=F": "CrudeOIL",
}

# Noms à essayer CHEZ LE COURTIER, dans l'ordre, pour ces mêmes instruments.
# resoudre_symbole() s'en tient STRICTEMENT à cette liste pour eux : réduire
# « CL=F » à sa racine « CL » — ce que fait l'heuristique générique — pourrait
# faire partir un ordre RÉEL sur Colgate-Palmolive.
# Note : « GOLD » y désigne le MÉTAL (l'instrument du Market Watch), pas
# l'action Barrick Gold (NYSE: GOLD) — qui n'est pas dans SYMBOLES_ACTIONS.
ALIAS_COURTIER = {
    "GC=F": ["GOLD.TR", "GOLD", "XAUUSD", "GOLDUSD", "XAUUSD.TR"],
    "CL=F": ["CrudeOIL", "CRUDEOIL", "CrudeOil", "CRUDEOIL.TR",
             "USOIL", "WTI"],
}

# Devises reconnues pour identifier une paire forex saisie au format courtier
# (EURUSD, USDSEK…). Même liste que utils/market_hours._DEVISES.
_DEVISES = {
    "EUR", "USD", "GBP", "JPY", "CHF", "AUD", "CAD", "NZD",
    "SEK", "NOK", "DKK", "PLN", "CZK", "HUF", "RON", "ISK",
    "MXN", "ZAR", "TRY", "SGD", "HKD", "CNH", "ILS", "THB",
}

# Racines crypto courantes chez les courtiers CFD (nom collé : BTCUSD)
_CRYPTOS = {"BTC", "ETH", "BNB", "SOL", "XRP", "ADA", "DOGE", "DOT",
            "LTC", "LINK", "AVAX", "MATIC", "TRX", "BCH", "XLM"}


def _normaliser_nom(nom: str) -> str:
    """Clé de comparaison d'un nom d'instrument : majuscules, sans séparateur.
    « GOLD.TR » → « GOLDTR », « CrudeOIL » → « CRUDEOIL ». Les courtiers
    décorent leurs symboles de points, tirets et underscores : comparer les
    chaînes brutes ferait manquer l'instrument que l'utilisateur a sous les
    yeux dans son Market Watch."""
    n = (nom or "").upper()
    for c in "-._#/ ":
        n = n.replace(c, "")
    return n


def _nom_courtier(symbole_yahoo: str) -> str:
    """Nom tel que l'interface l'AFFICHE (miroir de labelSymbole en JS) :
    « BTC-USD » → « BTCUSD », « EURUSD=X » → « EURUSD », « ^GSPC » → « GSPC »,
    « GC=F » → « GOLD.TR »."""
    fixe = NOMS_AFFICHES.get(symbole_yahoo.upper())
    if fixe:
        return fixe
    s = symbole_yahoo.upper()
    if s.endswith("=X"):
        s = s[:-2]
    if s.startswith("^"):
        s = s[1:]
    if s.endswith("-USD"):
        s = s[:-4] + "USD"
    return s


# Table inverse « nom affiché → symbole Yahoo », construite depuis les listes
# ci-dessus : elle est donc toujours cohérente avec ce que l'interface montre.
# Clés NORMALISÉES : « GOLD.TR », « gold.tr » et « GOLDTR » désignent le même
# instrument pour l'utilisateur, ils doivent donc mener au même ticker.
SYMBOLES_PAR_NOM_COURTIER = {_normaliser_nom(_nom_courtier(s)): s
                             for s in TOUS_SYMBOLES}

# Tous les noms sous lesquels un instrument aliasé peut être saisi : son nom
# affiché, ses noms chez le courtier (« GOLD », « XAUUSD », « WTI »…) et son
# propre ticker Yahoo. Sans cette table, taper « XAUUSD » dans le pré-vol ou
# le backtest partait en « action » et yfinance ne trouvait rien.
ALIAS_VERS_YAHOO = {}
for _yahoo, _noms in ALIAS_COURTIER.items():
    for _nom in [_yahoo, NOMS_AFFICHES.get(_yahoo, "")] + list(_noms):
        if _nom:
            ALIAS_VERS_YAHOO.setdefault(_normaliser_nom(_nom), _yahoo)


def vers_symbole_yahoo(entree: str) -> str:
    """Traduit une saisie utilisateur vers le format Yahoo (source des cours).

    L'interface AFFICHE les noms du courtier (« BTCUSD », « AUDUSD ») parce
    que ce sont ceux du Market Watch MetaTrader. L'utilisateur les recopie
    donc naturellement dans les champs de saisie — or le téléchargement des
    cours attend le format Yahoo (« BTC-USD », « AUDUSD=X »). Sans cette
    traduction, yfinance ne trouvait rien et l'application basculait sur des
    prix INVENTÉS : un Bitcoin à 89 et un AUDUSD à 128, avec un volume et une
    perte au stop calculés dessus. On ferme la boucle : ce que l'interface
    affiche est désormais accepté en entrée.

    Une saisie DÉJÀ au format Yahoo est renvoyée telle quelle.
    """
    brut = (entree or "").strip()
    if not brut:
        return brut
    # Déjà au format Yahoo (suffixe/préfixe caractéristique ou symbole connu)
    if (brut.endswith("=X") or brut.endswith("=F") or brut.startswith("^")
            or "-" in brut):
        return brut
    haut = brut.upper()
    if haut in {s.upper() for s in TOUS_SYMBOLES}:
        return brut
    norm = _normaliser_nom(brut)

    # 1) instrument dont le nom courtier n'a aucun rapport avec son ticker de
    #    cours : « GOLD.TR », « GOLD », « XAUUSD » → « GC=F ».
    alias = ALIAS_VERS_YAHOO.get(norm)
    if alias:
        return alias

    # 2) correspondance EXACTE avec un symbole configuré (via son nom affiché)
    connu = SYMBOLES_PAR_NOM_COURTIER.get(norm)
    if connu:
        return connu

    # 3) heuristiques pour les instruments hors listes
    if len(haut) == 6 and haut[:3] in _DEVISES and haut[3:] in _DEVISES:
        return haut + "=X"                      # paire forex : AUDUSD → AUDUSD=X
    if haut.endswith("USD") and haut[:-3] in _CRYPTOS:
        return haut[:-3] + "-USD"               # crypto : BTCUSD → BTC-USD
    if haut.endswith("USDT") and haut[:-4] in _CRYPTOS:
        return haut[:-4] + "-USD"

    # 4) sinon : action ou indice, le nom Yahoo est identique (AAPL, MSFT…)
    return brut


TIMEFRAMES = ["1m", "5m", "15m", "1h", "4h", "1d", "1wk"]
TIMEFRAME_DEFAULT = "1h"
PERIODE_ANALYSE = "3mo"

COMMISSION_PCT = 0.001
SLIPPAGE_PCT = 0.0005
TAUX_SANS_RISQUE = 0.05

GROUPES = {
    "analyse_marche": "Analyse de Marché",
    "strategies": "Stratégies de Trading",
    "risques": "Gestion des Risques",
    "execution": "Exécution & Opérations",
    "data_intelligence": "Data & Intelligence",
    "reporting": "Reporting & Communication",
}
