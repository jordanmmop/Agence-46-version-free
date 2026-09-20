"""Licence, abonnement et restrictions de la version d'essai.

    Abonnement / Licence   →   Feature Gate   →   Agents IA / Fonctionnalités
      (abonnement.py)           (gate.py)          (agents/, backend/)

Les valeurs des limites sont toutes dans `licence/config.py`, et nulle part
ailleurs. Les modules applicatifs n'importent que `gate` :

    from licence import gate
    gate.exiger("automatisations")              # lève ProRequis si essai
    agents = gate.agents_autorises(TOUS_LES_AGENTS)
"""
from licence import abonnement, config, gate, quota, verification
from licence.etat import EtatLicence
from licence.gate import ProRequis, QuotaDepasse

__all__ = [
    "abonnement", "config", "gate", "quota", "verification",
    "EtatLicence", "ProRequis", "QuotaDepasse",
]
