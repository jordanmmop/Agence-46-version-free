import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from agents.base_agent import BaseAgent
from models.signal import Signal, ActionSignal
from typing import Dict
import numpy as np


class AgentCorrelation(BaseAgent):
    @property
    def id(self): return "AC-008"
    @property
    def nom(self): return "Analyste Corrélations"
    @property
    def groupe(self): return "analyse_marche"
    @property
    def description(self): return "Analyse des corrélations inter-marchés, bêta et divergences pour signaux"
    @property
    def _system_prompt(self):
        return """Tu es un expert en analyse des corrélations entre marchés financiers.
Tu identifies les liens entre classes d'actifs (crypto/actions/forex/obligations),
détectes les divergences et utilises les corrélations pour prédire les mouvements.
Un actif qui diverge de ses corrélats habituels offre une opportunité de trading.
Tu communiques en français."""

    CORRELATIONS_TYPIQUES = {
        "BTC-USD": {"ETH-USD": 0.85, "SPY": 0.35, "AAPL": 0.30},
        "ETH-USD": {"BTC-USD": 0.85, "SPY": 0.30},
        "AAPL": {"MSFT": 0.75, "QQQ": 0.90, "SPY": 0.80},
        "SPY": {"QQQ": 0.90, "AAPL": 0.80, "MSFT": 0.78},
    }

    def _analyser(self, symbole: str, donnees: Dict, contexte: Dict) -> Signal:
        closes = donnees.get("closes", [])
        correlations = donnees.get("correlations", {})
        prix = closes[-1] if closes else 0
        autres_donnees = donnees.get("autres_symboles", {})

        if not correlations and autres_donnees:
            correlations = self._calculer_correlations(closes, autres_donnees)

        signaux = []
        divergences = []
        score = 50

        corr_attendues = self.CORRELATIONS_TYPIQUES.get(symbole, {})
        for symbole_ref, corr_attendue in corr_attendues.items():
            corr_reelle = correlations.get(symbole_ref)
            if corr_reelle is not None:
                divergence = abs(corr_reelle - corr_attendue)
                if divergence > 0.3:
                    divergences.append(f"{symbole} vs {symbole_ref}: attendu {corr_attendue:.2f}, réel {corr_reelle:.2f}")
                    if corr_reelle < corr_attendue and corr_reelle < 0.3:
                        score += 15
                        signaux.append(f"Découplage {symbole_ref} → {symbole} sous-performant, rattrapage probable")
                    elif corr_reelle > corr_attendue + 0.4:
                        score -= 10
                        signaux.append(f"Sur-corrélation {symbole_ref} → risque contagion")

        rendements_spy = autres_donnees.get("SPY", [])
        if rendements_spy and closes and len(closes) >= len(rendements_spy):
            beta = self._calculer_beta(closes[-len(rendements_spy):], rendements_spy)
            signaux.append(f"Bêta vs SPY: {beta:.2f}")
            if beta > 2.0:
                score -= 10
                signaux.append("Bêta élevé → amplification des mouvements marché")
            elif beta < 0.3:
                score += 5
                signaux.append("Bêta faible → actif défensif")

        if not signaux:
            return self._signal_neutre(symbole, "Corrélations normales, pas de divergence détectée")

        if score >= 65:
            action = ActionSignal.ACHAT
            confiance = min(70, score)
            sl = prix * 0.95 if prix > 0 else None
            tp = prix * 1.10 if prix > 0 else None
        elif score <= 35:
            action = ActionSignal.VENTE
            confiance = min(70, 100 - score)
            sl = prix * 1.05 if prix > 0 else None
            tp = prix * 0.90 if prix > 0 else None
        else:
            action = ActionSignal.SURVEILLER
            confiance = 40
            sl = None
            tp = None

        return self._creer_signal(
            symbole=symbole, action=action, confiance=confiance,
            raisonnement=" | ".join(signaux), prix=prix if prix > 0 else None,
            sl=sl, tp=tp, donnees={"correlations": correlations, "divergences": divergences, "score": score}
        )

    def _calculer_correlations(self, closes: list, autres: dict) -> dict:
        result = {}
        if len(closes) < 10:
            return result
        r1 = [closes[i] / closes[i - 1] - 1 for i in range(1, len(closes))]
        for sym, autres_closes in autres.items():
            if len(autres_closes) < 10:
                continue
            n = min(len(r1), len(autres_closes) - 1)
            r2 = [autres_closes[i] / autres_closes[i - 1] - 1 for i in range(1, len(autres_closes))]
            if n > 5:
                result[sym] = float(np.corrcoef(r1[-n:], r2[-n:])[0, 1])
        return result

    def _calculer_beta(self, closes: list, ref_closes: list) -> float:
        n = min(len(closes), len(ref_closes))
        if n < 5:
            return 1.0
        r_actif = [closes[i] / closes[i - 1] - 1 for i in range(1, n)]
        r_ref = [ref_closes[i] / ref_closes[i - 1] - 1 for i in range(1, n)]
        cov = np.cov(r_actif, r_ref)[0, 1]
        var_ref = np.var(r_ref)
        return cov / var_ref if var_ref > 0 else 1.0
