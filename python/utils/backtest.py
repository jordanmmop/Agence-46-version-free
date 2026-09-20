"""Backtesting — rejoue une stratégie technique sur l'historique.

Objectif : donner une idée honnête du comportement passé d'une stratégie
avant de risquer de l'argent réel. On rejoue bougie par bougie les
indicateurs (RSI, MACD, EMA) et on simule des entrées/sorties avec
commission, puis on calcule les statistiques de performance.

Ce n'est PAS une garantie de résultats futurs — les performances passées
ne préjugent pas des performances à venir.
"""
import logging
from typing import Any, Dict, List

from utils.indicators import Indicateurs

logger = logging.getLogger(__name__)

COMMISSION = 0.001  # 0,1 % par transaction


def _val(serie: List, i: int, defaut=None):
    v = serie[i] if 0 <= i < len(serie) else None
    return v if v is not None else defaut


def lancer_backtest(symbole: str, timeframe: str = "1d",
                    capital_initial: float = 10_000.0) -> Dict[str, Any]:
    """Rejoue une stratégie RSI + tendance EMA sur l'historique du symbole."""
    from utils.market_data import FetcheurDonnees
    # Historique PROFOND requis : on garde Yahoo comme source (le terminal
    # MetaTrader ne conserve que les dernières bougies chargées).
    md = FetcheurDonnees.obtenir_donnees(symbole, timeframe, prefer_courtier=False)
    if not md or len(md.bougies) < 60:
        return {"success": False, "error": "Historique insuffisant pour ce symbole"}
    # Ne JAMAIS présenter un backtest sur des prix INVENTÉS comme réel : si
    # yfinance est indisponible, obtenir_donnees renvoie une marche aléatoire
    # (indicateurs["simule"]=True). On refuse plutôt que d'induire en erreur.
    if md.indicateurs.get("simule"):
        return {"success": False,
                "error": "Données de marché indisponibles (prix simulés) — "
                         "backtest impossible. Réessayez quand la connexion sera rétablie."}

    closes = md.closes
    n = len(closes)
    rsi = Indicateurs.rsi(closes, 14)
    ema_rapide = Indicateurs.ema(closes, 20)
    ema_lente = Indicateurs.ema(closes, 50)

    capital = capital_initial
    position = 0.0          # quantité détenue
    prix_entree = 0.0
    equity_curve = []
    trades = []
    pic = capital_initial
    max_drawdown = 0.0

    for i in range(50, n):
        prix = closes[i]
        r = _val(rsi, i, 50)
        er = _val(ema_rapide, i, prix)
        el = _val(ema_lente, i, prix)

        tendance_haussiere = er > el
        valeur = capital + position * prix

        # Signal d'entrée : survente en tendance haussière
        if position == 0 and r < 35 and tendance_haussiere:
            qte = (capital * 0.95) / prix
            cout = qte * prix * (1 + COMMISSION)
            if cout <= capital:
                position = qte
                prix_entree = prix
                capital -= cout

        # Signal de sortie : surachat OU retournement de tendance
        elif position > 0 and (r > 68 or not tendance_haussiere):
            produit = position * prix * (1 - COMMISSION)
            pnl = produit - position * prix_entree
            pnl_pct = (prix - prix_entree) / prix_entree * 100 if prix_entree else 0
            capital += produit
            trades.append({"entree": round(prix_entree, 4), "sortie": round(prix, 4),
                           "pnl": round(pnl, 2), "pnl_pct": round(pnl_pct, 2)})
            position = 0.0
            prix_entree = 0.0

        valeur = capital + position * prix
        equity_curve.append(round(valeur, 2))
        if valeur > pic:
            pic = valeur
        dd = (valeur - pic) / pic * 100 if pic else 0
        max_drawdown = min(max_drawdown, dd)

    # Clôturer une position ouverte à la fin
    if position > 0:
        prix = closes[-1]
        produit = position * prix * (1 - COMMISSION)
        pnl_pct = (prix - prix_entree) / prix_entree * 100 if prix_entree else 0
        trades.append({"entree": round(prix_entree, 4), "sortie": round(prix, 4),
                       "pnl": round(produit - position * prix_entree, 2),
                       "pnl_pct": round(pnl_pct, 2)})
        capital += produit

    valeur_finale = capital
    rendement = ((valeur_finale - capital_initial) / capital_initial * 100
                 if capital_initial else 0)
    gagnants = [t for t in trades if t["pnl"] > 0]
    taux_reussite = len(gagnants) / len(trades) * 100 if trades else 0

    # Buy & Hold comparatif
    bh = (closes[-1] - closes[50]) / closes[50] * 100 if closes[50] else 0

    return {
        "success": True,
        "symbole": symbole,
        "timeframe": timeframe,
        "capital_initial": capital_initial,
        "valeur_finale": round(valeur_finale, 2),
        "rendement_pct": round(rendement, 2),
        "buy_hold_pct": round(bh, 2),
        "nb_trades": len(trades),
        "taux_reussite_pct": round(taux_reussite, 1),
        "max_drawdown_pct": round(max_drawdown, 2),
        "trades": trades[-20:],
        "equity_curve": equity_curve[::max(1, len(equity_curve) // 100)],
        "avertissement": "Les performances passées ne préjugent pas des performances futures.",
    }
