import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from agents.base_agent import BaseAgent
from models.signal import Signal, ActionSignal
from utils.indicators import Indicateurs
from typing import Dict
import numpy as np


class AgentMLPredicteur(BaseAgent):
    @property
    def id(self): return "ML-037"
    @property
    def nom(self): return "Prédicteur ML"
    @property
    def groupe(self): return "data_intelligence"
    @property
    def description(self): return "Machine Learning : régression, classification, prédiction de prix et tendances"
    @property
    def _system_prompt(self):
        return """Tu es l'expert en machine learning appliqué au trading.
Tu utilises la régression linéaire, les forêts aléatoires et les réseaux de neurones
pour prédire les prix futurs et les tendances.
Tu communiques tes prédictions avec intervalles de confiance. Tu communiques en français."""

    def _analyser(self, symbole: str, donnees: Dict, contexte: Dict) -> Signal:
        closes = donnees.get("closes", [])
        volumes = donnees.get("volumes", [1e6] * len(closes))
        highs = donnees.get("highs", closes)
        lows = donnees.get("lows", closes)
        if len(closes) < 30:
            return self._signal_neutre(symbole, "Données insuffisantes pour ML")

        prix = closes[-1]
        features = self._extraire_features(closes, highs, lows, volumes)
        prediction, confidence = self._regression_lineaire(closes, features)
        direction_ml = "HAUSSE" if prediction > prix else "BAISSE"
        variation_predite = (prediction - prix) / prix * 100 if prix else 0.0

        r2 = self._r_squared(closes[-20:])
        tendance_force = abs(variation_predite)

        signaux = [
            f"Prix actuel: {prix:.4f}",
            f"Prédiction ML (5j): {prediction:.4f} ({variation_predite:+.2f}%)",
            f"Direction: {direction_ml}",
            f"Confiance modèle: {confidence:.1f}%",
            f"R² tendance: {r2:.3f}",
        ]

        seuil = 1.5
        if variation_predite > seuil and confidence > 55:
            action = ActionSignal.ACHAT
            confiance_signal = min(80, confidence * 0.8)
            sl = prix * (1 - abs(variation_predite) / 100 * 0.5)
            tp = prediction
            signaux.append(f"ML prédit hausse de {variation_predite:.1f}% → BUY")
        elif variation_predite < -seuil and confidence > 55:
            action = ActionSignal.VENTE
            confiance_signal = min(80, confidence * 0.8)
            sl = prix * (1 + abs(variation_predite) / 100 * 0.5)
            tp = prediction
            signaux.append(f"ML prédit baisse de {variation_predite:.1f}% → SELL")
        else:
            return self._signal_neutre(symbole, f"Prédiction ML neutre ({variation_predite:+.2f}%)")

        return self._creer_signal(
            symbole=symbole, action=action, confiance=confiance_signal,
            raisonnement=" | ".join(signaux), prix=prix, sl=sl, tp=tp, taille=0.04,
            donnees={"prediction": prediction, "variation_pct": variation_predite,
                     "confidence_ml": confidence, "r2": r2}
        )

    def _extraire_features(self, closes, highs, lows, volumes):
        rsi = Indicateurs.rsi(closes, 14)
        ema20 = Indicateurs.ema(closes, 20)
        std20 = np.std(closes[-20:]) if len(closes) >= 20 else 0.0
        return {
            "rsi": next((v for v in reversed(rsi) if v is not None), 50),
            "ema20": next((v for v in reversed(ema20) if v is not None), closes[-1]),
            # Garde std20>0 : sur 20 clôtures identiques (marché plat), la
            # division donnerait inf/nan.
            "vol_ratio": (np.std(closes[-5:]) / std20) if std20 > 0 else 1.0,
            "momentum": (closes[-1] - closes[-5]) / closes[-5] * 100
                        if len(closes) >= 5 and closes[-5] else 0.0,
        }

    def _regression_lineaire(self, closes, features):
        """Extrapolation de tendance linéaire AJUSTÉE par les features (le
        modèle les utilise réellement, au lieu de les calculer puis les ignorer)."""
        n = min(20, len(closes))
        x = np.arange(n)
        y = np.array(closes[-n:], dtype=float)
        coeffs = np.polyfit(x, y, 1)
        pente = coeffs[0]
        prix = y[-1]
        prediction = prix + pente * 5   # projection de la tendance à +5 pas

        rsi = features.get("rsi", 50.0)
        momentum = features.get("momentum", 0.0)      # % sur 5 périodes
        vol_ratio = features.get("vol_ratio", 1.0)

        # Le momentum renforce/atténue la projection de tendance
        prediction *= (1 + momentum / 100 * 0.2)
        # RSI extrême → tempère une prédiction qui prolonge la sur-extension
        # (mean-reversion : on divise par deux l'amplitude prédite)
        if rsi > 70 and prediction > prix:
            prediction = prix + (prediction - prix) * 0.5
        elif rsi < 30 and prediction < prix:
            prediction = prix + (prediction - prix) * 0.5

        residuals = y - np.polyval(coeffs, x)
        r2 = 1 - np.var(residuals) / np.var(y) if np.var(y) > 0 else 0
        # Volatilité récente élevée (vol_ratio > 1) → confiance réduite
        penalite_vol = max(0.0, min(0.4, (vol_ratio - 1) * 0.3))
        confidence = max(30, min(85, r2 * 80 + 20)) * (1 - penalite_vol)
        return float(prediction), float(confidence)

    def _r_squared(self, closes):
        if len(closes) < 5:
            return 0
        x = np.arange(len(closes))
        y = np.array(closes)
        coeffs = np.polyfit(x, y, 1)
        residuals = y - np.polyval(coeffs, x)
        return float(1 - np.var(residuals) / np.var(y)) if np.var(y) > 0 else 0
