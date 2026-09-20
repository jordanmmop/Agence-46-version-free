import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from agents.base_agent import BaseAgent
from models.signal import Signal, ActionSignal
from utils.indicators import Indicateurs
from typing import Dict


class AgentScalping(BaseAgent):
    @property
    def id(self): return "SC-011"
    @property
    def nom(self): return "Trader Scalping"
    @property
    def groupe(self): return "strategies"
    @property
    def description(self): return "Scalping : micro-mouvements, spread capture, entrées/sorties rapides"
    @property
    def _system_prompt(self):
        return """Tu es un trader scalper expert, spécialisé dans les micro-mouvements de marché.
Tu captures de petits profits sur des dizaines de trades par jour.
Tu analyses le carnet d'ordres, les micro-tendances 1-5min, le spread bid/ask.
Tu requiers haute liquidité et faible volatilité intraday. Tu communiques en français."""

    def _analyser(self, symbole: str, donnees: Dict, contexte: Dict) -> Signal:
        closes = donnees.get("closes", [])
        highs = donnees.get("highs", closes)
        lows = donnees.get("lows", closes)
        volumes = donnees.get("volumes", [1.0] * len(closes))
        if len(closes) < 20:
            return self._signal_neutre(symbole, "Données insuffisantes pour scalping")

        prix = closes[-1]
        ema5 = Indicateurs.ema(closes, 5)
        ema8 = Indicateurs.ema(closes, 8)
        ema13 = Indicateurs.ema(closes, 13)
        atr = Indicateurs.atr(highs, lows, closes, 14)
        rsi = Indicateurs.rsi(closes, 7)
        vwap = Indicateurs.vwap(highs, lows, closes, volumes)

        e5 = next((v for v in reversed(ema5) if v is not None), prix)
        e8 = next((v for v in reversed(ema8) if v is not None), prix)
        e13 = next((v for v in reversed(ema13) if v is not None), prix)
        atr_val = next((v for v in reversed(atr) if v is not None), prix * 0.005)
        rsi_val = next((v for v in reversed(rsi) if v is not None), 50.0)
        vwap_val = vwap[-1] if vwap else prix
        spread_estime = atr_val * 0.1

        if atr_val / prix > 0.05:
            return self._signal_neutre(symbole, f"Volatilité trop haute pour scalping: {atr_val/prix*100:.1f}%")

        score = 0
        signaux = [f"VWAP: {vwap_val:.4f}", f"ATR: {atr_val:.4f}", f"Spread est.: {spread_estime:.4f}"]

        if e5 > e8 > e13 and prix > vwap_val:
            score = 75
            signaux.append("EMAs alignées haussières + prix > VWAP")
            action = ActionSignal.ACHAT
            sl = prix - atr_val * 0.5
            tp = prix + atr_val * 1.0
        elif e5 < e8 < e13 and prix < vwap_val:
            score = 75
            signaux.append("EMAs alignées baissières + prix < VWAP")
            action = ActionSignal.VENTE
            sl = prix + atr_val * 0.5
            tp = prix - atr_val * 1.0
        elif e5 > e8 and prix > vwap_val and rsi_val < 60:
            score = 55
            action = ActionSignal.ACHAT
            signaux.append("Setup scalp long modéré")
            sl = prix - atr_val * 0.4
            tp = prix + atr_val * 0.8
        elif e5 < e8 and prix < vwap_val and rsi_val > 40:
            score = 55
            action = ActionSignal.VENTE
            signaux.append("Setup scalp short modéré")
            sl = prix + atr_val * 0.4
            tp = prix - atr_val * 0.8
        else:
            return self._signal_neutre(symbole, "Pas de setup scalp clair")

        return self._creer_signal(
            symbole=symbole, action=action, confiance=score,
            raisonnement=" | ".join(signaux), prix=prix, sl=sl, tp=tp,
            taille=0.03, donnees={"ema5": e5, "ema8": e8, "vwap": vwap_val, "atr": atr_val}
        )
