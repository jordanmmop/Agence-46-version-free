import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from agents.base_agent import BaseAgent
from models.signal import Signal, ActionSignal
from config import COMMISSION_PCT, SLIPPAGE_PCT
from typing import Dict


class AgentAnalyseCouts(BaseAgent):
    @property
    def id(self): return "CA-033"
    @property
    def nom(self): return "Analyste Coûts"
    @property
    def groupe(self): return "execution"
    @property
    def description(self): return "Analyse des coûts : commissions, taxes, spread, impact total sur performance"
    @property
    def _system_prompt(self):
        return """Tu es l'analyste des coûts de trading.
Tu calcules le coût total de chaque trade : commission, slippage, taxes (30% en France), financement.
Tu identifies si un trade est rentable après tous les coûts.
Un signal fort ne vaut rien si les coûts grignotent le profit. Tu communiques en français."""

    TAUX_PFU_FRANCE = 0.30
    TAUX_FINANCEMENT_JOUR = 0.0001

    def _analyser(self, symbole: str, donnees: Dict, contexte: Dict) -> Signal:
        closes = donnees.get("closes", [])
        prix = closes[-1] if closes else 0
        signal_parent = donnees.get("signal", {})
        portfolio = donnees.get("portfolio", {})

        prix_entree = signal_parent.get("prix_entree", prix)
        stop_loss = signal_parent.get("stop_loss", prix_entree * 0.97 if prix_entree else 0)
        take_profit = signal_parent.get("take_profit", prix_entree * 1.06 if prix_entree else 0)
        taille_pct = signal_parent.get("taille_position_pct", 0.05)
        capital = portfolio.get("valeur_totale", 100000)
        valeur_position = capital * taille_pct

        duree_estimee_jours = donnees.get("duree_estimee_jours", 5)

        commission_entree = valeur_position * COMMISSION_PCT
        commission_sortie = valeur_position * COMMISSION_PCT
        slippage_entree = valeur_position * SLIPPAGE_PCT
        slippage_sortie = valeur_position * SLIPPAGE_PCT
        financement = valeur_position * self.TAUX_FINANCEMENT_JOUR * duree_estimee_jours
        cout_total = commission_entree + commission_sortie + slippage_entree + slippage_sortie + financement

        profit_brut_tp = valeur_position * abs(take_profit - prix_entree) / prix_entree if prix_entree else 0
        impot_tp = profit_brut_tp * self.TAUX_PFU_FRANCE
        profit_net_tp = profit_brut_tp - cout_total - impot_tp

        perte_brute_sl = valeur_position * abs(prix_entree - stop_loss) / prix_entree if prix_entree else 0
        cout_total_pct = cout_total / valeur_position * 100 if valeur_position > 0 else 0
        seuil_rentabilite_pct = cout_total / valeur_position * 100 if valeur_position > 0 else 0

        signaux = [
            f"Valeur position: {valeur_position:,.0f}€",
            f"Commission A/R: {(commission_entree+commission_sortie):.2f}€ ({COMMISSION_PCT*2*100:.3f}%)",
            f"Slippage A/R: {(slippage_entree+slippage_sortie):.2f}€",
            f"Financement ({duree_estimee_jours}j): {financement:.2f}€",
            f"Coût total: {cout_total:.2f}€ ({cout_total_pct:.3f}%)",
            f"Seuil rentabilité: {seuil_rentabilite_pct:.3f}%",
            f"Profit net (TP): {profit_net_tp:,.0f}€ (après impôts 30%)",
        ]

        est_rentable = profit_net_tp > 0
        ratio_cout = cout_total / profit_brut_tp if profit_brut_tp > 0 else float("inf")

        if not est_rentable:
            signaux.append("ALERTE: Trade non rentable après coûts et taxes")
            action = ActionSignal.ALERTE
            confiance = 85
        elif ratio_cout > 0.3:
            signaux.append(f"Coûts importants: {ratio_cout*100:.0f}% du profit brut")
            action = ActionSignal.SURVEILLER
            confiance = 65
        else:
            signaux.append(f"Trade rentable | RR net: {profit_net_tp/perte_brute_sl:.2f}")
            action = ActionSignal.HOLD
            confiance = 80

        return self._creer_signal(
            symbole=symbole, action=action, confiance=confiance,
            raisonnement=" | ".join(signaux), prix=prix_entree if prix_entree else prix,
            donnees={"cout_total": cout_total, "profit_net": profit_net_tp,
                     "rentable": est_rentable, "ratio_cout": ratio_cout}
        )
