import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from agents.base_agent import BaseAgent
from models.signal import Signal, ActionSignal
from utils.indicators import Indicateurs
from typing import Dict
import numpy as np


class AgentGestionLiquidite(BaseAgent):
    @property
    def id(self): return "LQ-026"
    @property
    def nom(self): return "Gestionnaire Liquidité"
    @property
    def groupe(self): return "risques"
    @property
    def description(self): return "Gestion de la liquidité : volume, bid/ask spread, impact marché"
    @property
    def _system_prompt(self):
        return """Tu es l'expert en gestion de la liquidité des marchés.
Tu analyses le volume, la profondeur du marché et l'impact des ordres.
Un actif peu liquide nécessite des positions plus petites et plus de prudence.
Tu calcules le slippage estimé et le coût d'impact marché. Tu communiques en français."""

    def _analyser(self, symbole: str, donnees: Dict, contexte: Dict) -> Signal:
        closes = donnees.get("closes", [])
        volumes = donnees.get("volumes", [])
        highs = donnees.get("highs", closes)
        lows = donnees.get("lows", closes)
        if len(closes) < 10:
            return self._signal_neutre(symbole, "Données insuffisantes")

        prix = closes[-1]
        vol_moy_20 = np.mean(volumes[-20:]) if len(volumes) >= 20 else np.mean(volumes) if volumes else 1e6
        vol_moy_5 = np.mean(volumes[-5:]) if len(volumes) >= 5 else vol_moy_20
        ratio_vol = vol_moy_5 / vol_moy_20 if vol_moy_20 > 0 else 1.0

        atr = Indicateurs.atr(highs, lows, closes, 14)
        atr_val = next((v for v in reversed(atr) if v is not None), prix * 0.02)
        spread_estime = atr_val * 0.05
        spread_pct = spread_estime / prix * 100

        position_test = 10000
        impact_marche = self._calculer_impact(position_test, vol_moy_20 * prix, spread_pct)
        taille_max_liquide = vol_moy_20 * prix * 0.01

        score_liquidite = 100
        signaux = [
            f"Volume moyen 20j: {vol_moy_20:,.0f}",
            f"Ratio vol récent/moy: {ratio_vol:.2f}",
            f"Spread estimé: {spread_pct:.3f}%",
            f"Impact marché (10k€): {impact_marche:.3f}%",
            f"Taille max liquide: {taille_max_liquide:,.0f}€",
        ]

        if vol_moy_20 < 100000:
            score_liquidite -= 40
            signaux.append("ALERTE: Faible liquidité → tailles réduites requises")
        elif vol_moy_20 < 500000:
            score_liquidite -= 20
            signaux.append("Liquidité modérée → prudence sur tailles")

        if ratio_vol < 0.5:
            score_liquidite -= 15
            signaux.append(f"Volume en baisse ({ratio_vol:.2f}x moy)")
        elif ratio_vol > 2:
            score_liquidite += 10
            signaux.append(f"Volume élevé ({ratio_vol:.2f}x moy) → bonne liquidité")

        if score_liquidite < 40:
            action = ActionSignal.ALERTE
            confiance = 85
        elif score_liquidite < 65:
            action = ActionSignal.SURVEILLER
            confiance = 70
        else:
            action = ActionSignal.HOLD
            confiance = 75

        return self._creer_signal(
            symbole=symbole, action=action, confiance=confiance,
            raisonnement=" | ".join(signaux), prix=prix,
            donnees={"score_liquidite": score_liquidite, "vol_moy_20": vol_moy_20,
                     "spread_pct": spread_pct, "impact_marche": impact_marche,
                     "taille_max": taille_max_liquide}
        )

    @staticmethod
    def _calculer_impact(taille_ordre: float, volume_quotidien: float, spread_pct: float) -> float:
        if volume_quotidien == 0:
            return spread_pct
        participation = taille_ordre / volume_quotidien
        impact = spread_pct * 0.5 + 0.1 * np.sqrt(participation) * 100
        return round(impact, 4)
