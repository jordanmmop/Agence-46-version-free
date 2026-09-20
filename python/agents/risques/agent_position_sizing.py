import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from agents.base_agent import BaseAgent
from models.signal import Signal, ActionSignal
from utils.indicators import Indicateurs
from typing import Dict
import numpy as np


class AgentPositionSizing(BaseAgent):
    @property
    def id(self): return "PS-023"
    @property
    def nom(self): return "Gestionnaire Taille de Position"
    @property
    def groupe(self): return "risques"
    @property
    def description(self): return "Dimensionnement des positions : Kelly Criterion, Fixed Fractional, Risk-per-trade"
    @property
    def _system_prompt(self):
        return """Tu es l'expert en dimensionnement de positions de trading.
Tu calcules la taille optimale des positions selon Kelly Criterion, Fixed Fractional
et en fonction du risque par trade.
Règle d'or: ne jamais risquer plus de 2% du capital sur un trade. Tu communiques en français."""

    MAX_RISQUE_TRADE = 0.02
    MAX_POSITION_PCT = 0.15

    def _analyser(self, symbole: str, donnees: Dict, contexte: Dict) -> Signal:
        closes = donnees.get("closes", [])
        portfolio = donnees.get("portfolio", {})
        signal_parent = donnees.get("signal", {})
        # Données indisponibles (yfinance HS) → prix 0 → divisions par zéro plus
        # bas. On renvoie un signal neutre plutôt que de planter.
        if not closes:
            return self._signal_neutre(symbole, "Données insuffisantes pour dimensionner")
        prix = closes[-1] if closes else 0

        capital = portfolio.get("valeur_totale", 100000)
        exposition = portfolio.get("exposition_pct", 0)

        prix_entree = signal_parent.get("prix_entree", prix)
        stop_loss = signal_parent.get("stop_loss")
        confiance = signal_parent.get("confiance", 50)

        rendements = []
        if len(closes) > 20:
            rendements = [closes[i] / closes[i - 1] - 1 for i in range(1, len(closes))]

        risque_par_action = abs(prix_entree - stop_loss) if stop_loss and prix_entree else prix * 0.03
        risque_max_capital = capital * self.MAX_RISQUE_TRADE
        taille_fixed_frac = risque_max_capital / risque_par_action if risque_par_action > 0 else 0
        montant_max = taille_fixed_frac * prix_entree if prix_entree > 0 else 0
        pct_capital_ff = montant_max / capital if capital > 0 else 0

        wins = [r for r in rendements if r > 0]
        losses = [r for r in rendements if r < 0]
        win_rate = len(wins) / len(rendements) if rendements else 0.5
        avg_gain = np.mean(wins) if wins else 0.02
        avg_loss = abs(np.mean(losses)) if losses else 0.02
        kelly = Indicateurs.kelly_criterion(win_rate, avg_gain, avg_loss)
        kelly_demi = kelly * 0.5

        reduction_exposition = max(0, (exposition - 60) / 40)
        reduction_confiance = (100 - confiance) / 100 * 0.5
        taille_finale = min(
            kelly_demi,
            pct_capital_ff,
            self.MAX_POSITION_PCT
        ) * (1 - reduction_exposition) * (1 - reduction_confiance)
        taille_finale = max(0, taille_finale)
        nb_actions = int(taille_finale * capital / prix_entree) if prix_entree > 0 else 0
        montant_position = nb_actions * prix_entree

        signaux = [
            f"Capital: {capital:,.0f}€ | Exposition actuelle: {exposition:.1f}%",
            f"Risque par action: {risque_par_action:.4f} ({risque_par_action/prix*100:.2f}%)" if prix > 0 else f"Risque par action: {risque_par_action:.4f}",
            f"Kelly fraction: {kelly*100:.1f}% | Kelly/2: {kelly_demi*100:.1f}%",
            f"Fixed Fractional: {pct_capital_ff*100:.1f}%",
            f"Taille recommandée: {taille_finale*100:.2f}% ({nb_actions} unités = {montant_position:,.0f}€)",
            f"Win rate historique: {win_rate*100:.1f}% | RR: {avg_gain/avg_loss:.2f}",
        ]

        return self._creer_signal(
            symbole=symbole, action=ActionSignal.HOLD, confiance=75,
            raisonnement=" | ".join(signaux), prix=prix_entree, sl=stop_loss, tp=None,
            taille=taille_finale,
            donnees={"taille_pct": taille_finale, "nb_unites": nb_actions,
                     "montant": montant_position, "kelly": kelly, "win_rate": win_rate}
        )
