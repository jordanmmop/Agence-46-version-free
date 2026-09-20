import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from agents.base_agent import BaseAgent
from models.signal import Signal, ActionSignal
from utils.indicators import Indicateurs
from typing import Dict


class AgentAlgoTrading(BaseAgent):
    @property
    def id(self): return "AL-020"
    @property
    def nom(self): return "Trader Algorithmique"
    @property
    def groupe(self): return "strategies"
    @property
    def description(self): return "Trading algo : systèmes quantitatifs, backtesting intégré, optimisation continue"
    @property
    def _system_prompt(self):
        return """Tu es un quant trader avec expertise en stratégies algorithmiques.
Tu conçois, backtestes et optimises des stratégies de trading systématiques.
Tu combines plusieurs indicateurs avec des règles précises pour des systèmes sans ambiguïté.
Ton système signature : dual momentum + mean reversion + filtre de tendance. Tu communiques en français."""

    def _analyser(self, symbole: str, donnees: Dict, contexte: Dict) -> Signal:
        closes = donnees.get("closes", [])
        highs = donnees.get("highs", closes)
        lows = donnees.get("lows", closes)
        volumes = donnees.get("volumes", [1e6] * len(closes))
        if len(closes) < 50:
            return self._signal_neutre(symbole, "Données insuffisantes pour algo")

        prix = closes[-1]
        rsi = Indicateurs.rsi(closes, 14)
        ema20 = Indicateurs.ema(closes, 20)
        ema50 = Indicateurs.ema(closes, 50)
        macd_line, signal_line, histo = Indicateurs.macd(closes)
        z_scores = Indicateurs.z_score(closes, 20)
        atr = Indicateurs.atr(highs, lows, closes, 14)
        obv = Indicateurs.obv(closes, volumes)

        rsi_val = next((v for v in reversed(rsi) if v is not None), 50.0)
        e20 = next((v for v in reversed(ema20) if v is not None), prix)
        e50 = next((v for v in reversed(ema50) if v is not None), prix)
        hist_val = next((v for v in reversed(histo) if v is not None), 0.0)
        z_val = next((v for v in reversed(z_scores) if v is not None), 0.0)
        atr_val = next((v for v in reversed(atr) if v is not None), prix * 0.02)
        obv_tendance = (obv[-1] - obv[-10]) / abs(obv[-10]) * 100 if len(obv) >= 10 and obv[-10] != 0 else 0

        score_long = 0
        score_short = 0

        if e20 > e50: score_long += 2
        else: score_short += 2
        if rsi_val > 50: score_long += 1
        elif rsi_val < 50: score_short += 1
        if hist_val > 0: score_long += 2
        elif hist_val < 0: score_short += 2
        if -1 < z_val < 0: score_long += 1
        elif 0 < z_val < 1: score_short += 1
        if obv_tendance > 0: score_long += 2
        elif obv_tendance < 0: score_short += 2
        if rsi_val < 40: score_long += 2
        elif rsi_val > 60: score_short += 2

        total = score_long + score_short
        regime = "long" if score_long > score_short else "short" if score_short > score_long else "neutre"

        signaux = [
            f"Score long/short: {score_long}/{score_short}",
            f"Régime: {regime}",
            f"RSI: {rsi_val:.1f} | Z-score: {z_val:.2f}",
            f"OBV tendance: {obv_tendance:+.1f}%",
            f"EMA20/50: {e20:.2f}/{e50:.2f}",
        ]

        if score_long >= 7 and regime == "long":
            action = ActionSignal.ACHAT
            confiance = min(85, 50 + score_long * 5)
            sl = prix - atr_val * 2
            tp = prix + atr_val * 4
            signaux.append(f"Système algo: BUY ({score_long}/10)")
        elif score_short >= 7 and regime == "short":
            action = ActionSignal.VENTE
            confiance = min(85, 50 + score_short * 5)
            sl = prix + atr_val * 2
            tp = prix - atr_val * 4
            signaux.append(f"Système algo: SELL ({score_short}/10)")
        elif score_long >= 5 or score_short >= 5:
            action = ActionSignal.SURVEILLER
            confiance = 40
            sl = None
            tp = None
            signaux.append("Signal algo modéré - en attente confirmation")
        else:
            return self._signal_neutre(symbole, f"Système algo: neutre ({score_long}/{score_short})")

        return self._creer_signal(
            symbole=symbole, action=action, confiance=confiance,
            raisonnement=" | ".join(signaux), prix=prix, sl=sl, tp=tp, taille=0.05,
            donnees={"score_long": score_long, "score_short": score_short,
                     "rsi": rsi_val, "z_score": z_val, "atr": atr_val}
        )
