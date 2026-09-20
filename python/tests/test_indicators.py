"""Tests des indicateurs techniques (alimentent les 45 agents).

Vérifie le contrat de longueur (série == longueur de l'entrée, padding None
en tête) sur données normales et insuffisantes, plus quelques arêtes.
"""
import _setup  # noqa: F401
import random

from utils.indicators import Indicateurs as I


def _serie(n=200, seed=1):
    random.seed(seed)
    prix = [100.0]
    for _ in range(n - 1):
        prix.append(prix[-1] * (1 + random.uniform(-0.01, 0.012)))
    highs = [p * 1.005 for p in prix]
    lows = [p * 0.995 for p in prix]
    return prix, highs, lows


def run():
    print("═══ Indicateurs techniques ═══")
    prix, highs, lows = _serie(200)

    # Contrat de longueur sur données normales ET insuffisantes
    for n in (200, 30, 5, 1):
        px, hi, lo = prix[:n], highs[:n], lows[:n]
        assert len(I.sma(px, 20)) == n
        assert len(I.ema(px, 20)) == n
        assert len(I.rsi(px, 14)) == n
        m, s, h = I.macd(px)
        assert len(m) == len(s) == len(h) == n, f"macd n={n}"
        u, mid, l = I.bollinger(px, 20)
        assert len(u) == len(mid) == len(l) == n, f"bollinger n={n}"
        assert len(I.atr(hi, lo, px, 14)) == n
        k, d = I.stochastique(hi, lo, px)
        assert len(k) == len(d) == n, f"stoch n={n}"
    print("  OK — longueur == entrée pour les 7 indicateurs (n=200,30,5,1)")

    # RSI sur prix parfaitement plats → 50 (neutre), pas 100
    r_plat = I.rsi([100.0] * 30, 14)
    val = next((v for v in reversed(r_plat) if v is not None), None)
    assert val == 50.0, f"RSI plat = {val}"
    print("  OK — RSI prix plats = 50 (neutre)")

    # MACD : histogramme == macd - signal partout où défini
    m, s, h = I.macd(prix)
    for mm, ss, hh in zip(m, s, h):
        if mm is not None and ss is not None:
            assert abs(hh - (mm - ss)) < 1e-9
    print("  OK — MACD histogramme = ligne MACD - signal")

    # ATR strictement positif en régime normal, Bollinger sup > inf
    atr = I.atr(highs, lows, prix, 14)
    assert next(v for v in reversed(atr) if v is not None) > 0
    u, mid, l = I.bollinger(prix, 20)
    i = next(k for k in range(len(u) - 1, -1, -1) if u[k] is not None)
    assert u[i] > mid[i] > l[i]
    print("  OK — ATR positif, bandes Bollinger ordonnées")


if __name__ == "__main__":
    run()
    print("✅ Indicateurs OK")
