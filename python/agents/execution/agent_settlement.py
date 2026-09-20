import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from agents.base_agent import BaseAgent
from models.signal import Signal, ActionSignal
from typing import Dict
from datetime import datetime, timedelta


class AgentSettlement(BaseAgent):
    @property
    def id(self): return "SE-034"
    @property
    def nom(self): return "Agent Règlement"
    @property
    def groupe(self): return "execution"
    @property
    def description(self): return "Règlement et livraison des transactions : T+1, T+2, cash management"
    @property
    def _system_prompt(self):
        return """Tu es le responsable du règlement-livraison des transactions financières.
Tu gères les cycles de règlement (T+1 pour actions US, T+2 pour Europe, quasi-instant pour crypto).
Tu t'assures que les fonds et titres sont disponibles pour le règlement.
Tu communiques en français."""

    CYCLES_REGLEMENT = {
        "default": 2,
        "crypto": 0,
        "BTC-USD": 0, "ETH-USD": 0, "BNB-USD": 0,
        "AAPL": 1, "MSFT": 1, "GOOGL": 1, "NVDA": 1, "AMZN": 1, "TSLA": 1,
        "SPY": 1, "QQQ": 1,
    }

    def _analyser(self, symbole: str, donnees: Dict, contexte: Dict) -> Signal:
        trades = donnees.get("trades", [])
        portfolio = donnees.get("portfolio", {})
        prix = self._dernier_prix(donnees)

        cycle = self.CYCLES_REGLEMENT.get(symbole, self.CYCLES_REGLEMENT["default"])
        date_reglement = datetime.now() + timedelta(days=cycle)
        capital_dispo = portfolio.get("capital_disponible", 0)

        trades_en_attente = [t for t in trades if t.get("statut") == "PENDING"]
        valeur_en_attente = sum(t.get("quantite", 0) * t.get("prix_entree", 0) for t in trades_en_attente)
        suffisant = capital_dispo >= valeur_en_attente

        signaux = [
            f"Cycle règlement {symbole}: T+{cycle}",
            f"Date règlement: {date_reglement.strftime('%Y-%m-%d')}",
            f"Trades en attente règlement: {len(trades_en_attente)}",
            f"Valeur en attente: {valeur_en_attente:,.0f}€",
            f"Capital disponible: {capital_dispo:,.0f}€",
            f"Couverture: {'OUI' if suffisant else 'INSUFFISANTE'}",
        ]

        if not suffisant and valeur_en_attente > 0:
            action = ActionSignal.ALERTE
            confiance = 90
            signaux.append(f"ALERTE RÈGLEMENT: Fonds insuffisants! Besoin: {valeur_en_attente-capital_dispo:,.0f}€ supplémentaires")
        elif len(trades_en_attente) > 5:
            action = ActionSignal.SURVEILLER
            confiance = 65
            signaux.append("File de règlement chargée → surveillance requise")
        else:
            action = ActionSignal.HOLD
            confiance = 80
            signaux.append("Règlement OK")

        return self._creer_signal(
            symbole=symbole, action=action, confiance=confiance,
            raisonnement=" | ".join(signaux), prix=prix,
            donnees={"cycle_t": cycle, "date_reglement": date_reglement.isoformat(),
                     "trades_attente": len(trades_en_attente), "couverture_ok": suffisant}
        )
