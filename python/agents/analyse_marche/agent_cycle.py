import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from agents.base_agent import BaseAgent
from models.signal import Signal, ActionSignal
from utils.indicators import Indicateurs
from typing import Dict
import numpy as np


class AgentCycleMarche(BaseAgent):
    @property
    def id(self): return "CY-009"
    @property
    def nom(self): return "Analyste Cycles de Marché"
    @property
    def groupe(self): return "analyse_marche"
    @property
    def description(self): return "Identification des phases de marché : accumulation, markup, distribution, markdown"
    @property
    def _system_prompt(self):
        return """Tu es un expert en cycles de marché et théorie de Wyckoff.
Tu identifies les phases : accumulation, markup (haussier), distribution, markdown (baissier).
Tu reconnais les structures de marché et les zones de demande/offre importantes.
Tu communiques en français avec une vision long terme des marchés."""

    def _analyser(self, symbole: str, donnees: Dict, contexte: Dict) -> Signal:
        closes = donnees.get("closes", [])
        highs = donnees.get("highs", closes)
        lows = donnees.get("lows", closes)
        volumes = donnees.get("volumes", [1.0] * len(closes))
        if len(closes) < 50:
            return self._signal_neutre(symbole, "Données insuffisantes pour analyse de cycle")

        prix = closes[-1]
        ema20 = Indicateurs.ema(closes, 20)
        ema50 = Indicateurs.ema(closes, 50)
        sma200 = Indicateurs.sma(closes, min(200, len(closes)))

        ema20_val = next((v for v in reversed(ema20) if v is not None), prix)
        ema50_val = next((v for v in reversed(ema50) if v is not None), prix)
        sma200_val = next((v for v in reversed(sma200) if v is not None), prix)

        prix_max_20 = max(closes[-20:])
        prix_min_20 = min(closes[-20:])
        range_pct = (prix_max_20 - prix_min_20) / prix_min_20 * 100

        vol_recente = np.mean(volumes[-10:]) if len(volumes) >= 10 else 0
        vol_ancienne = np.mean(volumes[-30:-10]) if len(volumes) >= 30 else vol_recente
        tendance_vol = "hausse" if vol_recente > vol_ancienne * 1.3 else "baisse" if vol_recente < vol_ancienne * 0.7 else "stable"

        phase = self._identifier_phase(prix, ema20_val, ema50_val, sma200_val, closes, tendance_vol)

        signaux = [
            f"Phase: {phase}",
            f"EMA20/EMA50: {ema20_val:.2f}/{ema50_val:.2f}",
            f"Prix vs SMA200: {((prix / sma200_val - 1) * 100):.1f}%",
            f"Range 20j: {range_pct:.1f}%",
            f"Volume: {tendance_vol}",
        ]

        if phase in ["MARKUP", "DÉBUT_MARKUP"]:
            action = ActionSignal.ACHAT
            confiance = 72 if phase == "MARKUP" else 60
            sl = min(closes[-5:]) * 0.98
            tp = prix * 1.15
        elif phase in ["MARKDOWN", "DÉBUT_MARKDOWN"]:
            action = ActionSignal.VENTE
            confiance = 70 if phase == "MARKDOWN" else 58
            sl = max(closes[-5:]) * 1.02
            tp = prix * 0.88
        elif phase == "ACCUMULATION":
            action = ActionSignal.SURVEILLER
            confiance = 55
            signaux.append("Zone d'accumulation → préparer position longue")
            sl = None
            tp = None
        else:
            action = ActionSignal.SURVEILLER
            confiance = 40
            sl = None
            tp = None

        return self._creer_signal(
            symbole=symbole, action=action, confiance=confiance,
            raisonnement=" | ".join(signaux), prix=prix, sl=sl, tp=tp,
            donnees={"phase": phase, "ema20": ema20_val, "ema50": ema50_val, "sma200": sma200_val}
        )

    def _identifier_phase(self, prix, ema20, ema50, sma200, closes, tendance_vol) -> str:
        above_sma200 = prix > sma200
        ema20_above_50 = ema20 > ema50
        rendements_recents = [closes[i] / closes[i - 1] - 1 for i in range(max(1, len(closes) - 10), len(closes))]
        tendance_prix = "hausse" if np.mean(rendements_recents) > 0.001 else "baisse" if np.mean(rendements_recents) < -0.001 else "lateral"

        if above_sma200 and ema20_above_50 and tendance_prix == "hausse":
            return "MARKUP"
        elif above_sma200 and ema20_above_50 and tendance_prix == "lateral":
            return "DISTRIBUTION"
        elif not above_sma200 and not ema20_above_50 and tendance_prix == "baisse":
            return "MARKDOWN"
        elif not above_sma200 and tendance_prix == "lateral" and tendance_vol == "hausse":
            return "ACCUMULATION"
        elif above_sma200 and not ema20_above_50:
            return "DÉBUT_MARKDOWN"
        elif not above_sma200 and ema20_above_50:
            return "DÉBUT_MARKUP"
        return "LATERAL"
