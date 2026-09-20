import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from agents.base_agent import BaseAgent
from models.signal import Signal, ActionSignal
from typing import Dict


class AgentReconciliation(BaseAgent):
    @property
    def id(self): return "RE-035"
    @property
    def nom(self): return "Agent Réconciliation"
    @property
    def groupe(self): return "execution"
    @property
    def description(self): return "Réconciliation comptable : vérification des trades, détection d'anomalies"
    @property
    def _system_prompt(self):
        return """Tu es le responsable de la réconciliation des trades et de la comptabilité.
Tu vérifies que chaque trade enregistré correspond aux données du broker.
Tu détectes les anomalies, doublons, et erreurs de comptabilisation.
Zéro erreur comptable est ton standard. Tu communiques en français."""

    def _analyser(self, symbole: str, donnees: Dict, contexte: Dict) -> Signal:
        trades_systeme = donnees.get("trades", [])
        trades_broker = donnees.get("trades_broker", [])
        prix = self._dernier_prix(donnees)
        portfolio = donnees.get("portfolio", {})

        pnl_calcule = sum(t.get("pnl", 0) for t in trades_systeme)
        pnl_portfolio = portfolio.get("pnl_realise", pnl_calcule)
        ecart_pnl = abs(pnl_calcule - pnl_portfolio)

        trades_manquants = []
        doublons = []
        ids_vus = set()
        for trade in trades_systeme:
            tid = trade.get("id")
            if tid in ids_vus:
                doublons.append(tid)
            ids_vus.add(tid)
            if trades_broker and tid not in [b.get("id") for b in trades_broker]:
                trades_manquants.append(tid)

        anomalies = []
        for trade in trades_systeme:
            if trade.get("prix_entree", 0) <= 0:
                anomalies.append(f"Prix invalide: {trade.get('id')}")
            if abs(trade.get("quantite", 0)) > 1e7:
                anomalies.append(f"Quantité anormale: {trade.get('id')}")

        score_reconciliation = 100
        if ecart_pnl > 1:
            score_reconciliation -= min(40, ecart_pnl / pnl_portfolio * 100 if pnl_portfolio != 0 else 20)
        if doublons:
            score_reconciliation -= len(doublons) * 15
        if anomalies:
            score_reconciliation -= len(anomalies) * 10

        signaux = [
            f"Trades système: {len(trades_systeme)} | Broker: {len(trades_broker)}",
            f"P&L calculé: {pnl_calcule:,.2f}€ | P&L portfolio: {pnl_portfolio:,.2f}€",
            f"Écart P&L: {ecart_pnl:,.2f}€",
            f"Doublons: {len(doublons)} | Manquants: {len(trades_manquants)}",
            f"Anomalies: {len(anomalies)}",
            f"Score réconciliation: {score_reconciliation:.0f}/100",
        ]

        if score_reconciliation < 60 or doublons or (ecart_pnl > 100 and pnl_portfolio != 0):
            action = ActionSignal.ALERTE
            confiance = 92
            signaux.append("ALERTE RÉCONCILIATION: Erreurs détectées!")
        elif score_reconciliation < 80:
            action = ActionSignal.SURVEILLER
            confiance = 70
            signaux.append("Réconciliation partielle → vérification requise")
        else:
            action = ActionSignal.HOLD
            confiance = 85
            signaux.append("Réconciliation OK")

        return self._creer_signal(
            symbole=symbole, action=action, confiance=confiance,
            raisonnement=" | ".join(signaux), prix=prix,
            donnees={"score": score_reconciliation, "ecart_pnl": ecart_pnl,
                     "doublons": doublons, "anomalies": anomalies}
        )
