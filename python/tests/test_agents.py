"""Tests des 46 agents + 46 assistants et de la sérialisation JSON."""
import _setup  # noqa: F401
import json
import random

from agents import TOUS_LES_AGENTS
from agents.orchestrateur import ChefOrchestre
from models.signal import Signal, ActionSignal


def _donnees_factices():
    random.seed(42)
    prix = 100.0
    closes = []
    for _ in range(120):
        prix *= 1 + random.uniform(-0.01, 0.012)
        closes.append(prix)
    return {
        "closes": closes,
        "highs":  [c * 1.005 for c in closes],
        "lows":   [c * 0.995 for c in closes],
        "volumes": [1000.0] * 120,
        "opens":  closes,
        "prix_actuel": closes[-1],
        "variation_24h": 1.2,
        "indicateurs": {},
        "portfolio": {"valeur_totale": 100000, "capital_disponible": 100000, "positions": {}},
        "historique_valeur": [],
        "trades": [],
        "info": {},
    }


def run():
    print("═══ Agents & assistants ═══")
    assert len(TOUS_LES_AGENTS) == 45
    ids = {a.assistant.id for a in TOUS_LES_AGENTS}
    assert len(ids) == 45
    chef = ChefOrchestre()
    assert chef.assistant.id == "AST-ORCH-000"
    print("  OK — 46 agents, 46 assistants uniques")

    donnees = _donnees_factices()
    for a in TOUS_LES_AGENTS:
        s = a.analyser("TEST-USD", donnees)
        assert isinstance(s, Signal)
        assert "assistant" in (s.donnees or {})
        json.dumps(a.to_dict())
    print("  OK — 45 signaux préparés + vérifiés, to_dict() sérialisable")

    # L'application est 100 % LOCALE : pendant une analyse, AUCUN agent ne doit
    # ouvrir de connexion vers une machine distante. Le test portait avant sur
    # le seul client Anthropic ; il ne prouvait donc rien d'une bibliothèque
    # tierce qui appellerait un service par un autre chemin. On coupe désormais
    # le réseau lui-même, en n'autorisant que la boucle locale — là où tournent
    # les moteurs IA embarqués.
    import socket as _socket

    def _est_local(hote) -> bool:
        return str(hote) in ("127.0.0.1", "::1", "localhost", "0.0.0.0", "")

    _vraie_connexion = _socket.socket.connect
    _vraie_creation = _socket.create_connection

    def _connect_bride(self, adresse, *a, **kw):
        if isinstance(adresse, tuple) and not _est_local(adresse[0]):
            raise AssertionError(
                f"un agent a tenté une connexion DISTANTE vers {adresse[0]} — "
                f"l'application doit fonctionner entièrement hors ligne")
        return _vraie_connexion(self, adresse, *a, **kw)

    def _create_connection_bride(adresse, *a, **kw):
        if isinstance(adresse, tuple) and not _est_local(adresse[0]):
            raise AssertionError(
                f"un agent a tenté une connexion DISTANTE vers {adresse[0]} — "
                f"l'application doit fonctionner entièrement hors ligne")
        return _vraie_creation(adresse, *a, **kw)

    _socket.socket.connect = _connect_bride
    _socket.create_connection = _create_connection_bride
    try:
        for a in TOUS_LES_AGENTS:
            assert isinstance(a.analyser("TEST-USD", donnees), Signal)
    finally:
        _socket.socket.connect = _vraie_connexion
        _socket.create_connection = _vraie_creation
    print("  OK — aucun agent n'ouvre de connexion distante (100 % hors ligne)")

    # régression numpy
    import numpy as np
    sig = Signal(agent_id="X", agent_nom="X", symbole="T", action=ActionSignal.ACHAT,
                 confiance=np.float64(88.5),
                 donnees={"b": np.bool_(True), "n": np.int64(7), "arr": np.array([1.0, 2.0])})
    out = sig.to_dict()
    json.dumps(out)
    assert out["donnees"]["b"] is True and out["donnees"]["n"] == 7
    print("  OK — types numpy convertis (régression /api/agents)")

    # correction SL/TP inversés
    ast = TOUS_LES_AGENTS[0].assistant
    s2 = Signal(agent_id="X", agent_nom="X", symbole="T", action=ActionSignal.ACHAT,
                confiance=80, prix_entree=100.0, stop_loss=110.0, take_profit=90.0)
    s2 = ast.verifier_signal(s2, donnees)
    assert s2.stop_loss == 90.0 and s2.take_profit == 110.0
    print("  OK — assistant corrige les SL/TP inversés")

    # ── Correctifs de correction (ADX lissé, Position short, ML features) ──
    from agents.analyse_marche.agent_momentum import AgentMomentum
    # ADX de Wilder : sur une tendance nette, ADX élevé et borné [0,100]
    highs = [100 + i * 0.5 for i in range(60)]
    lows = [h - 1 for h in highs]
    cl = [h - 0.5 for h in highs]
    adx, dip, dim = AgentMomentum._adx(highs, lows, cl)
    assert 0 <= adx <= 100 and dip > dim and adx > 40, (adx, dip, dim)
    # marché plat → ADX faible (le DX instantané aurait été bruité)
    plat = [100.0] * 60
    adx0, _, _ = AgentMomentum._adx(plat, plat, plat)
    assert 0 <= adx0 <= 100
    print(f"  OK — ADX de Wilder lissé (trend {adx:.0f}, plat {adx0:.0f})")

    # AUDIT : ordre DÉGÉNÉRÉ (SL == TP == prix d'entrée).
    # min(closes[-5:]) inclut la bougie courante : si la dernière clôture est
    # l'extrême de la fenêtre, le stop tombait exactement sur le prix d'entrée
    # et un ordre sans protection partait à 85 % de confiance.
    cl = [100 * (1.01 ** i) for i in range(60)]
    cl[-1] = min(cl[-5:]) * 0.999          # repli final
    dn = {"closes": cl, "highs": [c * 1.004 for c in cl], "lows": [c * 0.996 for c in cl],
          "volumes": [1e6] * 60, "prix_actuel": cl[-1],
          "portfolio": {"valeur_totale": 10000}, "indicateurs": {}}
    sm = AgentMomentum()._analyser("TEST", dn, {})
    if sm.action in (ActionSignal.ACHAT, ActionSignal.VENTE):
        assert sm.stop_loss != sm.prix_entree, "stop-loss confondu avec le prix d'entrée"
        assert sm.take_profit != sm.prix_entree, "take-profit confondu avec le prix"
        assert sm.stop_loss != sm.take_profit, "SL == TP"
    print("  OK — momentum : plus de SL/TP confondus avec le prix d'entrée")

    # Dernier rempart, valable pour TOUS les agents : l'assistant neutralise
    # un ordre dégénéré même si un agent en produit un.
    sdeg = Signal(agent_id="X", agent_nom="X", symbole="T", action=ActionSignal.ACHAT,
                  confiance=85, prix_entree=100.0, stop_loss=100.0, take_profit=100.0)
    sdeg = ast.verifier_signal(sdeg, donnees)
    assert sdeg.stop_loss is None and sdeg.take_profit is None, sdeg
    assert sdeg.confiance <= 40, sdeg.confiance
    ssain = Signal(agent_id="X", agent_nom="X", symbole="T", action=ActionSignal.ACHAT,
                   confiance=80, prix_entree=100.0, stop_loss=98.0, take_profit=105.0)
    ssain = ast.verifier_signal(ssain, donnees)
    assert ssain.stop_loss == 98.0 and ssain.take_profit == 105.0, "ordre sain altéré"
    print("  OK — assistant : ordre dégénéré neutralisé, ordre sain préservé")

    # AUDIT : volatilité annualisée sur le VRAI pas de temps (bougies 1h).
    # Une série 1h de vraie vol ~50 % doit ressortir ~50 %, pas ~17 %.
    from utils.indicators import Indicateurs
    _rng = np.random.default_rng(7)
    sig_h = 0.50 / np.sqrt(252 * 6.5)
    px = [100.0]
    for r in _rng.normal(0, sig_h, 400):
        px.append(px[-1] * np.exp(r))
    vh = Indicateurs.volatilite_historique(px, 20)
    assert 35 < vh < 65, f"volatilité 1h mal annualisée : {vh:.1f}%"
    print(f"  OK — volatilité annualisée sur bougies 1h ({vh:.0f}% pour 50% réel)")

    # AUDIT : agent_arbitrage ne fabrique plus d'ordre à partir d'un tirage
    # aléatoire quand les prix comparatifs sont absents.
    from agents.strategies.agent_arbitrage import AgentArbitrage
    darb = {"closes": [100 + i * 0.1 for i in range(60)], "prix_actuel": 106.0,
            "portfolio": {"valeur_totale": 10000}, "indicateurs": {},
            "highs": [101] * 60, "lows": [99] * 60, "volumes": [1e6] * 60}
    actions_arb = {AgentArbitrage()._analyser("T", darb, {}).action for _ in range(60)}
    assert actions_arb <= {ActionSignal.HOLD, ActionSignal.SURVEILLER}, actions_arb
    print("  OK — arbitrage : plus d'ordre issu d'un tirage aléatoire")

    # Position SHORT : P&L latent de signe correct (gagne quand le prix baisse)
    from models.portfolio import Position
    from models.trade import DirectionTrade
    pl = Position("X", 10, 100.0); pl.mettre_a_jour(110.0)
    assert pl.pnl_latent == 100.0
    ps = Position("X", 10, 100.0, direction=DirectionTrade.SHORT)
    ps.mettre_a_jour(90.0);  assert ps.pnl_latent == 100.0    # baisse → gain
    ps.mettre_a_jour(110.0); assert ps.pnl_latent == -100.0   # hausse → perte
    print("  OK — Position SHORT : signe du P&L latent corrigé")

    # ML : vol_ratio sans division par zéro sur marché plat ; features utilisées
    from agents.data_intelligence.agent_ml_predictor import AgentMLPredicteur
    ml = AgentMLPredicteur()
    feat = ml._extraire_features([100.0] * 30, [100.0] * 30, [100.0] * 30, [1e6] * 30)
    assert feat["vol_ratio"] == 1.0
    # rsi survendu tempère une prédiction baissière (features load-bearing)
    base_cl = [100 - i for i in range(20)]     # tendance baissière
    p_neutre, _ = ml._regression_lineaire(base_cl, {"rsi": 50, "momentum": 0, "vol_ratio": 1})
    p_survendu, _ = ml._regression_lineaire(base_cl, {"rsi": 20, "momentum": 0, "vol_ratio": 1})
    assert p_survendu > p_neutre, (p_neutre, p_survendu)   # RSI bas atténue la baisse
    print("  OK — ML : vol_ratio protégé + features réellement utilisées")


if __name__ == "__main__":
    run()
    print("✅ Agents OK")
