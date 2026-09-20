import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from agents.base_agent import BaseAgent
from models.signal import Signal, ActionSignal
from typing import Dict
import numpy as np


class AgentStressTest(BaseAgent):
    @property
    def id(self): return "ST-027"
    @property
    def nom(self): return "Analyste Stress Tests"
    @property
    def groupe(self): return "risques"
    @property
    def description(self): return "Stress tests : scénarios extrêmes, VaR conditionnelle (CVaR), black swans"
    @property
    def _system_prompt(self):
        return """Tu es l'expert en stress testing et scénarios extrêmes.
Tu simules des crises financières (2008, COVID-2020, Flash Crash) sur le portefeuille.
Tu calcules le CVaR (Expected Shortfall) et identifies les scénarios catastrophiques.
La préparation aux scénarios extrêmes est ta mission. Tu communiques en français."""

    SCENARIOS = {
        "Crash 2008": -0.50,
        "COVID Mars 2020": -0.35,
        "Flash Crash 2010": -0.10,
        "Hausse taux rapide": -0.20,
        "Crise crypto 2022": -0.65,
        "Hausse 20%": +0.20,
    }

    def _analyser(self, symbole: str, donnees: Dict, contexte: Dict) -> Signal:
        closes = donnees.get("closes", [])
        portfolio = donnees.get("portfolio", {})
        prix = closes[-1] if closes else 0

        valeur = portfolio.get("valeur_totale", 100000)
        rendements = []
        if len(closes) > 10:
            rendements = [closes[i] / closes[i - 1] - 1 for i in range(1, len(closes))]

        resultats_scenarios = {}
        for nom_scenario, choc in self.SCENARIOS.items():
            perte = valeur * choc
            resultats_scenarios[nom_scenario] = {
                "choc_pct": choc * 100,
                "perte_euros": round(perte, 0),
                "valeur_apres": round(valeur + perte, 0),
            }

        pire_cas = min(resultats_scenarios.items(), key=lambda x: x[1]["perte_euros"])
        meilleur_cas = max(resultats_scenarios.items(), key=lambda x: x[1]["perte_euros"])

        cvar_95 = self._cvar(rendements, valeur, 0.95) if len(rendements) > 20 else valeur * 0.03
        cvar_99 = self._cvar(rendements, valeur, 0.99) if len(rendements) > 20 else valeur * 0.05

        vol = np.std(rendements) * np.sqrt(252) * 100 if len(rendements) > 2 else 20

        signaux = [
            f"CVaR 95%: {cvar_95:,.0f}€ ({cvar_95/valeur*100:.1f}%)",
            f"CVaR 99%: {cvar_99:,.0f}€ ({cvar_99/valeur*100:.1f}%)",
            f"Pire scénario: {pire_cas[0]} ({pire_cas[1]['choc_pct']:.0f}% = {pire_cas[1]['perte_euros']:,.0f}€)",
            f"Volatilité annualisée: {vol:.1f}%",
        ]

        niveau_risque = "ÉLEVÉ" if cvar_95 / valeur > 0.05 else "MODÉRÉ" if cvar_95 / valeur > 0.02 else "FAIBLE"
        signaux.append(f"Niveau de risque global: {niveau_risque}")

        if niveau_risque == "ÉLEVÉ":
            action = ActionSignal.ALERTE
            confiance = 85
        elif niveau_risque == "MODÉRÉ":
            action = ActionSignal.SURVEILLER
            confiance = 70
        else:
            action = ActionSignal.HOLD
            confiance = 75

        return self._creer_signal(
            symbole=symbole, action=action, confiance=confiance,
            raisonnement=" | ".join(signaux), prix=prix if prix > 0 else None,
            donnees={"scenarios": resultats_scenarios, "cvar_95": cvar_95,
                     "cvar_99": cvar_99, "niveau_risque": niveau_risque}
        )

    @staticmethod
    def _cvar(rendements: list, capital: float, niveau: float) -> float:
        if len(rendements) < 10:
            return capital * 0.03
        pct = (1 - niveau) * 100
        seuil = np.percentile(rendements, pct)
        tail = [r for r in rendements if r <= seuil]
        return abs(np.mean(tail) * capital) if tail else capital * 0.03
