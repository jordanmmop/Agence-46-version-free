import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from agents.base_agent import BaseAgent
from models.signal import Signal, ActionSignal
from utils.indicators import Indicateurs
from typing import Dict


class AgentDayTrading(BaseAgent):
    @property
    def id(self): return "DT-012"
    @property
    def nom(self): return "Trader Intraday"
    @property
    def groupe(self): return "strategies"
    @property
    def description(self): return "Day trading : stratégies intraday, ouverture/clôture dans la journée"
    @property
    def _system_prompt(self):
        return """Tu es un day trader professionnel. Tu ouvres et fermes des positions dans la même journée.
Tu utilises les gaps d'ouverture, les niveaux de support/résistance intraday, et les patterns de reversal.
Tu te concentres sur les premières et dernières heures de trading, les plus volatiles.
Tu communiques en français."""

    def _analyser(self, symbole: str, donnees: Dict, contexte: Dict) -> Signal:
        closes = donnees.get("closes", [])
        highs = donnees.get("highs", closes)
        lows = donnees.get("lows", closes)
        opens = donnees.get("opens", closes)
        volumes = donnees.get("volumes", [1e6] * len(closes))
        if len(closes) < 15:
            return self._signal_neutre(symbole, "Données insuffisantes")

        prix = closes[-1]
        ouverture = opens[-1] if opens else prix
        gap = (ouverture - closes[-2]) / closes[-2] * 100 if len(closes) >= 2 else 0

        atr = Indicateurs.atr(highs, lows, closes, 5)
        atr_val = next((v for v in reversed(atr) if v is not None), prix * 0.01)
        ema20 = Indicateurs.ema(closes, 20)
        ema20_val = next((v for v in reversed(ema20) if v is not None), prix)
        vwap = Indicateurs.vwap(highs, lows, closes, volumes)
        vwap_val = vwap[-1] if vwap else prix
        rsi = Indicateurs.rsi(closes, 9)
        rsi_val = next((v for v in reversed(rsi) if v is not None), 50.0)

        supports, resistances = Indicateurs.support_resistance(highs, lows, 3)
        support_proche = max([s for s in supports if s < prix], default=prix * 0.97)
        resistance_proche = min([r for r in resistances if r > prix], default=prix * 1.03)

        signaux = [f"Gap ouverture: {gap:+.2f}%", f"VWAP: {vwap_val:.4f}", f"RSI(9): {rsi_val:.1f}"]

        if gap > 0.5 and prix > vwap_val and rsi_val > 50:
            action = ActionSignal.ACHAT
            confiance = min(78, 50 + gap * 10 + (rsi_val - 50) * 0.3)
            sl = max(vwap_val, support_proche) * 0.999
            tp = resistance_proche * 0.998
            signaux.append(f"Gap haussier + momentum → long jusqu'à R: {resistance_proche:.4f}")
        elif gap < -0.5 and prix < vwap_val and rsi_val < 50:
            action = ActionSignal.VENTE
            confiance = min(78, 50 + abs(gap) * 10 + (50 - rsi_val) * 0.3)
            sl = min(vwap_val, resistance_proche) * 1.001
            tp = support_proche * 1.001
            signaux.append(f"Gap baissier + momentum → short jusqu'à S: {support_proche:.4f}")
        elif abs(gap) < 0.2 and prix > vwap_val and rsi_val > 55:
            action = ActionSignal.ACHAT
            confiance = 55
            sl = vwap_val * 0.998
            tp = prix * 1.01
            signaux.append("Ouverture calme + prix > VWAP → suivi tendance")
        else:
            return self._signal_neutre(symbole, f"Pas de setup day trading (gap: {gap:.2f}%)")

        return self._creer_signal(
            symbole=symbole, action=action, confiance=confiance,
            raisonnement=" | ".join(signaux), prix=prix, sl=sl, tp=tp, taille=0.05,
            donnees={"gap": gap, "vwap": vwap_val, "support": support_proche, "resistance": resistance_proche}
        )
