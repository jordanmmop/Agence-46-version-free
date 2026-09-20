"""Test du moteur de backtest.

On injecte des données RÉELLES synthétiques (déterministes, hors ligne) pour
tester le moteur, et on vérifie séparément le refus des données simulées.
"""
import _setup  # noqa: F401
from utils.backtest import lancer_backtest


def _md_reelle(n: int = 200):
    """MarketData « réelle » (sans le drapeau simule) — série reproductible."""
    import numpy as np
    import pandas as pd
    from models.market_data import MarketData
    rng = np.random.default_rng(42)
    closes = [100.0]
    for _ in range(n - 1):
        closes.append(max(1.0, closes[-1] * (1 + rng.normal(0.001, 0.02))))
    df = pd.DataFrame(
        {
            "Open":   closes,
            "High":   [c * 1.01 for c in closes],
            "Low":    [c * 0.99 for c in closes],
            "Close":  closes,
            "Volume": [1_000_000.0] * n,
        },
        index=pd.date_range(end=pd.Timestamp("2025-01-01"), periods=n, freq="D"),
    )
    return MarketData.depuis_dataframe("BTC-USD", "1d", df)


def run():
    print("═══ Backtest ═══")
    from utils import market_data
    _orig = market_data.FetcheurDonnees.obtenir_donnees
    market_data.FetcheurDonnees.obtenir_donnees = staticmethod(lambda *a, **k: _md_reelle())
    try:
        r = lancer_backtest("BTC-USD", "1d", 10000.0)
        assert r["success"], r
        for champ in ("rendement_pct", "buy_hold_pct", "nb_trades",
                      "taux_reussite_pct", "max_drawdown_pct", "equity_curve"):
            assert champ in r, f"champ manquant : {champ}"
        assert r["capital_initial"] == 10000.0
        assert 0 <= r["taux_reussite_pct"] <= 100
        assert r["max_drawdown_pct"] <= 0  # drawdown négatif ou nul
        assert isinstance(r["equity_curve"], list)
        assert "performances passées" in r["avertissement"].lower()
        print(f"  OK — rendement {r['rendement_pct']}%, {r['nb_trades']} trades, "
              f"réussite {r['taux_reussite_pct']}%")

        # capital 0 ne doit pas provoquer de division par zéro
        r0 = lancer_backtest("BTC-USD", "1d", 0.0)
        assert r0["success"] and r0["rendement_pct"] == 0
        print("  OK — capital 0 sans division par zéro")

        # Données SIMULÉES → backtest REFUSÉ (jamais présenté comme réel)
        md_sim = _md_reelle()
        md_sim.indicateurs["simule"] = True
        market_data.FetcheurDonnees.obtenir_donnees = staticmethod(lambda *a, **k: md_sim)
        rs = lancer_backtest("BTC-USD", "1d", 10000.0)
        assert not rs["success"] and "simul" in rs["error"].lower(), rs
        print("  OK — backtest refuse les données simulées (pas de faux réel)")
    finally:
        market_data.FetcheurDonnees.obtenir_donnees = _orig


if __name__ == "__main__":
    run()
    print("✅ Backtest OK")
