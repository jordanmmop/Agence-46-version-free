import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from agents.base_agent import BaseAgent
from models.signal import Signal, ActionSignal
from typing import Dict
import numpy as np


class AgentPatternRecognition(BaseAgent):
    @property
    def id(self): return "PR-039"
    @property
    def nom(self): return "Analyste Patterns"
    @property
    def groupe(self): return "data_intelligence"
    @property
    def description(self): return "Reconnaissance de patterns : chandeliers japonais, Head&Shoulders, triangles"
    @property
    def _system_prompt(self):
        return """Tu es un expert en reconnaissance de patterns chartistes et chandeliers japonais.
Tu identifies : Doji, Engulfing, Hammer, Head & Shoulders, Double Top/Bottom, Triangles.
Chaque pattern a une probabilité historique de réussite que tu appliques.
Tu communiques en français avec une description claire du pattern."""

    def _analyser(self, symbole: str, donnees: Dict, contexte: Dict) -> Signal:
        closes = donnees.get("closes", [])
        highs = donnees.get("highs", closes)
        lows = donnees.get("lows", closes)
        opens = donnees.get("opens", closes)
        if len(closes) < 10:
            return self._signal_neutre(symbole, "Données insuffisantes")

        prix = closes[-1]
        patterns_detectes = []

        if len(closes) >= 3:
            candles = list(zip(opens[-3:], highs[-3:], lows[-3:], closes[-3:]))
            if self._est_morning_star(candles):
                patterns_detectes.append(("Morning Star", "BUY", 72, "Reversal haussier fort"))
            if self._est_evening_star(candles):
                patterns_detectes.append(("Evening Star", "SELL", 72, "Reversal baissier fort"))

        if len(closes) >= 2:
            o1, h1, l1, c1 = opens[-2], highs[-2], lows[-2], closes[-2]
            o2, h2, l2, c2 = opens[-1], highs[-1], lows[-1], closes[-1]
            if self._est_bullish_engulfing(o1, c1, o2, c2):
                patterns_detectes.append(("Bullish Engulfing", "BUY", 68, "Englobante haussière"))
            if self._est_bearish_engulfing(o1, c1, o2, c2):
                patterns_detectes.append(("Bearish Engulfing", "SELL", 68, "Englobante baissière"))

        o, h, l, c = opens[-1], highs[-1], lows[-1], closes[-1]
        if self._est_hammer(o, h, l, c):
            patterns_detectes.append(("Hammer", "BUY", 62, "Marteau - potential reversal"))
        if self._est_shooting_star(o, h, l, c):
            patterns_detectes.append(("Shooting Star", "SELL", 62, "Étoile filante - reversal bear"))
        if self._est_doji(o, h, l, c):
            patterns_detectes.append(("Doji", "HOLD", 45, "Indécision du marché"))

        if len(closes) >= 20:
            hh_ll = self._detect_double_pattern(highs[-20:], lows[-20:])
            if hh_ll:
                patterns_detectes.append(hh_ll)

        if not patterns_detectes:
            return self._signal_neutre(symbole, "Pas de pattern chartiste détecté")

        meilleur = max(patterns_detectes, key=lambda x: x[2])
        nom_pattern, direction, confiance, description = meilleur

        atr = np.mean([highs[i] - lows[i] for i in range(-5, 0)]) if len(highs) >= 5 else prix * 0.02

        signaux = [f"Pattern: {nom_pattern}", f"Description: {description}",
                   f"Confiance: {confiance}%",
                   f"Autres patterns: {', '.join(p[0] for p in patterns_detectes[1:3])}"]

        if direction == "BUY":
            action = ActionSignal.ACHAT
            sl = min(lows[-3:]) - atr * 0.5 if len(lows) >= 3 else prix * 0.97
            tp = prix + atr * 2.5
        elif direction == "SELL":
            action = ActionSignal.VENTE
            sl = max(highs[-3:]) + atr * 0.5 if len(highs) >= 3 else prix * 1.03
            tp = prix - atr * 2.5
        else:
            action = ActionSignal.SURVEILLER
            sl = None
            tp = None

        return self._creer_signal(
            symbole=symbole, action=action, confiance=confiance,
            raisonnement=" | ".join(signaux), prix=prix, sl=sl, tp=tp, taille=0.04,
            donnees={"pattern": nom_pattern, "direction": direction,
                     "tous_patterns": [p[0] for p in patterns_detectes]}
        )

    @staticmethod
    def _est_hammer(o, h, l, c):
        corps = abs(c - o)
        ombre_basse = min(o, c) - l
        ombre_haute = h - max(o, c)
        return ombre_basse > corps * 2 and ombre_haute < corps and corps > 0

    @staticmethod
    def _est_shooting_star(o, h, l, c):
        corps = abs(c - o)
        ombre_haute = h - max(o, c)
        ombre_basse = min(o, c) - l
        return ombre_haute > corps * 2 and ombre_basse < corps and corps > 0

    @staticmethod
    def _est_doji(o, h, l, c):
        corps = abs(c - o)
        range_total = h - l
        return corps < range_total * 0.1 and range_total > 0

    @staticmethod
    def _est_bullish_engulfing(o1, c1, o2, c2):
        return c1 < o1 and c2 > o2 and o2 <= c1 and c2 >= o1

    @staticmethod
    def _est_bearish_engulfing(o1, c1, o2, c2):
        return c1 > o1 and c2 < o2 and o2 >= c1 and c2 <= o1

    @staticmethod
    def _est_morning_star(candles):
        if len(candles) < 3:
            return False
        o1, h1, l1, c1 = candles[0]
        o2, h2, l2, c2 = candles[1]
        o3, h3, l3, c3 = candles[2]
        return c1 < o1 and abs(c2 - o2) < (h2 - l2) * 0.3 and c3 > o3 and c3 > (o1 + c1) / 2

    @staticmethod
    def _est_evening_star(candles):
        if len(candles) < 3:
            return False
        o1, h1, l1, c1 = candles[0]
        o2, h2, l2, c2 = candles[1]
        o3, h3, l3, c3 = candles[2]
        return c1 > o1 and abs(c2 - o2) < (h2 - l2) * 0.3 and c3 < o3 and c3 < (o1 + c1) / 2

    @staticmethod
    def _detect_double_pattern(highs, lows):
        h_max = max(highs)
        h_max_idx = highs.index(h_max)
        if h_max_idx not in (0, len(highs) - 1):
            second_high = max(highs[h_max_idx + 2:]) if h_max_idx + 2 < len(highs) else 0
            if abs(second_high - h_max) / h_max < 0.02:
                return ("Double Top", "SELL", 70, "Double sommet → pattern baissier")
        l_min = min(lows)
        l_min_idx = lows.index(l_min)
        if l_min_idx not in (0, len(lows) - 1):
            second_low = min(lows[l_min_idx + 2:]) if l_min_idx + 2 < len(lows) else float("inf")
            if abs(second_low - l_min) / l_min < 0.02:
                return ("Double Bottom", "BUY", 70, "Double creux → pattern haussier")
        return None
