import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from agents.base_agent import BaseAgent
from models.signal import Signal, ActionSignal
from typing import Dict
import numpy as np
import hashlib
from datetime import datetime


class AgentDonneesAlternatives(BaseAgent):
    @property
    def id(self): return "AD-040"
    @property
    def nom(self): return "Analyste Données Alternatives"
    @property
    def groupe(self): return "data_intelligence"
    @property
    def description(self): return "Données alternatives : sentiment social, on-chain crypto, open interest, flux options"
    @property
    def _system_prompt(self):
        return """Tu es l'expert en données alternatives pour le trading.
Tu analyses le sentiment des réseaux sociaux, les données on-chain crypto,
l'open interest des options, les positions des fonds (COT report).
Ces données dites "alternatives" donnent un avantage informationnel. Tu communiques en français."""

    def _analyser(self, symbole: str, donnees: Dict, contexte: Dict) -> Signal:
        closes = donnees.get("closes", [])
        prix = closes[-1] if closes else 0
        alt_data = donnees.get("alt_data", {})

        if not alt_data:
            alt_data = self._simuler_alt_data(symbole)

        sentiment_social = alt_data.get("sentiment_social", 50)
        volume_mentions = alt_data.get("volume_mentions", 1000)
        change_mentions = alt_data.get("change_mentions_24h", 0)
        open_interest = alt_data.get("open_interest", 0)
        long_short_ratio = alt_data.get("long_short_ratio", 1.0)

        est_crypto = any(c in symbole for c in ["BTC", "ETH", "BNB", "SOL"])
        on_chain_score = 50
        if est_crypto:
            whale_activity = alt_data.get("whale_transactions", 0)
            exchange_outflow = alt_data.get("exchange_outflow", 0)
            if exchange_outflow > 0:
                on_chain_score += 15
                if whale_activity > 10:
                    on_chain_score += 10

        score_total = (sentiment_social * 0.4 + on_chain_score * 0.3 +
                       min(100, 50 + change_mentions * 0.1) * 0.3)

        signaux = [
            f"Sentiment social: {sentiment_social:.0f}/100",
            f"Mentions 24h: {volume_mentions:,} ({change_mentions:+.0f}%)",
            f"L/S Ratio: {long_short_ratio:.2f}",
            f"Score données alternatives: {score_total:.0f}/100",
        ]
        if est_crypto:
            signaux.append(f"Score on-chain: {on_chain_score:.0f}/100")

        if score_total > 70 and change_mentions > 20:
            action = ActionSignal.ACHAT
            confiance = min(72, score_total * 0.8)
            sl = prix * 0.94 if prix > 0 else None
            tp = prix * 1.12 if prix > 0 else None
            signaux.append("Données alt bullish → momentum social positif")
        elif score_total < 35 and change_mentions < -20:
            action = ActionSignal.VENTE
            confiance = min(68, (100 - score_total) * 0.8)
            sl = prix * 1.06 if prix > 0 else None
            tp = prix * 0.90 if prix > 0 else None
            signaux.append("Données alt bearish → sentiment négatif croissant")
        else:
            action = ActionSignal.SURVEILLER
            confiance = 35
            sl = None
            tp = None

        return self._creer_signal(
            symbole=symbole, action=action, confiance=confiance,
            raisonnement=" | ".join(signaux), prix=prix if prix > 0 else None,
            sl=sl, tp=tp,
            donnees={"sentiment": sentiment_social, "mentions": volume_mentions,
                     "ls_ratio": long_short_ratio, "score_total": score_total}
        )

    @staticmethod
    def _simuler_alt_data(symbole: str) -> dict:
        seed = int(hashlib.md5((symbole + datetime.now().strftime("%H")).encode()).hexdigest(), 16) % 1000
        # Générateur LOCAL : np.random.seed() muterait l'état global de numpy,
        # partagé par les 46 agents exécutés en parallèle dans le
        # ThreadPoolExecutor — leurs tirages en seraient corrompus.
        rng = np.random.default_rng(seed)
        return {
            "sentiment_social": float(rng.uniform(30, 80)),
            "volume_mentions": int(rng.uniform(500, 50000)),
            "change_mentions_24h": float(rng.uniform(-30, 30)),
            "open_interest": float(rng.uniform(1e6, 1e9)),
            "long_short_ratio": float(rng.uniform(0.5, 2.5)),
            "whale_transactions": int(rng.uniform(0, 20)),
            "exchange_outflow": float(rng.uniform(-1000, 3000)),
        }
