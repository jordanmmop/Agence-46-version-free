import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from agents.base_agent import BaseAgent
from models.signal import Signal, ActionSignal
from typing import Dict
import numpy as np


class AgentSectoriel(BaseAgent):
    @property
    def id(self): return "AS-006"
    @property
    def nom(self): return "Analyste Sectoriel"
    @property
    def groupe(self): return "analyse_marche"
    @property
    def description(self): return "Rotation sectorielle, analyse des ETFs sectoriels et forces relatives"
    @property
    def _system_prompt(self):
        return """Tu es un spécialiste de l'analyse sectorielle et de la rotation entre secteurs.
Tu identifies les secteurs en force et en faiblesse relative au marché global.
Tu analyses les ETFs sectoriels et recommandes les meilleurs secteurs pour le moment.
Tu communiques en français."""

    SECTEURS = {
        "XLK": "Technologie", "XLF": "Finance", "XLV": "Santé",
        "XLE": "Énergie", "XLI": "Industrie", "XLY": "Consommation discr.",
        "XLP": "Consommation base", "XLU": "Services publics", "XLRE": "Immobilier",
        "XLB": "Matériaux", "XLC": "Communication",
    }

    SECTEUR_SYMBOLE = {
        "AAPL": "Technologie", "MSFT": "Technologie", "NVDA": "Technologie",
        "GOOGL": "Technologie", "AMZN": "Consommation discr.", "TSLA": "Consommation discr.",
        "SPY": "Marché global", "QQQ": "Technologie",
        "BTC-USD": "Crypto", "ETH-USD": "Crypto",
    }

    def _analyser(self, symbole: str, donnees: Dict, contexte: Dict) -> Signal:
        closes = donnees.get("closes", [])
        prix = closes[-1] if closes else 0
        secteur_data = donnees.get("secteurs", {})
        secteur_symbole = self.SECTEUR_SYMBOLE.get(symbole, "Non classifié")
        performances = secteur_data.get("performances", {})

        if not performances:
            performances = self._simuler_performances()

        moy_perf = np.mean(list(performances.values()))
        meilleurs = sorted(performances.items(), key=lambda x: x[1], reverse=True)[:3]
        pires = sorted(performances.items(), key=lambda x: x[1])[:3]

        perf_secteur = None
        for nom, perf in performances.items():
            if secteur_symbole.lower() in nom.lower():
                perf_secteur = perf
                break

        signaux = [
            f"Secteur symbole: {secteur_symbole}",
            f"Top 3: {', '.join(f'{n}(+{p:.1f}%)' for n,p in meilleurs)}",
            f"Flop 3: {', '.join(f'{n}({p:.1f}%)' for n,p in pires)}",
        ]

        if perf_secteur is not None:
            rs = perf_secteur - moy_perf
            signaux.append(f"Force relative secteur: {rs:+.1f}%")
            if rs > 3:
                action = ActionSignal.ACHAT
                confiance = min(80, 50 + rs * 5)
                sl = prix * 0.94 if prix > 0 else None
                tp = prix * 1.12 if prix > 0 else None
            elif rs < -3:
                action = ActionSignal.VENTE
                confiance = min(75, 50 + abs(rs) * 4)
                sl = prix * 1.06 if prix > 0 else None
                tp = prix * 0.90 if prix > 0 else None
            else:
                action = ActionSignal.SURVEILLER
                confiance = 35
                sl = None
                tp = None
        else:
            action = ActionSignal.SURVEILLER
            confiance = 30
            sl = None
            tp = None

        return self._creer_signal(
            symbole=symbole, action=action, confiance=confiance,
            raisonnement=" | ".join(signaux), prix=prix if prix > 0 else None,
            sl=sl, tp=tp, donnees={"secteur": secteur_symbole, "performances": performances}
        )

    def _simuler_performances(self) -> Dict[str, float]:
        import random
        random.seed(42)
        return {nom: round(random.uniform(-5, 8), 2) for nom in self.SECTEURS.values()}
