import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from agents.base_agent import BaseAgent
from models.signal import Signal, ActionSignal
from typing import Dict
from datetime import datetime, date


class AgentReportingReglementaire(BaseAgent):
    @property
    def id(self): return "RR-045"
    @property
    def nom(self): return "Agent Reporting Réglementaire"
    @property
    def groupe(self): return "reporting"
    @property
    def description(self): return "Reporting AMF/MiFID II : transaction reporting, positions, best execution"
    @property
    def _system_prompt(self):
        return """Tu es le responsable du reporting réglementaire pour l'agence.
Tu génères les rapports MiFID II, les déclarations AMF et les rapports de best execution.
Tu t'assures que tous les trades sont correctement déclarés dans les délais légaux.
Tolérance zéro pour les manquements réglementaires. Tu communiques en français."""

    DELAI_RAPPORT_MIFID = 1
    SEUIL_DECLARATION_POSITION = 500000

    def _analyser(self, symbole: str, donnees: Dict, contexte: Dict) -> Signal:
        trades = donnees.get("trades", [])
        portfolio = donnees.get("portfolio", {})
        prix = self._dernier_prix(donnees)
        aujourd_hui = date.today()

        trades_jour = [t for t in trades
                       if t.get("ouvert_le", "")[:10] == aujourd_hui.isoformat()]
        trades_non_declares = [t for t in trades
                                if not t.get("declare_mifid") and t.get("statut") == "CLOSED"]

        valeur_positions = portfolio.get("valeur_positions", 0)
        capital = portfolio.get("capital_initial", 100000)
        exposition_pct = valeur_positions / capital * 100 if capital > 0 else 0

        rapports_requis = []
        if trades_non_declares:
            rapports_requis.append(f"MiFID II T+1: {len(trades_non_declares)} trades à déclarer")
        if valeur_positions > self.SEUIL_DECLARATION_POSITION:
            rapports_requis.append(f"AMF Position: {valeur_positions:,.0f}€ > seuil {self.SEUIL_DECLARATION_POSITION:,}€")

        best_execution = self._verifier_best_execution(trades[-10:] if trades else [])
        rapport_mifid = self._generer_rapport_mifid(trades_jour, portfolio)

        signaux = [
            f"Trades aujourd'hui: {len(trades_jour)}",
            f"Trades à déclarer: {len(trades_non_declares)}",
            f"Valeur positions: {valeur_positions:,.0f}€",
            f"Best execution score: {best_execution:.0f}/100",
            f"Rapports requis: {len(rapports_requis)}",
        ]
        signaux += rapports_requis

        if trades_non_declares or best_execution < 60:
            action = ActionSignal.ALERTE
            confiance = 88
            signaux.append("ACTION REQUISE: Obligations réglementaires en attente")
        elif rapports_requis:
            action = ActionSignal.SURVEILLER
            confiance = 70
        else:
            action = ActionSignal.HOLD
            confiance = 80
            signaux.append("Compliance réglementaire OK")

        return self._creer_signal(
            symbole=symbole, action=action, confiance=confiance,
            raisonnement=" | ".join(signaux), prix=prix,
            donnees={"trades_declares": len(trades_non_declares) == 0,
                     "best_execution": best_execution,
                     "rapport_mifid": rapport_mifid,
                     "date_rapport": datetime.now().isoformat()}
        )

    @staticmethod
    def _verifier_best_execution(trades: list) -> float:
        if not trades:
            return 100.0
        scores = []
        for trade in trades:
            prix_marche = trade.get("prix_marche", trade.get("prix_entree", 0))
            prix_exec = trade.get("prix_entree", 0)
            if prix_marche and prix_exec and prix_marche > 0:
                ecart = abs(prix_exec - prix_marche) / prix_marche * 100
                scores.append(max(0, 100 - ecart * 1000))
        return float(sum(scores) / len(scores)) if scores else 100.0

    @staticmethod
    def _generer_rapport_mifid(trades: list, portfolio: dict) -> dict:
        return {
            "date": date.today().isoformat(),
            "nb_transactions": len(trades),
            "valeur_totale": sum(t.get("quantite", 0) * t.get("prix_entree", 0) for t in trades),
            "capital_reference": portfolio.get("capital_initial", 0),
            "statut": "CONFORME" if len(trades) <= 100 else "VOLUME_ELEVÉ",
        }
