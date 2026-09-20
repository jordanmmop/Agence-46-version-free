import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from agents.base_agent import BaseAgent
from models.signal import Signal, ActionSignal
from utils.indicators import Indicateurs
from typing import Dict
import numpy as np


class AgentGridTrading(BaseAgent):
    @property
    def id(self): return "GT-018"
    @property
    def nom(self): return "Trader Grille"
    @property
    def groupe(self): return "strategies"
    @property
    def description(self): return "Grid trading : grilles d'ordres automatiques en range pour marchés latéraux"
    @property
    def _system_prompt(self):
        return """Tu es un expert en grid trading (trading en grille).
Tu places des ordres d'achat et de vente à intervalles réguliers dans une range de prix.
La grille est optimale sur des marchés latéraux avec forte volatilité intraday.
Tu calcules l'espacement optimal des niveaux et la taille des positions. Tu communiques en français."""

    def _analyser(self, symbole: str, donnees: Dict, contexte: Dict) -> Signal:
        closes = donnees.get("closes", [])
        highs = donnees.get("highs", closes)
        lows = donnees.get("lows", closes)
        if len(closes) < 20:
            return self._signal_neutre(symbole, "Données insuffisantes")

        prix = closes[-1]
        vol_hist = Indicateurs.volatilite_historique(closes, 20)
        atr = Indicateurs.atr(highs, lows, closes, 14)
        atr_val = next((v for v in reversed(atr) if v is not None), prix * 0.02)

        range_20 = max(highs[-20:]) - min(lows[-20:])
        range_pct = range_20 / prix * 100
        rendements = [closes[i] / closes[i - 1] - 1 for i in range(1, len(closes))]
        tendance = np.mean(rendements[-10:]) * 100 if len(rendements) >= 10 else 0

        nb_niveaux = 10
        espacement = atr_val / prix * 100
        grille_haut = prix * (1 + range_pct / 100 * 0.5)
        grille_bas = prix * (1 - range_pct / 100 * 0.5)
        niveaux = [grille_bas + i * (grille_haut - grille_bas) / nb_niveaux for i in range(nb_niveaux + 1)]

        signaux = [
            f"Range 20j: {range_pct:.1f}%",
            f"Volatilité: {vol_hist:.1f}%",
            f"Tendance 10j: {tendance:+.2f}%/j",
            f"Grille: {grille_bas:.4f} → {grille_haut:.4f} ({nb_niveaux} niveaux)",
            f"Espacement: {espacement:.2f}%",
        ]

        est_range = abs(tendance) < 0.1 and range_pct > 3

        if est_range and vol_hist > 15:
            action = ActionSignal.ACHAT
            confiance = min(72, 50 + range_pct * 2)
            sl = grille_bas * 0.97
            tp = grille_haut * 1.01
            signaux.append("Marché en range → grille optimale activée")
        elif abs(tendance) > 0.5:
            return self._signal_neutre(symbole, f"Tendance trop forte ({tendance:.2f}%/j) → grille non recommandée")
        else:
            action = ActionSignal.SURVEILLER
            confiance = 40
            sl = None
            tp = None
            signaux.append("Conditions sub-optimales pour grille")

        return self._creer_signal(
            symbole=symbole, action=action, confiance=confiance,
            raisonnement=" | ".join(signaux), prix=prix,
            sl=sl, tp=tp, taille=0.02,
            donnees={"niveaux_grille": [round(n, 4) for n in niveaux],
                     "espacement_pct": espacement, "range_pct": range_pct}
        )
