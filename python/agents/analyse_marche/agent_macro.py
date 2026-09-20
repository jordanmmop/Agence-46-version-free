import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from agents.base_agent import BaseAgent
from models.signal import Signal, ActionSignal
from typing import Dict


class AgentMacroEconomique(BaseAgent):
    @property
    def id(self): return "ME-005"
    @property
    def nom(self): return "Analyste Macro-Économique"
    @property
    def groupe(self): return "analyse_marche"
    @property
    def description(self): return "Analyse macro : taux directeurs, inflation, PIB, chômage, politique monétaire"
    @property
    def _system_prompt(self):
        return """Tu es un économiste spécialisé dans l'analyse macro-économique pour le trading.
Tu analyses les données macroéconomiques : taux d'intérêt Fed/BCE, inflation, PIB, emploi,
et leur impact sur les marchés financiers. Tu identifies les tendances macro de fond
et leur influence sur les classes d'actifs. Tu communiques en français."""

    TAUX_FED = 5.25
    INFLATION_US = 3.1
    CROISSANCE_PIB = 2.1
    TAUX_CHOMAGE = 3.9

    def _analyser(self, symbole: str, donnees: Dict, contexte: Dict) -> Signal:
        macro = donnees.get("macro", {})
        closes = donnees.get("closes", [])
        prix = closes[-1] if closes else 0
        est_crypto = any(c in symbole for c in ["BTC", "ETH", "BNB", "SOL"])
        est_action = any(c in symbole for c in ["AAPL", "MSFT", "SPY", "QQQ", "NVDA"])

        # `donnees["macro"]` n'est pas alimenté par l'orchestrateur : sans lui,
        # ces constantes sont des repères FIGÉS (fin 2023), pas des données
        # courantes. On limite alors l'influence du signal sur les ordres réels.
        macro_figee = not macro
        taux = macro.get("taux_fed", self.TAUX_FED)
        inflation = macro.get("inflation", self.INFLATION_US)
        pib = macro.get("pib_croissance", self.CROISSANCE_PIB)
        chomage = macro.get("chomage", self.TAUX_CHOMAGE)

        score = 50
        signaux = []

        taux_reel = taux - inflation
        if taux_reel < -1:
            if est_crypto or est_action:
                score += 15
                signaux.append(f"Taux réel négatif ({taux_reel:.1f}%) → favorable actifs risqués")
        elif taux_reel > 3:
            score -= 15
            signaux.append(f"Taux réel élevé ({taux_reel:.1f}%) → défavorable actifs risqués")

        if inflation < 2.5:
            score += 10
            signaux.append(f"Inflation maîtrisée ({inflation:.1f}%) → zone de confort Fed")
        elif inflation > 4:
            score -= 15
            signaux.append(f"Inflation haute ({inflation:.1f}%) → pression sur taux")

        if pib > 2.5:
            if est_action:
                score += 15
                signaux.append(f"Croissance forte ({pib:.1f}%) → bon pour les actions")
        elif pib < 0.5:
            score -= 20
            signaux.append(f"Croissance faible ({pib:.1f}%) → risque récession")

        if chomage < 4.0:
            score += 8
            signaux.append(f"Emploi fort ({chomage:.1f}%) → consommation solide")
        elif chomage > 5.5:
            score -= 10
            signaux.append(f"Chômage élevé ({chomage:.1f}%) → faiblesse économique")

        if score >= 65:
            action = ActionSignal.ACHAT
            confiance = min(75, score)
            sl = prix * 0.93 if prix > 0 else None
            tp = prix * 1.15 if prix > 0 else None
        elif score <= 35:
            action = ActionSignal.VENTE
            confiance = min(75, 100 - score)
            sl = prix * 1.07 if prix > 0 else None
            tp = prix * 0.87 if prix > 0 else None
        else:
            action = ActionSignal.SURVEILLER
            confiance = abs(score - 50) + 20
            sl = None
            tp = None

        raison = " | ".join(signaux) if signaux else "Conditions macro neutres"
        if macro_figee:
            confiance = min(confiance, 45)
            raison = "[repères macro figés, non actualisés] " + raison

        return self._creer_signal(
            symbole=symbole, action=action, confiance=confiance,
            raisonnement=raison,
            prix=prix if prix > 0 else None, sl=sl, tp=tp,
            donnees={"score_macro": score, "taux_fed": taux, "inflation": inflation,
                     "pib": pib, "chomage": chomage, "taux_reel": taux_reel,
                     "simule": macro_figee}
        )
