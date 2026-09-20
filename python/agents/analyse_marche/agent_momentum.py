import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from agents.base_agent import BaseAgent
from models.signal import Signal, ActionSignal
from utils.indicators import Indicateurs
from typing import Dict
import numpy as np


class AgentMomentum(BaseAgent):
    @property
    def id(self): return "MO-010"
    @property
    def nom(self): return "Analyste Momentum"
    @property
    def groupe(self): return "analyse_marche"
    @property
    def description(self): return "Analyse du momentum : ROC, force relative, accélération de tendance"
    @property
    def _system_prompt(self):
        return """Tu es un expert en analyse du momentum des marchés financiers.
Tu mesures la vitesse et l'accélération des tendances avec le Rate of Change (ROC),
la force relative, et les indicateurs de momentum directionnel (ADX, DI+, DI-).
Un momentum fort et croissant indique une tendance saine; un momentum qui s'affaiblit signale un retournement.
Tu communiques en français."""

    def _analyser(self, symbole: str, donnees: Dict, contexte: Dict) -> Signal:
        closes = donnees.get("closes", [])
        highs = donnees.get("highs", closes)
        lows = donnees.get("lows", closes)
        if len(closes) < 20:
            return self._signal_neutre(symbole, "Données insuffisantes")

        prix = closes[-1]
        roc1 = self._roc(closes, 1)
        roc5 = self._roc(closes, 5)
        roc10 = self._roc(closes, 10)
        roc20 = self._roc(closes, 20)

        adx, di_plus, di_moins = self._adx(highs, lows, closes)
        rsi = Indicateurs.rsi(closes, 14)
        rsi_val = next((v for v in reversed(rsi) if v is not None), 50.0)

        acceleration = roc5 - roc10 if roc5 is not None and roc10 is not None else 0
        momentum_global = (
            (roc1 or 0) * 0.15 + (roc5 or 0) * 0.25 +
            (roc10 or 0) * 0.30 + (roc20 or 0) * 0.30
        )

        signaux = [
            # `is not None` : un ROC réel de 0.00% (marché plat) est une donnée
            # valide, pas « N/A ».
            f"ROC 1j: {roc1:.2f}%" if roc1 is not None else "ROC 1j: N/A",
            f"ROC 5j: {roc5:.2f}%" if roc5 is not None else "ROC 5j: N/A",
            f"ROC 20j: {roc20:.2f}%" if roc20 is not None else "ROC 20j: N/A",
            f"ADX: {adx:.1f} ({'tendance forte' if adx > 25 else 'tendance faible'})",
            f"Momentum global: {momentum_global:.2f}%",
            f"Accélération: {acceleration:.2f}%",
        ]

        # Distance de stop MINIMALE (% du prix) : `min/max(closes[-5:])` inclut
        # la bougie courante. Si la dernière clôture est l'extrême de la
        # fenêtre (repli normal en tendance), le stop tombait EXACTEMENT sur le
        # prix d'entrée → sl == tp == prix, soit un ordre dégénéré émis à 85 %
        # de confiance. On impose donc un écart plancher.
        ECART_MIN = 0.005          # 0,5 % du prix

        if momentum_global > 2 and adx > 25 and di_plus > di_moins:
            action = ActionSignal.ACHAT
            confiance = min(85, 50 + momentum_global * 5 + (adx - 25) * 0.5)
            signaux.append("Momentum haussier fort + ADX élevé")
            sl = min(min(closes[-5:]), prix * (1 - ECART_MIN))
            tp = prix + (prix - sl) * 2.5
        elif momentum_global < -2 and adx > 25 and di_moins > di_plus:
            action = ActionSignal.VENTE
            confiance = min(85, 50 + abs(momentum_global) * 5 + (adx - 25) * 0.5)
            signaux.append("Momentum baissier fort + ADX élevé")
            sl = max(max(closes[-5:]), prix * (1 + ECART_MIN))
            tp = prix - (sl - prix) * 2.5
        elif abs(momentum_global) < 0.5 and adx < 20:
            action = ActionSignal.HOLD
            confiance = 20
            signaux.append("Absence de momentum → marché sans tendance")
            sl = None
            tp = None
        else:
            action = ActionSignal.SURVEILLER
            confiance = 35
            sl = None
            tp = None

        return self._creer_signal(
            symbole=symbole, action=action, confiance=confiance,
            raisonnement=" | ".join(signaux), prix=prix, sl=sl, tp=tp,
            donnees={"roc20": roc20, "adx": adx, "di_plus": di_plus,
                     "di_moins": di_moins, "momentum_global": momentum_global}
        )

    @staticmethod
    def _roc(closes: list, periode: int) -> float:
        if len(closes) <= periode or closes[-1 - periode] == 0:
            return None
        return (closes[-1] - closes[-1 - periode]) / closes[-1 - periode] * 100

    @staticmethod
    def _adx(highs: list, lows: list, closes: list, periode: int = 14):
        """Vrai ADX de Wilder : lissage du DX (et non un DX instantané, bien
        plus bruité). Les seuils adx>25 / adx<20 sont calibrés pour cet ADX
        lissé. Retourne (adx, di_plus, di_minus)."""
        n = len(closes)
        if n < periode + 1:
            return 20.0, 25.0, 20.0
        dm_plus, dm_minus, tr = [], [], []
        for i in range(1, n):
            h_diff = highs[i] - highs[i - 1]
            l_diff = lows[i - 1] - lows[i]
            dm_plus.append(h_diff if h_diff > l_diff and h_diff > 0 else 0.0)
            dm_minus.append(l_diff if l_diff > h_diff and l_diff > 0 else 0.0)
            tr.append(max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]),
                          abs(lows[i] - closes[i - 1])))

        def _wilder(vals, p):
            """Lissage de Wilder : 1re valeur = somme des p premiers, puis
            v = v - v/p + nouvelle. Renvoie une série de longueur len(vals)-p+1."""
            if len(vals) < p:
                return []
            out = [float(sum(vals[:p]))]
            for v in vals[p:]:
                out.append(out[-1] - out[-1] / p + v)
            return out

        tr_s = _wilder(tr, periode)
        dmp_s = _wilder(dm_plus, periode)
        dmm_s = _wilder(dm_minus, periode)
        if not tr_s:
            return 20.0, 25.0, 20.0

        dx_series = []
        di_plus_last = di_minus_last = 0.0
        for trv, dpv, dmv in zip(tr_s, dmp_s, dmm_s):
            dip = (dpv / trv * 100) if trv > 0 else 0.0
            dim = (dmv / trv * 100) if trv > 0 else 0.0
            di_plus_last, di_minus_last = dip, dim
            somme = dip + dim
            dx_series.append(abs(dip - dim) / somme * 100 if somme > 0 else 0.0)

        # ADX = lissage de Wilder du DX (moyenne des `periode` premiers DX,
        # puis récurrence). Si l'historique est trop court, moyenne simple.
        if len(dx_series) >= periode:
            adx = float(np.mean(dx_series[:periode]))
            for dxv in dx_series[periode:]:
                adx = (adx * (periode - 1) + dxv) / periode
        else:
            adx = float(np.mean(dx_series)) if dx_series else 20.0
        return adx, float(di_plus_last), float(di_minus_last)
