"""Test du chemin d'exécution RÉEL MT5 avec un faux module MetaTrader5.

Reproduit le bug corrigé en v2.0.1 : un symbole Yahoo (BTC-USD) que le
courtier ne connaît pas était silencieusement simulé ; il est désormais
traduit en BTCUSD et réellement envoyé, ou refusé avec une erreur claire.
"""
import _setup  # noqa: F401
import sys
import types


def _faux_mt5():
    mt5 = types.ModuleType("MetaTrader5")
    mt5.ORDER_TYPE_BUY = 0
    mt5.ORDER_TYPE_SELL = 1
    mt5.TRADE_ACTION_DEAL = 1
    mt5.ORDER_TIME_GTC = 0
    mt5.ORDER_FILLING_FOK = 0
    mt5.ORDER_FILLING_IOC = 1
    mt5.ORDER_FILLING_RETURN = 2
    mt5.TRADE_RETCODE_DONE = 10009

    # Un vrai courtier expose la valeur du tick : c'est ce qui permet de
    # plafonner la perte au stop. « SANSSPEC » simule le cas où il ne l'expose
    # pas (symbole absent du Market Watch) — l'ordre doit alors être REFUSÉ,
    # et non envoyé au volume brut sans aucun plafond.
    symboles = {
        "BTCUSD": dict(visible=False, trade_mode=4, filling_mode=2,
                       volume_min=0.01, volume_max=50.0, volume_step=0.01,
                       trade_tick_size=0.01, trade_tick_value=0.01,
                       trade_contract_size=1.0),
        "EURUSD": dict(visible=True, trade_mode=4, filling_mode=1,
                       volume_min=0.01, volume_max=100.0, volume_step=0.01,
                       trade_tick_size=0.00001, trade_tick_value=1.0,
                       trade_contract_size=100000.0),
        "AAPL":   dict(visible=False, trade_mode=4, filling_mode=2,
                       volume_min=0.1, volume_max=500.0, volume_step=0.1,
                       trade_tick_size=0.01, trade_tick_value=0.01,
                       trade_contract_size=1.0),
        "SANSSPEC": dict(visible=True, trade_mode=4, filling_mode=2,
                         volume_min=0.01, volume_max=50.0, volume_step=0.01),
        # Matières premières telles que le courtier les nomme (Market Watch
        # AvaTrade). Aucune transformation de « GC=F » ou « CL=F » ne mène à
        # ces noms : seule la table d'alias de config.py le peut.
        "GOLD.TR": dict(visible=False, trade_mode=4, filling_mode=2,
                        volume_min=0.01, volume_max=50.0, volume_step=0.01,
                        trade_tick_size=0.01, trade_tick_value=0.01,
                        trade_contract_size=100.0),
        "CrudeOIL": dict(visible=False, trade_mode=4, filling_mode=2,
                         volume_min=0.01, volume_max=50.0, volume_step=0.01,
                         trade_tick_size=0.01, trade_tick_value=0.10,
                         trade_contract_size=1000.0),
        # LEURRE : Colgate-Palmolive, le vrai « CL » du courtier. Résoudre
        # « CL=F » (pétrole) par sa racine enverrait l'ordre ICI.
        "CL": dict(visible=True, trade_mode=4, filling_mode=2,
                   volume_min=0.1, volume_max=500.0, volume_step=0.1,
                   trade_tick_size=0.01, trade_tick_value=0.01,
                   trade_contract_size=1.0),
    }
    etat = {"ordres": []}

    class _Info:
        def __init__(self, name, d):
            self.name = name
            for k, v in d.items():
                setattr(self, k, v)

    class _Tick:
        ask = 60000.0
        bid = 59990.0

    class _Sym:
        def __init__(self, name):
            self.name = name

    class _Res:
        def __init__(self, retcode, order=0, price=0.0, comment="ok"):
            self.retcode, self.order, self.price, self.comment = retcode, order, price, comment

    mt5.symbol_info = lambda n: _Info(n, symboles[n]) if n in symboles else None
    mt5.symbol_select = lambda n, on: (symboles.__setitem__(n, {**symboles[n], "visible": on}) or True) if n in symboles else False
    mt5.symbol_info_tick = lambda n: _Tick() if n in symboles else None
    mt5.symbols_get = lambda: [_Sym(n) for n in symboles]
    mt5.last_error = lambda: (0, "no error")

    def order_send(req):
        # AAPL refuse IOC (retcode 10030) → doit basculer sur RETURN
        if req["symbol"] == "AAPL" and req["type_filling"] == mt5.ORDER_FILLING_IOC:
            return _Res(10030, comment="Unsupported filling mode")
        etat["ordres"].append(req)
        return _Res(mt5.TRADE_RETCODE_DONE, order=987654, price=req["price"])

    mt5.order_send = order_send
    return mt5, etat


def run():
    print("═══ Exécution MT5 réelle ═══")
    mt5, etat = _faux_mt5()
    sys.modules["MetaTrader5"] = mt5

    for m in list(sys.modules):
        if m.startswith("utils.mt5_manager"):
            del sys.modules[m]
    from utils.mt5_manager import MT5Manager

    m = MT5Manager()
    m._lib_available = True
    m._connected = True
    # equity : indispensable au plafonnement du risque. Sans elle, l'ordre est
    # refusé (on ne peut pas garantir « perte au stop ≤ X% » sans équité).
    # Volontairement large : ce module teste la RÉSOLUTION des symboles et la
    # bascule de mode de remplissage, pas le plafond de risque — qui a ses
    # propres tests dans test_trading_reel. Une équité trop faible ferait
    # refuser les ordres avant d'atteindre ce qu'on veut vérifier ici.
    m._account_info = {"balance": 100000, "equity": 100000, "currency": "USD"}

    assert m.resoudre_symbole("BTC-USD") == "BTCUSD"
    assert m.resoudre_symbole("EURUSD=X") == "EURUSD"
    assert m.resoudre_symbole("INEXISTANT-XYZ") is None
    print("  OK — résolution Yahoo → courtier")

    # Matières premières : le ticker de cours ne ressemble PAS au nom du
    # courtier. La table d'alias doit viser l'instrument exact — et surtout
    # pas la racine du ticker : « CL » existe chez ce courtier (Colgate).
    assert m.resoudre_symbole("GC=F") == "GOLD.TR"
    assert m.resoudre_symbole("CL=F") == "CrudeOIL", (
        "le pétrole doit se résoudre en CrudeOIL, jamais en « CL » "
        "(Colgate-Palmolive) — ce serait un ordre RÉEL sur un autre "
        "instrument")
    print("  OK — or et pétrole résolus par alias (pas par racine du ticker)")

    r_or = m.execute_trade({"symbol": "GC=F", "action": "BUY", "volume": 0.02,
                            "stop_loss": 59000, "take_profit": 62000})
    assert r_or["success"] and r_or["symbol"] == "GOLD.TR", r_or
    assert etat["ordres"][-1]["symbol"] == "GOLD.TR"
    print("  OK — ordre sur l'or envoyé sous le nom du courtier (GOLD.TR)")

    r = m.execute_trade({"symbol": "BTC-USD", "action": "BUY", "volume": 0.03,
                         "stop_loss": 59000, "take_profit": 62000})
    assert r["success"] and not r.get("simulated") and r["symbol"] == "BTCUSD"
    assert etat["ordres"][-1]["symbol"] == "BTCUSD"
    print("  OK — ordre réel envoyé au courtier (plus de fausse simulation)")

    r2 = m.execute_trade({"symbol": "AAPL", "action": "BUY", "volume": 0.15})
    assert r2["success"] and etat["ordres"][-1]["type_filling"] == mt5.ORDER_FILLING_RETURN
    print("  OK — bascule de mode de remplissage sur rejet")

    r3 = m.execute_trade({"symbol": "DOGE-USD", "action": "SELL", "volume": 0.01})
    assert not r3["success"] and "indisponible" in r3["error"]
    print("  OK — erreur claire si symbole absent")

    # Spécifications du courtier absentes → la perte au stop est INCALCULABLE.
    # Le comportement est désormais RÉGLABLE (Réglages ⚙️ → Moteur de
    # performance). Réglage « exiger un risque calculable » ACTIF (celui de
    # _setup) : l'ordre est refusé plutôt qu'envoyé sans plafond vérifiable.
    from utils import trading_config
    r4 = m.execute_trade({"symbol": "SANSSPEC", "action": "BUY", "volume": 0.33,
                          "stop_loss": 59000, "take_profit": 62000})
    assert not r4["success"], "ordre envoyé sans plafond de risque vérifiable"
    assert "incalculable" in r4["error"].lower(), r4["error"]
    print("  OK — risque non vérifiable → ordre refusé (réglage « exiger » actif)")

    # Même situation, réglage DÉSACTIVÉ (défaut « plein potentiel ») :
    # l'ordre part au volume demandé, sans plafond de risque vérifiable.
    trading_config.forcer_en_memoire(exiger_risque_calculable=False,
                                     sizing_levier=False,
                                     risque_par_trade_pct=1.0)
    r4b = m.execute_trade({"symbol": "SANSSPEC", "action": "BUY", "volume": 0.33,
                           "stop_loss": 59000, "take_profit": 62000})
    assert r4b["success"], r4b
    assert etat["ordres"][-1]["volume"] == 0.33, etat["ordres"][-1]["volume"]
    print("  OK — même cas, garde-fou levé → ordre envoyé au volume demandé")

    # Dimensionnement au LEVIER : volume = levier × équité / notionnel du lot.
    # EURUSD, contrat 100 000, prix 60 000 (faux tick) → à levier 2 sur 100 000
    # d'équité : 200 000 / 6 000 000 000 ≈ 0 → rabattu au lot minimum.
    # BTCUSD, contrat 1 : 2 × 100 000 / 60 000 ≈ 3.33 lot.
    trading_config.forcer_en_memoire(sizing_levier=True, levier_max=10.0,
                                     levier=2.0, risque_par_trade_pct=100.0,
                                     exiger_risque_calculable=False,
                                     volume_max_lot=50.0)
    etat["ordres"].clear()
    r_lev = m.execute_trade({"symbol": "BTC-USD", "action": "BUY", "volume": 0.01,
                             "confiance": 100, "stop_loss": 59000})
    assert r_lev["success"], r_lev
    vol_lev = etat["ordres"][-1]["volume"]
    assert 3.0 <= vol_lev <= 3.4, f"volume au levier inattendu : {vol_lev}"
    print(f"  OK — dimensionnement au levier x2 : {vol_lev} lot (équité 100 000)")

    # Le levier porté par la DÉCISION prime sur le réglage global.
    etat["ordres"].clear()
    r_lev4 = m.execute_trade({"symbol": "BTC-USD", "action": "BUY", "volume": 0.01,
                              "confiance": 100, "levier": 4.0, "stop_loss": 59000})
    assert r_lev4["success"], r_lev4
    vol4 = etat["ordres"][-1]["volume"]
    assert vol4 > vol_lev * 1.8, f"levier de la décision ignoré : {vol4} vs {vol_lev}"
    print(f"  OK — levier de la décision appliqué : x4 → {vol4} lot")

    # Un levier au-dessus du plafond utilisateur est RABATTU, jamais appliqué.
    trading_config.forcer_en_memoire(sizing_levier=True, levier_max=3.0, levier=3.0,
                                     risque_par_trade_pct=100.0,
                                     exiger_risque_calculable=False,
                                     volume_max_lot=50.0)
    etat["ordres"].clear()
    m.execute_trade({"symbol": "BTC-USD", "action": "BUY", "volume": 0.01,
                     "confiance": 100, "levier": 50.0, "stop_loss": 59000})
    vol_cap = etat["ordres"][-1]["volume"]
    assert 4.9 <= vol_cap <= 5.1, f"plafond de levier non respecté : {vol_cap}"
    print(f"  OK — levier 50 demandé, plafond 3 → {vol_cap} lot (rabattu)")

    trading_config.forcer_en_memoire(risque_par_trade_pct=1.0, sizing_levier=False,
                                     exiger_risque_calculable=True,
                                     autoriser_lot_minimum=False)

    # Équité illisible : même conclusion (pas d'équité, pas de plafond)
    sauve = m._account_info
    m._account_info = {"balance": 100000, "currency": "USD"}   # equity absente
    r5 = m.execute_trade({"symbol": "BTC-USD", "action": "BUY", "volume": 0.03,
                          "stop_loss": 59000, "take_profit": 62000})
    assert not r5["success"] and "incalculable" in r5["error"].lower(), r5
    m._account_info = sauve
    print("  OK — équité illisible → ordre refusé")

    # Rejets du courtier traduits en CONSIGNE, pas en jargon anglais
    from utils.mt5_manager import _message_retcode
    msg = _message_retcode(10027, "AutoTrading disabled by client")
    assert "AlgoTrading" in msg and "Ctrl+E" in msg and "10027" in msg, msg
    assert "marché est FERMÉ" in _message_retcode(10018, "Market closed")
    assert "10099" in _message_retcode(10099, "inconnu")   # code non répertorié
    print("  OK — codes de rejet du courtier traduits en consigne actionnable")

    # Courtier ne proposant PAS le pétrole, mais bien « CL » (Colgate) :
    # la résolution doit REFUSER plutôt que trader le mauvais instrument.
    class _S:
        def __init__(self, n): self.name = n

    faux = types.ModuleType("MetaTrader5")
    faux.symbol_info = lambda n: _S(n) if n in ("CL", "BAC", "EURUSD") else None
    faux.symbol_select = lambda n, on: True
    faux.symbols_get = lambda: [_S(n) for n in ("CL", "BAC", "EURUSD")]
    sys.modules["MetaTrader5"] = faux
    sans_petrole = MT5Manager()
    sans_petrole._lib_available = True
    sans_petrole._connected = True
    assert sans_petrole.resoudre_symbole("CL=F") is None, (
        "pétrole absent chez le courtier : la résolution doit refuser, "
        "surtout quand « CL » (Colgate-Palmolive) existe")
    print("  OK — pétrole absent : refus, pas de repli sur « CL » (Colgate)")

    del sys.modules["MetaTrader5"]


if __name__ == "__main__":
    run()
    print("✅ Exécution MT5 OK")
