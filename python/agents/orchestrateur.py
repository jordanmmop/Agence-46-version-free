import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from concurrent.futures import ThreadPoolExecutor, as_completed, TimeoutError as FutureTimeout
from datetime import datetime
from typing import Any, Dict, List, Optional
import logging
import threading
from config import CAPITAL_INITIAL
from models.signal import Signal, ActionSignal
from models.portfolio import Portfolio
from utils.market_data import FetcheurDonnees
from utils.database import Database

AGENT_TIMEOUT = 30   # secondes max par agent
SYNTHESE_TIMEOUT = 45  # secondes max pour la synthèse du moteur IA local

logger = logging.getLogger(__name__)


def _prix_exploitable(prix) -> bool:
    """Un prix sur lequel on peut réellement calculer un ordre.

    `not float(prix or 0)` ne suffisait pas : `nan` est VRAI au sens booléen,
    donc un prix NaN (barre yfinance manquante) passait le garde-fou
    « pas d'ordre réel sur des données inutilisables » — et produisait un
    stop-loss et un take-profit NaN envoyés au courtier.
    """
    import math
    try:
        p = float(prix)
    except (TypeError, ValueError):
        return False
    return math.isfinite(p) and p > 0


class ChefOrchestre:
    ID = "ORCH-000"
    NOM = "Chef d'Orchestre"
    GROUPE = "orchestration"
    DESCRIPTION = "Coordonne et synthétise les 45 agents pour prendre les décisions finales de trading"

    def __init__(self):
        self.statut = "idle"
        self.portfolio = Portfolio(capital_initial=CAPITAL_INITIAL)
        self.db = Database()
        self.derniere_analyse: Optional[datetime] = None
        self.nb_cycles = 0
        self.historique_decisions: List[Dict] = []
        # Sérialise orchestrer() : le singleton peut être appelé par la boucle
        # AutoTrader ET par /api/analyser — sans verrou, mutations concurrentes
        # de portfolio/nb_cycles/historique et écritures SQLite en course.
        self._orch_lock = threading.Lock()
        # L'orchestrateur a aussi son assistant IA local (vérifie la décision finale)
        from agents.assistant import AssistantIA
        self.assistant = AssistantIA(self.ID, self.NOM, self.GROUPE)

    @staticmethod
    def _moteur_local_disponible() -> bool:
        """Le moteur IA sélectionné (Ollama ou Hermès) répond-il ?

        Le test portait uniquement sur Ollama : avec Hermès sélectionné, le
        Chef d'Orchestre concluait « IA locale indisponible » et retombait sur
        sa synthèse textuelle de secours alors qu'Hermès tournait.
        """
        from utils import moteur_ia
        return moteur_ia.disponible(moteur_ia.moteur_actif())

    def _synthese_locale(self, symbole: str, signaux: List[Signal],
                         direction: str, prix: float) -> str:
        """Synthèse produite par le moteur IA local (Ollama ou Hermès)."""
        from utils import moteur_ia
        target_val = "BUY" if direction == "ACHAT" else "SELL"
        lignes = [
            f"- {s.agent_nom} (conf:{s.confiance:.0f}%): {s.raisonnement[:80]}"
            for s in signaux[:12] if s.action.value in [target_val, "HOLD", "WATCH"]
        ]
        prompt = (
            f"Tu es un analyste financier. Synthèse pour {symbole} @ {prix:.4f}:\n"
            f"Direction recommandée: {direction}\n"
            f"Signaux des agents:\n" + "\n".join(lignes[:8]) +
            f"\n\nRéponds en 2 phrases maximum pourquoi {direction} est la bonne décision."
        )
        texte = moteur_ia.chat([
            {"role": "system", "content": "Tu es un expert en trading. Réponds en français, concis."},
            {"role": "user",   "content": prompt},
        ], timeout=SYNTHESE_TIMEOUT)
        return texte or self._consensus_textuel(signaux, direction, prix)

    @staticmethod
    def _agents_du_cycle(tous: list) -> tuple:
        """(agents qui participent au cycle, nombre d'agents verrouillés).

        SEUL endroit où le nombre d'agents d'un cycle est décidé : la règle
        elle-même vit dans `licence/gate.py`, qui lit `licence/config.py`.
        Les agents verrouillés ne sont ni supprimés ni détruits — ils restent
        instanciés et visibles dans l'interface, ils ne sont simplement pas
        interrogés. Si la couche licence est indisponible (arborescence
        partielle, import cassé), le cycle tourne AVEC TOUS LES AGENTS :
        dégrader vers « plus de restriction » casserait l'application d'un
        abonné, ce qui est pire que de ne pas restreindre un essai.
        """
        try:
            from licence import gate
            permis = gate.agents_autorises(tous)
        except Exception as e:
            logger.warning(f"[licence] Périmètre d'agents indéterminable ({e}) — cycle complet")
            return list(tous), 0
        return permis, max(0, len(tous) - len(permis))

    def orchestrer(self, symboles: List[str] = None) -> Dict[str, Any]:
        from . import TOUS_LES_AGENTS

        if symboles is None:
            from config import SYMBOLES_DEFAULT
            symboles = SYMBOLES_DEFAULT[:3]

        agents_actifs, nb_verrouilles = self._agents_du_cycle(TOUS_LES_AGENTS)

        # Un seul cycle mute l'état à la fois (voir _orch_lock). Un appel
        # concurrent attend la fin du cycle en cours plutôt que de corrompre
        # portfolio/historique ou de provoquer « database is locked ».
        with self._orch_lock:
            self.statut = "orchestration"
            self.derniere_analyse = datetime.now()
            self.nb_cycles += 1
            logger.info(f"[{self.NOM}] Cycle {self.nb_cycles} - Analyse de {len(symboles)} symboles "
                        f"avec {len(agents_actifs)}/{len(TOUS_LES_AGENTS)} agents")

            resultats = {}
            for symbole in symboles:
                logger.info(f"  Analyse {symbole}...")
                donnees = self._preparer_donnees(symbole)
                signaux_agents = self._collecter_signaux(symbole, donnees, agents_actifs)
                decision = self._prendre_decision(symbole, signaux_agents, donnees)
                resultats[symbole] = decision
                self.db.sauver_signal(decision["signal_final"].to_dict())

            rapport = self._generer_rapport(resultats)
            # Le rapport dit avec COMBIEN d'agents il a été produit : sans
            # cela, un cycle d'essai et un cycle Pro seraient indiscernables
            # dans l'historique et dans l'interface.
            rapport["agents_actifs"] = len(agents_actifs)
            rapport["agents_total"] = len(TOUS_LES_AGENTS)
            rapport["agents_verrouilles"] = nb_verrouilles
            self.historique_decisions.append(rapport)
            if len(self.historique_decisions) > 50:
                self.historique_decisions = self.historique_decisions[-50:]

            try:
                self.db.sauver_portfolio(self.portfolio.to_dict())
            except Exception as e:
                logger.warning(f"Portfolio non sauvegardé: {e}")

            self.statut = "idle"
            return rapport

    def _preparer_donnees(self, symbole: str) -> Dict:
        md = FetcheurDonnees.obtenir_donnees(symbole, "1h")
        if not md or not md.bougies:
            logger.warning(f"[{self.NOM}] Données indisponibles pour {symbole}")
            return {
                "prix_actuel": 0.0,
                "variation_24h": 0.0,
                "closes": [], "highs": [], "lows": [], "volumes": [], "opens": [],
                # « simule » ARMÉ : c'est le cas le PIRE (aucune donnée du
                # tout, prix de référence à 0), et il laissait pourtant
                # source_simulee à False — l'auto-trader ne voyait donc rien
                # à bloquer et pouvait envoyer un ordre RÉEL fondé sur une
                # analyse sans le moindre cours. Le marqueur doit couvrir
                # « prix inventés » ET « pas de prix ».
                "indicateurs": {"simule": True},
                "portfolio": self.portfolio.to_dict(),
                "historique_valeur": self.portfolio.historique_valeur[-100:],
                "trades": [t.to_dict() for t in self.portfolio.trades],
                "info": {},
            }
        donnees = {
            "closes": md.closes,
            "highs": md.highs,
            "lows": md.lows,
            "volumes": md.volumes,
            "opens": [b.open for b in md.bougies],
            "prix_actuel": md.prix_actuel,
            "variation_24h": md.variation_24h,
            "indicateurs": md.indicateurs,
            "portfolio": self.portfolio.to_dict(),
            "historique_valeur": self.portfolio.historique_valeur[-100:],
            "trades": [t.to_dict() for t in self.portfolio.trades],
            "derniere_maj": md.derniere_maj.isoformat(),
        }
        try:
            donnees["info"] = FetcheurDonnees.obtenir_info_ticker(symbole)
        except Exception:
            donnees["info"] = {}
        return donnees

    def _collecter_signaux(self, symbole: str, donnees: Dict, agents: list) -> List[Signal]:
        signaux = []
        with ThreadPoolExecutor(max_workers=10) as pool:
            futures = {pool.submit(agent.analyser, symbole, donnees): agent for agent in agents}
            try:
                for future in as_completed(futures, timeout=AGENT_TIMEOUT * 2):
                    agent = futures[future]
                    try:
                        signal = future.result(timeout=AGENT_TIMEOUT)
                        signaux.append(signal)
                        self.db.sauver_signal(signal.to_dict())
                    except FutureTimeout:
                        logger.warning(f"[{agent.nom}] Timeout ({AGENT_TIMEOUT}s) — ignoré")
                    except Exception as e:
                        logger.warning(f"[{agent.nom}] Erreur: {e}")
            except FutureTimeout:
                # Délai GLOBAL dépassé : as_completed lève lui-même l'exception
                # AU NIVEAU DU `for` (hors du try interne). Sans ce garde, tout
                # le cycle plantait et les signaux déjà collectés étaient perdus.
                restants = sum(1 for f in futures if not f.done())
                logger.warning(f"[{self.NOM}] Délai global dépassé — {restants} agent(s) "
                               f"non terminé(s), {len(signaux)} signaux conservés")
        return signaux

    def _prendre_decision(self, symbole: str, signaux: List[Signal], donnees: Dict) -> Dict:
        votes_achat = [s for s in signaux if s.action == ActionSignal.ACHAT]
        votes_vente = [s for s in signaux if s.action == ActionSignal.VENTE]
        votes_hold = [s for s in signaux if s.action in [ActionSignal.HOLD, ActionSignal.SURVEILLER]]
        votes_alerte = [s for s in signaux if s.action == ActionSignal.ALERTE]

        score_achat = sum(s.confiance for s in votes_achat) / len(signaux) if signaux else 0
        score_vente = sum(s.confiance for s in votes_vente) / len(signaux) if signaux else 0
        confiance_moy_achat = sum(s.confiance for s in votes_achat) / len(votes_achat) if votes_achat else 0
        confiance_moy_vente = sum(s.confiance for s in votes_vente) / len(votes_vente) if votes_vente else 0

        alertes_critiques = [s for s in votes_alerte if s.confiance > 85]

        prix = donnees.get("prix_actuel", 0)

        if alertes_critiques:
            alerte_principale = max(alertes_critiques, key=lambda s: s.confiance)
            signal_final = Signal(
                agent_id=self.ID, agent_nom=self.NOM, symbole=symbole,
                action=ActionSignal.ALERTE, confiance=alerte_principale.confiance,
                raisonnement=f"ALERTE CRITIQUE: {alerte_principale.raisonnement}",
                donnees={"votes": {"achat": len(votes_achat), "vente": len(votes_vente), "alerte": len(votes_alerte)}}
            )
        elif score_achat > score_vente and len(votes_achat) >= 3:
            sl_avg = self._moyenne_valeurs([s.stop_loss for s in votes_achat if s.stop_loss])
            tp_avg = self._moyenne_valeurs([s.take_profit for s in votes_achat if s.take_profit])
            taille_avg = self._moyenne_valeurs([s.taille_position_pct for s in votes_achat if s.taille_position_pct > 0])
            agents_accord = [s.agent_nom for s in votes_achat][:5]
            raison = self._synthese_ia(symbole, signaux, "ACHAT", prix)
            signal_final = Signal(
                agent_id=self.ID, agent_nom=self.NOM, symbole=symbole,
                action=ActionSignal.ACHAT, confiance=min(90, confiance_moy_achat * 0.9),
                prix_entree=prix, stop_loss=sl_avg, take_profit=tp_avg,
                taille_position_pct=taille_avg or 0.05,
                raisonnement=raison,
                donnees={"votes": {"achat": len(votes_achat), "vente": len(votes_vente)},
                         "agents_accord": agents_accord, "score_achat": score_achat}
            )
        elif score_vente > score_achat and len(votes_vente) >= 3:
            sl_avg = self._moyenne_valeurs([s.stop_loss for s in votes_vente if s.stop_loss])
            tp_avg = self._moyenne_valeurs([s.take_profit for s in votes_vente if s.take_profit])
            taille_avg = self._moyenne_valeurs([s.taille_position_pct for s in votes_vente if s.taille_position_pct > 0])
            agents_accord = [s.agent_nom for s in votes_vente][:5]
            raison = self._synthese_ia(symbole, signaux, "VENTE", prix)
            signal_final = Signal(
                agent_id=self.ID, agent_nom=self.NOM, symbole=symbole,
                action=ActionSignal.VENTE, confiance=min(90, confiance_moy_vente * 0.9),
                prix_entree=prix, stop_loss=sl_avg, take_profit=tp_avg,
                taille_position_pct=taille_avg or 0.05,
                raisonnement=raison,
                donnees={"votes": {"achat": len(votes_achat), "vente": len(votes_vente)},
                         "agents_accord": agents_accord, "score_vente": score_vente}
            )
        else:
            signal_final = Signal(
                agent_id=self.ID, agent_nom=self.NOM, symbole=symbole,
                action=ActionSignal.HOLD, confiance=50,
                raisonnement=f"Pas de consensus ({len(votes_achat)} BUY, {len(votes_vente)} SELL, {len(votes_hold)} HOLD)",
                donnees={"votes": {"achat": len(votes_achat), "vente": len(votes_vente), "hold": len(votes_hold)}}
            )

        # L'assistant de l'orchestrateur vérifie la décision finale. Il doit
        # d'abord PRÉPARER le contexte : sans cela son `derniere_qualite`
        # restait None et le plafonnement de confiance sur données pauvres
        # (40 % / 60 %) ne s'appliquait JAMAIS à la décision finale — celle
        # qui dimensionne le volume réellement envoyé (confiance / 200).
        self.assistant.preparer_contexte(symbole, donnees, {})
        signal_final = self.assistant.verifier_signal(signal_final, donnees)

        # Traçabilité : marque si la décision repose sur des données SIMULÉES
        # (yfinance indisponible → prix inventés) ou ABSENTES (prix de
        # référence nul). L'auto-trader refuse alors tout ordre RÉEL fondé sur
        # ces prix — un prix à 0 n'est pas plus exploitable qu'un prix inventé.
        signal_final.donnees = dict(signal_final.donnees or {})
        signal_final.donnees["source_simulee"] = bool(
            (donnees.get("indicateurs") or {}).get("simule")
            or not _prix_exploitable(donnees.get("prix_actuel"))
        )

        # Levier choisi par les agents pour CETTE décision (dans la limite
        # réglée par l'utilisateur). Transporté avec la décision : c'est lui
        # qui dimensionnera l'ordre au moment de l'envoi au courtier.
        signal_final.donnees["levier"] = self._decider_levier(signal_final, donnees)

        return {
            "symbole": symbole,
            "signal_final": signal_final,
            "signaux_agents": [s.to_dict() for s in signaux],
            "stats": {
                "total_agents": len(signaux),
                "votes_achat": len(votes_achat),
                "votes_vente": len(votes_vente),
                "votes_hold": len(votes_hold),
                "votes_alerte": len(votes_alerte),
                "score_achat": round(score_achat, 2),
                "score_vente": round(score_vente, 2),
            },
            "timestamp": datetime.now().isoformat(),
        }

    def _synthese_ia(self, symbole: str, signaux: List[Signal],
                     direction: str, prix: float) -> str:
        """Synthèse de la décision par le moteur IA LOCAL sélectionné.

        Il n'existe plus de chemin distant : cette méthode routait auparavant
        vers l'API Anthropic quand une clé était présente. L'application
        fonctionne désormais entièrement hors ligne, et une IA injoignable
        retombe sur le consensus textuel des agents — jamais sur un appel
        réseau.
        """
        if self._moteur_local_disponible():
            return self._synthese_locale(symbole, signaux, direction, prix)
        logger.warning("[IA locale] Moteur non disponible — "
                       "synthèse textuelle de secours")
        return self._consensus_textuel(signaux, direction, prix)

    @staticmethod
    def _consensus_textuel(signaux: List[Signal], direction: str, prix: float) -> str:
        """Repli sans IA : la liste des agents qui portent la décision."""
        agents = [s.agent_nom for s in signaux if
                  (direction == "ACHAT" and s.action == ActionSignal.ACHAT) or
                  (direction == "VENTE" and s.action == ActionSignal.VENTE)]
        return f"Consensus {direction}: {', '.join(agents[:5])} @ {prix:.4f}"

    def _generer_rapport(self, resultats: Dict) -> Dict:
        from . import TOUS_LES_AGENTS
        achats = [sym for sym, r in resultats.items() if r["signal_final"].action == ActionSignal.ACHAT]
        ventes = [sym for sym, r in resultats.items() if r["signal_final"].action == ActionSignal.VENTE]
        alertes = [sym for sym, r in resultats.items() if r["signal_final"].action == ActionSignal.ALERTE]

        return {
            "cycle": self.nb_cycles,
            "timestamp": datetime.now().isoformat(),
            "orchestrateur": {"id": self.ID, "nom": self.NOM, "statut": self.statut},
            "portfolio": self.portfolio.to_dict(),
            "symboles_analyses": list(resultats.keys()),
            "decisions": {sym: r["signal_final"].to_dict() for sym, r in resultats.items()},
            "stats_decisions": {"achats": achats, "ventes": ventes, "alertes": alertes},
            "details": {sym: r["stats"] for sym, r in resultats.items()},
            "nb_agents_actifs": len(TOUS_LES_AGENTS),
            "signaux_par_symbole": {sym: len(r["signaux_agents"]) for sym, r in resultats.items()},
        }

    def to_dict(self) -> Dict:
        from . import TOUS_LES_AGENTS
        return {
            "id": self.ID,
            "nom": self.NOM,
            "groupe": self.GROUPE,
            "description": self.DESCRIPTION,
            "statut": self.statut,
            "nb_cycles": self.nb_cycles,
            "derniere_analyse": self.derniere_analyse.isoformat() if self.derniere_analyse else None,
            "nb_agents": len(TOUS_LES_AGENTS),
            # Agents réellement mobilisés dans l'offre en cours : l'interface
            # affiche « 6 / 45 » en essai et « 45 / 45 » en Pro.
            "nb_agents_actifs": len(self._agents_du_cycle(TOUS_LES_AGENTS)[0]),
            "portfolio": self.portfolio.to_dict(),
            "assistant": self.assistant.to_dict(),
        }

    # ── Levier piloté par les agents ─────────────────────────────────
    @staticmethod
    def _volatilite_recente(closes: list) -> Optional[float]:
        """Écart-type des rendements récents, en % (None si indisponible)."""
        if not closes or len(closes) < 6:
            return None
        serie = closes[-21:]
        rendements = [serie[i] / serie[i - 1] - 1
                      for i in range(1, len(serie))
                      if serie[i - 1]]
        if len(rendements) < 3:
            return None
        moyenne = sum(rendements) / len(rendements)
        variance = sum((r - moyenne) ** 2 for r in rendements) / len(rendements)
        return (variance ** 0.5) * 100

    def _decider_levier(self, signal_final, donnees: Dict) -> float:
        """Levier retenu pour la décision : conviction des agents, tempérée par
        la volatilité récente, borné par le plafond réglé dans l'interface
        (Réglages ⚙️ → Moteur de performance, 1 à 10).

        Sur une décision sans ordre (HOLD, ALERTE), on renvoie simplement le
        levier courant sans le modifier.
        """
        from utils import trading_config
        try:
            if signal_final.action not in (ActionSignal.ACHAT, ActionSignal.VENTE):
                return trading_config.levier_actuel()
            vol = self._volatilite_recente(donnees.get("closes") or [])
            propose = trading_config.levier_suggere(signal_final.confiance, vol)
            return trading_config.ajuster_levier(
                propose, source=f"agents · {signal_final.symbole}")
        except Exception as e:
            logger.warning(f"[{self.NOM}] Levier non ajusté : {e}")
            try:
                return trading_config.levier_actuel()
            except Exception:
                return 1.0

    @staticmethod
    def _moyenne_valeurs(valeurs: list) -> Optional[float]:
        vals = [v for v in valeurs if v is not None and v > 0]
        return sum(vals) / len(vals) if vals else None
