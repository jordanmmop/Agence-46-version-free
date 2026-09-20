import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from agents.base_agent import BaseAgent
from models.signal import Signal, ActionSignal
from typing import Dict
from datetime import datetime


class AgentOrderManagement(BaseAgent):
    @property
    def id(self): return "OM-030"
    @property
    def nom(self): return "Gestionnaire d'Ordres"
    @property
    def groupe(self): return "execution"
    @property
    def description(self): return "Gestion du carnet d'ordres : ordres actifs, modifications, annulations"
    @property
    def _system_prompt(self):
        return """Tu es le gestionnaire du carnet d'ordres de l'agence.
Tu tracks tous les ordres actifs, en attente et exécutés.
Tu gères les modifications et annulations d'ordres selon les conditions de marché.
Tu optimises le carnet pour minimiser les coûts. Tu communiques en français."""

    ordres_actifs = []

    def _analyser(self, symbole: str, donnees: Dict, contexte: Dict) -> Signal:
        closes = donnees.get("closes", [])
        prix = closes[-1] if closes else 0
        ordres = donnees.get("ordres", self.ordres_actifs)
        signal_parent = donnees.get("signal", {})

        ordres_symbole = [o for o in ordres if o.get("symbole") == symbole]
        nb_ordres_actifs = len([o for o in ordres if o.get("statut") == "ACTIF"])
        valeur_en_attente = sum(o.get("valeur", 0) for o in ordres_symbole if o.get("statut") == "ACTIF")

        prix_entree = signal_parent.get("prix_entree", prix)
        sl = signal_parent.get("stop_loss")
        tp = signal_parent.get("take_profit")
        action_demandee = signal_parent.get("action", "HOLD")

        ordres_creer = []
        if action_demandee in ["BUY", "SELL"] and prix_entree and sl and tp:
            ordre_principal = {
                "id": f"ORD-{symbole}-{datetime.now().strftime('%H%M%S')}",
                "symbole": symbole,
                "type": action_demandee,
                "prix": prix_entree,
                "quantite": signal_parent.get("taille_position_pct", 0.05),
                "stop_loss": sl,
                "take_profit": tp,
                "statut": "PENDING",
                "cree_le": datetime.now().isoformat(),
            }
            ordres_creer.append(ordre_principal)

        signaux = [
            f"Ordres actifs total: {nb_ordres_actifs}",
            f"Ordres sur {symbole}: {len(ordres_symbole)}",
            f"Valeur en attente: {valeur_en_attente:,.0f}€",
            f"Ordres à créer: {len(ordres_creer)}",
        ]

        if ordres_creer:
            signaux.append(f"Nouvel ordre: {ordres_creer[0]['type']} {symbole} @ {prix_entree:.4f}")
            action = ActionSignal.ACHAT if action_demandee == "BUY" else ActionSignal.VENTE
            confiance = 80
        elif nb_ordres_actifs > 8:
            signaux.append("Carnet surchargé → révision ordres recommandée")
            action = ActionSignal.SURVEILLER
            confiance = 60
        else:
            action = ActionSignal.HOLD
            confiance = 70

        return self._creer_signal(
            symbole=symbole, action=action, confiance=confiance,
            raisonnement=" | ".join(signaux), prix=prix_entree if prix_entree else prix,
            sl=sl, tp=tp, taille=signal_parent.get("taille_position_pct", 0),
            donnees={"ordres_actifs": nb_ordres_actifs, "ordres_crees": ordres_creer,
                     "valeur_attente": valeur_en_attente}
        )
