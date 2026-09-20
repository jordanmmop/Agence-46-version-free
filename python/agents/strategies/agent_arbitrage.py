import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from agents.base_agent import BaseAgent
from models.signal import Signal, ActionSignal
from typing import Dict


class AgentArbitrage(BaseAgent):
    @property
    def id(self): return "AR-015"
    @property
    def nom(self): return "Trader Arbitragiste"
    @property
    def groupe(self): return "strategies"
    @property
    def description(self): return "Arbitrage : opportunités de prix entre marchés, paires et spread trading"
    @property
    def _system_prompt(self):
        return """Tu es un spécialiste de l'arbitrage et du spread trading.
Tu identifies les divergences de prix pour le même actif sur différents marchés ou actifs corrélés.
Tu calcules les spreads historiques et alertes quand ils s'écartent de la normale.
Tu communiques en français avec des analyses quantitatives précises."""

    PAIRES_ARBITRAGE = {
        ("AAPL", "MSFT"): {"spread_moyen": 0.95, "ecart_type": 0.05},
        ("QQQ", "SPY"): {"spread_moyen": 0.25, "ecart_type": 0.02},
        ("BTC-USD", "ETH-USD"): {"spread_moyen": 18.5, "ecart_type": 2.0},
    }

    def _analyser(self, symbole: str, donnees: Dict, contexte: Dict) -> Signal:
        closes = donnees.get("closes", [])
        prix = closes[-1] if closes else 0
        autres_prix = donnees.get("autres_prix", {})

        opportunites = []
        for (sym1, sym2), params in self.PAIRES_ARBITRAGE.items():
            if symbole not in (sym1, sym2):
                continue
            sym_ref = sym2 if symbole == sym1 else sym1
            prix_ref = autres_prix.get(sym_ref)
            if not prix_ref or prix == 0:
                continue

            ratio_actuel = prix / prix_ref
            z_score = (ratio_actuel - params["spread_moyen"]) / params["ecart_type"]

            if abs(z_score) > 2:
                opportunites.append({
                    "paire": f"{sym1}/{sym2}",
                    "z_score": z_score,
                    "ratio": ratio_actuel,
                    "ratio_moyen": params["spread_moyen"],
                })

        # L'arbitrage exige les prix d'au moins DEUX instruments corrélés.
        # `donnees["autres_prix"]` n'est pas alimenté par l'orchestrateur :
        # l'agent fabriquait alors un spread avec np.random.normal(0, 0.5) et
        # pouvait en tirer un ACHAT/VENTE à 70-80 % de confiance — c'est-à-dire
        # un ORDRE RÉEL dont le volume était dicté par un tirage aléatoire.
        # Sans données réelles, on ne conclut rien.
        if not autres_prix:
            return self._signal_neutre(
                symbole, "Arbitrage impossible : prix comparatifs indisponibles")

        if not opportunites:
            return self._signal_neutre(symbole, "Pas d'opportunité d'arbitrage détectée")

        opp = max(opportunites, key=lambda x: abs(x["z_score"]))
        z = opp["z_score"]
        signaux = [
            f"Paire: {opp['paire']}",
            f"Z-score: {z:.2f}σ",
            f"Ratio actuel: {opp['ratio']:.4f} (moy: {opp['ratio_moyen']:.4f})",
        ]

        if z < -2:
            action = ActionSignal.ACHAT
            confiance = min(80, 50 + abs(z) * 10)
            sl = prix * 0.97
            tp = prix * 1.05
            signaux.append(f"Spread anormalement bas → achat {symbole} convergence")
        elif z > 2:
            action = ActionSignal.VENTE
            confiance = min(80, 50 + abs(z) * 10)
            sl = prix * 1.03
            tp = prix * 0.95
            signaux.append(f"Spread anormalement haut → vente {symbole} convergence")
        else:
            return self._signal_neutre(symbole, f"Z-score insuffisant ({z:.2f})")

        return self._creer_signal(
            symbole=symbole, action=action, confiance=confiance,
            raisonnement=" | ".join(signaux), prix=prix if prix > 0 else None,
            sl=sl if prix > 0 else None, tp=tp if prix > 0 else None,
            donnees={"z_score": z, "paire": opp["paire"], "opportunites": len(opportunites)}
        )
