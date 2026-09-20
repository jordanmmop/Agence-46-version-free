"""RiskGuard — dernier filet avant l'envoi d'un ordre au courtier.

Contrairement à l'agent de conformité (qui ne fait que produire un signal
consultatif), ce module BLOQUE réellement les ordres. Appelé sur les deux
chemins d'exécution : l'auto-trader et les ordres manuels.

Trois plafonds, TOUS réglables depuis l'interface (Réglages ⚙️ → Sécurité,
`/api/risk/config`) et désactivables d'un seul interrupteur (`actif`) :
  1. Kill-switch de perte journalière : si l'équité chute de plus de X %
     depuis le début de journée, tout nouvel ordre est refusé et
     l'auto-trading est coupé.
  2. Plafond de positions simultanées.
  3. Plafond d'exposition (marge / équité).

Les défauts sont volontairement LARGES (perte du jour 50 %, 50 positions,
exposition 1000 %) : à un levier de 10, les anciens défauts (5 % / 10 / 50 %)
coupaient le trading dès la première série de positions. Ce sont des bornes de
sécurité, pas une stratégie : réduisez-les dans l'interface pour un profil
prudent.
"""
import logging
import os
import threading
from datetime import datetime
from typing import Any, Dict, Optional, Tuple

logger = logging.getLogger(__name__)


def _nombre_fini(valeur):
    """float(valeur) si c'est un nombre fini, sinon None.

    Les champs du portefeuille viennent du courtier ou d'un cache : une valeur
    absente, textuelle ou NaN ne doit jamais faire lever la fonction qui
    autorise les ordres."""
    if isinstance(valeur, bool) or valeur is None:
        return None
    try:
        f = float(valeur)
    except (TypeError, ValueError):
        return None
    import math
    return f if math.isfinite(f) else None


def _env_bool(nom: str, defaut: bool) -> bool:
    v = os.getenv(nom)
    if v is None:
        return defaut
    return v.strip().lower() in ("1", "true", "yes", "oui")


def _env_nombre(nom: str, defaut, entier: bool = False):
    """Plafond lu dans l'environnement, TOLÉRANT à une saisie approximative.

    `float(os.getenv(...))` nu levait ValueError DANS __init__ : le singleton
    ne pouvait alors plus être construit, et comme RiskGuard est interrogé par
    /api/risk/status, /api/mt5/trade, l'auto-trader et le pré-vol, TOUTE
    l'application répondait « Erreur interne » — pour un simple
    « PERTE_MAX_JOURNALIERE_PCT=5,0 » dans le .env. Un plafond de sécurité
    illisible doit retomber sur son défaut (et le dire), jamais empêcher
    l'application de démarrer.
    """
    brut = os.getenv(nom)
    if brut is None:
        return defaut
    try:
        v = float(str(brut).strip().replace(",", ".").replace(" ", ""))
        if v != v or v in (float("inf"), float("-inf")):
            raise ValueError("valeur hors bornes")
    except (TypeError, ValueError):
        logger.warning(f"[RiskGuard] {nom}={brut!r} illisible — défaut {defaut} appliqué")
        return defaut
    return int(round(v)) if entier else v


class RiskGuard:
    def __init__(self, persister: bool = False):
        self._lock = threading.Lock()
        self.actif = _env_bool("RISK_GUARD_ACTIF", True)
        self.perte_max_pct = _env_nombre("PERTE_MAX_JOURNALIERE_PCT", 50.0)
        self.max_positions = _env_nombre("MAX_POSITIONS", 50, entier=True)
        self.exposition_max_pct = _env_nombre("EXPOSITION_MAX_PCT", 1000.0)

        self._jour: Optional[str] = None
        self._equity_debut = 0.0
        self._equity_actuelle = 0.0
        self._kill = False
        self._raison_kill = ""
        # Persistance de l'état journalier : sans elle, un redémarrage en cours
        # de séance après une perte reprend l'équité DÉGRADÉE comme référence →
        # nouveau budget de 5% (perte cumulée réelle ~10%). Le singleton de
        # production persiste ; les tests créent RiskGuard() sans persistance.
        self._persister = persister
        if persister:
            self._charger_reglages()
            self._charger_etat()

    # ── Réglages (plafonds) ──────────────────────────────────────────
    def _charger_reglages(self):
        """Restaure les plafonds réglés dans l'interface (/api/risk/config).

        L'INTERFACE EST PRIORITAIRE sur l'environnement. La règle inverse
        s'appliquait avant, et elle produisait le pire des comportements :
        l'utilisateur montait « positions max » à 50 dans l'écran Réglages,
        l'application confirmait, le fichier de configuration enregistrait 50 —
        puis, au redémarrage suivant, un `MAX_POSITIONS=10` oublié dans un
        vieux `.env` (celui livré en exemple) reprenait la main SANS RIEN DIRE.
        Le plafond bas était alors atteint dès les premières positions et plus
        aucun ordre ne partait, sans que le réglage affiché ait changé de
        valeur dans le fichier.

        Une variable d'environnement reste la valeur INITIALE (appliquée dans
        __init__, donc tant que l'utilisateur n'a rien réglé lui-même) : c'est
        la même convention que le moteur de performance (utils/trading_config).
        """
        try:
            from utils import app_config
            cfg = app_config.get("risk_config") or {}
            if not isinstance(cfg, dict):
                return
            if "actif" in cfg:
                self.actif = bool(cfg["actif"])
            if "perte_max_pct" in cfg:
                self.perte_max_pct = float(cfg["perte_max_pct"])
            if "max_positions" in cfg:
                self.max_positions = int(cfg["max_positions"])
            if "exposition_max_pct" in cfg:
                self.exposition_max_pct = float(cfg["exposition_max_pct"])
        except Exception as e:
            logger.warning(f"[RiskGuard] Réglages non restaurés : {e}")

    def _sauver_reglages(self):
        if not self._persister:
            return
        try:
            from utils import app_config
            app_config.set("risk_config", {
                "actif": self.actif,
                "perte_max_pct": self.perte_max_pct,
                "max_positions": self.max_positions,
                "exposition_max_pct": self.exposition_max_pct,
            })
        except Exception as e:
            logger.warning(f"[RiskGuard] Réglages non enregistrés : {e}")

    def _charger_etat(self):
        try:
            from utils import app_config
            etat = app_config.get("risk_etat") or {}
            auj = datetime.now().date().isoformat()
            if etat.get("jour") == auj and float(etat.get("equity_debut") or 0) > 0:
                self._jour = etat["jour"]
                self._equity_debut = float(etat["equity_debut"])
                self._kill = bool(etat.get("kill", False))
                self._raison_kill = etat.get("raison_kill", "") if self._kill else ""
                logger.info(f"[RiskGuard] État du jour restauré — équité départ "
                            f"{self._equity_debut}, kill={self._kill}")
        except Exception as e:
            logger.warning(f"[RiskGuard] État non restauré : {e}")

    def _sauver_etat(self):
        if not self._persister:
            return
        try:
            from utils import app_config
            app_config.set("risk_etat", {
                "jour": self._jour,
                "equity_debut": self._equity_debut,
                "kill": self._kill,
                "raison_kill": self._raison_kill,
            })
        except Exception:
            pass

    # ── Suivi de la journée ───────────────────────────────────────────
    def _maj_journee(self, equity: float):
        auj = datetime.now().date().isoformat()
        if self._jour != auj:
            # Ne fixer la référence du jour QUE sur une équité valide :
            # une première lecture à 0 (compte pas encore synchronisé)
            # désarmerait le kill-switch pour toute la journée.
            if not equity or equity <= 0:
                return
            self._jour = auj
            self._equity_debut = equity
            self._kill = False
            self._raison_kill = ""
            logger.info(f"[RiskGuard] Nouvelle journée — équité départ {self._equity_debut}")
            self._sauver_etat()

    # ── Décision : autoriser un ordre ? ───────────────────────────────
    def evaluer(self, portfolio: Dict[str, Any]) -> Tuple[bool, str]:
        """Retourne (autorisé, raison). Met à jour l'état interne."""
        if not self.actif:
            return True, ""
        # Portefeuille inexploitable → REFUS, jamais une exception. Cette
        # fonction décide si un ordre part : si elle lève, l'appelant retourne
        # une erreur 500 (ordre manuel) ou interrompt tout le cycle
        # (auto-trader). Le refus est la seule issue sûre.
        if not isinstance(portfolio, dict):
            return False, ("État du compte illisible — ordre refusé. "
                           "Vérifiez la connexion à votre compte AvaTrade.")
        with self._lock:
            equity = _nombre_fini(portfolio.get("equity"))
            if equity is None:
                equity = _nombre_fini(portfolio.get("valeur_totale")) or 0.0
            self._equity_actuelle = equity
            self._maj_journee(equity)

            if self._kill:
                return False, self._raison_kill

            # 0. Équité inexploitable → REFUS, mais SURTOUT pas de kill-switch.
            # get_portfolio() renvoie {"synced": False} (donc equity absente)
            # dès que le compte n'est pas connecté ou pas encore synchronisé.
            # Sans ce garde, equity=0 était lu comme une perte de 100% : le
            # kill-switch se déclenchait à tort ET était PERSISTÉ — un simple
            # ordre manuel envoyé hors connexion suffisait à couper le trading
            # pour le reste de la journée, y compris après redémarrage.
            if not equity or equity <= 0:
                return False, ("Équité du compte indisponible — ordre refusé. "
                               "Vérifiez la connexion à votre compte AvaTrade "
                               "(MetaTrader 5).")

            # 1. Kill-switch perte journalière
            if self._equity_debut > 0:
                perte_pct = (self._equity_debut - equity) / self._equity_debut * 100
                if perte_pct >= self.perte_max_pct:
                    self._kill = True
                    self._raison_kill = (
                        f"Kill-switch : perte du jour {perte_pct:.1f}% "
                        f"≥ limite {self.perte_max_pct:.1f}% — trading suspendu"
                    )
                    logger.warning(f"[RiskGuard] {self._raison_kill}")
                    self._sauver_etat()   # le kill survit à un redémarrage
                    return False, self._raison_kill

            # 2. Plafond de positions
            # Valeur illisible → comptée comme 0 : le plafond ne doit ni
            # planter ni bloquer indûment sur un champ absent.
            nb = int(_nombre_fini(portfolio.get("nb_positions")) or 0)
            if nb >= self.max_positions:
                return False, f"Plafond de positions atteint ({nb}/{self.max_positions})"

            # 3. Plafond d'exposition
            expo = _nombre_fini(portfolio.get("exposition_pct")) or 0.0
            if expo >= self.exposition_max_pct:
                return False, (f"Exposition maximale atteinte "
                               f"({expo:.0f}% ≥ {self.exposition_max_pct:.0f}%)")

            return True, ""

    # ── Contrôle & configuration ──────────────────────────────────────
    def reset_kill(self) -> Dict[str, Any]:
        with self._lock:
            self._kill = False
            self._raison_kill = ""
            self._sauver_etat()
        logger.info("[RiskGuard] Kill-switch réarmé manuellement")
        return {"success": True}

    @staticmethod
    def _nombre(valeur, entier: bool = False):
        """Convertit une valeur reçue de l'API, ou lève ValueError.

        `float(valeur)` seul lève TypeError sur une liste/un dict/None — une
        exception NON rattrapée qui remontait en 500 « Erreur interne ». On
        n'accepte donc que ce qui a un sens : nombre, ou chaîne numérique
        (virgule décimale tolérée, comme partout ailleurs dans l'application).
        """
        if isinstance(valeur, bool) or valeur is None:
            raise ValueError("valeur numérique attendue")
        if isinstance(valeur, str):
            valeur = valeur.strip().replace(",", ".")
            if not valeur:
                raise ValueError("valeur vide")
        if not isinstance(valeur, (int, float, str)):
            raise ValueError("valeur numérique attendue")
        n = float(valeur)                       # lève ValueError si non numérique
        if n != n or n in (float("inf"), float("-inf")):
            raise ValueError("valeur numérique invalide")
        return int(n) if entier else n

    def configurer(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Applique les plafonds de sécurité. TOUT OU RIEN.

        Les champs étaient auparavant écrits un par un : une valeur invalide
        en milieu de liste levait une exception APRÈS avoir déjà modifié les
        précédents. L'API répondait 500 (« Erreur interne »), l'utilisateur
        croyait que rien n'avait été pris en compte — alors qu'une partie des
        plafonds avait bien changé, sans être enregistrée. Sur un module dont
        le rôle est de limiter les pertes, cet écart est inacceptable : on
        valide donc tout d'abord, on n'applique qu'ensuite.
        """
        if not isinstance(data, dict):
            return {"success": False, "error": "Réglages invalides", **self.get_status()}

        nouveaux: Dict[str, Any] = {}
        try:
            if "actif" in data:
                nouveaux["actif"] = bool(data["actif"])
            # Bornes larges : c'est l'utilisateur qui décide de son niveau de
            # risque. 100 % de perte journalière = kill-switch neutralisé (il
            # ne se déclenchera qu'à compte vidé) ; 2000 % d'exposition laisse
            # la place à un levier de 10 sur plusieurs positions.
            if "perte_max_pct" in data:
                nouveaux["perte_max_pct"] = max(
                    0.5, min(100.0, self._nombre(data["perte_max_pct"])))
            if "max_positions" in data:
                nouveaux["max_positions"] = max(
                    1, min(500, self._nombre(data["max_positions"], entier=True)))
            if "exposition_max_pct" in data:
                nouveaux["exposition_max_pct"] = max(
                    1.0, min(2000.0, self._nombre(data["exposition_max_pct"])))
        except (TypeError, ValueError, OverflowError) as e:
            logger.warning(f"[RiskGuard] Réglage refusé : {e}")
            return {"success": False,
                    "error": ("Réglage invalide — attendu des nombres "
                              "(perte max %, positions max, exposition max %). "
                              "Aucune modification appliquée."),
                    **self.get_status()}

        with self._lock:
            for cle, val in nouveaux.items():
                setattr(self, cle, val)
            # Persisté : les plafonds réglés depuis l'interface doivent survivre
            # au redémarrage, sinon la protection annoncée n'est plus celle
            # réellement appliquée à la séance suivante.
            self._sauver_reglages()
        return {"success": True, **self.get_status()}

    def get_status(self) -> Dict[str, Any]:
        perte_pct = 0.0
        if self._equity_debut > 0:
            perte_pct = (self._equity_debut - self._equity_actuelle) / self._equity_debut * 100
        return {
            "actif":               self.actif,
            "kill_switch":         self._kill,
            "raison":              self._raison_kill,
            "perte_jour_pct":      round(perte_pct, 2),
            "perte_max_pct":       self.perte_max_pct,
            "max_positions":       self.max_positions,
            "exposition_max_pct":  self.exposition_max_pct,
            "equity_debut_jour":   round(self._equity_debut, 2),
            "equity_actuelle":     round(self._equity_actuelle, 2),
        }


# ── Singleton ─────────────────────────────────────────────────────────
_guard: Optional[RiskGuard] = None
_guard_lock = threading.Lock()


def get_risk_guard() -> RiskGuard:
    global _guard
    if _guard is None:
        with _guard_lock:
            if _guard is None:
                _guard = RiskGuard(persister=True)   # état journalier persistant
    return _guard
