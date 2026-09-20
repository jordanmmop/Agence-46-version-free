import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from agents.base_agent import BaseAgent
from models.signal import Signal, ActionSignal
from utils.indicators import Indicateurs
from typing import Dict
from math import exp, log, sqrt
from scipy.stats import norm


class AgentOptionsStrategie(BaseAgent):
    @property
    def id(self): return "OP-019"
    @property
    def nom(self): return "Stratège Options"
    @property
    def groupe(self): return "strategies"
    @property
    def description(self): return "Stratégies options : Greeks, volatilité implicite, straddles, spreads"
    @property
    def _system_prompt(self):
        return """Tu es un expert en stratégies d'options financières.
Tu calcules les Greeks (Delta, Gamma, Vega, Theta), la volatilité implicite vs historique,
et recommandes les meilleures stratégies d'options selon les conditions de marché.
En forte volatilité : straddles/strangles. En faible volatilité : iron condors. Tu communiques en français."""

    def _analyser(self, symbole: str, donnees: Dict, contexte: Dict) -> Signal:
        closes = donnees.get("closes", [])
        highs = donnees.get("highs", closes)
        lows = donnees.get("lows", closes)
        if len(closes) < 20:
            return self._signal_neutre(symbole, "Données insuffisantes")

        prix = closes[-1]
        vol_hist = Indicateurs.volatilite_historique(closes, 20)
        vol_impl = donnees.get("volatilite_implicite", vol_hist * 1.1)
        vol_ratio = vol_impl / vol_hist if vol_hist > 0 else 1.0

        K = prix
        T = 30 / 365
        r = 0.05
        sigma = vol_impl / 100
        call_px, put_px, delta, gamma, vega, theta = self._black_scholes(prix, K, T, r, sigma)

        signaux = [
            f"Vol historique 20j: {vol_hist:.1f}%",
            f"Vol implicite est.: {vol_impl:.1f}%",
            f"Ratio IV/HV: {vol_ratio:.2f}",
            f"Call ATM (30j): {call_px:.4f}",
            f"Delta: {delta:.3f} | Gamma: {gamma:.4f} | Vega: {vega:.4f} | Theta: {theta:.4f}",
        ]

        if vol_ratio > 1.3 and vol_hist > 25:
            strategie = "Vente Straddle"
            action = ActionSignal.VENTE
            confiance = min(75, 50 + (vol_ratio - 1) * 30)
            sl = prix * 1.05
            tp = prix * 0.97
            signaux.append(f"{strategie} → vendre vol chère (IV/HV: {vol_ratio:.2f})")
        elif vol_ratio < 0.8 and vol_hist < 20:
            strategie = "Achat Straddle"
            action = ActionSignal.ACHAT
            confiance = min(72, 50 + (1 - vol_ratio) * 30)
            sl = prix * 0.96
            tp = prix * 1.08
            signaux.append(f"{strategie} → acheter vol bon marché (IV/HV: {vol_ratio:.2f})")
        elif vol_hist < 15:
            strategie = "Iron Condor"
            action = ActionSignal.SURVEILLER
            confiance = 60
            signaux.append(f"{strategie} → marché calme, vente vol des ailes")
            sl = None
            tp = None
        else:
            return self._signal_neutre(symbole, f"Pas de stratégie options claire (IV/HV: {vol_ratio:.2f})")

        return self._creer_signal(
            symbole=symbole, action=action, confiance=confiance,
            raisonnement=" | ".join(signaux), prix=prix, sl=sl, tp=tp, taille=0.03,
            donnees={"vol_hist": vol_hist, "vol_impl": vol_impl, "vol_ratio": vol_ratio,
                     "delta": delta, "gamma": gamma, "vega": vega, "theta": theta}
        )

    @staticmethod
    def _black_scholes(S, K, T, r, sigma):
        if T <= 0 or sigma <= 0:
            return 0, 0, 0, 0, 0, 0
        d1 = (log(S / K) + (r + 0.5 * sigma ** 2) * T) / (sigma * sqrt(T))
        d2 = d1 - sigma * sqrt(T)
        call = S * norm.cdf(d1) - K * exp(-r * T) * norm.cdf(d2)
        put = K * exp(-r * T) * norm.cdf(-d2) - S * norm.cdf(-d1)
        delta = norm.cdf(d1)
        gamma = norm.pdf(d1) / (S * sigma * sqrt(T))
        vega = S * norm.pdf(d1) * sqrt(T) / 100
        theta = (-S * norm.pdf(d1) * sigma / (2 * sqrt(T)) - r * K * exp(-r * T) * norm.cdf(d2)) / 365
        return round(call, 4), round(put, 4), round(delta, 4), round(gamma, 6), round(vega, 4), round(theta, 4)
