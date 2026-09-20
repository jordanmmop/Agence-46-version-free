import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from agents.base_agent import BaseAgent
from models.signal import Signal, ActionSignal
from typing import Dict
from datetime import datetime


class AgentCommunicationClient(BaseAgent):
    @property
    def id(self): return "CC-044"
    @property
    def nom(self): return "Agent Communication Client"
    @property
    def groupe(self): return "reporting"
    @property
    def description(self): return "Communication avec les clients : rapports périodiques, alertes, notifications"
    @property
    def _system_prompt(self):
        return """Tu es le responsable communication clients de l'agence de trading.
Tu génères des rapports périodiques clairs, des alertes sur les événements importants
et des notifications en temps réel sur les positions et P&L.
Tu traduis le jargon financier en langage compréhensible. Tu communiques en français."""

    def _analyser(self, symbole: str, donnees: Dict, contexte: Dict) -> Signal:
        portfolio = donnees.get("portfolio", {})
        signaux_recents = donnees.get("signaux_recents", [])
        prix = self._dernier_prix(donnees)

        pnl_pct = portfolio.get("pnl_total_pct", 0)
        drawdown = portfolio.get("drawdown_actuel", 0)
        nb_positions = portfolio.get("nb_positions", 0)
        valeur = portfolio.get("valeur_totale", 100000)

        alertes = []
        if abs(drawdown) > 5:
            alertes.append({"type": "URGENT", "message": f"Drawdown important: {drawdown:.1f}%"})
        if pnl_pct > 10:
            alertes.append({"type": "INFO", "message": f"Performance excellente: +{pnl_pct:.1f}%"})
        if pnl_pct < -5:
            alertes.append({"type": "ALERTE", "message": f"Performance négative: {pnl_pct:.1f}%"})

        for sig in signaux_recents[-3:]:
            action = sig.get("action")
            confiance = sig.get("confiance", 0)
            if action in ["BUY", "SELL"] and confiance > 70:
                alertes.append({"type": "SIGNAL", "message": f"Signal fort {action} {symbole} (conf: {confiance:.0f}%)"})

        rapport_resume = self._generer_resume(portfolio, alertes)

        signaux = [
            f"Résumé: {rapport_resume}",
            f"Alertes actives: {len(alertes)}",
            f"Valeur portefeuille: {valeur:,.0f}€ ({pnl_pct:+.2f}%)",
        ]
        for a in alertes[:3]:
            signaux.append(f"[{a['type']}] {a['message']}")

        if any(a["type"] == "URGENT" for a in alertes):
            action = ActionSignal.ALERTE
            confiance = 85
        elif alertes:
            action = ActionSignal.SURVEILLER
            confiance = 60
        else:
            action = ActionSignal.HOLD
            confiance = 70

        return self._creer_signal(
            symbole=symbole, action=action, confiance=confiance,
            raisonnement=" | ".join(signaux), prix=prix,
            donnees={"alertes": alertes, "rapport": rapport_resume,
                     "timestamp": datetime.now().isoformat()}
        )

    @staticmethod
    def _generer_resume(portfolio: dict, alertes: list) -> str:
        pnl = portfolio.get("pnl_total_pct", 0)
        nb = portfolio.get("nb_positions", 0)
        valeur = portfolio.get("valeur_totale", 0)
        statut = "POSITIF" if pnl > 0 else "NÉGATIF"
        return f"Portefeuille {statut} | {nb} positions | {valeur:,.0f}€ ({pnl:+.2f}%) | {len(alertes)} alertes"
