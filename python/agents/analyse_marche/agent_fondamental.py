import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from agents.base_agent import BaseAgent
from models.signal import Signal, ActionSignal
from typing import Dict


class AgentAnalyseFondamentale(BaseAgent):
    @property
    def id(self): return "AF-002"
    @property
    def nom(self): return "Analyste Fondamental"
    @property
    def groupe(self): return "analyse_marche"
    @property
    def description(self): return "Analyse fondamentale : P/E, EPS, croissance, bilans, ratios financiers"
    @property
    def _system_prompt(self):
        return """Tu es un analyste fondamental expert. Tu évalues la valeur intrinsèque des actifs
basé sur les fondamentaux : P/E ratio, croissance des bénéfices, marge, dette, flux de trésorerie.
Tu identifies les actifs sous-évalués ou surévalués par rapport à leurs pairs.
Tu communiques en français avec des analyses précises et des recommandations argumentées."""

    SECTEURS_PE_MOYEN = {
        "Technology": 28, "Healthcare": 22, "Financial": 12,
        "Consumer": 20, "Energy": 15, "Utilities": 18, "default": 20
    }

    def _analyser(self, symbole: str, donnees: Dict, contexte: Dict) -> Signal:
        info = donnees.get("info", {})
        closes = donnees.get("closes", [])
        prix = closes[-1] if closes else info.get("prix", 0)

        pe = info.get("pe_ratio")
        eps = info.get("eps")
        beta = info.get("beta", 1.0)
        haut_52w = info.get("52w_haut")
        bas_52w = info.get("52w_bas")
        secteur = info.get("secteur", "default")
        capitalisation = info.get("capitalisation", 0)
        dividende = info.get("dividende_yield")

        score = 50
        signaux = []

        if pe is not None and pe > 0:
            pe_moyen = self.SECTEURS_PE_MOYEN.get(secteur, 20)
            if pe < pe_moyen * 0.7:
                score += 20
                signaux.append(f"P/E attractif {pe:.1f}x (moy secteur: {pe_moyen}x)")
            elif pe > pe_moyen * 1.5:
                score -= 20
                signaux.append(f"P/E élevé {pe:.1f}x (moy secteur: {pe_moyen}x)")
            else:
                signaux.append(f"P/E neutre {pe:.1f}x")

        if eps is not None and eps > 0:
            score += 10
            signaux.append(f"EPS positif: {eps:.2f}")
        elif eps is not None and eps < 0:
            score -= 15
            signaux.append(f"EPS négatif: {eps:.2f}")

        if beta is not None:
            if beta < 0.8:
                score += 5
                signaux.append(f"Beta faible (défensif): {beta:.2f}")
            elif beta > 1.5:
                score -= 5
                signaux.append(f"Beta élevé (volatil): {beta:.2f}")

        if haut_52w and bas_52w and prix > 0:
            position = (prix - bas_52w) / (haut_52w - bas_52w) * 100
            if position < 25:
                score += 15
                signaux.append(f"Prix près du plus bas 52s ({position:.0f}%)")
            elif position > 85:
                score -= 10
                signaux.append(f"Prix près du plus haut 52s ({position:.0f}%)")

        if dividende and dividende > 0.03:
            score += 8
            signaux.append(f"Dividende attractif: {dividende*100:.1f}%")

        if capitalisation > 1e11:
            score += 5
            signaux.append("Grande capitalisation (stabilité)")

        if score >= 70:
            action = ActionSignal.ACHAT
            confiance = min(90, score)
            sl = prix * 0.92 if prix > 0 else None
            tp = prix * 1.20 if prix > 0 else None
        elif score <= 35:
            action = ActionSignal.VENTE
            confiance = min(90, 100 - score)
            sl = prix * 1.08 if prix > 0 else None
            tp = prix * 0.85 if prix > 0 else None
        else:
            action = ActionSignal.SURVEILLER
            confiance = abs(score - 50)
            sl = None
            tp = None

        return self._creer_signal(
            symbole=symbole, action=action, confiance=confiance,
            raisonnement=" | ".join(signaux) if signaux else "Données fondamentales insuffisantes",
            prix=prix if prix > 0 else None, sl=sl, tp=tp,
            donnees={"score": score, "pe": pe, "eps": eps, "secteur": secteur}
        )
