import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from agents.base_agent import BaseAgent
from models.signal import Signal, ActionSignal
from utils.indicators import Indicateurs
from typing import Dict
import numpy as np


class AgentMarketMaking(BaseAgent):
    @property
    def id(self): return "MM-031"
    @property
    def nom(self): return "Market Maker"
    @property
    def groupe(self): return "execution"
    @property
    def description(self): return "Market making : spread bid/ask, fourniture de liquidité, capture du spread"
    @property
    def _system_prompt(self):
        return """Tu es le market maker de l'agence, expert en fourniture de liquidité.
Tu places des ordres des deux côtés du marché pour capturer le spread bid/ask.
Tu ajustes le spread en fonction de la volatilité et du risque d'inventaire.
Sur volatilité élevée, tu élargis le spread pour te protéger. Tu communiques en français."""

    def _analyser(self, symbole: str, donnees: Dict, contexte: Dict) -> Signal:
        closes = donnees.get("closes", [])
        highs = donnees.get("highs", closes)
        lows = donnees.get("lows", closes)
        volumes = donnees.get("volumes", [1e6] * len(closes))
        if len(closes) < 10:
            return self._signal_neutre(symbole, "Données insuffisantes")

        prix = closes[-1]
        atr = Indicateurs.atr(highs, lows, closes, 14)
        atr_val = next((v for v in reversed(atr) if v is not None), prix * 0.002)
        vol_hist = Indicateurs.volatilite_historique(closes, 20)
        vol_moy_vol = np.mean(volumes[-20:]) if len(volumes) >= 20 else 1e6

        spread_base = atr_val * 0.2 / prix
        ajustement_vol = vol_hist / 20
        spread_final = spread_base * ajustement_vol
        spread_pct = spread_final * 100

        bid = prix * (1 - spread_final / 2)
        ask = prix * (1 + spread_final / 2)
        profit_spread = prix * spread_final
        trades_jour_estimes = vol_moy_vol / (prix * 100) if prix > 0 else 0
        pnl_mm_jour = profit_spread * trades_jour_estimes * 0.3

        est_profitable = spread_pct > 0.05 and vol_hist < 50

        signaux = [
            f"Prix mid: {prix:.4f}",
            f"Bid: {bid:.4f} | Ask: {ask:.4f}",
            f"Spread: {spread_pct:.3f}%",
            f"Volatilité: {vol_hist:.1f}% (ajustement x{ajustement_vol:.2f})",
            f"P&L MM estimé/jour: {pnl_mm_jour:.0f}€",
        ]

        if vol_hist > 60:
            signaux.append("Vol trop haute → market making suspendu")
            return self._signal_neutre(symbole, f"Vol trop haute pour MM: {vol_hist:.1f}%")

        if est_profitable:
            action = ActionSignal.ACHAT
            confiance = min(72, 50 + (spread_pct - 0.05) * 100)
            signaux.append("MM profitable → bid/ask placés")
        else:
            action = ActionSignal.SURVEILLER
            confiance = 45
            signaux.append("Spread insuffisant → MM peu rentable")

        return self._creer_signal(
            symbole=symbole, action=action, confiance=confiance,
            raisonnement=" | ".join(signaux), prix=prix, sl=bid * 0.98, tp=ask * 1.02,
            taille=0.02,
            donnees={"bid": bid, "ask": ask, "spread_pct": spread_pct,
                     "pnl_estime": pnl_mm_jour, "vol": vol_hist}
        )
