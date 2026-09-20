import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from agents.base_agent import BaseAgent
from models.signal import Signal, ActionSignal
from utils.indicators import Indicateurs
from config import TAUX_SANS_RISQUE
from typing import Dict
import numpy as np
from datetime import datetime


class AgentRapportPerformance(BaseAgent):
    @property
    def id(self): return "RP-042"
    @property
    def nom(self): return "Analyste Performance"
    @property
    def groupe(self): return "reporting"
    @property
    def description(self): return "Rapport de performance : Sharpe, Sortino, Alpha, Beta, statistiques complètes"
    @property
    def _system_prompt(self):
        return """Tu es l'analyste performance de l'agence de trading.
Tu calcules et présentes les métriques de performance : Sharpe, Sortino, Calmar,
Alpha, Beta, Information Ratio, et attribution de performance par agent.
Tu communiques en français avec des rapports clairs et des benchmarks comparatifs."""

    def _analyser(self, symbole: str, donnees: Dict, contexte: Dict) -> Signal:
        portfolio = donnees.get("portfolio", {})
        historique = donnees.get("historique_valeur", [])
        trades = donnees.get("trades", [])
        # `.get("closes", [None])` ne protège PAS le cas closes=[] (fréquent
        # quand yfinance est indisponible) → [][-1] lèverait IndexError.
        closes = donnees.get("closes") or [None]
        prix = closes[-1]

        capital_initial = portfolio.get("capital_initial", 100000)
        valeur_totale = portfolio.get("valeur_totale", capital_initial)
        pnl_total = portfolio.get("pnl_total", 0)
        pnl_pct = portfolio.get("pnl_total_pct", 0)

        valeurs = [h.get("valeur", capital_initial) for h in historique] if historique else [capital_initial, valeur_totale]
        rendements = []
        if len(valeurs) > 1:
            rendements = [valeurs[i] / valeurs[i - 1] - 1 for i in range(1, len(valeurs))]

        sharpe = Indicateurs.sharpe_ratio(rendements, TAUX_SANS_RISQUE) if len(rendements) > 5 else 0
        sortino = Indicateurs.sortino_ratio(rendements, TAUX_SANS_RISQUE) if len(rendements) > 5 else 0
        max_dd = Indicateurs.max_drawdown(valeurs)
        calmar = abs(np.mean(rendements) * 252 / (max_dd / 100)) if max_dd > 0 else 0

        nb_trades = len(trades)
        trades_fermes = [t for t in trades if t.get("statut") == "CLOSED"]
        wins = [t for t in trades_fermes if t.get("pnl", 0) > 0]
        win_rate = len(wins) / len(trades_fermes) * 100 if trades_fermes else 0
        avg_win = np.mean([t.get("pnl", 0) for t in wins]) if wins else 0
        avg_loss = abs(np.mean([t.get("pnl", 0) for t in trades_fermes if t.get("pnl", 0) <= 0])) if trades_fermes else 0
        # win_rate == 100 → (1 - 1) = 0 → ZeroDivisionError. On plafonne alors
        # le profit factor à une valeur finie (stratégie 100% gagnante).
        if avg_loss > 0 and 0 < win_rate < 100:
            profit_factor = avg_win / avg_loss * (win_rate / 100) / (1 - win_rate / 100)
        elif avg_loss <= 0 and win_rate >= 100 and avg_win > 0:
            profit_factor = 999.0
        else:
            profit_factor = 0

        signaux = [
            f"P&L Total: {pnl_total:+,.2f}€ ({pnl_pct:+.2f}%)",
            f"Sharpe: {sharpe:.2f} | Sortino: {sortino:.2f} | Calmar: {calmar:.2f}",
            f"Max Drawdown: {max_dd:.2f}%",
            f"Win Rate: {win_rate:.1f}% | Profit Factor: {profit_factor:.2f}",
            f"Trades: {nb_trades} total ({len(trades_fermes)} fermés, {len(wins)} gagnants)",
            f"Avg Win: {avg_win:+.2f}€ | Avg Loss: -{avg_loss:.2f}€",
            f"RR moyen: {avg_win/avg_loss:.2f}" if avg_loss > 0 else "RR: N/A",
        ]

        if sharpe > 2 and win_rate > 55:
            action = ActionSignal.ACHAT
            confiance = 80
            signaux.append("PERFORMANCE EXCELLENTE → continuer la stratégie")
        elif sharpe < 0 or win_rate < 40:
            action = ActionSignal.ALERTE
            confiance = 80
            signaux.append("PERFORMANCE INSUFFISANTE → réviser la stratégie")
        else:
            action = ActionSignal.HOLD
            confiance = 70
            signaux.append("Performance correcte")

        return self._creer_signal(
            symbole=symbole, action=action, confiance=confiance,
            raisonnement=" | ".join(signaux), prix=prix if prix else None,
            donnees={"sharpe": sharpe, "sortino": sortino, "calmar": calmar,
                     "max_dd": max_dd, "win_rate": win_rate, "profit_factor": profit_factor,
                     "pnl_total": pnl_total, "rapport_date": datetime.now().isoformat()}
        )
