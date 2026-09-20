import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from agents.base_agent import BaseAgent
from models.signal import Signal, ActionSignal
from utils.indicators import Indicateurs
from typing import Dict
import numpy as np


class AgentSentimentMarche(BaseAgent):
    @property
    def id(self): return "SM-003"
    @property
    def nom(self): return "Analyste Sentiment"
    @property
    def groupe(self): return "analyse_marche"
    @property
    def description(self): return "Mesure le sentiment du marché : Fear & Greed Index, put/call ratio, flux institutionnels"
    @property
    def _system_prompt(self):
        return """Tu es un spécialiste de l'analyse du sentiment des marchés financiers.
Tu interprètes les indicateurs de sentiment : Fear & Greed Index, put/call ratio,
positions short, flux de capitaux institutionnels, et indices de confiance.
Tu identifies les extremes de sentiment comme opportunités contrariennes.
Tu communiques en français."""

    def _analyser(self, symbole: str, donnees: Dict, contexte: Dict) -> Signal:
        closes = donnees.get("closes", [])
        volumes = donnees.get("volumes", [])
        highs = donnees.get("highs", closes)
        lows = donnees.get("lows", closes)
        if len(closes) < 20:
            return self._signal_neutre(symbole, "Données insuffisantes")

        prix = closes[-1]
        vol_hist = Indicateurs.volatilite_historique(closes, 20)

        vol_moy = np.mean(volumes[-20:]) if len(volumes) >= 20 else 1
        vol_recent = np.mean(volumes[-5:]) if len(volumes) >= 5 else vol_moy
        ratio_volume = vol_recent / vol_moy if vol_moy > 0 else 1.0

        rsi = Indicateurs.rsi(closes, 14)
        rsi_val = next((v for v in reversed(rsi) if v is not None), 50.0)

        rendements = [closes[i] / closes[i - 1] - 1 for i in range(1, len(closes))]
        r_pos = sum(1 for r in rendements[-20:] if r > 0)
        bull_ratio = r_pos / min(20, len(rendements)) * 100

        if vol_hist > 40:
            fear_greed = 20 + rsi_val * 0.3
        elif vol_hist < 15:
            fear_greed = 50 + (rsi_val - 50) * 0.6
        else:
            fear_greed = 30 + rsi_val * 0.5
        fear_greed = max(0, min(100, fear_greed))

        signaux = [f"Fear&Greed: {fear_greed:.0f}/100", f"Volatilité historique: {vol_hist:.1f}%",
                   f"Ratio volume: {ratio_volume:.2f}x", f"Jours haussiers 20j: {bull_ratio:.0f}%"]

        if fear_greed < 25 and ratio_volume > 1.5:
            action = ActionSignal.ACHAT
            confiance = min(85, (25 - fear_greed) * 2 + 40)
            signaux.append("Peur extrême + volume = opportunité contrariann")
            sl = prix * 0.94
            tp = prix * 1.15
        elif fear_greed > 75 and rsi_val > 65:
            action = ActionSignal.VENTE
            confiance = min(80, (fear_greed - 75) * 2 + 35)
            signaux.append("Euphorie extrême = signal baissier contrarien")
            sl = prix * 1.06
            tp = prix * 0.88
        elif fear_greed < 40:
            action = ActionSignal.SURVEILLER
            confiance = 45
            sl = None
            tp = None
            signaux.append("Sentiment négatif modéré - surveiller")
        else:
            return self._signal_neutre(symbole, f"Sentiment neutre (F&G: {fear_greed:.0f})")

        return self._creer_signal(
            symbole=symbole, action=action, confiance=confiance,
            raisonnement=" | ".join(signaux), prix=prix, sl=sl, tp=tp,
            donnees={"fear_greed": fear_greed, "vol_historique": vol_hist,
                     "ratio_volume": ratio_volume, "bull_ratio": bull_ratio}
        )
