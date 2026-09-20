"""
Auto-trader : boucle de trading en temps réel.
Exécute analyses + ordres en continu. Trading autorisé sans intervention.
"""
import math
import os
import threading
import logging
import time
from datetime import datetime
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

RAPPORT_HEURE = int(os.getenv("RAPPORT_HEURE", "22"))   # heure locale d'envoi
# Rétention des signaux/portefeuille en base. Un cycle écrit 46 signaux PAR
# SYMBOLE (45 agents + décision finale) : à 3 symboles toutes les 60 s, la base
# grossit d'environ 200 000 lignes par jour. Sans purge, une instance laissée
# tourner atteint plusieurs gigaoctets et les lectures du tableau de bord
# ralentissent jusqu'au timeout.
RETENTION_JOURS = int(os.getenv("RETENTION_JOURS", "30"))


def construire_rapport_quotidien(jour: str, db=None) -> str:
    """Résumé de la journée de trading (envoyé par notification)."""
    if db is None:
        from utils.database import Database
        db = Database()
    ordres = db.ordres_du_jour(jour)
    reussis = [o for o in ordres if o.get("succes")]
    reels = [o for o in ordres if o.get("mode") == "reel"]
    lignes = [
        f"📊 Rapport du {jour}",
        f"Ordres : {len(ordres)} ({len(reussis)} réussis, {len(ordres) - len(reussis)} échoués)",
        f"Réel : {len(reels)} · Simulation : {len(ordres) - len(reels)}",
    ]
    try:
        from utils.mt5_manager import get_mt5_manager
        h = get_mt5_manager().get_historique_reel(jours=1)
        if h.get("synced"):
            lignes.append(f"P&L réel du jour : {h['pnl_total']:+.2f} {h.get('devise', 'USD')} "
                          f"({h['gagnants']}/{h['nb_trades']} gagnants)")
    except Exception:
        pass
    try:
        from utils.risk_guard import get_risk_guard
        rs = get_risk_guard().get_status()
        if rs.get("kill_switch"):
            lignes.append(f"🛑 Kill-switch déclenché : {rs.get('raison', '')}")
        elif rs.get("actif"):
            lignes.append(f"🛡️ Perte du jour : {rs.get('perte_jour_pct', 0)}% "
                          f"(limite {rs.get('perte_max_pct', 5)}%)")
    except Exception:
        pass
    return "\n".join(lignes)


_objectif_cache: Dict[str, Any] = {"ts": 0.0, "valeur": None}
_objectif_lock = threading.Lock()
# Durée de vie du cache. Le tableau de bord interroge /api/auto-trader/status
# toutes les 3 s : sans cache, CHAQUE sondage relançait un history_deals_get
# + positions_get SOUS LE VERROU du terminal MetaTrader — le même que celui
# dont order_send a besoin. Le suivi de l'objectif volait ainsi du temps
# d'exécution aux ordres, en continu.
OBJECTIF_TTL_S = float(os.getenv("OBJECTIF_TTL_S", "15"))


def objectif_du_jour(force: bool = False) -> Dict[str, Any]:
    """Progression vers l'objectif de gain quotidien réglé dans l'interface.

    Additionne le RÉALISÉ du jour (deals clôturés chez le courtier) et le
    LATENT (P&L des positions encore ouvertes) : c'est ce que l'utilisateur
    voit sur son compte, et c'est la seule mesure honnête d'un objectif
    exprimé en euros par jour.

    Résultat mis en cache OBJECTIF_TTL_S secondes (interroger le courtier à
    chaque sondage du tableau de bord ralentissait l'envoi des ordres).
    `force=True` ignore le cache.
    """
    from utils import trading_config
    if not force:
        with _objectif_lock:
            frais = (_objectif_cache["valeur"] is not None
                     and time.time() - _objectif_cache["ts"] < OBJECTIF_TTL_S)
            if frais:
                return dict(_objectif_cache["valeur"])
    objectif = float(trading_config.get("objectif_journalier", 80.0))
    capital = float(trading_config.get("capital_reference", 100.0))
    infos: Dict[str, Any] = {
        "objectif": round(objectif, 2),
        "capital_reference": round(capital, 2),
        "realise": 0.0,
        "latent": 0.0,
        "total": 0.0,
        "progression_pct": 0.0,
        "atteint": False,
        "devise": "USD",
        "synced": False,
        # Ce que l'objectif représente rapporté au capital : 80 €/jour sur
        # 100 € de capital = 80 % PAR JOUR. Affiché tel quel dans l'interface.
        "rendement_vise_pct": round(objectif / capital * 100, 1) if capital > 0 else 0.0,
    }
    try:
        from utils.mt5_manager import get_mt5_manager
        mt5 = get_mt5_manager()
        jour = datetime.now().strftime("%Y-%m-%d")
        h = mt5.get_historique_reel(jours=1)
        if h.get("synced"):
            infos["synced"] = True
            infos["devise"] = h.get("devise", "USD")
            for e in h.get("par_jour", []):
                if e.get("date") == jour:
                    infos["realise"] = round(float(e.get("pnl") or 0), 2)
        infos["latent"] = round(sum(float(p.get("profit") or 0)
                                    for p in mt5.get_positions()), 2)
    except Exception as e:
        logger.warning(f"[Objectif] calcul impossible : {e}")

    infos["total"] = round(infos["realise"] + infos["latent"], 2)
    if objectif > 0:
        infos["progression_pct"] = round(infos["total"] / objectif * 100, 1)
        infos["atteint"] = infos["total"] >= objectif
    with _objectif_lock:
        _objectif_cache["ts"] = time.time()
        _objectif_cache["valeur"] = dict(infos)
    return infos


def purger_base(jours: int = None) -> int:
    """Supprime les signaux et l'historique de portefeuille plus vieux que
    `jours` (défaut RETENTION_JOURS). Retourne le nombre de signaux supprimés.

    Appelée une fois par jour par la boucle auto-trader ET au démarrage du
    serveur : un utilisateur qui n'utilise que le bouton « Analyser » accumule
    lui aussi 46 signaux par symbole et par clic."""
    jours = RETENTION_JOURS if jours is None else jours
    if jours <= 0:
        return 0
    try:
        from utils.database import Database
        supprimes = Database().nettoyer_anciens_signaux(jours)
        if supprimes:
            logger.info(f"[Maintenance] {supprimes} signaux de plus de "
                        f"{jours} jours supprimés")
        return supprimes
    except Exception as e:
        logger.warning(f"[Maintenance] Purge de la base impossible : {e}")
        return 0


def completer_sl_tp(action: str, prix: float, sl: float, tp: float):
    """Complète SL/TP manquants à partir du prix.

    Les pourcentages viennent des réglages (Réglages ⚙️ → Moteur de
    performance) : `sl_pct` / `tp_pct`. Lus À CHAQUE APPEL et non au
    chargement du module, sinon un changement dans l'interface n'aurait
    d'effet qu'au redémarrage du serveur.
    """
    from utils import trading_config
    sl_defaut_pct = float(trading_config.get("sl_pct", 2.0))
    tp_defaut_pct = float(trading_config.get("tp_pct", 3.0))

    def _utilisable(v) -> bool:
        """Nombre fini et strictement positif.

        - NÉGATIF ou nul → traité comme ABSENT : plusieurs agents calculent
          leurs stops par soustraction (prix − k·ATR) et passent sous zéro sur
          un actif qui décroche. `if not sl` ne voyait que le 0.
        - NaN / infini → également absent. Une barre yfinance manquante donne
          un prix NaN ; `not nan` vaut False, donc le NaN traversait tous les
          tests et ressortait en stop-loss NaN envoyé au courtier.
        """
        try:
            f = float(v)
        except (TypeError, ValueError):
            return False
        return math.isfinite(f) and f > 0

    sl = float(sl) if _utilisable(sl) else 0.0
    tp = float(tp) if _utilisable(tp) else 0.0
    if not _utilisable(prix):
        return sl, tp
    if not sl:
        sl = prix * (1 - sl_defaut_pct / 100) if action == "BUY" else prix * (1 + sl_defaut_pct / 100)
    if not tp:
        tp = prix * (1 + tp_defaut_pct / 100) if action == "BUY" else prix * (1 - tp_defaut_pct / 100)
    return sl, tp


class AutoTrader:
    """Boucle de trading autonome : analyse → signal → ordre MT5 (réel ou simulé)."""

    def __init__(self):
        self._running      = False
        self._thread: Optional[threading.Thread] = None
        self._thread_pos: Optional[threading.Thread] = None   # surveillance positions
        self._lock         = threading.Lock()
        # Verrou DÉDIÉ au bandeau d'erreurs : il est alimenté par le thread
        # d'analyse ET par celui de surveillance des positions, et lu par l'API.
        # Distinct de _lock pour ne jamais coupler l'affichage au start/stop.
        self._errors_lock  = threading.Lock()
        # Sérialise TOUTE exécution d'ordre (boucle auto + endpoints) : garantit
        # que [anti-empilement → fermeture inverse → ordre → cooldown] est
        # atomique et qu'un ordre ne peut pas partir en double sous concurrence.
        self._exec_lock    = threading.Lock()
        # Numéro de génération : invalide immédiatement toute ancienne boucle
        # lors d'un stop()/start() rapproché (sinon deux boucles simultanées →
        # ordres réels dupliqués).
        self._generation   = 0
        self._interval     = 60
        self._symboles: List[str] = []
        self._last_run: Optional[str] = None
        self._last_signals: Dict     = {}
        self._cycle_count  = 0
        self._errors: List[str]      = []
        self._positions: List[Dict]  = []
        self._pos_check_interval     = 5
        self._trading_autorise       = True   # autorisation globale de trader
        # Kill-switch : suspend l'OUVERTURE de nouvelles positions, mais laisse
        # tourner la surveillance (trailing/breakeven) — couper la protection
        # alors que des positions réelles sont ouvertes serait dangereux.
        self._trading_suspendu       = False
        self._raison_suspension      = ""
        # Anti-churn : pas de ré-entrée sur le même symbole+sens avant N s.
        # Réglable dans l'interface (0 = ré-entrée immédiate autorisée) ;
        # resynchronisé à chaque cycle pour prendre effet sans redémarrage.
        from utils import trading_config
        self._cooldown_s = int(trading_config.get("cooldown_s", 60))
        self._derniers_trades: Dict[str, Dict] = {}
        self._dernier_rapport_jour: Optional[str] = None
        self._derniere_purge_jour: Optional[str] = None
        # Pourquoi aucun ordre n'est parti pour ce symbole (cooldown, marché
        # fermé, plafond atteint…). Ces abstentions n'existaient que dans les
        # journaux du serveur : l'interface affichait « Aucun ordre exécuté »
        # sans la moindre explication, et le robot paraissait cassé alors qu'il
        # appliquait exactement les règles demandées. Un motif par symbole,
        # donc borné, et effacé dès qu'un ordre part.
        self._abstentions: Dict[str, Dict[str, Any]] = {}

    def _abstenir(self, symbole: str, motif: str):
        """Mémorise POURQUOI aucun ordre n'est parti sur ce symbole, et le
        journalise. Remplace les `logger.info` muets : ces motifs remontent
        maintenant jusqu'au tableau de bord."""
        logger.info(f"[Auto-trader] {symbole} : {motif}")
        with self._errors_lock:
            self._abstentions[symbole] = {
                "symbole": symbole, "motif": motif,
                "heure": datetime.now().strftime("%H:%M:%S"),
            }

    def _abstentions_recentes(self) -> List[Dict[str, Any]]:
        with self._errors_lock:
            return list(self._abstentions.values())

    def _cooldown_actif(self, symbole: str, action: str) -> bool:
        if self._cooldown_s <= 0:          # anti-churn désactivé par l'utilisateur
            return False
        d = self._derniers_trades.get(symbole)
        return bool(d and d["action"] == action
                    and time.time() - d["ts"] < self._cooldown_s)

    def _purger_cooldowns(self):
        """Oublie les cooldowns expirés (le dictionnaire grossissait sans fin
        sur une instance laissée tourner des semaines avec beaucoup de symboles)."""
        limite = time.time() - self._cooldown_s
        for sym in [s for s, d in self._derniers_trades.items() if d["ts"] < limite]:
            self._derniers_trades.pop(sym, None)

    # ── Démarrage / Arrêt ────────────────────────────────────────────
    def start(self, symboles: List[str], interval: int = 60) -> Dict[str, Any]:
        with self._lock:
            if self._running and self._trading_suspendu:
                # Boucle vivante mais ouvertures suspendues par le kill-switch.
                # Répondre « déjà en cours » enfermait l'utilisateur : le bouton
                # affichait « ARRÊTER », « DÉMARRER » renvoyait une erreur, et
                # plus aucun ordre ne partait jusqu'à un arrêt/relance manuel.
                # Cliquer « Démarrer » ICI est une demande explicite de
                # reprendre : on lève la suspension et on réarme le kill-switch.
                self._trading_suspendu = False
                self._raison_suspension = ""
                if symboles:
                    self._symboles = symboles
                try:
                    from utils.risk_guard import get_risk_guard
                    get_risk_guard().reset_kill()
                except Exception as e:
                    logger.warning(f"Réarmement du kill-switch : {e}")
                try:
                    from utils.mt5_manager import get_mt5_manager
                    get_mt5_manager().set_auto_trading(True)
                except Exception:
                    pass
                logger.info("Auto-trader : suspension levée, ouvertures reprises")
                return {"success": True, "reprise": True,
                        "interval": self._interval, "symboles": self._symboles}
            if self._running:
                return {"success": False, "error": "Auto-trader déjà en cours"}
            self._symboles = symboles or []
            self._interval = max(30, int(interval))
            self._running  = True
            self._errors   = []
            self._trading_suspendu  = False   # nouveau départ = suspension levée
            self._raison_suspension = ""
            self._generation += 1
            gen = self._generation
            self._thread   = threading.Thread(target=self._boucle, args=(gen,), daemon=True)
            self._thread.start()
            # Thread DÉDIÉ à la protection des positions : jamais bloqué par
            # une synthèse LLM longue du cycle d'analyse.
            self._thread_pos = threading.Thread(target=self._boucle_positions,
                                                args=(gen,), daemon=True)
            self._thread_pos.start()
            logger.info(f"Auto-trader démarré — {len(self._symboles)} symboles, intervalle {self._interval}s")
            return {"success": True, "interval": self._interval, "symboles": self._symboles}

    def stop(self) -> Dict[str, Any]:
        with self._lock:
            self._running = False
            self._generation += 1   # invalide la boucle en cours immédiatement
        logger.info("Auto-trader arrêté")
        return {"success": True}

    # ── Boucle principale (analyses) ──────────────────────────────────
    def _boucle(self, gen: int):
        next_analyse = time.time()            # premier cycle immédiat

        # `gen == self._generation` : une boucle d'une génération antérieure
        # (stop()/start() rapproché) sort immédiatement — jamais deux boucles.
        while self._running and gen == self._generation:
            now = time.time()

            if now >= next_analyse and self._running and gen == self._generation:
                # Suspendu par le kill-switch : on n'analyse plus (donc plus
                # aucune ouverture), mais la boucle et la surveillance des
                # positions restent vivantes.
                if not self._trading_suspendu:
                    self._cycle_analyse(gen)
                next_analyse = now + self._interval

            self._rapport_quotidien_si_du()
            self._purge_quotidienne_si_due()
            time.sleep(1)

    # ── Boucle de PROTECTION des positions (thread dédié) ─────────────
    def _boucle_positions(self, gen: int):
        """Surveille les positions (breakeven +1R, trailing stop) dans un thread
        SÉPARÉ de l'analyse.

        Indispensable : un cycle d'analyse peut durer 1 à 2 minutes (synthèses
        LLM Ollama/Claude, jusqu'à 45 s par symbole). Dans une boucle unique,
        la remontée protectrice des stops restait gelée pendant tout ce temps —
        exactement lorsque le marché bouge. Ici, elle s'exécute toutes les
        `_pos_check_interval` secondes quoi qu'il arrive."""
        while self._running and gen == self._generation:
            self._verifier_positions()
            # sommeil fractionné : arrêt réactif sans attendre l'intervalle plein
            fin = time.time() + self._pos_check_interval
            while time.time() < fin and self._running and gen == self._generation:
                time.sleep(0.5)

    def _cycle_analyse(self, gen: int = None):
        self._cycle_count += 1
        self._last_run = datetime.now().strftime("%H:%M:%S")
        logger.info(f"[Auto-trader] Cycle #{self._cycle_count} — {self._symboles}")

        # Les réglages de l'interface prennent effet au cycle suivant, sans
        # redémarrage du serveur.
        from utils import trading_config
        self._cooldown_s = int(trading_config.get("cooldown_s", 60))

        try:
            # ── Utiliser le singleton orchestrateur ───────────────────
            from backend.main import get_orchestrateur
            chef = get_orchestrateur()

            rapport = chef.orchestrer(self._symboles)
            decisions = rapport.get("decisions", {})
            self._last_signals = decisions

            if not decisions:
                self._ajouter_erreur("Aucune décision reçue du Chef d'Orchestre")
                return

            # ── Exécuter les ordres ───────────────────────────────────
            from utils.mt5_manager import get_mt5_manager
            mt5 = get_mt5_manager()
            mt5_status = mt5.get_status()

            # Compte MetaTrader 5 connecté requis pour trader.
            if not mt5_status.get("connected"):
                if mt5.connect(0, "", "demo").get("success"):
                    pass  # connexion établie
                elif os.getenv("AUTORISER_SIMULATION", "true").lower() in ("1", "true", "yes", "oui") and not mt5._lib_available:
                    # Démo hors-ligne uniquement si la simulation est autorisée
                    mt5._connect_simulated(99999999, "autotrader", "Simulation", "demo")
                else:
                    self._ajouter_erreur("Compte non connecté — connectez votre compte AvaTrade (MetaTrader 5)")
                    return

            # ── Objectif du jour atteint ? (option « prendre ses gains ») ─
            if trading_config.get("stop_sur_objectif", False):
                obj = objectif_du_jour()
                if obj.get("atteint"):
                    msg = (f"Objectif du jour atteint ({obj['total']:+.2f} "
                           f"{obj.get('devise', '')} ≥ {obj['objectif']}) — "
                           f"aucune nouvelle ouverture")
                    logger.info(f"[Auto-trader] {msg}")
                    self._ajouter_erreur(msg)
                    return

            from utils.risk_guard import get_risk_guard
            risk = get_risk_guard()

            for symbole, decision in decisions.items():
                # Arrêt demandé pendant le cycle (clic « Arrêter », kill-switch,
                # redémarrage) : ne PAS continuer à envoyer les ordres des
                # symboles restants — un cycle peut durer plus d'une minute.
                if not self._running or (gen is not None and gen != self._generation):
                    logger.info("[Auto-trader] Arrêt demandé — cycle interrompu")
                    return

                action    = decision.get("action")
                confiance = decision.get("confiance", 50)

                if action not in ("BUY", "SELL"):
                    # Cas le PLUS fréquent quand « rien ne se passe » : les 45
                    # agents n'ont pas dégagé de consensus (il en faut 3 dans le
                    # même sens). Ce n'est pas une panne, mais l'utilisateur
                    # doit pouvoir le lire.
                    self._abstenir(symbole, f"aucun ordre : décision « {action} » "
                                            f"(pas de consensus acheteur ni vendeur)")
                    continue

                # ── Filtre de conviction (0 = aucun filtre) ───────────
                seuil = float(trading_config.get("confiance_min", 0.0))
                if seuil > 0 and float(confiance or 0) < seuil:
                    self._abstenir(symbole, f"confiance {confiance}% sous le seuil "
                                            f"réglé ({seuil}%)")
                    continue

                # ── Données simulées + compte réel = refus ────────────
                # yfinance indisponible → prix inventés. On n'envoie pas un
                # ordre RÉEL fondé sur des prix fictifs. Levable dans les
                # réglages (`bloquer_donnees_simulees`), au risque d'ouvrir
                # des positions dimensionnées sur des prix inventés.
                if (trading_config.get("bloquer_donnees_simulees", True)
                        and decision.get("donnees", {}).get("source_simulee")
                        and mt5.est_reel()):
                    msg = (f"{symbole} ignoré : données de marché indisponibles "
                           "(prix simulés) — aucun ordre réel sur des prix fictifs")
                    logger.warning(f"[Auto-trader] {msg}")
                    self._ajouter_erreur(msg)
                    self._abstenir(symbole, "cours indisponibles (prix simulés) — "
                                            "aucun ordre réel sur des prix fictifs")
                    continue

                # ── Marché fermé ? (week-end, hors séance) ────────────
                if trading_config.get("respecter_heures_marche", True):
                    from utils.market_hours import marche_ouvert
                    ouvert, raison_marche = marche_ouvert(symbole)
                    if not ouvert:
                        self._abstenir(symbole, raison_marche or "marché fermé")
                        continue

                # ── Garde-fous DURS avant tout ordre ──────────────────
                autorise, raison = risk.evaluer(mt5.get_portfolio())
                if not autorise:
                    self._ajouter_erreur(f"Ordre bloqué (sécurité) : {raison}")
                    self._abstenir(symbole, f"bloqué par la sécurité : {raison}")
                    logger.warning(f"[Auto-trader] Ordre {symbole} bloqué : {raison}")
                    if "Kill-switch" in raison:
                        # Perte journalière dépassée → PLUS AUCUNE ouverture.
                        # On NE coupe PAS self._running : le thread de
                        # surveillance doit continuer à protéger (breakeven /
                        # trailing) les positions réelles encore ouvertes.
                        mt5.set_auto_trading(False)
                        self._trading_suspendu = True
                        self._raison_suspension = raison
                        try:
                            from utils.notifier import notifier
                            notifier(f"🛑 SÉCURITÉ : {raison} — surveillance des "
                                     f"positions maintenue")
                        except Exception:
                            pass
                        break
                    continue

                # ── Séquence atomique : anti-empilement → fermeture inverse
                #    → ordre → cooldown, sous verrou pour interdire tout ordre
                #    dupliqué sous concurrence.
                with self._exec_lock:
                    # Anti-churn : cooldown sur le même symbole+sens
                    if self._cooldown_actif(symbole, action):
                        reste = int(self._cooldown_s
                                    - (time.time() - self._derniers_trades[symbole]["ts"]))
                        self._abstenir(symbole, f"{action} en attente : anti ré-entrée "
                                                f"encore {max(0, reste)} s")
                        continue

                    # Anti-empilement : une seule position par sens et par
                    # symbole. Désactivé par défaut (`anti_empilement`) — on
                    # empile alors jusqu'à `max_positions_symbole` pour renforcer
                    # une position gagnante au lieu de laisser passer le signal.
                    if trading_config.get("anti_empilement", False):
                        if action in mt5.directions_ouvertes(symbole):
                            self._abstenir(symbole, f"position {action} déjà ouverte "
                                                    f"(anti-empilement activé)")
                            continue
                    else:
                        plafond_sym = int(trading_config.get("max_positions_symbole", 3))
                        deja = self._nb_positions_symbole(symbole, action, mt5)
                        if deja >= plafond_sym:
                            self._abstenir(symbole, f"{deja} position(s) {action} déjà "
                                                    f"ouverte(s) — plafond par symbole "
                                                    f"atteint ({plafond_sym})")
                            continue

                    # Fermer la position inverse existante AVANT d'ouvrir.
                    # Si la fermeture d'une vraie position inverse échoue, on
                    # n'ouvre PAS (sinon les deux sens coexistent sur compte
                    # hedging = perte doublée) — on retentera au cycle suivant.
                    if not self._fermer_position_inverse(symbole, action, mt5):
                        self._ajouter_erreur(f"{symbole} : fermeture inverse échouée — ordre reporté")
                        self._abstenir(symbole, "fermeture de la position inverse "
                                                "échouée — ordre reporté au cycle suivant")
                        continue

                    # Volume : proportionnel à la confiance, plafonné par le
                    # réglage « volume max ». Le dimensionnement DÉFINITIF est
                    # calculé côté courtier (levier × équité / notionnel du lot),
                    # là où le prix d'exécution et la taille de contrat sont
                    # connus — cette valeur n'est qu'un point de départ.
                    vmax_lot = float(trading_config.get("volume_max_lot", 5.0))
                    volume = round(max(0.01, min(vmax_lot, vmax_lot * confiance / 100)), 2)

                    # Levier décidé par les agents pour ce symbole (transporté
                    # dans les données de la décision), borné par le plafond
                    # réglé dans l'interface (1 à 10).
                    levier = ((decision.get("donnees") or {}).get("levier")
                              or trading_config.levier_actuel())

                    prix = decision.get("prix_entree") or 0.0
                    if trading_config.get("stop_loss_obligatoire", True):
                        sl, tp = completer_sl_tp(action, prix,
                                                 decision.get("stop_loss") or 0.0,
                                                 decision.get("take_profit") or 0.0)
                    else:
                        sl = decision.get("stop_loss") or 0.0
                        tp = decision.get("take_profit") or 0.0

                    result = mt5.execute_trade({
                        "symbol":      symbole,
                        "action":      action,
                        "volume":      volume,
                        "prix_entree": prix,
                        "stop_loss":   sl,
                        "take_profit": tp,
                        "confiance":   confiance,
                        "levier":      levier,
                    })
                    if result.get("success"):
                        self._derniers_trades[symbole] = {"action": action, "ts": time.time()}
                        self._purger_cooldowns()
                        with self._errors_lock:
                            self._abstentions.pop(symbole, None)   # ordre parti
                    else:
                        self._abstenir(symbole, f"ordre {action} refusé : "
                                                f"{result.get('error', 'motif inconnu')}")

                status = "OK" if result.get("success") else f"ECHEC {result.get('error','')}"
                try:
                    logger.info(f"[Auto-trader] {action} {symbole} vol={volume} "
                                f"conf={confiance}% -> {status}")
                except Exception:
                    # Un problème d'AFFICHAGE (console Windows cp1252) ne doit
                    # JAMAIS interrompre le cycle : sans ce garde, l'exception
                    # remontait et les symboles suivants n'étaient plus traités.
                    pass
                try:
                    from utils.notifier import notifier
                    if result.get("success"):
                        notifier(f"{action} {symbole} {volume} lot exécuté @ {result.get('price','?')}")
                    else:
                        self._ajouter_erreur(f"{action} {symbole} échoué : {result.get('error','')}")
                except Exception:
                    if not result.get("success"):
                        self._ajouter_erreur(f"{action} {symbole} échoué : {result.get('error','')}")

        except Exception as exc:
            self._ajouter_erreur(str(exc))
            logger.error(f"[Auto-trader] Erreur cycle : {exc}", exc_info=True)

    @staticmethod
    def _nb_positions_symbole(symbole: str, action: str, mt5) -> int:
        """Positions déjà ouvertes sur ce symbole DANS CE SENS.

        Le courtier nomme le symbole autrement que Yahoo (BTC-USD → BTCUSD) :
        on compare aux deux noms, sinon l'empilement ne serait jamais plafonné.
        """
        try:
            sym = mt5.resoudre_symbole(symbole) or symbole
        except Exception:
            sym = symbole
        try:
            return sum(1 for p in mt5.get_positions()
                       if p.get("symbol") in (sym, symbole) and p.get("type") == action)
        except Exception:
            return 0

    def _verifier_positions(self):
        """Récupère les positions ouvertes depuis MT5 + gestion active."""
        try:
            from utils.mt5_manager import get_mt5_manager
            mt5 = get_mt5_manager()
            if not mt5.get_status().get("connected"):
                return
            self._positions = mt5.get_positions()
            # Gestion active : breakeven à +1R puis trailing stop
            mt5.gerer_trailing()
        except Exception as exc:
            # La protection active des positions (trailing/breakeven) ne doit
            # pas échouer en silence sur un compte réel — on la remonte.
            logger.warning(f"[Auto-trader] Surveillance des positions : {exc}")
            self._ajouter_erreur(f"Surveillance positions : {exc}")

    def _fermer_position_inverse(self, symbole: str, new_action: str, mt5) -> bool:
        """Ferme une position dans le sens contraire avant d'ouvrir.

        Utilise la VRAIE clôture par ticket (mt5.fermer_position) : sur un
        compte hedging (défaut AvaTrade), un simple ordre inverse ouvrirait
        une position opposée au lieu de fermer l'existante.

        Retourne True s'il n'y a rien à fermer OU si toutes les fermetures ont
        réussi ; False si au moins une position réellement inverse n'a PAS pu
        être fermée (l'appelant doit alors s'abstenir d'ouvrir).
        """
        if not mt5._lib_available:
            return True
        ok = True
        try:
            sym = mt5.resoudre_symbole(symbole) or symbole
            for p in mt5.get_positions():
                if p.get("symbol") != sym:
                    continue
                if p.get("type") != new_action:   # sens opposé → fermer
                    r = mt5.fermer_ticket(p.get("ticket"))
                    reussi = bool(r.get("success"))
                    ok = ok and reussi
                    statut = "OK" if reussi else f"ECHEC {r.get('error', '')}"
                    logger.info(f"[Auto-trader] Fermeture inverse {p.get('type')} "
                                f"{symbole} -> {statut}")
        except Exception as exc:
            logger.warning(f"[Auto-trader] Fermeture inverse : {exc}")
            return False
        return ok

    def _rapport_quotidien_si_du(self):
        """Envoie le résumé de la journée (une fois par jour, après RAPPORT_HEURE)."""
        if os.getenv("RAPPORT_QUOTIDIEN", "true").lower() not in ("1", "true", "yes", "oui"):
            return
        now = datetime.now()
        jour = now.strftime("%Y-%m-%d")
        if now.hour < RAPPORT_HEURE or self._dernier_rapport_jour == jour:
            return
        self._dernier_rapport_jour = jour
        try:
            from utils.notifier import notifier, canaux_configures
            if not canaux_configures():
                return
            notifier(construire_rapport_quotidien(jour))
            logger.info("[Auto-trader] Rapport quotidien envoyé")
        except Exception as e:
            logger.warning(f"Rapport quotidien : {e}")

    def _purge_quotidienne_si_due(self):
        """Purge les signaux et l'historique de portefeuille périmés (1×/jour).

        `Database.nettoyer_anciens_signaux()` existait mais n'était appelée
        NULLE PART : la base grossissait indéfiniment sur une instance laissée
        tourner (~200 000 lignes/jour à 3 symboles et 60 s d'intervalle),
        jusqu'à ralentir le tableau de bord et saturer le disque.

        Déportée dans un thread : la suppression et le VACUUM peuvent durer
        plusieurs secondes et ne doivent pas retarder la surveillance des
        positions ni le cycle d'analyse.
        """
        if RETENTION_JOURS <= 0:          # 0 = purge désactivée
            return
        jour = datetime.now().strftime("%Y-%m-%d")
        if self._derniere_purge_jour == jour:
            return
        self._derniere_purge_jour = jour
        threading.Thread(target=purger_base, daemon=True).start()

    def _ajouter_erreur(self, msg: str):
        ts = datetime.now().strftime("%H:%M:%S")
        # Anti-inondation : la surveillance des positions tourne toutes les 5 s.
        # Une panne persistante (courtier injoignable) remplissait les 20 lignes
        # du bandeau avec le MÊME message en moins de deux minutes, chassant
        # toutes les autres erreurs — dont celles qui expliquent un ordre refusé.
        with self._errors_lock:
            if self._errors and self._errors[0].endswith(msg):
                self._errors[0] = f"[{ts}] {msg}"
                return
            self._errors.insert(0, f"[{ts}] {msg}")
            if len(self._errors) > 20:
                self._errors = self._errors[:20]

    def _erreurs_recentes(self, n: int = 5) -> List[str]:
        """Copie du bandeau d'erreurs SOUS VERROU : la liste est réécrite par
        deux threads (analyse + surveillance des positions) pendant que l'API
        la lit."""
        with self._errors_lock:
            return list(self._errors[:n])

    # ── Statut ────────────────────────────────────────────────────────
    def get_status(self) -> Dict[str, Any]:
        from utils.mt5_manager import get_mt5_manager
        mt5 = get_mt5_manager()
        mt5_journal = mt5.get_journal(20)
        mt5_status  = mt5.get_status()
        # TROIS états, et non deux. Un compte DÉMO AvaTrade est une vraie
        # connexion MetaTrader (les ordres partent au courtier), mais l'argent
        # est fictif : l'afficher « Mode RÉEL — les ordres partent sur votre
        # compte AvaTrade » était trompeur, et la confirmation de démarrage
        # annonçait « argent réel » à tort. Le jour où le compte est financé,
        # cet avertissement doit encore vouloir dire quelque chose.
        if mt5._lib_available and mt5_status.get("connected"):
            is_sim = (mt5_status.get("account_info") or {}).get("simulated", False)
        else:
            is_sim = True

        try:
            est_demo = mt5.est_demo() if not is_sim else False
        except Exception:
            est_demo = False
        mode = "simulation" if is_sim else ("demo" if est_demo else "reel")

        # Diagnostic : pourquoi le mode est simulation (aide l'utilisateur)
        raison = ""
        if is_sim:
            if not mt5._lib_available:
                raison = ("Librairie MetaTrader5 absente de l'application. "
                          "Recompilez l'exe avec build_exe.bat (elle est incluse "
                          "au build sur Windows).")
            elif not mt5_status.get("connected"):
                raison = "Aucun compte connecté — cliquez « Connexion AvaTrade »."
            else:
                raison = "Compte de démonstration."

        # Bouton « AlgoTrading » du terminal : sans lui, TOUS les ordres sont
        # rejetés en 10027. Remonté en permanence pour que le blocage soit
        # visible AVANT le premier ordre refusé, pas après.
        try:
            algo = mt5.algotrading_actif()
        except Exception:
            algo = None
        if algo is False:
            self._ajouter_erreur(
                "Bouton « AlgoTrading » désactivé dans MetaTrader 5 — aucun "
                "ordre ne peut partir. Cliquez-le dans la barre d'outils du "
                "terminal (Ctrl+E).")

        return {
            "running":       self._running,
            "suspendu":      self._trading_suspendu,
            "raison_suspension": self._raison_suspension,
            "interval":      self._interval,
            "symboles":      self._symboles,
            "cycle_count":   self._cycle_count,
            "last_run":      self._last_run,
            "last_signals":  self._last_signals,
            "positions":     self._positions,
            "journal":       mt5_journal,
            "errors":        self._erreurs_recentes(5),
            "mode":          mode,
            "mode_raison":   raison,
            "algotrading":   algo,          # None = inconnu, False = bouton éteint
            "mt5_connected": mt5_status.get("connected", False),
            "risk":          self._risk_status(),
            "objectif":      self._objectif_status(),
            "trading":       self._trading_status(),
            # Pourquoi aucun ordre n'est parti, symbole par symbole.
            "abstentions":   self._abstentions_recentes(),
        }

    @staticmethod
    def _risk_status() -> Dict[str, Any]:
        try:
            from utils.risk_guard import get_risk_guard
            return get_risk_guard().get_status()
        except Exception:
            return {}

    @staticmethod
    def _objectif_status() -> Dict[str, Any]:
        try:
            return objectif_du_jour()
        except Exception:
            return {}

    @staticmethod
    def _trading_status() -> Dict[str, Any]:
        """Réglages de performance actifs (levier courant, risque par ordre) :
        affichés en permanence sur l'accueil pour qu'on sache à quelle
        agressivité le robot tourne réellement."""
        try:
            from utils import trading_config
            cfg = trading_config.tout()
            return {
                "levier":        cfg["levier_actuel"],
                "levier_max":    cfg["levier_max"],
                "levier_source": cfg["levier_source"],
                "levier_auto":   cfg["levier_auto"],
                "risque_par_trade_pct": cfg["risque_par_trade_pct"],
                "volume_max_lot":       cfg["volume_max_lot"],
            }
        except Exception:
            return {}


# ── Singleton ────────────────────────────────────────────────────────
_auto_trader: Optional[AutoTrader] = None
_auto_trader_lock = threading.Lock()


def get_auto_trader() -> AutoTrader:
    global _auto_trader
    if _auto_trader is None:
        with _auto_trader_lock:
            if _auto_trader is None:
                _auto_trader = AutoTrader()
    return _auto_trader
