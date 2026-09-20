import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from agents.base_agent import BaseAgent
from models.signal import Signal, ActionSignal
from utils.indicators import Indicateurs
from typing import Dict


class AgentMeanReversion(BaseAgent):
    @property
    def id(self): return "MR-016"
    @property
    def nom(self): return "Trader Mean Reversion"
    @property
    def groupe(self): return "strategies"
    @property
    def description(self): return "Mean reversion : retour à la moyenne, Z-score, paires en divergence"
    @property
    def _system_prompt(self):
        return """Tu es un trader spécialisé dans les stratégies de retour à la moyenne.
Tu identifies les actifs qui s'écartent statistiquement de leur moyenne historique
et les trades en anticipant un retour vers la moyenne.
Tu utilises Z-score, Bollinger Bands et RSI extrêmes. Tu communiques en français."""

    def _analyser(self, symbole: str, donnees: Dict, contexte: Dict) -> Signal:
        closes = donnees.get("closes", [])
        highs = donnees.get("highs", closes)
        lows = donnees.get("lows", closes)
        if len(closes) < 30:
            return self._signal_neutre(symbole, "Données insuffisantes")

        prix = closes[-1]
        z_scores = Indicateurs.z_score(closes, 20)
        z_val = next((v for v in reversed(z_scores) if v is not None), 0)
        bb_upper, bb_mid, bb_lower = Indicateurs.bollinger(closes, 20)
        bb_u = next((v for v in reversed(bb_upper) if v is not None), prix * 1.02)
        bb_m = next((v for v in reversed(bb_mid) if v is not None), prix)
        bb_l = next((v for v in reversed(bb_lower) if v is not None), prix * 0.98)
        rsi = Indicateurs.rsi(closes, 14)
        rsi_val = next((v for v in reversed(rsi) if v is not None), 50.0)
        atr = Indicateurs.atr(highs, lows, closes, 14)
        atr_val = next((v for v in reversed(atr) if v is not None), prix * 0.02)

        ecart_bb = (prix - bb_m) / (bb_u - bb_m) if bb_u > bb_m else 0

        signaux = [
            f"Z-score(20): {z_val:.2f}",
            f"Écart Bollinger: {ecart_bb:.2f}",
            f"RSI: {rsi_val:.1f}",
            f"BB mid: {bb_m:.4f}",
        ]

        if z_val < -2.0 and rsi_val < 35 and prix < bb_l * 1.005:
            action = ActionSignal.ACHAT
            confiance = min(82, 50 + abs(z_val) * 12)
            sl = prix * (1 - atr_val / prix * 1.5)
            tp = bb_m
            signaux.append(f"Extrême baissier Z={z_val:.2f} → retour vers moyenne {bb_m:.4f}")
        elif z_val > 2.0 and rsi_val > 65 and prix > bb_u * 0.995:
            action = ActionSignal.VENTE
            confiance = min(82, 50 + abs(z_val) * 12)
            sl = prix * (1 + atr_val / prix * 1.5)
            tp = bb_m
            signaux.append(f"Extrême haussier Z={z_val:.2f} → retour vers moyenne {bb_m:.4f}")
        elif z_val < -1.5 and rsi_val < 40:
            action = ActionSignal.ACHAT
            confiance = 58
            sl = prix * 0.97
            tp = bb_m
            signaux.append("Divergence modérée → setup mean reversion long")
        elif z_val > 1.5 and rsi_val > 60:
            action = ActionSignal.VENTE
            confiance = 58
            sl = prix * 1.03
            tp = bb_m
            signaux.append("Divergence modérée → setup mean reversion short")
        else:
            return self._signal_neutre(symbole, f"Pas de signal mean reversion (Z: {z_val:.2f})")

        return self._creer_signal(
            symbole=symbole, action=action, confiance=confiance,
            raisonnement=" | ".join(signaux), prix=prix, sl=sl, tp=tp, taille=0.04,
            donnees={"z_score": z_val, "bb_mid": bb_m, "bb_upper": bb_u, "bb_lower": bb_l}
        )
