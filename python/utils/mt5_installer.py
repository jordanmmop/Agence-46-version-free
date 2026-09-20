"""Installation de MetaTrader 5 (Windows) — programme d'installation EMBARQUÉ.

Comme pour Ollama et Hermès, l'installeur officiel MetaTrader 5 AvaTrade est
téléchargé AU BUILD (tools/preparer_runtimes.py) et empaqueté dans l'exe :
`runtime/mt5/mt5setup.exe`. L'utilisateur n'a donc rien à télécharger — un
clic lance l'installeur déjà présent, même sans connexion.

Si l'exe n'embarque pas l'installeur (dépôt cloné, build allégé), l'app le
télécharge à la demande comme avant.

Une fois le terminal installé, la librairie Python MetaTrader5 (embarquée
dans l'exe au build) s'y connecte et les ordres partent RÉELLEMENT sur le
compte AvaTrade. 100 % gratuit, aucun abonnement, aucun tiers — uniquement le
courtier. Windows uniquement (le terminal MT5 et la librairie sont Windows).
"""
import logging
import os
import platform
import subprocess
import threading
import time
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

DOSSIER = Path(os.path.expanduser("~")) / ".agence_financiere" / "mt5"

# Programmes d'installation officiels (MetaQuotes CDN). Le premier est
# la version AvaTrade pré-configurée ; repli sur le MT5 générique.
_URLS = [
    os.getenv("MT5_SETUP_URL", ""),
    "https://download.mql5.com/cdn/web/ava.trade.markets/mt5/avatrade5setup.exe",
    "https://download.mql5.com/cdn/web/metaquotes.software.corp/mt5/mt5setup.exe",
]

_CHEMINS_TERMINAL = [
    r"C:\Program Files\MetaTrader 5\terminal64.exe",
    r"C:\Program Files\AvaTrade MT5 Terminal\terminal64.exe",
    r"C:\Program Files (x86)\MetaTrader 5\terminal64.exe",
    r"C:\Program Files (x86)\AvaTrade MT5 Terminal\terminal64.exe",
]


class MT5Installer:
    def __init__(self):
        self.dossier = DOSSIER
        self.etape = "inactif"        # inactif|telechargement|installation|pret|erreur
        self.progression = 0.0
        self.detail = ""
        self.erreur = ""
        self._lock = threading.Lock()

    # ── Détection ─────────────────────────────────────────────────────
    @staticmethod
    def terminal_embarque() -> Optional[str]:
        """Terminal MetaTrader 5 PORTABLE livré dans l'application.

        Si le build a déposé une installation portable dans
        `runtime/mt5/terminal/`, il n'y a strictement rien à installer : le
        terminal se lance depuis l'application (mode /portable, ses données
        restent dans son propre dossier).

        SAUF dans un paquet MSIX. Le mode `/portable` écrit ses profils et son
        `config\\common.ini` À CÔTÉ de l'exécutable, or le dossier
        d'installation d'un MSIX est monté en LECTURE SEULE — pour tout le
        monde, y compris un processus administrateur. Le terminal démarrerait
        et refuserait ensuite toute connexion, sans message exploitable, et
        seulement à la première tentative de trading réel. On préfère répondre
        « pas de terminal embarqué » : l'application retombe alors sur la
        détection d'un MetaTrader installé normalement, c'est-à-dire sur le
        comportement qu'elle avait avant l'embarquement.
        """
        from utils import runtime_embarque
        for nom in ("terminal64.exe", "terminal.exe"):
            trouve = runtime_embarque.chemin("mt5", "terminal", nom)
            if trouve:
                from utils import msix
                if not msix.terminal_portable_utilisable(trouve):
                    return None
                return str(trouve)
        return None

    @staticmethod
    def terminal_installe() -> Optional[str]:
        portable = MT5Installer.terminal_embarque()
        if portable:
            return portable
        if platform.system() != "Windows":
            return None
        chemins = list(_CHEMINS_TERMINAL)
        appdata = os.environ.get("APPDATA", "")
        if appdata:
            import glob
            chemins += glob.glob(os.path.join(
                appdata, "MetaQuotes", "Terminal", "*", "terminal64.exe"))
        for p in chemins:
            if os.path.isfile(p):
                return p
        return None

    @staticmethod
    def lib_disponible() -> bool:
        try:
            import MetaTrader5  # noqa: F401
            return True
        except Exception:
            return False

    @staticmethod
    def setup_embarque() -> Optional[Path]:
        """Programme d'installation MetaTrader 5 livré DANS l'application."""
        from utils import runtime_embarque
        for motif in ("*setup*.exe", "*.exe"):
            trouve = runtime_embarque.premier(motif, "mt5")
            if trouve:
                return trouve
        return None

    @staticmethod
    def courtier_embarque() -> str:
        """Courtier du terminal embarqué : « avatrade », « metaquotes », ou "".

        Lu dans le manifeste écrit au build. Un terminal qui n'est pas celui
        du courtier démarre parfaitement mais ne connaît aucun serveur
        AvaTrade : la connexion au compte échouera, et il vaut mieux le dire
        avant que l'utilisateur ne cherche du côté de son mot de passe.
        """
        try:
            from utils import runtime_embarque
            return str((runtime_embarque.manifeste().get("mt5") or {})
                       .get("courtier", ""))
        except Exception:
            return ""

    @staticmethod
    def serveurs_embarques() -> list:
        """Serveurs que connaît le terminal embarqué (relevés au build)."""
        try:
            from utils import runtime_embarque
            return list((runtime_embarque.manifeste().get("mt5") or {})
                        .get("serveurs", []))
        except Exception:
            return []

    def get_status(self) -> Dict[str, Any]:
        return {
            "os_windows":    platform.system() == "Windows",
            # Courtier du terminal embarqué : seul « avatrade » permet de se
            # connecter à un compte AvaTrade sans configuration manuelle.
            "courtier":      self.courtier_embarque(),
            "serveurs":      self.serveurs_embarques()[:8],
            # Installeur livré avec l'application : rien à télécharger
            "embarque":      self.setup_embarque() is not None,
            # Terminal PORTABLE livré : rien à installer du tout
            "portable":      bool(self.terminal_embarque()),
            "lib":           self.lib_disponible(),
            "terminal":      bool(self.terminal_installe()),
            "chemin":        self.terminal_installe() or "",
            "etape":         self.etape,
            "progression":   round(self.progression, 1),
            "detail":        self.detail,
            "erreur":        self.erreur,
            "pret":          bool(self.lib_disponible() and self.terminal_installe()),
        }

    # ── Installation automatique ──────────────────────────────────────
    def installer_async(self) -> Dict[str, Any]:
        if platform.system() != "Windows":
            return {"success": False,
                    "error": "MetaTrader 5 ne s'installe que sur Windows. "
                             "Sur macOS/Linux, installez MetaTrader 5 via Wine."}
        with self._lock:
            if self.etape in ("telechargement", "installation"):
                return {"success": False, "error": "Installation déjà en cours"}
            self.etape = "telechargement"
            self.progression = 0.0
            self.erreur = ""
        threading.Thread(target=self._installer, daemon=True).start()
        return {"success": True}

    def _installer(self):
        try:
            if self.terminal_installe():
                self.etape = "pret"
                self.detail = "MetaTrader 5 déjà installé"
                return
            setup = self._telecharger()
            self.etape = "installation"
            self.progression = 0.0
            self.detail = "Installation de MetaTrader 5 (fenêtre du courtier possible)..."
            logger.info(f"[MT5] Lancement installeur {setup}")
            # /auto : installation automatique des installeurs MetaQuotes
            proc = subprocess.Popen([str(setup), "/auto"])
            # attendre l'apparition du terminal (jusqu'à 3 min)
            duree = 180
            fin = time.time() + duree
            while time.time() < fin:
                if self.terminal_installe():
                    self.etape = "pret"
                    self.progression = 100.0
                    self.detail = "MetaTrader 5 installé — connectez votre compte AvaTrade"
                    logger.info("[MT5] Terminal installé")
                    return
                # L'installeur s'est terminé sans que le terminal apparaisse :
                # échec immédiat (setup corrompu, /auto non supporté) → inutile
                # d'attendre 3 min.
                code = proc.poll()
                if code is not None and not self.terminal_installe():
                    self.etape = "erreur"
                    self.erreur = (f"L'installeur MetaTrader s'est terminé (code {code}) "
                                   "sans installer le terminal. Réessayez ou installez "
                                   "MetaTrader 5 manuellement.")
                    return
                # Progression indicative pendant l'attente (sinon figée à 0%)
                self.progression = min(95.0, (duree - (fin - time.time())) / duree * 100)
                time.sleep(2)
            # Timeout : ne pas laisser l'installeur en orphelin
            if proc.poll() is None:
                try:
                    proc.terminate()
                except Exception:
                    pass
            self.etape = "erreur"
            self.erreur = ("Installation non détectée automatiquement. Terminez "
                           "l'installation dans la fenêtre MetaTrader, puis "
                           "cliquez « Vérifier ».")
        except Exception as e:
            logger.error(f"[MT5] Installation : {e}")
            self.etape = "erreur"
            self.erreur = str(e)

    def _telecharger(self) -> Path:
        # Installeur EMBARQUÉ au build : aucun téléchargement, aucune attente,
        # et l'installation reste possible sans connexion Internet.
        embarque = self.setup_embarque()
        if embarque is not None:
            logger.info(f"[MT5] Installeur embarqué : {embarque}")
            self.progression = 100.0
            self.detail = "Programme d'installation MetaTrader 5 embarqué"
            return embarque

        self.dossier.mkdir(parents=True, exist_ok=True)
        cible = self.dossier / "mt5setup.exe"
        self.etape = "telechargement"
        self.detail = "Téléchargement de MetaTrader 5..."
        # utils.reseau : essaie plusieurs magasins de certificats. L'appel
        # `urlopen` nu utilisé ici échouait sur « CERTIFICATE_VERIFY_FAILED »
        # dès qu'un antivirus inspectait le HTTPS ou que le magasin Windows
        # était incomplet — cas très courant, et le message ne nommait ni la
        # cause ni le remède. Il ignorait aussi le repli sans proxy.
        from utils import reseau
        derniere = None
        for url in [u for u in _URLS if u]:
            try:
                logger.info(f"[MT5] Téléchargement {url}")
                reseau.telecharger(
                    url, cible, timeout=60,
                    progression=lambda pct: setattr(self, "progression", pct))
                if cible.stat().st_size > 500_000:
                    return cible
                derniere = RuntimeError("fichier trop petit (page d'erreur ?)")
            except Exception as e:
                derniere = e
                logger.warning(f"[MT5] {url} : {e}")
                continue

        if isinstance(derniere, reseau.CertificatIntrouvable):
            # Message DÉJÀ explicite (cause + marche à suivre) : le noyer dans
            # un texte générique ferait perdre la seule information utile.
            raise RuntimeError(
                f"{derniere} Vous pouvez aussi installer MetaTrader 5 vous-même "
                f"depuis avatrade.fr, puis cliquer « Vérifier ».")
        raise RuntimeError(
            f"Téléchargement impossible ({derniere}). Installez MetaTrader 5 "
            "manuellement depuis avatrade.fr puis cliquez « Vérifier ».")


# ── Singleton ─────────────────────────────────────────────────────────
_installer: Optional[MT5Installer] = None
_installer_lock = threading.Lock()


def get_mt5_installer() -> MT5Installer:
    global _installer
    if _installer is None:
        with _installer_lock:
            if _installer is None:
                _installer = MT5Installer()
    return _installer
