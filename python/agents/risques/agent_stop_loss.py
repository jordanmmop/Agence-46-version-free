import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from agents.base_agent import BaseAgent
from models.signal import Signal, ActionSignal
from utils.indicators import Indicateurs
from typing import Dict


class AgentStopLoss(BaseAgent):
    @property
    def id(self): return "SL-022"
    @property
    def nom(self): return "Gestionnaire Stop Loss"
    @property
    def groupe(self): return "risques"
    @property
    def description(self): return "Gestion des stop loss : niveaux optimaux, trailing stops, stops adaptatifs"
    @property
    def _system_prompt(self):
        return """Tu es l'expert en gestion des stop loss et protection du capital.
Tu calcules les niveaux de stop optimaux basés sur l'ATR, les supports et la volatilité.
Tu gères les trailing stops qui suivent le prix favorablement.
Ton principe : le stop est le gardien du capital. Tu communiques en français."""

    def _analyser(self, symbole: str, donnees: Dict, contexte: Dict) -> Signal:
        closes = donnees.get("closes", [])
        highs = donnees.get("highs", closes)
        lows = donnees.get("lows", closes)
        position = donnees.get("position", {})
        if len(closes) < 15:
            return self._signal_neutre(symbole, "Données insuffisantes")

        prix = closes[-1]
        atr = Indicateurs.atr(highs, lows, closes, 14)
        atr_val = next((v for v in reversed(atr) if v is not None), prix * 0.02)
        supports, resistances = Indicateurs.support_resistance(highs, lows, 5)
        vol_hist = Indicateurs.volatilite_historique(closes, 20)

        stop_atr_long = prix - atr_val * 2
        stop_atr_short = prix + atr_val * 2
        stop_pct_long = prix * 0.97
        stop_pct_short = prix * 1.03
        support_proche = max([s for s in supports if s < prix], default=prix * 0.95)
        trailing_step = atr_val * 0.5

        stop_long_final = max(stop_atr_long, stop_pct_long, support_proche * 0.998)
        stop_short_final = min(stop_atr_short, stop_pct_short)

        risque_long = (prix - stop_long_final) / prix * 100
        risque_short = (stop_short_final - prix) / prix * 100

        prix_entree = position.get("prix_entree")
        direction = position.get("direction", "LONG")
        if prix_entree:
            pnl_pct = (prix - prix_entree) / prix_entree * 100
            if direction == "LONG" and pnl_pct > 3:
                stop_trailing = prix_entree * 1.005
                signaux = [f"Trailing stop activé: {stop_trailing:.4f} (PnL: +{pnl_pct:.1f}%)"]
            elif direction == "SHORT" and pnl_pct < -3:
                stop_trailing = prix_entree * 0.995
                signaux = [f"Trailing stop activé short: {stop_trailing:.4f}"]
            else:
                stop_trailing = None
                signaux = []
        else:
            stop_trailing = None
            signaux = []

        signaux += [
            f"Stop LONG ATR-2: {stop_long_final:.4f} (risque: {risque_long:.2f}%)",
            f"Stop SHORT ATR-2: {stop_short_final:.4f} (risque: {risque_short:.2f}%)",
            f"ATR: {atr_val:.4f} | Vol 20j: {vol_hist:.1f}%",
            f"Support le plus proche: {support_proche:.4f}",
            f"Pas trailing: {trailing_step:.4f}",
        ]

        return self._creer_signal(
            symbole=symbole, action=ActionSignal.ALERTE if vol_hist > 50 else ActionSignal.HOLD,
            confiance=70, raisonnement=" | ".join(signaux), prix=prix,
            sl=stop_long_final, tp=None,
            donnees={"stop_long": stop_long_final, "stop_short": stop_short_final,
                     "stop_trailing": stop_trailing, "atr": atr_val, "risque_pct": risque_long}
        )
