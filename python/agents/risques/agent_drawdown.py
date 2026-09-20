import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from agents.base_agent import BaseAgent
from models.signal import Signal, ActionSignal
from utils.indicators import Indicateurs
from typing import Dict


class AgentDrawdownControl(BaseAgent):
    @property
    def id(self): return "DD-025"
    @property
    def nom(self): return "Contrôleur Drawdown"
    @property
    def groupe(self): return "risques"
    @property
    def description(self): return "Contrôle du drawdown : max drawdown, recovery time, circuit breakers"
    @property
    def _system_prompt(self):
        return """Tu es l'expert en contrôle du drawdown et protection contre les pertes excessives.
Tu surveilles le drawdown en temps réel et actives les circuit breakers si nécessaire.
Un drawdown > 10% déclenche une réduction des positions de 50%.
Un drawdown > 15% suspend le trading. Tu communiques en français."""

    SEUIL_ALERTE = 5.0
    SEUIL_REDUCTION = 10.0
    SEUIL_STOP = 15.0

    def _analyser(self, symbole: str, donnees: Dict, contexte: Dict) -> Signal:
        closes = donnees.get("closes", [])
        portfolio = donnees.get("portfolio", {})
        historique = donnees.get("historique_valeur", [])
        prix = closes[-1] if closes else 0

        valeur_actuelle = portfolio.get("valeur_totale", 100000)
        capital_initial = portfolio.get("capital_initial", 100000)
        pnl_pct = portfolio.get("pnl_total_pct", 0)

        valeurs = [h.get("valeur", capital_initial) for h in historique] if historique else [capital_initial, valeur_actuelle]
        if len(valeurs) < 2:
            valeurs = [capital_initial, valeur_actuelle]

        max_dd_global = Indicateurs.max_drawdown(valeurs)
        pic_actuel = max(valeurs)
        dd_actuel = (valeur_actuelle - pic_actuel) / pic_actuel * 100 if pic_actuel > 0 else 0

        recovery = self._temps_recovery(valeurs)
        nb_jours_sous_pic = sum(1 for v in valeurs if v < pic_actuel)

        actif_dd = []
        if len(closes) > 1:
            pic_actif = max(closes)
            dd_actif = (prix - pic_actif) / pic_actif * 100 if pic_actif > 0 else 0
            actif_dd.append(f"DD actif {symbole}: {dd_actif:.2f}%")

        signaux = [
            f"Drawdown actuel: {dd_actuel:.2f}%",
            f"Max drawdown historique: {max_dd_global:.2f}%",
            f"P&L total: {pnl_pct:+.2f}%",
            f"Jours sous le pic: {nb_jours_sous_pic}",
            f"Recovery estimé: {recovery}",
        ] + actif_dd

        if abs(dd_actuel) >= self.SEUIL_STOP:
            action = ActionSignal.ALERTE
            confiance = 98
            signaux.append(f"CIRCUIT BREAKER ACTIVÉ: DD {dd_actuel:.2f}% → STOP TRADING")
        elif abs(dd_actuel) >= self.SEUIL_REDUCTION:
            action = ActionSignal.ALERTE
            confiance = 90
            signaux.append(f"ALERTE: DD {dd_actuel:.2f}% → Réduire positions 50%")
        elif abs(dd_actuel) >= self.SEUIL_ALERTE:
            action = ActionSignal.SURVEILLER
            confiance = 75
            signaux.append(f"Attention: DD {dd_actuel:.2f}% → Surveiller activement")
        else:
            action = ActionSignal.HOLD
            confiance = 80
            signaux.append(f"Drawdown contrôlé ({dd_actuel:.2f}%)")

        return self._creer_signal(
            symbole=symbole, action=action, confiance=confiance,
            raisonnement=" | ".join(signaux), prix=prix if prix > 0 else None,
            donnees={"dd_actuel": dd_actuel, "max_dd": max_dd_global,
                     "pic": pic_actuel, "recovery": recovery}
        )

    @staticmethod
    def _temps_recovery(valeurs: list) -> str:
        if len(valeurs) < 3:
            return "N/A"
        pic = max(valeurs)
        actuel = valeurs[-1]
        if actuel >= pic:
            return "Récupéré"
        dd_pct = abs(actuel - pic) / pic
        jours_estimes = int(dd_pct * 365 / 0.15)
        return f"~{jours_estimes} jours estimés"
