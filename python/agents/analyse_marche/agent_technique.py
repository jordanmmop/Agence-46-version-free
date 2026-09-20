import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from agents.base_agent import BaseAgent
from models.signal import Signal, ActionSignal
from utils.indicators import Indicateurs
from typing import Dict


class AgentAnalyseTechnique(BaseAgent):
    @property
    def id(self): return "AT-001"
    @property
    def nom(self): return "Analyste Technique"
    @property
    def groupe(self): return "analyse_marche"
    @property
    def description(self): return "Analyse les indicateurs techniques : RSI, MACD, Bollinger Bands, EMA/SMA"
    @property
    def _system_prompt(self):
        return """Tu es un expert en analyse technique des marchés financiers.
Tu analyses les indicateurs techniques (RSI, MACD, Bollinger Bands, EMA, SMA, Stochastique)
et génères des signaux d'achat/vente précis avec des niveaux de stop-loss et take-profit.
Tu communiques en français et fournis des analyses concises et actionables."""

    def _analyser(self, symbole: str, donnees: Dict, contexte: Dict) -> Signal:
        closes = donnees.get("closes", [])
        highs = donnees.get("highs", closes)
        lows = donnees.get("lows", closes)
        volumes = donnees.get("volumes", [1.0] * len(closes))
        if len(closes) < 30:
            return self._signal_neutre(symbole, "Données insuffisantes")

        rsi = Indicateurs.rsi(closes, 14)
        macd_line, signal_line, histo = Indicateurs.macd(closes)
        bb_upper, bb_mid, bb_lower = Indicateurs.bollinger(closes)
        ema20 = Indicateurs.ema(closes, 20)
        ema50 = Indicateurs.ema(closes, 50)
        atr = Indicateurs.atr(highs, lows, closes, 14)
        stoch_k, stoch_d = Indicateurs.stochastique(highs, lows, closes)

        rsi_val = next((v for v in reversed(rsi) if v is not None), 50.0)
        macd_val = next((v for v in reversed(macd_line) if v is not None), 0.0)
        sig_val = next((v for v in reversed(signal_line) if v is not None), 0.0)
        histo_val = next((v for v in reversed(histo) if v is not None), 0.0)
        histo_prev = next((v for v in reversed(histo[:-1]) if v is not None), 0.0)
        bb_u = next((v for v in reversed(bb_upper) if v is not None), closes[-1])
        bb_l = next((v for v in reversed(bb_lower) if v is not None), closes[-1])
        ema20_val = next((v for v in reversed(ema20) if v is not None), closes[-1])
        ema50_val = next((v for v in reversed(ema50) if v is not None), closes[-1])
        atr_val = next((v for v in reversed(atr) if v is not None), closes[-1] * 0.02)
        stoch_val = next((v for v in reversed(stoch_k) if v is not None), 50.0)
        prix = closes[-1]

        score_achat = 0
        score_vente = 0
        signaux = []

        if rsi_val < 30:
            score_achat += 25
            signaux.append(f"RSI survendu ({rsi_val:.1f})")
        elif rsi_val > 70:
            score_vente += 25
            signaux.append(f"RSI suracheté ({rsi_val:.1f})")
        elif rsi_val < 45:
            score_achat += 10

        if histo_val > 0 and histo_val > histo_prev:
            score_achat += 20
            signaux.append("MACD momentum haussier")
        elif histo_val < 0 and histo_val < histo_prev:
            score_vente += 20
            signaux.append("MACD momentum baissier")

        if prix < bb_l:
            score_achat += 20
            signaux.append("Prix sous Bollinger bas")
        elif prix > bb_u:
            score_vente += 20
            signaux.append("Prix au-dessus Bollinger haut")

        if ema20_val > ema50_val:
            score_achat += 15
            signaux.append("EMA20 > EMA50 (tendance haussière)")
        else:
            score_vente += 15
            signaux.append("EMA20 < EMA50 (tendance baissière)")

        if prix > ema20_val:
            score_achat += 10
        else:
            score_vente += 10

        if stoch_val < 20:
            score_achat += 10
            signaux.append(f"Stochastique survendu ({stoch_val:.1f})")
        elif stoch_val > 80:
            score_vente += 10
            signaux.append(f"Stochastique suracheté ({stoch_val:.1f})")

        sl_pct = atr_val / prix * 2
        tp_pct = sl_pct * 2.5

        if score_achat > score_vente and score_achat >= 40:
            action = ActionSignal.ACHAT
            confiance = min(95, score_achat)
            sl = prix * (1 - sl_pct)
            tp = prix * (1 + tp_pct)
        elif score_vente > score_achat and score_vente >= 40:
            action = ActionSignal.VENTE
            confiance = min(95, score_vente)
            sl = prix * (1 + sl_pct)
            tp = prix * (1 - tp_pct)
        else:
            return self._signal_neutre(symbole, f"Pas de consensus technique (achat:{score_achat}, vente:{score_vente})")

        return self._creer_signal(
            symbole=symbole, action=action, confiance=confiance,
            raisonnement=" | ".join(signaux),
            prix=prix, sl=sl, tp=tp, taille=0.05,
            donnees={"rsi": rsi_val, "macd": macd_val, "atr": atr_val,
                     "score_achat": score_achat, "score_vente": score_vente}
        )
