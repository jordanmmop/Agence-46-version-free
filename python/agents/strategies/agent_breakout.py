import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from agents.base_agent import BaseAgent
from models.signal import Signal, ActionSignal
from utils.indicators import Indicateurs
from typing import Dict
import numpy as np


class AgentBreakout(BaseAgent):
    @property
    def id(self): return "BR-017"
    @property
    def nom(self): return "Trader Breakout"
    @property
    def groupe(self): return "strategies"
    @property
    def description(self): return "Cassures de niveaux : breakouts de consolidations, triangles, canaux"
    @property
    def _system_prompt(self):
        return """Tu es un expert en trading de breakout.
Tu identifies les zones de consolidation et les cassures de niveaux clés avec confirmation de volume.
Un vrai breakout nécessite volume > 150% de la moyenne et confirmation de la bougie.
Tu utilises les canaux, triangles et rectangles. Tu communiques en français."""

    def _analyser(self, symbole: str, donnees: Dict, contexte: Dict) -> Signal:
        closes = donnees.get("closes", [])
        highs = donnees.get("highs", closes)
        lows = donnees.get("lows", closes)
        volumes = donnees.get("volumes", [1e6] * len(closes))
        if len(closes) < 25:
            return self._signal_neutre(symbole, "Données insuffisantes")

        prix = closes[-1]
        n_consolidation = 20
        # La zone de consolidation EXCLUT la bougie courante ([-21:-1]).
        # En l'incluant, l'agent était TOTALEMENT INERTE : par définition OHLC,
        # low[-1] ≤ close[-1] ≤ high[-1], donc max(highs) ≥ prix et
        # min(lows) ≤ prix — les conditions « prix > haut_zone × 1.002 » et
        # « prix < bas_zone × 0.998 » ne pouvaient JAMAIS être vraies. Sur
        # 4 000 marchés simulés, l'agent n'émettait aucun BUY/SELL : un
        # quarante-cinquième des voix du consensus était muet en permanence.
        # Un breakout se définit d'ailleurs par rapport aux bougies PRÉCÉDENTES.
        haut_zone = max(highs[-n_consolidation - 1:-1])
        bas_zone = min(lows[-n_consolidation - 1:-1])
        if bas_zone <= 0:
            return self._signal_neutre(symbole, "Zone de consolidation invalide")
        range_zone = (haut_zone - bas_zone) / bas_zone * 100

        vol_moy = np.mean(volumes[-20:]) if len(volumes) >= 20 else 1
        vol_actuelle = volumes[-1] if volumes else vol_moy
        ratio_vol = vol_actuelle / vol_moy if vol_moy > 0 else 1

        atr = Indicateurs.atr(highs, lows, closes, 14)
        atr_val = next((v for v in reversed(atr) if v is not None), prix * 0.02)

        signaux = [
            f"Zone consolidation: {bas_zone:.4f} - {haut_zone:.4f} ({range_zone:.1f}%)",
            f"Ratio volume: {ratio_vol:.2f}x",
            f"ATR: {atr_val:.4f}",
        ]

        if range_zone < 2:
            signaux.append("Consolidation serrée → énergie accumulée")

        seuil_cassure = 0.002

        if prix > haut_zone * (1 + seuil_cassure) and ratio_vol > 1.3:
            action = ActionSignal.ACHAT
            confiance = min(80, 55 + ratio_vol * 10 + (1 - range_zone / 10) * 10)
            sl = haut_zone * 0.998
            tp = prix + (prix - bas_zone) * 1.5
            signaux.append(f"BREAKOUT HAUSSIER confirmé (vol {ratio_vol:.1f}x) → cible: {tp:.4f}")
        elif prix < bas_zone * (1 - seuil_cassure) and ratio_vol > 1.3:
            action = ActionSignal.VENTE
            confiance = min(80, 55 + ratio_vol * 10 + (1 - range_zone / 10) * 10)
            sl = bas_zone * 1.002
            tp = prix - (haut_zone - prix) * 1.5
            signaux.append(f"BREAKOUT BAISSIER confirmé (vol {ratio_vol:.1f}x) → cible: {tp:.4f}")
        elif prix > haut_zone * 0.998 and ratio_vol > 1.1:
            action = ActionSignal.SURVEILLER
            confiance = 50
            sl = None
            tp = None
            signaux.append("Tentative cassure haussière - attendre confirmation")
        elif prix < bas_zone * 1.002 and ratio_vol > 1.1:
            action = ActionSignal.SURVEILLER
            confiance = 50
            sl = None
            tp = None
            signaux.append("Tentative cassure baissière - attendre confirmation")
        else:
            return self._signal_neutre(symbole, "Pas de breakout - actif en consolidation")

        return self._creer_signal(
            symbole=symbole, action=action, confiance=confiance,
            raisonnement=" | ".join(signaux), prix=prix, sl=sl, tp=tp, taille=0.05,
            donnees={"haut_zone": haut_zone, "bas_zone": bas_zone,
                     "ratio_vol": ratio_vol, "range_pct": range_zone}
        )
