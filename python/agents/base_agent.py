"""Classe de base des 45 agents spécialisés.

Les agents raisonnent en LOCAL : indicateurs techniques + règles, puis
vérification par leur assistant IA (cf. agents/assistant.py), qui consulte le
moteur IA choisi — Ollama ou Hermès, tous deux sur la machine.

AUCUN appel distant, nulle part : ni ici, ni dans les assistants, ni dans la
synthèse du Chef d'Orchestre. L'application tourne entièrement hors ligne et
sans clé API.
"""
from abc import ABC, abstractmethod
from datetime import datetime
from typing import Dict, List, Optional
import logging
import math
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from models.signal import Signal, ActionSignal

logger = logging.getLogger(__name__)


class BaseAgent(ABC):

    def __init__(self):
        self.statut = "idle"
        self.derniere_analyse: Optional[datetime] = None
        self.nb_analyses = 0
        self.historique_signaux: List[Signal] = []
        # Chaque agent a son assistant IA local (préparation + vérification)
        from agents.assistant import AssistantIA
        self.assistant = AssistantIA(self.id, self.nom, self.groupe)

    @property
    @abstractmethod
    def id(self) -> str: ...

    @property
    @abstractmethod
    def nom(self) -> str: ...

    @property
    @abstractmethod
    def groupe(self) -> str: ...

    @property
    @abstractmethod
    def description(self) -> str: ...

    # Rôle de l'agent, en français. Sert de documentation exécutable : chaque
    # agent DOIT décrire sa spécialité, et c'est le texte qui serait envoyé
    # comme prompt système si un agent devait un jour raisonner à distance.
    # Aucun appel distant ne le lit aujourd'hui (cf. docstring du module).
    @property
    @abstractmethod
    def _system_prompt(self) -> str: ...

    def analyser(self, symbole: str, donnees: Dict, contexte: Dict = None) -> Signal:
        self.statut = "analyse"
        self.derniere_analyse = datetime.now()
        self.nb_analyses += 1
        # L'assistant prépare le contexte (qualité des données, tendance, volatilité)
        contexte = self.assistant.preparer_contexte(symbole, donnees, contexte or {})
        try:
            signal = self._analyser(symbole, donnees, contexte)
        except Exception as e:
            logger.error(f"[{self.nom}] Erreur analyse {symbole}: {e}")
            signal = self._signal_neutre(symbole, f"Erreur: {str(e)}")
        finally:
            self.statut = "idle"
        # L'assistant vérifie et fiabilise le signal avant diffusion
        signal = self.assistant.verifier_signal(signal, donnees)
        self.historique_signaux.append(signal)
        if len(self.historique_signaux) > 100:
            self.historique_signaux = self.historique_signaux[-100:]
        return signal

    @abstractmethod
    def _analyser(self, symbole: str, donnees: Dict, contexte: Dict) -> Signal: ...

    @staticmethod
    def _dernier_prix(donnees: Dict) -> Optional[float]:
        """Dernière clôture connue, ou None si les cours sont indisponibles.

        `donnees.get("closes", [None])[-1]` — la forme employée jusqu'ici par
        six agents — levait IndexError quand `closes` était présent mais VIDE.
        Or c'est exactement ce que l'orchestrateur transmet quand le
        téléchargement des cours échoue (`_preparer_donnees` renvoie
        `"closes": []`) : ces agents retombaient alors en signal neutre
        « Erreur: list index out of range », donc six voix perdues sur
        quarante-cinq au moment précis où le consensus compte le plus.
        """
        closes = donnees.get("closes") or []
        try:
            valeur = float(closes[-1])
        except (IndexError, TypeError, ValueError):
            return None
        return valeur if math.isfinite(valeur) and valeur > 0 else None

    def _signal_neutre(self, symbole: str, raison: str = "Pas de signal clair") -> Signal:
        return Signal(
            agent_id=self.id,
            agent_nom=self.nom,
            symbole=symbole,
            action=ActionSignal.HOLD,
            confiance=0.0,
            raisonnement=raison,
        )

    def _creer_signal(self, symbole: str, action: ActionSignal, confiance: float,
                      raisonnement: str, prix: float = None, sl: float = None,
                      tp: float = None, taille: float = 0.0, donnees: Dict = None) -> Signal:
        return Signal(
            agent_id=self.id,
            agent_nom=self.nom,
            symbole=symbole,
            action=action,
            confiance=min(100.0, max(0.0, confiance)),
            prix_entree=prix,
            stop_loss=sl,
            take_profit=tp,
            taille_position_pct=taille,
            raisonnement=raisonnement,
            donnees=donnees or {},
        )

    def to_dict(self) -> Dict:
        dernier_signal = None
        if self.historique_signaux:
            dernier_signal = self.historique_signaux[-1].to_dict()
        return {
            "id": self.id,
            "nom": self.nom,
            "groupe": self.groupe,
            "description": self.description,
            "statut": self.statut,
            "nb_analyses": self.nb_analyses,
            "derniere_analyse": self.derniere_analyse.isoformat() if self.derniere_analyse else None,
            "dernier_signal": dernier_signal,
            "assistant": self.assistant.to_dict(),
        }
