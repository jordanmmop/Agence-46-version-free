import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from agents.base_agent import BaseAgent
from models.signal import Signal, ActionSignal
from typing import Dict
import numpy as np


class AgentRiskManager(BaseAgent):
    @property
    def id(self): return "RM-021"
    @property
    def nom(self): return "Gestionnaire des Risques"
    @property
    def groupe(self): return "risques"
    @property
    def description(self): return "Évaluation globale du risque portefeuille, VaR, limites d'exposition"
    @property
    def _system_prompt(self):
        return """Tu es le gestionnaire des risques principal de l'agence.
Tu évalues le risque global du portefeuille : VaR, exposition, concentration, corrélations.
Tu établis des limites de risque et alertes quand elles sont atteintes.
Ta priorité absolue est la préservation du capital. Tu communiques en français."""

    def _analyser(self, symbole: str, donnees: Dict, contexte: Dict) -> Signal:
        closes = donnees.get("closes", [])
        portfolio = donnees.get("portfolio", {})
        prix = closes[-1] if closes else 0

        capital = portfolio.get("capital_initial", 100000)
        valeur = portfolio.get("valeur_totale", capital)
        exposition = portfolio.get("exposition_pct", 0)
        nb_positions = portfolio.get("nb_positions", 0)
        drawdown = portfolio.get("drawdown_actuel", 0)
        pnl_pct = portfolio.get("pnl_total_pct", 0)

        rendements = []
        if len(closes) > 1:
            rendements = [closes[i] / closes[i - 1] - 1 for i in range(1, len(closes))]

        var_95 = self._calculer_var(rendements, valeur, 0.95) if rendements else valeur * 0.02
        var_99 = self._calculer_var(rendements, valeur, 0.99) if rendements else valeur * 0.03

        risk_score = 100
        alertes = []

        if abs(drawdown) > 8:
            risk_score -= 30
            alertes.append(f"ALERTE: Drawdown critique {drawdown:.1f}%")
        elif abs(drawdown) > 5:
            risk_score -= 15
            alertes.append(f"Drawdown élevé {drawdown:.1f}%")

        if exposition > 85:
            risk_score -= 20
            alertes.append(f"Sur-exposition ({exposition:.0f}%)")
        elif exposition > 70:
            risk_score -= 10

        if nb_positions > 12:
            risk_score -= 10
            alertes.append(f"Trop de positions ouvertes ({nb_positions})")

        var_pct = var_95 / valeur * 100 if valeur > 0 else 0
        if var_pct > 3:
            risk_score -= 15
            alertes.append(f"VaR 95% élevée: {var_pct:.1f}%")

        signaux = [
            f"Score risque: {risk_score}/100",
            f"VaR 95%: {var_95:.0f}€ ({var_pct:.2f}%)",
            f"VaR 99%: {var_99:.0f}€",
            f"Exposition: {exposition:.1f}% | Positions: {nb_positions}",
            f"Drawdown: {drawdown:.2f}%",
        ] + alertes

        if risk_score < 50:
            action = ActionSignal.ALERTE
            confiance = 90
            sl = None
            tp = None
        elif risk_score < 70:
            action = ActionSignal.SURVEILLER
            confiance = 65
            sl = None
            tp = None
        else:
            action = ActionSignal.HOLD
            confiance = 80
            sl = None
            tp = None

        return self._creer_signal(
            symbole=symbole, action=action, confiance=confiance,
            raisonnement=" | ".join(signaux), sl=sl, tp=tp,
            donnees={"risk_score": risk_score, "var_95": var_95, "var_99": var_99,
                     "exposition": exposition, "nb_alertes": len(alertes)}
        )

    @staticmethod
    def _calculer_var(rendements: list, capital: float, niveau: float) -> float:
        if len(rendements) < 10:
            return capital * 0.02
        percentile = (1 - niveau) * 100
        return abs(float(np.percentile(rendements, percentile)) * capital)
