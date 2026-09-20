import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from agents.base_agent import BaseAgent
from models.signal import Signal, ActionSignal
from utils.indicators import Indicateurs
from typing import Dict
import numpy as np


class AgentVolatilite(BaseAgent):
    @property
    def id(self): return "AV-007"
    @property
    def nom(self): return "Analyste Volatilité"
    @property
    def groupe(self): return "analyse_marche"
    @property
    def description(self): return "Analyse de la volatilité : VIX, ATR, volatilité historique vs implicite"
    @property
    def _system_prompt(self):
        return """Tu es un expert en volatilité des marchés financiers.
Tu analyses la volatilité historique, implicite, le VIX et l'ATR pour évaluer le risque de marché.
Tu identifies les régimes de faible/haute volatilité et adaptes les stratégies en conséquence.
Une faible volatilité indique calme avant tempête ; haute volatilité peut signifier opportunité.
Tu communiques en français."""

    def _analyser(self, symbole: str, donnees: Dict, contexte: Dict) -> Signal:
        closes = donnees.get("closes", [])
        highs = donnees.get("highs", closes)
        lows = donnees.get("lows", closes)
        if len(closes) < 30:
            return self._signal_neutre(symbole, "Données insuffisantes pour analyse volatilité")

        prix = closes[-1]
        atr_vals = Indicateurs.atr(highs, lows, closes, 14)
        atr = next((v for v in reversed(atr_vals) if v is not None), 0)
        atr_pct = atr / prix * 100

        vol_20 = Indicateurs.volatilite_historique(closes, 20)
        vol_60 = Indicateurs.volatilite_historique(closes, min(60, len(closes)))
        vol_ratio = vol_20 / vol_60 if vol_60 > 0 else 1.0

        rendements = [closes[i] / closes[i - 1] - 1 for i in range(1, len(closes))]
        vol_moy_10 = np.std(rendements[-10:]) * np.sqrt(Indicateurs.BOUGIES_PAR_AN_1H) * 100 if len(rendements) >= 10 else vol_20
        tendance_vol = "hausse" if vol_20 > vol_60 * 1.2 else "baisse" if vol_20 < vol_60 * 0.8 else "stable"

        parkinson = self._parkinson(highs[-20:], lows[-20:])

        signaux = [
            f"ATR: {atr:.4f} ({atr_pct:.2f}%)",
            f"Vol historique 20j: {vol_20:.1f}%",
            f"Vol historique 60j: {vol_60:.1f}%",
            f"Ratio vol: {vol_ratio:.2f} ({tendance_vol})",
            f"Vol Parkinson 20j: {parkinson:.1f}%",
        ]

        if vol_20 < 15 and vol_ratio < 0.7:
            action = ActionSignal.ALERTE
            confiance = 70
            signaux.append("Volatilité compressée → explosion imminente possible")
            sl = None
            tp = None
        elif vol_20 > 50 and atr_pct > 5:
            action = ActionSignal.ALERTE
            confiance = 75
            signaux.append(f"Volatilité extrême ({vol_20:.0f}%) → réduction positions recommandée")
            sl = None
            tp = None
        elif vol_20 < 25 and vol_ratio < 1.1:
            action = ActionSignal.ACHAT
            confiance = 55
            signaux.append("Environnement favorable basse volatilité → opportunité carry")
            sl = prix * (1 - atr_pct / 100 * 2)
            tp = prix * (1 + atr_pct / 100 * 4)
        else:
            return self._signal_neutre(symbole, f"Vol neutre {vol_20:.1f}% | ATR {atr_pct:.2f}%")

        return self._creer_signal(
            symbole=symbole, action=action, confiance=confiance,
            raisonnement=" | ".join(signaux), prix=prix, sl=sl, tp=tp,
            donnees={"atr": atr, "atr_pct": atr_pct, "vol_20": vol_20,
                     "vol_60": vol_60, "vol_ratio": vol_ratio, "parkinson": parkinson}
        )

    @staticmethod
    def _parkinson(highs: list, lows: list) -> float:
        if len(highs) < 2:
            return 0.0
        somme = sum((np.log(h / l)) ** 2 for h, l in zip(highs, lows) if l > 0)
        return np.sqrt(somme / (4 * len(highs) * np.log(2))) * np.sqrt(Indicateurs.BOUGIES_PAR_AN_1H) * 100
