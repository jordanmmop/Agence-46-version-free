import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from agents.base_agent import BaseAgent
from models.signal import Signal, ActionSignal
from typing import Dict


class AgentSuiviPnL(BaseAgent):
    @property
    def id(self): return "PL-043"
    @property
    def nom(self): return "Tracker P&L"
    @property
    def groupe(self): return "reporting"
    @property
    def description(self): return "Suivi P&L temps réel : latent, réalisé, attribution par position et par agent"
    @property
    def _system_prompt(self):
        return """Tu es le tracker P&L temps réel de l'agence.
Tu calcules et surveilles le P&L réalisé et latent pour chaque position et en agrégé.
Tu alertes sur les positions qui dépassent les seuils de perte et identifies les meilleures sources d'alpha.
Tu communiques en français avec précision comptable."""

    def _analyser(self, symbole: str, donnees: Dict, contexte: Dict) -> Signal:
        portfolio = donnees.get("portfolio", {})
        positions = portfolio.get("positions", {})
        trades = donnees.get("trades", [])
        closes = donnees.get("closes", [])
        prix_actuel = closes[-1] if closes else 0

        pnl_realise = sum(t.get("pnl", 0) for t in trades if t.get("statut") == "CLOSED")
        pnl_commissions = sum(t.get("commission", 0) for t in trades)

        position_actuelle = positions.get(symbole, {})
        pnl_latent = position_actuelle.get("pnl_latent", 0)
        prix_moyen = position_actuelle.get("prix_moyen", prix_actuel)
        quantite = position_actuelle.get("quantite", 0)

        pnl_jour_estime = 0
        if closes and len(closes) > 1:
            variation_jour = closes[-1] - closes[-2]
            pnl_jour_estime = variation_jour * quantite if quantite else 0

        objectif_mensuel = portfolio.get("capital_initial", 100000) * 0.05
        pnl_total = pnl_realise + pnl_latent
        progression_objectif = (pnl_total / objectif_mensuel * 100) if objectif_mensuel > 0 else 0

        attributions = self._attribution_pnl(trades)

        signaux = [
            f"P&L réalisé: {pnl_realise:+,.2f}€",
            f"P&L latent {symbole}: {pnl_latent:+,.2f}€ (qty: {quantite:.4f} @ {prix_moyen:.4f})",
            f"P&L journée: {pnl_jour_estime:+,.2f}€",
            f"P&L total: {pnl_total:+,.2f}€",
            f"Commissions payées: {pnl_commissions:.2f}€",
            f"Objectif mensuel: {objectif_mensuel:,.0f}€ | Progression: {progression_objectif:.1f}%",
        ]
        if attributions:
            top3 = sorted(attributions.items(), key=lambda x: x[1], reverse=True)[:3]
            signaux.append(f"Top agents P&L: {', '.join(f'{k}:{v:+.0f}€' for k,v in top3)}")

        if pnl_latent < -prix_moyen * quantite * 0.05 and quantite > 0:
            action = ActionSignal.ALERTE
            confiance = 80
            signaux.append(f"ALERTE: Perte latente importante sur {symbole}")
        elif pnl_latent > prix_moyen * quantite * 0.10 and quantite > 0:
            action = ActionSignal.SURVEILLER
            confiance = 70
            signaux.append("Position très profitable → considérer prise partielle")
        else:
            action = ActionSignal.HOLD
            confiance = 75

        return self._creer_signal(
            symbole=symbole, action=action, confiance=confiance,
            raisonnement=" | ".join(signaux), prix=prix_actuel if prix_actuel > 0 else None,
            donnees={"pnl_realise": pnl_realise, "pnl_latent": pnl_latent,
                     "pnl_total": pnl_total, "commissions": pnl_commissions,
                     "progression_objectif": progression_objectif}
        )

    @staticmethod
    def _attribution_pnl(trades: list) -> Dict:
        attribution = {}
        for trade in trades:
            agent = trade.get("agent_source", "inconnu")
            pnl = trade.get("pnl", 0)
            attribution[agent] = attribution.get(agent, 0) + pnl
        return attribution
