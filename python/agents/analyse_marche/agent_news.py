import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from agents.base_agent import BaseAgent
from models.signal import Signal, ActionSignal
from typing import Dict


class AgentTraderNews(BaseAgent):
    @property
    def id(self): return "TN-004"
    @property
    def nom(self): return "Trader Actualités"
    @property
    def groupe(self): return "analyse_marche"
    @property
    def description(self): return "Analyse l'impact des actualités économiques et événements sur les prix"
    @property
    def _system_prompt(self):
        return """Tu es un trader spécialisé dans le trading sur actualités (news trading).
Tu analyses l'impact des annonces économiques, résultats d'entreprises, décisions des banques centrales
et événements géopolitiques sur les marchés financiers. Tu réagis rapidement aux informations.
Tu communiques en français avec des analyses précises et rapides."""

    MOTS_POSITIFS = ["hausse", "croissance", "bénéfices", "record", "accord", "approbation",
                     "positif", "fort", "solide", "expansion", "acquisition", "partenariat"]
    MOTS_NEGATIFS = ["baisse", "perte", "déficit", "risque", "investigation", "fraude",
                     "négatif", "faible", "déclin", "licenciements", "dettes", "faillite"]

    def _analyser(self, symbole: str, donnees: Dict, contexte: Dict) -> Signal:
        closes = donnees.get("closes", [])
        news = donnees.get("news", [])
        prix = closes[-1] if closes else 0

        # `donnees["news"]` n'est pas alimenté par l'orchestrateur : les
        # actualités sont alors GÉNÉRIQUES (générées), pas de vraies dépêches.
        # On le signale et on limite l'influence de ce signal sur le vote de
        # consensus qui dimensionne les ordres réels.
        news_simulees = not news
        if not news:
            news = self._simuler_news(symbole)

        score_sentiment = 0
        impact_total = 0
        analyses_news = []

        for article in news[:10]:
            titre = article.get("titre", article.get("title", "")).lower()
            impact = article.get("impact", 0.5)
            pos = sum(1 for m in self.MOTS_POSITIFS if m in titre)
            neg = sum(1 for m in self.MOTS_NEGATIFS if m in titre)
            score_article = (pos - neg) * impact
            score_sentiment += score_article
            if pos > neg:
                analyses_news.append(f"+ {titre[:50]}...")
            elif neg > pos:
                analyses_news.append(f"- {titre[:50]}...")

        if not analyses_news:
            return self._signal_neutre(symbole, "Pas d'actualités significatives")

        if score_sentiment > 1.5:
            action = ActionSignal.ACHAT
            confiance = min(80, 50 + score_sentiment * 10)
            raison = f"Actualités positives ({score_sentiment:.1f}): {analyses_news[0]}"
            sl = prix * 0.96 if prix > 0 else None
            tp = prix * 1.10 if prix > 0 else None
        elif score_sentiment < -1.5:
            action = ActionSignal.VENTE
            confiance = min(80, 50 + abs(score_sentiment) * 10)
            raison = f"Actualités négatives ({score_sentiment:.1f}): {analyses_news[0]}"
            sl = prix * 1.04 if prix > 0 else None
            tp = prix * 0.92 if prix > 0 else None
        else:
            action = ActionSignal.SURVEILLER
            confiance = 30
            raison = f"Actualités mixtes (score: {score_sentiment:.1f})"
            sl = None
            tp = None

        if news_simulees:
            # Signal fondé sur des actualités génériques : influence bridée
            # (n'engage pas un gros volume) et raisonnement honnête.
            confiance = min(confiance, 45)
            raison = "[actualités indicatives, non réelles] " + raison

        return self._creer_signal(
            symbole=symbole, action=action, confiance=confiance,
            raisonnement=raison, prix=prix if prix > 0 else None, sl=sl, tp=tp,
            donnees={"score_sentiment": score_sentiment, "nb_news": len(news),
                     "news_analysees": analyses_news[:3], "simule": news_simulees}
        )

    def _simuler_news(self, symbole: str) -> list:
        headlines = [
            {"titre": f"{symbole} annonce des résultats trimestriels solides", "impact": 0.8},
            {"titre": f"Analyse technique positive pour {symbole}", "impact": 0.5},
            {"titre": f"Les investisseurs restent prudents sur {symbole}", "impact": 0.4},
        ]
        return headlines
