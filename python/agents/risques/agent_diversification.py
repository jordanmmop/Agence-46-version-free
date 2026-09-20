import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from agents.base_agent import BaseAgent
from models.signal import Signal, ActionSignal
from typing import Dict


class AgentDiversification(BaseAgent):
    @property
    def id(self): return "DV-024"
    @property
    def nom(self): return "Analyste Diversification"
    @property
    def groupe(self): return "risques"
    @property
    def description(self): return "Analyse diversification : allocation d'actifs, corrélations portefeuille, Markowitz"
    @property
    def _system_prompt(self):
        return """Tu es un expert en diversification de portefeuille et théorie moderne de portefeuille (Markowitz).
Tu analyses la concentration du portefeuille, les corrélations entre positions,
et recommandes la meilleure allocation pour maximiser le Sharpe ratio.
Un portefeuille optimal combine actifs faiblement corrélés. Tu communiques en français."""

    CLASSES_ACTIFS = {
        "crypto": ["BTC-USD", "ETH-USD", "BNB-USD", "SOL-USD"],
        "actions_tech": ["AAPL", "MSFT", "NVDA", "GOOGL"],
        "indices": ["SPY", "QQQ", "^GSPC"],
        "forex": ["EURUSD=X", "GBPUSD=X"],
    }

    def _analyser(self, symbole: str, donnees: Dict, contexte: Dict) -> Signal:
        portfolio = donnees.get("portfolio", {})
        positions = portfolio.get("positions", {})
        prix = self._dernier_prix(donnees)

        if not positions:
            positions_sim = {symbole: {"valeur_marche": 10000}}
        else:
            positions_sim = positions

        total = sum(p.get("valeur_marche", 0) for p in positions_sim.values())
        if total == 0:
            return self._signal_neutre(symbole, "Portefeuille vide")

        allocation_classes = {classe: 0.0 for classe in self.CLASSES_ACTIFS}
        for sym, pos in positions_sim.items():
            val = pos.get("valeur_marche", 0) / total * 100
            for classe, symboles_classe in self.CLASSES_ACTIFS.items():
                if sym in symboles_classe:
                    allocation_classes[classe] += val

        herfindahl = sum((val / 100) ** 2 for val in [p.get("valeur_marche", 0) / total for p in positions_sim.values()])
        concentration_score = herfindahl * 100
        nb_positions = len(positions_sim)
        classe_symbole = next((c for c, s in self.CLASSES_ACTIFS.items() if symbole in s), "autre")

        signaux = [
            f"Positions: {nb_positions} | Index Herfindahl: {concentration_score:.1f}",
            f"Allocation: {', '.join(f'{c}:{v:.0f}%' for c,v in allocation_classes.items() if v > 0)}",
            f"Classe {symbole}: {classe_symbole}",
        ]

        crypto_pct = allocation_classes.get("crypto", 0)
        tech_pct = allocation_classes.get("actions_tech", 0)

        if crypto_pct > 40:
            signaux.append(f"RISQUE: Sur-exposition crypto ({crypto_pct:.0f}%)")
            action = ActionSignal.ALERTE
            confiance = 80
        elif tech_pct > 50:
            signaux.append(f"RISQUE: Sur-concentration tech ({tech_pct:.0f}%)")
            action = ActionSignal.ALERTE
            confiance = 75
        elif concentration_score > 50:
            signaux.append("Concentration élevée → diversifier vers d'autres classes")
            action = ActionSignal.SURVEILLER
            confiance = 65
        elif nb_positions < 3:
            signaux.append("Portefeuille peu diversifié → ajouter positions")
            action = ActionSignal.SURVEILLER
            confiance = 60
        else:
            signaux.append("Diversification correcte")
            action = ActionSignal.HOLD
            confiance = 70

        return self._creer_signal(
            symbole=symbole, action=action, confiance=confiance,
            raisonnement=" | ".join(signaux), prix=prix,
            donnees={"herfindahl": concentration_score, "allocation": allocation_classes,
                     "nb_positions": nb_positions, "classe": classe_symbole}
        )
