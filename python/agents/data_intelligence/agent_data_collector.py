import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from agents.base_agent import BaseAgent
from models.signal import Signal, ActionSignal
from typing import Dict
from datetime import datetime


class AgentCollecteurData(BaseAgent):
    @property
    def id(self): return "DC-036"
    @property
    def nom(self): return "Collecteur de Données"
    @property
    def groupe(self): return "data_intelligence"
    @property
    def description(self): return "Collecte et validation des données : APIs, flux temps réel, qualité données"
    @property
    def _system_prompt(self):
        return """Tu es le responsable de la collecte et validation des données de marché.
Tu t'assures que les données sont fraîches, complètes et sans anomalies.
Tu détectes les données manquantes, aberrantes et les gaps.
Une mauvaise donnée peut fausser toute l'analyse. Tu communiques en français."""

    def _analyser(self, symbole: str, donnees: Dict, contexte: Dict) -> Signal:
        closes = donnees.get("closes", [])
        highs = donnees.get("highs", [])
        lows = donnees.get("lows", [])
        volumes = donnees.get("volumes", [])
        simule = donnees.get("indicateurs", {}).get("simule", True)

        problemes = []
        score_qualite = 100

        if len(closes) < 20:
            problemes.append(f"Données insuffisantes: {len(closes)} bougies (min requis: 20)")
            score_qualite -= 50

        if closes:
            for i in range(1, len(closes)):
                if closes[i-1] > 0:
                    variation = abs(closes[i] - closes[i-1]) / closes[i-1]
                    if variation > 0.3:
                        problemes.append(f"Variation anormale à index {i}: {variation*100:.1f}%")
                        score_qualite -= 10
                        break

            if any(c <= 0 for c in closes):
                problemes.append("Prix négatifs ou zéro détectés")
                score_qualite -= 30

            if volumes and any(v < 0 for v in volumes):
                problemes.append("Volumes négatifs détectés")
                score_qualite -= 20

        if simule:
            problemes.append("ATTENTION: Données simulées (pas de connexion API)")
            score_qualite -= 20

        fraicheur = donnees.get("derniere_maj")
        if not fraicheur:
            problemes.append("Timestamp manquant")
            score_qualite -= 5

        signaux = [
            f"Symbole: {symbole} | Bougies: {len(closes)}",
            f"Score qualité données: {score_qualite}/100",
            f"Source: {'Simulée' if simule else 'API Réelle'}",
            f"Timestamp: {fraicheur or datetime.now().isoformat()}",
            f"Problèmes détectés: {len(problemes)}",
        ]
        signaux += [f"- {p}" for p in problemes[:3]]

        if score_qualite < 50:
            action = ActionSignal.ALERTE
            confiance = 90
            signaux.append("ALERTE: Données non fiables → analyses suspendues")
        elif score_qualite < 75:
            action = ActionSignal.SURVEILLER
            confiance = 65
            signaux.append("Qualité données dégradée → résultats avec réserves")
        else:
            action = ActionSignal.HOLD
            confiance = 85
            signaux.append(("Données SIMULÉES → analyses non fiables" if simule else "Données validées → analyses fiables"))

        return self._creer_signal(
            symbole=symbole, action=action, confiance=confiance,
            raisonnement=" | ".join(signaux),
            donnees={"score_qualite": score_qualite, "nb_bougies": len(closes),
                     "problemes": problemes, "simule": simule}
        )
