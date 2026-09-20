import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from agents.base_agent import BaseAgent
from models.signal import Signal, ActionSignal
from utils.indicators import Indicateurs
from typing import Dict


class AgentSwingTrading(BaseAgent):
    @property
    def id(self): return "SW-013"
    @property
    def nom(self): return "Trader Swing"
    @property
    def groupe(self): return "strategies"
    @property
    def description(self): return "Swing trading : positions 2-10 jours sur tendances court/moyen terme"
    @property
    def _system_prompt(self):
        return """Tu es un swing trader expert avec 10 ans d'expérience.
Tu trades des mouvements de 2 à 10 jours basés sur des patterns techniques et niveaux clés.
Tu utilises les retracements Fibonacci, les patterns candlestick et les zones support/résistance.
Un bon setup swing offre un ratio risque/rendement minimum de 1:2. Tu communiques en français."""

    def _analyser(self, symbole: str, donnees: Dict, contexte: Dict) -> Signal:
        closes = donnees.get("closes", [])
        highs = donnees.get("highs", closes)
        lows = donnees.get("lows", closes)
        if len(closes) < 40:
            return self._signal_neutre(symbole, "Données insuffisantes pour swing")

        prix = closes[-1]
        ema9 = Indicateurs.ema(closes, 9)
        ema21 = Indicateurs.ema(closes, 21)
        ema50 = Indicateurs.ema(closes, 50)
        rsi = Indicateurs.rsi(closes, 14)
        bb_upper, bb_mid, bb_lower = Indicateurs.bollinger(closes, 20, 2)
        atr = Indicateurs.atr(highs, lows, closes, 14)
        macd_line, signal_line, histo = Indicateurs.macd(closes)

        e9 = next((v for v in reversed(ema9) if v is not None), prix)
        e21 = next((v for v in reversed(ema21) if v is not None), prix)
        e50 = next((v for v in reversed(ema50) if v is not None), prix)
        rsi_val = next((v for v in reversed(rsi) if v is not None), 50.0)
        bb_low = next((v for v in reversed(bb_lower) if v is not None), prix)
        bb_high = next((v for v in reversed(bb_upper) if v is not None), prix)
        atr_val = next((v for v in reversed(atr) if v is not None), prix * 0.02)
        hist = next((v for v in reversed(histo) if v is not None), 0)

        haut_recent = max(highs[-20:])
        bas_recent = min(lows[-20:])
        fib = Indicateurs.fibonacci_retracement(haut_recent, bas_recent)

        fib_382 = fib["0.382"]
        fib_618 = fib["0.618"]
        croisement_haussier = e9 > e21 and (e9 - e21) / e21 < 0.02
        croisement_baissier = e9 < e21 and (e21 - e9) / e21 < 0.02

        signaux = [f"RSI: {rsi_val:.1f}", f"EMA9/21: {e9:.2f}/{e21:.2f}",
                   f"Fib 38.2%: {fib_382:.4f}", f"Fib 61.8%: {fib_618:.4f}"]

        if croisement_haussier and rsi_val > 45 and rsi_val < 65 and hist > 0:
            action = ActionSignal.ACHAT
            confiance = 72
            sl = max(bas_recent, fib_618) * 0.99
            tp = haut_recent
            signaux.append("Croisement EMA haussier + MACD positif → swing long")
        elif prix <= fib_382 * 1.005 and e50 < prix and rsi_val < 45:
            action = ActionSignal.ACHAT
            confiance = 68
            sl = bas_recent * 0.995
            tp = haut_recent * 0.98
            signaux.append("Rebond sur Fib 38.2% en tendance haussière")
        elif croisement_baissier and rsi_val < 55 and rsi_val > 35 and hist < 0:
            action = ActionSignal.VENTE
            confiance = 70
            sl = min(haut_recent, e21 * 1.01)
            tp = bas_recent
            signaux.append("Croisement EMA baissier + MACD négatif → swing short")
        elif prix >= fib_382 * 0.995 and prix > bas_recent and rsi_val > 60:
            action = ActionSignal.VENTE
            confiance = 60
            sl = haut_recent * 1.005
            tp = fib_618
            signaux.append("Résistance Fib 38.2% en tendance baissière")
        else:
            return self._signal_neutre(symbole, "Pas de setup swing valide")

        rr = abs(tp - prix) / abs(sl - prix) if sl and abs(sl - prix) > 0 else 0
        signaux.append(f"RR: {rr:.2f}:1")
        if rr < 1.5:
            return self._signal_neutre(symbole, f"RR insuffisant ({rr:.2f}:1 < 1.5:1)")

        return self._creer_signal(
            symbole=symbole, action=action, confiance=confiance,
            raisonnement=" | ".join(signaux), prix=prix, sl=sl, tp=tp, taille=0.05,
            donnees={"rsi": rsi_val, "atr": atr_val, "fib_382": fib_382, "rr": rr}
        )
