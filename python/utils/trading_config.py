"""Moteur de performance : tous les plafonds de trading, en un seul endroit.

Avant ce module, l'agressivité du robot était FIGÉE dans le code : risque au
stop plafonné à 1 % de l'équité, volume plafonné à 0,5 lot, cooldown de 30
minutes, aucun levier explicite. Sur un petit compte, ces plafonds ne
« protégeaient » plus rien : ils refusaient purement et simplement les ordres
(« Risque au stop > 1 % du compte même au lot minimum — ordre refusé »).

Tout est désormais réglable :
  - depuis l'interface (Réglages ⚙️ → Moteur de performance, `/api/trading/config`) ;
  - par les agents IA pour le LEVIER, dans la limite `levier_max` fixée par
    l'utilisateur (`ajuster_levier`, appelé par le Chef d'Orchestre).

Priorité des valeurs : réglage enregistré dans l'interface > variable
d'environnement > défaut. L'interface est volontairement PRIORITAIRE sur
l'environnement (contrairement au RiskGuard) : un `LEVIER_MAX=3` oublié dans
un vieux `.env` ne doit pas rendre le curseur de l'écran de réglages inopérant.
"""
import logging
import os
import threading
from typing import Any, Dict, Optional, Tuple

logger = logging.getLogger(__name__)

# clé → (défaut, minimum, maximum, type, variable d'environnement)
# Les défauts correspondent au mode « plein potentiel » : ce sont des réglages
# OFFENSIFS, à réduire dans l'interface pour revenir à un profil prudent.
_SPEC: Dict[str, Tuple[Any, Optional[float], Optional[float], type, str]] = {
    # ── Levier ────────────────────────────────────────────────────────
    "levier_max":               (10.0,  1.0,  10.0,    float, "LEVIER_MAX"),
    "levier":                   (10.0,  1.0,  10.0,    float, "LEVIER"),
    "levier_auto":              (True,  None, None,    bool,  "LEVIER_AUTO"),
    # ── Objectif de gain ──────────────────────────────────────────────
    "capital_reference":        (100.0, 1.0,  1e9,     float, "CAPITAL_REFERENCE"),
    "objectif_journalier":      (80.0,  0.0,  1e9,     float, "OBJECTIF_JOURNALIER"),
    "stop_sur_objectif":        (False, None, None,    bool,  "STOP_SUR_OBJECTIF"),
    # ── Dimensionnement des ordres ────────────────────────────────────
    "sizing_levier":            (True,  None, None,    bool,  "SIZING_LEVIER"),
    "risque_par_trade_pct":     (25.0,  0.1,  100.0,   float, "RISQUE_PAR_TRADE_PCT"),
    "volume_max_lot":           (5.0,   0.01, 100.0,   float, "VOLUME_MAX_LOT"),
    "marge_utilisable_pct":     (90.0,  1.0,  100.0,   float, "MARGE_UTILISABLE_PCT"),
    # ── Filtres d'entrée (0 = aucun filtre) ───────────────────────────
    "confiance_min":            (0.0,   0.0,  100.0,   float, "CONFIANCE_MIN"),
    "cooldown_s":               (60,    0,    86400,   int,   "TRADE_COOLDOWN_S"),
    "anti_empilement":          (False, None, None,    bool,  "ANTI_EMPILEMENT"),
    "max_positions_symbole":    (3,     1,    100,     int,   "MAX_POSITIONS_SYMBOLE"),
    # ── Stops ─────────────────────────────────────────────────────────
    "stop_loss_obligatoire":    (True,  None, None,    bool,  "STOP_LOSS_OBLIGATOIRE"),
    "sl_pct":                   (2.0,   0.1,  50.0,    float, "SL_DEFAUT_PCT"),
    "tp_pct":                   (3.0,   0.1,  200.0,   float, "TP_DEFAUT_PCT"),
    # ── Garde-fous levables ───────────────────────────────────────────
    "spread_max_pct":           (2.0,   0.0,  100.0,   float, "SPREAD_MAX_PCT"),
    "exiger_risque_calculable": (False, None, None,    bool,  "EXIGER_RISQUE_CALCULABLE"),
    "autoriser_lot_minimum":    (True,  None, None,    bool,  "AUTORISER_LOT_MINIMUM"),
    "respecter_heures_marche":  (True,  None, None,    bool,  "HEURES_MARCHE_ACTIF"),
    "bloquer_donnees_simulees": (True,  None, None,    bool,  "BLOQUER_DONNEES_SIMULEES"),
}

_CLE_CONFIG = "trading_config"     # entrée dans ~/.agence_financiere/config.json

_lock = threading.RLock()
_cache: Optional[Dict[str, Any]] = None

# Levier décidé par les agents IA : vit en MÉMOIRE (un cycle par minute ne doit
# pas réécrire le fichier de configuration à chaque symbole). Le réglage
# utilisateur reste la référence persistée, et le remet à zéro dès qu'il change.
_levier_runtime: Optional[float] = None
_levier_source = "réglage"


def _vrai(v) -> bool:
    if isinstance(v, bool):
        return v
    return str(v).strip().lower() in ("1", "true", "yes", "oui", "on")


def _borner(cle: str, valeur):
    """Convertit et borne une valeur selon _SPEC. Lève ValueError si inutilisable."""
    defaut, mini, maxi, typ, _ = _SPEC[cle]
    if typ is bool:
        return _vrai(valeur)
    if isinstance(valeur, str):
        valeur = valeur.strip().replace(",", ".")
        if not valeur:
            raise ValueError(f"{cle} : valeur vide")
    if isinstance(valeur, bool) or valeur is None:
        raise ValueError(f"{cle} : nombre attendu")
    n = float(valeur)
    if n != n or n in (float("inf"), float("-inf")):
        raise ValueError(f"{cle} : nombre invalide")
    if mini is not None:
        n = max(mini, n)
    if maxi is not None:
        n = min(maxi, n)
    return int(round(n)) if typ is int else round(n, 4)


def _charger() -> Dict[str, Any]:
    global _cache
    if _cache is not None:
        return _cache
    enregistre: Dict[str, Any] = {}
    try:
        from utils import app_config
        brut = app_config.get(_CLE_CONFIG) or {}
        if isinstance(brut, dict):
            enregistre = brut
    except Exception as e:
        logger.warning(f"[trading_config] réglages non restaurés : {e}")

    valeurs: Dict[str, Any] = {}
    for cle, (defaut, _mini, _maxi, _typ, env) in _SPEC.items():
        source = None
        if cle in enregistre:                      # 1. réglage de l'interface
            source = enregistre[cle]
        elif os.getenv(env) is not None:           # 2. variable d'environnement
            source = os.getenv(env)
        if source is None:
            valeurs[cle] = defaut                  # 3. défaut « plein potentiel »
            continue
        try:
            valeurs[cle] = _borner(cle, source)
        except (TypeError, ValueError) as e:
            logger.warning(f"[trading_config] {e} — défaut appliqué ({defaut})")
            valeurs[cle] = defaut

    # Cohérence : le levier courant ne peut pas dépasser le plafond utilisateur.
    valeurs["levier"] = min(valeurs["levier"], valeurs["levier_max"])
    _cache = valeurs
    return _cache


def recharger() -> Dict[str, Any]:
    """Oublie le cache : la prochaine lecture repart du fichier + environnement."""
    global _cache
    with _lock:
        _cache = None
        return dict(_charger())


def forcer_en_memoire(**valeurs) -> Dict[str, Any]:
    """Réglages EN MÉMOIRE, à partir des DÉFAUTS, sans rien écrire sur disque.

    Réservé aux tests et aux diagnostics. Deux raisons de repartir des défauts
    plutôt que du fichier : `configurer()` persiste dans la configuration de
    l'utilisateur (qu'une suite de tests ne doit jamais toucher), et un test
    qui hériterait des réglages de la machine qui le lance échouerait chez les
    uns et passerait chez les autres.
    """
    global _cache, _levier_runtime, _levier_source
    with _lock:
        _levier_runtime = None
        _levier_source = "réglage"
        courant = {cle: spec[0] for cle, spec in _SPEC.items()}
        for cle, valeur in valeurs.items():
            if cle in _SPEC:
                courant[cle] = _borner(cle, valeur)
        courant["levier"] = min(courant["levier"], courant["levier_max"])
        _cache = courant
        return dict(courant)


def get(cle: str, defaut=None):
    """Valeur courante d'un réglage (voir _SPEC pour la liste)."""
    if cle not in _SPEC:
        return defaut
    with _lock:
        return _charger().get(cle, _SPEC[cle][0])


def tout() -> Dict[str, Any]:
    """Tous les réglages + l'état du levier (pour l'interface et l'API)."""
    with _lock:
        valeurs = dict(_charger())
    valeurs["levier_actuel"] = levier_actuel()
    valeurs["levier_source"] = _levier_source
    return valeurs


def configurer(data: Dict[str, Any]) -> Dict[str, Any]:
    """Applique des réglages venus de l'interface. TOUT OU RIEN.

    Même règle que RiskGuard.configurer : on valide l'intégralité avant
    d'écrire quoi que ce soit, pour ne jamais laisser la moitié d'un formulaire
    appliquée sans être enregistrée.
    """
    global _cache, _levier_runtime, _levier_source
    if not isinstance(data, dict):
        return {"success": False, "error": "Réglages invalides", "config": tout()}

    nouveaux: Dict[str, Any] = {}
    for cle, valeur in data.items():
        if cle not in _SPEC:
            continue                                # champ inconnu : ignoré
        try:
            nouveaux[cle] = _borner(cle, valeur)
        except (TypeError, ValueError, OverflowError) as e:
            logger.warning(f"[trading_config] réglage refusé : {e}")
            return {"success": False,
                    "error": (f"Réglage invalide ({cle}) — aucune modification "
                              f"appliquée."),
                    "config": tout()}
    if not nouveaux:
        return {"success": False, "error": "Aucun réglage reconnu", "config": tout()}

    with _lock:
        courant = dict(_charger())
        courant.update(nouveaux)
        # Un plafond abaissé rabat immédiatement le levier courant : sinon
        # l'utilisateur voit « levier max 3 » et le robot continue à 10.
        courant["levier"] = min(courant["levier"], courant["levier_max"])
        _cache = courant
        # Le réglage utilisateur reprend la main sur la décision des agents.
        if "levier" in nouveaux or "levier_max" in nouveaux or "levier_auto" in nouveaux:
            _levier_runtime = None
            _levier_source = "réglage"
        try:
            from utils import app_config
            app_config.set(_CLE_CONFIG, {c: courant[c] for c in _SPEC})
        except Exception as e:
            logger.warning(f"[trading_config] réglages non enregistrés : {e}")
            return {"success": False,
                    "error": f"Réglages appliqués mais non enregistrés : {e}",
                    "config": tout()}
    logger.info(f"[trading_config] réglages mis à jour : {sorted(nouveaux)}")
    return {"success": True, "config": tout()}


# ── Levier ────────────────────────────────────────────────────────────
def levier_actuel() -> float:
    """Levier réellement utilisé pour dimensionner les ordres."""
    with _lock:
        cfg = _charger()
        base = _levier_runtime if _levier_runtime is not None else cfg["levier"]
        return round(max(1.0, min(cfg["levier_max"], float(base))), 2)


def ajuster_levier(valeur: float, source: str = "agents IA") -> float:
    """Appelé par les agents IA : règle le levier dans la limite `levier_max`.

    Sans effet si l'utilisateur a désactivé `levier_auto` — le curseur de
    l'interface reste alors maître.
    """
    global _levier_runtime, _levier_source
    with _lock:
        cfg = _charger()
        if not cfg["levier_auto"]:
            return levier_actuel()
        try:
            v = float(valeur)
        except (TypeError, ValueError):
            return levier_actuel()
        if v != v:                                   # NaN
            return levier_actuel()
        _levier_runtime = max(1.0, min(cfg["levier_max"], v))
        _levier_source = source
    return levier_actuel()


def levier_suggere(confiance: float, volatilite_pct: Optional[float] = None) -> float:
    """Levier proposé pour une décision : proportionnel à la conviction des
    agents, réduit quand la volatilité récente s'emballe.

    100 % de confiance sur un marché calme → `levier_max`.
    50 % de confiance → la moitié du budget. Volatilité > 5 % → divisé par deux.
    """
    with _lock:
        lmax = _charger()["levier_max"]
    try:
        conf = max(0.0, min(100.0, float(confiance)))
    except (TypeError, ValueError):
        conf = 50.0
    lev = 1.0 + (lmax - 1.0) * (conf / 100.0)
    try:
        if volatilite_pct is not None:
            vol = abs(float(volatilite_pct))
            if vol > 5.0:
                lev *= 0.5
            elif vol > 3.0:
                lev *= 0.75
    except (TypeError, ValueError):
        pass
    return round(max(1.0, min(lmax, lev)), 2)


# ── Dimensionnement ───────────────────────────────────────────────────
def volume_cible(equity: float, prix: float, taille_contrat: float,
                 confiance: Optional[float] = None,
                 levier: Optional[float] = None) -> Optional[float]:
    """Volume (en lots) pour engager `levier` × équité de notionnel.

    Retourne None si le calcul est impossible (prix ou taille de contrat
    inconnus) — l'appelant conserve alors le volume qu'il avait prévu.
    """
    try:
        equity = float(equity)
        prix = float(prix)
        contrat = float(taille_contrat)
    except (TypeError, ValueError):
        return None
    if not (equity > 0 and prix > 0 and contrat > 0):
        return None

    with _lock:
        cfg = _charger()
        lmax = cfg["levier_max"]
        vmax_lot = cfg["volume_max_lot"]
    # `levier` vient de la décision des agents OU du JSON libre de
    # /api/mt5/trade : un texte ou une liste y est possible. `float(levier)`
    # nu levait alors ValueError/TypeError DEPUIS _send_order_real — donc en
    # plein envoi d'ordre, hors de tout `except` : l'ordre n'était ni envoyé
    # ni journalisé, et l'utilisateur ne lisait qu'un « Erreur interne ».
    # Une valeur inexploitable retombe sur le levier courant.
    if levier is None:
        lev = levier_actuel()
    else:
        try:
            v = float(levier)
        except (TypeError, ValueError):
            logger.warning(f"[trading_config] levier {levier!r} illisible — "
                           f"levier courant appliqué")
            v = levier_actuel()
        if v != v:                                   # NaN
            v = levier_actuel()
        lev = max(1.0, min(lmax, v))

    # La confiance module l'engagement : 100 % → budget plein, 40 % → 40 %.
    fraction = 1.0
    if confiance is not None:
        try:
            fraction = max(0.2, min(1.0, float(confiance) / 100.0))
        except (TypeError, ValueError):
            fraction = 1.0

    volume = (equity * lev * fraction) / (prix * contrat)
    return round(min(vmax_lot, max(0.0, volume)), 4)
