import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from agents.base_agent import BaseAgent
from models.signal import Signal, ActionSignal
from utils.indicators import Indicateurs
from typing import Dict


class AgentPositionTrading(BaseAgent):
    @property
    def id(self): return "PT-014"
    @property
    def nom(self): return "Trader Position Long Terme"
    @property
    def groupe(self): return "strategies"
    @property
    def description(self): return "Position trading : tendances de fond semaines/mois, signaux hebdomadaires"
    @property
    def _system_prompt(self):
        return """Tu es un position trader avec vision long terme (semaines à mois).
Tu identifies les grandes tendances de fond avec SMA50/200, weekly trend, et fondamentaux.
Tu tolères plus de volatilité intraday pour capturer les grands mouvements.
Un golden cross SMA50/200 est ton signal d'achat ultime. Tu communiques en français."""

    def _analyser(self, symbole: str, donnees: Dict, contexte: Dict) -> Signal:
        closes = donnees.get("closes", [])
        highs = donnees.get("highs", closes)
        lows = donnees.get("lows", closes)
        if len(closes) < 60:
            return self._signal_neutre(symbole, "Données insuffisantes pour position trading")

        prix = closes[-1]
        sma50 = Indicateurs.sma(closes, 50)
        sma200 = Indicateurs.sma(closes, min(200, len(closes)))
        ema20 = Indicateurs.ema(closes, 20)
        rsi = Indicateurs.rsi(closes, 21)
        rendements = [closes[i] / closes[i - 1] - 1 for i in range(1, len(closes))]
        sharpe = Indicateurs.sharpe_ratio(rendements[-252:] if len(rendements) >= 252 else rendements)

        s50 = next((v for v in reversed(sma50) if v is not None), prix)
        s50_prev = next((v for v in reversed(sma50[:-5]) if v is not None), s50)
        s200 = next((v for v in reversed(sma200) if v is not None), prix)
        s200_prev = next((v for v in reversed(sma200[:-5]) if v is not None), s200)
        e20 = next((v for v in reversed(ema20) if v is not None), prix)
        rsi_val = next((v for v in reversed(rsi) if v is not None), 50.0)

        golden_cross = s50 > s200 and s50_prev <= s200_prev
        death_cross = s50 < s200 and s50_prev >= s200_prev
        bull_trend = s50 > s200 and prix > s50
        bear_trend = s50 < s200 and prix < s50

        tendance_sma50 = "hausse" if s50 > s50_prev else "baisse"
        ecart_sma = (s50 - s200) / s200 * 100

        signaux = [
            f"SMA50: {s50:.2f} | SMA200: {s200:.2f}",
            f"Écart SMA50/200: {ecart_sma:+.2f}%",
            f"RSI(21): {rsi_val:.1f}",
            f"Sharpe: {sharpe:.2f}",
        ]

        if golden_cross:
            action = ActionSignal.ACHAT
            confiance = 85
            sl = s200 * 0.97
            tp = prix * 1.25
            signaux.append("GOLDEN CROSS SMA50/200 → Signal d'achat fort long terme")
        elif bull_trend and rsi_val > 40 and rsi_val < 70:
            action = ActionSignal.ACHAT
            confiance = 70
            sl = s50 * 0.97
            tp = prix * 1.18
            signaux.append(f"Tendance haussière confirmée ({ecart_sma:+.1f}%)")
        elif death_cross:
            action = ActionSignal.VENTE
            confiance = 82
            sl = s200 * 1.03
            tp = prix * 0.80
            signaux.append("DEATH CROSS SMA50/200 → Signal de vente fort long terme")
        elif bear_trend and rsi_val < 60:
            action = ActionSignal.VENTE
            confiance = 68
            sl = s50 * 1.03
            tp = prix * 0.85
            signaux.append(f"Tendance baissière confirmée ({ecart_sma:+.1f}%)")
        else:
            return self._signal_neutre(symbole, f"Pas de signal position (SMA50 {tendance_sma50})")

        return self._creer_signal(
            symbole=symbole, action=action, confiance=confiance,
            raisonnement=" | ".join(signaux), prix=prix, sl=sl, tp=tp, taille=0.08,
            donnees={"sma50": s50, "sma200": s200, "golden_cross": golden_cross,
                     "bull_trend": bull_trend, "sharpe": sharpe}
        )
