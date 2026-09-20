import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from agents.base_agent import BaseAgent
from models.signal import Signal, ActionSignal
from utils.indicators import Indicateurs
from config import COMMISSION_PCT, SLIPPAGE_PCT
from typing import Dict
import numpy as np
import uuid
from datetime import datetime


class AgentExecution(BaseAgent):
    @property
    def id(self): return "EX-029"
    @property
    def nom(self): return "Agent d'Exécution"
    @property
    def groupe(self): return "execution"
    @property
    def description(self): return "Exécution optimale des ordres : TWAP, VWAP, market/limit orders"
    @property
    def _system_prompt(self):
        return """Tu es l'expert en exécution d'ordres sur les marchés financiers.
Tu choisis le meilleur type d'ordre (market, limit, TWAP, VWAP) pour minimiser l'impact marché.
Tu monitores l'exécution et reportes les fills. Tu communiques en français."""

    def _analyser(self, symbole: str, donnees: Dict, contexte: Dict) -> Signal:
        closes = donnees.get("closes", [])
        volumes = donnees.get("volumes", [1e6] * len(closes))
        highs = donnees.get("highs", closes)
        lows = donnees.get("lows", closes)
        signal_parent = donnees.get("signal", {})
        if len(closes) < 5:
            return self._signal_neutre(symbole, "Données insuffisantes")

        prix = closes[-1]
        action_str = signal_parent.get("action", "HOLD")
        taille = signal_parent.get("taille_position_pct", 0.05)

        vol_moy = np.mean(volumes[-20:]) if len(volumes) >= 20 else np.mean(volumes) if volumes else 1e6
        vol_actuelle = volumes[-1] if volumes else vol_moy
        ratio_vol = vol_actuelle / vol_moy if vol_moy > 0 else 1.0

        atr = Indicateurs.atr(highs, lows, closes, 5)
        atr_val = next((v for v in reversed(atr) if v is not None), prix * 0.01)
        vwap = Indicateurs.vwap(highs, lows, closes, volumes)
        vwap_val = vwap[-1] if vwap else prix

        slippage = SLIPPAGE_PCT * (1 + (1 - min(ratio_vol, 2)) * 0.5)
        commission = COMMISSION_PCT
        cout_total = slippage + commission

        if ratio_vol > 1.5:
            type_ordre = "MARKET"
            prix_execution = prix * (1 + slippage if action_str == "BUY" else 1 - slippage)
        else:
            type_ordre = "LIMIT"
            if action_str == "BUY":
                prix_execution = min(vwap_val, prix * 1.001)
            else:
                prix_execution = max(vwap_val, prix * 0.999)

        trade_id = str(uuid.uuid4())[:8].upper()

        signaux = [
            f"Trade ID: {trade_id}",
            f"Type ordre: {type_ordre}",
            f"Prix marché: {prix:.4f} | Prix exécution est.: {prix_execution:.4f}",
            f"Slippage estimé: {slippage*100:.3f}% | Commission: {commission*100:.3f}%",
            f"Coût total: {cout_total*100:.3f}%",
            f"VWAP: {vwap_val:.4f} | Ratio volume: {ratio_vol:.2f}",
        ]

        if action_str in ["BUY", "SELL"]:
            action = ActionSignal.ACHAT if action_str == "BUY" else ActionSignal.VENTE
            confiance = 85
            signaux.append(f"Exécution {action_str}: {type_ordre} order à {prix_execution:.4f}")
        else:
            action = ActionSignal.HOLD
            confiance = 50

        return self._creer_signal(
            symbole=symbole, action=action, confiance=confiance,
            raisonnement=" | ".join(signaux), prix=prix_execution,
            sl=signal_parent.get("stop_loss"), tp=signal_parent.get("take_profit"),
            taille=taille,
            donnees={"trade_id": trade_id, "type_ordre": type_ordre,
                     "prix_execution": prix_execution, "cout_total_pct": cout_total,
                     "timestamp_execution": datetime.now().isoformat()}
        )
