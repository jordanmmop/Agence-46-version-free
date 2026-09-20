"""Tests des protections de trading réel (v2.5.0).

1. Jamais d'ordre sans stop-loss (défaut appliqué)
2. Volume plafonné par le risque (perte au SL ≤ 1% du compte)
3. Anti-empilement (directions_ouvertes) et cooldown
4. Trailing stop : breakeven à +1R puis suivi
"""
import _setup  # noqa: F401
import sys
import time
import types


def _faux_mt5():
    mt5 = types.ModuleType("MetaTrader5")
    mt5.ORDER_TYPE_BUY = 0
    mt5.ORDER_TYPE_SELL = 1
    mt5.TRADE_ACTION_DEAL = 1
    mt5.TRADE_ACTION_SLTP = 2
    mt5.ORDER_TIME_GTC = 0
    mt5.ORDER_FILLING_FOK = 0
    mt5.ORDER_FILLING_IOC = 1
    mt5.ORDER_FILLING_RETURN = 2
    mt5.TRADE_RETCODE_DONE = 10009

    symboles = {
        "BTCUSD": dict(visible=True, trade_mode=4, filling_mode=2,
                       volume_min=0.01, volume_max=50.0, volume_step=0.01,
                       digits=2, trade_tick_size=0.01, trade_tick_value=0.01),
    }
    etat = {"ordres": [], "sltp": [], "positions": []}

    class _Info:
        def __init__(self, name, d):
            self.name = name
            for k, v in d.items():
                setattr(self, k, v)

    class _Tick:
        ask = 60000.0
        bid = 59990.0

    class _Res:
        def __init__(self, retcode, order=0, price=0.0, comment="ok"):
            self.retcode, self.order, self.price, self.comment = retcode, order, price, comment

    mt5.symbol_info = lambda n: _Info(n, symboles[n]) if n in symboles else None
    mt5.symbol_select = lambda n, on: n in symboles
    mt5.symbol_info_tick = lambda n: _Tick() if n in symboles else None
    mt5.symbols_get = lambda: [types.SimpleNamespace(name=n) for n in symboles]
    mt5.positions_get = lambda symbol=None: [p for p in etat["positions"]
                                             if symbol is None or p.symbol == symbol]
    mt5.last_error = lambda: (0, "no error")

    def order_send(req):
        if req["action"] == mt5.TRADE_ACTION_SLTP:
            etat["sltp"].append(req)
        else:
            etat["ordres"].append(req)
        return _Res(mt5.TRADE_RETCODE_DONE, order=111, price=req.get("price", 0))

    mt5.order_send = order_send
    return mt5, etat


def run():
    print("═══ Protections trading réel ═══")
    mt5, etat = _faux_mt5()
    sys.modules["MetaTrader5"] = mt5
    for m in list(sys.modules):
        if m.startswith("utils.mt5_manager") or m.startswith("utils.auto_trader"):
            del sys.modules[m]
    from utils.mt5_manager import MT5Manager
    from utils.auto_trader import completer_sl_tp, AutoTrader

    # 1. SL/TP par défaut (fonction pure de l'auto-trader)
    sl, tp = completer_sl_tp("BUY", 100.0, 0.0, 0.0)
    assert sl == 98.0 and tp == 103.0, (sl, tp)
    sl, tp = completer_sl_tp("SELL", 100.0, 0.0, 0.0)
    assert sl == 102.0 and tp == 97.0, (sl, tp)
    sl, tp = completer_sl_tp("BUY", 100.0, 95.0, 110.0)   # fournis → inchangés
    assert sl == 95.0 and tp == 110.0
    print("  OK — SL/TP par défaut (2%/3%), fournis respectés")

    m = MT5Manager()
    m._lib_available = True
    m._connected = True
    m._account_info = {"balance": 10000, "equity": 10000, "currency": "USD"}

    # 2. Ordre réel SANS stop-loss → le filet de sécurité en met un
    r = m.execute_trade({"symbol": "BTC-USD", "action": "BUY", "volume": 0.03})
    assert r["success"]
    req = etat["ordres"][-1]
    assert req["sl"] > 0 and req["sl"] < 60000, req["sl"]
    assert req["tp"] > 60000, req["tp"]
    print(f"  OK — ordre sans SL → SL forcé à {req['sl']} (jamais nu)")

    # 3. Volume plafonné par le risque : 0.2 lot demandé, perte/lot=1200$,
    #    risque max 1% de 10 000 = 100$ → volume max ≈ 0.083 → 0.08
    r2 = m.execute_trade({"symbol": "BTC-USD", "action": "BUY", "volume": 0.2,
                          "stop_loss": 58800.0})
    assert r2["success"]
    vol = etat["ordres"][-1]["volume"]
    assert vol <= 0.09, f"volume non plafonné : {vol}"
    print(f"  OK — volume demandé 0.2 → plafonné à {vol} (risque 1%)")

    # 4. Anti-empilement : position BUY ouverte → directions_ouvertes la voit
    etat["positions"].append(types.SimpleNamespace(
        symbol="BTCUSD", type=0, magic=234_000,
        price_open=60000.0, sl=58800.0, tp=61800.0,
        price_current=60100.0, ticket=42, volume=0.05))
    assert "BUY" in m.directions_ouvertes("BTC-USD")
    print("  OK — anti-empilement : position BUY détectée")

    # 5. Cooldown
    at = AutoTrader()
    at._cooldown_s = 1
    at._derniers_trades["BTC-USD"] = {"action": "BUY", "ts": time.time()}
    assert at._cooldown_actif("BTC-USD", "BUY")
    assert not at._cooldown_actif("BTC-USD", "SELL")
    time.sleep(1.1)
    assert not at._cooldown_actif("BTC-USD", "BUY")
    print("  OK — cooldown actif puis expiré, sens opposé libre")

    # 6. Trailing : gain +2R → SL remonté au-dessus du breakeven
    etat["positions"][0].price_current = 62400.0   # gain 2400 = 2R (R=1200)
    m.gerer_trailing()
    assert etat["sltp"], "aucune modification SL envoyée"
    nouveau = etat["sltp"][-1]["sl"]
    assert nouveau >= 60000.0, nouveau   # au moins breakeven
    print(f"  OK — trailing : SL remonté à {nouveau} (breakeven+)")

    # 7. P&L réel : agrégation des deals de clôture MT5
    import time as _t
    mt5.history_deals_get = lambda a, b: [
        types.SimpleNamespace(entry=1, profit=100.0, commission=-2.0, swap=0.0,
                              time=_t.time(), symbol="BTCUSD"),
        types.SimpleNamespace(entry=1, profit=-40.0, commission=-2.0, swap=-1.0,
                              time=_t.time(), symbol="BTCUSD"),
        types.SimpleNamespace(entry=0, profit=0.0, commission=0.0, swap=0.0,
                              time=_t.time(), symbol="BTCUSD"),  # ouverture: ignorée
    ]
    h = m.get_historique_reel(7)
    assert h["synced"] and h["nb_trades"] == 2, h
    assert h["pnl_total"] == 55.0, h["pnl_total"]        # 98 - 43
    assert h["gagnants"] == 1 and h["perdants"] == 1
    assert h["taux_reussite_pct"] == 50.0
    assert len(h["par_jour"]) == 1
    print(f"  OK — P&L réel agrégé : {h['pnl_total']} USD sur {h['nb_trades']} trades")

    # 8b. AUDIT H1 : risque > budget même au lot min → ordre REFUSÉ
    #     (equity 100$, risque 1% = 1$, perte/lot BTCUSD = 1200$ → refus)
    m._account_info = {"balance": 100, "equity": 100, "currency": "USD"}
    r_refus = m.execute_trade({"symbol": "BTC-USD", "action": "BUY",
                               "volume": 0.01, "stop_loss": 58800.0})
    assert not r_refus["success"] and "lot minimum" in r_refus["error"], r_refus
    m._account_info = {"balance": 10000, "equity": 10000, "currency": "USD"}
    print("  OK — risque intenable au lot minimum → ordre refusé (pas de dépassement)")

    # 8c. AUDIT H2 : SL du mauvais côté après gap → recalculé côté correct
    r_gap = m.execute_trade({"symbol": "BTC-USD", "action": "BUY",
                             "volume": 0.03, "stop_loss": 61000.0})  # SL AU-DESSUS du prix
    assert r_gap["success"]
    req_gap = etat["ordres"][-1]
    assert req_gap["sl"] < 60000.0, f"SL non corrigé : {req_gap['sl']}"
    print(f"  OK — SL au-dessus du prix (gap) → recalculé à {req_gap['sl']}")

    # 8d. AUDIT M1 : fermeture par ticket (hedging) — position ciblée
    pos = etat["positions"][0]
    r_close = m.fermer_position(pos)
    assert r_close["success"], r_close
    assert etat["ordres"][-1].get("position") == pos.ticket, "fermeture sans ticket !"
    print("  OK — fermeture par ticket (compte hedging géré)")

    # 8e. AUDIT M2 : équité illisible → ordre REFUSÉ, sans latcher de référence
    # ni armer le kill-switch.
    #  - refus : sans équité, _plafonner_volume_risque ne peut PAS appliquer le
    #    plafond « perte au stop ≤ X% » — laisser passer l'ordre reviendrait à
    #    l'envoyer sans aucun dimensionnement du risque.
    #  - pas de kill : get_portfolio() renvoie {"synced": False} dès que le
    #    compte est déconnecté ; lire cette absence comme une perte de 100%
    #    coupait le trading de la journée (état persisté) sur un simple clic.
    from utils.risk_guard import RiskGuard
    g = RiskGuard()
    g.perte_max_pct = 5.0     # explicite : le défaut livré est un réglage utilisateur
    ok0, raison0 = g.evaluer({"equity": 0, "nb_positions": 0, "exposition_pct": 0})
    assert not ok0 and "quité" in raison0, raison0
    assert "Kill-switch" not in raison0, "kill-switch armé à tort sur équité nulle"
    assert not g._kill, "kill-switch persistant déclenché par une équité illisible"
    assert g._equity_debut == 0.0 and g._jour is None
    # Portefeuille non synchronisé (compte déconnecté) : même traitement.
    ok_ns, raison_ns = g.evaluer({"synced": False})
    assert not ok_ns and not g._kill, raison_ns
    # La première équité VALIDE fixe la référence et réarme le fonctionnement.
    okv, _ = g.evaluer({"equity": 10000, "nb_positions": 0, "exposition_pct": 0})
    assert okv, "ordre refusé alors que l'équité est valide"
    assert g._equity_debut == 10000, "référence non fixée sur équité valide"
    okk, raison = g.evaluer({"equity": 9000, "nb_positions": 0, "exposition_pct": 0})
    assert not okk and "Kill-switch" in raison
    print("  OK — équité illisible : ordre refusé SANS armer le kill-switch")

    # 8. Rapport quotidien : contenu cohérent depuis une base temporaire
    from utils.auto_trader import construire_rapport_quotidien
    from utils.database import Database
    from pathlib import Path
    import tempfile
    from datetime import datetime
    db = Database(Path(tempfile.mkstemp(suffix=".db")[1]))
    db.sauver_ordre({"symbol": "BTC-USD", "action": "BUY", "volume": 0.01,
                     "price": 60000, "ticket": 1, "success": True, "simulated": True})
    jour = datetime.now().strftime("%Y-%m-%d")
    rapport = construire_rapport_quotidien(jour, db=db)
    assert jour in rapport and "Ordres : 1" in rapport, rapport
    print("  OK — rapport quotidien généré avec les ordres du jour")

    # 9. Badge mode : une connexion RÉELLE MT5 ne doit pas afficher "simulation"
    import utils.mt5_manager as _mm
    from utils.auto_trader import get_auto_trader

    class _FakeReel:
        _lib_available = True
        def get_journal(self, n): return []
        def get_status(self): return {"connected": True,
                                      "account_info": {"account": "1", "simulated": False}}
    _orig = _mm.get_mt5_manager
    _mm.get_mt5_manager = lambda: _FakeReel()
    try:
        st = get_auto_trader().get_status()
        assert st["mode"] == "reel", st["mode"]

        class _FakeNoLib:
            _lib_available = False
            def get_journal(self, n): return []
            def get_status(self): return {"connected": False}
        _mm.get_mt5_manager = lambda: _FakeNoLib()
        st2 = get_auto_trader().get_status()
        assert st2["mode"] == "simulation" and st2["mode_raison"]
    finally:
        _mm.get_mt5_manager = _orig
    print("  OK — connexion réelle → badge RÉEL ; sinon simulation + diagnostic")

    # 10. Mémorisation des identifiants : obfuscation réversible, jamais en clair
    from utils.mt5_manager import MT5Manager as _MM2
    ob = _MM2._obfusquer("MotDePasse!42")
    assert ob != "MotDePasse!42" and _MM2._desobfusquer(ob) == "MotDePasse!42"
    assert "MotDePasse" not in ob
    print("  OK — identifiants mémorisés obfusqués (mot de passe jamais en clair)")

    # 11. AUDIT H3 : est_reel() distingue compte RÉEL / simulé / hors-ligne
    #     (sert à interdire tout ordre réel fondé sur des données simulées)
    m3 = MT5Manager()
    m3._lib_available = True
    m3._connected = True
    m3._account_info = {"equity": 10000, "simulated": False}
    assert m3.est_reel() is True
    m3._account_info = {"equity": 10000, "simulated": True}
    assert m3.est_reel() is False
    m3._connected = False
    assert m3.est_reel() is False
    print("  OK — est_reel() : compte réel vs simulé/hors-ligne")

    # 12. AUDIT H3 : l'orchestrateur marque « source_simulee » quand yfinance
    #     est indisponible (prix inventés) → l'auto-trader refusera le réel.
    from agents.orchestrateur import ChefOrchestre
    chef = ChefOrchestre()
    d_sim = chef._prendre_decision("BTC-USD", [], {"indicateurs": {"simule": True},
                                                   "prix_actuel": 60000})
    assert d_sim["signal_final"].donnees.get("source_simulee") is True, d_sim
    d_reel = chef._prendre_decision("BTC-USD", [], {"indicateurs": {"rsi": 55},
                                                    "prix_actuel": 60000})
    assert d_reel["signal_final"].donnees.get("source_simulee") is False, d_reel
    print("  OK — décision marquée source_simulee quand données yfinance simulées")

    # 13. AUDIT H2 : /api/mt5/trade — une « fermeture » ferme PAR TICKET et
    #     n'OUVRE jamais de position (execute_trade ouvrirait sur BUY/SELL).
    import asyncio
    import utils.mt5_manager as _mmod
    import utils.risk_guard as _rmod

    class _FakeTradeMgr:
        def __init__(self):
            self.ferme = None
            self.ouvert = None
        def fermer_ticket(self, t):
            self.ferme = t
            return {"success": True, "ticket": t}
        def execute_trade(self, sig):
            self.ouvert = sig
            return {"success": True}
        def get_portfolio(self):
            return {"equity": 10000, "nb_positions": 0, "exposition_pct": 0}

    class _RiskOK:
        def evaluer(self, _p):
            return (True, "")

    fake = _FakeTradeMgr()
    _o1, _o2 = _mmod.get_mt5_manager, _rmod.get_risk_guard
    _mmod.get_mt5_manager = lambda: fake
    _rmod.get_risk_guard = lambda: _RiskOK()
    try:
        from backend.main import mt5_trade
        # fermeture d'une position BUY : ferme le ticket, n'ouvre RIEN
        r = asyncio.run(mt5_trade({"action": "BUY", "fermeture": True, "ticket": 42}))
        assert r["success"] and fake.ferme == 42 and fake.ouvert is None, (r, fake.ferme, fake.ouvert)
        # fermeture sans ticket → refus explicite
        r2 = asyncio.run(mt5_trade({"action": "BUY", "fermeture": True}))
        assert not r2["success"] and "Ticket" in r2["error"], r2
        # ouverture normale → execute_trade appelé
        r3 = asyncio.run(mt5_trade({"action": "BUY", "symbol": "BTC-USD", "volume": 0.01}))
        assert r3["success"] and fake.ouvert is not None, (r3, fake.ouvert)
    finally:
        _mmod.get_mt5_manager, _rmod.get_risk_guard = _o1, _o2
    print("  OK — fermeture ferme par ticket, n'ouvre jamais (garde-fous préservés)")

    # 14. AUDIT B4 : résolution de symbole — refuse un préfixe ambigu
    #     (ne JAMAIS ouvrir un ordre réel sur le mauvais instrument).
    _oi, _og, _os = mt5.symbol_info, mt5.symbols_get, mt5.symbol_select
    mt5.symbol_info = lambda n: None            # aucun candidat direct
    mt5.symbol_select = lambda n, on: True
    mt5.symbols_get = lambda: [types.SimpleNamespace(name="US300"),
                               types.SimpleNamespace(name="BABA")]
    m._symbol_cache.clear()
    assert m.resoudre_symbole("US30") is None   # US30 → US300 refusé (reste « 0 »)
    assert m.resoudre_symbole("BA") is None      # BA → BABA refusé (reste « BA »)
    m._symbol_cache.clear()
    mt5.symbols_get = lambda: [types.SimpleNamespace(name="US30"),
                               types.SimpleNamespace(name="US300")]
    assert m.resoudre_symbole("US30") == "US30"  # correspondance EXACTE acceptée
    mt5.symbol_info, mt5.symbols_get, mt5.symbol_select = _oi, _og, _os
    m._symbol_cache.clear()
    print("  OK — résolution symbole : exact accepté, préfixe ambigu refusé (B4)")

    # 15. AUDIT B15 : fermer_ticket accepte un ticket en CHAÎNE (JSON/URL)
    etat["ordres"].clear()
    r_str = m.fermer_ticket("42")   # position ticket 42 ajoutée au test 4
    assert r_str.get("success"), r_str
    assert etat["ordres"][-1].get("position") == 42
    print("  OK — fermer_ticket : ticket en chaîne accepté (B15)")

    # 16. AUDIT B12 : volume plafonné par le risque JAMAIS arrondi au-dessus
    #     du cap (floor). perte/lot=1200$, risque 1% de 10000=100$ →
    #     0.0833 → arrondi VERS LE BAS au pas 0.01 = 0.08 (et non 0.09).
    etat["ordres"].clear()
    m._account_info = {"balance": 10000, "equity": 10000, "currency": "USD"}
    r_floor = m.execute_trade({"symbol": "BTC-USD", "action": "BUY",
                               "volume": 0.2, "stop_loss": 58800.0})
    assert r_floor["success"]
    vol_floor = etat["ordres"][-1]["volume"]
    assert vol_floor == 0.08, f"floor attendu 0.08, obtenu {vol_floor}"
    print(f"  OK — volume plafonné arrondi vers le bas : {vol_floor} (jamais au-dessus du cap, B12)")

    # 17. REVUE : résolution de symbole — règle du SÉPARATEUR.
    #     Doit refuser un AUTRE instrument (BA→BAC, T→TM, V→VZ) ET accepter
    #     les décorations légitimes du courtier (AAPL.us, EURUSDm…).
    _oi, _og, _os = mt5.symbol_info, mt5.symbols_get, mt5.symbol_select
    mt5.symbol_info = lambda n: None
    mt5.symbol_select = lambda n, on: True

    def _resol(yahoo, dispo):
        mt5.symbols_get = lambda: [types.SimpleNamespace(name=x) for x in dispo]
        m._symbol_cache.clear()
        return m.resoudre_symbole(yahoo)

    for y, d in [("BA", ["BAC", "AAPL"]), ("T", ["TM"]), ("V", ["VZ"]),
                 ("US30", ["US300"]), ("BA", ["BABA"])]:
        assert _resol(y, d) is None, f"{y} → mauvais instrument {d} !"
    for y, d, att in [("AAPL", ["AAPL.us"], "AAPL.us"),
                      ("EURUSD", ["EURUSD.a"], "EURUSD.a"),
                      ("XAUUSD", ["XAUUSD.sd"], "XAUUSD.sd"),
                      ("US500", ["US500.cfd"], "US500.cfd"),
                      ("EURUSD", ["EURUSDm"], "EURUSDm"),
                      ("EURUSD", ["EURUSD_i"], "EURUSD_i")]:
        assert _resol(y, d) == att, f"{y} : décoration légitime {d} refusée"
    assert _resol("EURUSD", ["EURUSD.a", "EURUSD.b"]) is None, "ambiguïté acceptée"
    mt5.symbol_info, mt5.symbols_get, mt5.symbol_select = _oi, _og, _os
    m._symbol_cache.clear()
    print("  OK — symbole : autre instrument refusé, décoration courtier acceptée")

    # 18. REVUE : action invalide → JAMAIS un SELL réel par défaut
    r_inv = m.execute_trade({"symbol": "BTC-USD", "action": "CLOSE", "volume": 0.01})
    assert not r_inv["success"] and "invalide" in r_inv["error"].lower(), r_inv
    print("  OK — action inconnue refusée (jamais de SELL réel par défaut)")

    # 19. REVUE : le floor du volume ne perd plus une marche (imprécision float)
    etat["ordres"].clear()
    m._account_info = {"balance": 1_000_000, "equity": 1_000_000, "currency": "USD"}
    r_vol = m.execute_trade({"symbol": "BTC-USD", "action": "BUY",
                             "volume": 0.29, "stop_loss": 59900.0})
    assert r_vol["success"], r_vol
    assert etat["ordres"][-1]["volume"] == 0.29, \
        f"volume rabaissé par l'imprécision flottante : {etat['ordres'][-1]['volume']}"
    m._account_info = {"balance": 10000, "equity": 10000, "currency": "USD"}
    print("  OK — volume 0.29 conservé (plus de marche perdue au floor)")

    del sys.modules["MetaTrader5"]


if __name__ == "__main__":
    run()
    print("✅ Protections trading réel OK")
