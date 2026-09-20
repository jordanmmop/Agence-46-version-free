import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from agents.base_agent import BaseAgent
from models.signal import Signal, ActionSignal
from utils.indicators import Indicateurs
from typing import Dict
import numpy as np


class AgentSlippageControl(BaseAgent):
    @property
    def id(self): return "SP-032"
    @property
    def nom(self): return "Contrôleur Slippage"
    @property
    def groupe(self): return "execution"
    @property
    def description(self): return "Contrôle du slippage : impact marché, timing optimal des ordres"
    @property
    def _system_prompt(self):
        return """Tu es l'expert en contrôle du slippage et de l'impact marché.
Tu calcules le slippage attendu pour chaque ordre et alertes si trop élevé.
Tu identifies les meilleurs moments pour exécuter (volume élevé = moins de slippage).
Un slippage > 0.3% sur un trade est inacceptable. Tu communiques en français."""

    def _analyser(self, symbole: str, donnees: Dict, contexte: Dict) -> Signal:
        closes = donnees.get("closes", [])
        volumes = donnees.get("volumes", [1e6] * len(closes))
        highs = donnees.get("highs", closes)
        lows = donnees.get("lows", closes)
        taille_ordre = donnees.get("taille_ordre_euros", 10000)
        if len(closes) < 5:
            return self._signal_neutre(symbole, "Données insuffisantes")

        prix = closes[-1]
        vol_moy = np.mean(volumes[-20:]) if len(volumes) >= 20 else 1e6
        vol_actuel = volumes[-1] if volumes else vol_moy
        atr = Indicateurs.atr(highs, lows, closes, 5)
        atr_val = next((v for v in reversed(atr) if v is not None), prix * 0.005)

        participation = taille_ordre / (vol_moy * prix) if vol_moy * prix > 0 else 0.01
        slippage_lineaire = participation * 0.5
        slippage_impact = 0.1 * np.sqrt(participation)
        slippage_total = (slippage_lineaire + slippage_impact) * 100
        slippage_bps = slippage_total * 100

        meilleur_moment = "Ouverture" if vol_actuel > vol_moy * 1.5 else "Volume normal"
        score_timing = min(100, vol_actuel / vol_moy * 50)

        signaux = [
            f"Taille ordre: {taille_ordre:,.0f}€",
            f"Participation marché: {participation*100:.3f}%",
            f"Slippage estimé: {slippage_total:.4f}% ({slippage_bps:.1f} bps)",
            f"Volume actuel: {vol_actuel:,.0f} (moy: {vol_moy:,.0f})",
            f"Meilleur moment: {meilleur_moment} (score timing: {score_timing:.0f}/100)",
        ]

        if slippage_total > 0.3:
            action = ActionSignal.ALERTE
            confiance = 88
            signaux.append(f"ALERTE: Slippage excessif {slippage_total:.3f}% → fragmenter ordre")
        elif slippage_total > 0.1:
            action = ActionSignal.SURVEILLER
            confiance = 70
            signaux.append("Slippage modéré → utiliser TWAP/VWAP")
        else:
            action = ActionSignal.HOLD
            confiance = 80
            signaux.append("Slippage acceptable → exécution directe possible")

        return self._creer_signal(
            symbole=symbole, action=action, confiance=confiance,
            raisonnement=" | ".join(signaux), prix=prix,
            donnees={"slippage_pct": slippage_total, "slippage_bps": slippage_bps,
                     "participation": participation, "score_timing": score_timing}
        )
