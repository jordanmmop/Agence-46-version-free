"""Gestionnaire MetaTrader 5 — détection, connexion, exécution de trades"""
import functools
import os
import glob
import math
import platform
import subprocess
import logging
import threading
import time
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# Délai maximum d'attente du verrou terminal pour une LECTURE d'affichage.
# Au-delà, on sert la dernière valeur connue plutôt que de bloquer la requête
# (cf. MT5Manager._lecture). Court volontairement : l'interface doit rester
# vivante même quand le terminal est monopolisé par une connexion.
DELAI_LECTURE_S = float(os.getenv("MT5_LECTURE_TIMEOUT_S", "1.5"))
# Une fois le terminal constaté occupé, les lectures suivantes échouent
# IMMÉDIATEMENT pendant ce délai, au lieu de re-payer l'attente chacune leur
# tour. Un seul statut enchaîne quatre lectures (compte, AlgoTrading,
# historique, positions) : sans ce coupe-circuit, il cumulait quatre attentes
# et dépassait encore la cadence de sondage de 3 s du tableau de bord.
BACKOFF_OCCUPE_S = float(os.getenv("MT5_BACKOFF_S", "2.0"))


# Codes de rejet MetaTrader 5 traduits en CONSIGNE, pas en jargon.
# « Ordre rejeté (10027) : AutoTrading disabled by client » ne dit pas à
# l'utilisateur quoi faire — or la cause est un simple bouton du terminal.
_RETCODES = {
    10027: ("Le bouton « AlgoTrading » de MetaTrader 5 est DÉSACTIVÉ. "
            "Ouvrez le terminal MetaTrader 5, cliquez sur « AlgoTrading » "
            "dans la barre d'outils (le bouton doit devenir vert), puis "
            "relancez. Raccourci : Ctrl+E."),
    10026: ("Le trading automatique est désactivé côté SERVEUR du courtier. "
            "Contactez AvaTrade : votre compte n'autorise pas les robots."),
    10018: ("Le marché est FERMÉ pour cet instrument. L'ordre repartira à "
            "l'ouverture de la séance."),
    10019: ("Fonds insuffisants pour ce volume. Réduisez la taille de "
            "position ou alimentez le compte."),
    10016: ("Stop-loss ou take-profit trop proche du prix (distance minimale "
            "imposée par le courtier). L'application les recalcule "
            "automatiquement au prochain essai."),
    10014: ("Volume invalide pour cet instrument (hors des bornes min/max ou "
            "du pas autorisé par le courtier)."),
    10015: "Prix invalide pour cet instrument.",
    10013: "Requête invalide refusée par le terminal.",
    10006: ("Ordre rejeté par le courtier, sans motif détaillé. Vérifiez que "
            "l'instrument est bien négociable sur votre compte."),
    10017: ("Le trading est DÉSACTIVÉ pour ce compte chez le courtier. "
            "Contactez AvaTrade."),
    10021: "Plus aucune cotation pour cet instrument (marché suspendu).",
    10031: "Pas de connexion au serveur du courtier.",
}


# Codes d'ERREUR de la librairie Python MetaTrader5 (initialize / login),
# distincts des codes de rejet d'ordre plus haut. Traduits en MARCHE À SUIVRE :
# « (-10005, 'IPC timeout') » ne dit rien à personne, alors que la cause est
# presque toujours une boîte de dialogue restée ouverte dans le terminal.
_ERREURS_INIT = {
    -10005: ("MetaTrader 5 ne répond pas à l'application (délai de communication "
             "dépassé). Dans l'ordre :\n"
             "1. FERMEZ toute fenêtre de dialogue ouverte dans MetaTrader 5 — "
             "en particulier la fenêtre « Se connecter ». Tant qu'une boîte de "
             "dialogue est affichée, le terminal ne répond à aucun programme.\n"
             "2. Lancez MetaTrader 5 ET cette application avec les MÊMES droits "
             "(les deux en administrateur, ou aucun des deux).\n"
             "3. Ne gardez qu'UN SEUL terminal MetaTrader 5 ouvert.\n"
             "4. Dans MetaTrader 5 : Outils → Options → Expert Advisors → cochez "
             "« Autoriser le trading automatique ».\n"
             "5. Si le terminal est à jour mais pas la librairie, mettez-la à "
             "niveau : pip install --upgrade MetaTrader5 (un terminal récent "
             "avec un paquet ancien donne exactement cette erreur)."),
    -10004: ("Impossible d'ouvrir la communication avec MetaTrader 5. Vérifiez que "
             "le terminal est bien DÉMARRÉ et qu'un compte y est ouvert, puis "
             "réessayez."),
    -10003: ("Initialisation de la communication impossible. Fermez complètement "
             "MetaTrader 5, relancez-le, laissez-le se connecter, puis réessayez."),
    -10002: "Réponse illisible du terminal MetaTrader 5.",
    -10001: "Envoi impossible vers le terminal MetaTrader 5.",
    -10000: "Erreur interne de la librairie MetaTrader 5.",
    -6:     ("Autorisation refusée : le numéro de compte, le mot de passe ou le "
             "nom du serveur ne correspondent pas. Vérifiez les trois dans "
             "MetaTrader 5 → Fichier → Ouvrir un compte."),
    -5:     ("Version de MetaTrader 5 incompatible avec la librairie. Mettez le "
             "terminal à jour (Aide → Vérifier les mises à jour)."),
    -4:     "Terminal MetaTrader 5 introuvable sur cette machine.",
    -3:     "Mémoire insuffisante pour communiquer avec MetaTrader 5.",
    -2:     "Paramètres de connexion invalides.",
}


def version_librairie() -> str:
    """Version du paquet Python MetaTrader5 installé (« ? » si indisponible).

    Un terminal RÉCENT avec un paquet Python ancien produit exactement le
    symptôme le plus déroutant : un délai de communication dépassé, sans
    aucune autre explication. La version doit donc apparaître dans le
    diagnostic — c'est parfois la seule chose à corriger.
    """
    try:
        import MetaTrader5 as mt5
        return str(getattr(mt5, "__version__", "") or "?")
    except Exception:
        return "?"


def _code_erreur(err) -> Optional[int]:
    """Code numérique d'un `mt5.last_error()` — il renvoie un tuple (code, texte)."""
    if isinstance(err, (tuple, list)) and err:
        try:
            return int(err[0])
        except (TypeError, ValueError):
            return None
    try:
        return int(err)
    except (TypeError, ValueError):
        return None


def _conseil_init(err) -> str:
    """Marche à suivre correspondant à une erreur d'initialisation/connexion."""
    return _ERREURS_INIT.get(_code_erreur(err), "")


def _message_retcode(retcode, commentaire: str) -> str:
    """Message d'erreur d'ordre : consigne claire + code brut pour le support."""
    aide = _RETCODES.get(retcode)
    brut = f"code {retcode}" + (f" — {commentaire}" if commentaire else "")
    if aide:
        return f"{aide} [{brut}]"
    return f"Ordre rejeté par le courtier ({brut})"


def _nombre_fini(valeur, defaut=None):
    """float(valeur) si c'est un nombre fini exploitable, sinon `defaut`.

    Les champs d'un ordre arrivent d'un JSON libre (`/api/mt5/trade`) ou d'un
    calcul d'agent : une chaîne, None, NaN ou l'infini y sont possibles et
    doivent être écartés AVANT d'atteindre le courtier ou l'arrondi du journal.
    """
    if isinstance(valeur, bool) or valeur is None:
        return defaut
    try:
        f = float(valeur)
    except (TypeError, ValueError):
        return defaut
    return f if math.isfinite(f) else defaut


def _normaliser_nom(nom: str) -> str:
    """Clé de comparaison d'un nom d'instrument : majuscules, sans séparateur
    (« GOLD.TR » → « GOLDTR »). Les courtiers décorent leurs symboles ; la
    comparaison, elle, doit porter sur l'instrument."""
    n = (nom or "").upper()
    for c in "-._#/ ":
        n = n.replace(c, "")
    return n


def _alias_courtier(symbole_yahoo: str) -> List[str]:
    """Noms EXPLICITES du symbole chez le courtier, quand son ticker de cours
    n'a aucun rapport lexical avec lui (config.ALIAS_COURTIER).

    « GC=F » ne devient jamais « GOLD.TR » par transformation de chaîne, et
    « CL=F » réduit à sa racine « CL » désignerait Colgate-Palmolive. Pour ces
    instruments, la liste de config.py est la SEULE source autorisée.
    """
    try:
        from config import ALIAS_COURTIER
    except Exception:
        return []
    return list(ALIAS_COURTIER.get(symbole_yahoo.upper(), ()))


# Sous-dossiers de `bases/` qui ne sont PAS des serveurs de courtier. Sans ce
# tri, la fenêtre de connexion annonçait « Détectés sur votre terminal :
# Chats, Custom, MetaQuotes-Demo, signals » — trois entrées sur quatre étaient
# des dossiers techniques de MetaTrader, et la seule vraie se noyait dedans.
_DOSSIERS_NON_SERVEURS = {
    "default", "embedded", "news", "custom", "chats", "signals", "signal",
    "files", "community", "logs", "tester", "history", "ticks", "symbols",
    "common", "config", "profiles", "templates", "mql5", "bases",
}


def _est_dossier_serveur(dossier: str) -> bool:
    """Ce sous-dossier de `bases/` désigne-t-il un serveur de courtier ?

    Deux indices, l'un suffit :
    - il contient les caches que MetaTrader crée par serveur (`symbols`,
      `ticks`, `history`) ;
    - son nom a la forme d'un serveur MetaTrader, qui porte toujours le
      courtier et le type de compte séparés par un tiret (« MetaQuotes-Demo »,
      « Ava-Demo 1-MT5 », « ICMarketsSC-Live »).
    """
    nom = os.path.basename(dossier)
    if not nom or nom.lower() in _DOSSIERS_NON_SERVEURS:
        return False
    for marqueur in ("symbols", "ticks", "history"):
        try:
            if os.path.isdir(os.path.join(dossier, marqueur)):
                return True
        except Exception:
            pass
    return "-" in nom


def _sous_verrou(methode):
    """Sérialise la méthode sur `_mt5_lock`.

    La connexion terminal MetaTrader5 est UNIQUE et non thread-safe, alors que
    trois producteurs l'utilisent en parallèle : la boucle auto-trader
    (order_send), le thread de surveillance des positions (toutes les 5 s) et
    les requêtes du tableau de bord. Certaines méthodes ne prenaient le verrou
    que parce que leur appelant le faisait — invariant fragile, invisible à la
    lecture de la méthode. Le décorateur le rend LOCAL ; `_mt5_lock` étant un
    RLock, le prendre à nouveau quand l'appelant le détient déjà est gratuit.
    """
    @functools.wraps(methode)
    def _enveloppe(self, *args, **kwargs):
        with self._mt5_lock:
            return methode(self, *args, **kwargs)
    return _enveloppe


# Chemins courants de l'exécutable MT5 selon l'OS
_MT5_PATHS_WINDOWS = [
    r"C:\Program Files\MetaTrader 5\terminal64.exe",
    r"C:\Program Files (x86)\MetaTrader 5\terminal64.exe",
    r"C:\Program Files\AvaTrade MT5 Terminal\terminal64.exe",
    r"C:\Program Files (x86)\AvaTrade MT5 Terminal\terminal64.exe",
]

_AVATRADE_SERVERS = {
    "demo": "Ava-Demo 1-MT5",
    "real": "Ava-Real 1-MT5",
}

# Tous les serveurs AvaTrade connus
AVATRADE_SERVERS_LIST = [
    "Ava-Demo 1-MT5",
    "AvaTrade-Demo",
    "Ava-Real 1-MT5",
    "AvaTrade-Real 2-MT5",
    "AvaTrade-Real 3-MT5",
    "AvaTrade-Real 4-MT5",
]


class MT5Manager:
    """Gère la connexion et les opérations MetaTrader 5."""

    def __init__(self):
        self._connected = False
        self._account_info: Optional[Dict] = None
        self._auto_trading = False
        self._journal: List[Dict] = []
        # Le journal est écrit par l'auto-trader (thread) et lu par l'API
        self._journal_lock = threading.Lock()
        # La librairie MetaTrader5 est UNE connexion terminal globale, non
        # thread-safe : l'auto-trader (thread) et l'API (event loop) doivent
        # sérialiser leurs appels order_send/positions_get.
        self._mt5_lock = threading.RLock()
        self._equity_peak = 0.0
        # Cache Yahoo→MT5 : BTC-USD → BTCUSD, EURUSD=X → EURUSD, etc.
        self._symbol_cache: Dict[str, Optional[str]] = {}
        # Dernier état CONNU du terminal. Sert de réponse quand le verrou est
        # tenu par une opération longue (connexion, changement de compte) :
        # le tableau de bord affiche la dernière valeur connue au lieu
        # d'attendre — voir _lecture().
        self._positions_cache: List[Dict] = []
        self._algotrading_cache: Optional[bool] = None
        self._historique_cache: Dict[str, Any] = {}
        # Libellé de l'opération longue en cours (« Connexion à Ava-Demo… »),
        # remonté à l'interface pour qu'elle explique l'attente au lieu de
        # paraître figée.
        self._operation = ""
        # Coupe-circuit : instant (monotone) jusqu'auquel le terminal est
        # considéré occupé, pour ne pas re-payer l'attente à chaque lecture.
        self._occupe_jusqua = 0.0
        self._lib_available = self._check_lib()

    # ── Lectures non bloquantes ──────────────────────────────────────
    @contextmanager
    def _lecture(self):
        """Verrou du terminal pour une LECTURE — sans jamais faire attendre.

        Le tableau de bord sonde le terminal en continu (statut toutes les
        3 s, portefeuille et positions toutes les 5 s). Ces lectures prenaient
        `_mt5_lock` de façon BLOQUANTE : pendant une connexion — où
        `mt5.initialize()` peut mettre 30 à 60 s, le temps de lancer le
        terminal — chaque sondage restait bloqué pour toute la durée. Les
        requêtes s'empilaient dans le pool de threads du serveur (40 places),
        l'épuisaient, et PLUS RIEN ne répondait : l'application entière
        paraissait plantée (« la fenêtre ne répond pas »), y compris des
        écrans sans rapport comme la détection d'Ollama.

        Une lecture qui n'obtient pas le verrou rend la main immédiatement :
        l'appelant sert alors la dernière valeur connue et signale `occupe`.
        """
        if time.monotonic() < self._occupe_jusqua:
            yield False           # terminal déjà connu occupé : aucune attente
            return
        obtenu = self._mt5_lock.acquire(timeout=DELAI_LECTURE_S)
        if not obtenu:
            self._occupe_jusqua = time.monotonic() + BACKOFF_OCCUPE_S
        try:
            yield obtenu
        finally:
            if obtenu:
                self._mt5_lock.release()

    @contextmanager
    def _operation_longue(self, libelle: str):
        """Marque une opération qui monopolise le terminal (connexion,
        déconnexion, bascule de compte) pour que l'interface l'explique."""
        self._operation = libelle
        try:
            yield
        finally:
            self._operation = ""

    @property
    def verrou(self) -> "threading.RLock":
        """Verrou de sérialisation des appels MetaTrader5 (RLock réentrant).

        Exposé pour les appelants qui utilisent la librairie DIRECTEMENT
        (pré-vol : symbol_info, symbol_info_tick). Sans lui, ces lectures
        s'intercalaient entre l'order_send de l'auto-trader et sa réponse —
        or la connexion terminal est unique et non thread-safe."""
        return self._mt5_lock

    def _calculer_drawdown(self, equity: float) -> float:
        """Vrai drawdown : écart au pic d'équité observé (pas le P&L courant)."""
        if equity > self._equity_peak:
            self._equity_peak = equity
        if not self._equity_peak:
            return 0.0
        return round((equity - self._equity_peak) / self._equity_peak * 100, 2)

    # ── Initialisation ───────────────────────────────────────────────
    def _check_lib(self) -> bool:
        try:
            import MetaTrader5  # noqa: F401
            return True
        except ImportError:
            logger.info("Bibliothèque MetaTrader5 non disponible — mode simulation actif")
            return False

    # ── Détection et lancement ───────────────────────────────────────
    def find_path(self) -> Optional[str]:
        """Premier exécutable MT5 trouvé (None si aucun)."""
        chemins = self.find_paths()
        return chemins[0] if chemins else None

    def find_paths(self) -> List[str]:
        """TOUS les exécutables MT5 trouvés sur la machine.

        Il peut y en avoir plusieurs (terminal MetaQuotes, terminal d'un
        courtier, installation portable). Ne garder que le premier était une
        erreur : si ce n'est pas celui qui TOURNE, `initialize(path=…)` tente
        d'en démarrer un second, et la communication n'aboutit jamais.
        """
        system = platform.system()

        paths = []
        # Terminal PORTABLE embarqué dans l'application (runtime/mt5/terminal) :
        # placé EN TÊTE — c'est celui que l'application maîtrise, il ne dépend
        # d'aucune installation faite par l'utilisateur.
        portable = self.terminal_embarque()
        if portable:
            paths.append(portable)
        paths += list(_MT5_PATHS_WINDOWS)

        # Sous Windows, chercher aussi dans AppData
        if system == "Windows":
            appdata = os.environ.get("APPDATA", "")
            if appdata:
                pattern = os.path.join(appdata, "MetaQuotes", "Terminal", "*", "terminal64.exe")
                paths.extend(glob.glob(pattern))
                # `origin.txt` contient le dossier d'INSTALLATION réel du
                # terminal correspondant. C'est la seule source fiable quand
                # MetaTrader n'est pas installé à un emplacement standard
                # (terminal MetaQuotes, terminal d'un autre courtier, dossier
                # choisi à la main) : sans elle, `initialize()` partait sans
                # chemin et pouvait ouvrir un SECOND terminal au lieu de
                # parler à celui qui tourne déjà.
                origines = os.path.join(appdata, "MetaQuotes", "Terminal", "*", "origin.txt")
                for fichier in glob.glob(origines):
                    try:
                        with open(fichier, encoding="utf-16-le") as f:
                            dossier = f.read().strip().lstrip("\ufeff")
                    except Exception:
                        try:
                            with open(fichier, encoding="utf-8", errors="ignore") as f:
                                dossier = f.read().strip().lstrip("\ufeff")
                        except Exception:
                            continue
                    if dossier:
                        paths.append(os.path.join(dossier, "terminal64.exe"))
                        paths.append(os.path.join(dossier, "terminal.exe"))

        # Sous Linux/Mac via Wine
        elif system in ("Linux", "Darwin"):
            home = os.path.expanduser("~")
            wine_roots = [
                os.path.join(home, ".wine", "drive_c"),
                "/opt/wine/drive_c",
            ]
            for root in wine_roots:
                for rel in [
                    "Program Files/MetaTrader 5/terminal64.exe",
                    "Program Files (x86)/MetaTrader 5/terminal64.exe",
                    "Program Files/AvaTrade MT5 Terminal/terminal64.exe",
                ]:
                    paths.append(os.path.join(root, rel))

        trouves: List[str] = []
        for path in paths:
            try:
                if os.path.isfile(path) and path not in trouves:
                    trouves.append(path)
            except Exception:
                continue
        if trouves:
            logger.info(f"MT5 trouvé : {', '.join(trouves)}")
        return trouves

    @staticmethod
    def terminal_embarque() -> Optional[str]:
        """Terminal MetaTrader 5 PORTABLE livré dans l'application, ou None."""
        try:
            from utils.mt5_installer import MT5Installer
            return MT5Installer.terminal_embarque()
        except Exception:
            return None

    @staticmethod
    def terminaux_en_cours() -> int:
        """Nombre de processus terminal64.exe RÉELLEMENT en cours d'exécution.

        Lu auprès du système, sans passer par la librairie MetaTrader5 : c'est
        le seul moyen de savoir si un terminal tourne SANS risquer d'en
        démarrer un (voir `_terminal_repond`). Retourne -1 si le système ne
        sait pas répondre — l'appelant traite alors le nombre comme inconnu
        plutôt que comme « aucun ».
        """
        try:
            if platform.system() == "Windows":
                r = subprocess.run(
                    ["tasklist", "/FI", "IMAGENAME eq terminal64.exe",
                     "/FO", "CSV", "/NH"],
                    capture_output=True, text=True, timeout=10,
                    creationflags=0x08000000)          # sans fenêtre console
                lignes = [l for l in r.stdout.splitlines()
                          if l.strip().lower().startswith('"terminal64.exe"')]
                return len(lignes)
            r = subprocess.run(["pgrep", "-c", "-f", "terminal64"],
                               capture_output=True, text=True, timeout=10)
            return int((r.stdout or "0").strip() or 0)
        except Exception:
            return -1

    @classmethod
    def _terminal_repond(cls, timeout_ms: int = 5000) -> bool:
        """Un terminal MetaTrader 5 est-il joignable MAINTENANT ?

        ⚠ `mt5.initialize()`, MÊME SANS CHEMIN, DÉMARRE un terminal quand
        aucun ne tourne — la documentation MetaQuotes est explicite, et le
        commentaire qui affirmait le contraire ici était faux. Cette méthode
        étant appelée depuis six endroits, dont une boucle d'attente qui la
        rejoue toutes les deux secondes, chaque sondage ouvrait un terminal de
        plus : deux fenêtres, puis dix, puis autant que de sondages. Et comme
        un seul terminal peut réserver le port de communication local
        (« MCP bind error on 127.0.0.1:22346 [10048] » dans le journal
        MetaTrader), tous les suivants étaient inutilisables — d'où l'« IPC
        timeout » alors que MetaTrader était bien ouvert à l'écran.

        On vérifie donc D'ABORD, auprès du système, qu'un terminal tourne. Sans
        processus, la réponse est « non » sans jamais toucher à la librairie.
        """
        if cls.terminaux_en_cours() == 0:
            return False                      # aucun terminal : ne rien lancer
        try:
            import MetaTrader5 as mt5
        except Exception:
            return False
        try:
            if mt5.initialize(timeout=timeout_ms):
                return True
        except Exception:
            pass
        try:
            mt5.shutdown()
        except Exception:
            pass
        return False

    @staticmethod
    def _autoriser_trading_automatique(chemin_terminal: str) -> bool:
        """Active « Autoriser le trading automatique » dans le terminal EMBARQUÉ.

        Sans cette option, MetaTrader accepte la connexion mais REFUSE chaque
        ordre avec le code 10027 — l'application semble alors fonctionner, et
        rien ne part chez le courtier. C'est le premier obstacle que rencontre
        quelqu'un qui vient d'installer MetaTrader, et il se règle dans un
        menu que personne ne pense à ouvrir.

        L'application ne le fait QUE sur le terminal qu'elle livre elle-même
        (mode portable, dossier `runtime/mt5/terminal`) : toucher aux réglages
        d'un MetaTrader installé par l'utilisateur serait s'arroger une
        décision qui lui appartient.
        """
        import configparser
        dossier = Path(chemin_terminal).parent / "config"
        fichier = dossier / "common.ini"
        try:
            dossier.mkdir(parents=True, exist_ok=True)
            cfg = configparser.ConfigParser()
            cfg.optionxform = str            # MetaTrader distingue la casse
            if fichier.is_file():
                cfg.read(fichier, encoding="utf-16le")
                if not cfg.sections():       # certains terminaux écrivent en UTF-8
                    cfg.read(fichier, encoding="utf-8")
            if not cfg.has_section("Experts"):
                cfg.add_section("Experts")
            cfg.set("Experts", "AllowLiveTrading", "1")
            cfg.set("Experts", "Enabled", "1")
            # DLL externes laissées INTERDITES : l'application n'en a pas
            # besoin, et les autoriser ouvrirait le terminal à du code tiers.
            cfg.set("Experts", "AllowDllImport", "0")
            with open(fichier, "w", encoding="utf-16le") as f:
                cfg.write(f, space_around_delimiters=False)
            logger.info(f"[MT5] Trading automatique activé dans {fichier}")
            return True
        except Exception as e:
            logger.warning(f"[MT5] Activation du trading automatique impossible : {e}")
            return False

    def demarrer_terminal_embarque(self, attente_max: float = 90.0) -> Dict[str, Any]:
        """Démarre le terminal EMBARQUÉ en mode portable, et attend qu'il réponde.

        Sans cette étape, la connexion reposait entièrement sur
        `mt5.initialize(path=…)`, qui lance le terminal SANS l'option
        « /portable ». Un terminal livré avec l'application se serait donc
        ouvert en mode normal, avec un dossier de données ailleurs sur la
        machine — et, s'il tournait déjà en portable, la librairie en aurait
        démarré un SECOND. Le terminal refuse la double instance, la
        communication expire, et l'utilisateur ne voit qu'un « IPC timeout »
        alors que MetaTrader est ouvert sous ses yeux.

        On lance donc nous-mêmes, avec les bonnes options, puis on se branche
        sur l'instance obtenue par un `initialize()` nu.
        """
        chemin = self.terminal_embarque()
        if not chemin:
            return {"success": False, "error": "Aucun terminal MetaTrader embarqué"}

        # UN SEUL terminal, toujours. MetaTrader n'autorise qu'une réservation
        # du port de communication local : un second processus démarre, échoue
        # sur « MCP bind error […] 10048 », et rend la communication
        # impossible pour tout le monde. Si un terminal tourne déjà — le nôtre
        # ou celui de l'utilisateur — on s'en sert, on n'en ouvre pas un autre.
        en_cours = self.terminaux_en_cours()
        if en_cours > 0:
            if self._terminal_repond():
                return {"success": True, "deja_actif": True, "path": chemin,
                        "terminaux": en_cours}
            if en_cours > 1:
                return {"success": False, "path": chemin, "terminaux": en_cours,
                        "error": (f"{en_cours} terminaux MetaTrader 5 sont ouverts "
                                  f"en même temps. Un seul peut communiquer avec "
                                  f"l'application (les autres échouent sur « MCP "
                                  f"bind error »). Fermez-les TOUS, puis cliquez "
                                  f"« Démarrer / réparer la connexion ».")}
            return {"success": False, "path": chemin, "terminaux": en_cours,
                    "error": ("Un terminal MetaTrader 5 est ouvert mais ne répond "
                              "pas. Fermez ses boîtes de dialogue (en particulier "
                              "la fenêtre « Se connecter »), ou fermez-le "
                              "complètement puis réessayez.")}

        # Réglages appliqués AVANT le lancement : MetaTrader lit ses fichiers
        # de configuration au démarrage et les réécrit à la fermeture.
        self._autoriser_trading_automatique(chemin)

        argv = [chemin, "/portable"]
        logger.info(f"[MT5] Démarrage du terminal embarqué : {' '.join(argv)}")
        try:
            if platform.system() in ("Linux", "Darwin"):
                subprocess.Popen(["wine"] + argv)
            else:
                subprocess.Popen(argv, shell=False)
        except Exception as e:
            return {"success": False, "error": f"Lancement impossible : {e}"}

        # Le premier démarrage d'un terminal neuf prend du temps (création du
        # profil, mise à jour). On sonde jusqu'à ce qu'il réponde — la sonde
        # ne peut plus démarrer de terminal, cette boucle n'en multiplie donc
        # plus (c'était la cause des fenêtres MetaTrader « à l'infini »).
        fin = time.monotonic() + attente_max
        while time.monotonic() < fin:
            if self._terminal_repond(timeout_ms=5000):
                logger.info("[MT5] Terminal embarqué prêt")
                return {"success": True, "path": chemin,
                        "terminaux": self.terminaux_en_cours()}
            time.sleep(2)
        return {"success": False, "path": chemin,
                "terminaux": self.terminaux_en_cours(),
                "error": (f"Le terminal embarqué n'a pas répondu en "
                          f"{int(attente_max)}s. Ouvrez-le une fois à la main "
                          f"({chemin}) puis réessayez.")}

    def fermer_terminaux(self) -> Dict[str, Any]:
        """Ferme TOUS les terminaux MetaTrader 5, pour repartir proprement.

        Quand plusieurs terminaux se sont ouverts, aucun n'est utilisable : le
        premier tient le port de communication, les suivants échouent sur
        « MCP bind error ». Les fermer un par un à la souris est fastidieux —
        et certains sont réduits dans la barre des tâches.
        """
        avant = self.terminaux_en_cours()
        if avant <= 0:
            return {"success": True, "fermes": 0,
                    "message": "Aucun terminal MetaTrader ouvert."}
        # La communication est coupée d'abord : sinon la librairie garde une
        # référence sur un terminal qu'on est en train de fermer.
        try:
            import MetaTrader5 as mt5
            mt5.shutdown()
        except Exception:
            pass
        self._connected = False
        self._account_info = None
        try:
            if platform.system() == "Windows":
                subprocess.run(["taskkill", "/F", "/IM", "terminal64.exe"],
                               capture_output=True, timeout=20,
                               creationflags=0x08000000)
            else:
                subprocess.run(["pkill", "-f", "terminal64"],
                               capture_output=True, timeout=20)
        except Exception as e:
            return {"success": False, "error": f"Fermeture impossible : {e}"}

        for _ in range(10):                    # attendre la libération du port
            if self.terminaux_en_cours() == 0:
                break
            time.sleep(1)
        reste = self.terminaux_en_cours()
        logger.info(f"[MT5] Terminaux fermés : {avant} → {reste}")
        return {"success": reste == 0, "fermes": max(0, avant - max(reste, 0)),
                "restants": reste,
                "error": "" if reste == 0 else
                         f"{reste} terminal(aux) résiste(nt) — fermez-les à la main."}

    def launch(self) -> Dict[str, Any]:
        """Lance le terminal MT5."""
        path = self.find_path()
        if not path:
            return {
                "success": False,
                "installed": False,
                "error": "MetaTrader 5 n'est pas installé ou introuvable.",
            }

        # Un terminal embarqué se lance en mode PORTABLE : ses données
        # (profils, journaux) restent dans son propre dossier au lieu d'aller
        # se mélanger à une éventuelle installation MetaTrader de la machine.
        argv = [path]
        if path == self.terminal_embarque():
            argv.append("/portable")

        try:
            if platform.system() in ("Linux", "Darwin"):
                subprocess.Popen(["wine"] + argv)
            else:
                subprocess.Popen(argv, shell=False)
            logger.info(f"MT5 lancé : {path}")
            return {"success": True, "installed": True, "path": path}
        except FileNotFoundError as exc:
            return {"success": False, "installed": True, "path": path, "error": str(exc)}
        except Exception as exc:
            return {"success": False, "installed": True, "path": path, "error": str(exc)}

    def resoudre_serveur(self, demande: str) -> str:
        """Nom EXACT du serveur tel que le terminal le connaît.

        MetaTrader exige le nom au caractère près. Or le même serveur s'écrit
        « Ava-Demo 1-MT5 » dans la liste livrée avec l'application et
        « Ava - Demo 1-MT5 » dans la fenêtre du terminal, selon le courtier et
        la version : une simple espace autour du tiret suffisait à faire
        échouer la connexion, avec un message parlant du mot de passe.

        On compare donc sans espaces, tirets ni casse, et on renvoie
        l'orthographe du TERMINAL. Aucune correspondance : la saisie est
        rendue telle quelle — c'est peut-être un serveur légitime que le
        terminal découvrira lui-même.
        """
        demande = (demande or "").strip()
        if not demande:
            return demande
        try:
            connus = self.serveurs_connus()
        except Exception:
            return demande
        for nom in connus:
            if nom == demande:
                return nom                       # correspondance exacte
        cible = _normaliser_nom(demande)
        for nom in connus:
            if _normaliser_nom(nom) == cible:
                logger.info(f"Serveur « {demande} » reconnu comme « {nom} » "
                            f"(orthographe du terminal)")
                return nom
        return demande

    def serveurs_terminal(self) -> List[str]:
        """Serveurs lus SUR LE DISQUE, dans l'installation MetaTrader.

        Distinct de `serveurs_connus()`, qui y ajoute les comptes mémorisés :
        l'interface annonce « détectés sur votre terminal », ce qui doit
        désigner le terminal et lui seul.
        """
        return self._serveurs(inclure_comptes=False)

    def serveurs_connus(self) -> List[str]:
        """Serveurs que le terminal MetaTrader 5 connaît RÉELLEMENT, lus sur
        le disque, plus ceux des comptes déjà mémorisés.

        La fenêtre de connexion proposait une liste FIGÉE de six noms écrits
        dans le HTML. AvaTrade en exploite d'autres selon l'entité et la région
        (« Ava-Real 2-MT5 », « AvaTradeEU-Real »…) : quiconque possédait un
        compte hors de ces six noms n'avait tout simplement AUCUN moyen de
        saisir le sien. On lit donc les serveurs que le terminal a déjà vus —
        MetaTrader crée un dossier par serveur sous `bases/` — et on les
        complète par ceux des comptes enregistrés dans l'application.
        """
        return self._serveurs(inclure_comptes=True)

    def _serveurs(self, inclure_comptes: bool) -> List[str]:
        trouves: List[str] = []

        def _ajouter(nom):
            nom = str(nom or "").strip()
            if nom and nom not in trouves:
                trouves.append(nom)

        # 1) Dossiers `bases/<NomDuServeur>` des installations MetaTrader
        racines = []
        appdata = os.environ.get("APPDATA")
        if appdata:
            racines.append(os.path.join(appdata, "MetaQuotes", "Terminal"))
        chemin_exe = self.find_path()
        if chemin_exe:
            racines.append(os.path.dirname(chemin_exe))
        for racine in racines:
            for motif in (os.path.join(racine, "*", "bases", "*"),
                          os.path.join(racine, "bases", "*")):
                try:
                    for dossier in glob.glob(motif):
                        if os.path.isdir(dossier) and _est_dossier_serveur(dossier):
                            _ajouter(os.path.basename(dossier))
                except Exception:
                    continue

        # 2) Serveurs des comptes déjà mémorisés dans l'application
        if not inclure_comptes:
            return trouves
        try:
            from utils import app_config
            for compte in (app_config.get("comptes") or []):
                if isinstance(compte, dict):
                    _ajouter(compte.get("server"))
            _ajouter((app_config.get("mt5_creds") or {}).get("server"))
        except Exception:
            pass
        return trouves

    def est_reel(self) -> bool:
        """Vrai si les ordres partent VRAIMENT au courtier (MetaTrader 5
        connecté, hors simulation hors-ligne) — compte démo INCLUS.

        Sert à interdire tout ordre transmis au courtier sur des données de
        marché simulées. Ne dit PAS si de l'argent réel est engagé : voir
        `est_demo()`, qui distingue démo et compte financé."""
        if self._lib_available and self._connected:
            return not (self._account_info or {}).get("simulated", False)
        return False

    def algotrading_actif(self) -> Optional[bool]:
        """Le bouton « AlgoTrading » du terminal MetaTrader 5 est-il activé ?

        None si l'information n'est pas disponible (librairie absente, non
        connecté). C'est LE point de blocage le plus courant : sans ce bouton,
        le terminal refuse TOUS les ordres du robot avec le code 10027, et
        l'utilisateur ne voit qu'un « AutoTrading disabled by client » qui ne
        dit pas où cliquer. On le vérifie donc AVANT d'envoyer, et on le
        signale en permanence dans le panneau de trading."""
        if not (self._lib_available and self._connected):
            return None
        try:
            import MetaTrader5 as mt5
            # Non bloquant : remonté à CHAQUE sondage du panneau de trading.
            # Bloquer ici gelait le tableau de bord pendant une connexion.
            with self._lecture() as libre:
                if not libre:
                    return self._algotrading_cache
                info = mt5.terminal_info()
            if info is None:
                return self._algotrading_cache
            self._algotrading_cache = bool(getattr(info, "trade_allowed", False))
            return self._algotrading_cache
        except Exception:
            return self._algotrading_cache

    def est_demo(self) -> bool:
        """Vrai si le compte connecté est un compte de DÉMONSTRATION.

        Distinct d'`est_reel()` : un compte démo AvaTrade est bien une vraie
        connexion MetaTrader (les ordres partent au courtier), mais l'argent
        est fictif. Les confondre faisait annoncer « Compte RÉEL connecté —
        chaque signal engagera de l'argent réel » sur un serveur Ava-Demo :
        un avertissement faux, qui décrédibilise le vrai le jour où il compte.
        """
        ai = self._account_info or {}
        if not self._connected:
            return False
        if "demo" in ai:
            return bool(ai["demo"])
        return ("demo" in str(ai.get("server", "")).lower()
                or str(ai.get("account_type", "demo")).lower() != "real")

    def equity_live(self) -> float:
        """Équité COURANTE lue en direct (fallback sur la valeur en cache).

        Le plafonnement du volume par le risque doit se calculer sur l'équité
        du moment : une équité périmée (drawdown en cours) sur-dimensionnerait
        l'ordre et ferait dépasser la perte-au-stop autorisée."""
        cached = float((self._account_info or {}).get("equity") or 0)
        if self._lib_available and self._connected:
            try:
                import MetaTrader5 as mt5
                # _mt5_lock est un RLock : sûr même quand l'appelant le détient
                # déjà (_send_order_real), et indispensable quand il ne le
                # détient pas (pré-vol lancé pendant que la boucle trade).
                with self._mt5_lock:
                    info = mt5.account_info()
                if info and getattr(info, "equity", None):
                    return float(info.equity)
            except Exception:
                pass
        return cached

    # ── Connexion / déconnexion ───────────────────────────────────────
    def connect(self, account: int, password: str, account_type: str, server: str = "") -> Dict[str, Any]:
        """Connexion au compte MT5 (réelle ou simulée)."""
        # Un nouveau courtier peut nommer ses symboles autrement
        self._symbol_cache.clear()
        if not server:
            server = _AVATRADE_SERVERS.get(account_type, "AvaTrade-Demo")

        # Opération LONGUE : le lancement du terminal peut durer une minute.
        # Le libellé remonte au tableau de bord pour qu'il explique l'attente
        # (« Connexion à Ava-Demo 1-MT5… ») au lieu de paraître figé.
        with self._operation_longue(f"Connexion à {server}…"):
            if self._lib_available:
                r = self._connect_real(account, password, server, account_type)
            else:
                r = self._connect_simulated(account, password, server, account_type)
        # Mémoriser les identifiants pour la reconnexion automatique
        if r.get("success") and account and password:
            self.enregistrer_identifiants(account, password, account_type, server)
            # Rendre le compte basculable (multi-comptes)
            try:
                from utils import comptes
                comptes.memoriser_mt5(account, password, account_type, server)
            except Exception:
                pass
        return r

    def connecter_terminal_courant(self) -> Dict[str, Any]:
        """Reprend le compte DÉJÀ OUVERT dans MetaTrader 5 — sans identifiants.

        C'est le chemin le plus simple et le plus sûr : si l'utilisateur est
        déjà connecté à son compte AvaTrade dans le terminal (le cas normal :
        MetaTrader mémorise la session), il n'y a aucune raison de lui
        redemander numéro, mot de passe et nom exact du serveur — trois
        occasions de se tromper — ni de rejouer un login() qui pourrait
        casser une session qui fonctionne.
        """
        if not self._lib_available:
            return {"success": False,
                    "error": ("La librairie MetaTrader5 n'est pas disponible. "
                              "Installez MetaTrader 5 (Réglages ⚙️) puis relancez.")}
        self._symbol_cache.clear()
        with self._operation_longue("Lecture du compte ouvert dans MetaTrader 5…"):
            r = self._connect_real(0, "", "", "demo", adopter=True)
        if r.get("success"):
            infos = r.get("account_info") or {}
            logger.info(f"Compte repris depuis le terminal : {infos.get('account')} "
                        f"({infos.get('server')})")
            # Pas de mot de passe en mémoire : la reconnexion automatique au
            # prochain démarrage repassera par le terminal, comme ici.
            try:
                from utils import app_config
                app_config.update(mt5_attacher=True)
            except Exception:
                pass
        return r

    # ── Mémorisation des identifiants (dossier de config local) ───────
    @staticmethod
    def _obfusquer(txt: str) -> str:
        """Obfuscation locale réversible (pas du chiffrement fort : le
        fichier reste sur la machine de l'utilisateur, comme MetaTrader)."""
        import base64
        cle = b"AgenceNumeriqueFinanciere"
        b = txt.encode("utf-8")
        x = bytes(c ^ cle[i % len(cle)] for i, c in enumerate(b))
        return base64.b64encode(x).decode("ascii")

    @staticmethod
    def _desobfusquer(txt: str) -> str:
        import base64
        cle = b"AgenceNumeriqueFinanciere"
        try:
            x = base64.b64decode(txt.encode("ascii"))
            return bytes(c ^ cle[i % len(cle)] for i, c in enumerate(x)).decode("utf-8")
        except Exception:
            return ""

    def enregistrer_identifiants(self, account, password, account_type, server):
        try:
            from utils import app_config
            app_config.update(mt5_creds={
                "account": str(account),
                "password": self._obfusquer(str(password)),
                "account_type": account_type,
                "server": server,
            })
        except Exception as e:
            logger.warning(f"Identifiants non enregistrés : {e}")

    def identifiants_enregistres(self) -> Dict[str, Any]:
        try:
            from utils import app_config
            c = app_config.get("mt5_creds") or {}
            return {"account": c.get("account", ""), "server": c.get("server", ""),
                    "account_type": c.get("account_type", "demo"),
                    "enregistres": bool(c.get("account") and c.get("password"))}
        except Exception:
            return {"enregistres": False}

    def oublier_identifiants(self) -> Dict[str, Any]:
        try:
            from utils import app_config
            app_config.update(mt5_creds={})
        except Exception:
            pass
        return {"success": True}

    def reconnexion_auto(self) -> Dict[str, Any]:
        """Reconnecte automatiquement au démarrage.

        Si l'utilisateur a choisi de reprendre le compte ouvert dans le
        terminal, on refait exactement ça — aucun identifiant n'a été
        mémorisé, et il n'y a rien à ressaisir.
        """
        try:
            from utils import app_config
            if app_config.get("mt5_attacher"):
                r = self.connecter_terminal_courant()
                if r.get("success"):
                    return r
            c = app_config.get("mt5_creds") or {}
            if not (c.get("account") and c.get("password")):
                return {"success": False, "error": "Aucun identifiant enregistré"}
            mdp = self._desobfusquer(c["password"])
            if not mdp:
                return {"success": False, "error": "Identifiants illisibles"}
            try:
                account = int(str(c["account"]).strip())
            except ValueError:
                return {"success": False, "error": "Compte invalide"}
            r = self.connect(account, mdp, c.get("account_type", "demo"), c.get("server", ""))
            if r.get("success"):
                logger.info("Reconnexion automatique AvaTrade réussie")
            return r
        except Exception as e:
            return {"success": False, "error": str(e)}

    def _echec_connexion(self, mt5, serveur: str, etapes: List[str]) -> Dict[str, Any]:
        """Termine proprement une connexion ratée.

        IMPÉRATIF : remettre l'état à « non connecté ». On vient d'appeler
        `mt5.shutdown()`, donc le lien terminal est fermé — mais `_connected`
        et `_account_info` gardaient les valeurs de la session PRÉCÉDENTE. Le
        tableau de bord affichait alors « Connecté » sur un lien mort, les
        lectures renvoyaient des chiffres périmés, et plus aucune connexion ne
        semblait possible jusqu'au redémarrage de l'application.
        """
        try:
            mt5.shutdown()
        except Exception:
            pass
        self._connected = False
        self._account_info = None
        self._auto_trading = False
        self._algotrading_cache = None
        self._positions_cache = []

        connus = self.serveurs_connus()
        conseil = ""
        for etape in etapes:
            conseil = _conseil_init(etape.get("erreur")) or conseil
            if conseil:
                break

        # ── Causes que l'on peut NOMMER, par ordre de certitude ──────────
        # L'ordre suit la CHRONOLOGIE d'une connexion : on ne peut diagnostiquer
        # le serveur que si l'on a d'abord réussi à parler au terminal. Un
        # « IPC timeout » signifie que la communication n'a jamais abouti — le
        # nom du serveur n'y est alors pour rien, et le mettre en avant
        # enverrait corriger un réglage correct.
        ouverts = self.terminaux_en_cours()
        cible = _normaliser_nom(serveur or "")
        connus_normalises = {_normaliser_nom(n) for n in connus}
        # Erreurs de COMMUNICATION avec le terminal (initialize), par
        # opposition à un refus du courtier (login).
        panne_communication = any(
            _code_erreur(e.get("erreur")) in (-10005, -10004, -10003, -10002, -10001)
            for e in etapes)

        if ouverts > 1:
            # Un seul terminal peut réserver le port de communication local ;
            # les autres échouent sur « MCP bind error […] 10048 » et rendent
            # la communication impossible.
            aide = (f"{ouverts} terminaux MetaTrader 5 sont ouverts en même temps. "
                    f"Un seul peut communiquer avec l'application. Fermez-les TOUS "
                    f"(y compris ceux réduits dans la barre des tâches), puis "
                    f"cliquez « Démarrer / réparer la connexion » dans Réglages ⚙️.")
        elif panne_communication and conseil:
            # La communication avec le terminal n'a jamais abouti : tout
            # diagnostic sur le serveur ou les identifiants serait prématuré.
            aide = conseil
        elif conseil:
            # Verdict explicite du courtier (identifiants refusés, version
            # incompatible…) : il prime, car il vient de la source. Mais si le
            # terminal ne connaît pas non plus le serveur demandé, c'est une
            # piste décisive qu'on AJOUTE au lieu de la taire — un terminal
            # sans les serveurs du courtier refuse aussi bien un bon mot de
            # passe qu'un mauvais.
            aide = conseil + self._note_serveur_inconnu(
                serveur, connus, cible, connus_normalises)
        elif serveur and (not connus or cible not in connus_normalises):
            aide = ("La connexion a échoué sans verdict du courtier."
                    + self._note_serveur_inconnu(
                        serveur, connus, cible, connus_normalises)).strip()
        elif connus:
            aide = (f"Vérifiez le numéro de compte et le mot de passe. "
                    f"Serveurs connus de votre terminal : {', '.join(connus[:8])}.")
        else:
            aide = ("Ouvrez MetaTrader 5 → Fichier → Ouvrir un compte pour voir le "
                    "nom exact du serveur de votre courtier.")
        detail = " ; ".join(e.get("texte", "") for e in etapes) or "cause inconnue"
        detail += (f" | librairie MetaTrader5 {version_librairie()}"
                   f" | terminaux ouverts : {ouverts}")
        return {"success": False,
                "error": f"Connexion au compte impossible. {aide}",
                "detail": detail,
                "librairie": version_librairie(),
                "terminaux_ouverts": ouverts,
                "serveurs_connus": connus}

    @staticmethod
    def _note_serveur_inconnu(serveur, connus, cible, connus_normalises) -> str:
        """Complément d'explication quand le terminal ignore le serveur demandé.

        Cas typique d'un terminal MetaTrader générique (MetaQuotes) livré sans
        les serveurs du courtier : le nom saisi est correct, mais CE
        terminal-là ne sait pas où joindre AvaTrade. Rien à voir avec le mot
        de passe, que le message d'erreur du courtier met pourtant en cause.
        """
        if not serveur:
            return ""
        if not connus:
            return (f" À noter : votre terminal MetaTrader ne connaît encore "
                    f"AUCUN serveur de courtier, il ne peut donc pas joindre "
                    f"« {serveur} ». Ouvrez MetaTrader 5 → Fichier → Ouvrir un "
                    f"compte, cherchez « AvaTrade » et validez : le terminal "
                    f"télécharge alors les serveurs.")
        if cible not in connus_normalises:
            return (f" À noter : votre terminal ne connaît pas le serveur "
                    f"« {serveur} » — seulement {', '.join(connus[:8])}. Ouvrez "
                    f"MetaTrader 5 → Fichier → Ouvrir un compte, tapez "
                    f"« AvaTrade » et validez pour télécharger les serveurs du "
                    f"courtier.")
        return ""

    @_sous_verrou
    def _connect_real(self, account: int, password: str, server: str,
                      account_type: str, adopter: bool = False) -> Dict[str, Any]:
        """Connexion au terminal MetaTrader 5.

        `adopter=True` : on se contente du compte DÉJÀ ouvert dans le terminal,
        sans identifiants (voir connecter_terminal_courant).
        """
        import MetaTrader5 as mt5

        delai = int(os.getenv("MT5_INIT_TIMEOUT_MS", "30000"))
        budget = float(os.getenv("MT5_CONNEXION_BUDGET_S", "75"))
        etapes: List[Dict[str, Any]] = []

        # ÉTAPE 0 — s'assurer qu'un terminal TOURNE avant toute tentative.
        # Le terminal embarqué doit être lancé PAR NOUS, avec « /portable » :
        # laisser la librairie s'en charger (via `path=`) le démarrerait en
        # mode normal, ou en ouvrirait un second à côté de celui qui tourne —
        # le terminal refuse la double instance et la communication expire.
        # Ce démarrage est hors budget de connexion : c'est une préparation,
        # pas une tentative, et il ne doit pas consommer le temps accordé aux
        # stratégies qui suivent.
        portable = self.terminal_embarque()
        if portable and not self._terminal_repond(timeout_ms=3000):
            prepare = self.demarrer_terminal_embarque()
            if not prepare.get("success"):
                _noter_demarrage = prepare.get("error", "")
                if _noter_demarrage:
                    etapes.append({"texte": f"démarrage du terminal embarqué → "
                                            f"{_noter_demarrage}",
                                   "erreur": None})

        # Orthographe du serveur alignée sur celle du terminal AVANT toute
        # tentative : « Ava-Demo 1-MT5 » et « Ava - Demo 1-MT5 » désignent le
        # même serveur, mais MetaTrader n'accepte que le sien.
        if server and not adopter:
            exact = self.resoudre_serveur(server)
            if exact != server:
                etapes.append({"texte": f"serveur « {server} » → « {exact} » "
                                        f"(orthographe du terminal)",
                               "erreur": None})
                server = exact

        echeance = time.monotonic() + budget

        def _noter(quoi: str, err):
            etapes.append({"texte": f"{quoi} → {err}", "erreur": err})

        def _delai_restant() -> int:
            """Délai (ms) accordé à l'appel SUIVANT, borné par le budget total."""
            reste = echeance - time.monotonic()
            return max(2000, min(delai, int(reste * 1000)))

        def _budget_epuise() -> bool:
            return time.monotonic() >= echeance

        def _fermer():
            try:
                mt5.shutdown()
            except Exception:
                pass

        # ── Échelle de stratégies, de la PLUS SIMPLE à la plus intrusive ──
        # L'ancienne version passait TOUJOURS `path=…`, donc n'essayait jamais
        # `mt5.initialize()` nu — le seul appel qui se contente de se brancher
        # sur le terminal DÉJÀ EN COURS. Quand le chemin trouvé n'était pas
        # celui du terminal qui tourne (plusieurs MetaTrader installés, dossier
        # non standard), la librairie tentait d'en lancer un SECOND : le
        # terminal refuse la double instance, et la communication expirait —
        # « (-10005, 'IPC timeout') », terminal ouvert et connecté à l'écran.
        strategies: List[Dict[str, Any]] = [
            {"nom": "terminal en cours", "kw": {}},
        ]
        if not adopter:
            strategies.append(
                {"nom": "identifiants sans chemin",
                 "kw": {"login": int(account), "password": password, "server": server}})
        for chemin in self.find_paths():
            # Le terminal EMBARQUÉ est exclu de ces stratégies : `path=` le
            # relancerait sans « /portable » — donc une seconde instance,
            # refusée par MetaTrader, et un délai de communication dépassé.
            # Il tourne déjà (étape 0) et se joint par « terminal en cours ».
            if portable and os.path.normcase(chemin) == os.path.normcase(portable):
                continue
            strategies.append({"nom": f"terminal {os.path.basename(chemin)}",
                               "kw": {"path": chemin}})
            if not adopter:
                strategies.append(
                    {"nom": f"identifiants via {os.path.basename(chemin)}",
                     "kw": {"path": chemin, "login": int(account),
                            "password": password, "server": server}})

        ouvert = False
        depassement = False
        for strategie in strategies:
            if _budget_epuise():
                depassement = True
                break
            _fermer()                      # repartir d'une communication propre
            try:
                ouvert = bool(mt5.initialize(timeout=_delai_restant(),
                                             **strategie["kw"]))
                if not ouvert:
                    _noter(f"initialize [{strategie['nom']}]", mt5.last_error())
            except Exception as e:
                _noter(f"initialize [{strategie['nom']}]", e)
            if not ouvert:
                continue

            # Communication établie. Le terminal est-il DÉJÀ sur le bon compte ?
            # Si oui — cas courant : l'utilisateur s'y est connecté lui-même —
            # il n'y a plus rien à faire. Rejouer un login() ne ferait que
            # risquer de casser une session qui fonctionne.
            courant = None
            try:
                courant = mt5.account_info()
            except Exception:
                courant = None
            if adopter:
                if courant is not None:
                    break
                _noter(f"account_info [{strategie['nom']}]", mt5.last_error())
                ouvert = False
                continue
            if courant is not None and int(getattr(courant, "login", 0)) == int(account):
                logger.info(f"Terminal déjà connecté au compte {account} — "
                            f"aucun login nécessaire")
                break

            # Compte différent : basculer.
            if _budget_epuise():
                depassement = True
                break
            try:
                bascule = bool(mt5.login(int(account), password=password,
                                         server=server, timeout=_delai_restant()))
                if not bascule:
                    _noter(f"login [{strategie['nom']}]", mt5.last_error())
            except Exception as e:
                _noter(f"login [{strategie['nom']}]", e)
                bascule = False
            if bascule:
                break
            ouvert = False
            # Identifiants refusés : changer de stratégie n'y changera rien.
            if _code_erreur(etapes[-1].get("erreur")) == -6:
                break

        if not ouvert:
            if depassement:
                _noter("budget de connexion dépassé",
                       f"aucune réponse du terminal en {budget:.0f}s")
            return self._echec_connexion(mt5, server, etapes)

        info = mt5.account_info()
        if info is None:
            _noter("account_info()", mt5.last_error())
            return self._echec_connexion(mt5, server, etapes)

        # ACCOUNT_TRADE_MODE : 0 = DEMO, 1 = CONCOURS, 2 = RÉEL. C'est le
        # courtier qui le déclare — bien plus fiable que le bouton radio
        # démo/réel de la fenêtre de connexion, que l'utilisateur peut laisser
        # sur le mauvais choix. Repli sur le nom du serveur (« Ava-Demo… »).
        trade_mode = getattr(info, "trade_mode", None)
        if trade_mode is not None:
            demo = trade_mode != 2
        else:
            demo = ("demo" in str(info.server).lower()
                    or str(account_type).lower() != "real")

        self._connected = True
        self._account_info = {
            "account":      str(info.login),
            "name":         info.name,
            "server":       info.server,
            "balance":      round(info.balance, 2),
            "equity":       round(info.equity, 2),
            "margin":       round(info.margin, 2),
            "free_margin":  round(info.margin_free, 2),
            "currency":     info.currency,
            "leverage":     info.leverage,
            "account_type": "demo" if demo else "real",
            "demo":         demo,
            "simulated":    False,   # connexion RÉELLE MetaTrader 5
        }
        logger.info(f"MT5 connecté : compte {info.login} ({server})")
        return {"success": True, "account_info": self._account_info}

    def _connect_simulated(self, account: int, password: str, server: str, account_type: str) -> Dict[str, Any]:
        if len(str(account)) < 5 or len(password) < 4:
            return {"success": False, "error": "Numéro de compte ou mot de passe invalide"}

        balance = 10_000.0 if account_type == "demo" else 25_000.0
        self._connected = True
        self._account_info = {
            "account":      str(account),
            "name":         "Compte AvaTrade",
            "server":       server,
            "balance":      balance,
            "equity":       balance,
            "margin":       0.0,
            "free_margin":  balance,
            "currency":     "USD",
            "leverage":     400,
            "account_type": account_type,
            "simulated":    True,
        }
        logger.info(f"MT5 simulé : compte {account} ({server})")
        return {"success": True, "account_info": self._account_info}

    def disconnect(self) -> Dict[str, Any]:
        if self._lib_available and self._connected:
            try:
                import MetaTrader5 as mt5
                # SOUS VERROU : shutdown() ferme la connexion terminal, qui est
                # unique et non thread-safe. Sans lui, il pouvait s'exécuter en
                # plein order_send de la boucle auto-trader.
                with self._mt5_lock:
                    mt5.shutdown()
            except Exception as exc:
                logger.warning(f"Arrêt MT5 : {exc}")

        self._connected = False
        self._account_info = None
        self._auto_trading = False
        self._algotrading_cache = None
        self._positions_cache = []
        self._historique_cache = {}
        logger.info("Compte déconnecté")
        return {"success": True}

    def fermer_ticket(self, ticket) -> Dict[str, Any]:
        """Ferme une position par son ticket, quel que soit le backend."""
        # Le ticket peut arriver en chaîne (JSON/URL) : sans coercition,
        # `p.ticket (int) == ticket (str)` est TOUJOURS faux → la position
        # ne peut plus être fermée (perte laissée courir).
        try:
            ticket_int = int(ticket)
        except (TypeError, ValueError):
            return {"success": False, "error": "Ticket invalide"}

        if self._lib_available:
            import MetaTrader5 as mt5
            with self._mt5_lock:
                positions = mt5.positions_get()
            for p in (positions or []):
                if p.ticket == ticket_int:
                    return self.fermer_position(p)
            return {"success": False, "error": "position introuvable"}

        # ── Simulation : les positions dérivent du journal ────────────
        # Sans ce chemin, le bouton « Fermer » du tableau de bord échouait
        # TOUJOURS en mode simulation (« position introuvable ») : impossible
        # de s'entraîner à clôturer avant de passer en réel.
        with self._journal_lock:
            ouverte = next(
                (e for e in self._journal
                 if e.get("success") and e.get("action") in ("BUY", "SELL")
                 and str(e.get("ticket")) == str(ticket_int)),
                None,
            )
            if ouverte is None:
                return {"success": False, "error": "position introuvable"}
            self._journal.insert(0, {
                "timestamp": datetime.now().strftime("%H:%M:%S"),
                "symbol":    ouverte.get("symbol"),
                "action":    "CLOSE",
                "volume":    ouverte.get("volume", 0.01),
                "price":     ouverte.get("price", 0),
                "ticket":    ouverte.get("ticket"),
                "success":   True,
                "error":     None,
                "simulated": True,
            })
            if len(self._journal) > 200:
                self._journal = self._journal[:200]
        logger.info(f"Position simulée {ticket_int} ({ouverte.get('symbol')}) fermée")
        return {"success": True, "ticket": ticket_int, "simulated": True}

    # ── Statut ───────────────────────────────────────────────────────
    def get_status(self) -> Dict[str, Any]:
        if not self._connected:
            return {"connected": False, "auto_trading": False,
                    "operation": self._operation}

        occupe = False
        if self._lib_available:
            try:
                import MetaTrader5 as mt5
                # Lecture NON BLOQUANTE : sondée toutes les 3 s par le tableau
                # de bord, donc en concurrence avec les order_send de
                # l'auto-trader ET avec une éventuelle connexion en cours. Si
                # le terminal est monopolisé, on rend les derniers chiffres
                # connus plutôt que de faire attendre la requête (cf._lecture).
                with self._lecture() as libre:
                    info = mt5.account_info() if libre else None
                occupe = not libre
                if info and self._account_info:
                    self._account_info.update({
                        "balance":     round(info.balance, 2),
                        "equity":      round(info.equity, 2),
                        "margin":      round(info.margin, 2),
                        "free_margin": round(info.margin_free, 2),
                    })
            except Exception as exc:
                logger.warning(f"Mise à jour statut MT5 : {exc}")

        return {
            "connected":    True,
            "account_info": self._account_info,
            "auto_trading": self._auto_trading,
            # Chiffres possiblement légèrement périmés : le terminal était
            # occupé (connexion, envoi d'ordre). L'interface le signale.
            "occupe":       occupe,
            "operation":    self._operation,
        }

    # ── Trading automatique ──────────────────────────────────────────
    def set_auto_trading(self, active: bool) -> Dict[str, Any]:
        if not self._connected:
            return {"success": False, "error": "Connectez-vous à un compte AvaTrade avant d'activer le trading automatique"}
        self._auto_trading = active
        logger.info(f"Trading automatique {'activé' if active else 'désactivé'}")
        return {"success": True, "auto_trading": active}

    # ── Exécution de trades ──────────────────────────────────────────
    def execute_trade(self, signal: Dict) -> Dict[str, Any]:
        if not self._connected:
            return {"success": False, "error": "Non connecté au compte AvaTrade"}

        # Valider l'action AVANT tout aiguillage : une valeur inattendue ne doit
        # jamais être interprétée comme un ordre.
        action = str(signal.get("action") or "").strip().upper()
        if action not in ("BUY", "SELL"):
            return {"success": False,
                    "error": f"Action invalide : {signal.get('action')!r} (BUY ou SELL attendu)"}

        # Le SYMBOLE doit être du texte. `/api/mt5/trade` accepte un JSON libre :
        # une liste ou un objet y était journalisé tel quel, puis get_positions()
        # levait « unhashable type » à CHAQUE appel suivant (positions du
        # tableau de bord définitivement cassées, y compris les vraies), et
        # sauver_ordre() échouait sur le binding SQLite.
        symbole = signal.get("symbol")
        if not isinstance(symbole, str) or not symbole.strip():
            return {"success": False,
                    "error": f"Symbole invalide : {symbole!r} (texte attendu)"}

        # Volume et prix : numériques finis, sinon l'arrondi du journal
        # (`round(prix, 5)`) lève TypeError sur une chaîne, rendu en 500.
        volume = _nombre_fini(signal.get("volume"), defaut=0.01)
        if volume is None or volume <= 0:
            return {"success": False,
                    "error": f"Volume invalide : {signal.get('volume')!r}"}
        prix_demande = _nombre_fini(signal.get("prix_entree"), defaut=0.0)

        # SL/TP et LEVIER : mêmes garanties que le volume et le prix. Le corps
        # de /api/mt5/trade est un JSON LIBRE, et la décision d'un agent peut
        # porter un niveau NaN : plus bas, `float(signal.get("stop_loss") or 0)`
        # et `float(levier)` levaient ValueError/TypeError EN PLEIN ENVOI
        # D'ORDRE, hors de tout `except`. L'ordre n'était alors ni envoyé ni
        # journalisé, et l'appelant ne recevait qu'un « Erreur interne » sans
        # rapport avec la vraie cause. Une valeur inexploitable est traitée
        # comme ABSENTE : les stops par défaut la remplacent, et le levier
        # retombe sur le réglage courant.
        sl_demande = _nombre_fini(signal.get("stop_loss"), defaut=0.0) or 0.0
        tp_demande = _nombre_fini(signal.get("take_profit"), defaut=0.0) or 0.0
        levier_demande = _nombre_fini(signal.get("levier"), defaut=None)

        signal = {**signal, "action": action, "symbol": symbole.strip(),
                  "volume": volume, "prix_entree": prix_demande or 0.0,
                  "stop_loss": max(0.0, sl_demande),
                  "take_profit": max(0.0, tp_demande),
                  "levier": levier_demande}

        if self._lib_available:
            with self._mt5_lock:
                result = self._send_order_real(signal)
            reel = True
        else:
            # Simulation autorisée uniquement si explicitement activée
            if os.getenv("AUTORISER_SIMULATION", "true").lower() not in ("1", "true", "yes", "oui"):
                return {"success": False,
                        "error": ("Aucun compte réel connecté. Installez MetaTrader 5 "
                                  "(Réglages) puis connectez votre compte AvaTrade pour "
                                  "envoyer de vrais ordres.")}
            result = self._send_order_simulated(signal)
            reel = False

        entry = {
            "timestamp": datetime.now().strftime("%H:%M:%S"),
            "symbol":    signal.get("symbol", "?"),
            "action":    signal.get("action", "?"),
            "volume":    signal.get("volume", 0.01),
            "price":     result.get("price", 0),
            "ticket":    result.get("ticket"),
            "success":   result.get("success", False),
            "error":     result.get("error"),
            "simulated": not reel,
        }
        with self._journal_lock:
            self._journal.insert(0, entry)
            if len(self._journal) > 200:
                self._journal = self._journal[:200]

        # Persistance : l'historique survit au redémarrage
        try:
            from utils.database import Database
            Database().sauver_ordre(entry)
        except Exception as e:
            logger.warning(f"Ordre non persisté : {e}")

        return result

    def suggerer_symboles(self, symbole_yahoo: str, limite: int = 5) -> List[str]:
        """Noms RÉELS du courtier ressemblant au symbole demandé.

        Quand la résolution échoue, l'utilisateur a besoin du nom EXACT utilisé
        par son courtier (AvaTrade nomme par ex. le Bitcoin « BTCUSD »),
        pas d'une invitation à chercher lui-même."""
        if not self._lib_available:
            return []
        try:
            import MetaTrader5 as mt5
            base = _normaliser_nom((symbole_yahoo or "")
                                   .replace("=X", "").replace("=F", "")
                                   .replace("^", ""))
            if not base:
                return []
            # Racine : « BTCUSD » -> « BTC » (permet de retrouver BTCUSD.a, BTCUSDm…)
            racine = base[:-3] if len(base) > 4 and base.endswith("USD") else base
            # Instrument aliasé : « GC=F » réduit à « GC » ne ressemble à rien
            # dans le Market Watch — et « GC » se retrouverait dans la moitié
            # des noms du courtier. On cherche alors sous ses vrais noms
            # (GOLD, XAUUSD…) et sous eux SEULEMENT.
            alias = [_normaliser_nom(a) for a in _alias_courtier(symbole_yahoo)]
            trouves = []
            # SOUS VERROU : symbols_get() parle au terminal, comme order_send.
            # Cette suggestion est produite juste APRÈS un échec de résolution,
            # donc en plein cycle de trading.
            with self._mt5_lock:
                symboles = list(mt5.symbols_get() or [])
            for s in symboles:
                nom = s.name
                norm = nom.upper()
                for c in ("-", ".", "_", "#", "/", " "):
                    norm = norm.replace(c, "")
                if alias:
                    proche = any(norm.startswith(a) or a in norm for a in alias)
                else:
                    proche = (norm.startswith(base) or base in norm
                              or norm.startswith(racine))
                if proche:
                    trouves.append(nom)
                    if len(trouves) >= limite:
                        break
            return trouves
        except Exception as e:
            logger.warning(f"Suggestion de symboles {symbole_yahoo} : {e}")
            return []

    def resoudre_symbole(self, symbole_yahoo: str) -> Optional[str]:
        """Traduit un symbole Yahoo (BTC-USD, EURUSD=X, AAPL) en symbole MT5
        réellement disponible chez le courtier, et le sélectionne dans le
        Market Watch. Retourne None si aucun équivalent n'existe.

        Prend `_mt5_lock` (RLock) : symbol_info/symbols_get/symbol_select
        parlent au terminal, et cette méthode est appelée aussi bien depuis le
        chemin d'ordre (verrou déjà tenu — d'où le RLock) que depuis le pré-vol,
        qui peut tourner PENDANT que la boucle auto-trader envoie un ordre.
        """
        import MetaTrader5 as mt5

        if symbole_yahoo in self._symbol_cache:
            return self._symbol_cache[symbole_yahoo]
        with self._mt5_lock:
            return self._resoudre_symbole_verrouille(mt5, symbole_yahoo)

    @_sous_verrou
    def _resoudre_symbole_verrouille(self, mt5, symbole_yahoo: str) -> Optional[str]:
        # Re-vérification sous verrou : un autre thread a pu résoudre le même
        # symbole pendant l'attente (évite une seconde recherche complète).
        if symbole_yahoo in self._symbol_cache:
            return self._symbol_cache[symbole_yahoo]

        alias = _alias_courtier(symbole_yahoo)
        if alias:
            # Instrument aliasé (or, pétrole…) : on cherche UNIQUEMENT sous les
            # noms déclarés. Aucune dérivation générique, donc aucun risque de
            # tomber sur un autre instrument — « CL=F » réduit à « CL » ferait
            # partir un ordre RÉEL sur Colgate-Palmolive.
            candidats = list(alias)
            cibles = [_normaliser_nom(a) for a in alias]
        else:
            base = symbole_yahoo.upper().replace("=X", "").replace("^", "")
            # Clé de comparaison NORMALISÉE exactement comme les noms du courtier
            # (mêmes caractères retirés). Sans cette symétrie, un symbole contenant
            # un point ou un underscore (BRK.B, RDS.A, BF_B) ne peut jamais
            # correspondre — même quand le courtier propose le symbole identique.
            sans_tiret = _normaliser_nom(base)

            candidats = [symbole_yahoo, base, base.replace("-", ""), sans_tiret]
            for prefixe in ("#", "."):
                candidats.append(prefixe + sans_tiret)
            for suffixe in (".NAS", ".NYSE", "m", ".r", "-USD", "USD"):
                candidats.append(sans_tiret + suffixe)
            cibles = [sans_tiret]

        # 1) essais directs
        for c in candidats:
            if not c:
                continue
            try:
                if mt5.symbol_info(c) is not None:
                    mt5.symbol_select(c, True)
                    self._symbol_cache[symbole_yahoo] = c
                    logger.info(f"Symbole {symbole_yahoo} -> {c}")
                    return c
            except Exception:
                continue

        # 2) recherche large dans tous les symboles du courtier.
        #    SÉCURITÉ : ne JAMAIS deviner par simple préfixe (BA→BABA,
        #    US30→US300 ⇒ ordre RÉEL sur le mauvais instrument). On exige une
        #    correspondance EXACTE ; à défaut, un préfixe n'est accepté que si
        #    le reste est un suffixe de décoration courtier CONNU (ex. .CASH,
        #    m, .r) ET qu'un seul symbole correspond. Sinon on refuse (None).
        try:
            # Règle du SÉPARATEUR : une décoration de courtier est introduite
            # par un séparateur (AAPL.us, EURUSD_i, XAUUSD-sd) OU est un suffixe
            # collé en MINUSCULES (EURUSDm, BTCUSDpro). Un autre instrument
            # (BA→BAC, T→TM, V→VZ, US30→US300) n'a ni séparateur ni suffixe
            # minuscule connu : il est donc refusé. Cette règle évite à la fois
            # d'ouvrir un ordre sur le MAUVAIS instrument et de bloquer les
            # symboles légitimement décorés.
            SEPARATEURS = ".-_#/ "
            SUFFIXES_COLLES = ("m", "c", "i", "z", "r", "e", "pro", "ecn",
                               "micro", "cash", "spot", "raw", "sml")

            def _decoration(nom_brut: str, sans_tiret: str):
                """Retourne le suffixe de décoration si `nom_brut` == base +
                décoration, sinon None. `sans_tiret` est la base visée : il y
                en a plusieurs quand le symbole a des alias (GOLD.TR, XAUUSD…)."""
                brut = nom_brut.strip()
                # on ignore un préfixe décoratif éventuel (#AAPL, .US30)
                tete = brut.lstrip("#.")
                norm_tete = tete.upper().replace("-", "").replace("_", "").replace(".", "")
                if not norm_tete.startswith(sans_tiret):
                    return None
                # Reconstituer la position dans le nom BRUT après la base
                n = i = 0
                while i < len(tete) and n < len(sans_tiret):
                    if tete[i] in SEPARATEURS:
                        i += 1
                        continue
                    if tete[i].upper() != sans_tiret[n]:
                        return None
                    n += 1
                    i += 1
                if n < len(sans_tiret):
                    return None
                reste = tete[i:]
                if not reste:
                    return ""                       # correspondance exacte
                if reste[0] in SEPARATEURS and len(reste) <= 6:
                    return reste                    # séparateur → décoration sûre
                if reste in SUFFIXES_COLLES:        # collé, minuscule → connu
                    return reste
                return None                         # sinon : AUTRE instrument

            exact = None
            prefixes = []
            for s in (mt5.symbols_get() or []):
                nom_norm = _normaliser_nom(s.name)
                if nom_norm in cibles:
                    exact = s.name
                    break
                if any(_decoration(s.name, cible) is not None for cible in cibles):
                    prefixes.append(s.name)
            choix = exact or (prefixes[0] if len(prefixes) == 1 else None)
            if choix:
                mt5.symbol_select(choix, True)
                self._symbol_cache[symbole_yahoo] = choix
                logger.info(f"Symbole {symbole_yahoo} -> {choix} (recherche)")
                return choix
            if prefixes:
                logger.warning(f"Symbole {symbole_yahoo} ambigu ({len(prefixes)} candidats : "
                               f"{prefixes[:5]}) — ordre refusé par sécurité.")
        except Exception as e:
            logger.warning(f"Recherche symbole {symbole_yahoo} : {e}")

        self._symbol_cache[symbole_yahoo] = None
        return None

    @staticmethod
    def _plafonner_volume_risque(volume: float, price: float, sl: float,
                                 equity: float, info) -> Optional[float]:
        """Plafonne le volume pour que la perte au stop-loss ne dépasse pas
        le « risque par ordre » réglé dans l'interface (Réglages ⚙️ → Moteur
        de performance, `risque_par_trade_pct`).

        Retourne None si le risque est INCALCULABLE. La fonction renvoyait
        auparavant le volume INCHANGÉ dans ce cas : dès que le courtier
        n'exposait pas la valeur du tick pour l'instrument, la garantie
        « perte au stop ≤ 1% » cessait de s'appliquer EN SILENCE, et le volume
        brut partait tel quel (0,33 lot de BTCUSD sur un compte de 10 000 €,
        soit ~6% de l'équité au stop). Une garantie qu'on ne peut pas vérifier
        ne doit pas être annoncée : l'appelant refuse l'ordre.
        """
        try:
            from utils import trading_config
            risque_pct = float(trading_config.get("risque_par_trade_pct", 25.0)) / 100
            if not (sl and equity > 0 and risque_pct > 0):
                return None
            dist = abs(price - sl)
            if dist <= 0:
                return None

            tick_size = getattr(info, "trade_tick_size", 0) or 0
            tick_val = getattr(info, "trade_tick_value", 0) or 0
            if tick_size > 0 and tick_val > 0:
                perte_par_lot = dist / tick_size * tick_val   # devise du COMPTE
            else:
                # Repli sur la taille de contrat : la perte est alors exprimée
                # en devise de COTATION. L'écart avec la devise du compte
                # (quelques %) est absorbé par la marge de sécurité — bien
                # préférable à l'absence totale de plafond.
                contrat = getattr(info, "trade_contract_size", 0) or 0
                if contrat <= 0:
                    logger.warning(
                        f"Risque au stop incalculable pour "
                        f"{getattr(info, 'name', '?')} : ni valeur du tick ni "
                        f"taille de contrat exposées par le courtier.")
                    return None
                perte_par_lot = dist * contrat
                logger.info(f"Plafond de risque estimé via la taille de contrat "
                            f"({contrat}) — devise de cotation.")

            if perte_par_lot <= 0:
                return None
            volume_max = (equity * risque_pct) / perte_par_lot
            if volume_max < volume:
                logger.info(f"Volume réduit {volume} -> {volume_max:.4f} "
                            f"(risque {risque_pct*100:.1f}% de {equity})")
                return volume_max
            return volume
        except Exception as e:
            logger.warning(f"Plafond de risque non calculable : {e}")
            return None

    def _plafonner_volume_marge(self, symbol: str, order_type, volume: float,
                                price: float) -> Optional[float]:
        """Volume maximum réellement couvrable par la marge libre du compte.

        Le levier d'un compte MetaTrader est fixé par le COURTIER : côté
        application, « travailler à un levier de 10 » veut dire engager dix
        fois l'équité en notionnel — encore faut-il que la marge suive. Sans ce
        plafond, le terminal rejette l'ordre entier (retcode 10019, « No
        money ») là où un volume à peine plus petit serait passé.

        Retourne None si la marge n'est pas calculable (le volume demandé est
        alors conservé tel quel : c'est le courtier qui tranchera).
        """
        if not (self._lib_available and self._connected):
            return None
        try:
            import MetaTrader5 as mt5
            from utils import trading_config
            with self._mt5_lock:
                marge = mt5.order_calc_margin(order_type, symbol, volume, price)
                info_compte = mt5.account_info()
            if not marge or marge <= 0 or info_compte is None:
                return None
            libre = float(getattr(info_compte, "margin_free", 0) or 0)
            if libre <= 0:
                return None
            part = float(trading_config.get("marge_utilisable_pct", 90.0)) / 100
            budget = libre * max(0.01, min(1.0, part))
            if marge <= budget:
                return volume
            plafond = volume * budget / marge
            logger.info(f"Volume réduit par la marge disponible : {volume} -> "
                        f"{plafond:.4f} lot ({symbol}, marge libre {libre:.2f})")
            return plafond
        except Exception as e:
            logger.warning(f"Marge non calculable pour {symbol} : {e}")
            return None

    def fermer_toutes_positions(self) -> Dict[str, Any]:
        """Ferme TOUTES les positions ouvertes, une par une, par ticket.

        Sert au bouton d'urgence de l'accueil. On ne s'arrête PAS à la première
        erreur : une position récalcitrante (marché fermé, prix indisponible) ne
        doit pas laisser les autres ouvertes. Le détail des échecs est renvoyé
        pour être affiché tel quel dans l'interface.
        """
        positions = self.get_positions()
        if not positions:
            return {"success": True, "fermees": 0, "echecs": 0, "details": [],
                    "message": "Aucune position ouverte"}

        fermees, echecs, details = 0, 0, []
        for p in positions:
            ticket = p.get("ticket")
            r = self.fermer_ticket(ticket)
            if r.get("success"):
                fermees += 1
                details.append({"ticket": ticket, "symbole": p.get("symbol"),
                                "succes": True})
            else:
                echecs += 1
                details.append({"ticket": ticket, "symbole": p.get("symbol"),
                                "succes": False, "erreur": r.get("error", "")})
        logger.info(f"Fermeture globale : {fermees} position(s) fermée(s), "
                    f"{echecs} échec(s)")
        return {
            "success": echecs == 0,
            "fermees": fermees,
            "echecs": echecs,
            "details": details,
            "message": (f"{fermees} position(s) fermée(s)"
                        + (f", {echecs} échec(s)" if echecs else "")),
        }

    def directions_ouvertes(self, symbole_yahoo: str) -> list:
        """Sens (BUY/SELL) des positions déjà ouvertes sur ce symbole.
        Sert à l'anti-empilement de l'auto-trader."""
        if not (self._lib_available and self._connected):
            return []
        try:
            import MetaTrader5 as mt5
            with self._mt5_lock:
                sym = self.resoudre_symbole(symbole_yahoo)
                if not sym:
                    return []
                positions = mt5.positions_get(symbol=sym) or []
            return list({("BUY" if p.type == 0 else "SELL") for p in positions})
        except Exception:
            return []

    def fermer_position(self, position) -> Dict[str, Any]:
        """FERME réellement une position par son ticket.

        Indispensable sur les comptes hedging (défaut AvaTrade) : un simple
        ordre inverse y OUVRIRAIT une position opposée au lieu de fermer.
        Le champ `position: ticket` cible la clôture sur les deux types de
        comptes (hedging et netting). Aucun SL/TP, aucun trailing.
        """
        if not (self._lib_available and self._connected):
            return {"success": False, "error": "MT5 non connecté"}
        import MetaTrader5 as mt5
        try:
            with self._mt5_lock:
                tick = mt5.symbol_info_tick(position.symbol)
                if not tick:
                    return {"success": False, "error": f"Prix indisponible pour {position.symbol}"}
                est_buy = position.type == 0
                req = {
                    "action":       mt5.TRADE_ACTION_DEAL,
                    "position":     position.ticket,
                    "symbol":       position.symbol,
                    "volume":       position.volume,
                    "type":         mt5.ORDER_TYPE_SELL if est_buy else mt5.ORDER_TYPE_BUY,
                    "price":        tick.bid if est_buy else tick.ask,
                    "deviation":    20,
                    "magic":        234_000,
                    "comment":      "AgenceNumerique-close",
                    "type_time":    mt5.ORDER_TIME_GTC,
                    "type_filling": self._modes_remplissage(mt5.symbol_info(position.symbol))[0],
                }
                res = mt5.order_send(req)
            if res is not None and res.retcode == mt5.TRADE_RETCODE_DONE:
                logger.info(f"Position {position.ticket} ({position.symbol}) fermée")
                with self._journal_lock:
                    self._journal.insert(0, {
                        "timestamp": datetime.now().strftime("%H:%M:%S"),
                        "symbol":    position.symbol,
                        "action":    "CLOSE",
                        "volume":    position.volume,
                        "price":     res.price,
                        "ticket":    position.ticket,
                        "success":   True,
                        "error":     None,
                    })
                return {"success": True, "ticket": position.ticket}
            err = getattr(res, "comment", None) or str(mt5.last_error())
            return {"success": False, "error": f"Fermeture refusée : {err}"}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def gerer_trailing(self):
        """Gestion active des positions : breakeven à +1R, puis trailing à 1R.

        R = distance entre le prix d'entrée et le stop-loss initial. Dès que
        le gain atteint 1R, le SL est remonté au prix d'entrée (la position ne
        peut plus perdre) ; au-delà, il suit le prix à distance de 1R.
        """
        if os.getenv("TRAILING_ACTIF", "true").lower() not in ("1", "true", "yes", "oui"):
            return
        if not (self._lib_available and self._connected):
            return
        try:
            import MetaTrader5 as mt5
            with self._mt5_lock:
                positions = list(mt5.positions_get() or [])
            for p in positions:
                if getattr(p, "magic", 0) != 234_000:
                    continue          # ne toucher que les positions de l'app
                if not p.sl:
                    continue
                r = abs(p.price_open - p.sl)
                if r <= 0:
                    continue
                if p.type == 0:       # BUY
                    gain = p.price_current - p.price_open
                    if gain >= r:
                        nouveau_sl = max(p.price_open, p.price_current - r)
                        if nouveau_sl > p.sl + r * 0.05:   # éviter les micro-maj
                            self._modifier_sl(p, nouveau_sl)
                else:                 # SELL
                    gain = p.price_open - p.price_current
                    if gain >= r:
                        nouveau_sl = min(p.price_open, p.price_current + r)
                        if nouveau_sl < p.sl - r * 0.05:
                            self._modifier_sl(p, nouveau_sl)
        except Exception as e:
            logger.warning(f"Trailing : {e}")

    def _modifier_sl(self, position, nouveau_sl: float):
        import MetaTrader5 as mt5
        # Respecter la distance minimale du courtier (sinon rejet 10016).
        # Lecture sous verrou : gerer_trailing() a relâché _mt5_lock avant
        # d'itérer sur les positions, un ordre peut donc partir en parallèle.
        try:
            with self._mt5_lock:
                info = mt5.symbol_info(position.symbol)
            point = getattr(info, "point", 0) or 0
            stops_dist = (getattr(info, "trade_stops_level", 0) or 0) * point
            if stops_dist > 0:
                if position.type == 0:   # BUY : SL sous le prix courant
                    nouveau_sl = min(nouveau_sl, position.price_current - stops_dist)
                else:                    # SELL : SL au-dessus
                    nouveau_sl = max(nouveau_sl, position.price_current + stops_dist)
            digits = getattr(info, "digits", 5) or 5
            nouveau_sl = round(nouveau_sl, digits)
        except Exception:
            pass
        req = {
            "action":   mt5.TRADE_ACTION_SLTP,
            "position": position.ticket,
            "symbol":   position.symbol,
            "sl":       nouveau_sl,
            "tp":       position.tp,
        }
        with self._mt5_lock:
            res = mt5.order_send(req)
        if res is not None and res.retcode == mt5.TRADE_RETCODE_DONE:
            logger.info(f"Trailing : SL {position.symbol} -> {nouveau_sl}")
        else:
            logger.warning(f"Trailing refusé {position.symbol} : "
                           f"{getattr(res, 'comment', mt5.last_error())}")

    @staticmethod
    def _modes_remplissage(info) -> list:
        """Modes de remplissage supportés par le symbole, du plus au moins sûr."""
        import MetaTrader5 as mt5
        fm = getattr(info, "filling_mode", 0) or 0
        modes = []
        if fm & 1:  # SYMBOL_FILLING_FOK
            modes.append(mt5.ORDER_FILLING_FOK)
        if fm & 2:  # SYMBOL_FILLING_IOC
            modes.append(mt5.ORDER_FILLING_IOC)
        modes.append(mt5.ORDER_FILLING_RETURN)  # dernier recours
        # dédoublonnage en conservant l'ordre
        uniques = []
        for m in modes:
            if m not in uniques:
                uniques.append(m)
        return uniques

    @_sous_verrou
    def _send_order_real(self, signal: Dict) -> Dict[str, Any]:
        import MetaTrader5 as mt5
        import time as _time

        symbole_src = signal.get("symbol", "")
        action = signal.get("action")

        # Le courtier n'utilise PAS les noms Yahoo : traduire d'abord.
        # En mode réel on ne simule JAMAIS en douce — on rapporte l'erreur.
        symbol = self.resoudre_symbole(symbole_src)
        if symbol is None:
            # Proposer les noms RÉELS du courtier plutôt que de renvoyer
            # l'utilisateur chercher lui-même dans le Market Watch.
            pistes = self.suggerer_symboles(symbole_src)
            aide = (f" Essayez plutôt : {', '.join(pistes)}."
                    if pistes else
                    " Ouvrez MetaTrader > Market Watch (clic droit > Afficher tout) "
                    "pour voir les symboles proposés.")
            return {"success": False,
                    "error": f"Symbole « {symbole_src} » indisponible chez votre courtier.{aide}"}

        # Bouton « AlgoTrading » du terminal : vérifié AVANT de composer
        # l'ordre. Sans lui, le terminal rejette tout en 10027 — autant le dire
        # tout de suite, et avec la marche à suivre.
        if self.algotrading_actif() is False:
            return {"success": False, "retcode": 10027,
                    "error": _RETCODES[10027]}

        info = mt5.symbol_info(symbol)
        if info is None:
            return {"success": False, "error": f"Infos indisponibles pour {symbol}"}
        if getattr(info, "trade_mode", 1) == 0:  # SYMBOL_TRADE_MODE_DISABLED
            return {"success": False, "error": f"Trading désactivé pour {symbol} chez le courtier"}

        # Volume borné aux contraintes du symbole (min / max / pas)
        volume = float(signal.get("volume", 0.01))
        vmin = getattr(info, "volume_min", 0.01) or 0.01
        vmax = getattr(info, "volume_max", 100.0) or 100.0
        vstep = getattr(info, "volume_step", 0.01) or 0.01
        volume = max(vmin, min(vmax, volume))
        volume = round(round(volume / vstep) * vstep, 8)
        if volume < vmin:
            volume = vmin

        # Prix : après symbol_select le tick peut mettre un instant à arriver
        tick = None
        for _ in range(10):
            tick = mt5.symbol_info_tick(symbol)
            if tick and (tick.ask or tick.bid):
                break
            _time.sleep(0.1)
        if not tick or not (tick.ask or tick.bid):
            return {"success": False,
                    "error": f"Prix indisponible pour {symbol} (marché fermé ?)"}

        # ── Filtre de SPREAD : un carnet anormalement large (ouverture, news,
        # illiquidité) ferait subir un slippage bien au-delà de ce que
        # `deviation` absorbe. Réglable dans l'interface — `0` le désactive
        # complètement (le courtier reste seul juge du prix obtenu).
        from utils import trading_config
        spread_max_pct = float(trading_config.get("spread_max_pct", 2.0)) / 100
        if tick.ask and tick.bid and spread_max_pct > 0:
            milieu = (tick.ask + tick.bid) / 2
            spread = tick.ask - tick.bid
            if milieu > 0 and spread > 0 and (spread / milieu) > spread_max_pct:
                return {"success": False,
                        "error": (f"Spread anormal sur {symbol} : "
                                  f"{spread / milieu * 100:.2f}% > "
                                  f"{spread_max_pct * 100:.2f}% — ordre refusé "
                                  f"(marché illiquide ou très volatil).")}

        price = tick.ask if action == "BUY" else tick.bid
        order_type = mt5.ORDER_TYPE_BUY if action == "BUY" else mt5.ORDER_TYPE_SELL
        sl = float(signal.get("stop_loss") or 0.0)
        tp = float(signal.get("take_profit") or 0.0)

        # ── Stops : complétés automatiquement quand les agents n'en donnent
        # pas. Désactivable dans l'interface (`stop_loss_obligatoire`) — les
        # ordres partent alors NUS, sans stop ni objectif.
        sl_pct = float(trading_config.get("sl_pct", 2.0)) / 100
        tp_pct = float(trading_config.get("tp_pct", 3.0)) / 100
        stops_imposes = bool(trading_config.get("stop_loss_obligatoire", True))
        point = getattr(info, "point", 0) or 0
        stops_dist = (getattr(info, "trade_stops_level", 0) or 0) * point
        digits = getattr(info, "digits", 5) or 5

        def _caler_sl_tp(prix_ref: float, sl_in: float, tp_in: float):
            """SL/TP valides POUR CE PRIX : présents, du bon côté, et au moins
            à la distance minimale imposée par le courtier.

            Extrait en fonction pour être rejoué après un requote : le prix
            change alors, et un SL calculé sur l'ancien prix pouvait se
            retrouver du MAUVAIS côté du nouveau → rejet 10016 « Invalid
            stops », rapporté à l'utilisateur comme un échec inexpliqué.
            """
            # Un niveau ≤ 0 est traité comme ABSENT (et non comme fourni) :
            # les formules par soustraction de certains agents (prix − k·ATR,
            # extrapolation de tendance) passent sous zéro sur un décrochage,
            # et un prix négatif serait transmis tel quel au courtier.
            s = sl_in if (sl_in or 0) > 0 else 0.0
            t = tp_in if (tp_in or 0) > 0 else 0.0
            if not s:
                s = prix_ref * (1 - sl_pct) if action == "BUY" else prix_ref * (1 + sl_pct)
            if not t:
                t = prix_ref * (1 + tp_pct) if action == "BUY" else prix_ref * (1 - tp_pct)
            # Le prix a pu bouger depuis l'analyse (gap) : un SL/TP du mauvais
            # côté du prix d'exécution serait rejeté → recalculer par défaut.
            if action == "BUY":
                if s >= prix_ref:
                    s = prix_ref * (1 - sl_pct)
                if t <= prix_ref:
                    t = prix_ref * (1 + tp_pct)
            else:
                if s <= prix_ref:
                    s = prix_ref * (1 + sl_pct)
                if t >= prix_ref:
                    t = prix_ref * (1 - tp_pct)
            # Distance minimale imposée par le courtier (stops level) :
            # un stop trop proche serait rejeté (retcode 10016).
            if stops_dist > 0:
                if action == "BUY":
                    s = min(s, prix_ref - stops_dist)
                    t = max(t, prix_ref + stops_dist)
                else:
                    s = max(s, prix_ref + stops_dist)
                    t = min(t, prix_ref - stops_dist)
            # Plancher STRICTEMENT positif : sur une VENTE, `prix - stops_dist`
            # (ou un SL_DEFAUT_PCT mal saisi, > 100 %) peut encore produire un
            # niveau ≤ 0. Un prix négatif envoyé au courtier est un rejet
            # certain, au mieux ; on rabat sur un pourcentage du prix.
            plancher = prix_ref * 0.01
            s = max(s, plancher)
            t = max(t, plancher)
            return round(s, digits), round(t, digits)

        if stops_imposes:
            sl, tp = _caler_sl_tp(price, sl, tp)
        else:
            # Stops rendus facultatifs par l'utilisateur : on n'envoie que ce
            # que les agents ont réellement calculé (0 = aucun stop chez le
            # courtier — la position ne se coupera pas toute seule).
            sl, tp = _caler_sl_tp(price, sl, tp) if (sl > 0 or tp > 0) else (0.0, 0.0)

        equity = self.equity_live()   # équité EN DIRECT, pas la valeur en cache

        # ── Dimensionnement au LEVIER ────────────────────────────────
        # Le volume reçu du signal n'est qu'une intention : c'est ici qu'on
        # connaît le prix d'exécution et la taille de contrat, donc le seul
        # endroit où « engager L fois l'équité » a un sens. Le levier vient
        # de la décision des agents (signal["levier"]), sinon du réglage de
        # l'interface. Désactivable via `sizing_levier`.
        # `volume_impose` : un appelant qui sait exactement ce qu'il veut
        # (ordre manuel via l'API) garde son volume, levier ou pas.
        levier = signal.get("levier")
        if trading_config.get("sizing_levier", True) and not signal.get("volume_impose"):
            contrat = getattr(info, "trade_contract_size", 0) or 0
            v_levier = trading_config.volume_cible(
                equity, price, contrat,
                confiance=signal.get("confiance"), levier=levier)
            if v_levier and v_levier > 0:
                logger.info(f"Volume au levier x{levier or trading_config.levier_actuel()} : "
                            f"{volume} -> {v_levier} lot ({symbol}, équité {equity})")
                volume = max(vmin, min(vmax, v_levier))

        # ── Volume plafonné par le risque : perte au SL ≤ X% du compte ─
        # X est réglé dans l'interface. Deux comportements, eux aussi réglables,
        # quand la limite ne peut pas être tenue :
        #   - `exiger_risque_calculable` : refuser si le courtier n'expose pas
        #     de quoi calculer la perte au stop (défaut : non, on envoie) ;
        #   - `autoriser_lot_minimum` : envoyer le lot minimum quand même
        #     plutôt que de refuser (défaut : oui — sur un petit compte, c'était
        #     la cause n°1 des ordres jamais partis).
        volume_risque = self._plafonner_volume_risque(volume, price, sl, equity, info)
        risque_pct = float(trading_config.get("risque_par_trade_pct", 25.0))
        if volume_risque is None:
            # Deux causes distinctes, deux messages : sans stop-loss, il n'y a
            # pas de « perte au stop » à calculer ; avec stop, c'est le courtier
            # qui n'expose pas les spécifications du symbole.
            cause = ("aucun stop-loss n'est défini (stops rendus facultatifs "
                     "dans les réglages)" if not sl else
                     f"le courtier n'expose ni la valeur du tick ni la taille de "
                     f"contrat pour {symbol}")
            if trading_config.get("exiger_risque_calculable", False):
                return {"success": False,
                        "error": (f"Perte au stop incalculable : {cause}. "
                                  f"Impossible de garantir la limite de "
                                  f"{risque_pct:.1f}% par ordre — ordre refusé par "
                                  f"sécurité. Ouvrez MetaTrader → Market Watch et "
                                  f"ajoutez {symbol} pour charger ses "
                                  f"spécifications, ou rétablissez le stop-loss "
                                  f"obligatoire.")}
            logger.warning(f"Perte au stop incalculable ({cause}) — ordre envoyé au "
                           f"volume demandé ({volume}), plafond de risque NON "
                           f"appliqué (réglage « exiger un risque calculable » "
                           f"désactivé).")
            volume_risque = volume
        if volume_risque < vmin:
            if not trading_config.get("autoriser_lot_minimum", True):
                return {"success": False,
                        "error": (f"Risque au stop > {risque_pct:.1f}% du compte même au "
                                  f"lot minimum ({vmin}) — ordre refusé. Augmentez le "
                                  f"« risque par ordre » dans Réglages ⚙️ → Moteur de "
                                  f"performance, ou le capital du compte.")}
            logger.warning(f"{symbol} : lot minimum ({vmin}) au-dessus du budget de "
                           f"risque ({risque_pct:.1f}%) — envoyé au lot minimum.")
            volume_risque = vmin
        # Arrondi VERS LE BAS sur le pas : un arrondi au plus proche pourrait
        # remonter le volume AU-DESSUS du plafond de risque (perte au stop > X%).
        volume = min(vmax, volume_risque)
        volume = round(math.floor(round(volume / vstep, 9)) * vstep, 8)
        if volume < vmin:
            volume = vmin

        # ── Plafond de MARGE : ne pas demander plus que ce que le compte peut
        # couvrir. Ce n'est pas un garde-fou de prudence mais d'efficacité :
        # sans lui, un volume trop grand est rejeté en bloc (« No money »,
        # 10019) alors qu'un volume légèrement réduit serait passé.
        volume_marge = self._plafonner_volume_marge(symbol, order_type, volume, price)
        if volume_marge is not None and volume_marge < volume:
            volume = round(math.floor(round(volume_marge / vstep, 9)) * vstep, 8)
            if volume < vmin:
                volume = vmin

        def _envoyer(filling, prix_courant, sl_courant, tp_courant):
            return mt5.order_send({
                "action":       mt5.TRADE_ACTION_DEAL,
                "symbol":       symbol,
                "volume":       volume,
                "type":         order_type,
                "price":        prix_courant,
                "sl":           sl_courant,
                "tp":           tp_courant,
                "deviation":    20,
                "magic":        234_000,
                "comment":      "AgenceNumerique",
                "type_time":    mt5.ORDER_TIME_GTC,
                "type_filling": filling,
            })

        # Deux causes de rejet INDÉPENDANTES, donc deux compteurs distincts :
        #  - 10030 (mode de remplissage refusé) → essayer le mode suivant ;
        #  - 10004 (requote) → même mode, prix rafraîchi, SL/TP RECALÉS sur ce
        #    nouveau prix. Avant, un requote faisait passer au mode suivant et
        #    laissait le SL calculé sur l'ANCIEN prix : sur un gap, il pouvait
        #    se retrouver du mauvais côté (rejet 10016), et sur un symbole à
        #    mode unique la relance n'avait tout simplement jamais lieu.
        REQUOTES_MAX = 2
        dernier_err = None
        for filling in self._modes_remplissage(info):
            for _tentative in range(REQUOTES_MAX + 1):
                res = _envoyer(filling, price, sl, tp)
                if res is None:
                    dernier_err = f"order_send a échoué : {mt5.last_error()}"
                    break                      # mode suivant
                if res.retcode == mt5.TRADE_RETCODE_DONE:
                    logger.info(f"Ordre RÉEL exécuté : {action} {symbol} {volume} "
                                f"@ {res.price} (ticket {res.order})")
                    return {"success": True, "ticket": res.order, "symbol": symbol,
                            "action": action, "volume": volume, "price": res.price}
                if res.retcode == 10030:       # remplissage non supporté
                    dernier_err = f"Mode de remplissage refusé ({res.comment})"
                    break                      # mode suivant
                if res.retcode == 10004:       # requote
                    dernier_err = f"Requote ({res.comment})"
                    t2 = mt5.symbol_info_tick(symbol)
                    if not t2 or not (t2.ask or t2.bid):
                        break
                    price = t2.ask if action == "BUY" else t2.bid
                    # Recale les stops DEMANDÉS sur le nouveau prix : ils ne
                    # changent que s'ils sont devenus invalides (mauvais côté
                    # ou trop proches), les niveaux des agents sont préservés.
                    # Un ordre volontairement SANS stop (réglage désactivé) le
                    # reste : le requote ne doit pas lui en inventer un.
                    if stops_imposes or sl > 0 or tp > 0:
                        sl, tp = _caler_sl_tp(price, sl, tp)
                    continue                   # même mode, prix rafraîchi
                # autre rejet → inutile d'insister
                return {"success": False, "retcode": res.retcode,
                        "error": _message_retcode(res.retcode, res.comment)}

        return {"success": False, "error": dernier_err or "Ordre non exécuté"}

    def _send_order_simulated(self, signal: Dict) -> Dict[str, Any]:
        import random
        symbol = signal.get("symbol", "")

        # Utiliser le prix fourni par le signal s'il existe
        price = signal.get("prix_entree") or signal.get("price") or 0.0

        # Sinon, tenter yfinance
        if not price:
            try:
                import yfinance as yf
                ticker = yf.Ticker(symbol)
                price = ticker.fast_info.get("lastPrice") or ticker.fast_info.get("regularMarketPrice") or 0.0
                price = float(price)
            except Exception:
                price = 0.0

        return {
            "success":   True,
            "ticket":    random.randint(100_000, 999_999),
            "symbol":    symbol,
            "action":    signal.get("action"),
            "volume":    signal.get("volume", 0.01),
            "price":     round(price, 5) if price else 0.0,
            "simulated": True,
        }

    # ── Journal ─────────────────────────────────────────────────────
    def get_journal(self, limite: int = 50) -> List[Dict]:
        with self._journal_lock:
            return list(self._journal[:limite])

    def clear_journal(self) -> None:
        with self._journal_lock:
            self._journal.clear()

    # ── Portefeuille synchronisé depuis MT5 ──────────────────────────
    def get_portfolio(self) -> Dict[str, Any]:
        """Retourne le portefeuille réel depuis le compte connecté."""
        if not self._connected:
            return {"synced": False}
        if not self._account_info:
            return {"synced": False}

        ai = self._account_info
        balance    = ai.get("balance", 0)
        equity     = ai.get("equity", 0)
        margin     = ai.get("margin", 0)
        free_margin = ai.get("free_margin", 0)

        pnl     = round(equity - balance, 2)
        pnl_pct = round((pnl / balance * 100), 2) if balance else 0.0
        expo_pct = round((margin / equity * 100), 2) if equity else 0.0

        positions = self.get_positions()

        return {
            "synced":           True,
            "source":           "MT5",
            "currency":         ai.get("currency", "USD"),
            "valeur_totale":    equity,
            "capital_disponible": free_margin,
            "pnl_total":        pnl,
            "pnl_total_pct":    pnl_pct,
            "nb_positions":     len(positions),
            "exposition_pct":   expo_pct,
            "drawdown_actuel":  self._calculer_drawdown(equity),
            "positions":        positions,
            "balance":          balance,
            "equity":           equity,
            "margin":           margin,
            "free_margin":      free_margin,
            "leverage":         ai.get("leverage", 0),
        }

    # ── Historique réel (deals MT5) ──────────────────────────────────
    def get_historique_reel(self, jours: int = 30) -> Dict[str, Any]:
        """P&L RÉEL depuis l'historique des transactions du compte MT5
        (profit + commission + swap des deals de clôture), agrégé par jour.
        Remplace le portefeuille simulé quand un compte est connecté."""
        if not (self._lib_available and self._connected):
            return {"synced": False, "raison": "Compte réel non connecté"}
        try:
            import MetaTrader5 as mt5
            from datetime import timedelta
            fin = datetime.now() + timedelta(days=1)
            debut = fin - timedelta(days=jours + 1)
            # Non bloquant : alimente l'objectif du jour, sondé en continu.
            with self._lecture() as libre:
                if not libre:
                    # Terminal occupé : dernier résultat connu, sinon rien —
                    # jamais une attente qui gèle la requête.
                    return dict(self._historique_cache) or {
                        "synced": False, "raison": "Terminal occupé"}
                deals = mt5.history_deals_get(debut, fin) or []
            DEAL_ENTRY_OUT = 1                     # deal de clôture (porte le P&L)
            fermetures = [d for d in deals if getattr(d, "entry", None) == DEAL_ENTRY_OUT]

            total, gagnes = 0.0, 0
            par_jour: Dict[str, float] = {}
            for d in fermetures:
                pnl = ((d.profit or 0)
                       + (getattr(d, "commission", 0) or 0)
                       + (getattr(d, "swap", 0) or 0))
                total += pnl
                if pnl > 0:
                    gagnes += 1
                j = datetime.fromtimestamp(d.time).strftime("%Y-%m-%d")
                par_jour[j] = round(par_jour.get(j, 0.0) + pnl, 2)

            nb = len(fermetures)
            self._historique_cache = {
                "synced": True,
                "source": "MT5",
                "jours": jours,
                "pnl_total": round(total, 2),
                "nb_trades": nb,
                "gagnants": gagnes,
                "perdants": nb - gagnes,
                "taux_reussite_pct": round(gagnes / nb * 100, 1) if nb else 0,
                "devise": (self._account_info or {}).get("currency", "USD"),
                "par_jour": [{"date": k, "pnl": v} for k, v in sorted(par_jour.items())],
            }
            return dict(self._historique_cache)
        except Exception as e:
            return {"synced": False, "raison": str(e)}

    # ── Positions ouvertes ───────────────────────────────────────────
    def get_positions(self) -> List[Dict]:
        """Retourne les positions ouvertes (réelles ou simulées)."""
        if not self._connected:
            return []

        if self._lib_available:
            try:
                import MetaTrader5 as mt5
                # Lecture NON BLOQUANTE (sondée toutes les 5 s par le thread de
                # surveillance ET par le tableau de bord). Quand le terminal
                # est monopolisé — connexion, envoi d'ordre — on rend la
                # dernière liste connue : une position affichée avec quelques
                # secondes de retard vaut mieux qu'un tableau de bord figé.
                with self._lecture() as libre:
                    if not libre:
                        return list(self._positions_cache)
                    positions = mt5.positions_get()
                if positions is None:
                    return []
                self._positions_cache = [
                    {
                        "ticket":      p.ticket,
                        "symbol":      p.symbol,
                        "type":        "BUY" if p.type == 0 else "SELL",
                        "volume":      round(p.volume, 2),
                        "price_open":  round(p.price_open, 5),
                        "price_current": round(p.price_current, 5),
                        "profit":      round(p.profit, 2),
                        "swap":        round(p.swap, 2),
                        "sl":          round(p.sl, 5),
                        "tp":          round(p.tp, 5),
                        "comment":     p.comment,
                    }
                    for p in positions
                ]
                return list(self._positions_cache)
            except Exception as exc:
                logger.warning(f"Positions MT5 : {exc}")
                return []

        # Simulation : le journal (le plus récent d'abord) fait foi. Pour chaque
        # symbole, seule la PREMIÈRE entrée compte : un CLOSE signifie que la
        # position a été fermée et ne doit plus apparaître comme ouverte.
        # Copie sous verrou : le journal est écrit par le thread auto-trader
        # pendant que l'API le lit (itération sur liste mutée = doublons/omissions).
        with self._journal_lock:
            journal = list(self._journal)
        seen = set()
        positions = []
        for entry in journal:
            sym = entry.get("symbol")
            if sym in seen:
                continue
            if entry.get("action") == "CLOSE" and entry.get("success"):
                seen.add(sym)          # position clôturée : plus rien à afficher
                continue
            if entry.get("success") and entry.get("action") in ("BUY", "SELL"):
                if sym not in seen:
                    seen.add(sym)
                    positions.append({
                        "ticket":      entry.get("ticket", 0),
                        "symbol":      sym,
                        "type":        entry.get("action"),
                        "volume":      round(entry.get("volume", 0.01), 2),
                        "price_open":  round(entry.get("price", 0), 5),
                        "price_current": round(entry.get("price", 0), 5),
                        "profit":      0.0,
                        "swap":        0.0,
                        "sl":          0.0,
                        "tp":          0.0,
                        "comment":     "simulation",
                    })
        return positions


# ── Singleton ────────────────────────────────────────────────────────
_manager: Optional[MT5Manager] = None
_manager_lock = threading.Lock()


def get_mt5_manager() -> MT5Manager:
    global _manager
    if _manager is None:
        with _manager_lock:
            if _manager is None:
                _manager = MT5Manager()
    return _manager
