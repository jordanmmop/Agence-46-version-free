import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from agents.base_agent import BaseAgent
from models.signal import Signal, ActionSignal
from utils.indicators import Indicateurs
from config import COMMISSION_PCT
from typing import Dict
import numpy as np


class AgentBacktesting(BaseAgent):
    @property
    def id(self): return "BT-038"
    @property
    def nom(self): return "Agent Backtesting"
    @property
    def groupe(self): return "data_intelligence"
    @property
    def description(self): return "Backtesting : tests historiques des stratégies, métriques de performance"
    @property
    def _system_prompt(self):
        return """Tu es l'expert en backtesting de stratégies de trading.
Tu testes les stratégies sur données historiques et calcules les métriques clés :
Sharpe, Sortino, max drawdown, win rate, profit factor.
Un backtest réaliste inclut frais, slippage et risque de survie. Tu communiques en français."""

    def _analyser(self, symbole: str, donnees: Dict, contexte: Dict) -> Signal:
        closes = donnees.get("closes", [])
        highs = donnees.get("highs", closes)
        lows = donnees.get("lows", closes)
        if len(closes) < 50:
            return self._signal_neutre(symbole, "Données insuffisantes pour backtest")

        prix = closes[-1]
        resultats = self._backtest_sma_crossover(closes, highs, lows)
        resultats_mr = self._backtest_mean_reversion(closes)
        meilleur = resultats if resultats["sharpe"] >= resultats_mr["sharpe"] else resultats_mr

        signaux = [
            f"Stratégie testée: {meilleur['strategie']}",
            f"Trades: {meilleur['nb_trades']} | Win rate: {meilleur['win_rate']:.1f}%",
            f"Sharpe: {meilleur['sharpe']:.2f} | Sortino: {meilleur['sortino']:.2f}",
            f"Max DD: {meilleur['max_dd']:.1f}% | Profit Factor: {meilleur['profit_factor']:.2f}",
            f"Rendement total: {meilleur['rendement_total']:+.1f}%",
        ]

        if meilleur["sharpe"] > 0.30 and meilleur["win_rate"] > 50:
            action = ActionSignal.ACHAT
            confiance = min(78, 50 + meilleur["sharpe"] * 50)
            sl = prix * 0.95
            tp = prix * (1 + meilleur["rendement_total"] / meilleur["nb_trades"] / 100)
            signaux.append("Stratégie validée → signal fort")
        elif meilleur["sharpe"] > 0.10:
            action = ActionSignal.SURVEILLER
            confiance = 50
            sl = None
            tp = None
            signaux.append("Stratégie correcte mais amélioration possible")
        else:
            return self._signal_neutre(symbole, f"Backtest décevant (Sharpe: {meilleur['sharpe']:.2f})")

        return self._creer_signal(
            symbole=symbole, action=action, confiance=confiance,
            raisonnement=" | ".join(signaux), prix=prix, sl=sl, tp=tp,
            donnees=meilleur
        )

    def _backtest_sma_crossover(self, closes, highs, lows):
        ema20 = Indicateurs.ema(closes, 20)
        ema50 = Indicateurs.ema(closes, 50)
        trades, position, entree = [], None, 0
        for i in range(50, len(closes)):
            e20 = ema20[i]
            e50 = ema50[i]
            if e20 is None or e50 is None:
                continue
            if e20 > e50 and position != "LONG":
                if position == "SHORT":
                    pnl = (entree - closes[i]) / entree - COMMISSION_PCT * 2
                    trades.append(pnl)
                position, entree = "LONG", closes[i]
            elif e20 < e50 and position != "SHORT":
                if position == "LONG":
                    pnl = (closes[i] - entree) / entree - COMMISSION_PCT * 2
                    trades.append(pnl)
                position, entree = "SHORT", closes[i]
        return self._calc_metriques(trades, "SMA Crossover (20/50)")

    def _backtest_mean_reversion(self, closes):
        z_scores = Indicateurs.z_score(closes, 20)
        trades, position, entree = [], None, 0
        for i in range(20, len(closes)):
            z = z_scores[i]
            if z is None:
                continue
            if z < -2 and position != "LONG":
                if position == "SHORT":
                    trades.append((entree - closes[i]) / entree - COMMISSION_PCT * 2)
                position, entree = "LONG", closes[i]
            elif z > 2 and position != "SHORT":
                if position == "LONG":
                    trades.append((closes[i] - entree) / entree - COMMISSION_PCT * 2)
                position, entree = "SHORT", closes[i]
            elif abs(z) < 0.5 and position:
                pnl = (closes[i] - entree) / entree * (1 if position == "LONG" else -1) - COMMISSION_PCT * 2
                trades.append(pnl)
                position = None
        return self._calc_metriques(trades, "Mean Reversion (Z-score)")

    def _calc_metriques(self, trades, nom):
        if not trades:
            return {"strategie": nom, "nb_trades": 0, "win_rate": 0, "sharpe": 0, "sortino": 0,
                    "max_dd": 0, "profit_factor": 0, "rendement_total": 0}
        wins = [t for t in trades if t > 0]
        losses = [t for t in trades if t <= 0]
        win_rate = len(wins) / len(trades) * 100 if trades else 0
        profit_factor = sum(wins) / abs(sum(losses)) if losses and sum(losses) != 0 else float("inf")
        rendement_total = sum(trades) * 100
        # Sharpe PAR TRADE : sharpe_ratio() annualise sur 252 JOURS
        # (mean*252 / (std*sqrt(252)) = sqrt(252)*mean/std). Appliqué à des
        # P&L par trade, il multipliait le ratio par ~15,9 : le seuil « > 1,5 »
        # correspondait en réalité à 0,09 par trade, franchissable par du bruit.
        moy = float(np.mean(trades)) if trades else 0.0
        ecart = float(np.std(trades)) if trades else 0.0
        sharpe = moy / ecart if ecart > 0 else 0.0
        pertes = [t for t in trades if t < 0]
        ecart_bas = float(np.std(pertes)) if pertes else 0.0
        sortino = moy / ecart_bas if ecart_bas > 0 else 0.0
        valeurs = [1.0]
        for r in trades:
            valeurs.append(valeurs[-1] * (1 + r))
        max_dd = Indicateurs.max_drawdown(valeurs)
        return {"strategie": nom, "nb_trades": len(trades), "win_rate": round(win_rate, 1),
                "sharpe": round(sharpe, 2), "sortino": round(sortino, 2),
                "max_dd": round(max_dd, 2), "profit_factor": round(profit_factor, 2),
                "rendement_total": round(rendement_total, 2)}
